"""Weather ingestion for forecast features (plan section 9, steps 2-3).

Four providers, in the order we recommend using them:

1. MET Norway Locationforecast — clean JSON point forecasts, global coverage,
   free. Terms require an identifying User-Agent (set METNO_USER_AGENT).
   This is the default production source for D+1..D+7 temperature features.

2. NOAA GFS on AWS Open Data (noaa-gfs-bdp-pds) — anonymous S3, updated four
   times daily. GRIB2 files; full-grid processing needs cfgrib/eccodes, so this
   client only lists/downloads cycles and leaves decoding to an optional extra.

3. DWD Open Data (ICON model) — no key needed, HTTPS directory listing.

4. ECMWF Open Data — free forecast subset over HTTPS; a key is only needed for
   licensed archive datasets. Install the `ecmwf-opendata` extra for retrieval.

Note on credentials: MET Norway and the AWS GFS bucket do not require API keys
for these endpoints; optional tokens from your accounts are read from env vars
and attached only where a service actually uses them.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx
import pandas as pd

from ..config import get_settings

log = logging.getLogger("nrgflux.weather")


# --------------------------------------------------------------- MET Norway

def metno_point_forecast(lat: float, lon: float) -> pd.Series:
    """Hourly 2m temperature forecast (°C), UTC-indexed, ~9 days ahead."""
    s = get_settings()
    headers = {"User-Agent": s.metno_user_agent}
    if s.metno_api_key:
        headers["X-Api-Key"] = s.metno_api_key
    url = "https://api.met.no/weatherapi/locationforecast/2.0/compact"
    with httpx.Client(timeout=30, headers=headers) as client:
        r = client.get(url, params={"lat": round(lat, 4), "lon": round(lon, 4)})
        r.raise_for_status()
        data = r.json()
    rows = {}
    for step in data["properties"]["timeseries"]:
        ts = datetime.fromisoformat(step["time"].replace("Z", "+00:00"))
        rows[ts] = step["data"]["instant"]["details"].get("air_temperature")
    ser = pd.Series(rows).sort_index()
    ser.index = pd.DatetimeIndex(ser.index, tz="UTC")
    return ser.resample("h").interpolate(limit=6)


# --------------------------------------------------------------- NOAA GFS

def gfs_latest_cycle(now: datetime | None = None) -> tuple[str, str]:
    """Return (date 'YYYYMMDD', cycle 'HH') of the most recent GFS run."""
    now = now or datetime.now(timezone.utc)
    ref = now - timedelta(hours=5)  # publication latency margin
    cycle = (ref.hour // 6) * 6
    return ref.strftime("%Y%m%d"), f"{cycle:02d}"


def gfs_grib_url(date: str, cycle: str, fhour: int, resolution: str = "0p25") -> str:
    s = get_settings()
    return (f"{s.noaa_gfs_bucket}/gfs.{date}/{cycle}/atmos/"
            f"gfs.t{cycle}z.pgrb2.{resolution}.f{fhour:03d}")


def gfs_download(url: str, dest: str) -> str:
    with httpx.Client(timeout=300, follow_redirects=True) as client:
        with client.stream("GET", url) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    f.write(chunk)
    return dest
# Decoding GRIB2 -> zone temperature requires: pip install cfgrib xarray
# then population-weight with the Eurostat GISCO 1km grid (docs/weather.md).


# --------------------------------------------------------------- DWD (ICON)

def dwd_icon_index(model: str = "icon-eu", variable: str = "t_2m") -> list[str]:
    """List available ICON GRIB files for a variable (no key required)."""
    s = get_settings()
    url = f"{s.dwd_base_url}/weather/nwp/{model}/grib/00/{variable}/"
    with httpx.Client(timeout=30) as client:
        r = client.get(url)
        r.raise_for_status()
    import re
    return re.findall(r'href="([^"]+\.grib2\.bz2)"', r.text)


# --------------------------------------------------------------- ECMWF open data

def ecmwf_open_data_available() -> bool:
    try:
        import ecmwf.opendata  # noqa: F401  (pip install ecmwf-opendata)
        return True
    except ImportError:
        return False


def ecmwf_retrieve_t2m(target_dir: str) -> str | None:
    """Retrieve the latest 2m-temperature open-data forecast (optional extra)."""
    if not ecmwf_open_data_available():
        log.info("ecmwf-opendata not installed; skipping ECMWF retrieval")
        return None
    from ecmwf.opendata import Client
    client = Client(source="ecmwf")
    target = f"{target_dir}/ecmwf_t2m_latest.grib2"
    client.retrieve(type="fc", step=list(range(0, 145, 3)), param="2t", target=target)
    return target


# ------------------------------------------------- zone temperature service

def zone_temperature_forecast(zone_eic: str) -> pd.Series:
    """Production path: MET Norway point forecast at the zone centroid.

    Upgrade path (docs/weather.md): population-weighted GFS/ECMWF grids using
    the Eurostat GISCO 1 km population raster.
    """
    from ..utils.eic import ZONE_CENTROIDS
    lat, lon = ZONE_CENTROIDS.get(zone_eic, ZONE_CENTROIDS["10YIT-GRTN-----B"])
    return metno_point_forecast(lat, lon)
