"""SARIMAX load forecaster — the interpretable classical benchmark.

WHY THIS EXISTS
---------------
SARIMAX (Seasonal ARIMA with eXogenous variables) is the classical,
fully-transparent alternative to the LightGBM model. Every coefficient has a
direct physical meaning you can put in a client slide:

    y_t = c + Σ φ_i·y_(t-i)  +  Σ θ_j·ε_(t-j)  +  seasonal terms
            + β1·HDD_t + β2·CDD_t + β3·weekend_t + β4·holiday_t + ε_t

e.g. "β2 = +610 means each cooling degree adds ~610 MW of demand."

WHAT "NAIVE" MEANS
------------------
The naive (persistence) forecast is the zero-intelligence baseline:
  * naive-daily : ŷ(t) = y(t − 24h)   — "same as yesterday, same hour"
  * naive-weekly: ŷ(t) = y(t − 168h)  — "same as last week, same hour"
It costs nothing to produce, so any paid model MUST beat it, or the honest
conclusion is that the model adds no value. We report both and compare
against the tougher one.

MODELLING NOTE
--------------
Hourly load has two seasonalities (24h and 168h). SARIMAX handles only one
seasonal period tractably, so we use s=24 and hand the weekly pattern to the
exogenous variables (weekend/holiday dummies + the y(t-168) term as a
regressor). This keeps fitting fast and the model honest.

Usage:
    cd backend
    pip install statsmodels
    python scripts/train_sarimax.py                 # uses DB load history
    python scripts/train_sarimax.py --days 90 --test-days 14
"""
import argparse
import sys
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

warnings.filterwarnings("ignore")

from app.config import get_settings  # noqa: E402
from app.db.models import LoadActual, SessionLocal, init_db  # noqa: E402
from sqlalchemy import select  # noqa: E402

try:
    from statsmodels.tsa.statespace.sarimax import SARIMAX
except ImportError:
    sys.exit("pip install statsmodels  # then re-run")

HDD_BASE, CDD_BASE = 16.0, 21.0   # °C thresholds — heating / cooling demand


def wape(actual, pred):
    actual, pred = np.asarray(actual), np.asarray(pred)
    return float(np.abs(actual - pred).sum() / np.abs(actual).sum() * 100)


def load_history(area: str, days: int) -> pd.Series:
    init_db()
    db = SessionLocal()
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = db.execute(select(LoadActual)
                      .where(LoadActual.area_eic == area, LoadActual.ts_utc >= since)
                      .order_by(LoadActual.ts_utc)).scalars().all()
    db.close()
    if not rows:
        sys.exit(f"No load history for {area}. Run seed_demo.py or a backfill first.")
    idx = pd.DatetimeIndex([r.ts_utc.replace(tzinfo=timezone.utc)
                            if r.ts_utc.tzinfo is None else r.ts_utc
                            for r in rows], tz="UTC")
    s = pd.Series([r.load_mw for r in rows], index=idx).sort_index()
    return s.resample("h").mean().interpolate(limit=3)


def temperature_series(idx: pd.DatetimeIndex) -> pd.Series:
    """Temperature aligned to the load index.

    Demo mode: the synthetic generator (perfectly consistent with the data).
    Live mode: replace with your stored weather history; the MET Norway
    ingestion in app/ingestion/weather.py is the forward-looking source.
    """
    s = get_settings()
    if s.demo_mode:
        from app.demo.synthetic import temperature_at
        return pd.Series([temperature_at(t.to_pydatetime()) for t in idx], index=idx)
    # crude but serviceable fallback for live mode without stored weather:
    # a smooth seasonal-diurnal proxy. Swap for real ERA5/MET history ASAP.
    hours = idx.hour.values
    doy = idx.dayofyear.values
    return pd.Series(12 + 10 * np.sin((doy - 105) / 365 * 2 * np.pi)
                     + 5 * np.sin((hours - 9) / 24 * 2 * np.pi), index=idx)


def build_exog(y: pd.Series, temp: pd.Series) -> pd.DataFrame:
    """Exogenous regressors — each one interpretable."""
    import holidays as hol
    it_holidays = hol.country_holidays("IT")
    X = pd.DataFrame(index=y.index)
    X["hdd"] = np.maximum(0.0, HDD_BASE - temp)          # heating degrees
    X["cdd"] = np.maximum(0.0, temp - CDD_BASE)          # cooling degrees
    X["weekend"] = (y.index.dayofweek >= 5).astype(float)
    X["holiday"] = [1.0 if d in it_holidays else 0.0 for d in y.index.date]
    X["lag168"] = y.shift(168)                           # weekly memory as regressor
    return X


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--area", default="10YIT-GRTN-----B")
    ap.add_argument("--days", type=int, default=45, help="history window to use")
    ap.add_argument("--test-days", type=int, default=7, help="hold-out length")
    args = ap.parse_args()

    y = load_history(args.area, args.days)
    temp = temperature_series(y.index)
    X = build_exog(y, temp)

    ok = X["lag168"].notna()
    y, X = y[ok], X[ok]
    n_test = args.test_days * 24
    y_tr, y_te = y.iloc[:-n_test], y.iloc[-n_test:]
    X_tr, X_te = X.iloc[:-n_test], X.iloc[-n_test:]
    print(f"Fitting SARIMAX(1,0,1)x(1,1,1,24) on {len(y_tr)} hours, "
          f"testing on {len(y_te)} ...")

    model = SARIMAX(y_tr, exog=X_tr,
                    order=(1, 0, 1), seasonal_order=(1, 1, 1, 24),
                    enforce_stationarity=False, enforce_invertibility=False)
    fit = model.fit(disp=False, maxiter=200)

    # ---- interpretable coefficients: the whole point of this model ----
    print("\n=== Exogenous coefficients (MW per unit) ===")
    for name in ["hdd", "cdd", "weekend", "holiday", "lag168"]:
        if name in fit.params.index:
            c = fit.params[name]
            unit = {"hdd": "MW per heating degree", "cdd": "MW per cooling degree",
                    "weekend": "MW weekend shift", "holiday": "MW holiday shift",
                    "lag168": "per MW of last-week load"}[name]
            print(f"  {name:<8} {c:>10.1f}   ({unit})")

    # ---- forecast the hold-out ----
    pred = fit.forecast(steps=len(y_te), exog=X_te)

    # ---- baselines: what 'naive' means, computed explicitly ----
    naive_daily = y.shift(24).iloc[-n_test:]     # yesterday, same hour
    naive_weekly = y.shift(168).iloc[-n_test:]   # last week, same hour

    w_model = wape(y_te, pred)
    w_nd = wape(y_te, naive_daily)
    w_nw = wape(y_te, naive_weekly)
    best_naive = min(w_nd, w_nw)

    print("\n=== Backtest (WAPE, lower = better) ===")
    print(f"  SARIMAX        : {w_model:.2f}%")
    print(f"  naive daily    : {w_nd:.2f}%   (y(t-24h))")
    print(f"  naive weekly   : {w_nw:.2f}%   (y(t-168h))")
    print(f"  beats best naive: {w_model < best_naive}"
          f"   (skill {((best_naive - w_model) / best_naive * 100):+.1f}%)")

    # compare with the LightGBM card if one exists
    import json, os
    s = get_settings()
    card = os.path.join(s.model_dir, f"model_card_{args.area}.json")
    if os.path.exists(card):
        lgbm = json.load(open(card))["backtest"]["wape_model"]
        print(f"\n  For reference — LightGBM WAPE on its own backtest: {lgbm}%")
        print("  (different hold-out windows: indicative, not a strict A/B)")

    print("\nInterpretation guide:")
    print("  * beats naive = the model has genuine signal beyond persistence")
    print("  * SARIMAX coefficients are your client-facing explanations")
    print("  * expect LightGBM to win on accuracy; SARIMAX wins on transparency")


if __name__ == "__main__":
    main()
