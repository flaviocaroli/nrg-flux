"""Parsers for ENTSO-E XML market documents.

Handles the generic TimeSeries/Period/Point structure shared by
Publication_MarketDocument (prices, flows) and GL_MarketDocument (load).
Resolution-aware: PT15M/PT30M/PT60M; positions map onto the period start.
All output timestamps are UTC.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from lxml import etree

PARSER_VERSION = "1.0.0"

_RES = {"PT15M": 15, "PT30M": 30, "PT60M": 60, "P1D": 1440}


def _strip_ns(root):
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


def _parse_dt(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)


def _iter_points(xml: str, value_tag: str):
    root = _strip_ns(etree.fromstring(xml.encode()))
    if root.tag == "Acknowledgement_MarketDocument":   # "no data" response
        return
    for ts in root.iter("TimeSeries"):
        for period in ts.iter("Period"):
            start = _parse_dt(period.findtext("timeInterval/start"))
            res_txt = period.findtext("resolution") or "PT60M"
            step = timedelta(minutes=_RES.get(res_txt, 60))
            last_pos, last_val = 0, None
            for point in period.iter("Point"):
                pos = int(point.findtext("position"))
                raw = point.findtext(value_tag)
                val = float(raw) if raw is not None else None
                # ENTSO-E omits repeated points (curveType A03): forward-fill
                if last_val is not None:
                    for p in range(last_pos + 1, pos):
                        yield start + step * (p - 1), last_val, res_txt
                if val is not None:
                    yield start + step * (pos - 1), val, res_txt
                last_pos, last_val = pos, val


def parse_price_document(xml: str) -> list[dict]:
    return [{"ts_utc": ts, "price_eur_mwh": v, "resolution": res}
            for ts, v, res in _iter_points(xml, "price.amount")]


def parse_load_document(xml: str) -> list[dict]:
    return [{"ts_utc": ts, "mw": v, "resolution": res}
            for ts, v, res in _iter_points(xml, "quantity")]


def parse_flow_document(xml: str) -> list[dict]:
    return [{"ts_utc": ts, "mw": v, "resolution": res}
            for ts, v, res in _iter_points(xml, "quantity")]
