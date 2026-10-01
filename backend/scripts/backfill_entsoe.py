"""Backfill live ENTSO-E data into canonical tables with bounded windows.

Examples:
    python scripts/backfill_entsoe.py --days 365 --chunk-days 30
    python scripts/backfill_entsoe.py --start 2025-10-01 --end 2026-10-01 --chunk-days 30

Each completed window is committed in small source-document batches and followed
by a SQLite quick_check. Output is flushed so progress is visible immediately.
"""
import argparse
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, func, select as sa_select, text  # noqa: E402
from sqlalchemy.dialects.sqlite import insert as sqlite_insert  # noqa: E402
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db.models import (EicCode, FlowPhysical, LoadActual,  # noqa: E402
                           LoadForecastTso, Outage, PriceDayAhead,
                           RawDocument, SessionLocal, init_db)
from app.ingestion.entsoe import EntsoeClient  # noqa: E402
from app.utils.eic import EIC_SEED, IT_BORDERS, ITALY_ZONES  # noqa: E402
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


def seed_eic_codes():
    db = SessionLocal()
    try:
        count = db.execute(sa_select(func.count()).select_from(EicCode)).scalar_one()
        if count == 0:
            print("Seeding EIC display names ...", flush=True)
            for entry in EIC_SEED:
                db.add(EicCode(
                    eic_code=entry.eic,
                    code_type=entry.code_type,
                    display_name=entry.name,
                    aliases=entry.aliases,
                    country=entry.country,
                ))
            commit_batch(db, "EIC seed")
    finally:
        db.close()


def ingest_window(
    client,
    start: datetime,
    end: datetime,
    include_future_outages: bool,
    store_raw_documents: bool,
    ingest_outages: bool,
):
    db = SessionLocal()
    national = "10YIT-GRTN-----B"

    try:
        for zone in ITALY_ZONES:
            print(f"  prices {zone}", flush=True)
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
                commit_batch(db, f"prices {zone} batch {batch}")
                print(f"    batch {batch}: {len(rows)} rows", flush=True)

        for area in [national, *ITALY_ZONES]:
            print(f"  load {area}", flush=True)
            for batch, (xml, rows) in enumerate(
                client.actual_load(area, start, end), start=1
            ):
                if store_raw_documents:
                    db.add(RawDocument(
                        source="entsoe",
                        query=f"A65/A16 {area}",
                        document_type="A65",
                        payload=xml,
                    ))
                for row in rows:
                    upsert(db, LoadActual, dict(
                        area_eic=area,
                        ts_utc=row["ts_utc"],
                        load_mw=row["mw"],
                        resolution=row["resolution"],
                        source_doc_id="A65",
                    ), ["area_eic", "ts_utc"])
                commit_batch(db, f"load {area} batch {batch}")
                print(f"    batch {batch}: {len(rows)} rows", flush=True)

        print(f"  TSO load forecast {national}", flush=True)
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
            commit_batch(db, f"TSO forecast batch {batch}")
            print(f"    batch {batch}: {len(rows)} rows", flush=True)

        for from_area, to_area, label in IT_BORDERS:
            print(f"  flows {label}", flush=True)
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
                commit_batch(db, f"flows {label} batch {batch}")
                print(f"    batch {batch}: {len(rows)} rows", flush=True)

        if not ingest_outages:
            print("  outages skipped for historical training backfill", flush=True)
            return

        outage_end = end + timedelta(days=30) if include_future_outages else end

        print("  outages generation (A80)", flush=True)
        try:
            for batch, (xmls, rows) in enumerate(
                client.outages(national, start, outage_end, doc_type="A80"), start=1
            ):
                if store_raw_documents:
                    for xml in xmls:
                        db.add(RawDocument(
                            source="entsoe",
                            query=f"A80 {national}",
                            document_type="A80",
                            payload=xml,
                        ))
                for row in rows:
                    row.pop("revision", None)
                    row.pop("created", None)
                    upsert(db, Outage, row, ["outage_id"])
                commit_batch(db, f"A80 batch {batch}")
                print(f"    batch {batch}: {len(rows)} rows", flush=True)
        except SQLAlchemyError:
            raise
        except Exception as exc:
            db.rollback()
            print(f"    ! generation outage ingestion failed: {exc}", flush=True)

        print("  outages transmission (A78, per border)", flush=True)
        for from_area, to_area, label in IT_BORDERS:
            try:
                for batch, (xmls, rows) in enumerate(
                    client.outages(
                        national,
                        start,
                        outage_end,
                        doc_type="A78",
                        in_domain=to_area,
                        out_domain=from_area,
                    ),
                    start=1,
                ):
                    if store_raw_documents:
                        for xml in xmls:
                            db.add(RawDocument(
                                source="entsoe",
                                query=f"A78 {label}",
                                document_type="A78",
                                payload=xml,
                            ))
                    for row in rows:
                        row.pop("revision", None)
                        row.pop("created", None)
                        upsert(db, Outage, row, ["outage_id"])
                    commit_batch(db, f"A78 {label} batch {batch}")
                    print(f"    {label} batch {batch}: {len(rows)} rows", flush=True)
            except SQLAlchemyError:
                raise
            except Exception as exc:
                db.rollback()
                print(f"    ! {label}: {exc}", flush=True)
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--start", help="UTC start, for example 2025-10-01")
    parser.add_argument("--end", help="UTC exclusive end, for example 2026-10-01")
    parser.add_argument("--chunk-days", type=int, default=30)
    parser.add_argument(
        "--reset-outages",
        action="store_true",
        help="clear the outages table before ingestion",
    )
    parser.add_argument(
        "--skip-outages",
        action="store_true",
        help="skip outage ingestion for historical model-training backfills",
    )
    parser.add_argument(
        "--skip-raw-documents",
        action="store_true",
        help="store normalized rows without duplicating large historical XML payloads",
    )
    args = parser.parse_args()

    if args.chunk_days < 1 or args.days < 1:
        parser.error("--days and --chunk-days must be positive")
    if bool(args.start) != bool(args.end):
        parser.error("--start and --end must be supplied together")

    settings = get_settings()
    if not settings.entsoe_api_token:
        sys.exit(
            "ENTSOE_API_TOKEN is not set. Register at transparency.entsoe.eu, "
            "request Web API access, and put the token in backend/.env"
        )

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
    seed_eic_codes()

    if args.reset_outages:
        db = SessionLocal()
        try:
            print("Clearing outages table ...", flush=True)
            db.execute(delete(Outage))
            commit_batch(db, "reset outages")
        finally:
            db.close()

    windows = list(iter_windows(overall_start, overall_end, args.chunk_days))
    total = len(windows)
    client = EntsoeClient()

    print(
        f"Backfilling Italy from {overall_start.isoformat()} to "
        f"{overall_end.isoformat()} in {total} window(s) ...",
        flush=True,
    )
    print(
        "Mode: "
        f"outages={'off' if args.skip_outages else 'on'}, "
        f"raw_documents={'off' if args.skip_raw_documents else 'on'}",
        flush=True,
    )

    try:
        for index, (start, end) in enumerate(windows, start=1):
            percent = math.floor((index - 1) * 100 / total)
            print(
                f"\n[{index}/{total} | {percent}%] "
                f"{start.isoformat()} -> {end.isoformat()}",
                flush=True,
            )
            ingest_window(
                client,
                start,
                end,
                include_future_outages=index == total,
                store_raw_documents=not args.skip_raw_documents,
                ingest_outages=not args.skip_outages,
            )
            check_database()
            print(f"[{index}/{total}] checkpoint integrity: ok", flush=True)
    except KeyboardInterrupt:
        print(
            "\nInterrupted safely. The current transaction was rolled back; "
            "completed windows remain committed.",
            flush=True,
        )
        raise SystemExit(130)

    print("\n[100%] Backfill complete.", flush=True)


if __name__ == "__main__":
    main()
