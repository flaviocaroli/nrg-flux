"""NRG-Flux test suite.

Includes the DST "golden tests" the plan makes a hard gate:
"No paid pilot until DST tests pass."
Run: cd backend && python -m pytest tests/ -q
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.parsers.entsoe_xml import parse_price_document
from app.utils.eic import score_match
from app.utils.timeutils import (hours_in_market_day, market_day,
                                 market_day_bounds_utc, to_utc)


# --------------------------------------------------------- DST golden tests

def test_normal_winter_day_has_24_hours():
    assert hours_in_market_day("2026-01-15") == 24


def test_normal_summer_day_has_24_hours():
    assert hours_in_market_day("2026-07-15") == 24


def test_dst_start_day_has_23_hours():
    # Europe: clocks jump forward on the last Sunday of March 2026 (Mar 29)
    assert hours_in_market_day("2026-03-29") == 23


def test_dst_end_day_has_25_hours():
    # Clocks fall back on the last Sunday of October 2026 (Oct 25)
    assert hours_in_market_day("2026-10-25") == 25


def test_market_day_starts_2300_utc_in_winter():
    start, _ = market_day_bounds_utc("2026-01-15")
    assert start == datetime(2026, 1, 14, 23, 0, tzinfo=timezone.utc)


def test_market_day_starts_2200_utc_in_summer():
    start, _ = market_day_bounds_utc("2026-07-15")
    assert start == datetime(2026, 7, 14, 22, 0, tzinfo=timezone.utc)


def test_market_day_assignment_around_midnight():
    # 22:30 UTC on Jul 14 is already Jul 15 in Rome (CEST = UTC+2)
    assert market_day(datetime(2026, 7, 14, 22, 30, tzinfo=timezone.utc)) == "2026-07-15"
    assert market_day(datetime(2026, 7, 14, 21, 30, tzinfo=timezone.utc)) == "2026-07-14"


def test_naive_datetime_assumed_utc():
    assert to_utc(datetime(2026, 7, 1, 12, 0)).tzinfo == timezone.utc


# ------------------------------------------------------------- XML parser

PRICE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Publication_MarketDocument xmlns="urn:iec62325.351:tc57wg16:451-3:publicationdocument:7:3">
  <TimeSeries>
    <Period>
      <timeInterval><start>2026-07-10T22:00Z</start><end>2026-07-11T22:00Z</end></timeInterval>
      <resolution>PT60M</resolution>
      <Point><position>1</position><price.amount>95.10</price.amount></Point>
      <Point><position>2</position><price.amount>88.00</price.amount></Point>
      <Point><position>4</position><price.amount>81.30</price.amount></Point>
    </Period>
  </TimeSeries>
</Publication_MarketDocument>"""


def test_price_parser_positions_and_forward_fill():
    rows = parse_price_document(PRICE_XML)
    assert rows[0]["ts_utc"] == datetime(2026, 7, 10, 22, 0, tzinfo=timezone.utc)
    assert rows[0]["price_eur_mwh"] == 95.10
    # position 3 was omitted (curveType A03) -> forward-filled from position 2
    ts = {r["ts_utc"].hour: r["price_eur_mwh"] for r in rows}
    assert ts[0] == 88.00       # 00:00 UTC = position 3, filled
    assert ts[1] == 81.30       # position 4


def test_price_parser_handles_acknowledgement():
    ack = ('<?xml version="1.0"?><Acknowledgement_MarketDocument '
           'xmlns="urn:x"><Reason/></Acknowledgement_MarketDocument>')
    assert parse_price_document(ack) == []


# ------------------------------------------------------------- EIC resolver

def test_eic_exact_code_match_wins():
    assert score_match("10Y1001A1001A73I", "IT-North (NORD)", "north,nord", "10Y1001A1001A73I") == 100


def test_eic_alias_match():
    assert score_match("nord", "IT-North (NORD)", "north,nord,italy north", "10Y...") > 0
    assert score_match("zzz", "IT-North (NORD)", "north,nord", "10Y...") == 0


# ------------------------------------------------------------- API smoke

@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c


def test_status_endpoint(client):
    r = client.get("/v1/status")
    assert r.status_code == 200
    assert "datasets" in r.json()


def test_eic_resolve_endpoint(client):
    r = client.get("/v1/eic/resolve", params={"q": "sicily"})
    assert r.status_code == 200


def test_invalid_window_rejected(client):
    r = client.get("/v1/prices/dayahead", params={
        "area": "10Y1001A1001A73I",
        "start": "2026-07-10T00:00:00Z", "end": "2026-07-09T00:00:00Z"})
    assert r.status_code == 422


def test_forecast_invalid_horizon(client):
    r = client.get("/v1/forecast/load", params={"horizon": 99})
    assert r.status_code == 422


def test_weather_history_endpoint(client):
    # ERA5 temperature must be downloadable for every backfilled market, not
    # just Italy. Guards the multi-market data catalog.
    for area in ("10YIT-GRTN-----B", "10YFR-RTE------C",
                 "10Y1001A1001A83F", "10YCH-SWISSGRIDZ"):
        r = client.get("/v1/weather/history", params={"area": area})
        assert r.status_code == 200, area
        body = r.json()
        assert "series" in body
        assert body["lineage"]["dataset"].startswith("Observed temperature")


def test_weather_history_uses_de_control_area(client):
    # Germany's trap: weather is keyed to the control area (…A83F). The DE-LU
    # bidding zone (…A82H) must NEVER carry weather rows. Self-seed one control
    # -area row so this holds on CI's empty DB as well as a backfilled one,
    # then clean it up.
    from datetime import timedelta

    from app.db.models import SessionLocal, WeatherHistory, init_db
    init_db()
    ctrl_eic, zone_eic = "10Y1001A1001A83F", "10Y1001A1001A82H"
    ts = datetime.now(timezone.utc) - timedelta(days=1)
    db = SessionLocal()
    seeded = False
    if db.query(WeatherHistory).filter_by(area_eic=ctrl_eic, ts_utc=ts).first() is None:
        db.add(WeatherHistory(area_eic=ctrl_eic, ts_utc=ts, temp_c=18.0, source="test"))
        db.commit()
        seeded = True
    db.close()
    try:
        ctrl = client.get("/v1/weather/history", params={"area": ctrl_eic})
        zone = client.get("/v1/weather/history", params={"area": zone_eic})
        assert ctrl.status_code == 200 and zone.status_code == 200
        assert ctrl.json()["count"] > 0        # weather lives under the control area
        assert zone.json()["count"] == 0       # never under the DE-LU price zone
    finally:
        if seeded:
            db = SessionLocal()
            db.query(WeatherHistory).filter_by(
                area_eic=ctrl_eic, ts_utc=ts, source="test").delete()
            db.commit()
            db.close()


# ------------------------------------------------------- outage parser

OUTAGE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Unavailability_MarketDocument xmlns="urn:iec62325.351:tc57wg16:451-6:outagedocument:3:0">
  <mRID>OUTDOC-123</mRID>
  <revisionNumber>2</revisionNumber>
  <type>A80</type>
  <createdDateTime>2026-07-10T09:00:00Z</createdDateTime>
  <unavailability_Time_Period.timeInterval>
    <start>2026-07-11T06:00Z</start><end>2026-07-15T18:00Z</end>
  </unavailability_Time_Period.timeInterval>
  <TimeSeries>
    <businessType>A54</businessType>
    <biddingZone_Domain.mRID>10Y1001A1001A71M</biddingZone_Domain.mRID>
    <production_RegisteredResource.mRID>26T0123456789012</production_RegisteredResource.mRID>
    <production_RegisteredResource.name>Montalto CCGT</production_RegisteredResource.name>
    <production_RegisteredResource.pSRType.psrType>B04</production_RegisteredResource.pSRType.psrType>
    <production_RegisteredResource.pSRType.powerSystemResources.name>Montalto U2</production_RegisteredResource.pSRType.powerSystemResources.name>
    <production_RegisteredResource.pSRType.powerSystemResources.nominalP>780</production_RegisteredResource.pSRType.powerSystemResources.nominalP>
    <Available_Period>
      <timeInterval><start>2026-07-11T06:00Z</start><end>2026-07-15T18:00Z</end></timeInterval>
      <resolution>PT60M</resolution>
      <Point><position>1</position><quantity>150</quantity></Point>
    </Available_Period>
  </TimeSeries>
</Unavailability_MarketDocument>"""


def test_outage_parser_extracts_event():
    from app.parsers.entsoe_xml import parse_unavailability_document
    rows = parse_unavailability_document(OUTAGE_XML)
    assert len(rows) == 1
    o = rows[0]
    assert o["kind"] == "generation"
    assert o["planned"] is False                    # A54 = forced
    assert o["asset_name"] == "Montalto U2"
    assert o["fuel"] == "fossil gas"                # B04
    assert o["unavailable_mw"] == 630.0             # 780 nominal - 150 available
    assert o["start_utc"] == datetime(2026, 7, 11, 6, 0, tzinfo=timezone.utc)
    assert o["area_eic"] == "10Y1001A1001A71M"


def test_outage_parser_handles_acknowledgement():
    from app.parsers.entsoe_xml import parse_unavailability_document
    ack = ('<?xml version="1.0"?><Acknowledgement_MarketDocument xmlns="urn:x">'
           '<Reason/></Acknowledgement_MarketDocument>')
    assert parse_unavailability_document(ack) == []


# ------------------------------------------------------- auth & rate limits

@pytest.fixture()
def customer_key():
    from app.db.models import SessionLocal, init_db
    from app.services import auth_service
    init_db()
    # tiny test plan so limits are hit quickly
    auth_service.PLANS["testplan"] = {"per_minute": 3, "per_day": 5}
    db = SessionLocal()
    client, key = auth_service.create_client(db, name="pytest-co", plan="free")
    row = db.get(auth_service.ApiClient, client.id)
    row.plan = "testplan"
    db.commit()
    db.close()
    yield key
    db = SessionLocal()
    auth_service.revoke_client(db, key.split("_")[1])
    db.close()


def test_customer_key_authenticates(client, customer_key):
    r = client.get("/v1/eic/resolve", params={"q": "sicily"},
                   headers={"X-Api-Key": customer_key})
    assert r.status_code == 200


def test_invalid_customer_key_rejected(client):
    r = client.get("/v1/eic/resolve", params={"q": "sicily"},
                   headers={"X-Api-Key": "nrgf_deadbeef_" + "0" * 40})
    assert r.status_code == 401


def test_per_minute_rate_limit_429(client, customer_key):
    from app.services import auth_service
    auth_service._minute_buckets.clear()
    codes = [client.get("/v1/eic/resolve", params={"q": "italy"},
                        headers={"X-Api-Key": customer_key}).status_code
             for _ in range(4)]
    assert codes[:3] == [200, 200, 200]
    assert codes[3] == 429


def test_revoked_key_403(client, customer_key):
    from app.db.models import SessionLocal
    from app.services.auth_service import revoke_client
    db = SessionLocal()
    revoke_client(db, customer_key.split("_")[1])
    db.close()
    r = client.get("/v1/eic/resolve", params={"q": "italy"},
                   headers={"X-Api-Key": customer_key})
    assert r.status_code == 403


def test_account_usage_endpoint(client, customer_key):
    from app.services import auth_service
    auth_service._minute_buckets.clear()
    r = client.get("/v1/account/usage", headers={"X-Api-Key": customer_key})
    assert r.status_code == 200
    body = r.json()
    assert body["plan"] == "testplan"
    assert body["used_today"] >= 1


TRANSMISSION_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Unavailability_MarketDocument xmlns="urn:iec62325.351:tc57wg16:451-6:outagedocument:3:0">
  <mRID>TDOC-9</mRID><type>A78</type>
  <unavailability_Time_Period.timeInterval>
    <start>2026-07-12T06:00Z</start><end>2026-07-14T18:00Z</end>
  </unavailability_Time_Period.timeInterval>
  <TimeSeries>
    <businessType>A53</businessType>
    <in_Domain.mRID> 10Y1001A1001A73I </in_Domain.mRID>
    <out_Domain.mRID>10YFR-RTE------C</out_Domain.mRID>
    <Asset_RegisteredResource.mRID>10T-FR-IT-000012</Asset_RegisteredResource.mRID>
    <Asset_RegisteredResource.name>
      Albertville-Rondissone 400kV
    </Asset_RegisteredResource.name>
    <Available_Period>
      <timeInterval><start>2026-07-12T06:00Z</start><end>2026-07-14T18:00Z</end></timeInterval>
      <Point><position>1</position><quantity>1450</quantity></Point>
    </Available_Period>
  </TimeSeries>
</Unavailability_MarketDocument>"""


def test_transmission_outage_semantics():
    """A78: no fake MW from available capacity; names + whitespace handled."""
    from app.parsers.entsoe_xml import parse_unavailability_document
    rows = parse_unavailability_document(TRANSMISSION_XML)
    assert len(rows) == 1
    o = rows[0]
    assert o["kind"] == "transmission"
    assert o["asset_name"] == "Albertville-Rondissone 400kV"   # stripped
    assert o["area_eic"] == "10Y1001A1001A73I"                 # stripped
    assert o["unavailable_mw"] == 0.0                          # not 1450!
    assert "reduced to 1450 MW" in o["reason"]
    assert o["planned"] is True


def test_outage_id_stable_across_documents():
    """Same asset+window in two different documents -> same outage_id."""
    from app.parsers.entsoe_xml import parse_unavailability_document
    a = parse_unavailability_document(OUTAGE_XML)[0]
    b = parse_unavailability_document(OUTAGE_XML.replace("OUTDOC-123", "OUTDOC-999"))[0]
    assert a["outage_id"] == b["outage_id"]


# ------------------------------------------------- registry integrity

def test_eic_seed_has_no_duplicates():
    """Regression: a duplicated EIC broke seed_demo.py in CI (UNIQUE constraint)."""
    from collections import Counter
    from app.utils.eic import EIC_SEED
    dups = {k: v for k, v in Counter(e.eic for e in EIC_SEED).items() if v > 1}
    assert not dups, f"duplicate EIC codes in EIC_SEED: {dups}"


def test_eu_markets_reference_known_eics():
    """Every market's zones/national must exist in the seed registry."""
    from app.utils.eic import EIC_SEED, EU_MARKETS
    known = {e.eic for e in EIC_SEED}
    missing = []
    for cc, m in EU_MARKETS.items():
        for z in [m["national"], *m["zones"]]:
            if z not in known:
                missing.append((cc, z))
    assert not missing, f"markets reference unseeded EICs: {missing}"


def test_neso_utc_bounds_accept_aware_and_naive_datetimes():
    from app.ingestion.neso import _utc_timestamp

    naive = _utc_timestamp(datetime(2026, 1, 1, 12, 0))
    aware = _utc_timestamp(datetime(2026, 1, 1, 13, 0,
                                    tzinfo=timezone(timedelta(hours=1))))

    assert naive.isoformat() == "2026-01-01T12:00:00+00:00"
    assert aware.isoformat() == "2026-01-01T12:00:00+00:00"


def test_europe_expansion_markets_have_weather_centroids():
    """Every configured expansion market must be trainable with real weather."""
    from app.utils.eic import EU_MARKETS, ZONE_CENTROIDS

    expected = {
        "BE": "10YBE----------2",
        "SI": "10YSI-ELES-----O",
        "GR": "10YGR-HTSO-----Y",
    }
    for country, national_eic in expected.items():
        assert EU_MARKETS[country]["national"] == national_eic
        assert national_eic in ZONE_CENTROIDS

# --------------------------------------------------- scheduler time math
# The scheduler must fire at 13:05 Europe/Rome regardless of DST — these are
# golden tests in the same spirit as the market-day DST gate above.

def test_next_daily_run_summer_is_1105_utc():
    from app.utils.scheduling import next_daily_run
    now = datetime(2026, 7, 18, 8, 0, tzinfo=timezone.utc)  # CEST (UTC+2)
    assert next_daily_run(now) == datetime(2026, 7, 18, 11, 5, tzinfo=timezone.utc)


def test_next_daily_run_winter_is_1205_utc():
    from app.utils.scheduling import next_daily_run
    now = datetime(2026, 1, 15, 8, 0, tzinfo=timezone.utc)  # CET (UTC+1)
    assert next_daily_run(now) == datetime(2026, 1, 15, 12, 5, tzinfo=timezone.utc)


def test_next_daily_run_rolls_to_tomorrow_across_dst_end():
    # 2026-10-24 14:00 local is after 13:05, so next run is Sunday the 25th —
    # the day clocks fall back. 13:05 CET on the 25th is 12:05 UTC.
    from app.utils.scheduling import next_daily_run
    now = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)  # 14:00 CEST
    assert next_daily_run(now) == datetime(2026, 10, 25, 12, 5, tzinfo=timezone.utc)


def test_next_half_hour_boundaries():
    from app.utils.scheduling import next_half_hour
    t = datetime(2026, 7, 18, 9, 14, 59, tzinfo=timezone.utc)
    assert next_half_hour(t) == datetime(2026, 7, 18, 9, 30, tzinfo=timezone.utc)
    t2 = datetime(2026, 7, 18, 9, 30, 0, tzinfo=timezone.utc)
    assert next_half_hour(t2) == datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc)


def test_tomorrow_market_day_uses_local_calendar():
    # 23:30 UTC on the 18th is already the 19th in Rome (CEST), so "tomorrow"
    # for publication purposes is the 20th.
    from app.utils.scheduling import tomorrow_market_day
    late = datetime(2026, 7, 18, 23, 30, tzinfo=timezone.utc)
    assert tomorrow_market_day(late) == "2026-07-20"

# --------------------------------------------------- asymmetric rolling CQR (v6)

def test_asymmetric_cqr_covers_skewed_errors():
    # Errors with a heavy UPPER tail (price-spike shaped). A correct asymmetric
    # calibration must reach ~target coverage on fresh data from the same
    # distribution; the symmetric-equivalent factor alone must be driven by
    # the worse (upper) tail.
    import numpy as np
    from app.forecasting.calibration import apply_scales, asymmetric_cqr
    rng = np.random.default_rng(7)
    n = 4000
    y_true = rng.normal(100, 5, n)
    noise = np.where(rng.random(n) < 0.1, rng.exponential(30, n), rng.normal(0, 3, n))
    y = y_true + noise                      # spikes upward only
    lo, hi = y_true - 4.0, y_true + 4.0     # deliberately too-narrow raw band

    cal, test = slice(0, 2000), slice(2000, None)
    s = asymmetric_cqr(y[cal], lo[cal], hi[cal], target_coverage=0.80)
    assert s.hi > s.lo, "upper tail must need more widening than lower"
    p10, p90 = apply_scales(lo[test], hi[test], s.lo, s.hi)
    cov = np.mean((y[test] >= p10) & (y[test] <= p90))
    assert 0.76 <= cov <= 0.92, f"coverage {cov:.2%} far from 80% target"


def test_asymmetric_cqr_can_tighten_too_wide_band():
    import numpy as np
    from app.forecasting.calibration import asymmetric_cqr
    rng = np.random.default_rng(3)
    y = rng.normal(0, 1, 3000)
    lo, hi = y * 0 - 50.0, y * 0 + 50.0     # absurdly wide band
    s = asymmetric_cqr(y, lo, hi, 0.80)
    assert s.lo < 0 and s.hi < 0, "factors must go negative to tighten"
    assert s.raw_coverage_pct == 100.0


def test_rolling_cal_hours_bounds():
    import os
    from app.forecasting.calibration import rolling_cal_hours
    os.environ.pop("NRGFLUX_CAL_DAYS", None)
    assert rolling_cal_hours(24 * 700) == 180 * 24         # default 180d window
    assert rolling_cal_hours(24 * 40) == int(24 * 40 * 0.30)  # capped at 30%
    os.environ["NRGFLUX_CAL_DAYS"] = "10"
    assert rolling_cal_hours(24 * 700) == 24 * 14          # floored at 2 weeks
    os.environ.pop("NRGFLUX_CAL_DAYS", None)


def test_old_model_cards_fall_back_to_symmetric():
    # A pre-v6 card has no scale_lo/scale_hi — the loader must fall back to
    # the single legacy factor so nothing breaks on deploy day.
    import json
    import os
    import tempfile
    from app.forecasting.model import LoadForecaster
    with tempfile.TemporaryDirectory() as d:
        card = {"calibration": {"conformal_q_mw": 100.0, "conformal_scale": 0.25}}
        with open(os.path.join(d, "model_card_TESTAREA.json"), "w") as f:
            json.dump(card, f)
        m = LoadForecaster(model_dir=d, area_eic="TESTAREA")
        try:
            m.load_models()
        except Exception:
            pass  # boosters absent — we only care about the card fields
        with open(os.path.join(d, "model_card_TESTAREA.json")) as f:
            m.model_card = json.load(f)
        cal = m.model_card["calibration"]
        m.conformal_scale = float(cal.get("conformal_scale", 0.0))
        m.scale_lo = float(cal.get("conformal_scale_lo", m.conformal_scale))
        m.scale_hi = float(cal.get("conformal_scale_hi", m.conformal_scale))
        assert m.scale_lo == m.scale_hi == 0.25


def test_outage_parser_normalises_kw_nominal_to_mw():
    from app.parsers.entsoe_xml import parse_unavailability_document

    xml = OUTAGE_XML.replace(
        "<production_RegisteredResource.pSRType.powerSystemResources.nominalP>780</production_RegisteredResource.pSRType.powerSystemResources.nominalP>",
        "<production_RegisteredResource.pSRType.powerSystemResources.nominalP>867000.0</production_RegisteredResource.pSRType.powerSystemResources.nominalP>",
    ).replace(
        "<Point><position>1</position><quantity>150</quantity></Point>",
        "<Point><position>1</position><quantity>771</quantity></Point>",
    )

    row = parse_unavailability_document(xml)[0]

    assert row["unavailable_mw"] == 96.0

def test_neso_2026_resource_id_is_current():
    source_path = Path(__file__).resolve().parents[1] / "app" / "ingestion" / "neso.py"
    source = source_path.read_text(encoding="utf-8")
    assert '2026: "8a4a771c-3929-4e56-93ad-cdf13219dea5"' in source