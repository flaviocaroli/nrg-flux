"""EIC Resolver (section 8: must-have module).

Ships with a curated seed of the areas needed for the Italy beachhead plus the
main EU zones. In production, sync the full ENTSO-E approved-codes list monthly
(see app/ingestion/entsoe.py::sync_eic_codes).
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class EicSeed:
    eic: str
    code_type: str
    name: str
    aliases: str
    country: str


EIC_SEED: list[EicSeed] = [
    # --- Italy: country + bidding zones ---
    EicSeed("10YIT-GRTN-----B", "area", "Italy (IT)", "italy,it,italia,terna", "IT"),
    EicSeed("10YGB----------A", "area", "Great Britain (GB)",
            "gb,uk,britain,great britain,national grid,neso,england", "GB"),
    EicSeed("10Y1001A1001A73I", "area", "IT-North (NORD)", "north,nord,italy north,it-north", "IT"),
    EicSeed("10Y1001A1001A70O", "area", "IT-Centre-North (CNOR)", "cnor,centre north,centro nord", "IT"),
    EicSeed("10Y1001A1001A71M", "area", "IT-Centre-South (CSUD)", "csud,centre south,centro sud", "IT"),
    EicSeed("10Y1001A1001A788", "area", "IT-South (SUD)", "sud,south,italy south", "IT"),
    EicSeed("10Y1001A1001A74G", "area", "IT-Sardinia (SARD)", "sard,sardinia,sardegna", "IT"),
    EicSeed("10Y1001A1001A75E", "area", "IT-Sicily (SICI)", "sici,sicily,sicilia", "IT"),
    EicSeed("10Y1001C--00096J", "area", "IT-Calabria (CALA)", "cala,calabria", "IT"),
    # --- neighbours (for borders/flows) ---
    EicSeed("10YFR-RTE------C", "area", "France (FR)", "france,fr,rte", "FR"),
    EicSeed("10YCH-SWISSGRIDZ", "area", "Switzerland (CH)", "switzerland,ch,swissgrid", "CH"),
    EicSeed("10YAT-APG------L", "area", "Austria (AT)", "austria,at,apg", "AT"),
    EicSeed("10YSI-ELES-----O", "area", "Slovenia (SI)", "slovenia,si,eles", "SI"),
    EicSeed("10YGR-HTSO-----Y", "area", "Greece (GR)", "greece,gr,ipto", "GR"),
    EicSeed("10Y1001A1001A885", "area", "Montenegro (ME)", "montenegro,me,cges", "ME"),
    # --- major EU zones for expansion (section 13, weeks 11-12) ---
    EicSeed("10Y1001A1001A82H", "area", "Germany-Luxembourg (DE-LU)", "germany,de,de-lu", "DE"),
    EicSeed("10YES-REE------0", "area", "Spain (ES)", "spain,es,ree", "ES"),
]

# Italian borders used for physical-flow queries
IT_BORDERS: list[tuple[str, str, str]] = [
    ("10YFR-RTE------C", "10Y1001A1001A73I", "FR → IT-North"),
    ("10YCH-SWISSGRIDZ", "10Y1001A1001A73I", "CH → IT-North"),
    ("10YAT-APG------L", "10Y1001A1001A73I", "AT → IT-North"),
    ("10YSI-ELES-----O", "10Y1001A1001A73I", "SI → IT-North"),
    ("10YGR-HTSO-----Y", "10Y1001A1001A788", "GR → IT-South"),
    ("10Y1001A1001A885", "10Y1001A1001A71M", "ME → IT-Centre-South"),
]

ITALY_ZONES = ["10Y1001A1001A73I", "10Y1001A1001A70O", "10Y1001A1001A71M",
               "10Y1001A1001A788", "10Y1001A1001A74G", "10Y1001A1001A75E"]

# Short zone labels, independent of the DB (used by the dashboard as fallback)
ZONE_SHORT: dict[str, str] = {
    "10Y1001A1001A73I": "NORD", "10Y1001A1001A70O": "CNOR",
    "10Y1001A1001A71M": "CSUD", "10Y1001A1001A788": "SUD",
    "10Y1001A1001A74G": "SARD", "10Y1001A1001A75E": "SICI",
    "10YIT-GRTN-----B": "IT", "10YGB----------A": "GB",
    "10YFR-RTE------C": "FR", "10YCH-SWISSGRIDZ": "CH",
    "10YAT-APG------L": "AT", "10YSI-ELES-----O": "SI",
    "10YGR-HTSO-----Y": "GR", "10Y1001A1001A885": "ME",
}

# Approximate zone centroids for population-weighted weather features
ZONE_CENTROIDS: dict[str, tuple[float, float]] = {
    "10YIT-GRTN-----B": (42.5, 12.5),
    "10YGB----------A": (52.5, -1.5),
    "10Y1001A1001A73I": (45.4, 9.9),   # NORD  (Po valley, Milan-centric weighting)
    "10Y1001A1001A70O": (43.5, 11.0),  # CNOR  (Florence)
    "10Y1001A1001A71M": (41.9, 12.9),  # CSUD  (Rome)
    "10Y1001A1001A788": (40.9, 16.0),  # SUD   (Bari/Naples axis)
    "10Y1001A1001A74G": (39.9, 9.0),   # SARD  (Cagliari)
    "10Y1001A1001A75E": (37.8, 14.2),  # SICI  (Palermo/Catania axis)
}


def score_match(query: str, name: str, aliases: str, eic: str) -> int:
    q = query.strip().lower()
    if not q:
        return 0
    if q == eic.lower():
        return 100
    if q == name.lower():
        return 95
    hits = 0
    for token in q.split():
        if token in name.lower():
            hits += 30
        if any(token in a for a in aliases.split(",")):
            hits += 25
        if token in eic.lower():
            hits += 10
    return min(hits, 90)
