"""Seed the database with demo data so the whole product runs end-to-end.

Usage:  cd backend && python scripts/seed_demo.py
Writes: 45 days of hourly load/prices/flows for Italy + zones, sample outages,
        demo TSO forecast, EIC codes, and a couple of data-quality events.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete  # noqa: E402

from app.db.models import (DataQualityEvent, EicCode, FlowPhysical,  # noqa: E402
                           LoadActual, LoadForecastTso, Outage, PriceDayAhead,
                           SessionLocal, init_db)
from app.demo.synthetic import (generate_history, national_load_mw,  # noqa: E402
                                sample_outages)
from app.utils.eic import EIC_SEED  # noqa: E402
from app.utils.timeutils import market_day  # noqa: E402

DAYS_HISTORY = 45
HOURS_TSO_FORECAST = 36


def main():
    init_db()
    db = SessionLocal()
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = now - timedelta(days=DAYS_HISTORY)

    print("Clearing previous demo rows ...")
    for model in (PriceDayAhead, LoadActual, LoadForecastTso, FlowPhysical,
                  Outage, EicCode, DataQualityEvent):
        db.execute(delete(model))
    db.commit()

    print("Seeding EIC codes ...")
    for e in EIC_SEED:
        db.add(EicCode(eic_code=e.eic, code_type=e.code_type,
                       display_name=e.name, aliases=e.aliases, country=e.country))
    db.commit()

    print(f"Generating {DAYS_HISTORY} days of hourly history (load/prices/flows) ...")
    batch = 0
    for row in generate_history(start, now + timedelta(hours=1)):
        if row["kind"] == "load":
            db.add(LoadActual(area_eic=row["area"], ts_utc=row["ts"], load_mw=row["mw"],
                              source_doc_id="demo"))
        elif row["kind"] == "price":
            db.add(PriceDayAhead(area_eic=row["area"], ts_utc=row["ts"],
                                 market_day=row["md"], price_eur_mwh=round(row["eur"], 2),
                                 source_doc_id="demo"))
        elif row["kind"] == "flow":
            db.add(FlowPhysical(from_area_eic=row["from"], to_area_eic=row["to"],
                                ts_utc=row["ts"], mw=round(row["mw"], 1),
                                source_doc_id="demo"))
        batch += 1
        if batch % 5000 == 0:
            db.commit()
            print(f"  ... {batch} rows")
    db.commit()

    print("Seeding demo TSO day-ahead forecast ...")
    for h in range(1, HOURS_TSO_FORECAST + 1):
        ts = now + timedelta(hours=h)
        mw = national_load_mw(ts) * 1.012  # slight systematic bias, as in real life
        db.add(LoadForecastTso(area_eic="10YIT-GRTN-----B", ts_utc=ts,
                               forecast_mw=round(mw, 1), source_doc_id="demo"))
    db.commit()

    print("Seeding sample outages ...")
    for o in sample_outages(now):
        db.add(Outage(**o))
    db.commit()

    print("Seeding sample data-quality events ...")
    db.add(DataQualityEvent(dataset="prices_dayahead", area_eic="10Y1001A1001A75E",
                            ts_utc=now - timedelta(hours=30), severity="info",
                            issue_type="revision",
                            details="SICI hour 18 revised +0.8 EUR/MWh by source"))
    db.add(DataQualityEvent(dataset="load_actual", area_eic="10YIT-GRTN-----B",
                            ts_utc=now - timedelta(hours=54), severity="warn",
                            issue_type="gap", details="1 missing hourly interval, back-filled"))
    db.commit()
    db.close()
    print("Done. Next: python scripts/train_forecast.py")


if __name__ == "__main__":
    main()
