"""Daily retrain + forecast reissue — run once after the ~12:45 CET
day-ahead publication (the timers schedule 13:05 Europe/Rome).

    python3 scripts/daily_retrain.py
    python3 scripts/daily_retrain.py --no-wait     # train with whatever is there

Sequence:
  1. Publication gate: check the DB actually has tomorrow's day-ahead prices
     for the reference zone (IT-North). If not, run an ingest cycle and retry
     — publication delays happen, and training on yesterday's snapshot would
     silently ship stale forecasts.
  2. Train the load model for every scheduled market (national/control EIC).
  3. Train the price model for every scheduled market (bidding-zone EIC —
     Germany's two-code trap is encoded here once, in MARKET_EICS).
     Each train script reissues its forecast on success, so a green run means
     fresh p10/p50/p90 in the API.
  4. Print a per-model PASS/FAIL summary. Exit non-zero if any LOAD model
     failed (price models depend on load, so a load failure is systemic);
     individual price-model failures are reported but don't fail the run.

Environment knobs:
  NRGFLUX_MARKETS          comma list, default "IT,FR,DE,CH"
  NRGFLUX_RETRAIN_RETRIES  publication-gate retries, default 8 (x10 min)
"""
from __future__ import annotations

import argparse
import fcntl
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

LOCK_FILE = BACKEND / ".retrain.lock"
TRAIN_TIMEOUT_S = 40 * 60
RETRY_SLEEP_S = 10 * 60

# load-EIC vs price-EIC per market. DE is the only split pair (control area
# for load/weather, DE-LU bidding zone for price) — see handover §5.
MARKET_EICS = {
    "IT": {"load": "10YIT-GRTN-----B", "price": "10Y1001A1001A73I"},
    "FR": {"load": "10YFR-RTE------C", "price": "10YFR-RTE------C"},
    "DE": {"load": "10Y1001A1001A83F", "price": "10Y1001A1001A82H"},
    "CH": {"load": "10YCH-SWISSGRIDZ", "price": "10YCH-SWISSGRIDZ"},
}
REFERENCE_PRICE_ZONE = MARKET_EICS["IT"]["price"]


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}Z] {msg}", flush=True)


def tomorrow_published(zone_eic: str) -> bool:
    """True if the DB holds day-ahead prices covering tomorrow's market day."""
    from sqlalchemy import func, select
    from app.db.models import PriceDayAhead, SessionLocal, init_db
    from app.utils.scheduling import tomorrow_market_day
    from app.utils.timeutils import market_day_bounds_utc
    init_db()
    day = tomorrow_market_day(datetime.now(timezone.utc))
    s, e = market_day_bounds_utc(day)
    db = SessionLocal()
    try:
        n = db.execute(select(func.count()).select_from(PriceDayAhead)
                       .where(PriceDayAhead.area_eic == zone_eic,
                              PriceDayAhead.ts_utc >= s,
                              PriceDayAhead.ts_utc < e)).scalar_one()
        log(f"publication gate: {n} price rows for market day {day} ({zone_eic})")
        return n >= 20  # a full day minus DST/edge tolerance
    finally:
        db.close()


def run(cmd: list[str], timeout: int = TRAIN_TIMEOUT_S) -> bool:
    log("run: " + " ".join(cmd))
    try:
        return subprocess.run([sys.executable, *cmd], cwd=BACKEND,
                              timeout=timeout).returncode == 0
    except subprocess.TimeoutExpired:
        log(f"TIMEOUT after {timeout}s: {' '.join(cmd)}")
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--no-wait", action="store_true",
                    help="skip the publication gate (train on current data)")
    args = ap.parse_args()

    markets = [m.strip().upper() for m in
               os.environ.get("NRGFLUX_MARKETS", "IT,FR,DE,CH").split(",")
               if m.strip() and m.strip().upper() in MARKET_EICS]
    retries = int(os.environ.get("NRGFLUX_RETRAIN_RETRIES", "8"))

    lock = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("a retrain is already running — exiting cleanly")
        return 0

    # 1 · publication gate --------------------------------------------------
    if not args.no_wait:
        for attempt in range(retries + 1):
            if tomorrow_published(REFERENCE_PRICE_ZONE):
                break
            if attempt == retries:
                log("publication never landed — training anyway on latest data "
                    "(forecasts will carry an older anchor; a quality event "
                    "was recorded by the stale check)")
                break
            log(f"not published yet — ingest + retry {attempt + 1}/{retries} "
                f"in {RETRY_SLEEP_S // 60} min")
            run(["scripts/ingest_cycle.py"], timeout=30 * 60)
            time.sleep(RETRY_SLEEP_S)
    else:
        # still top up once so we never train on a manually-started stale DB
        run(["scripts/ingest_cycle.py"], timeout=30 * 60)

    # 2 · load models (price models depend on these) ------------------------
    results: dict[str, bool] = {}
    for cc in markets:
        results[f"load {cc}"] = run(["scripts/train_forecast.py",
                                     "--area", MARKET_EICS[cc]["load"]])

    # 3 · price models ------------------------------------------------------
    for cc in markets:
        if not results.get(f"load {cc}"):
            log(f"skip price {cc}: its load model failed")
            results[f"price {cc}"] = False
            continue
        results[f"price {cc}"] = run(["scripts/train_price_forecast.py",
                                      "--area", MARKET_EICS[cc]["price"]])

    # 4 · summary -----------------------------------------------------------
    log("=== retrain summary ===")
    for name, ok in results.items():
        log(f"  {name:<10} {'PASS' if ok else 'FAIL'}")
    load_failed = any(not ok for name, ok in results.items()
                      if name.startswith("load"))
    log("retrain done: " + ("OK" if not load_failed else "LOAD FAILURES"))
    return 1 if load_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
