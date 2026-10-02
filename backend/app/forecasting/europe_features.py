"""Issue-time-safe features for the Italian day-ahead load benchmark.

The benchmark predicts the 24 hours after a forecast origin.  Every feature at
target hour ``t`` is therefore derived from information no later than
``t - 24h``.  This is deliberately stricter than a conventional retrospective
holdout and prevents realised values inside the forecast day from leaking into
later horizons.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .features import calendar_frame


@dataclass(frozen=True)
class NeighbourSpec:
    country: str
    name: str
    load_area: str
    flow_from: str
    flow_to: str


ITALY_AREA = "10YIT-GRTN-----B"

# Ordered for the cumulative ablation required by the submission evidence.
ITALY_NEIGHBOURS: tuple[NeighbourSpec, ...] = (
    NeighbourSpec("FR", "France", "10YFR-RTE------C",
                  "10YFR-RTE------C", "10Y1001A1001A73I"),
    NeighbourSpec("CH", "Switzerland", "10YCH-SWISSGRIDZ",
                  "10YCH-SWISSGRIDZ", "10Y1001A1001A73I"),
    NeighbourSpec("AT", "Austria", "10YAT-APG------L",
                  "10YAT-APG------L", "10Y1001A1001A73I"),
    NeighbourSpec("SI", "Slovenia", "10YSI-ELES-----O",
                  "10YSI-ELES-----O", "10Y1001A1001A73I"),
    NeighbourSpec("GR", "Greece", "10YGR-HTSO-----Y",
                  "10YGR-HTSO-----Y", "10Y1001A1001A788"),
)


CALENDAR_FEATURES = [
    "hour", "weekday", "month", "is_weekend", "is_holiday", "is_dst",
    "hour_sin", "hour_cos", "week_sin", "week_cos",
]

DOMESTIC_FEATURES = CALENDAR_FEATURES + [
    "load_lag_24h", "load_lag_168h",
    "load_issue_mean_24h", "load_issue_mean_168h",
    "temp_lag_24h", "temp_lag_168h", "temp_issue_mean_24h",
]


def neighbour_feature_names(country: str) -> list[str]:
    prefix = country.lower()
    return [
        f"{prefix}_load_lag_24h",
        f"{prefix}_load_lag_168h",
        f"{prefix}_load_issue_mean_24h",
        f"{prefix}_flow_lag_24h",
        f"{prefix}_flow_lag_168h",
        f"{prefix}_flow_issue_mean_24h",
    ]


def feature_sets() -> dict[str, list[str]]:
    """Cumulative Domestic -> EU-5 feature sets in the declared order."""
    sets = {"lgbm_domestic": list(DOMESTIC_FEATURES)}
    current = list(DOMESTIC_FEATURES)
    for i, spec in enumerate(ITALY_NEIGHBOURS, start=1):
        current = current + neighbour_feature_names(spec.country)
        sets[f"lgbm_eu_{i}"] = list(current)
    return sets


def _issue_mean(series: pd.Series, hours: int) -> pd.Series:
    # At target t, shift(24) ends at the D+1 issue boundary t-24.
    return series.shift(24).rolling(hours, min_periods=hours).mean()


def build_issue_time_frame(
    italy_load: pd.Series,
    temperature: pd.Series,
    neighbour_loads: dict[str, pd.Series],
    import_flows: dict[str, pd.Series],
) -> pd.DataFrame:
    """Return one hourly supervised frame with no post-cutoff features.

    ``import_flows`` uses the ENTSO-E query direction foreign-area -> Italian
    bidding zone.  Missing reverse queries are not interpreted as zero.
    """
    italy = italy_load.sort_index()
    df = calendar_frame(italy.index)
    df["hour_sin"] = np.sin(2 * np.pi * df["hour"] / 24.0)
    df["hour_cos"] = np.cos(2 * np.pi * df["hour"] / 24.0)
    week_hour = df["weekday"] * 24 + df["hour"]
    df["week_sin"] = np.sin(2 * np.pi * week_hour / 168.0)
    df["week_cos"] = np.cos(2 * np.pi * week_hour / 168.0)

    df["load_lag_24h"] = italy.shift(24)
    df["load_lag_168h"] = italy.shift(168)
    df["load_issue_mean_24h"] = _issue_mean(italy, 24)
    df["load_issue_mean_168h"] = _issue_mean(italy, 168)

    temp = temperature.reindex(italy.index).sort_index()
    df["temp_lag_24h"] = temp.shift(24)
    df["temp_lag_168h"] = temp.shift(168)
    df["temp_issue_mean_24h"] = _issue_mean(temp, 24)

    for spec in ITALY_NEIGHBOURS:
        prefix = spec.country.lower()
        load = neighbour_loads.get(spec.country, pd.Series(dtype=float))
        load = load.reindex(italy.index)
        flow = import_flows.get(spec.country, pd.Series(dtype=float))
        flow = flow.reindex(italy.index)

        df[f"{prefix}_load_lag_24h"] = load.shift(24)
        df[f"{prefix}_load_lag_168h"] = load.shift(168)
        df[f"{prefix}_load_issue_mean_24h"] = _issue_mean(load, 24)
        df[f"{prefix}_flow_lag_24h"] = flow.shift(24)
        df[f"{prefix}_flow_lag_168h"] = flow.shift(168)
        df[f"{prefix}_flow_issue_mean_24h"] = _issue_mean(flow, 24)

    df["y"] = italy
    df["naive_weekly"] = italy.shift(168)
    return df
