"""Core Market Data API (plan section 8, endpoint spec)."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.models import (EicCode, FlowPhysical, LoadActual, LoadForecastTso,
                         Outage, PriceDayAhead, get_db)
from ..utils.timeutils import iso
from .deps import envelope, parse_window, point, require_api_key

router = APIRouter(prefix="/v1", tags=["market-data"], dependencies=[Depends(require_api_key)])


def _area_name(db: Session, eic: str) -> str:
    row = db.execute(select(EicCode).where(EicCode.eic_code == eic)).scalar_one_or_none()
    return row.display_name if row else eic


@router.get("/prices/dayahead")
def prices_dayahead(area: str, start: str | None = None, end: str | None = None,
                    db: Session = Depends(get_db)):
    s, e = parse_window(start, end, default_hours=72)
    rows = db.execute(
        select(PriceDayAhead).where(PriceDayAhead.area_eic == area,
                                    PriceDayAhead.ts_utc >= s, PriceDayAhead.ts_utc < e)
        .order_by(PriceDayAhead.ts_utc)).scalars().all()
    name = _area_name(db, area)
    demo = get_settings().demo_mode
    series = [point(r.ts_utc, r.price_eur_mwh, "EUR/MWh", r.area_eic, name, {
        "quality": {"gap_flag": False, "revision": r.revision, "confidence": 0.98 if not demo else 1.0},
        "retrieved_at_utc": iso(r.retrieved_at_utc),
    }) for r in rows]
    return envelope(series, "Day-ahead prices", "ENTSO-E Transparency Platform", demo, "A44")


@router.get("/load/actual")
def load_actual(area: str, start: str | None = None, end: str | None = None,
                db: Session = Depends(get_db)):
    s, e = parse_window(start, end, default_hours=72)
    rows = db.execute(
        select(LoadActual).where(LoadActual.area_eic == area,
                                 LoadActual.ts_utc >= s, LoadActual.ts_utc < e)
        .order_by(LoadActual.ts_utc)).scalars().all()
    demo = get_settings().demo_mode
    series = [point(r.ts_utc, r.load_mw, "MW", r.area_eic, _area_name(db, area),
                    {"quality": {"gap_flag": r.quality_flag != "ok", "flag": r.quality_flag}})
              for r in rows]
    return envelope(series, "Actual total load", "ENTSO-E Transparency Platform", demo, "A65/A16")


@router.get("/load/forecast/tso")
def load_forecast_tso(area: str, start: str | None = None, end: str | None = None,
                      db: Session = Depends(get_db)):
    s, e = parse_window(start, end, default_hours=48)
    rows = db.execute(
        select(LoadForecastTso).where(LoadForecastTso.area_eic == area,
                                      LoadForecastTso.ts_utc >= s, LoadForecastTso.ts_utc < e)
        .order_by(LoadForecastTso.ts_utc)).scalars().all()
    demo = get_settings().demo_mode
    series = [point(r.ts_utc, r.forecast_mw, "MW", r.area_eic, _area_name(db, area),
                    {"issued_at_utc": iso(r.issued_at_utc)}) for r in rows]
    return envelope(series, "TSO day-ahead load forecast", "ENTSO-E Transparency Platform", demo, "A65/A01")


@router.get("/flows/physical")
def flows_physical(from_area: str = Query(alias="from"), to_area: str = Query(alias="to"),
                   start: str | None = None, end: str | None = None,
                   db: Session = Depends(get_db)):
    s, e = parse_window(start, end, default_hours=48)
    rows = db.execute(
        select(FlowPhysical).where(FlowPhysical.from_area_eic == from_area,
                                   FlowPhysical.to_area_eic == to_area,
                                   FlowPhysical.ts_utc >= s, FlowPhysical.ts_utc < e)
        .order_by(FlowPhysical.ts_utc)).scalars().all()
    demo = get_settings().demo_mode
    series = [point(r.ts_utc, r.mw, "MW", extra={
        "from_area_eic": r.from_area_eic, "to_area_eic": r.to_area_eic}) for r in rows]
    return envelope(series, "Physical cross-border flow", "ENTSO-E Transparency Platform", demo, "A11")


@router.get("/outages")
def outages(area: str | None = None, active_only: bool = False,
            db: Session = Depends(get_db)):
    q = select(Outage).order_by(Outage.unavailable_mw.desc())
    if area:
        q = q.where(Outage.area_eic == area)
    rows = db.execute(q).scalars().all()
    if active_only:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        rows = [r for r in rows if r.start_utc.replace(tzinfo=r.start_utc.tzinfo or timezone.utc) <= now]
    demo = get_settings().demo_mode
    series = [{
        "outage_id": r.outage_id, "kind": r.kind,
        "planned": r.planned, "area_eic": r.area_eic,
        "area_name": _area_name(db, r.area_eic),
        "asset_name": r.asset_name, "fuel": r.fuel,
        "start_utc": iso(r.start_utc), "end_utc": iso(r.end_utc),
        "unavailable_mw": r.unavailable_mw, "reason": r.reason,
        "updated_at_utc": iso(r.updated_at_utc),
    } for r in rows]
    return envelope(series, "Unavailability of generation/transmission",
                    "ENTSO-E Transparency Platform", demo, "A77/A80")


@router.get("/eic/resolve")
def eic_resolve(q: str, type: str = "all", db: Session = Depends(get_db)):
    from ..utils.eic import score_match
    rows = db.execute(select(EicCode)).scalars().all()
    scored = []
    for r in rows:
        if type != "all" and r.code_type != type:
            continue
        sc = score_match(q, r.display_name, r.aliases, r.eic_code)
        if sc > 0:
            scored.append((sc, r))
    scored.sort(key=lambda t: -t[0])
    return {"query": q, "matches": [{
        "eic_code": r.eic_code, "code_type": r.code_type, "display_name": r.display_name,
        "country": r.country, "score": sc} for sc, r in scored[:10]]}
