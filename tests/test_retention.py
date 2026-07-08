"""Phase 11 — retention.py (plan §7 / §11 Phase 11).

Risk under test: files older than TTL get deleted, generations.expired=1 is
set, but generations/activity_log rows themselves are NEVER touched (deleted
or content-modified). A missing-file race or a locked DB write must not crash
the whole run (MAJOR M5) — errors on one job must not stop the others, and a
concurrent run against the same SQLite file (busy_timeout) must not raise.
"""
import os
import time
from pathlib import Path

import pytest

import db
import retention


async def _make_client_and_job(slug="alice"):
    cid = await db.create_client(
        slug=slug, password_hash="x", design_tokens={}, display_name=slug, ig_handle=f"@{slug}",
    )
    job_id = f"{slug}-job"
    await db.create_generation(cid, job_id)
    await db.update_generation(job_id, status="done", slide_count=2, completed=True)
    return cid, job_id


def _write_job_files(job_dir: Path, n: int = 2):
    job_dir.mkdir(parents=True, exist_ok=True)
    for i in range(1, n + 1):
        (job_dir / f"slide_{i:02d}.png").write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(10))


def _backdate(path: Path, hours: float):
    old = time.time() - hours * 3600
    os.utime(path, (old, old))


@pytest.fixture
async def env(fresh_db, tmp_path, monkeypatch):
    monkeypatch.setenv("CAROUSEL_DATA_DIR", str(tmp_path))
    return tmp_path


async def test_deletes_files_older_than_ttl_and_marks_expired(env):
    jobs = retention.jobs_dir()
    cid, job_id = await _make_client_and_job("alice")
    job_dir = jobs / job_id
    _write_job_files(job_dir)
    _backdate(job_dir, hours=100)  # older than default 72h TTL

    summary = await retention.run_once(ttl_hours=72)

    assert summary["cleaned"] == 1
    assert summary["marked_expired"] == 1
    assert list(job_dir.glob("*.png")) == []  # files gone
    g = await db.get_generation(job_id)
    assert g["expired"] == 1
    # history row itself is untouched (still present, same job_id/status)
    assert g["status"] == "done"
    assert g["slide_count"] == 2


async def test_does_not_touch_recent_files(env):
    jobs = retention.jobs_dir()
    cid, job_id = await _make_client_and_job("bob")
    job_dir = jobs / job_id
    _write_job_files(job_dir)
    # freshly created -> mtime is "now", well within TTL

    summary = await retention.run_once(ttl_hours=72)

    assert summary["cleaned"] == 0
    assert summary["marked_expired"] == 0
    assert len(list(job_dir.glob("*.png"))) == 2
    g = await db.get_generation(job_id)
    assert g["expired"] == 0


async def test_activity_log_and_generations_rows_never_deleted(env):
    cid, job_id = await _make_client_and_job("carol")
    await db.log_activity(cid, "generate", job_id=job_id)
    await db.log_activity(cid, "download_slide", job_id=job_id, meta={"slide": 1})
    jobs = retention.jobs_dir()
    job_dir = jobs / job_id
    _write_job_files(job_dir)
    _backdate(job_dir, hours=200)

    before_activity = await db.list_activity(cid, limit=10)
    await retention.run_once(ttl_hours=72)
    after_activity = await db.list_activity(cid, limit=10)

    assert len(after_activity) == len(before_activity) == 2
    g = await db.get_generation(job_id)
    assert g is not None and g["expired"] == 1  # only the flag changed


async def test_orphaned_pending_upload_dir_without_generation_row_is_cleaned(env):
    jobs = retention.jobs_dir()
    upload_dir = jobs / "orphan-upload-id"
    upload_dir.mkdir(parents=True)
    (upload_dir / "cover.jpg").write_bytes(b"\xff\xd8\xff" + bytes(10))
    _backdate(upload_dir, hours=200)

    summary = await retention.run_once(ttl_hours=72)

    assert summary["cleaned"] == 1
    assert summary["marked_expired"] == 0  # no generations row to mark
    assert list(upload_dir.glob("*")) == []


async def test_missing_file_race_does_not_crash(env, monkeypatch):
    """Simulate a file vanishing mid-cleanup (e.g. a concurrent download) — the
    run must swallow FileNotFoundError and keep going, not raise."""
    cid, job_id = await _make_client_and_job("dave")
    jobs = retention.jobs_dir()
    job_dir = jobs / job_id
    _write_job_files(job_dir, n=1)
    _backdate(job_dir, hours=200)

    real_unlink = Path.unlink

    def flaky_unlink(self, *a, **kw):
        if self.name == "slide_01.png":
            raise FileNotFoundError("vanished mid-run")
        return real_unlink(self, *a, **kw)

    monkeypatch.setattr(Path, "unlink", flaky_unlink)

    # must not raise
    summary = await retention.run_once(ttl_hours=72)
    assert summary["errors"] == 0  # FileNotFoundError is treated as "already gone", not an error


async def test_unexpected_delete_error_is_logged_not_fatal_and_other_jobs_still_processed(env, monkeypatch):
    cid1, job1 = await _make_client_and_job("erin")
    cid2, job2 = await _make_client_and_job("frank")
    jobs = retention.jobs_dir()
    dir1, dir2 = jobs / job1, jobs / job2
    _write_job_files(dir1)
    _write_job_files(dir2)
    _backdate(dir1, hours=200)
    _backdate(dir2, hours=200)

    real_unlink = Path.unlink

    def broken_unlink(self, *a, **kw):
        if job1 in str(self):
            raise PermissionError("simulated locked/undeletable file")
        return real_unlink(self, *a, **kw)

    monkeypatch.setattr(Path, "unlink", broken_unlink)

    summary = await retention.run_once(ttl_hours=72)  # must not raise

    assert summary["errors"] >= 1
    # the other tenant's job was still cleaned despite job1's failure
    assert list(dir2.glob("*.png")) == []
    g2 = await db.get_generation(job2)
    assert g2["expired"] == 1


async def test_concurrent_runs_do_not_crash_process(env):
    """Two retention runs racing against the same SQLite file (busy_timeout,
    MAJOR M5) must not raise 'database is locked' or any other exception."""
    import asyncio

    cid, job_id = await _make_client_and_job("grace")
    jobs = retention.jobs_dir()
    job_dir = jobs / job_id
    _write_job_files(job_dir)
    _backdate(job_dir, hours=200)

    results = await asyncio.gather(
        retention.run_once(ttl_hours=72),
        retention.run_once(ttl_hours=72),
        return_exceptions=True,
    )
    for r in results:
        assert not isinstance(r, Exception), r

    g = await db.get_generation(job_id)
    assert g["expired"] == 1
    assert list(job_dir.glob("*.png")) == []
