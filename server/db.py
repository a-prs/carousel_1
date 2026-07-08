"""db.py — aiosqlite schema + helpers for carousel-studio (plan §3).

Multitenant: clients hold per-tenant secrets (bcrypt hash) and design_tokens.
generations = permanent history (never deleted by retention). activity_log =
lightweight action log (created now, written from Phase 9). pending_uploads =
cover uploads not yet bound to a generation (closes cross-tenant leak B5).

Every connection: WAL + busy_timeout=5000 (MAJOR M5 — retention.py writes the
same SQLite concurrently) + foreign_keys=ON.
"""
from __future__ import annotations

import json as _json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

import aiosqlite

logger = logging.getLogger(__name__)


def _default_db_path() -> Path:
    data_dir = os.environ.get("CAROUSEL_DATA_DIR", "/home/office/multi2/data/carousel-studio")
    return Path(data_dir) / "carousel.db"


_db_path: Path = _default_db_path()


def init_db(path: str | Path) -> None:
    global _db_path
    _db_path = Path(path)


def db_path() -> Path:
    return _db_path


_SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    slug          TEXT NOT NULL UNIQUE,
    display_name  TEXT,
    ig_handle     TEXT,
    password_hash TEXT NOT NULL,
    design_tokens TEXT NOT NULL DEFAULT '{}',
    token_version INTEGER NOT NULL DEFAULT 1,
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS generations (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id    INTEGER NOT NULL REFERENCES clients(id),
    job_id       TEXT NOT NULL UNIQUE,
    cover_title  TEXT,
    slide_count  INTEGER DEFAULT 0,
    status       TEXT DEFAULT 'queued',
    error        TEXT,
    expired      INTEGER DEFAULT 0,
    created_at   TEXT DEFAULT (datetime('now')),
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_gen_client ON generations(client_id, created_at);

CREATE TABLE IF NOT EXISTS activity_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id  INTEGER NOT NULL REFERENCES clients(id),
    job_id     TEXT,
    action     TEXT NOT NULL,
    meta       TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_act_client ON activity_log(client_id, created_at);

CREATE TABLE IF NOT EXISTS pending_uploads (
    upload_id  TEXT PRIMARY KEY,
    client_id  INTEGER NOT NULL REFERENCES clients(id),
    created_at TEXT DEFAULT (datetime('now'))
);
"""


@asynccontextmanager
async def _conn():
    _db_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(_db_path) as c:
        c.row_factory = aiosqlite.Row
        await c.execute("PRAGMA journal_mode=WAL")
        await c.execute("PRAGMA busy_timeout=5000")
        await c.execute("PRAGMA foreign_keys=ON")
        yield c


async def apply_schema() -> None:
    async with _conn() as c:
        for stmt in [s.strip() for s in _SCHEMA.split(";") if s.strip()]:
            await c.execute(stmt)
        await c.commit()
    logger.info("carousel-studio schema applied: %s", _db_path)


def _d(row) -> Optional[dict]:
    return dict(row) if row is not None else None


# --------------------------------------------------------------------------
# clients
# --------------------------------------------------------------------------

async def create_client(
    slug: str,
    password_hash: str,
    design_tokens: dict | str,
    display_name: Optional[str] = None,
    ig_handle: Optional[str] = None,
) -> int:
    tokens = design_tokens if isinstance(design_tokens, str) else _json.dumps(
        design_tokens, ensure_ascii=False
    )
    async with _conn() as c:
        cur = await c.execute(
            """INSERT INTO clients (slug, display_name, ig_handle, password_hash, design_tokens)
               VALUES (?, ?, ?, ?, ?)""",
            (slug, display_name, ig_handle, password_hash, tokens),
        )
        await c.commit()
        return cur.lastrowid


async def get_client_by_slug(slug: str) -> Optional[dict]:
    async with _conn() as c:
        row = await (await c.execute("SELECT * FROM clients WHERE slug=?", (slug,))).fetchone()
    return _d(row)


async def get_client_by_id(cid: int) -> Optional[dict]:
    async with _conn() as c:
        row = await (await c.execute("SELECT * FROM clients WHERE id=?", (cid,))).fetchone()
    return _d(row)


async def list_clients() -> list[dict]:
    async with _conn() as c:
        rows = await (await c.execute("SELECT * FROM clients ORDER BY slug")).fetchall()
    return [dict(r) for r in rows]


async def set_theme(slug: str, design_tokens: dict | str) -> None:
    tokens = design_tokens if isinstance(design_tokens, str) else _json.dumps(
        design_tokens, ensure_ascii=False
    )
    async with _conn() as c:
        await c.execute("UPDATE clients SET design_tokens=? WHERE slug=?", (tokens, slug))
        await c.commit()


async def set_password(slug: str, password_hash: str) -> None:
    """Reset password AND bump token_version — invalidates old tokens (MAJOR M7)."""
    async with _conn() as c:
        await c.execute(
            "UPDATE clients SET password_hash=?, token_version=token_version+1 WHERE slug=?",
            (password_hash, slug),
        )
        await c.commit()


# --------------------------------------------------------------------------
# generations
# --------------------------------------------------------------------------

async def create_generation(client_id: int, job_id: str, cover_title: Optional[str] = None) -> int:
    async with _conn() as c:
        cur = await c.execute(
            "INSERT INTO generations (client_id, job_id, cover_title) VALUES (?, ?, ?)",
            (client_id, job_id, cover_title),
        )
        await c.commit()
        return cur.lastrowid


async def get_generation(job_id: str) -> Optional[dict]:
    async with _conn() as c:
        row = await (await c.execute("SELECT * FROM generations WHERE job_id=?", (job_id,))).fetchone()
    return _d(row)


async def update_generation(
    job_id: str,
    status: Optional[str] = None,
    error: Optional[str] = None,
    slide_count: Optional[int] = None,
    cover_title: Optional[str] = None,
    expired: Optional[int] = None,
    completed: bool = False,
) -> None:
    sets, params = [], []
    if status is not None:
        sets.append("status=?"); params.append(status)
    if error is not None:
        sets.append("error=?"); params.append(error)
    if slide_count is not None:
        sets.append("slide_count=?"); params.append(slide_count)
    if cover_title is not None:
        sets.append("cover_title=?"); params.append(cover_title)
    if expired is not None:
        sets.append("expired=?"); params.append(expired)
    if completed:
        sets.append("completed_at=datetime('now')")
    if not sets:
        return
    params.append(job_id)
    async with _conn() as c:
        await c.execute(f"UPDATE generations SET {', '.join(sets)} WHERE job_id=?", params)
        await c.commit()


async def list_generations(client_id: int, limit: int = 30) -> list[dict]:
    async with _conn() as c:
        rows = await (await c.execute(
            "SELECT * FROM generations WHERE client_id=? ORDER BY created_at DESC, id DESC LIMIT ?",
            (client_id, limit),
        )).fetchall()
    return [dict(r) for r in rows]


async def list_all_generations(slug: Optional[str] = None, limit: int = 50) -> list[dict]:
    """Admin cross-tenant listing (MAJOR M3)."""
    q = ("SELECT g.*, c.slug AS slug, c.display_name AS display_name "
         "FROM generations g JOIN clients c ON c.id = g.client_id ")
    params: list[Any] = []
    if slug:
        q += "WHERE c.slug=? "
        params.append(slug)
    q += "ORDER BY g.created_at DESC, g.id DESC LIMIT ?"
    params.append(limit)
    async with _conn() as c:
        rows = await (await c.execute(q, params)).fetchall()
    return [dict(r) for r in rows]


async def reconcile_startup() -> int:
    """BLOCKER B1: flip stale queued/running rows to error on startup. Not re-enqueued."""
    async with _conn() as c:
        cur = await c.execute(
            "UPDATE generations SET status='error', error='Прервано рестартом сервиса' "
            "WHERE status IN ('queued','running')"
        )
        await c.commit()
        return cur.rowcount


# --------------------------------------------------------------------------
# pending_uploads
# --------------------------------------------------------------------------

async def create_pending_upload(upload_id: str, client_id: int) -> None:
    async with _conn() as c:
        await c.execute(
            "INSERT INTO pending_uploads (upload_id, client_id) VALUES (?, ?)",
            (upload_id, client_id),
        )
        await c.commit()


async def get_pending_upload(upload_id: str) -> Optional[dict]:
    async with _conn() as c:
        row = await (await c.execute(
            "SELECT * FROM pending_uploads WHERE upload_id=?", (upload_id,)
        )).fetchone()
    return _d(row)


async def delete_pending_upload(upload_id: str) -> None:
    async with _conn() as c:
        await c.execute("DELETE FROM pending_uploads WHERE upload_id=?", (upload_id,))
        await c.commit()


# --------------------------------------------------------------------------
# activity_log (helper present now; called from Phase 9)
# --------------------------------------------------------------------------

async def log_activity(client_id: int, action: str, job_id: Optional[str] = None,
                       meta: Optional[dict] = None) -> None:
    async with _conn() as c:
        await c.execute(
            "INSERT INTO activity_log (client_id, job_id, action, meta) VALUES (?, ?, ?, ?)",
            (client_id, job_id, action, _json.dumps(meta or {}, ensure_ascii=False)),
        )
        await c.commit()


async def list_activity(client_id: int, limit: int = 50) -> list[dict]:
    async with _conn() as c:
        rows = await (await c.execute(
            "SELECT * FROM activity_log WHERE client_id=? ORDER BY created_at DESC, id DESC LIMIT ?",
            (client_id, limit),
        )).fetchall()
    return [dict(r) for r in rows]
