"""Fallback scheduler loop — for environments without systemd (e.g. WSL).

    python3 scripts/run_scheduler.py

Prefer the systemd timers in deploy/systemd/ on any real host: they survive
reboots, log to journald, and never overlap. This loop exists so the pipeline
can run TODAY on the WSL laptop without touching cron (which, per the trap
list, dies the moment the WSL window closes — keep this process in a tmux/
screen session or a Windows service wrapper instead).

Behaviour:
  - every :00 and :30 -> scripts/ingest_cycle.py
  - daily at 13:05 Europe/Rome (DST-aware) -> scripts/daily_retrain.py
Jobs run as subprocesses; a crashing job never kills the loop, and the flock
inside each job prevents overlap even if a run outlasts its slot.
"""
from __future__ import annotations

import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.utils.scheduling import next_daily_run, next_half_hour  # noqa: E402


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}Z] scheduler: {msg}",
          flush=True)


def fire(script: str) -> None:
    log(f"firing {script}")
    try:
        rc = subprocess.run([sys.executable, f"scripts/{script}"],
                            cwd=BACKEND).returncode
        log(f"{script} exited rc={rc}")
    except Exception as e:  # never let a job take the loop down
        log(f"{script} crashed the launcher: {e}")


def main() -> None:
    log("started — ingest every 30 min, retrain daily 13:05 Europe/Rome")
    fire("ingest_cycle.py")  # catch up immediately on start
    while True:
        now = datetime.now(timezone.utc)
        n_ingest, n_retrain = next_half_hour(now), next_daily_run(now)
        nxt, job = min((n_ingest, "ingest_cycle.py"),
                       (n_retrain, "daily_retrain.py"))
        wait = max(0.0, (nxt - datetime.now(timezone.utc)).total_seconds())
        log(f"next: {job} at {nxt:%Y-%m-%d %H:%M}Z (in {wait / 60:.1f} min)")
        time.sleep(wait)
        fire(job)
        # a retrain slot also counts as that half-hour's ingest (it ingests
        # internally), so just loop — next_half_hour will move on naturally.


if __name__ == "__main__":
    main()
