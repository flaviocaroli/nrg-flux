"""Explainable forecasting API (plan sections 8-9)."""
import json
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.models import ForecastExplanation, ForecastLoad, get_db
from ..utils.timeutils import iso, parse_iso
from .deps import require_api_key

router = APIRouter(prefix="/v1/forecast", tags=["forecasting"],
                   dependencies=[Depends(require_api_key)])


def _model_card(area: str) -> dict:
    s = get_settings()
    path = os.path.join(s.model_dir, f"model_card_{area}.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


@router.get("/load")
def forecast_load(area: str | None = None, horizon: int = 168,
                  db: Session = Depends(get_db)):
    s = get_settings()
    area = area or s.forecast_default_area
    if horizon not in (24, 48, 72, 168):
        raise HTTPException(422, detail={"error": "invalid_horizon",
                                         "hint": "horizon must be one of 24, 48, 72, 168"})
    latest_issue = db.execute(
        select(ForecastLoad.issued_at_utc).where(ForecastLoad.area_eic == area)
        .order_by(ForecastLoad.issued_at_utc.desc()).limit(1)).scalar_one_or_none()
    if latest_issue is None:
        raise HTTPException(404, detail={
            "error": "no_forecast",
            "hint": "Run `python scripts/train_forecast.py` to train and issue a forecast."})
    rows = db.execute(
        select(ForecastLoad).where(ForecastLoad.area_eic == area,
                                   ForecastLoad.issued_at_utc == latest_issue,
                                   ForecastLoad.horizon_h <= horizon)
        .order_by(ForecastLoad.target_ts_utc)).scalars().all()
    card = _model_card(area)
    return {
        "area_eic": area,
        "issued_at_utc": iso(latest_issue),
        "horizon_hours": horizon,
        "model_version": rows[0].model_version if rows else "unknown",
        "forecast": [{"ts_utc": iso(r.target_ts_utc), "horizon_h": r.horizon_h,
                      "mw_p10": round(r.p10_mw, 1), "mw_p50": round(r.p50_mw, 1),
                      "mw_p90": round(r.p90_mw, 1)} for r in rows],
        "backtest_summary": card.get("backtest", {}),
        "beats_naive_baseline": card.get("beats_naive_baseline"),
        "lineage": {"load_source": "ENTSO-E" if not s.demo_mode else "synthetic demo",
                    "weather_forecast": "MET Norway / NOAA GFS" if not s.demo_mode else "synthetic demo",
                    "method": "LightGBM quantile GBM + TreeSHAP"},
        "disclaimer": "Probabilistic decision-support output, not a guaranteed outcome.",
    }


@router.get("/explain")
def forecast_explain(area: str | None = None, ts: str | None = None,
                     db: Session = Depends(get_db)):
    s = get_settings()
    area = area or s.forecast_default_area
    latest_issue = db.execute(
        select(ForecastLoad.issued_at_utc).where(ForecastLoad.area_eic == area)
        .order_by(ForecastLoad.issued_at_utc.desc()).limit(1)).scalar_one_or_none()
    if latest_issue is None:
        raise HTTPException(404, detail={"error": "no_forecast"})
    q = select(ForecastLoad).where(ForecastLoad.area_eic == area,
                                   ForecastLoad.issued_at_utc == latest_issue)
    if ts:
        q = q.where(ForecastLoad.target_ts_utc == parse_iso(ts))
    else:
        q = q.order_by(ForecastLoad.target_ts_utc).limit(24)
    fc_rows = db.execute(q).scalars().all()
    if not fc_rows:
        raise HTTPException(404, detail={"error": "timestamp_not_in_forecast"})
    out = []
    for r in fc_rows:
        expl = db.execute(select(ForecastExplanation)
                          .where(ForecastExplanation.forecast_id == r.id)).scalars().all()
        out.append({
            "ts_utc": iso(r.target_ts_utc), "mw_p50": round(r.p50_mw, 1),
            "explanation": [{"feature": e.feature_name, "feature_value": e.feature_value,
                             "impact_mw": e.impact_mw, "direction": e.direction,
                             "method": e.method} for e in
                            sorted(expl, key=lambda x: -abs(x.impact_mw))],
        })
    return {"area_eic": area, "issued_at_utc": iso(latest_issue), "points": out,
            "method": "TreeSHAP on LightGBM p50 model"}


class WhatIfRequest(BaseModel):
    area: str | None = None
    temp_delta_c: float = Field(0.0, ge=-15, le=15,
                                description="Uniform temperature shift in °C")
    horizon: int = 48


@router.post("/whatif")
def forecast_whatif(req: WhatIfRequest):
    """Scenario: shift the temperature path and re-run the model in memory."""
    from ..services.forecast_service import run_whatif
    s = get_settings()
    return run_whatif(req.area or s.forecast_default_area, req.temp_delta_c, req.horizon)


@router.get("/modelcard")
def model_card(area: str | None = None):
    s = get_settings()
    card = _model_card(area or s.forecast_default_area)
    if not card:
        raise HTTPException(404, detail={"error": "no_model_card"})
    return card
