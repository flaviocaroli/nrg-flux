"""Forecast issuing service — bridges data (DB or live), models and the API."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.models import (ForecastExplanation, ForecastLoad, LoadActual,
                         SessionLocal)
from ..forecasting.model import MODEL_VERSION, LoadForecaster
from ..utils.timeutils import iso


def _history_from_db(db: Session, area: str, days: int = 30) -> pd.Series:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = db.execute(select(LoadActual)
                      .where(LoadActual.area_eic == area, LoadActual.ts_utc >= since)
                      .order_by(LoadActual.ts_utc)).scalars().all()
    idx = pd.DatetimeIndex([r.ts_utc.replace(tzinfo=timezone.utc)
                            if r.ts_utc.tzinfo is None else r.ts_utc for r in rows], tz="UTC")
    return pd.Series([r.load_mw for r in rows], index=idx).sort_index()


def _temperature_forecast(area: str, start: datetime, hours: int) -> pd.Series:
    s = get_settings()
    idx = pd.date_range(start, periods=hours, freq="h", tz="UTC")
    if s.demo_mode:
        from ..demo.synthetic import temperature_at
        return pd.Series([temperature_at(t.to_pydatetime()) for t in idx], index=idx)
    from ..ingestion.weather import zone_temperature_forecast
    ser = zone_temperature_forecast(area)
    return ser.reindex(idx).interpolate(limit=12).bfill().ffill()


def issue_forecast(area: str | None = None, horizon_hours: int = 168) -> dict:
    """Run the model and persist forecast + SHAP explanations. Returns summary."""
    s = get_settings()
    area = area or s.forecast_default_area
    fc = LoadForecaster(s.model_dir, area)
    if not fc.load_models():
        raise RuntimeError("No trained model found — run scripts/train_forecast.py first")

    db = SessionLocal()
    try:
        history = _history_from_db(db, area)
        if len(history) < 200:
            raise RuntimeError("Not enough load history in DB — run scripts/seed_demo.py "
                               "or a live backfill first")
        start = history.index[-1] + timedelta(hours=1)
        temp = _temperature_forecast(area, start, horizon_hours)
        pred = fc.predict(history, temp, horizon_hours)
        expl = fc.explain(history, temp, horizon_hours)
        expl_by_ts = {e["ts_utc"]: e["drivers"] for e in expl}

        issued = datetime.now(timezone.utc)
        db.execute(delete(ForecastLoad).where(ForecastLoad.area_eic == area))
        db.commit()
        for h, (ts, row) in enumerate(pred.iterrows(), start=1):
            f = ForecastLoad(area_eic=area, issued_at_utc=issued,
                             target_ts_utc=ts.to_pydatetime(), horizon_h=h,
                             p10_mw=float(row["p10"]), p50_mw=float(row["p50"]),
                             p90_mw=float(row["p90"]), model_version=MODEL_VERSION)
            db.add(f)
            db.flush()
            for d in expl_by_ts.get(ts.strftime("%Y-%m-%dT%H:%M:%SZ"), []):
                db.add(ForecastExplanation(
                    forecast_id=f.id, feature_name=d["feature"],
                    feature_value=d["feature_value"], impact_mw=d["impact_mw"],
                    direction=d["direction"], method="shap"))
        db.commit()
        return {"area": area, "issued_at_utc": iso(issued),
                "points": int(len(pred)), "model_version": MODEL_VERSION}
    finally:
        db.close()


def run_whatif(area: str, temp_delta_c: float, horizon: int) -> dict:
    s = get_settings()
    fc = LoadForecaster(s.model_dir, area)
    if not fc.load_models():
        return {"error": "no_model", "hint": "train first"}
    db = SessionLocal()
    try:
        history = _history_from_db(db, area)
    finally:
        db.close()
    start = history.index[-1] + timedelta(hours=1)
    temp = _temperature_forecast(area, start, horizon)
    base = fc.predict(history, temp, horizon)
    shifted = fc.predict(history, temp + temp_delta_c, horizon)
    return {
        "area_eic": area, "temp_delta_c": temp_delta_c, "horizon_hours": horizon,
        "scenario": [{"ts_utc": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                      "baseline_mw": round(float(base.loc[ts, "p50"]), 1),
                      "scenario_mw": round(float(shifted.loc[ts, "p50"]), 1),
                      "delta_mw": round(float(shifted.loc[ts, "p50"] - base.loc[ts, "p50"]), 1)}
                     for ts in base.index],
        "summary_delta_mw_mean": round(float((shifted["p50"] - base["p50"]).mean()), 1),
    }
