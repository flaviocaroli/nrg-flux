"""Small cross-platform non-blocking process lock.

The ingestion scripts originally imported :mod:`fcntl` directly, which works
on Linux/WSL but crashes immediately under native Windows PowerShell. Keep the
handle alive for the lifetime of the job; the operating system releases it if
the process exits unexpectedly.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import BinaryIO


def try_process_lock(path: str | Path) -> BinaryIO | None:
    """Return an open, locked handle, or ``None`` when another job owns it."""
    handle = open(Path(path), "a+b")
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        # Do not read byte 0 here: another process may already hold an
        # exclusive Windows byte-range lock on it, in which case even the
        # probe read raises PermissionError before LK_NBLCK can report the
        # expected non-blocking contention. fstat inspects metadata without
        # touching the locked byte.
        if os.fstat(handle.fileno()).st_size == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            handle.close()
            return None
    else:
        import fcntl

        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            handle.close()
            return None
    return handle
