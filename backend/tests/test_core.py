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
