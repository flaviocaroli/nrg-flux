"""One incremental ingestion cycle — designed to run every hour.

    python3 scripts/ingest_cycle.py            # normal cycle
    python3 scripts/ingest_cycle.py --days 7   # deeper top-up after downtime

What it does, in order:
  1. Take an exclusive lock (a second overlapping cycle exits immediately).
  2. Top up ENTSO-E market data for every scheduled market (upserts, so a
     small --days window is cheap and safe to repeat).
  3. Top up ERA5 weather — but only when the newest weather row is older than
     WEATHER_TOPUP_HOURS, because weather history publishes daily, not
     hourly, and there is no point hammering Open-Meteo 24x a day.
  4. Optionally refresh the fuel curve (evaluation only until a licensed feed
     exists) when NRGFLUX_FUEL_PROVIDER is set.
  5. Run the data-quality checks that previously existed but never ran
     (handover Tier-3 #11), so gaps/staleness/outliers land in
     /v1/quality/events on every cycle.
  6. Reissue existing load and price forecasts from the newly ingested data.
     This does NOT retrain models; fitting remains the daily job.

Environment knobs (all optional):
  NRGFLUX_MARKETS        comma list, default "IT,FR,DE,CH"
  NRGFLUX_INGEST_DAYS    ENTSO-E top-up window, default 3
  NRGFLUX_FUEL_PROVIDER  csv|yfinance|manual — unset = skip fuel
  NRGFLUX_FUEL_CSV       path when provider is csv

Exit code is non-zero if any *market-data* step fails; weather/fuel/quality
problems are logged but do not fail the cycle (they self-heal next run).
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.utils.process_lock import try_process_lock  # noqa: E402

LOCK_FILE = BACKEND / ".ingest.lock"
WEATHER_TOPUP_HOURS = 26  # ERA5 lands daily; top up when older than ~a day
STEP_TIMEOUT_S = 25 * 60

DEFAULT_MARKETS = ["IT", "FR", "DE", "CH"]


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}Z] {msg}", flush=True)


def run_step(name: str, cmd: list[str]) -> bool:
    log(f"step: {name}: {' '.join(cmd)}")
    try:
        r = subprocess.run([sys.executable, *cmd], cwd=BACKEND,
                           timeout=STEP_TIMEOUT_S)
        ok = r.returncode == 0
        log(f"step: {name}: {'ok' if ok else f'FAILED rc={r.returncode}'}")
        return ok
    except subprocess.TimeoutExpired:
        log(f"step: {name}: TIMEOUT after {STEP_TIMEOUT_S}s")
        return False


def weather_is_stale(hours: float = WEATHER_TOPUP_HOURS) -> bool:
    from sqlalchemy import select
    from app.db.models import SessionLocal, WeatherHistory, init_db
    init_db()
    db = SessionLocal()
    try:
        latest = db.execute(select(WeatherHistory.ts_utc)
                            .order_by(WeatherHistory.ts_utc.desc()).limit(1)
                            ).scalar_one_or_none()
        if latest is None:
            return True
        if latest.tzinfo is None:
            latest = latest.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - latest > timedelta(hours=hours)
    finally:
        db.close()


def run_quality_checks(markets: list[str]) -> None:
    """Wire the existing-but-never-scheduled quality engine into the cycle."""
    from app.db.models import (LoadActual, PriceDayAhead, SessionLocal,
                               WeatherHistory, init_db)
    from app.quality.checks import (check_gaps, check_price_outliers,
                                    check_stale)
    from app.utils.eic import EU_MARKETS
    init_db()
    db = SessionLocal()
    try:
        for cc in markets:
            m = EU_MARKETS.get(cc)
            if not m:
                continue
            gaps = check_gaps(db, m["national"])
            outliers = check_price_outliers(db, m["zones"][0])
            if gaps or outliers:
                log(f"quality: {cc}: {gaps} gap event(s), {outliers} outlier event(s)")
        # dataset-level staleness (limits chosen per publication cadence)
        for name, model, limit in (("load_actual", LoadActual, 3.0),
                                   ("prices_dayahead", PriceDayAhead, 26.0),
                                   ("weather_history", WeatherHistory, 48.0)):
            if check_stale(db, name, model, limit):
                log(f"quality: {name} is stale (limit {limit}h) — event recorded")
    except Exception as e:  # quality must never break ingestion
        log(f"quality: checks errored (non-fatal): {e}")
    finally:
        db.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int,
                    default=int(os.environ.get("NRGFLUX_INGEST_DAYS", "3")),
                    help="ENTSO-E incremental window (default 3)")
    ap.add_argument("--skip-forecast-refresh", action="store_true",
                    help="ingest/quality only; do not reissue forecasts")
    args = ap.parse_args()

    markets = [m.strip().upper() for m in
               os.environ.get("NRGFLUX_MARKETS", ",".join(DEFAULT_MARKETS)).split(",")
               if m.strip()]
    eu_markets = [m for m in markets if m != "IT"]

    lock = try_process_lock(LOCK_FILE)
    if lock is None:
        log("another ingest cycle is already running — exiting cleanly")
        return 0

    log(f"ingest cycle start: markets={markets} days={args.days}")
    ok = True

    if "IT" in markets:
        ok &= run_step("entsoe IT", ["scripts/backfill_entsoe.py",
                                     "--days", str(args.days)])
    if eu_markets:
        ok &= run_step("entsoe EU", ["scripts/backfill_eu.py",
                                     "--markets", *eu_markets,
                                     "--days", str(args.days)])

    if weather_is_stale():
        # non-fatal: the load model tolerates a day of missing weather
        run_step("era5 top-up", ["scripts/backfill_era5.py",
                                 "--all-markets", "--years", "1"])
    else:
        log("weather fresh — skipping ERA5 top-up")

    fuel = os.environ.get("NRGFLUX_FUEL_PROVIDER", "").strip()
    if fuel:
        cmd = ["scripts/ingest_fuel.py", "--provider", fuel, "--days", "30"]
        if fuel == "csv":
            cmd += ["--path", os.environ.get("NRGFLUX_FUEL_CSV", "ttf.csv")]
        run_step("fuel", cmd)  # non-fatal

    run_quality_checks(markets)

    if ok and not args.skip_forecast_refresh:
        ok &= run_step("forecast refresh", ["scripts/refresh_forecasts.py"])
    elif not ok:
        log("forecast refresh skipped because market-data ingestion had failures")
    else:
        log("forecast refresh skipped by command-line flag")

    log(f"ingest cycle done: {'OK' if ok else 'WITH FAILURES'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
