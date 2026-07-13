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

    class ApiError(RuntimeError):
        """ENTSO-E returned a definitive error (HTTP 400 etc.) with a reason."""
        def __init__(self, status: int, reason: str):
            super().__init__(f"HTTP {status}: {reason}")
            self.status, self.reason = status, reason

    @staticmethod
    def _ack_reason(body: bytes) -> str:
        """Extract Reason/text from an Acknowledgement error document."""
        try:
            from lxml import etree
            root = etree.fromstring(body)
            for el in root.iter():
                if isinstance(el.tag, str) and el.tag.endswith("text"):
                    return (el.text or "").strip()
        except Exception:
            pass
        return body[:200].decode("utf-8", "replace")

    def _get_bytes(self, params: dict) -> bytes:
        """GET raw bytes. Retries 429/5xx/network with backoff; a 400 is
        deterministic, so it raises ApiError immediately with ENTSO-E's
        stated reason instead of retrying."""
        last = "no attempt made"
        for attempt in range(4):
            wait = self.min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()
            try:
                with httpx.Client(timeout=120) as client:
                    r = client.get(self.base,
                                   params={"securityToken": self.token, **params})
            except httpx.HTTPError as exc:
                last = f"network error: {exc}"
                time.sleep(2 ** (attempt + 1))
                continue
            if r.status_code == 400:
                raise self.ApiError(400, self._ack_reason(r.content))
            if r.status_code in (401, 403):
                raise self.ApiError(r.status_code,
                                    "unauthorized — token missing/invalid or Web API "
                                    "access not yet approved by ENTSO-E")
            if r.status_code == 429 or r.status_code >= 500:
                last = f"HTTP {r.status_code}"
                log.warning("ENTSO-E %s, backing off (attempt %d)", last, attempt + 1)
                time.sleep(2 ** (attempt + 1))
                continue
            r.raise_for_status()
            return r.content
        raise self.ApiError(0, f"retries exhausted ({last})")

    _TOO_MANY_MARKERS = ("200 documents", "amount of requested data", "exceeds")

    def outages(self, area_eic: str, start: datetime, end: datetime,
                doc_type: str = "A80",
                in_domain: str | None = None, out_domain: str | None = None):
        """Unavailability documents.

        A80/A77 (generation/production): query by biddingZone_Domain=area_eic.
        A78 (transmission): ENTSO-E requires a border — pass in_domain and
        out_domain instead of an area.

        The endpoint caps responses at 200 documents; busy zones exceed that
        easily, so windows start at 7 days and bisect automatically down to
        1 day whenever the cap is hit. Responses may be a single XML or a ZIP
        of XMLs — both are handled.

        Yields (list_of_raw_xml_strings, list_of_outage_dicts) per window.
        """
        import io
        import zipfile

        if doc_type == "A78" and not (in_domain and out_domain):
            raise ValueError("A78 (transmission) requires in_domain and out_domain")

        # initial 7-day windows, bisected on the 200-document cap
        windows: list[tuple[datetime, datetime]] = []
        cur = to_utc(start)
        end = to_utc(end)
        while cur < end:
            nxt = min(cur + timedelta(days=7), end)
            windows.append((cur, nxt))
            cur = nxt

        while windows:
            s, e = windows.pop(0)
            params = {"documentType": doc_type,
                      "periodStart": self._fmt(s), "periodEnd": self._fmt(e)}
            if doc_type == "A78":
                params["in_Domain"] = in_domain
                params["out_Domain"] = out_domain
            else:
                params["biddingZone_Domain"] = area_eic
            try:
                content = self._get_bytes(params)
            except self.ApiError as err:
                reason_l = err.reason.lower()
                if err.status == 400 and any(m in reason_l
                                             for m in ("no matching data",
                                                       "no data available")):
                    continue                       # empty window — fine
                if any(m in reason_l for m in self._TOO_MANY_MARKERS) \
                        and (e - s) > timedelta(days=1):
                    mid = s + (e - s) / 2          # too many docs: bisect
                    log.info("outages window %s→%s over 200-doc cap; splitting",
                             s.date(), e.date())
                    windows[:0] = [(s, mid), (mid, e)]
                    continue
                log.warning("outages %s %s→%s failed: %s",
                            doc_type, s.date(), e.date(), err)
                continue

            xmls: list[str] = []
            if content[:2] == b"PK":               # ZIP archive
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
                except Exception as exc:           # keep the window alive
                    log.warning("outage document parse failed: %s", exc)
            yield xmls, rows
