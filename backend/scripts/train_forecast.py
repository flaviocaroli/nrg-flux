"""Train the explainable load forecaster and issue a D+1..D+7 forecast.

IMPORTANT: this trains on the REAL load history in your database whenever
enough is available (>= 45 days). It only falls back to the synthetic demo
generator in demo mode, or when the DB is too thin — and it says so loudly.

An earlier version always trained on synthetic data, which produced a model
biased by ~12 GW against real Italian load while reporting a meaningless
1.2% backtest. Always check the "training source" line below.

Usage:
    python scripts/train_forecast.py                          # auto
    python scripts/train_forecast.py --area 10YFR-RTE------C  # France
    python scripts/train_forecast.py --source db              # fail if no real data
    python scripts/train_forecast.py --source synthetic       # force demo data
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.forecasting.history import training_series  # noqa: E402
from app.forecasting.model import LoadForecaster  # noqa: E402
from app.services.forecast_service import issue_forecast  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--area", default=None)
    ap.add_argument("--source", default="auto", choices=["auto", "db", "synthetic"])
    args = ap.parse_args()
    s = get_settings()
    area = args.area or s.forecast_default_area

    try:
        load, temp, src = training_series(area, args.source)
    except RuntimeError as e:
        sys.exit(f"\n  ! {e}\n")

    days = len(load) / 24
    print(f"Training LightGBM quantile models for {area}")
    print(f"  training source : {src.upper()}  ({days:.0f} days, "
          f"{load.index[0].date()} -> {load.index[-1].date()})")
    print(f"  load range      : {load.min():,.0f} - {load.max():,.0f} MW "
          f"(mean {load.mean():,.0f})")
    if src == "synthetic" and not s.demo_mode:
        print("  !! WARNING: synthetic training data while DEMO_MODE=false.")
        print("     The backtest below does NOT describe your real market and the")
        print("     forecast will be biased. Backfill more history, then retrain.")

    fc = LoadForecaster(s.model_dir, area)
    result = fc.train(load, temp, source=src)
    m = result.metrics

    print("\n=== Backtest (last 8 weeks, rolling hold-out) ===")
    print(f"  model WAPE : {m['wape_model']}%   naive weekly WAPE: {m['wape_naive_weekly']}%")
    print(f"  model MAPE : {m['mape_model']}%   RMSE: {m['rmse_model']} MW")
    cov, raw = m['p10_p90_coverage_pct'], m.get('p10_p90_coverage_uncalibrated_pct')
    print(f"  p10-p90 coverage: {cov}%  (target {m.get('coverage_target_pct', 80)}%)")
    print(f"    uncalibrated  : {raw}%  -> conformal widening "
          f"+/-{m.get('conformal_widening_mw')} MW")
    print(f"  beats naive baseline: {result.model_card['beats_naive_baseline']}")
    print(f"  model card: {s.model_dir}/model_card_{area}.json")

    print("\nIssuing D+1..D+7 forecast into the database ...")
    try:
        summary = issue_forecast(area, horizon_hours=168)
        print(f"  issued {summary['points']} points at {summary['issued_at_utc']}")
    except RuntimeError as e:
        print(f"  ! {e}")
    print("Done.")


if __name__ == "__main__":
    main()
