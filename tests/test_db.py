"""Phase 1 — DB schema + CRUD tests (written before db.py)."""
import json
import pytest
import db


@pytest.fixture
async def fresh_db(tmp_path):
    db.init_db(tmp_path / "carousel.db")
    await db.apply_schema()
    return db


async def test_schema_creates_all_tables(fresh_db):
    async with db._conn() as c:
        rows = await (await c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )).fetchall()
    names = {r["name"] for r in rows}
    assert {"clients", "generations", "activity_log", "pending_uploads"} <= names


async def test_pragmas_applied(fresh_db):
    async with db._conn() as c:
        jm = await (await c.execute("PRAGMA journal_mode")).fetchone()
        bt = await (await c.execute("PRAGMA busy_timeout")).fetchone()
    assert jm[0].lower() == "wal"
    assert bt[0] == 5000


async def test_create_and_get_client(fresh_db):
    tokens = {"bg": "#0b1220", "accent": "#e0ac1c"}
    cid = await db.create_client(
        slug="vauforma", display_name="M K", ig_handle="@m_i_h_0",
        password_hash="hash123", design_tokens=tokens,
    )
    assert cid > 0
    c = await db.get_client_by_slug("vauforma")
    assert c["slug"] == "vauforma"
    assert c["ig_handle"] == "@m_i_h_0"
    assert c["token_version"] == 1
    assert json.loads(c["design_tokens"])["accent"] == "#e0ac1c"
    # by id
    c2 = await db.get_client_by_id(cid)
    assert c2["slug"] == "vauforma"


async def test_slug_unique(fresh_db):
    await db.create_client(slug="dup", password_hash="h", design_tokens={})
    with pytest.raises(Exception):
        await db.create_client(slug="dup", password_hash="h2", design_tokens={})


async def test_set_theme(fresh_db):
    await db.create_client(slug="v", password_hash="h", design_tokens={"bg": "#000"})
    await db.set_theme("v", {"bg": "#fff", "accent": "#f00"})
    c = await db.get_client_by_slug("v")
    assert json.loads(c["design_tokens"])["bg"] == "#fff"


async def test_set_password_bumps_token_version(fresh_db):
    await db.create_client(slug="v", password_hash="h", design_tokens={})
    c1 = await db.get_client_by_slug("v")
    assert c1["token_version"] == 1
    await db.set_password("v", "newhash")
    c2 = await db.get_client_by_slug("v")
    assert c2["password_hash"] == "newhash"
    assert c2["token_version"] == 2


async def test_generation_lifecycle(fresh_db):
    cid = await db.create_client(slug="v", password_hash="h", design_tokens={})
    gid = await db.create_generation(cid, "job-abc", cover_title="GLP-1: Друг")
    assert gid > 0
    g = await db.get_generation("job-abc")
    assert g["status"] == "queued"
    assert g["client_id"] == cid
    assert g["cover_title"] == "GLP-1: Друг"
    await db.update_generation("job-abc", status="running")
    assert (await db.get_generation("job-abc"))["status"] == "running"
    await db.update_generation("job-abc", status="done", slide_count=5, completed=True)
    g2 = await db.get_generation("job-abc")
    assert g2["status"] == "done"
    assert g2["slide_count"] == 5
    assert g2["completed_at"] is not None


async def test_list_generations_scoped_to_client(fresh_db):
    a = await db.create_client(slug="a", password_hash="h", design_tokens={})
    b = await db.create_client(slug="b", password_hash="h", design_tokens={})
    await db.create_generation(a, "ja1")
    await db.create_generation(a, "ja2")
    await db.create_generation(b, "jb1")
    la = await db.list_generations(a, limit=10)
    assert {g["job_id"] for g in la} == {"ja1", "ja2"}
    lb = await db.list_generations(b, limit=10)
    assert {g["job_id"] for g in lb} == {"jb1"}


async def test_pending_upload_ownership(fresh_db):
    a = await db.create_client(slug="a", password_hash="h", design_tokens={})
    await db.create_pending_upload("up-1", a)
    pu = await db.get_pending_upload("up-1")
    assert pu["client_id"] == a
    assert await db.get_pending_upload("nope") is None


async def test_startup_reconciliation(fresh_db):
    cid = await db.create_client(slug="v", password_hash="h", design_tokens={})
    await db.create_generation(cid, "q1")  # queued
    await db.create_generation(cid, "r1")
    await db.update_generation("r1", status="running")
    await db.create_generation(cid, "d1")
    await db.update_generation("d1", status="done", completed=True)
    n = await db.reconcile_startup()
    assert n == 2
    assert (await db.get_generation("q1"))["status"] == "error"
    assert (await db.get_generation("r1"))["status"] == "error"
    assert "Прервано" in (await db.get_generation("q1"))["error"]
    assert (await db.get_generation("d1"))["status"] == "done"  # untouched


async def test_list_all_generations_admin(fresh_db):
    a = await db.create_client(slug="a", password_hash="h", design_tokens={})
    b = await db.create_client(slug="b", password_hash="h", design_tokens={})
    await db.create_generation(a, "ja1", cover_title="A title")
    await db.create_generation(b, "jb1", cover_title="B title")
    allg = await db.list_all_generations(limit=10)
    assert len(allg) == 2
    assert all("slug" in g for g in allg)  # joined client slug
    onlyb = await db.list_all_generations(slug="b", limit=10)
    assert len(onlyb) == 1 and onlyb[0]["slug"] == "b"
