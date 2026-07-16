"""Backfill any EU market covered by ENTSO-E — no new credentials needed.

The SAME ENTSO-E token that serves Italy also serves France, Germany-Lux,
Switzerland, Great Britain, Spain, Austria and the Netherlands. Expansion is
a configuration change, not an integration project.

Usage:
    python scripts/backfill_eu.py --markets FR DE CH GB --days 30
    python scripts/backfill_eu.py --markets ALL --days 7
    python scripts/backfill_eu.py --list

Then train per market:
    python scripts/train_forecast.py --area 10YFR-RTE------C
"""
import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.dialects.sqlite import insert as sqlite_insert  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db.models import (FlowPhysical, LoadActual, LoadForecastTso,  # noqa: E402
                           PriceDayAhead, RawDocument, SessionLocal, init_db)
from app.ingestion.entsoe import EntsoeClient  # noqa: E402
from app.utils.eic import EU_BORDERS, EU_MARKETS  # noqa: E402
from app.utils.timeutils import market_day  # noqa: E402


def upsert(db, model, values, keys):
    stmt = sqlite_insert(model).values(**values)
    db.execute(stmt.on_conflict_do_update(
        index_elements=keys, set_={k: v for k, v in values.items() if k not in keys}))


def do_market(db, client, cc: str, start, end):
    m = EU_MARKETS[cc]
    print(f"\n=== {cc} · {m['name']} ===")

    for zone in m["zones"]:
        print(f"  prices {zone}")
        try:
            for xml, rows in client.dayahead_prices(zone, start, end):
                db.add(RawDocument(source="entsoe", query=f"A44 {zone}",
                                   document_type="A44", payload=xml))
                for r in rows:
                    upsert(db, PriceDayAhead, dict(
                        area_eic=zone, ts_utc=r["ts_utc"],
                        market_day=market_day(r["ts_utc"]),
                        price_eur_mwh=r["price_eur_mwh"],
                        resolution=r["resolution"], source_doc_id="A44"),
                        ["area_eic", "ts_utc"])
                db.commit()
        except Exception as e:
            print(f"    ! {e}")

    nat = m["national"]
    print(f"  load {nat}")
    try:
        for xml, rows in client.actual_load(nat, start, end):
            for r in rows:
                upsert(db, LoadActual, dict(area_eic=nat, ts_utc=r["ts_utc"],
                                            load_mw=r["mw"], resolution=r["resolution"],
                                            source_doc_id="A65"), ["area_eic", "ts_utc"])
            db.commit()
    except Exception as e:
        print(f"    ! {e}")

    print(f"  TSO load forecast {nat}")
    try:
        for xml, rows in client.tso_load_forecast(nat, start, end + timedelta(days=1)):
            for r in rows:
                upsert(db, LoadForecastTso, dict(area_eic=nat, ts_utc=r["ts_utc"],
                                                 forecast_mw=r["mw"],
                                                 source_doc_id="A65/A01"),
                       ["area_eic", "ts_utc"])
            db.commit()
    except Exception as e:
        print(f"    ! {e}")

    for f, t, label in EU_BORDERS.get(cc, []):
        print(f"  flows {label}")
        try:
            for xml, rows in client.physical_flows(f, t, start, end):
                for r in rows:
                    upsert(db, FlowPhysical, dict(from_area_eic=f, to_area_eic=t,
                                                  ts_utc=r["ts_utc"], mw=r["mw"],
                                                  source_doc_id="A11"),
                           ["from_area_eic", "to_area_eic", "ts_utc"])
                db.commit()
        except Exception as e:
            print(f"    ! {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--markets", nargs="+", default=["FR", "DE", "CH", "GB"],
                    help="country codes, or ALL")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--list", action="store_true", help="show supported markets and exit")
    args = ap.parse_args()

    if args.list:
        print(f"{'CC':<4} {'market':<22} {'zones':<6} national")
        for cc, m in EU_MARKETS.items():
            print(f"{cc:<4} {m['name']:<22} {len(m['zones']):<6} {m['national']}")
        print("\nAll served by your existing ENTSO-E token — no new API keys.")
        return

    s = get_settings()
    if not s.entsoe_api_token:
        sys.exit("ENTSOE_API_TOKEN not set in backend/.env")

    markets = list(EU_MARKETS) if args.markets == ["ALL"] else [c.upper() for c in args.markets]
    unknown = [c for c in markets if c not in EU_MARKETS]
    if unknown:
        sys.exit(f"unknown markets: {unknown}. Try --list")

    init_db()
    db = SessionLocal()
    client = EntsoeClient()
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=args.days)

    for cc in markets:
        do_market(db, client, cc, start, end)
    db.close()

    print("\nDone. Train a forecaster per market, e.g.:")
    for cc in markets[:2]:
        print(f"  python scripts/train_forecast.py --area {EU_MARKETS[cc]['national']}")


if __name__ == "__main__":
    main()
