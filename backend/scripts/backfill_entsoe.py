"""Backfill live ENTSO-E data into the canonical tables (plan week 1).

Usage:
    cd backend
    ENTSOE_API_TOKEN=... DEMO_MODE=false python scripts/backfill_entsoe.py --days 30

Stores raw XML in raw_documents (raw-first governance) and normalized rows in
prices_dayahead / load_actual / load_forecast_tso / flows_physical.
"""
import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.dialects.sqlite import insert as sqlite_insert  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db.models import (FlowPhysical, LoadActual, LoadForecastTso,  # noqa: E402
                           Outage, PriceDayAhead, RawDocument, SessionLocal,
                           init_db)
from app.ingestion.entsoe import EntsoeClient  # noqa: E402
from app.utils.eic import IT_BORDERS, ITALY_ZONES  # noqa: E402
from app.utils.timeutils import market_day  # noqa: E402


def upsert(db, model, values: dict, keys: list[str]):
    stmt = sqlite_insert(model).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=keys, set_={k: v for k, v in values.items() if k not in keys})
    db.execute(stmt)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args()

    s = get_settings()
    if not s.entsoe_api_token:
        sys.exit("ENTSOE_API_TOKEN is not set. Register at transparency.entsoe.eu, "
                 "request Web API access, and put the token in backend/.env")

    init_db()
    db = SessionLocal()
    client = EntsoeClient()
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=args.days)
    national = "10YIT-GRTN-----B"

    print(f"Backfilling {args.days} days for Italy ...")

    for zone in ITALY_ZONES:
        print(f"  prices {zone}")
        for xml, rows in client.dayahead_prices(zone, start, end):
            db.add(RawDocument(source="entsoe", query=f"A44 {zone}", document_type="A44",
                               payload=xml))
            for r in rows:
                upsert(db, PriceDayAhead, dict(
                    area_eic=zone, ts_utc=r["ts_utc"], market_day=market_day(r["ts_utc"]),
                    price_eur_mwh=r["price_eur_mwh"], resolution=r["resolution"],
                    source_doc_id="A44"), ["area_eic", "ts_utc"])
            db.commit()

    for area in [national, *ITALY_ZONES]:
        print(f"  load {area}")
        for xml, rows in client.actual_load(area, start, end):
            db.add(RawDocument(source="entsoe", query=f"A65/A16 {area}",
                               document_type="A65", payload=xml))
            for r in rows:
                upsert(db, LoadActual, dict(area_eic=area, ts_utc=r["ts_utc"],
                                            load_mw=r["mw"], resolution=r["resolution"],
                                            source_doc_id="A65"), ["area_eic", "ts_utc"])
            db.commit()

    print(f"  TSO load forecast {national}")
    for xml, rows in client.tso_load_forecast(national, start, end + timedelta(days=1)):
        for r in rows:
            upsert(db, LoadForecastTso, dict(area_eic=national, ts_utc=r["ts_utc"],
                                             forecast_mw=r["mw"], source_doc_id="A65/A01"),
                   ["area_eic", "ts_utc"])
        db.commit()

    for f, t, label in IT_BORDERS:
        print(f"  flows {label}")
        for xml, rows in client.physical_flows(f, t, start, end):
            for r in rows:
                upsert(db, FlowPhysical, dict(from_area_eic=f, to_area_eic=t,
                                              ts_utc=r["ts_utc"], mw=r["mw"],
                                              source_doc_id="A11"),
                       ["from_area_eic", "to_area_eic", "ts_utc"])
            db.commit()

    # outages: generation (A80) and transmission (A78) unavailabilities.
    # Window includes the future so ongoing/announced outages are captured.
    for doc_type, label in (("A80", "generation"), ("A78", "transmission")):
        print(f"  outages {label} ({doc_type})")
        try:
            for xmls, rows in client.outages(national, start,
                                             end + timedelta(days=30),
                                             doc_type=doc_type):
                for xml in xmls:
                    db.add(RawDocument(source="entsoe",
                                       query=f"{doc_type} {national}",
                                       document_type=doc_type, payload=xml))
                for r in rows:
                    r.pop("revision", None)
                    r.pop("created", None)
                    upsert(db, Outage, r, ["outage_id"])
                db.commit()
        except Exception as exc:
            print(f"    ! outage ingestion for {doc_type} failed: {exc}")
            print("      (continuing — check token permissions / query window)")

    db.close()
    print("Backfill complete. Set DEMO_MODE=false in .env and restart the API.")


if __name__ == "__main__":
    main()
