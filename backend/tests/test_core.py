"""NRG-Flux test suite.

Includes the DST "golden tests" the plan makes a hard gate:
"No paid pilot until DST tests pass."
Run: cd backend && python -m pytest tests/ -q
"""
import sys
from datetime import datetime, timezone
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
