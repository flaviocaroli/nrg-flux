"""Ingest fuel prices (TTF gas, EUA carbon) into the fuel_prices table.

There is no free licensed TTF feed — see app/ingestion/fuel.py for the full
explanation. Three ways to get data in:

    # 1. CSV you control (production path — always legal)
    python scripts/ingest_fuel.py --provider csv --path ttf.csv
    #    CSV format:  date,price
    #                 2026-07-01,34.10

    # 2. Yahoo Finance (EVALUATION ONLY — do not resell this data)
    pip install yfinance
    python scripts/ingest_fuel.py --provider yfinance --days 730

    # 3. Flat assumed price (scenarios / smoke tests)
    python scripts/ingest_fuel.py --provider manual --price 35
"""
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.dialects.sqlite import insert as sqlite_insert  # noqa: E402

from app.db.models import FuelPrice, SessionLocal, init_db  # noqa: E402
from app.ingestion.fuel import fetch  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="csv", choices=["csv", "yfinance", "manual", "flat"])
    ap.add_argument("--fuel", default="TTF", choices=["TTF", "EUA"])
    ap.add_argument("--path", default="", help="CSV path when --provider csv")
    ap.add_argument("--price", type=float, default=35.0, help="flat price when --provider manual")
    ap.add_argument("--days", type=int, default=730)
    args = ap.parse_args()

    init_db()
    try:
        s = fetch(args.provider, args.fuel, args.path, args.price, args.days)
    except Exception as e:
        sys.exit(f"fuel fetch failed: {e}")
    if s.empty:
        sys.exit("no rows returned")

    unit = "EUR/MWh" if args.fuel == "TTF" else "EUR/tCO2"
    db = SessionLocal()
    for ts, price in s.items():
        day = ts.strftime("%Y-%m-%d")
        stmt = sqlite_insert(FuelPrice).values(
            fuel=args.fuel, day=day, price=float(price), unit=unit,
            provider=args.provider, retrieved_at_utc=datetime.now(timezone.utc))
        stmt = stmt.on_conflict_do_update(index_elements=["fuel", "day"],
                                          set_={"price": float(price),
                                                "provider": args.provider})
        db.execute(stmt)
    db.commit()
    db.close()
    print(f"Ingested {len(s)} daily {args.fuel} prices "
          f"({s.index.min().date()} → {s.index.max().date()}) via {args.provider}")
    print(f"  latest: {float(s.iloc[-1]):.2f} {unit}")
    if args.provider == "yfinance":
        print("  ! EVALUATION ONLY — do not redistribute. License EEX/ICE for production.")
    print("\nNext: python scripts/train_price_forecast.py   (gas feature now active)")


if __name__ == "__main__":
    main()
