"""Train the day-ahead PRICE forecaster and issue a fresh price forecast.

Prerequisite: a load forecast must already be issued (train_forecast.py),
because our own load p50 is the model's key input.

Usage:
    cd backend
    python scripts/train_price_forecast.py                       # IT-North
    python scripts/train_price_forecast.py --area 10Y1001A1001A75E   # Sicily

Reads price history + load history from the database (works in both demo and
live mode), trains p10/p50/p90, prints an honest backtest vs naive baselines,
writes a model card, and issues a 48h forecast into forecast_price.
"""
import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db.models import (FuelPrice, LoadActual, LoadForecastTso,  # noqa: E402
                           PriceDayAhead, SessionLocal, init_db)
from app.forecasting.price_model import PriceForecaster  # noqa: E402
from app.services.forecast_service import (issue_price_forecast,  # noqa: E402
                                           national_for_zone)


def series_from(db, model, area, value_attr, days=400):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = db.execute(select(model).where(model.area_eic == area,
                                          model.ts_utc >= since)
                      .order_by(model.ts_utc)).scalars().all()
    if not rows:
        return pd.Series(dtype=float)
    idx = pd.DatetimeIndex([r.ts_utc.replace(tzinfo=timezone.utc)
                            if r.ts_utc.tzinfo is None else r.ts_utc
                            for r in rows], tz="UTC")
    return pd.Series([getattr(r, value_attr) for r in rows], index=idx).sort_index()


def tso_forecast_series(db, area: str, days: int = 800) -> pd.Series:
    """The TSO's own day-ahead load forecast, from ENTSO-E.

    THIS IS THE RIGHT LOAD FEATURE. Training on ACTUAL load while serving a
    FORECAST is train/serve skew: the model learns to react sharply to load it
    will never actually know, then receives a smoothed forecast at inference
    and produces a flat, muted price curve. The TSO forecast has the same
    distribution at train time and at serve time, because it is published
    day-ahead — which is exactly when we need it.
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = db.execute(select(LoadForecastTso)
                      .where(LoadForecastTso.area_eic == area,
                             LoadForecastTso.ts_utc >= since)
                      .order_by(LoadForecastTso.ts_utc)).scalars().all()
    if not rows:
        return pd.Series(dtype=float)
    idx = pd.DatetimeIndex([r.ts_utc.replace(tzinfo=timezone.utc)
                            if r.ts_utc.tzinfo is None else r.ts_utc
                            for r in rows], tz="UTC")
    s = pd.Series([r.forecast_mw for r in rows], index=idx).sort_index()
    return s[~s.index.duplicated(keep="last")]


def gas_series(db) -> pd.Series:
    """TTF daily settlements from the DB (empty Series if none ingested)."""
    rows = db.execute(select(FuelPrice).where(FuelPrice.fuel == "TTF")
                      .order_by(FuelPrice.day)).scalars().all()
    if not rows:
        return pd.Series(dtype=float)
    idx = pd.DatetimeIndex([pd.Timestamp(r.day, tz="UTC") for r in rows])
    return pd.Series([r.price for r in rows], index=idx)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--area", default="10Y1001A1001A73I", help="zone EIC for prices")
    ap.add_argument("--horizon", type=int, default=48)
    args = ap.parse_args()

    init_db()
    s = get_settings()
    db = SessionLocal()
    try:
        price = series_from(db, PriceDayAhead, args.area, "price_eur_mwh")
        nat = national_for_zone(args.area)
        actual = series_from(db, LoadActual, nat, "load_mw")
        tso = tso_forecast_series(db, nat)
        gas = gas_series(db)
    finally:
        db.close()

    if price.empty:
        sys.exit(f"No price history for {args.area}. Run seed_demo.py or "
                 f"backfill_entsoe.py first.")
    if actual.empty:
        sys.exit(f"No load history for {nat} — the national area behind {args.area}. "
                 f"It is the key price driver. Backfill it first.")

    # Choose the load feature: TSO forecast if we have decent coverage of the
    # price window, else actual load with an explicit skew warning.
    cov = 0.0
    if not tso.empty:
        cov = float(tso.reindex(price.index).notna().mean())
    if cov >= 0.80:
        load, load_src = tso, "tso_forecast"
        print(f"  load driver: {nat} — TSO day-ahead forecast "
              f"({len(tso)} h, {cov:.0%} coverage)  [no train/serve skew]")
    else:
        load, load_src = actual, "actual_load"
        print(f"  load driver: {nat} — ACTUAL load ({len(actual)} h)")
        print(f"  !! TSO forecast covers only {cov:.0%} of the price window.")
        print(f"     Training on actual load while serving a forecast is TRAIN/SERVE")
        print(f"     SKEW: the backtest will be optimistic and the issued forecast")
        print(f"     will look flat. Fix: backfill more TSO forecast history.")

    if gas.empty:
        print("  ! No TTF gas data — the gas feature will be inert (zeros).")
        print("    Ingest some:  python scripts/ingest_fuel.py --provider manual --price 35")
    else:
        print(f"  gas: {len(gas)} daily TTF settlements, latest {float(gas.iloc[-1]):.2f} EUR/MWh")

    print(f"Training price model for {args.area} on {len(price)} hourly prices ...")
    pf = PriceForecaster(s.model_dir, args.area)
    from app.forecasting.price_drivers import outage_series, neighbor_price_series
    # v9: per-market feature gate. NRGFLUX_V8_FEATURES lists the zones that
    # use the outage+neighbor drivers; others train exactly as v7. Evidence
    # so far: FR gains strongly (+7pp coverage), IT is better without.
    import os as _os
    _v8_zones = _os.environ.get(
        "NRGFLUX_V8_FEATURES",
        "10YFR-RTE------C,10Y1001A1001A82H,10YCH-SWISSGRIDZ").split(",")
    if args.area in [z.strip() for z in _v8_zones]:
        _out = outage_series(db, args.area); _nb = neighbor_price_series(db, args.area)
    else:
        import pandas as _pd
        _out, _nb = _pd.Series(dtype=float), _pd.Series(dtype=float)
        print("  v8 drivers: OFF for this zone (NRGFLUX_V8_FEATURES)")
    if len(_out): print(f"  outages: {int(_out.max())} MW peak unavailable in zone history")
    if len(_nb):  print(f"  neighbor prices: {len(_nb)} h")
    res = pf.train(price, load, gas=gas if not gas.empty else None,
                   outage=_out if len(_out) else None,
                   neighbor=_nb if len(_nb) else None,
                   load_source=load_src)
    m = res.metrics

    print("\n=== Price backtest (last 4 weeks hold-out) ===")
    print(f"  model WAPE      : {m['wape_model']}%")
    print(f"  naive weekly    : {m['wape_naive_weekly']}%    naive daily: {m['wape_naive_daily']}%")
    print(f"  MAE             : {m['mae_model_eur_mwh']} EUR/MWh   RMSE: {m['rmse_model_eur_mwh']}")
    tgt = m.get('coverage_target_pct', 80)
    cov, raw = m['p10_p90_coverage_pct'], m.get('p10_p90_coverage_uncalibrated_pct')
    flag = "OK" if abs(cov - tgt) <= 7 else "CHECK"
    print(f"  p10-p90 coverage: {cov}%  (target {tgt}%)  [{flag}]")
    print(f"    uncalibrated  : {raw}%   -> normalized CQR scale "
          f"x{m.get('conformal_scale')}")
    print(f"    mean band width: {m.get('mean_band_width_eur_mwh')} EUR/MWh "
          f"(calibrated on {m.get('calibration_hours')} h)")
    print(f"  beats naive     : {res.model_card['beats_naive_baseline']}"
          f"   (skill {res.model_card['skill_vs_best_naive_pct']}%)")
    if not res.model_card["beats_naive_baseline"]:
        print("\n  !! Model does NOT beat the naive baseline for this zone.")
        print("     Per the plan's honesty gate: do not sell price forecasts here")
        print("     until it does. The API will report this flag to clients.")

    print("\nIssuing price forecast ...")
    try:
        out = issue_price_forecast(args.area, args.horizon)
        print(f"  issued {out['points']} points at {out['issued_at_utc']}")
    except RuntimeError as e:
        print(f"  ! {e}")
        print("  (run scripts/train_forecast.py first — the load forecast is an input)")
    print("Done.")


if __name__ == "__main__":
    main()
