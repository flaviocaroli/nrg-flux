"""Pure time-math helpers for the ingestion/retrain scheduler.

No DB, no I/O — everything here is deterministic and unit-tested, because
the one thing a scheduler must never get wrong is *when*. All wall-clock
reasoning happens in Europe/Rome (the market timezone used across the
codebase); everything returned is UTC.

The daily retrain is anchored to the day-ahead publication (~12:45 CET/CEST).
We schedule at 13:05 local so the normal case needs no retries, and let the
retrain job itself verify the data actually landed before training.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

MARKET_TZ = ZoneInfo("Europe/Rome")

# Local wall-clock time for the daily retrain: shortly after the ~12:45
# day-ahead publication. DST is handled by zoneinfo, so this is 11:05 UTC in
# summer and 12:05 UTC in winter without any code caring.
RETRAIN_LOCAL_HOUR = 13
RETRAIN_LOCAL_MINUTE = 5


def next_half_hour(now_utc: datetime) -> datetime:
    """The next :00 or :30 boundary strictly after ``now_utc`` (UTC)."""
    now_utc = now_utc.astimezone(timezone.utc)
    base = now_utc.replace(second=0, microsecond=0)
    if base.minute < 30:
        nxt = base.replace(minute=30)
    else:
        nxt = base.replace(minute=0) + timedelta(hours=1)
    if nxt <= now_utc:  # exactly on a boundary -> take the following one
        nxt += timedelta(minutes=30)
    return nxt


def next_hour(now_utc: datetime) -> datetime:
    """The next whole UTC hour strictly after ``now_utc``."""
    now_utc = now_utc.astimezone(timezone.utc)
    base = now_utc.replace(minute=0, second=0, microsecond=0)
    return base + timedelta(hours=1)


def next_daily_run(now_utc: datetime,
                   hour: int = RETRAIN_LOCAL_HOUR,
                   minute: int = RETRAIN_LOCAL_MINUTE) -> datetime:
    """Next occurrence of ``hour:minute`` Europe/Rome, returned in UTC.

    DST-correct by construction: we build the target on the *local* calendar
    and convert, rather than adding fixed UTC offsets.
    """
    now_local = now_utc.astimezone(MARKET_TZ)
    target = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now_local:
        target = (target + timedelta(days=1)).replace(hour=hour, minute=minute)
    return target.astimezone(timezone.utc)


def tomorrow_market_day(now_utc: datetime) -> str:
    """The market day the ~12:45 publication covers: local tomorrow, ISO date."""
    return (now_utc.astimezone(MARKET_TZ).date() + timedelta(days=1)).isoformat()
