"""Explainable forecasting API (plan sections 8-9)."""
import json
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.models import (ForecastExplanation, ForecastLoad, SessionLocal,
                         get_db)
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
        "training_source": card.get("training_source"),
        "calibration": card.get("calibration"),
        "coverage": {
            "target_pct": card.get("backtest", {}).get("coverage_target_pct"),
            "achieved_pct": card.get("backtest", {}).get("p10_p90_coverage_pct"),
            "uncalibrated_pct": card.get("backtest", {}).get("p10_p90_coverage_uncalibrated_pct"),
        },
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


# ------------------------------------------------------------ price forecast

def _price_card(area: str) -> dict:
    s = get_settings()
    path = os.path.join(s.model_dir, f"model_card_price_{area}.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


@router.get("/price")
def forecast_price(area: str = "10Y1001A1001A73I", horizon: int = 48,
                   db: Session = Depends(get_db)):
    """Day-ahead PRICE forecast: p10/p50/p90 per hour, with published skill.

    Sold as a distribution, not a point estimate. The response always carries
    its own backtest and a `beats_naive_baseline` flag — if the model is not
    beating the naive baseline for this zone, you will see it here.
    """
    from ..db.models import ForecastPrice
    if horizon not in (24, 48, 72, 168):
        raise HTTPException(422, detail={"error": "invalid_horizon",
                                         "hint": "horizon must be 24, 48, 72 or 168"})
    latest = db.execute(select(ForecastPrice.issued_at_utc)
                        .where(ForecastPrice.area_eic == area)
                        .order_by(ForecastPrice.issued_at_utc.desc())
                        .limit(1)).scalar_one_or_none()
    if latest is None:
        raise HTTPException(404, detail={
            "error": "no_price_forecast",
            "hint": "Run `python scripts/train_price_forecast.py` to train and issue."})
    rows = db.execute(select(ForecastPrice)
                      .where(ForecastPrice.area_eic == area,
                             ForecastPrice.issued_at_utc == latest,
                             ForecastPrice.horizon_h <= horizon)
                      .order_by(ForecastPrice.target_ts_utc)).scalars().all()
    card = _price_card(area)
    bt = card.get("backtest", {})
    return {
        "area_eic": area,
        "issued_at_utc": iso(latest),
        "horizon_hours": horizon,
        "unit": "EUR/MWh",
        "model_version": rows[0].model_version if rows else "unknown",
        "forecast": [{"ts_utc": iso(r.target_ts_utc), "horizon_h": r.horizon_h,
                      "eur_p10": round(r.p10_eur, 2), "eur_p50": round(r.p50_eur, 2),
                      "eur_p90": round(r.p90_eur, 2)} for r in rows],
        "backtest_summary": bt,
        "beats_naive_baseline": card.get("beats_naive_baseline"),
        "skill_vs_best_naive_pct": card.get("skill_vs_best_naive_pct"),
        "calibration": card.get("calibration"),
        "coverage": {
            "target_pct": bt.get("coverage_target_pct"),
            "achieved_pct": bt.get("p10_p90_coverage_pct"),
            "uncalibrated_pct": bt.get("p10_p90_coverage_uncalibrated_pct"),
        },
        "key_input": card.get("key_input"),
        "known_weaknesses": card.get("known_weaknesses", []),
        "disclaimer": "Probabilistic price range for decision support. "
                      "Not a trading signal and not a guaranteed outcome.",
    }


@router.get("/price/explain")
def forecast_price_explain(area: str = "10Y1001A1001A73I", horizon: int = 24):
    """SHAP drivers for the price forecast, in EUR/MWh per feature."""
    from ..forecasting.price_model import PriceForecaster
    from ..services.forecast_service import (_load_forecast_series,
                                             _price_history_from_db,
                                             national_for_zone)
    s = get_settings()
    pf = PriceForecaster(s.model_dir, area)
    if not pf.load_models():
        raise HTTPException(404, detail={"error": "no_price_model"})
    db = SessionLocal()
    try:
        hist = _price_history_from_db(db, area)
        load_fc = _load_forecast_series(db, national_for_zone(area))
    finally:
        db.close()
    if len(hist) < 200 or load_fc.empty:
        raise HTTPException(404, detail={"error": "insufficient_history"})
    return {"area_eic": area, "unit": "EUR/MWh",
            "points": pf.explain(hist, load_fc, horizon),
            "method": "TreeSHAP on LightGBM p50 price model"}


@router.get("/price/modelcard")
def price_model_card(area: str = "10Y1001A1001A73I"):
    card = _price_card(area)
    if not card:
        raise HTTPException(404, detail={"error": "no_price_model_card"})
    return card
