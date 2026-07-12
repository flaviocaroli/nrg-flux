"""Shared API dependencies: key auth, lineage envelope, errors."""
from datetime import datetime, timezone

from fastapi import Header, HTTPException

from ..config import get_settings
from ..utils.timeutils import iso, market_day, parse_iso

SCHEMA_VERSION = "1.0.0"
PARSER_VERSION = "1.0.0"


def require_api_key(x_api_key: str = Header(default="")) -> str:
    s = get_settings()
    if s.environment == "development" and not x_api_key:
        return "dev"
    if x_api_key not in s.api_key_list():
        raise HTTPException(status_code=401, detail={
            "error": "invalid_api_key",
            "hint": "Pass your key in the X-Api-Key header. "
                    "Get a sandbox key from the NRG-Flux portal."})
    return x_api_key


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
