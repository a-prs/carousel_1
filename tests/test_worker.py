"""Phase 5 — worker + pipeline: end-to-end on mocks, crash isolation, reconcile."""
import asyncio
import json

import pytest

import db
import llm
import pipeline
import worker


async def _make_client(monkeypatch, tmp_path):
    monkeypatch.setenv("CAROUSEL_DATA_DIR", str(tmp_path))
    db.init_db(tmp_path / "carousel.db")
    await db.apply_schema()
    cid = await db.create_client(
        slug="v", password_hash="h",
        design_tokens={"bg": "#0b1220", "text": "#fff", "accent": "#e0ac1c",
                       "secondary": "#1e3a8a", "font_head": "Fira Sans Extra Condensed",
                       "font_head_alt": "Fira Sans Condensed", "font_body": "PT Sans"},
        ig_handle="@v",
    )
    return await db.get_client_by_id(cid)


def _ok_segmenter(text, ig_handle):
    return llm.SegmentResult(
        ok=True,
        cover={"plate": "P", "title": "Заголовок"},
        cards=[{"order": 2, "type": "summary", "title": "Итог", "body": "b",
                "cta": {"present": False}}],
        cover_title="P Заголовок",
    )


def _fake_renderer(deck, out_dir, prefix, scale, job_id):
    from pathlib import Path
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for i in (1, 2):
        p = out / f"{prefix}_{i:02d}.png"
        p.write_bytes(b"\x89PNG\r\n")
        paths.append(p)
    return paths


async def test_job_flows_through_queue(monkeypatch, tmp_path):
    client = await _make_client(monkeypatch, tmp_path)
    await db.create_generation(client["id"], "job1")
    w = worker.Worker(segmenter=_ok_segmenter, renderer=_fake_renderer)
    w.start()
    await w.enqueue({"job_id": "job1", "client": client, "text": "hello"})
    await asyncio.wait_for(w.queue.join(), timeout=10)
    await w.stop()
    g = await db.get_generation("job1")
    assert g["status"] == "done"
    assert g["slide_count"] == 2
    assert g["cover_title"] == "P Заголовок"
    assert g["completed_at"] is not None


async def test_one_job_crash_does_not_kill_loop(monkeypatch, tmp_path):
    client = await _make_client(monkeypatch, tmp_path)
    await db.create_generation(client["id"], "bad")
    await db.create_generation(client["id"], "good")

    def crash_renderer(deck, out_dir, prefix, scale, job_id):
        raise RuntimeError("chromium exploded")

    def selective_renderer(deck, out_dir, prefix, scale, job_id):
        if job_id == "bad":
            raise RuntimeError("boom")
        return _fake_renderer(deck, out_dir, prefix, scale, job_id)

    w = worker.Worker(segmenter=_ok_segmenter, renderer=selective_renderer)
    w.start()
    await w.enqueue({"job_id": "bad", "client": client, "text": "x"})
    await w.enqueue({"job_id": "good", "client": client, "text": "y"})
    await asyncio.wait_for(w.queue.join(), timeout=10)
    await w.stop()
    bad = await db.get_generation("bad")
    good = await db.get_generation("good")
    assert bad["status"] == "error"
    assert good["status"] == "done"  # loop survived the first crash


async def test_llm_failure_marks_error_with_client_message(monkeypatch, tmp_path):
    client = await _make_client(monkeypatch, tmp_path)
    await db.create_generation(client["id"], "jf")

    def failing_segmenter(text, ig_handle):
        return llm.SegmentResult(ok=False, error=llm.CLIENT_ERROR)

    w = worker.Worker(segmenter=failing_segmenter, renderer=_fake_renderer)
    w.start()
    await w.enqueue({"job_id": "jf", "client": client, "text": "x"})
    await asyncio.wait_for(w.queue.join(), timeout=10)
    await w.stop()
    g = await db.get_generation("jf")
    assert g["status"] == "error"
    assert g["error"] == llm.CLIENT_ERROR


async def test_startup_reconciliation(monkeypatch, tmp_path):
    client = await _make_client(monkeypatch, tmp_path)
    await db.create_generation(client["id"], "stuck_q")
    await db.create_generation(client["id"], "stuck_r")
    await db.update_generation("stuck_r", status="running")
    n = await db.reconcile_startup()
    assert n == 2
    assert (await db.get_generation("stuck_q"))["status"] == "error"
    assert (await db.get_generation("stuck_r"))["status"] == "error"
