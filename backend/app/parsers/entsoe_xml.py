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


# ------------------------------------------------------------- outages

# ENTSO-E psrType codes -> human fuel labels
PSR_FUEL = {
    "B01": "biomass", "B02": "lignite", "B03": "coal gas", "B04": "fossil gas",
    "B05": "hard coal", "B06": "oil", "B09": "geothermal",
    "B10": "hydro pumped storage", "B11": "hydro run-of-river",
    "B12": "hydro reservoir", "B14": "nuclear", "B16": "solar",
    "B17": "waste", "B18": "wind offshore", "B19": "wind onshore", "B20": "other",
}


def parse_unavailability_document(xml: str) -> list[dict]:
    """Parse an Unavailability_MarketDocument (doc types A77/A78/A80).

    Returns dicts shaped for the outages table. Notes on semantics:
      * businessType A53 = planned maintenance, A54 = forced unavailability
      * Available_Period Point quantity = capacity still AVAILABLE during the
        event; unavailable_mw = nominal power − min(available). When nominalP
        is missing (some transmission docs), we fall back to the max quantity
        as a best-effort magnitude.
    """
    root = _strip_ns(etree.fromstring(xml.encode()))
    if root.tag == "Acknowledgement_MarketDocument":
        return []
    doc_id = root.findtext("mRID") or ""
    revision = root.findtext("revisionNumber") or "0"
    doc_type = root.findtext("type") or ""
    kind = "transmission" if doc_type == "A78" else "generation"
    created = root.findtext("createdDateTime") or ""

    # document-level outage window (fallback: first period interval)
    w_start = root.findtext("unavailability_Time_Period.timeInterval/start")
    w_end = root.findtext("unavailability_Time_Period.timeInterval/end")

    out = []
    for i, ts in enumerate(root.iter("TimeSeries")):
        business = ts.findtext("businessType") or ""
        area = (ts.findtext("biddingZone_Domain.mRID")
                or ts.findtext("in_Domain.mRID") or "")
        asset_name = (ts.findtext("production_RegisteredResource.pSRType."
                                  "powerSystemResources.name")
                      or ts.findtext("production_RegisteredResource.name")
                      or ts.findtext("registeredResource.name") or "unknown asset")
        asset_eic = (ts.findtext("production_RegisteredResource.mRID")
                     or ts.findtext("registeredResource.mRID") or "")
        psr = ts.findtext("production_RegisteredResource.pSRType.psrType") or ""
        nominal = ts.findtext("production_RegisteredResource.pSRType."
                              "powerSystemResources.nominalP")

        avail_vals, p_start, p_end = [], None, None
        for period in ts.iter("Available_Period"):
            s = period.findtext("timeInterval/start")
            e = period.findtext("timeInterval/end")
            p_start = p_start or s
            p_end = e or p_end
            for point in period.iter("Point"):
                q = point.findtext("quantity")
                if q is not None:
                    avail_vals.append(float(q))

        start_txt = w_start or p_start
        end_txt = w_end or p_end
        if not start_txt or not end_txt:
            continue
        if nominal is not None and avail_vals:
            unavailable = max(0.0, float(nominal) - min(avail_vals))
        elif avail_vals:
            unavailable = max(avail_vals)   # best effort when nominalP absent
        elif nominal is not None:
            unavailable = float(nominal)    # fully out
        else:
            unavailable = 0.0

        reason = (root.findtext("Reason/text") or ts.findtext("Reason/text")
                  or ("Planned maintenance" if business == "A53" else
                      "Forced outage" if business == "A54" else ""))
        out.append({
            "outage_id": f"{doc_id}-{i}" if doc_id else f"{asset_eic}-{start_txt}",
            "kind": kind,
            "planned": business == "A53",
            "area_eic": area,
            "asset_name": asset_name,
            "asset_eic": asset_eic,
            "fuel": PSR_FUEL.get(psr, ""),
            "start_utc": _parse_dt(start_txt),
            "end_utc": _parse_dt(end_txt),
            "unavailable_mw": round(unavailable, 1),
            "reason": reason,
            "revision": int(revision),
            "created": created,
        })
    return out
