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
    EicSeed("10YES-REE------0", "area", "Spain (ES)", "spain,es,ree", "ES"),
    EicSeed("10Y1001A1001A82H", "area", "Germany-Luxembourg (DE-LU)",
            "germany,de,de-lu,deutschland,germany-luxembourg", "DE"),
    EicSeed("10Y1001A1001A83F", "area", "Germany (DE control area)", "germany control,de ca", "DE"),
    EicSeed("10YNL----------L", "area", "Netherlands (NL)", "netherlands,nl,holland,tennet", "NL"),
    EicSeed("10YBE----------2", "area", "Belgium (BE)", "belgium,be,elia", "BE"),
    EicSeed("10YPT-REN------W", "area", "Portugal (PT)", "portugal,pt,ren", "PT"),
    EicSeed("10YDK-1--------W", "area", "Denmark 1 (DK1)", "denmark,dk1", "DK"),
    EicSeed("10Y1001A1001A016", "area", "Ireland (IE/SEM)", "ireland,ie,sem,eirgrid", "IE"),
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

def _dedupe_seed(seeds: list[EicSeed]) -> list[EicSeed]:
    """Keep the first entry per EIC code.

    A duplicate here used to blow up seed_demo.py with a UNIQUE constraint
    error (and only in CI, where the DB starts empty). Deduping at import
    time makes the registry safe to extend.
    """
    seen: set[str] = set()
    out: list[EicSeed] = []
    for e in seeds:
        if e.eic in seen:
            continue
        seen.add(e.eic)
        out.append(e)
    return out


EIC_SEED = _dedupe_seed(EIC_SEED)


# ---------------------------------------------------------------- EU markets
# Every market below is served by the SAME ENTSO-E token — no new credentials.
# `zones` = bidding zones to pull prices for. `national` = load/forecast area.
EU_MARKETS: dict[str, dict] = {
    "IT": {
        "name": "Italy",
        "national": "10YIT-GRTN-----B",
        "zones": ["10Y1001A1001A73I", "10Y1001A1001A70O", "10Y1001A1001A71M",
                  "10Y1001A1001A788", "10Y1001A1001A74G", "10Y1001A1001A75E"],
        "tz": "Europe/Rome", "currency": "EUR",
    },
    "FR": {
        "name": "France",
        "national": "10YFR-RTE------C",
        "zones": ["10YFR-RTE------C"],          # single national bidding zone
        "tz": "Europe/Paris", "currency": "EUR",
    },
    "DE": {
        "name": "Germany-Luxembourg",
        "national": "10Y1001A1001A83F",          # DE (control area, for load)
        "zones": ["10Y1001A1001A82H"],           # DE-LU bidding zone (prices)
        "tz": "Europe/Berlin", "currency": "EUR",
    },
    "CH": {
        "name": "Switzerland",
        "national": "10YCH-SWISSGRIDZ",
        "zones": ["10YCH-SWISSGRIDZ"],
        "tz": "Europe/Zurich", "currency": "EUR",
    },
    "GB": {
        "name": "Great Britain",
        "national": "10YGB----------A",
        "zones": ["10YGB----------A"],
        "tz": "Europe/London", "currency": "GBP",
    },
    "ES": {
        "name": "Spain",
        "national": "10YES-REE------0",
        "zones": ["10YES-REE------0"],
        "tz": "Europe/Madrid", "currency": "EUR",
    },
    "BE": {
        "name": "Belgium",
        "national": "10YBE----------2",
        "zones": ["10YBE----------2"],
        "tz": "Europe/Brussels", "currency": "EUR",
    },
    "AT": {
        "name": "Austria",
        "national": "10YAT-APG------L",
        "zones": ["10YAT-APG------L"],
        "tz": "Europe/Vienna", "currency": "EUR",
    },
    "SI": {
        "name": "Slovenia",
        "national": "10YSI-ELES-----O",
        "zones": ["10YSI-ELES-----O"],
        "tz": "Europe/Ljubljana", "currency": "EUR",
    },
    "GR": {
        "name": "Greece",
        "national": "10YGR-HTSO-----Y",
        "zones": ["10YGR-HTSO-----Y"],
        "tz": "Europe/Athens", "currency": "EUR",
    },
    "NL": {
        "name": "Netherlands",
        "national": "10YNL----------L",
        "zones": ["10YNL----------L"],
        "tz": "Europe/Amsterdam", "currency": "EUR",
    },
}

# Cross-border pairs worth tracking per market (from_eic, to_eic, label)
EU_BORDERS: dict[str, list[tuple[str, str, str]]] = {
    "IT": IT_BORDERS,
    "FR": [
        ("10YDE-VE-------2", "10YFR-RTE------C", "DE → FR"),
        ("10YES-REE------0", "10YFR-RTE------C", "ES → FR"),
        ("10YCH-SWISSGRIDZ", "10YFR-RTE------C", "CH → FR"),
        ("10YBE----------2", "10YFR-RTE------C", "BE → FR"),
        ("10YGB----------A", "10YFR-RTE------C", "GB → FR"),
    ],
    "DE": [
        ("10YFR-RTE------C", "10Y1001A1001A82H", "FR → DE"),
        ("10YNL----------L", "10Y1001A1001A82H", "NL → DE"),
        ("10YAT-APG------L", "10Y1001A1001A82H", "AT → DE"),
        ("10YCH-SWISSGRIDZ", "10Y1001A1001A82H", "CH → DE"),
        ("10YDK-1--------W", "10Y1001A1001A82H", "DK1 → DE"),
    ],
    "CH": [
        ("10YFR-RTE------C", "10YCH-SWISSGRIDZ", "FR → CH"),
        ("10Y1001A1001A82H", "10YCH-SWISSGRIDZ", "DE → CH"),
        ("10YAT-APG------L", "10YCH-SWISSGRIDZ", "AT → CH"),
        ("10Y1001A1001A73I", "10YCH-SWISSGRIDZ", "IT-North → CH"),
    ],
    "GB": [
        ("10YFR-RTE------C", "10YGB----------A", "FR → GB"),
        ("10YNL----------L", "10YGB----------A", "NL → GB"),
        ("10YBE----------2", "10YGB----------A", "BE → GB"),
        ("10Y1001A1001A016", "10YGB----------A", "IE → GB"),
    ],
    "ES": [("10YFR-RTE------C", "10YES-REE------0", "FR → ES"),
           ("10YPT-REN------W", "10YES-REE------0", "PT → ES")],
    "AT": [("10Y1001A1001A82H", "10YAT-APG------L", "DE → AT"),
           ("10YCH-SWISSGRIDZ", "10YAT-APG------L", "CH → AT")],
    "SI": [("10YAT-APG------L", "10YSI-ELES-----O", "AT → SI"),
           ("10Y1001A1001A73I", "10YSI-ELES-----O", "IT-North → SI")],
    "GR": [("10Y1001A1001A788", "10YGR-HTSO-----Y", "IT-South → GR")],
    "NL": [("10Y1001A1001A82H", "10YNL----------L", "DE → NL"),
           ("10YBE----------2", "10YNL----------L", "BE → NL")],
    "BE": [("10YFR-RTE------C", "10YBE----------2", "FR → BE"),
           ("10YNL----------L", "10YBE----------2", "NL → BE"),
           ("10YGB----------A", "10YBE----------2", "GB → BE")],
}


def market_zones(country: str) -> list[str]:
    return EU_MARKETS.get(country.upper(), {}).get("zones", [])


def market_national(country: str) -> str:
    return EU_MARKETS.get(country.upper(), {}).get("national", "")


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
    "10Y1001A1001A82H": "DE-LU", "10Y1001A1001A83F": "DE",
    "10YES-REE------0": "ES", "10YNL----------L": "NL",
    "10YBE----------2": "BE", "10YPT-REN------W": "PT",
    "10YDK-1--------W": "DK1", "10Y1001A1001A016": "IE",
}

# Approximate zone centroids for population-weighted weather features
ZONE_CENTROIDS: dict[str, tuple[float, float]] = {
    "10YIT-GRTN-----B": (42.5, 12.5),
    "10YGB----------A": (52.5, -1.5),
    "10YFR-RTE------C": (46.6, 2.4),    # France
    "10Y1001A1001A82H": (51.2, 10.4),   # DE-LU
    "10Y1001A1001A83F": (51.2, 10.4),
    "10YCH-SWISSGRIDZ": (46.8, 8.2),    # Switzerland
    "10YES-REE------0": (40.3, -3.7),   # Spain
    "10YBE----------2": (50.8, 4.5),    # Belgium
    "10YAT-APG------L": (47.6, 14.1),   # Austria
    "10YSI-ELES-----O": (46.2, 15.0),   # Slovenia
    "10YGR-HTSO-----Y": (39.1, 22.0),   # Greece
    "10YNL----------L": (52.2, 5.3),    # Netherlands
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
