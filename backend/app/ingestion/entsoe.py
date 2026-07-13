"""ENTSO-E Transparency Platform client (build-plan days 2-5).

Implements the practices the plan calls for:
  * token auth via env var (ENTSOE_API_TOKEN)
  * request slicing (max window per request)
  * retry with exponential backoff, polite rate limiting
  * raw-first storage of every response for audit/reprocessing

Document types used:
  A44  day-ahead prices                (documentType=A44)
  A65  system total load               (processType A16=actual, A01=day-ahead fcst)
  A11  aggregated interconnector flows (physical)
  A09  scheduled exchanges
  A77/A80 outages (production/generation unavailability)

API reference: https://documenter.getpostman.com/view/7009892/2s93JtP3F6
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import get_settings
from ..parsers.entsoe_xml import (parse_flow_document, parse_load_document,
                                  parse_price_document,
                                  parse_unavailability_document)
from ..utils.timeutils import to_utc

log = logging.getLogger("nrgflux.entsoe")


class EntsoeClient:
    def __init__(self, token: str | None = None):
        s = get_settings()
        self.token = token or s.entsoe_api_token
        self.base = s.entsoe_base_url
        self.max_days = s.entsoe_max_days_per_request
        self.min_interval = 1.0 / max(s.entsoe_rps, 0.05)
        self._last_call = 0.0
        if not self.token:
            log.warning("ENTSOE_API_TOKEN not set — live ingestion disabled. "
                        "Register at transparency.entsoe.eu and set the token in .env")

    # ------------------------------------------------------------ transport

    @retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=2, min=2, max=60))
    def _get(self, params: dict) -> str:
        wait = self.min_interval - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()
        with httpx.Client(timeout=60) as client:
            r = client.get(self.base, params={"securityToken": self.token, **params})
        if r.status_code == 429:
            log.warning("ENTSO-E rate limit hit, backing off")
            raise httpx.HTTPStatusError("429", request=r.request, response=r)
        r.raise_for_status()
        return r.text

    def _sliced(self, start: datetime, end: datetime):
        """Yield (start, end) windows honoring the max request span."""
        cur = to_utc(start)
        end = to_utc(end)
        while cur < end:
            nxt = min(cur + timedelta(days=self.max_days), end)
            yield cur, nxt
            cur = nxt

    @staticmethod
    def _fmt(dt: datetime) -> str:
        return to_utc(dt).strftime("%Y%m%d%H%M")

    # ------------------------------------------------------------- datasets

    def dayahead_prices(self, area_eic: str, start: datetime, end: datetime):
        """Yield (raw_xml, rows) per slice. Rows: {ts_utc, price_eur_mwh, resolution}."""
        for s, e in self._sliced(start, end):
            xml = self._get({"documentType": "A44", "in_Domain": area_eic,
                             "out_Domain": area_eic,
                             "periodStart": self._fmt(s), "periodEnd": self._fmt(e)})
            yield xml, parse_price_document(xml)

    def actual_load(self, area_eic: str, start: datetime, end: datetime):
        for s, e in self._sliced(start, end):
            xml = self._get({"documentType": "A65", "processType": "A16",
                             "outBiddingZone_Domain": area_eic,
                             "periodStart": self._fmt(s), "periodEnd": self._fmt(e)})
            yield xml, parse_load_document(xml)

    def tso_load_forecast(self, area_eic: str, start: datetime, end: datetime):
        for s, e in self._sliced(start, end):
            xml = self._get({"documentType": "A65", "processType": "A01",
                             "outBiddingZone_Domain": area_eic,
                             "periodStart": self._fmt(s), "periodEnd": self._fmt(e)})
            yield xml, parse_load_document(xml)

    def physical_flows(self, from_eic: str, to_eic: str, start: datetime, end: datetime):
        for s, e in self._sliced(start, end):
            xml = self._get({"documentType": "A11", "in_Domain": to_eic,
                             "out_Domain": from_eic,
                             "periodStart": self._fmt(s), "periodEnd": self._fmt(e)})
            yield xml, parse_flow_document(xml)

    def scheduled_exchanges(self, from_eic: str, to_eic: str, start: datetime, end: datetime):
        for s, e in self._sliced(start, end):
            xml = self._get({"documentType": "A09", "in_Domain": to_eic,
                             "out_Domain": from_eic,
                             "periodStart": self._fmt(s), "periodEnd": self._fmt(e)})
            yield xml, parse_flow_document(xml)

    @retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=2, min=2, max=60))
    def _get_bytes(self, params: dict) -> bytes:
        wait = self.min_interval - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()
        with httpx.Client(timeout=120) as client:
            r = client.get(self.base, params={"securityToken": self.token, **params})
        if r.status_code == 429:
            raise httpx.HTTPStatusError("429", request=r.request, response=r)
        r.raise_for_status()
        return r.content

    def outages(self, area_eic: str, start: datetime, end: datetime,
                doc_type: str = "A80"):
        """Unavailability documents. A80 = generation, A77 = production,
        A78 = transmission. The API returns a ZIP of XML documents when more
        than one matches — both plain-XML and ZIP responses are handled.

        Yields (list_of_raw_xml_strings, list_of_outage_dicts) per slice.
        ENTSO-E caps these queries at 200 documents / 1 year per request;
        the standard slicing keeps us well inside that.
        """
        import io
        import zipfile
        for s, e in self._sliced(start, end):
            content = self._get_bytes({"documentType": doc_type,
                                       "biddingZone_Domain": area_eic,
                                       "periodStart": self._fmt(s),
                                       "periodEnd": self._fmt(e)})
            xmls: list[str] = []
            if content[:2] == b"PK":                      # ZIP archive
                with zipfile.ZipFile(io.BytesIO(content)) as zf:
                    for name in zf.namelist():
                        if name.lower().endswith(".xml"):
                            xmls.append(zf.read(name).decode("utf-8", "replace"))
            else:
                xmls.append(content.decode("utf-8", "replace"))
            rows: list[dict] = []
            for xml in xmls:
                try:
                    rows.extend(parse_unavailability_document(xml))
                except Exception as exc:               # keep the slice alive
                    log.warning("outage document parse failed: %s", exc)
            yield xmls, rows
