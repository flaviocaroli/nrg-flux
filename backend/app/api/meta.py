"""Status, data-quality, webhooks and the dashboard aggregate endpoint."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, HttpUrl
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.models import (DataQualityEvent, EicCode, FlowPhysical, LoadActual,
                         Outage, PriceDayAhead, Webhook, get_db)
from ..utils.eic import (EU_BORDERS, EU_MARKETS, IT_BORDERS, ITALY_ZONES,
                         ZONE_SHORT)
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


@router.get("/account/usage")
def account_usage(ctx=Depends(require_api_key), db: Session = Depends(get_db)):
    """Let a client inspect their own plan, limits, and today's usage."""
    from ..services.auth_service import PLANS, usage_breakdown, usage_today
    if ctx.client_id is None:
        return {"plan": ctx.plan, "limits": PLANS["internal"],
                "note": "internal/dev key — unmetered"}
    used = usage_today(db, ctx.client_id)
    limits = PLANS.get(ctx.plan, PLANS["free"])
    return {"client": ctx.name, "plan": ctx.plan,
            "limits": {"per_minute": limits["per_minute"],
                       "per_day": limits["per_day"]},
            "used_today": used,
            "remaining_today": (limits["per_day"] - used) if limits["per_day"] else None,
            "resets_at_utc": "00:00",
            "by_endpoint": usage_breakdown(db, ctx.client_id)}


# ------------------------------------------------------------- dashboard feed

@router.get("/dashboard/italy")
def dashboard_italy(db: Session = Depends(get_db)):
    """Single aggregate payload powering Italy Power Watch (one round trip)."""
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    h_start, h_end = now - timedelta(hours=72), now + timedelta(hours=36)

    names = {r.eic_code: r.display_name for r in db.execute(select(EicCode)).scalars()}
    short = {z: (names[z].split("(")[-1].rstrip(")") if z in names
                 else ZONE_SHORT.get(z, z)) for z in ITALY_ZONES}

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

    # Latest observation per border within the last 48h. Live ENTSO-E flows
    # publish with delay, so an exact `now - 1h` match would often be empty.
    flow_rows = db.execute(select(FlowPhysical)
                           .where(FlowPhysical.ts_utc >= now - timedelta(hours=48),
                                  FlowPhysical.ts_utc <= now + timedelta(hours=1))
                           .order_by(FlowPhysical.ts_utc)).scalars().all()
    latest_per_border: dict[tuple, FlowPhysical] = {}
    for r in flow_rows:
        latest_per_border[(r.from_area_eic, r.to_area_eic)] = r  # last wins (sorted)

    # Label EVERY border we know about, not just the Italian ones — once other
    # EU markets are backfilled their flows land in the same table and would
    # otherwise render as raw EIC codes.
    border_labels: dict[tuple, str] = {}
    for _cc, _bl in EU_BORDERS.items():
        for f, t, lbl in _bl:
            border_labels[(f, t)] = lbl

    def _lbl(k):
        return border_labels.get(k) or (f"{ZONE_SHORT.get(k[0], k[0])} → "
                                        f"{ZONE_SHORT.get(k[1], k[1])}")

    it_keys = {(f, t) for f, t, _ in IT_BORDERS}
    # flows_now = Italian borders only (this is Italy Power Watch; the bar
    # chart must not fill up with DE→NL). flows_eu = everything, for the map.
    flow_now = [{"border": _lbl(k), "mw": round(r.mw, 0), "ts_utc": iso(r.ts_utc)}
                for k, r in latest_per_border.items() if k in it_keys]
    flow_eu = [{"border": _lbl(k), "from": k[0], "to": k[1],
                "mw": round(r.mw, 0), "ts_utc": iso(r.ts_utc)}
               for k, r in latest_per_border.items()]

    # only outages that are ongoing or ended within the last 3 days
    outage_rows = db.execute(select(Outage)
                             .where(
                                 Outage.kind == "generation",
                                 Outage.start_utc <= now,
                                 Outage.end_utc > now,
                                 Outage.unavailable_mw >= 0,
                                 Outage.unavailable_mw < 10_000,
                             )
                             .order_by(Outage.unavailable_mw.desc())
                             .limit(10)).scalars().all()
    outages = [{"asset": r.asset_name, "zone": short.get(r.area_eic) or ZONE_SHORT.get(r.area_eic, r.area_eic),
                "kind": r.kind, "planned": r.planned, "mw": r.unavailable_mw,
                "fuel": r.fuel, "start": iso(r.start_utc), "end": iso(r.end_utc),
                "reason": r.reason} for r in outage_rows]

    # ---- other EU markets (populated by scripts/backfill_eu.py) ----
    # Latest known day-ahead price per market so the EU map can colour them.
    markets: dict[str, dict] = {}
    for cc, m in EU_MARKETS.items():
        if cc == "IT":
            continue
        zone = m["zones"][0]
        row = db.execute(select(PriceDayAhead)
                         .where(PriceDayAhead.area_eic == zone,
                                PriceDayAhead.ts_utc <= now + timedelta(hours=36))
                         .order_by(PriceDayAhead.ts_utc.desc())
                         .limit(1)).scalar_one_or_none()
        ld = db.execute(select(LoadActual)
                        .where(LoadActual.area_eic == m["national"])
                        .order_by(LoadActual.ts_utc.desc())
                        .limit(1)).scalar_one_or_none()
        if row or ld:
            markets[cc] = {
                "name": m["name"], "zone": zone,
                "price": round(row.price_eur_mwh, 2) if row else None,
                "price_ts_utc": iso(row.ts_utc) if row else None,
                "load_mw": round(ld.load_mw, 0) if ld else None,
                "currency": m["currency"],
            }

    return {"generated_at_utc": iso(datetime.now(timezone.utc)),
            "demo_mode": get_settings().demo_mode,
            "prices": price_series, "load": load_series,
            "flows_now": flow_now, "flows_eu": flow_eu,
            "markets": markets, "outages": outages}


@router.get("/markets")
def markets_catalog(db: Session = Depends(get_db)):
    """What can the UI actually show?

    Lists every EU market we know about, whether it has data backfilled, and
    whether a load / price model has been trained and issued for it. The
    frontend uses this to build its market selector instead of hard-coding
    Italy.
    """
    import os

    from ..db.models import ForecastLoad, ForecastPrice
    from ..utils.eic import EU_MARKETS, ZONE_SHORT

    s = get_settings()
    out = []
    for cc, m in EU_MARKETS.items():
        nat, zones = m["national"], m["zones"]
        load_rows = db.execute(select(func.count()).select_from(LoadActual)
                               .where(LoadActual.area_eic == nat)).scalar_one()
        price_rows = db.execute(select(func.count()).select_from(PriceDayAhead)
                                .where(PriceDayAhead.area_eic.in_(zones))).scalar_one()
        has_load_fc = db.execute(select(func.count()).select_from(ForecastLoad)
                                 .where(ForecastLoad.area_eic == nat)).scalar_one() > 0
        has_price_fc = db.execute(select(func.count()).select_from(ForecastPrice)
                                  .where(ForecastPrice.area_eic.in_(zones))).scalar_one() > 0
        card = os.path.join(s.model_dir, f"model_card_{nat}.json")
        pcard = os.path.join(s.model_dir, f"model_card_price_{zones[0]}.json")
        out.append({
            "country": cc, "name": m["name"], "currency": m["currency"],
            "national_eic": nat,
            "zones": [{"eic": z, "label": ZONE_SHORT.get(z, z)} for z in zones],
            "load_rows": load_rows, "price_rows": price_rows,
            "has_load_forecast": has_load_fc,
            "has_price_forecast": has_price_fc,
            "load_model_trained": os.path.exists(card),
            "price_model_trained": os.path.exists(pcard),
            "ready": bool(load_rows and price_rows),
        })
    out.sort(key=lambda r: (not r["ready"], r["country"] != "IT", r["country"]))
    return {"markets": out,
            "hint": "Backfill a market with scripts/backfill_eu.py --markets XX, "
                    "then train with scripts/train_forecast.py --area <national_eic>"}
