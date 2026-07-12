"""NESO (GB National Energy System Operator) Data Portal client.

First step of the plan's "expand coverage" phase (weeks 11-12): Great Britain
demand data alongside the Italian ENTSO-E core.

The NESO Data Portal is a CKAN instance. Data access is via
`/api/3/action/datastore_search` (paged) or `datastore_search_sql`.
Most open datasets require no API key; an account key (env NESO_API_KEY,
sent as the Authorization header) raises rate limits and unlocks
authenticated endpoints.

Datasets used here:
  * Historic Demand Data — half-hourly national demand (ND / TSD), per year.
    Resource IDs differ per year and occasionally change; pass the resource id
    for the year you need (find it on the dataset page) or use the
    convenience defaults below and update them if NESO rotates them.

GB area is mapped to EIC 10YGB----------A and demand is normalized from
half-hourly settlement periods to UTC-hour averages so it fits the same
`load_actual` table and forecasting pipeline as the Italian zones.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx
import pandas as pd

from ..config import get_settings

log = logging.getLogger("nrgflux.neso")

GB_EIC = "10YGB----------A"

# NESO "Historic Demand Data" resource ids by year. NESO occasionally rotates
# these; check https://www.neso.energy/data-portal/historic-demand-data and
# update if a request returns 404.
HISTORIC_DEMAND_RESOURCES = {
    2026: "b2bde559-3455-4021-b179-dfe60c0337b0",   # historic_demand_data_2026
    2025: "f6d02c0f-957b-48cb-82ee-09003f2ba759",   # historic_demand_data_2025
}


class NesoClient:
    def __init__(self, api_key: str | None = None):
        s = get_settings()
        self.base = s.neso_base_url
        self.api_key = api_key or s.neso_api_key

    def _headers(self) -> dict:
        # CKAN accepts the key in the Authorization header
        return {"Authorization": self.api_key} if self.api_key else {}

    def datastore_search(self, resource_id: str, limit: int = 10000,
                         offset: int = 0, **filters) -> dict:
        params = {"resource_id": resource_id, "limit": limit, "offset": offset}
        if filters:
            import json
            params["filters"] = json.dumps(filters)
        with httpx.Client(timeout=60, headers=self._headers()) as client:
            r = client.get(f"{self.base}/api/3/action/datastore_search", params=params)
            r.raise_for_status()
            data = r.json()
        if not data.get("success"):
            raise RuntimeError(f"NESO datastore_search failed: {data}")
        return data["result"]

    def datastore_sql(self, sql: str) -> list[dict]:
        with httpx.Client(timeout=60, headers=self._headers()) as client:
            r = client.get(f"{self.base}/api/3/action/datastore_search_sql",
                           params={"sql": sql})
            r.raise_for_status()
            data = r.json()
        if not data.get("success"):
            raise RuntimeError(f"NESO datastore_search_sql failed: {data}")
        return data["result"]["records"]

    # ---------------------------------------------------------------- demand

    def gb_demand(self, start: datetime, end: datetime) -> pd.Series:
        """GB national demand (ND, MW) as an hourly UTC series.

        Source rows are half-hourly settlement periods (SETTLEMENT_DATE,
        SETTLEMENT_PERIOD 1..48/46/50 on clock-change days, ND in MW).
        Settlement periods are defined in local (Europe/London) time; we
        convert each period to its UTC start and average to hourly.
        """
        frames = []
        for year in range(start.year, end.year + 1):
            rid = HISTORIC_DEMAND_RESOURCES.get(year)
            if not rid:
                log.warning("No NESO resource id configured for %s — see "
                            "HISTORIC_DEMAND_RESOURCES", year)
                continue
            records, offset = [], 0
            while True:
                result = self.datastore_search(rid, limit=32000, offset=offset)
                records.extend(result["records"])
                if len(result["records"]) < 32000:
                    break
                offset += 32000
            if records:
                frames.append(pd.DataFrame(records))
        if not frames:
            return pd.Series(dtype=float)

        df = pd.concat(frames, ignore_index=True)
        df.columns = [c.upper() for c in df.columns]
        date = pd.to_datetime(df["SETTLEMENT_DATE"])
        # settlement period n starts at (n-1) * 30min after local midnight
        local_start = date + pd.to_timedelta((df["SETTLEMENT_PERIOD"].astype(int) - 1) * 30,
                                             unit="m")
        ts_utc = (local_start.dt.tz_localize("Europe/London", ambiguous=True,
                                             nonexistent="shift_forward")
                  .dt.tz_convert("UTC"))
        ser = (pd.Series(pd.to_numeric(df["ND"], errors="coerce").values, index=ts_utc)
               .sort_index()
               .resample("h").mean()
               .dropna())
        return ser[(ser.index >= pd.Timestamp(start, tz="UTC")) &
                   (ser.index < pd.Timestamp(end, tz="UTC"))]
