"""API-key management, rate limiting, and usage metering.

Key design
----------
* Key format: ``nrgf_<8-hex prefix>_<40-hex secret>``. Only a sha256 hash is
  stored; the plaintext is displayed once at creation (scripts/manage_clients.py).
* Lookup is by the indexed prefix, then constant-time hash comparison.
* Plans define a per-minute rate (token window, in-memory) and a per-day quota
  (persistent daily counters in api_usage_daily, also used for billing/metering).
* Env keys in ``API_KEYS`` keep working and are treated as the unlimited
  ``internal`` plan — they are for you and the dashboard, not for customers.

429 responses include Retry-After and X-RateLimit-* headers.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import ApiClient, ApiUsage

PLANS: dict[str, dict] = {
    "internal":   {"per_minute": None, "per_day": None},
    "free":       {"per_minute": 10,   "per_day": 500},
    "pro":        {"per_minute": 60,   "per_day": 10_000},
    "business":   {"per_minute": 300,  "per_day": 100_000},
    "enterprise": {"per_minute": 1200, "per_day": 2_000_000},
}

# in-memory per-minute buckets: {client_id: [minute_epoch, count]}
_minute_buckets: dict[int, list[int]] = {}


@dataclass
class ClientContext:
    client_id: int | None
    name: str
    plan: str
    per_minute: int | None
    per_day: int | None
    remaining_today: int | None = None


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


# ------------------------------------------------------------------ issuing

def create_client(db: Session, name: str, email: str = "", plan: str = "free",
                  notes: str = "") -> tuple[ApiClient, str]:
    """Create a client and return (row, plaintext_key). Show the key ONCE."""
    if plan not in PLANS or plan == "internal":
        raise ValueError(f"plan must be one of {[p for p in PLANS if p != 'internal']}")
    prefix = secrets.token_hex(4)
    secret = secrets.token_hex(20)
    full_key = f"nrgf_{prefix}_{secret}"
    client = ApiClient(name=name, email=email, plan=plan, notes=notes,
                       key_prefix=prefix, key_hash=_hash(full_key))
    db.add(client)
    db.commit()
    return client, full_key


def revoke_client(db: Session, key_prefix: str) -> bool:
    row = db.execute(select(ApiClient).where(ApiClient.key_prefix == key_prefix)
                     ).scalar_one_or_none()
    if not row:
        return False
    row.active = False
    row.revoked_at_utc = datetime.now(timezone.utc)
    db.commit()
    return True


# --------------------------------------------------------------- verifying

def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def usage_today(db: Session, client_id: int) -> int:
    total = db.execute(select(func.coalesce(func.sum(ApiUsage.count), 0))
                       .where(ApiUsage.client_id == client_id,
                              ApiUsage.day == _today())).scalar_one()
    return int(total)


def usage_breakdown(db: Session, client_id: int) -> list[dict]:
    rows = db.execute(select(ApiUsage)
                      .where(ApiUsage.client_id == client_id, ApiUsage.day == _today())
                      .order_by(ApiUsage.count.desc())).scalars().all()
    return [{"endpoint": r.endpoint, "count": r.count} for r in rows]


def _record_usage(db: Session, client_id: int, endpoint: str) -> None:
    row = db.execute(select(ApiUsage).where(
        ApiUsage.client_id == client_id, ApiUsage.day == _today(),
        ApiUsage.endpoint == endpoint)).scalar_one_or_none()
    if row:
        row.count += 1
    else:
        db.add(ApiUsage(client_id=client_id, day=_today(), endpoint=endpoint, count=1))
    db.commit()


def _check_minute_rate(client_id: int, per_minute: int) -> int:
    """Sliding one-minute window. Returns remaining; raises 429 if exhausted."""
    now_min = int(time.time() // 60)
    bucket = _minute_buckets.get(client_id)
    if bucket is None or bucket[0] != now_min:
        bucket = [now_min, 0]
        _minute_buckets[client_id] = bucket
    if bucket[1] >= per_minute:
        retry = 60 - int(time.time() % 60)
        raise HTTPException(status_code=429, detail={
            "error": "rate_limited",
            "hint": f"Per-minute limit of {per_minute} reached for your plan."},
            headers={"Retry-After": str(retry),
                     "X-RateLimit-Limit": str(per_minute),
                     "X-RateLimit-Remaining": "0"})
    bucket[1] += 1
    return per_minute - bucket[1]


def authenticate(db: Session, api_key: str, endpoint: str) -> ClientContext:
    """Validate a customer key, enforce limits, meter the request."""
    parts = api_key.split("_")
    if len(parts) != 3 or parts[0] != "nrgf":
        raise HTTPException(status_code=401, detail={
            "error": "invalid_api_key",
            "hint": "Pass your key in the X-Api-Key header (format nrgf_xxx_yyy)."})
    client = db.execute(select(ApiClient).where(ApiClient.key_prefix == parts[1])
                        ).scalar_one_or_none()
    if not client or not hmac.compare_digest(client.key_hash, _hash(api_key)):
        raise HTTPException(status_code=401, detail={"error": "invalid_api_key"})
    if not client.active:
        raise HTTPException(status_code=403, detail={
            "error": "key_revoked",
            "hint": "This key has been revoked. Contact support for a new one."})

    limits = PLANS.get(client.plan, PLANS["free"])
    remaining_min = None
    if limits["per_minute"] is not None:
        remaining_min = _check_minute_rate(client.id, limits["per_minute"])
    used = usage_today(db, client.id)
    if limits["per_day"] is not None and used >= limits["per_day"]:
        raise HTTPException(status_code=429, detail={
            "error": "daily_quota_exceeded",
            "hint": f"Daily quota of {limits['per_day']} calls reached "
                    f"({client.plan} plan). Resets at 00:00 UTC."},
            headers={"Retry-After": "3600",
                     "X-RateLimit-Limit": str(limits["per_day"]),
                     "X-RateLimit-Remaining": "0"})
    _record_usage(db, client.id, endpoint)
    remaining_day = (limits["per_day"] - used - 1) if limits["per_day"] else None

    return ClientContext(client_id=client.id, name=client.name, plan=client.plan,
                         per_minute=limits["per_minute"], per_day=limits["per_day"],
                         remaining_today=remaining_day)
