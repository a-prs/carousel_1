"""templates.py — Jinja2 render of the carousel deck (plan §5b).

design_tokens (per-tenant, from DB) + validated cards -> deck.html. Structure is
fixed (generalized from the confirmed style_test.html); only colors/fonts vary by
tenant. Self-hosted woff2 @font-face rules are injected with absolute file:// URIs
(server-side render only).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATES_DIR = Path(__file__).parent / "templates_html"
FONTS_DIR = TEMPLATES_DIR / "fonts"
FACES_JSON = FONTS_DIR / "faces.json"

_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "j2"]),
)


def _load_faces() -> list[dict]:
    if FACES_JSON.exists():
        return json.loads(FACES_JSON.read_text())
    return []


def render_deck(
    client: dict,
    cover: dict,
    cards: list[dict],
    tokens: dict,
    cover_photo_path: Optional[Path] = None,
) -> str:
    total = 1 + len(cards)
    fonts = _load_faces()
    cover_photo_uri = None
    if cover_photo_path and Path(cover_photo_path).exists():
        cover_photo_uri = Path(cover_photo_path).resolve().as_uri()
    tmpl = _env.get_template("base.html.j2")
    return tmpl.render(
        client=client,
        cover=cover,
        cards=cards,
        tokens=tokens,
        total=total,
        fonts=fonts,
        fonts_dir=FONTS_DIR.resolve().as_uri(),
        cover_photo_uri=cover_photo_uri,
    )


def write_deck(job_dir: Path, client: dict, cover: dict, cards: list[dict],
               tokens: dict, cover_photo_path: Optional[Path] = None) -> Path:
    job_dir = Path(job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    html = render_deck(client, cover, cards, tokens, cover_photo_path)
    deck = job_dir / "deck.html"
    deck.write_text(html, encoding="utf-8")
    return deck
