"""Historical weather (ERA5) — the missing training input.

TWO WAYS TO GET THE SAME DATA
-----------------------------
  provider="openmeteo"  (default, recommended)
      Open-Meteo's archive API serves ERA5 reanalysis over plain JSON.
      No API key, no registration, no queue. Free for non-commercial use;
      a paid commercial tier exists and is inexpensive — check their terms
      before you ship this in a product you charge for.

  provider="cds"
      Straight from the Copernicus Climate Data Store (the official source).
      Free but needs registration, licence acceptance and a shared queue.

Both return the SAME underlying ERA5 numbers. Start with openmeteo; move to
cds (or an Open-Meteo commercial plan) when you go paid.

NOTE ON YOUR EXISTING KEYS: MET Norway, GFS, DWD and ECMWF open-data are all
FORECAST feeds. They cannot train a model — training needs observed history.
That is a different dataset, not a permissions problem.

WHY ERA5 (and not MET Norway / GFS)
-----------------------------------
Those give FORECASTS — what the weather will be. To TRAIN a load model you
need HISTORY — what the weather actually was, hour by hour, aligned with the
load you observed. Without it the model cannot learn "how much MW does a
cooling degree add"; it can only learn calendar shape, which the naive
baseline already captures for free. That is exactly why the Italian load
model does not beat naive today.

ERA5 is the ECMWF reanalysis: hourly, global, 0.25 degree grid, 1940-present,
free via the Copernicus Climate Data Store. It is the de-facto standard for
energy demand research — a physically consistent reconstruction of past
weather, not a patchwork of station readings.

SETUP (once, ~5 minutes, free)
------------------------------
 1. Register: https://cds.climate.copernicus.eu
 2. Accept the ERA5 licence on the dataset page (required, or requests 403)
 3. Copy your key from your CDS profile page
 4. Put it in backend/.env:
        CDS_API_KEY=<uid>:<api-key>
 5. pip install cdsapi xarray netcdf4

Then:
        python scripts/backfill_era5.py --area 10YIT-GRTN-----B --years 2

NOTE ON QUEUE TIMES: CDS is a shared queue. A 2-year single-point request is
usually minutes; large areas can take longer. We request a small bounding box
around the zone centroid rather than the full grid, which keeps it fast.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime

import numpy as np
import pandas as pd

log = logging.getLogger("nrgflux.era5")


def openmeteo_history(lat: float, lon: float, years: int = 2) -> pd.Series:
    """Hourly 2m temperature (degC) from Open-Meteo's ERA5 archive. No key."""
    import httpx
    end = datetime.utcnow().date() - pd.Timedelta(days=6)   # archive lag ~5 days
    start = end - pd.Timedelta(days=365 * years)
    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": round(lat, 3), "longitude": round(lon, 3),
        "start_date": str(start), "end_date": str(end),
        "hourly": "temperature_2m", "timezone": "UTC",
    }
    with httpx.Client(timeout=120) as c:
        r = c.get(url, params=params)
        r.raise_for_status()
        j = r.json()
    if "hourly" not in j:
        raise RuntimeError(f"Open-Meteo returned no data: {str(j)[:200]}")
    idx = pd.DatetimeIndex(j["hourly"]["time"], tz="UTC")
    ser = pd.Series(j["hourly"]["temperature_2m"], index=idx, dtype=float)
    return ser.dropna().sort_index().rename("temp_c")


def cds_available() -> tuple[bool, str]:
    try:
        import cdsapi  # noqa: F401
    except ImportError:
        return False, "pip install cdsapi xarray netcdf4"
    from ..config import get_settings
    key = getattr(get_settings(), "cds_api_key", "")
    if not key:
        return False, ("CDS_API_KEY not set in backend/.env — register free at "
                       "https://cds.climate.copernicus.eu and accept the ERA5 licence")
    return True, ""


def fetch_era5_2m_temperature(lat: float, lon: float, years: int = 2,
                              cache_dir: str = "./era5_cache") -> pd.Series:
    """Hourly 2m temperature (deg C) at a point, UTC-indexed.

    Requests a tiny bounding box around the point (CDS requires an area, not a
    single coordinate) and takes the box mean. For a proper zone temperature,
    population-weight several points — see docs/weather.md.
    """
    ok, why = cds_available()
    if not ok:
        raise RuntimeError(why)
    import cdsapi
    import xarray as xr

    from ..config import get_settings
    os.makedirs(cache_dir, exist_ok=True)
    end = datetime.utcnow()
    yrs = [str(y) for y in range(end.year - years + 1, end.year + 1)]
    target = os.path.join(cache_dir, f"era5_{lat:.2f}_{lon:.2f}_{years}y.nc")

    if not os.path.exists(target):
        key = get_settings().cds_api_key
        c = cdsapi.Client(url="https://cds.climate.copernicus.eu/api", key=key)
        log.info("Requesting ERA5 from CDS (queue times vary) ...")
        c.retrieve(
            "reanalysis-era5-single-levels",
            {
                "product_type": "reanalysis",
                "variable": "2m_temperature",
                "year": yrs,
                "month": [f"{m:02d}" for m in range(1, 13)],
                "day": [f"{d:02d}" for d in range(1, 32)],
                "time": [f"{h:02d}:00" for h in range(24)],
                # small box around the point: CDS needs [N, W, S, E]
                "area": [round(lat + 0.25, 2), round(lon - 0.25, 2),
                         round(lat - 0.25, 2), round(lon + 0.25, 2)],
                "format": "netcdf",
            },
            target,
        )
    ds = xr.open_dataset(target)
    var = "t2m" if "t2m" in ds else list(ds.data_vars)[0]
    da = ds[var]
    dims = [d for d in da.dims if d not in ("time", "valid_time")]
    ser = da.mean(dim=dims).to_series() - 273.15          # K -> degC
    ser.index = pd.DatetimeIndex(ser.index).tz_localize("UTC") \
        if ser.index.tz is None else ser.index.tz_convert("UTC")
    return ser.sort_index().rename("temp_c")


def zone_temperature_history(area_eic: str, years: int = 2,
                             provider: str = "openmeteo") -> pd.Series:
    """ERA5 temperature history at a zone centroid, via either provider."""
    from ..utils.eic import ZONE_CENTROIDS
    lat, lon = ZONE_CENTROIDS.get(area_eic, (42.5, 12.5))
    if provider == "openmeteo":
        return openmeteo_history(lat, lon, years)
    if provider == "cds":
        return fetch_era5_2m_temperature(lat, lon, years)
    raise ValueError("provider must be 'openmeteo' or 'cds'")


def synthetic_notice(real: pd.Series | None) -> str:
    if real is None or real.empty:
        return ("NO real weather history — using a climatology proxy. Expect "
                "roughly 2x the forecast error and a model that may not beat "
                "naive. Run scripts/backfill_era5.py to fix this.")
    return f"ERA5 weather history: {len(real)} hours"
