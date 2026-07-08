"""Phase 6 — API endpoints. Emphasis: tenant isolation, upload ownership (B5),
path-traversal on slide-n, 404-not-403 on cross-tenant access.

Phase 9 — zip endpoint + activity_log wiring (generate/regenerate,
download_slide, download_zip) is tested at the bottom of this file, reusing
the same `api` fixture/tenants."""
import asyncio
import io
import zipfile

import httpx
import pytest
from PIL import Image

import auth
import db
import llm


def _ok_segmenter(text, ig_handle):
    return llm.SegmentResult(
        ok=True, cover={"plate": "P", "title": "Заголовок"},
        cards=[{"order": 2, "type": "summary", "title": "Итог", "body": "b",
                "cta": {"present": False}}],
        cover_title="P Заголовок")


def _fake_renderer(deck, out_dir, prefix, scale, job_id):
    from pathlib import Path
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    paths = []
    for i in (1, 2, 3):
        p = out / f"{prefix}_{i:02d}.png"
        p.write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(20))
        paths.append(p)
    return paths


def _png_bytes():
    img = Image.new("RGB", (1200, 1600), (10, 18, 32))
    b = io.BytesIO(); img.save(b, "PNG"); return b.getvalue()


@pytest.fixture
async def api(tmp_path, monkeypatch):
    monkeypatch.setenv("CAROUSEL_DATA_DIR", str(tmp_path))
    import app as app_module
    db.init_db(tmp_path / "carousel.db")
    await app_module.init_state(segmenter=_ok_segmenter, renderer=_fake_renderer)
    # two tenants
    for slug in ("alice", "bob"):
        await db.create_client(slug=slug, password_hash=auth.hash_password(f"pw-{slug}"),
                               design_tokens={"bg": "#0b1220", "text": "#fff", "accent": "#e0ac1c",
                                              "secondary": "#1e3a8a", "font_head": "Fira Sans Extra Condensed",
                                              "font_head_alt": "Fira Sans Condensed", "font_body": "PT Sans"},
                               ig_handle=f"@{slug}")
    transport = httpx.ASGITransport(app=app_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, app_module
    await app_module.WORKER.stop()


async def _login(c, slug):
    r = await c.post("/carousel/api/auth", json={"username": slug, "password": f"pw-{slug}"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


async def _wait_done(c, hdr, app_module, job_id):
    await asyncio.wait_for(app_module.WORKER.queue.join(), timeout=15)
    r = await c.get(f"/carousel/api/jobs/{job_id}", headers=hdr)
    return r.json()


# ---- auth ----

async def test_login_ok_and_bad(api):
    c, _ = api
    good = await c.post("/carousel/api/auth", json={"username": "alice", "password": "pw-alice"})
    assert good.status_code == 200 and "token" in good.json()
    bad = await c.post("/carousel/api/auth", json={"username": "alice", "password": "nope"})
    assert bad.status_code == 401


async def test_me_requires_auth(api):
    c, _ = api
    assert (await c.get("/carousel/api/me")).status_code == 401
    hdr = await _login(c, "alice")
    r = await c.get("/carousel/api/me", headers=hdr)
    assert r.json()["slug"] == "alice"
    assert r.json()["ig_handle"] == "@alice"


async def test_health(api):
    c, _ = api
    r = await c.get("/carousel/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


# ---- upload + generate happy path ----

async def test_upload_and_generate_flow(api):
    c, app_module = api
    hdr = await _login(c, "alice")
    up = await c.post("/carousel/api/upload-cover", headers=hdr,
                      files={"file": ("c.png", _png_bytes(), "image/png")})
    assert up.status_code == 200
    upload_id = up.json()["upload_id"]
    gen = await c.post("/carousel/api/generate", headers=hdr,
                       json={"text": "GLP-1 друг или враг", "cover_upload_id": upload_id})
    assert gen.status_code == 200
    job_id = gen.json()["job_id"]
    data = await _wait_done(c, hdr, app_module, job_id)
    assert data["status"] == "done"
    assert data["slide_count"] == 3
    assert len(data["slides"]) == 3
    # download a slide (owner)
    dl = await c.get(f"/carousel/api/jobs/{job_id}/slide/1.png", headers=hdr)
    assert dl.status_code == 200
    assert dl.headers["content-type"] == "image/png"
    assert "attachment" in dl.headers["content-disposition"]


async def test_upload_rejects_non_image(api):
    c, _ = api
    hdr = await _login(c, "alice")
    r = await c.post("/carousel/api/upload-cover", headers=hdr,
                     files={"file": ("x.txt", b"hello", "text/plain")})
    assert r.status_code == 415


# ---- B5: cross-tenant upload ownership ----

async def test_generate_rejects_foreign_upload(api):
    c, _ = api
    ha = await _login(c, "alice")
    hb = await _login(c, "bob")
    up = await c.post("/carousel/api/upload-cover", headers=ha,
                      files={"file": ("c.png", _png_bytes(), "image/png")})
    alice_upload = up.json()["upload_id"]
    # bob tries to generate with alice's upload_id
    r = await c.post("/carousel/api/generate", headers=hb,
                     json={"text": "steal", "cover_upload_id": alice_upload})
    assert r.status_code == 404  # not found for bob


async def test_upload_preview_ownership(api):
    c, _ = api
    ha = await _login(c, "alice")
    hb = await _login(c, "bob")
    up = await c.post("/carousel/api/upload-cover", headers=ha,
                      files={"file": ("c.png", _png_bytes(), "image/png")})
    uid = up.json()["upload_id"]
    assert (await c.get(f"/carousel/api/uploads/{uid}/cover.jpg", headers=ha)).status_code == 200
    assert (await c.get(f"/carousel/api/uploads/{uid}/cover.jpg", headers=hb)).status_code == 404


# ---- cross-tenant job isolation ----

async def test_tenant_cannot_see_or_download_other_job(api):
    c, app_module = api
    ha = await _login(c, "alice")
    hb = await _login(c, "bob")
    gen = await c.post("/carousel/api/generate", headers=ha, json={"text": "alice text"})
    job_id = gen.json()["job_id"]
    await _wait_done(c, ha, app_module, job_id)
    # bob polling alice's job -> 404 (not 403)
    assert (await c.get(f"/carousel/api/jobs/{job_id}", headers=hb)).status_code == 404
    # bob downloading alice's slide -> 404
    assert (await c.get(f"/carousel/api/jobs/{job_id}/slide/1.png", headers=hb)).status_code == 404
    # alice still can
    assert (await c.get(f"/carousel/api/jobs/{job_id}/slide/1.png", headers=ha)).status_code == 200


async def test_slide_n_path_traversal_and_bounds(api):
    c, app_module = api
    hdr = await _login(c, "alice")
    gen = await c.post("/carousel/api/generate", headers=hdr, json={"text": "x"})
    job_id = gen.json()["job_id"]
    await _wait_done(c, hdr, app_module, job_id)
    # non-int n -> 422 (route type validation)
    assert (await c.get(f"/carousel/api/jobs/{job_id}/slide/abc.png", headers=hdr)).status_code == 422
    # traversal attempt -> 422/404, never serves another file
    trav = await c.get(f"/carousel/api/jobs/{job_id}/slide/..%2f..%2fcover.png", headers=hdr)
    assert trav.status_code in (404, 422)
    # out-of-range n -> 404
    assert (await c.get(f"/carousel/api/jobs/{job_id}/slide/99.png", headers=hdr)).status_code == 404


async def test_history_scoped_to_tenant(api):
    c, app_module = api
    ha = await _login(c, "alice")
    hb = await _login(c, "bob")
    g1 = await c.post("/carousel/api/generate", headers=ha, json={"text": "a1"})
    await _wait_done(c, ha, app_module, g1.json()["job_id"])
    g2 = await c.post("/carousel/api/generate", headers=hb, json={"text": "b1"})
    await _wait_done(c, hb, app_module, g2.json()["job_id"])
    ha_hist = (await c.get("/carousel/api/history", headers=ha)).json()["items"]
    hb_hist = (await c.get("/carousel/api/history", headers=hb)).json()["items"]
    assert len(ha_hist) == 1 and ha_hist[0]["job_id"] == g1.json()["job_id"]
    assert len(hb_hist) == 1 and hb_hist[0]["job_id"] == g2.json()["job_id"]


# ---- Phase 9: zip endpoint ----

async def test_zip_contains_all_slides_and_is_owner_scoped(api):
    c, app_module = api
    ha = await _login(c, "alice")
    hb = await _login(c, "bob")
    gen = await c.post("/carousel/api/generate", headers=ha, json={"text": "alice zip"})
    job_id = gen.json()["job_id"]
    await _wait_done(c, ha, app_module, job_id)

    r = await c.get(f"/carousel/api/jobs/{job_id}/zip", headers=ha)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert "attachment" in r.headers["content-disposition"]
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    assert sorted(zf.namelist()) == ["slide_01.png", "slide_02.png", "slide_03.png"]

    # cross-tenant: bob can't fetch alice's zip (404, not 403 — B5-style isolation)
    assert (await c.get(f"/carousel/api/jobs/{job_id}/zip", headers=hb)).status_code == 404


async def test_slide_expired_returns_410(api):
    c, app_module = api
    hdr = await _login(c, "alice")
    gen = await c.post("/carousel/api/generate", headers=hdr, json={"text": "x"})
    job_id = gen.json()["job_id"]
    await _wait_done(c, hdr, app_module, job_id)
    await db.update_generation(job_id, expired=1)
    r = await c.get(f"/carousel/api/jobs/{job_id}/slide/1.png", headers=hdr)
    assert r.status_code == 410


async def test_slide_missing_file_returns_410_even_if_not_flagged_expired(api):
    """Retention deletes files and sets expired=1 together, but the download
    endpoint must also degrade gracefully if files are gone for any other
    reason (defense in depth, plan §7/§11)."""
    c, app_module = api
    hdr = await _login(c, "alice")
    gen = await c.post("/carousel/api/generate", headers=hdr, json={"text": "x"})
    job_id = gen.json()["job_id"]
    await _wait_done(c, hdr, app_module, job_id)
    (app_module.jobs_dir() / job_id / "slide_01.png").unlink()
    r = await c.get(f"/carousel/api/jobs/{job_id}/slide/1.png", headers=hdr)
    assert r.status_code == 410


async def test_zip_expired_returns_410(api):
    c, app_module = api
    hdr = await _login(c, "alice")
    gen = await c.post("/carousel/api/generate", headers=hdr, json={"text": "x"})
    job_id = gen.json()["job_id"]
    await _wait_done(c, hdr, app_module, job_id)
    await db.update_generation(job_id, expired=1)
    r = await c.get(f"/carousel/api/jobs/{job_id}/zip", headers=hdr)
    assert r.status_code == 410


async def test_zip_not_ready_returns_404(api):
    c, app_module = api
    hdr = await _login(c, "alice")
    client_row = await db.get_client_by_slug("alice")
    await db.create_generation(client_row["id"], "not-ready-job")  # still status=queued
    r = await c.get("/carousel/api/jobs/not-ready-job/zip", headers=hdr)
    assert r.status_code == 404


# ---- Phase 9: activity_log wiring ----

async def test_activity_log_records_generate_slide_and_zip_downloads(api):
    c, app_module = api
    hdr = await _login(c, "alice")
    client_row = await db.get_client_by_slug("alice")

    gen = await c.post("/carousel/api/generate", headers=hdr, json={"text": "log me"})
    job_id = gen.json()["job_id"]
    await _wait_done(c, hdr, app_module, job_id)

    assert (await c.get(f"/carousel/api/jobs/{job_id}/slide/1.png", headers=hdr)).status_code == 200
    assert (await c.get(f"/carousel/api/jobs/{job_id}/zip", headers=hdr)).status_code == 200

    rows = await db.list_activity(client_row["id"], limit=10)
    actions = [r["action"] for r in reversed(rows)]  # list_activity is DESC, chronological order
    assert actions == ["generate", "download_slide", "download_zip"]
    assert all(r["job_id"] == job_id for r in rows)
    slide_row = next(r for r in rows if r["action"] == "download_slide")
    assert '"slide": 1' in slide_row["meta"] or '"slide":1' in slide_row["meta"]


async def test_activity_log_second_generation_is_regenerate(api):
    c, app_module = api
    hdr = await _login(c, "alice")
    client_row = await db.get_client_by_slug("alice")

    g1 = await c.post("/carousel/api/generate", headers=hdr, json={"text": "one"})
    await _wait_done(c, hdr, app_module, g1.json()["job_id"])
    g2 = await c.post("/carousel/api/generate", headers=hdr, json={"text": "two"})
    await _wait_done(c, hdr, app_module, g2.json()["job_id"])

    rows = await db.list_activity(client_row["id"], limit=10)
    gen_actions = [r["action"] for r in reversed(rows) if r["action"] in ("generate", "regenerate")]
    assert gen_actions == ["generate", "regenerate"]


async def test_activity_log_scoped_per_tenant(api):
    c, app_module = api
    ha = await _login(c, "alice")
    hb = await _login(c, "bob")
    alice_row = await db.get_client_by_slug("alice")
    bob_row = await db.get_client_by_slug("bob")

    ga = await c.post("/carousel/api/generate", headers=ha, json={"text": "a"})
    await _wait_done(c, ha, app_module, ga.json()["job_id"])
    gb = await c.post("/carousel/api/generate", headers=hb, json={"text": "b"})
    await _wait_done(c, hb, app_module, gb.json()["job_id"])

    alice_activity = await db.list_activity(alice_row["id"])
    bob_activity = await db.list_activity(bob_row["id"])
    assert all(r["job_id"] == ga.json()["job_id"] for r in alice_activity)
    assert all(r["job_id"] == gb.json()["job_id"] for r in bob_activity)
