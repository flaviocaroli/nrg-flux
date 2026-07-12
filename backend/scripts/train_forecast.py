"""Train the explainable load forecaster and issue a fresh D+1..D+7 forecast.

Usage:  cd backend && python scripts/train_forecast.py [--area EIC]

Demo mode: trains on 2 years of generated history (same generative process as
the seeded DB, so the model learns real structure). Live mode: trains on the
load_actual table + ERA5/weather history you have backfilled.
"""
import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.demo.synthetic import national_load_mw, temperature_at  # noqa: E402
from app.forecasting.model import LoadForecaster  # noqa: E402
from app.services.forecast_service import issue_forecast  # noqa: E402

TRAIN_YEARS = 2


def demo_training_series(area: str) -> tuple[pd.Series, pd.Series]:
    end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    idx = pd.date_range(end - timedelta(days=365 * TRAIN_YEARS), end, freq="h", tz="UTC")
    rng = np.random.default_rng(42)
    load = pd.Series([national_load_mw(t.to_pydatetime(), float(rng.normal(0, 380)))
                      for t in idx], index=idx)
    temp = pd.Series([temperature_at(t.to_pydatetime()) for t in idx], index=idx)
    return load, temp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--area", default=None)
    args = ap.parse_args()
    s = get_settings()
    area = args.area or s.forecast_default_area

    print(f"Training LightGBM quantile models for {area} ({TRAIN_YEARS}y history) ...")
    load, temp = demo_training_series(area)
    fc = LoadForecaster(s.model_dir, area)
    result = fc.train(load, temp)

    m = result.metrics
    print("\n=== Backtest (last 8 weeks, rolling hold-out) ===")
    print(f"  model WAPE : {m['wape_model']}%   naive weekly WAPE: {m['wape_naive_weekly']}%")
    print(f"  model MAPE : {m['mape_model']}%   RMSE: {m['rmse_model']} MW")
    print(f"  p10-p90 coverage: {m['p10_p90_coverage_pct']}%  (target ≈ 80%)")
    print(f"  beats naive baseline: {result.model_card['beats_naive_baseline']}")
    print(f"  model card: {s.model_dir}/model_card_{area}.json")

    print("\nIssuing D+1..D+7 forecast into the database ...")
    summary = issue_forecast(area, horizon_hours=168)
    print(f"  issued {summary['points']} points at {summary['issued_at_utc']}")
    print("Done. Start the API and open the dashboard.")


if __name__ == "__main__":
    main()
