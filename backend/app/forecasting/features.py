"""Feature engineering for the explainable load forecast (section 9, step 6).

Features: lagged load (previous day / previous week same hour), rolling means,
temperature + heating/cooling degrees, calendar (weekday, month, hour, holiday),
and DST flags. All timestamps UTC; calendar features computed in market time.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import holidays as holidays_lib
import numpy as np
import pandas as pd

from ..utils.timeutils import MARKET_TZ

IT_HOLIDAYS = holidays_lib.country_holidays("IT")

FEATURES = [
    "hour", "weekday", "month", "is_weekend", "is_holiday", "is_dst",
    "temp_c", "hdd", "cdd", "temp_anomaly",
    "load_lag_24h", "load_lag_168h", "load_roll_24h_mean", "load_roll_168h_mean",
]

FRIENDLY = {
    "hour": "hour of day", "weekday": "day of week", "month": "month",
    "is_weekend": "weekend", "is_holiday": "public holiday", "is_dst": "daylight saving",
    "temp_c": "temperature", "hdd": "heating degrees", "cdd": "cooling degrees",
    "temp_anomaly": "temperature anomaly",
    "load_lag_24h": "load yesterday same hour",
    "load_lag_168h": "load last week same hour",
    "load_roll_24h_mean": "24h average load", "load_roll_168h_mean": "7-day average load",
}


def calendar_frame(ts_index: pd.DatetimeIndex) -> pd.DataFrame:
    local = ts_index.tz_convert(MARKET_TZ)
    df = pd.DataFrame(index=ts_index)
    df["hour"] = local.hour
    df["weekday"] = local.weekday
    df["month"] = local.month
    df["is_weekend"] = (local.weekday >= 5).astype(int)
    df["is_holiday"] = np.array([d.date() in IT_HOLIDAYS for d in local]).astype(int)
    df["is_dst"] = np.array([bool(d.dst()) for d in local]).astype(int)
    return df


def build_training_frame(load: pd.Series, temp: pd.Series) -> pd.DataFrame:
    """load/temp: UTC-indexed hourly series. Returns frame with FEATURES + y."""
    load = load.sort_index()
    df = calendar_frame(load.index)
    df["temp_c"] = temp.reindex(load.index).interpolate(limit=3)
    df["hdd"] = (16.0 - df["temp_c"]).clip(lower=0)
    df["cdd"] = (df["temp_c"] - 21.0).clip(lower=0)
    clim = df.groupby([df["month"], df["hour"]])["temp_c"].transform("mean")
    df["temp_anomaly"] = df["temp_c"] - clim
    df["load_lag_24h"] = load.shift(24)
    df["load_lag_168h"] = load.shift(168)
    df["load_roll_24h_mean"] = load.shift(1).rolling(24).mean()
    df["load_roll_168h_mean"] = load.shift(1).rolling(168).mean()
    df["y"] = load
    return df.dropna()


def build_inference_frame(history: pd.Series, temp_forecast: pd.Series,
                          horizon_hours: int) -> pd.DataFrame:
    """Build feature rows for the next `horizon_hours` after the end of history.

    Uses recursive-safe lags: 24h/168h lags fall back to the most recent
    observed value for the same hour when the true lag is inside the horizon.
    """
    history = history.sort_index()
    last = history.index[-1]
    future_idx = pd.date_range(last + timedelta(hours=1), periods=horizon_hours,
                               freq="h", tz="UTC")
    df = calendar_frame(future_idx)
    df["temp_c"] = temp_forecast.reindex(future_idx).interpolate(limit=6).bfill().ffill()
    df["hdd"] = (16.0 - df["temp_c"]).clip(lower=0)
    df["cdd"] = (df["temp_c"] - 21.0).clip(lower=0)
    temp_hist_mean = df.groupby(["month", "hour"])["temp_c"].transform("mean")
    df["temp_anomaly"] = (df["temp_c"] - temp_hist_mean).fillna(0.0)

    def latest_same_hour(ts: pd.Timestamp, step_h: int) -> float:
        """Most recent observed value at ts - k*step_h (k>=1)."""
        t = ts - timedelta(hours=step_h)
        first = history.index[0]
        while t not in history.index and t > first:
            t -= timedelta(hours=step_h)
        return float(history.get(t, history.iloc[-step_h:].mean()))

    df["load_lag_24h"] = [latest_same_hour(ts, 24) for ts in future_idx]
    df["load_lag_168h"] = [latest_same_hour(ts, 168) for ts in future_idx]
    # beyond the history edge, hold the last known rolling means constant
    df["load_roll_24h_mean"] = float(history.tail(24).mean())
    df["load_roll_168h_mean"] = float(history.tail(168).mean())
    return df[FEATURES].fillna(0.0)
