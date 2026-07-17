"""Backfill ERA5 historical weather — the input the load model is missing.

Setup (free, once):
    1. register at https://cds.climate.copernicus.eu
    2. accept the ERA5 licence on the dataset page
    3. put CDS_API_KEY=<uid>:<key> in backend/.env
    4. pip install cdsapi xarray netcdf4

Usage:
    python scripts/backfill_era5.py --area 10YIT-GRTN-----B --years 2
    python scripts/backfill_era5.py --all-markets --years 2
"""
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.dialects.sqlite import insert as sqlite_insert  # noqa: E402

from app.db.models import SessionLocal, WeatherHistory, init_db  # noqa: E402
from app.ingestion.era5 import cds_available, zone_temperature_history  # noqa: E402
from app.utils.eic import EU_MARKETS  # noqa: E402


def do_area(db, area: str, years: int, provider: str = "openmeteo"):
    print(f"\n=== {area}  (provider: {provider}) ===")
    try:
        ser = zone_temperature_history(area, years, provider)
    except Exception as e:
        print(f"  ! {e}")
        return 0
    for ts, t in ser.items():
        stmt = sqlite_insert(WeatherHistory).values(
            area_eic=area, ts_utc=ts.to_pydatetime(), temp_c=float(t), source="era5")
        db.execute(stmt.on_conflict_do_update(
            index_elements=["area_eic", "ts_utc"], set_={"temp_c": float(t)}))
    db.commit()
    print(f"  stored {len(ser)} hours  ({ser.index.min().date()} -> {ser.index.max().date()})")
    print(f"  range {ser.min():.1f} to {ser.max():.1f} degC, mean {ser.mean():.1f}")
    return len(ser)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--area", default="10YIT-GRTN-----B")
    ap.add_argument("--all-markets", action="store_true")
    ap.add_argument("--years", type=int, default=2)
    ap.add_argument("--provider", default="openmeteo", choices=["openmeteo", "cds"],
                    help="openmeteo = ERA5 via JSON, no key (default). "
                         "cds = official Copernicus, needs CDS_API_KEY.")
    args = ap.parse_args()

    ok, why = (True, "") if args.provider == "openmeteo" else cds_available()
    if not ok:
        sys.exit(f"\n  ERA5 not configured: {why}\n\n"
                 f"  Free setup:\n"
                 f"    1. register  https://cds.climate.copernicus.eu\n"
                 f"    2. accept the ERA5 licence on the dataset page\n"
                 f"    3. backend/.env:  CDS_API_KEY=<uid>:<key>\n"
                 f"    4. pip install cdsapi xarray netcdf4\n")

    init_db()
    db = SessionLocal()
    areas = ([m["national"] for m in EU_MARKETS.values()] if args.all_markets
             else [args.area])
    total = sum(do_area(db, a, args.years, args.provider) for a in areas)
    db.close()
    print(f"\nStored {total} weather hours. Now retrain:")
    print(f"  python scripts/train_forecast.py --area {areas[0]}")
    print("  (the model will pick up ERA5 automatically and should beat naive)")


if __name__ == "__main__":
    main()
