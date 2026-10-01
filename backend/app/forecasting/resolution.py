"""Resolution normalization shared by forecast training and serving."""
from __future__ import annotations

import pandas as pd


def to_hourly_mean(series: pd.Series, interpolate_limit: int = 3) -> pd.Series:
    """Return a sorted, unique, hourly mean series.

    Energy-market inputs can change cadence over time (for example, hourly to
    quarter-hourly). Forecast features use row lags such as 24 and 168, so they
    must always receive one row per hour. Duplicate source revisions keep the
    last observed value before aggregation. Short gaps are interpolated; longer
    gaps remain absent and are removed rather than silently filled indefinitely.
    """
    if series.empty:
        return pd.Series(dtype=float)
    normalized = series.sort_index()
    normalized = normalized[~normalized.index.duplicated(keep="last")]
    hourly = normalized.resample("h").mean()
    if interpolate_limit > 0:
        hourly = hourly.interpolate(limit=interpolate_limit)
    return hourly.dropna()
