"""Backfill configured European ENTSO-E markets in bounded windows.

Historical training example:
    python scripts/backfill_eu.py --markets FR DE CH --days 365 \
        --chunk-days 30 --skip-raw-documents

Every market/window is committed in small source batches and followed by a
SQLite quick_check. Database exceptions are never hidden.
"""
import argparse
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.dialects.sqlite import insert as sqlite_insert  # noqa: E402
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db.models import (FlowPhysical, LoadActual, LoadForecastTso,  # noqa: E402
                           PriceDayAhead, RawDocument, SessionLocal, init_db)
from app.ingestion.entsoe import EntsoeClient  # noqa: E402
from app.utils.eic import EU_BORDERS, EU_MARKETS  # noqa: E402
from app.utils.timeutils import market_day  # noqa: E402


def upsert(db, model, values: dict, keys: list[str]):
    stmt = sqlite_insert(model).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=keys,
        set_={key: value for key, value in values.items() if key not in keys},
    )
    db.execute(stmt)


def commit_batch(db, label: str):
    try:
        db.commit()
    except BaseException:
        db.rollback()
        print(f"    rollback: {label}", flush=True)
        raise


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def iter_windows(start: datetime, end: datetime, chunk_days: int):
    cursor = start
    delta = timedelta(days=chunk_days)
    while cursor < end:
        window_end = min(cursor + delta, end)
        yield cursor, window_end
        cursor = window_end


def check_database():
    db = SessionLocal()
    try:
        checkpoint = db.execute(text("PRAGMA wal_checkpoint(FULL)")).one()
        if checkpoint[0] != 0:
            raise RuntimeError(f"SQLite WAL checkpoint was busy: {checkpoint}")
        result = [row[0] for row in db.execute(text("PRAGMA quick_check(1)"))]
        if result != ["ok"]:
            raise RuntimeError("SQLite quick_check failed: " + " | ".join(result))
    finally:
        db.close()


def configure_database():
    db = SessionLocal()
    try:
        journal_mode = db.execute(text("PRAGMA journal_mode=WAL")).scalar_one()
        db.execute(text("PRAGMA synchronous=FULL"))
        db.execute(text("PRAGMA busy_timeout=30000"))
        print(
            f"SQLite: journal_mode={journal_mode}, synchronous=FULL",
            flush=True,
        )
    finally:
        db.close()


def report_non_database_error(db, label: str, exc: Exception):
    db.rollback()
    print(f"    ! {label}: {exc}", flush=True)


def ingest_market_window(
    client,
    country: str,
    start: datetime,
    end: datetime,
    store_raw_documents: bool,
):
    market = EU_MARKETS[country]
    db = SessionLocal()

    try:
        for zone in market["zones"]:
            print(f"  prices {zone}", flush=True)
            try:
                for batch, (xml, rows) in enumerate(
                    client.dayahead_prices(zone, start, end), start=1
                ):
                    if store_raw_documents:
                        db.add(RawDocument(
                            source="entsoe",
                            query=f"A44 {zone}",
                            document_type="A44",
                            payload=xml,
                        ))
                    for row in rows:
                        upsert(db, PriceDayAhead, dict(
                            area_eic=zone,
                            ts_utc=row["ts_utc"],
                            market_day=market_day(row["ts_utc"]),
                            price_eur_mwh=row["price_eur_mwh"],
                            resolution=row["resolution"],
                            source_doc_id="A44",
                        ), ["area_eic", "ts_utc"])
                    commit_batch(db, f"{country} prices {zone} batch {batch}")
                    print(f"    batch {batch}: {len(rows)} rows", flush=True)
            except SQLAlchemyError:
                raise
            except Exception as exc:
                report_non_database_error(db, f"prices {zone}", exc)

        national = market["national"]
        print(f"  load {national}", flush=True)
        try:
            for batch, (_xml, rows) in enumerate(
                client.actual_load(national, start, end), start=1
            ):
                for row in rows:
                    upsert(db, LoadActual, dict(
                        area_eic=national,
                        ts_utc=row["ts_utc"],
                        load_mw=row["mw"],
                        resolution=row["resolution"],
                        source_doc_id="A65",
                    ), ["area_eic", "ts_utc"])
                commit_batch(db, f"{country} load batch {batch}")
                print(f"    batch {batch}: {len(rows)} rows", flush=True)
        except SQLAlchemyError:
            raise
        except Exception as exc:
            report_non_database_error(db, "load", exc)

        print(f"  TSO load forecast {national}", flush=True)
        try:
            for batch, (_xml, rows) in enumerate(
                client.tso_load_forecast(national, start, end + timedelta(days=1)),
                start=1,
            ):
                for row in rows:
                    upsert(db, LoadForecastTso, dict(
                        area_eic=national,
                        ts_utc=row["ts_utc"],
                        forecast_mw=row["mw"],
                        source_doc_id="A65/A01",
                    ), ["area_eic", "ts_utc"])
                commit_batch(db, f"{country} TSO forecast batch {batch}")
                print(f"    batch {batch}: {len(rows)} rows", flush=True)
        except SQLAlchemyError:
            raise
        except Exception as exc:
            report_non_database_error(db, "TSO load forecast", exc)

        for from_area, to_area, label in EU_BORDERS.get(country, []):
            print(f"  flows {label}", flush=True)
            try:
                for batch, (_xml, rows) in enumerate(
                    client.physical_flows(from_area, to_area, start, end), start=1
                ):
                    for row in rows:
                        upsert(db, FlowPhysical, dict(
                            from_area_eic=from_area,
                            to_area_eic=to_area,
                            ts_utc=row["ts_utc"],
                            mw=row["mw"],
                            source_doc_id="A11",
                        ), ["from_area_eic", "to_area_eic", "ts_utc"])
                    commit_batch(db, f"{country} flows {label} batch {batch}")
                    print(f"    batch {batch}: {len(rows)} rows", flush=True)
            except SQLAlchemyError:
                raise
            except Exception as exc:
                report_non_database_error(db, label, exc)
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--markets",
        nargs="+",
        default=["FR", "DE", "CH", "GB"],
        help="country codes, or ALL",
    )
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--start", help="UTC start, for example 2025-10-01")
    parser.add_argument("--end", help="UTC exclusive end, for example 2026-10-01")
    parser.add_argument("--chunk-days", type=int, default=30)
    parser.add_argument("--list", action="store_true")
    parser.add_argument(
        "--skip-raw-documents",
        action="store_true",
        help="store normalized history without duplicating large XML payloads",
    )
    args = parser.parse_args()

    if args.list:
        print(f"{'CC':<4} {'market':<22} {'zones':<6} national")
        for country, market in EU_MARKETS.items():
            print(
                f"{country:<4} {market['name']:<22} "
                f"{len(market['zones']):<6} {market['national']}"
            )
        return

    if args.chunk_days < 1 or args.days < 1:
        parser.error("--days and --chunk-days must be positive")
    if bool(args.start) != bool(args.end):
        parser.error("--start and --end must be supplied together")

    settings = get_settings()
    if not settings.entsoe_api_token:
        sys.exit("ENTSOE_API_TOKEN not set in backend/.env")

    markets = (
        list(EU_MARKETS)
        if args.markets == ["ALL"]
        else [country.upper() for country in args.markets]
    )
    unknown = [country for country in markets if country not in EU_MARKETS]
    if unknown:
        sys.exit(f"unknown markets: {unknown}. Try --list")

    overall_end = parse_utc(args.end) if args.end else datetime.now(timezone.utc)
    overall_start = (
        parse_utc(args.start)
        if args.start
        else overall_end - timedelta(days=args.days)
    )
    if overall_start >= overall_end:
        parser.error("start must be earlier than end")

    init_db()
    configure_database()
    client = EntsoeClient()
    windows = list(iter_windows(overall_start, overall_end, args.chunk_days))
    total_steps = len(markets) * len(windows)
    step = 0

    print(
        f"Backfilling {len(markets)} market(s) from "
        f"{overall_start.isoformat()} to {overall_end.isoformat()} in "
        f"{len(windows)} window(s) each.",
        flush=True,
    )
    print(
        f"Mode: raw_documents={'off' if args.skip_raw_documents else 'on'}",
        flush=True,
    )

    try:
        for country in markets:
            print(f"\n=== {country} · {EU_MARKETS[country]['name']} ===", flush=True)
            for start, end in windows:
                step += 1
                percent = math.floor((step - 1) * 100 / total_steps)
                print(
                    f"\n[{step}/{total_steps} | {percent}%] "
                    f"{start.isoformat()} -> {end.isoformat()}",
                    flush=True,
                )
                ingest_market_window(
                    client,
                    country,
                    start,
                    end,
                    store_raw_documents=not args.skip_raw_documents,
                )
                check_database()
                print(f"[{step}/{total_steps}] checkpoint integrity: ok", flush=True)
    except KeyboardInterrupt:
        print(
            "\nInterrupted safely. The current transaction was rolled back; "
            "completed windows remain committed.",
            flush=True,
        )
        raise SystemExit(130)

    print("\n[100%] European market backfill complete.", flush=True)


if __name__ == "__main__":
    main()
