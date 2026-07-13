"""Shared API dependencies: key auth, lineage envelope, errors."""
from datetime import datetime, timezone

from fastapi import Header, HTTPException, Request, Response

from ..config import get_settings
from ..db.models import SessionLocal
from ..services.auth_service import ClientContext, authenticate
from ..utils.timeutils import iso, market_day, parse_iso

SCHEMA_VERSION = "1.0.0"
PARSER_VERSION = "1.0.0"


def require_api_key(request: Request, response: Response,
                    x_api_key: str = Header(default="")):
    """Auth for all data endpoints.

    Accepts, in order:
      1. no key in development mode (local convenience)
      2. an internal key from the API_KEYS env list (unlimited, for you/dashboard)
      3. a customer key (nrgf_..., issued via scripts/manage_clients.py) —
         validated against the api_clients table with per-plan rate limits
         and daily quotas; usage is metered per endpoint.
    """
    s = get_settings()
    if not x_api_key:
        if s.environment == "development":
            return ClientContext(client_id=None, name="dev", plan="internal",
                                 per_minute=None, per_day=None)
        raise HTTPException(status_code=401, detail={
            "error": "missing_api_key",
            "hint": "Pass your key in the X-Api-Key header."})
    if x_api_key in s.api_key_list():
        return ClientContext(client_id=None, name="internal", plan="internal",
                             per_minute=None, per_day=None)

    db = SessionLocal()
    try:
        ctx = authenticate(db, x_api_key, endpoint=request.url.path)
    finally:
        db.close()
    if ctx.per_day is not None:
        response.headers["X-RateLimit-Limit"] = str(ctx.per_day)
        response.headers["X-RateLimit-Remaining"] = str(max(ctx.remaining_today or 0, 0))
    return ctx


def parse_window(start: str | None, end: str | None, default_hours: int = 48):
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    e = parse_iso(end) if end else now + timedelta(hours=36)
    s_ = parse_iso(start) if start else e - timedelta(hours=default_hours)
    if s_ >= e:
        raise HTTPException(422, detail={"error": "invalid_window",
                                         "hint": "start must be before end (ISO8601)"})
    return s_, e


def envelope(series: list[dict], dataset: str, source: str,
             demo: bool, document_type: str = "") -> dict:
    return {
        "series": series,
        "count": len(series),
        "schema_version": SCHEMA_VERSION,
        "lineage": {
            "source": source if not demo else "NRG-Flux demo generator (synthetic)",
            "dataset": dataset,
            "document_type": document_type,
            "parser_version": PARSER_VERSION,
            "demo_mode": demo,
        },
        "attribution": "Market data © ENTSO-E Transparency Platform. "
                       "Weather: MET Norway / NOAA GFS / DWD / ECMWF open data."
        if not demo else "Synthetic demonstration data — not for trading decisions.",
    }


def point(ts, value: float, unit: str, area_eic: str = "", area_name: str = "",
          extra: dict | None = None) -> dict:
    d = {
        "ts_utc": iso(ts),
        "market_day": market_day(ts),
        "market_timezone": "Europe/Rome",
        "value": round(float(value), 2),
        "unit": unit,
    }
    if area_eic:
        d["area_eic"] = area_eic
    if area_name:
        d["area_name"] = area_name
    if extra:
        d.update(extra)
    return d
