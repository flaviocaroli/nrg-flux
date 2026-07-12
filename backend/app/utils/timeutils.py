"""Market-time handling (section 4/14: "Time normalization" epic).

Rules implemented here:
  * Everything is stored in UTC internally.
  * "Market day" follows the market timezone (Europe/Rome for Italian zones,
    Europe/Brussels as the general EU market-time convention). A market day
    starts at 22:00 UTC in summer (CEST) and 23:00 UTC in winter (CET).
  * DST transition days have 23 or 25 hourly points — never silently 24.
Golden tests for these rules live in tests/test_timeutils.py.
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

MARKET_TZ = ZoneInfo("Europe/Rome")
UTC = timezone.utc


def to_utc(dt: datetime) -> datetime:
    """Normalize any datetime to tz-aware UTC. Naive datetimes are assumed UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def market_day(ts_utc: datetime, tz: ZoneInfo = MARKET_TZ) -> str:
    """Return the market delivery day (YYYY-MM-DD) for a UTC timestamp."""
    return to_utc(ts_utc).astimezone(tz).strftime("%Y-%m-%d")


def market_day_bounds_utc(day: str, tz: ZoneInfo = MARKET_TZ) -> tuple[datetime, datetime]:
    """UTC [start, end) of a market day. Handles DST: length is 23/24/25 h."""
    y, m, d = (int(x) for x in day.split("-"))
    local_start = datetime(y, m, d, 0, 0, tzinfo=tz)
    local_end = datetime(y, m, d, 0, 0, tzinfo=tz) + timedelta(days=1)
    # normalize across the fold by round-tripping through UTC
    return local_start.astimezone(UTC), local_end.astimezone(UTC)


def hours_in_market_day(day: str, tz: ZoneInfo = MARKET_TZ) -> int:
    start, end = market_day_bounds_utc(day, tz)
    return int((end - start).total_seconds() // 3600)


def hourly_range(start_utc: datetime, end_utc: datetime) -> list[datetime]:
    """Inclusive-exclusive hourly UTC timestamps."""
    start_utc, end_utc = to_utc(start_utc), to_utc(end_utc)
    out, cur = [], start_utc.replace(minute=0, second=0, microsecond=0)
    while cur < end_utc:
        out.append(cur)
        cur += timedelta(hours=1)
    return out


def iso(dt: datetime) -> str:
    return to_utc(dt).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> datetime:
    return to_utc(datetime.fromisoformat(s.replace("Z", "+00:00")))
