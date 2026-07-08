#!/usr/bin/env python3
"""render.py — Playwright/Chromium render of deck.html -> per-slide PNGs.

Adapted from agents/ig-carousel/render.py with three fixes required by the plan:
  1. Honest `await document.fonts.ready` (the reference did not await the Promise).
  2. Overflow-guard (BLOCKER B4): measure scrollHeight>clientHeight per slide and
     step --fs-scale down 2-3 steps; log every overflow event (job_id, order).
  3. --no-sandbox + executable_path resolved from the shared Chromium cache.

Blocking sync Playwright API — the caller (pipeline) runs this in asyncio.to_thread
under a global lock (concurrency=1).
"""
from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
from typing import Optional

from playwright.sync_api import sync_playwright

logger = logging.getLogger("carousel.render")

W, H = 1080, 1350
FS_STEPS = [0.92, 0.85, 0.78]  # progressive shrink on overflow


def _find_chrome() -> str:
    base = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/home/office/.cache/ms-playwright")
    matches = sorted(Path(base).glob("chromium-*/chrome-linux*/chrome"))
    if not matches:
        raise RuntimeError(f"No Chromium binary under {base}")
    return str(matches[-1])


def _guard_overflow(slide, order: int, job_id: str) -> None:
    """Shrink font-size on this slide until content fits (or steps exhausted)."""
    overflow = slide.evaluate("el => el.scrollHeight > el.clientHeight + 2")
    step = 0
    while overflow and step < len(FS_STEPS):
        sc = FS_STEPS[step]
        slide.evaluate("(el, sc) => el.style.setProperty('--fs-scale', sc)", sc)
        logger.warning("overflow job=%s card=%s -> fs-scale=%s", job_id, order, sc)
        overflow = slide.evaluate("el => el.scrollHeight > el.clientHeight + 2")
        step += 1
    if overflow:
        logger.error("overflow job=%s card=%s STILL overflowing after shrink", job_id, order)


def render_deck_to_png(
    deck_path: str | Path,
    out_dir: str | Path,
    prefix: str = "slide",
    scale: int = 1,
    job_id: str = "-",
) -> list[Path]:
    deck = Path(deck_path).resolve()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    chrome = _find_chrome()
    paths: list[Path] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=chrome, args=["--no-sandbox"])
        try:
            page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=scale)
            page.goto(deck.as_uri())
            page.wait_for_load_state("networkidle")
            # Honest wait for webfonts (await the Promise, not just touch it).
            try:
                page.evaluate("async () => { await document.fonts.ready; }")
            except Exception:
                pass
            page.wait_for_timeout(400)  # safety settle

            slides = page.query_selector_all(".slide")
            for i, s in enumerate(slides, 1):
                order = s.get_attribute("data-order") or str(i)
                _guard_overflow(s, order, job_id)
                p = out / f"{prefix}_{i:02d}.png"
                s.screenshot(path=str(p))
                paths.append(p)
        finally:
            browser.close()
    return paths


def main():
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser()
    ap.add_argument("deck")
    ap.add_argument("out")
    ap.add_argument("--prefix", default="slide")
    ap.add_argument("--scale", type=int, default=1)
    a = ap.parse_args()
    paths = render_deck_to_png(a.deck, a.out, a.prefix, a.scale)
    print(f"done: {len(paths)} slides in {a.out}")


if __name__ == "__main__":
    main()
