"""Status, data-quality, webhooks and the dashboard aggregate endpoint."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, HttpUrl
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.models import (DataQualityEvent, EicCode, FlowPhysical, LoadActual,
                         Outage, PriceDayAhead, Webhook, get_db)
from ..utils.eic import IT_BORDERS, ITALY_ZONES
from ..utils.timeutils import iso
from .deps import require_api_key

router = APIRouter(prefix="/v1", tags=["platform"])


@router.get("/status")
def status(db: Session = Depends(get_db)):
    """Source freshness + fill rate — the trust dashboard (plan section 8)."""
    now = datetime.now(timezone.utc)
    out = {}
    for dataset, model, ts_col in [("prices_dayahead", PriceDayAhead, PriceDayAhead.ts_utc),
                                   ("load_actual", LoadActual, LoadActual.ts_utc),
                                   ("flows_physical", FlowPhysical, FlowPhysical.ts_utc)]:
        latest = db.execute(select(func.max(ts_col))).scalar_one_or_none()
        count = db.execute(select(func.count()).select_from(model)).scalar_one()
        lag_min = None
        if latest:
            if latest.tzinfo is None:
                latest = latest.replace(tzinfo=timezone.utc)
            lag_min = round((now - latest).total_seconds() / 60, 1)
        out[dataset] = {"latest_ts_utc": iso(latest) if latest else None,
                        "rows": count, "freshness_lag_min": lag_min,
                        "state": "ok" if count else "empty"}
    s = get_settings()
    return {"service": "nrg-flux-api", "time_utc": iso(now),
            "demo_mode": s.demo_mode, "datasets": out}


@router.get("/quality/events", dependencies=[Depends(require_api_key)])
def quality_events(limit: int = 50, db: Session = Depends(get_db)):
    rows = db.execute(select(DataQualityEvent)
                      .order_by(DataQualityEvent.detected_at_utc.desc())
                      .limit(limit)).scalars().all()
    return {"events": [{
        "dataset": r.dataset, "area_eic": r.area_eic, "severity": r.severity,
        "issue_type": r.issue_type, "details": r.details,
        "detected_at_utc": iso(r.detected_at_utc)} for r in rows]}


class WebhookCreate(BaseModel):
    url: HttpUrl
    events: list[str] = ["price.published", "outage.updated", "forecast.issued"]


@router.post("/webhooks", dependencies=[Depends(require_api_key)])
def create_webhook(body: WebhookCreate, db: Session = Depends(get_db)):
    wh = Webhook(url=str(body.url), events=",".join(body.events))
    db.add(wh)
    db.commit()
    return {"id": wh.id, "url": wh.url, "events": body.events, "active": True,
            "signing": "HMAC-SHA256 over the raw body with your webhook secret"}


# ------------------------------------------------------------- dashboard feed

@router.get("/dashboard/italy")
def dashboard_italy(db: Session = Depends(get_db)):
    """Single aggregate payload powering Italy Power Watch (one round trip)."""
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    h_start, h_end = now - timedelta(hours=72), now + timedelta(hours=36)

    names = {r.eic_code: r.display_name for r in db.execute(select(EicCode)).scalars()}
    short = {z: names.get(z, z).split("(")[-1].rstrip(")") for z in ITALY_ZONES}

    prices = db.execute(select(PriceDayAhead)
                        .where(PriceDayAhead.area_eic.in_(ITALY_ZONES),
                               PriceDayAhead.ts_utc >= h_start, PriceDayAhead.ts_utc < h_end)
                        .order_by(PriceDayAhead.ts_utc)).scalars().all()
    price_series: dict[str, list] = {short[z]: [] for z in ITALY_ZONES}
    for r in prices:
        price_series[short[r.area_eic]].append([iso(r.ts_utc), r.price_eur_mwh])

    load_rows = db.execute(select(LoadActual)
                           .where(LoadActual.area_eic == "10YIT-GRTN-----B",
                                  LoadActual.ts_utc >= h_start, LoadActual.ts_utc <= now)
                           .order_by(LoadActual.ts_utc)).scalars().all()
    load_series = [[iso(r.ts_utc), r.load_mw] for r in load_rows]

    flows = db.execute(select(FlowPhysical)
                       .where(FlowPhysical.ts_utc == now - timedelta(hours=1))).scalars().all()
    border_labels = {(f, t): lbl for f, t, lbl in IT_BORDERS}
    flow_now = [{"border": border_labels.get((r.from_area_eic, r.to_area_eic),
                                             f"{r.from_area_eic}→{r.to_area_eic}"),
                 "mw": round(r.mw, 0)} for r in flows]

    outage_rows = db.execute(select(Outage).order_by(Outage.unavailable_mw.desc())
                             .limit(10)).scalars().all()
    outages = [{"asset": r.asset_name, "zone": short.get(r.area_eic, r.area_eic),
                "kind": r.kind, "planned": r.planned, "mw": r.unavailable_mw,
                "fuel": r.fuel, "start": iso(r.start_utc), "end": iso(r.end_utc),
                "reason": r.reason} for r in outage_rows]

    return {"generated_at_utc": iso(datetime.now(timezone.utc)),
            "demo_mode": get_settings().demo_mode,
            "prices": price_series, "load": load_series,
            "flows_now": flow_now, "outages": outages}
