"""Parsers for ENTSO-E XML market documents.

Handles the generic TimeSeries/Period/Point structure shared by
Publication_MarketDocument (prices, flows) and GL_MarketDocument (load).
Resolution-aware: PT15M/PT30M/PT60M; positions map onto the period start.
All output timestamps are UTC.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from lxml import etree

PARSER_VERSION = "1.1.0"

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



def _normalise_nominal_power_mw(
    nominal: float | None,
    available_mw: list[float],
) -> float | None:
    """Convert known A80 nominal-power values in kW before MW subtraction."""
    if nominal is None:
        return None

    if nominal >= 100_000:
        scaled_mw = nominal / 1_000.0
        if not available_mw or scaled_mw >= max(available_mw):
            return scaled_mw

    return nominal


def parse_unavailability_document(xml: str) -> list[dict]:
    """Parse an Unavailability_MarketDocument (doc types A77/A78/A80).

    Semantics handled here:
      * businessType A53 = planned maintenance, A54 = forced unavailability
      * GENERATION (A77/A80): Point quantity = capacity still AVAILABLE;
        unavailable_mw = nominalP - min(available).
      * TRANSMISSION (A78): documents carry no nominalP - the quantity is the
        remaining transfer capacity. We do NOT fake an outage size from it;
        unavailable_mw stays 0 and the reduction is described in `reason`
        (so interconnector notices don't pollute MW rankings with capacities).
      * Asset naming differs by type: production_RegisteredResource.* for
        generation, Asset_RegisteredResource.* for transmission.
      * outage_id is derived from asset + window, so the same physical outage
        reported across multiple documents/revisions collapses on upsert.
    """
    root = _strip_ns(etree.fromstring(xml.encode()))
    if root.tag == "Acknowledgement_MarketDocument":
        return []

    def t(el, tag: str) -> str:
        return (el.findtext(tag) or "").strip()

    doc_type = t(root, "type")
    kind = "transmission" if doc_type == "A78" else "generation"

    # document-level outage window (fallback: first period interval)
    w_start = t(root, "unavailability_Time_Period.timeInterval/start")
    w_end = t(root, "unavailability_Time_Period.timeInterval/end")

    out = []
    for ts in root.iter("TimeSeries"):
        business = t(ts, "businessType")
        in_dom = t(ts, "in_Domain.mRID")
        out_dom = t(ts, "out_Domain.mRID")
        area = t(ts, "biddingZone_Domain.mRID") or in_dom

        asset_name = (t(ts, "production_RegisteredResource.pSRType."
                            "powerSystemResources.name")
                      or t(ts, "production_RegisteredResource.name")
                      or t(ts, "Asset_RegisteredResource.name")
                      or t(ts, "registeredResource.name")
                      or t(ts, "production_RegisteredResource.location.name"))
        asset_eic = (t(ts, "production_RegisteredResource.mRID")
                     or t(ts, "Asset_RegisteredResource.mRID")
                     or t(ts, "registeredResource.mRID"))
        if not asset_name:
            asset_name = (f"Interconnector {out_dom} -> {in_dom}"
                          if kind == "transmission" and in_dom
                          else asset_eic or "unnamed asset")
        psr = (t(ts, "production_RegisteredResource.pSRType.psrType")
               or t(ts, "Asset_RegisteredResource.pSRType.psrType"))
        nominal_txt = t(ts, "production_RegisteredResource.pSRType."
                            "powerSystemResources.nominalP")
        nominal_raw = float(nominal_txt) if nominal_txt else None

        avail_vals, p_start, p_end = [], "", ""
        for period in ts.iter("Available_Period"):
            p_start = p_start or t(period, "timeInterval/start")
            p_end = t(period, "timeInterval/end") or p_end
            for point in period.iter("Point"):
                q = point.findtext("quantity")
                if q is not None:
                    avail_vals.append(float(q.strip()))

        nominal = _normalise_nominal_power_mw(nominal_raw, avail_vals)

        start_txt = w_start or p_start
        end_txt = w_end or p_end
        if not start_txt or not end_txt:
            continue

        reason = (t(root, "Reason/text") or t(ts, "Reason/text")
                  or ("Planned maintenance" if business == "A53" else
                      "Forced outage" if business == "A54" else ""))

        if kind == "generation" and nominal is not None and avail_vals:
            unavailable = max(0.0, nominal - min(avail_vals))
        elif kind == "generation" and nominal is not None:
            unavailable = nominal            # no periods listed -> fully out
        elif kind == "generation" and avail_vals:
            unavailable = max(avail_vals)    # best effort, nominal absent
        else:
            # transmission (or nothing usable): don't invent an outage size
            unavailable = 0.0
            if avail_vals:
                reason = (f"Transfer capacity reduced to "
                          f"{min(avail_vals):.0f} MW. {reason}").strip()

        out.append({
            "outage_id": f"{doc_type}-{asset_eic or asset_name[:40]}-{start_txt}",
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
        })
    return out
