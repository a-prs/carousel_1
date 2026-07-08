"""pipeline.py — orchestrate llm -> templates -> render for one job (plan §5).

Playwright is serialized under a global asyncio.Lock (concurrency=1); blocking
work runs in threads via asyncio.to_thread. Raises PipelineError (client-facing
message) on any failure so the worker can mark the generation 'error' without
killing the queue loop.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Callable, Optional

import db
import llm
import render as render_mod
import templates

logger = logging.getLogger("carousel.pipeline")

GENERIC_ERROR = "Не удалось сгенерировать карусель, попробуйте позже"


def _data_dir() -> Path:
    return Path(os.environ.get("CAROUSEL_DATA_DIR", "/home/office/multi2/data/carousel-studio"))


def jobs_dir() -> Path:
    return _data_dir() / "jobs"


# One global render lock — Chromium is heavy and concurrent renders are unstable.
_render_lock = asyncio.Lock()


class PipelineError(Exception):
    def __init__(self, client_message: str):
        super().__init__(client_message)
        self.client_message = client_message


def _default_segmenter(text: str, ig_handle: str) -> "llm.SegmentResult":
    return llm.segment_text(text, ig_handle, model=os.environ.get("MODEL"))


async def process_job(
    job: dict,
    segmenter: Optional[Callable] = None,
    renderer: Optional[Callable] = None,
) -> list[Path]:
    """job = {job_id, client(dict), text, cover_path(optional Path)}."""
    segmenter = segmenter or _default_segmenter
    renderer = renderer or render_mod.render_deck_to_png

    job_id = job["job_id"]
    client = job["client"]
    text = job["text"]
    cover_path = job.get("cover_path")
    tokens = client.get("design_tokens", {})
    if isinstance(tokens, str):
        tokens = json.loads(tokens or "{}")

    await db.update_generation(job_id, status="running")

    # 1. LLM segmentation (blocking subprocess -> thread)
    seg = await asyncio.to_thread(segmenter, text, client.get("ig_handle") or "")
    if not seg.ok:
        raise PipelineError(seg.error or GENERIC_ERROR)

    # 2. Render deck HTML
    job_dir = jobs_dir() / job_id
    deck = templates.write_deck(job_dir, client, seg.cover, seg.cards, tokens, cover_path)

    # 3. Playwright render (serialized)
    async with _render_lock:
        slides = await asyncio.to_thread(renderer, deck, job_dir, "slide", 1, job_id)

    await db.update_generation(
        job_id, status="done", slide_count=len(slides),
        cover_title=seg.cover_title, completed=True,
    )
    logger.info("job %s done: %d slides", job_id, len(slides))
    return slides
