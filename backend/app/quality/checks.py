"""Data-quality engine (plan section 14, "Data quality" epic).

Checks implemented: missing intervals (gaps), stale sources, and simple
z-score outliers. Each finding is persisted as a DataQualityEvent and is
visible via /v1/quality/events and the status dashboard.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import DataQualityEvent, LoadActual, PriceDayAhead


def _tz(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def check_gaps(db: Session, area: str, hours: int = 72) -> int:
    """Detect missing hourly intervals in load_actual over the recent window."""
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = db.execute(select(LoadActual.ts_utc)
                      .where(LoadActual.area_eic == area, LoadActual.ts_utc >= since)
                      .order_by(LoadActual.ts_utc)).scalars().all()
    found = 0
    for prev, cur in zip(rows, rows[1:]):
        delta = (_tz(cur) - _tz(prev)).total_seconds() / 3600
        if delta > 1.01:
            db.add(DataQualityEvent(dataset="load_actual", area_eic=area,
                                    ts_utc=_tz(prev), severity="warn", issue_type="gap",
                                    details=f"{delta - 1:.0f} missing hourly interval(s) after this point"))
            found += 1
    db.commit()
    return found


def check_stale(db: Session, dataset: str, model, max_lag_hours: float) -> bool:
    latest = db.execute(select(model.ts_utc).order_by(model.ts_utc.desc()).limit(1)
                        ).scalar_one_or_none()
    if latest is None:
        return False
    lag = (datetime.now(timezone.utc) - _tz(latest)).total_seconds() / 3600
    if lag > max_lag_hours:
        db.add(DataQualityEvent(dataset=dataset, severity="error", issue_type="stale",
                                details=f"latest point is {lag:.1f}h old (limit {max_lag_hours}h)"))
        db.commit()
        return True
    return False


def check_price_outliers(db: Session, area: str, z: float = 5.0, days: int = 30) -> int:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = db.execute(select(PriceDayAhead)
                      .where(PriceDayAhead.area_eic == area, PriceDayAhead.ts_utc >= since)
                      ).scalars().all()
    if len(rows) < 100:
        return 0
    vals = np.array([r.price_eur_mwh for r in rows])
    mu, sd = vals.mean(), vals.std() or 1.0
    found = 0
    for r in rows:
        if abs(r.price_eur_mwh - mu) / sd > z:
            db.add(DataQualityEvent(dataset="prices_dayahead", area_eic=area,
                                    ts_utc=_tz(r.ts_utc), severity="warn", issue_type="outlier",
                                    details=f"price {r.price_eur_mwh:.2f} EUR/MWh, z={abs(r.price_eur_mwh-mu)/sd:.1f}"))
            found += 1
    db.commit()
    return found
