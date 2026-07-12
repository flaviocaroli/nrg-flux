"""Backfill GB national demand from the NESO Data Portal into load_actual.

Usage:
    cd backend
    python scripts/backfill_neso.py --days 30

No key required for the open dataset; set NESO_API_KEY in .env for higher
rate limits. After backfilling you can train a GB forecaster:
    python scripts/train_forecast.py --area 10YGB----------A   (live mode)
"""
import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.dialects.sqlite import insert as sqlite_insert  # noqa: E402

from app.db.models import LoadActual, SessionLocal, init_db  # noqa: E402
from app.ingestion.neso import GB_EIC, NesoClient  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args()

    init_db()
    db = SessionLocal()
    client = NesoClient()
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=args.days)

    print(f"Fetching GB national demand from NESO ({args.days} days) ...")
    ser = client.gb_demand(start, end)
    if ser.empty:
        sys.exit("No records returned — check the resource ids in "
                 "app/ingestion/neso.py against the NESO data portal.")

    print(f"Upserting {len(ser)} hourly points into load_actual ({GB_EIC}) ...")
    for ts, mw in ser.items():
        stmt = sqlite_insert(LoadActual).values(
            area_eic=GB_EIC, ts_utc=ts.to_pydatetime(), load_mw=float(mw),
            resolution="PT60M", source_doc_id="neso-historic-demand")
        stmt = stmt.on_conflict_do_update(
            index_elements=["area_eic", "ts_utc"],
            set_={"load_mw": float(mw)})
        db.execute(stmt)
    db.commit()
    db.close()
    print("Done. GB demand is now queryable: /v1/load/actual?area=10YGB----------A")


if __name__ == "__main__":
    main()
