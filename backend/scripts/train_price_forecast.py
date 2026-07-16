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
from app.db.models import (FuelPrice, LoadActual, PriceDayAhead,  # noqa: E402
                           SessionLocal, init_db)
from app.forecasting.price_model import PriceForecaster  # noqa: E402
from app.services.forecast_service import issue_price_forecast  # noqa: E402


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
        load = series_from(db, LoadActual, s.forecast_default_area, "load_mw")
        gas = gas_series(db)
    finally:
        db.close()

    if price.empty:
        sys.exit(f"No price history for {args.area}. Run seed_demo.py or "
                 f"backfill_entsoe.py first.")
    if load.empty:
        sys.exit("No load history found — needed as the key price driver.")

    if gas.empty:
        print("  ! No TTF gas data — the gas feature will be inert (zeros).")
        print("    Ingest some:  python scripts/ingest_fuel.py --provider manual --price 35")
    else:
        print(f"  gas: {len(gas)} daily TTF settlements, latest {float(gas.iloc[-1]):.2f} EUR/MWh")

    print(f"Training price model for {args.area} on {len(price)} hourly prices ...")
    pf = PriceForecaster(s.model_dir, args.area)
    res = pf.train(price, load, gas=gas if not gas.empty else None)
    m = res.metrics

    print("\n=== Price backtest (last 4 weeks hold-out) ===")
    print(f"  model WAPE      : {m['wape_model']}%")
    print(f"  naive weekly    : {m['wape_naive_weekly']}%    naive daily: {m['wape_naive_daily']}%")
    print(f"  MAE             : {m['mae_model_eur_mwh']} EUR/MWh   RMSE: {m['rmse_model_eur_mwh']}")
    tgt = m.get('coverage_target_pct', 80)
    cov, raw = m['p10_p90_coverage_pct'], m.get('p10_p90_coverage_uncalibrated_pct')
    flag = "OK" if abs(cov - tgt) <= 7 else "CHECK"
    print(f"  p10-p90 coverage: {cov}%  (target {tgt}%)  [{flag}]")
    print(f"    uncalibrated  : {raw}%   -> conformal widening "
          f"+/-{m.get('conformal_widening_eur_mwh')} EUR/MWh")
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
