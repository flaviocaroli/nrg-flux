"""Training history — read REAL data from the database.

Why this module exists
----------------------
The original train_forecast.py always trained on the synthetic demo generator,
even in live mode. That produced a model calibrated to fake load levels
(~31.5 GW base) while real Italian load runs 35-50 GW in July — a systematic
~12 GW bias, with a meaningless backtest measured against synthetic holdout.
This module makes the DB the default source of truth and keeps synthetic data
strictly for demo mode.

Weather history caveat
----------------------
We ingest FORECAST weather (MET Norway) but do not yet store weather HISTORY.
For live training we therefore use a smooth seasonal-diurnal climatology proxy
for temperature. It carries the shape of the year and the day but not actual
weather anomalies, so the temperature features are weaker than they should be.
The proper fix is an ERA5 backfill (see docs/weather.md) — until then, the
model leans on the load-lag features and the proxy is documented as a known
weakness in the model card.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
from sqlalchemy import select

from ..config import get_settings
from ..db.models import LoadActual, PriceDayAhead, SessionLocal

log = logging.getLogger("nrgflux.history")

MIN_TRAIN_DAYS = 45          # below this, a GBM on hourly load is unreliable


def stored_weather(area: str) -> pd.Series:
    """Observed temperature history from the DB (ERA5), empty if none."""
    from ..db.models import WeatherHistory
    db = SessionLocal()
    try:
        rows = db.execute(select(WeatherHistory)
                          .where(WeatherHistory.area_eic == area)
                          .order_by(WeatherHistory.ts_utc)).scalars().all()
    finally:
        db.close()
    if not rows:
        return pd.Series(dtype=float)
    idx = pd.DatetimeIndex([r.ts_utc.replace(tzinfo=timezone.utc)
                            if r.ts_utc.tzinfo is None else r.ts_utc
                            for r in rows], tz="UTC")
    return pd.Series([r.temp_c for r in rows], index=idx).sort_index()


def load_history(area: str, days: int = 730) -> pd.Series:
    """Hourly actual load (MW) for an area, straight from the DB."""
    db = SessionLocal()
    try:
        since = datetime.now(timezone.utc) - timedelta(days=days)
        rows = db.execute(select(LoadActual)
                          .where(LoadActual.area_eic == area,
                                 LoadActual.ts_utc >= since)
                          .order_by(LoadActual.ts_utc)).scalars().all()
    finally:
        db.close()
    if not rows:
        return pd.Series(dtype=float)
    idx = pd.DatetimeIndex([r.ts_utc.replace(tzinfo=timezone.utc)
                            if r.ts_utc.tzinfo is None else r.ts_utc
                            for r in rows], tz="UTC")
    s = pd.Series([r.load_mw for r in rows], index=idx).sort_index()
    s = s[~s.index.duplicated(keep="last")]
    return s.resample("h").mean().interpolate(limit=3).dropna()


def price_history(area: str, days: int = 730) -> pd.Series:
    db = SessionLocal()
    try:
        since = datetime.now(timezone.utc) - timedelta(days=days)
        rows = db.execute(select(PriceDayAhead)
                          .where(PriceDayAhead.area_eic == area,
                                 PriceDayAhead.ts_utc >= since)
                          .order_by(PriceDayAhead.ts_utc)).scalars().all()
    finally:
        db.close()
    if not rows:
        return pd.Series(dtype=float)
    idx = pd.DatetimeIndex([r.ts_utc.replace(tzinfo=timezone.utc)
                            if r.ts_utc.tzinfo is None else r.ts_utc
                            for r in rows], tz="UTC")
    s = pd.Series([r.price_eur_mwh for r in rows], index=idx).sort_index()
    return s[~s.index.duplicated(keep="last")]


def temperature_history(idx: pd.DatetimeIndex, area: str = "") -> pd.Series:
    """Temperature aligned to a load index.

    Demo mode  -> the synthetic generator (consistent with the demo load).
    Live mode  -> seasonal-diurnal climatology proxy (see module docstring).
    """
    s = get_settings()
    if s.demo_mode:
        from ..demo.synthetic import temperature_at
        return pd.Series([temperature_at(t.to_pydatetime()) for t in idx], index=idx)

    # 1) real ERA5 history if we have it — this is what makes the model work
    real = stored_weather(area)
    if not real.empty:
        cov = real.reindex(idx).notna().mean()
        if cov > 0.9:
            log.info("using ERA5 weather history (%.0f%% coverage)", cov * 100)
            return real.reindex(idx).interpolate(limit=6).ffill().bfill()
        log.warning("ERA5 covers only %.0f%% of the training window — "
                    "backfill more years", cov * 100)

    # 2) fallback: climatology proxy. Documented, but roughly doubles the error.
    log.warning("No ERA5 weather history for %s — falling back to a climatology "
                "proxy. Expect ~2x error; the model may not beat naive. "
                "Fix with: python scripts/backfill_era5.py --area %s", area, area)
    from ..utils.eic import ZONE_CENTROIDS
    lat = ZONE_CENTROIDS.get(area, (43.0, 12.0))[0]
    # amplitude of the annual swing grows with latitude; phase peaks late July
    ann_amp = 6.0 + (lat - 36.0) * 0.35
    mean_t = 20.0 - (lat - 40.0) * 0.55
    doy = idx.dayofyear.values
    hour = idx.hour.values
    seasonal = ann_amp * np.sin((doy - 105) / 365.25 * 2 * np.pi)
    diurnal = 4.5 * np.sin((hour - 9) / 24 * 2 * np.pi)
    return pd.Series(mean_t + seasonal + diurnal, index=idx)


def training_series(area: str, source: str = "auto") -> tuple[pd.Series, pd.Series, str]:
    """Return (load, temperature, source_used).

    source: auto | db | synthetic
      auto -> DB when it holds >= MIN_TRAIN_DAYS of history, else synthetic
              (and synthetic is only acceptable in demo mode).
    """
    s = get_settings()
    if source in ("auto", "db"):
        load = load_history(area)
        days = len(load) / 24 if len(load) else 0
        if len(load) and days >= MIN_TRAIN_DAYS:
            temp = temperature_history(load.index, area)
            return load, temp, "database"
        if source == "db":
            raise RuntimeError(
                f"only {days:.1f} days of load history in the DB for {area} "
                f"(need >= {MIN_TRAIN_DAYS}). Run scripts/backfill_entsoe.py "
                f"--days 120 or scripts/backfill_eu.py first.")
        if not s.demo_mode:
            log.warning("Only %.1f days of real history for %s — falling back to "
                        "SYNTHETIC training data. The backtest will NOT reflect "
                        "your market. Backfill more history.", days, area)

    # synthetic fallback
    from ..demo.synthetic import national_load_mw, temperature_at
    end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    idx = pd.date_range(end - timedelta(days=730), end, freq="h", tz="UTC")
    rng = np.random.default_rng(42)
    load = pd.Series([national_load_mw(t.to_pydatetime(), float(rng.normal(0, 380)))
                      for t in idx], index=idx)
    temp = pd.Series([temperature_at(t.to_pydatetime()) for t in idx], index=idx)
    return load, temp, "synthetic"
