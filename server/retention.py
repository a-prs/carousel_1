#!/usr/bin/env python3
"""retention.py — TTL cleanup for carousel-studio job files (plan §7 / §11 Phase 11).

Run daily by the (already-drafted, NOT enabled — red zone) systemd timer
`multi2-carousel-retention.timer` -> `multi2-carousel-retention.service`
(oneshot, WorkingDirectory=server, `python retention.py`).

Walks `data/carousel-studio/jobs/*` and, for directories whose mtime is older
than TTL_HOURS (default 72):
  - deletes the directory's file contents (the directory itself is left in
    place, empty — cheap and avoids racing an in-flight write to a brand new
    subdirectory with the same name);
  - if a matching `generations.job_id` row exists, sets `expired=1`.

`generations` / `activity_log` rows are NEVER deleted or content-modified by
this script — only the `expired` flag changes, and only on the row matching a
cleaned job_id. Directories with no matching generations row (e.g. an
abandoned `pending_uploads` cover that was never turned into a generation)
are cleaned the same way, just without a DB flag to flip.

MAJOR M5 (adversarial review): this is a *separate process* writing the same
SQLite file concurrently with the running app (and, in tests, with itself).
Every DB write goes through `db.py`'s connections, which already set
`PRAGMA busy_timeout=5000`. On top of that, each job directory is its own
short unit of work: a failure on one job (locked DB row, a file that vanished
mid-cleanup because a client is actively downloading it, a permission error)
is caught, logged, and counted as an error for THIS RUN — it never aborts the
rest of the run, and never raises out of `main()`. The next scheduled run
picks up anything left over.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from pathlib import Path

import db

logger = logging.getLogger("carousel.retention")

DEFAULT_TTL_HOURS = float(os.environ.get("CAROUSEL_RETENTION_TTL_HOURS", "72"))


def _data_dir() -> Path:
    return Path(os.environ.get("CAROUSEL_DATA_DIR", "/home/office/multi2/data/carousel-studio"))


def jobs_dir() -> Path:
    return _data_dir() / "jobs"


def _is_stale(dir_path: Path, now: float, ttl_hours: float) -> bool:
    try:
        mtime = dir_path.stat().st_mtime
    except FileNotFoundError:
        return False
    return (now - mtime) / 3600 >= ttl_hours


def _delete_dir_contents(dir_path: Path) -> bool:
    """Remove every file/subdir under dir_path (keep dir_path itself).

    Returns True if everything was removed cleanly. A file that is already
    gone (FileNotFoundError — e.g. raced with an active download) is treated
    as success, not an error. Any other failure (PermissionError, OSError,
    'database is locked'-style issues don't apply here since this is pure
    filesystem I/O) is logged and reported so the caller can retry next run.
    """
    ok = True
    try:
        children = list(dir_path.iterdir())
    except FileNotFoundError:
        return True  # whole directory is already gone — nothing to do
    for child in children:
        try:
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
        except FileNotFoundError:
            continue  # already gone — fine, not an error
        except Exception:
            logger.exception("retention: failed to remove %s (will retry next run)", child)
            ok = False
    return ok


async def _mark_expired(job_id: str) -> bool:
    """Its own short transaction (via db.update_generation) — MAJOR M5."""
    try:
        await db.update_generation(job_id, expired=1)
        return True
    except Exception:
        logger.exception("retention: failed to mark expired job_id=%s (will retry next run)", job_id)
        return False


async def run_once(ttl_hours: float = DEFAULT_TTL_HOURS) -> dict:
    """Process jobs/* once. Never raises — every per-job failure is caught,
    logged, and counted in the returned summary so the process (and any
    concurrent invocation) survives a single bad job directory."""
    root = jobs_dir()
    summary = {"scanned": 0, "cleaned": 0, "marked_expired": 0, "errors": 0}
    if not root.exists():
        return summary

    now = time.time()
    try:
        entries = sorted(root.iterdir())
    except FileNotFoundError:
        return summary

    for entry in entries:
        try:
            if not entry.is_dir():
                continue
            summary["scanned"] += 1
            if not _is_stale(entry, now, ttl_hours):
                continue

            job_id = entry.name
            deleted_ok = _delete_dir_contents(entry)
            if deleted_ok:
                summary["cleaned"] += 1
            else:
                summary["errors"] += 1

            try:
                g = await db.get_generation(job_id)
            except Exception:
                logger.exception("retention: db lookup failed for job_id=%s (will retry next run)", job_id)
                summary["errors"] += 1
                continue

            if g and not g["expired"]:
                if await _mark_expired(job_id):
                    summary["marked_expired"] += 1
                else:
                    summary["errors"] += 1
        except Exception:
            # Belt-and-braces: a single job directory must never take down the
            # whole run (MAJOR M5) — log and move on to the next one.
            logger.exception("retention: unexpected error processing %s (will retry next run)", entry)
            summary["errors"] += 1
            continue

    return summary


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    summary = asyncio.run(run_once())
    logger.info("retention run: %s", summary)
    print(summary)


if __name__ == "__main__":
    main()
