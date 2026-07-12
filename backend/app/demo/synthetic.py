"""Demo-mode data generator.

Produces statistically realistic Italian power-market data so the whole stack —
API, forecasting, explanations, dashboard — runs end-to-end before any
ENTSO-E/weather credentials are configured. Structure, units and shapes match
the real ingestion output exactly, so switching DEMO_MODE=false is seamless.

The load model: base + weekly/daily shape + temperature response (heating and
cooling degree effects) + holidays + noise. This mirrors the true drivers of
Italian demand and gives the ML model something honest to learn.
"""
from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone

import holidays as holidays_lib
import numpy as np

from ..utils.eic import ITALY_ZONES, IT_BORDERS
from ..utils.timeutils import MARKET_TZ, market_day

IT_HOLIDAYS = holidays_lib.country_holidays("IT")

# zone share of national load (approximate, sums to 1)
ZONE_SHARE = {
    "10Y1001A1001A73I": 0.53,  # NORD
    "10Y1001A1001A70O": 0.10,  # CNOR
    "10Y1001A1001A71M": 0.16,  # CSUD
    "10Y1001A1001A788": 0.09,  # SUD
    "10Y1001A1001A74G": 0.04,  # SARD
    "10Y1001A1001A75E": 0.08,  # SICI
}

DAILY_SHAPE = np.array([  # hourly multipliers, local time, weekday
    0.78, 0.74, 0.72, 0.71, 0.72, 0.76, 0.84, 0.94, 1.02, 1.07, 1.10, 1.11,
    1.09, 1.05, 1.03, 1.03, 1.05, 1.08, 1.12, 1.14, 1.10, 1.02, 0.93, 0.85,
])
WEEKEND_DAMP = np.array([
    0.85, 0.82, 0.80, 0.79, 0.79, 0.80, 0.82, 0.85, 0.90, 0.95, 0.98, 1.00,
    0.99, 0.96, 0.94, 0.93, 0.95, 0.99, 1.04, 1.07, 1.05, 0.99, 0.92, 0.88,
])


def _rng(seed: int = 42) -> random.Random:
    return random.Random(seed)


def temperature_at(ts_utc: datetime, lat_bias: float = 0.0, rng_state: np.random.Generator | None = None) -> float:
    """Smooth synthetic temperature (°C) with annual + diurnal cycles."""
    local = ts_utc.astimezone(MARKET_TZ)
    doy = local.timetuple().tm_yday
    annual = 14.5 + 10.5 * math.sin(2 * math.pi * (doy - 105) / 365.25)
    diurnal = 4.5 * math.sin(2 * math.pi * (local.hour - 9) / 24)
    weather_noise = 3.0 * math.sin(2 * math.pi * doy / 11.3) + 1.5 * math.sin(2 * math.pi * doy / 4.7)
    return round(annual + diurnal + weather_noise + lat_bias, 2)


def national_load_mw(ts_utc: datetime, noise: float = 0.0) -> float:
    """Italian national load in MW driven by calendar + temperature."""
    local = ts_utc.astimezone(MARKET_TZ)
    base = 33500.0
    shape = (WEEKEND_DAMP if local.weekday() >= 5 else DAILY_SHAPE)[local.hour]
    temp = temperature_at(ts_utc)
    hdd = max(0.0, 16.0 - temp)          # heating demand
    cdd = max(0.0, temp - 21.0)          # cooling demand (strong in Italy)
    thermo = 260.0 * hdd + 520.0 * cdd
    holiday_factor = 0.86 if local.date() in IT_HOLIDAYS else 1.0
    august_dip = 0.90 if local.month == 8 and 5 <= local.day <= 25 else 1.0
    return max(18000.0, (base * shape + thermo) * holiday_factor * august_dip + noise)


def dayahead_price(ts_utc: datetime, zone: str, load_mw: float) -> float:
    """Zonal day-ahead price loosely coupled to national load with zonal spreads."""
    local = ts_utc.astimezone(MARKET_TZ)
    stress = (load_mw - 30000) / 22000
    base = 78 + 55 * max(0.0, stress) ** 1.6
    solar_dip = -14 * math.exp(-((local.hour - 13) ** 2) / 8) if 8 <= local.hour <= 18 else 0
    evening_peak = 18 * math.exp(-((local.hour - 20) ** 2) / 5)
    zone_premium = {"10Y1001A1001A75E": 9.5, "10Y1001A1001A74G": 4.0,
                    "10Y1001A1001A788": -3.0, "10Y1001A1001A73I": 1.5}.get(zone, 0.0)
    doy = local.timetuple().tm_yday
    seasonal = 12 * math.sin(2 * math.pi * (doy - 20) / 365.25)
    return round(max(5.0, base + solar_dip + evening_peak + zone_premium + seasonal), 2)


def border_flow(ts_utc: datetime, border_idx: int) -> float:
    """Physical import flow into Italy (MW), typically importing."""
    local = ts_utc.astimezone(MARKET_TZ)
    caps = [3200, 4200, 800, 680, 500, 600]
    cap = caps[border_idx % len(caps)]
    util = 0.55 + 0.30 * math.sin(2 * math.pi * (local.hour - 7) / 24) \
        + 0.10 * math.sin(2 * math.pi * local.timetuple().tm_yday / 91)
    return round(cap * min(0.98, max(0.05, util)), 1)


def generate_history(start_utc: datetime, end_utc: datetime, seed: int = 42):
    """Yield dict rows for load, prices, flows across [start, end)."""
    np_rng = np.random.default_rng(seed)
    cur = start_utc.replace(minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
    while cur < end_utc:
        noise = float(np_rng.normal(0, 380))
        nat_load = national_load_mw(cur, noise)
        yield {"kind": "load", "area": "10YIT-GRTN-----B", "ts": cur, "mw": round(nat_load, 1)}
        for z in ITALY_ZONES:
            zl = nat_load * ZONE_SHARE[z] * float(np_rng.normal(1, 0.012))
            yield {"kind": "load", "area": z, "ts": cur, "mw": round(zl, 1)}
            yield {"kind": "price", "area": z, "ts": cur, "md": market_day(cur),
                   "eur": dayahead_price(cur, z, nat_load) * float(np_rng.normal(1, 0.02))}
        for i, (f, t, _) in enumerate(IT_BORDERS):
            yield {"kind": "flow", "from": f, "to": t, "ts": cur,
                   "mw": border_flow(cur, i) * float(np_rng.normal(1, 0.03))}
        cur += timedelta(hours=1)


def sample_outages(now_utc: datetime) -> list[dict]:
    rng = _rng(7)
    assets = [
        ("Montalto di Castro CCGT unit 2", "generation", "fossil gas", "10Y1001A1001A71M", 780),
        ("Priolo Gargallo CCGT unit 1", "generation", "fossil gas", "10Y1001A1001A75E", 360),
        ("Brindisi Sud unit 4", "generation", "fossil gas", "10Y1001A1001A788", 620),
        ("SACOI HVDC link pole 1", "transmission", "", "10Y1001A1001A74G", 250),
        ("La Casella CCGT unit 3", "generation", "fossil gas", "10Y1001A1001A73I", 410),
        ("Fiume Santo unit 3", "generation", "hard coal", "10Y1001A1001A74G", 320),
        ("Sorgente-Rizziconi 380kV line", "transmission", "", "10Y1001A1001A75E", 550),
    ]
    out = []
    for i, (name, kind, fuel, zone, mw) in enumerate(assets):
        start = now_utc - timedelta(days=rng.randint(0, 6), hours=rng.randint(0, 20))
        end = start + timedelta(days=rng.randint(1, 12), hours=rng.randint(0, 12))
        out.append({
            "outage_id": f"DEMO-{now_utc:%Y%m}-{i:03d}",
            "kind": kind, "planned": rng.random() > 0.4, "area_eic": zone,
            "asset_name": name, "fuel": fuel, "start_utc": start, "end_utc": end,
            "unavailable_mw": float(mw), "reason": "Planned maintenance" if rng.random() > 0.4 else "Forced outage",
        })
    return out
