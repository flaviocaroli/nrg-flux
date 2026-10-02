"""Fallback scheduler loop — for environments without systemd (e.g. WSL).

    python scripts/run_scheduler.py

Behaviour:
  - ingest immediately on startup, then on each whole UTC hour;
  - ingest_cycle.py refreshes forecasts after a successful data top-up;
  - retrain daily at 13:05 Europe/Rome, with catch-up if an hourly ingest
    overlaps the 13:05 slot.

The catch-up rule matters because this launcher is intentionally sequential:
an ingest that starts at 13:00 and finishes at 13:10 must not silently skip
that day's retrain.
"""
from __future__ import annotations

import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.utils.scheduling import (  # noqa: E402
    MARKET_TZ,
    RETRAIN_LOCAL_HOUR,
    RETRAIN_LOCAL_MINUTE,
    next_daily_run,
    next_hour,
)


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}Z] scheduler: {msg}",
          flush=True)


def fire(script: str) -> int:
    log(f"firing {script}")
    try:
        rc = subprocess.run([sys.executable, f"scripts/{script}"],
                            cwd=BACKEND).returncode
        log(f"{script} exited rc={rc}")
        return rc
    except Exception as exc:  # never let one job kill the scheduler loop
        log(f"{script} crashed the launcher: {exc}")
        return 1


def retrain_due(now_utc: datetime, last_retrain_local_day: str | None) -> bool:
    local = now_utc.astimezone(MARKET_TZ)
    gate = local.replace(hour=RETRAIN_LOCAL_HOUR,
                         minute=RETRAIN_LOCAL_MINUTE,
                         second=0, microsecond=0)
    return local >= gate and local.date().isoformat() != last_retrain_local_day


def main() -> None:
    log("started — ingest hourly; refresh forecasts after ingest; "
        "retrain daily 13:05 Europe/Rome")

    last_retrain_local_day: str | None = None

    # Catch up data and forecasts immediately on startup.
    fire("ingest_cycle.py")
    now = datetime.now(timezone.utc)
    if retrain_due(now, last_retrain_local_day):
        rc = fire("daily_retrain.py")
        if rc == 0:
            last_retrain_local_day = now.astimezone(MARKET_TZ).date().isoformat()
        else:
            log("daily retrain failed; it will be retried after the next hourly ingest")

    while True:
        now = datetime.now(timezone.utc)
        n_ingest = next_hour(now)
        n_retrain = next_daily_run(now)
        nxt, job = min((n_ingest, "ingest_cycle.py"),
                       (n_retrain, "daily_retrain.py"))
        wait = max(0.0, (nxt - datetime.now(timezone.utc)).total_seconds())
        log(f"next: {job} at {nxt:%Y-%m-%d %H:%M}Z (in {wait / 60:.1f} min)")
        time.sleep(wait)

        rc = fire(job)
        finished = datetime.now(timezone.utc)

        if job == "daily_retrain.py":
            if rc == 0:
                last_retrain_local_day = finished.astimezone(MARKET_TZ).date().isoformat()
            else:
                log("daily retrain failed; it will be retried after the next hourly ingest")
        elif retrain_due(finished, last_retrain_local_day):
            # Example: 13:00 ingest runs through 13:05. Retrain immediately
            # after it finishes instead of rolling the retrain to tomorrow.
            log("13:05 retrain slot passed during ingestion — running catch-up retrain now")
            retrain_rc = fire("daily_retrain.py")
            if retrain_rc == 0:
                last_retrain_local_day = datetime.now(timezone.utc).astimezone(
                    MARKET_TZ).date().isoformat()
            else:
                log("catch-up retrain failed; it will be retried after the next hourly ingest")


if __name__ == "__main__":
    main()
