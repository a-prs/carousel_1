"""Phase 3 — render: mock cards -> PNG 1080x1350; overflow-guard triggers + logs."""
import logging
from pathlib import Path

from PIL import Image

import render
import templates

TOKENS = {
    "bg": "#0b1220", "bg2": "#111b30", "text": "#f5f7fa",
    "text_secondary": "rgba(245,247,250,0.66)", "text_faint": "rgba(245,247,250,0.5)",
    "accent": "#e0ac1c", "accent_text": "#0b1220", "secondary": "#1e3a8a",
    "font_head": "Fira Sans Extra Condensed", "font_head_alt": "Fira Sans Condensed",
    "font_body": "PT Sans", "swipe_label": "листай",
}
CLIENT = {"slug": "vauforma", "ig_handle": "@m_i_h_0"}
COVER = {"plate": "GLP-1:", "title": "Друг или враг"}
CARDS = [
    {"order": 2, "type": "compare", "sides": [
        {"marker": "no", "title": "Враг — если ты:",
         "items": [{"num": 1, "sub": "Сам себе доктор",
                    "desc": "Используешь препараты GLP-1 без консультации с врачом."}]},
        {"marker": "yes", "title": "Друг — если ты:",
         "items": [{"num": 1, "sub": "Болен, а не ленив",
                    "desc": "Официально диагностировано ожирение."}]},
    ]},
    {"order": 3, "type": "summary", "title": "Итог", "body": "Короткий вывод.",
     "cta": {"present": False, "word": None, "text": None}},
]


def test_render_produces_correct_size_pngs(tmp_path):
    deck = templates.write_deck(tmp_path, CLIENT, COVER, CARDS, TOKENS)
    pngs = render.render_deck_to_png(deck, tmp_path / "out", job_id="t1")
    assert len(pngs) == 3  # cover + 2 cards
    for p in pngs:
        assert p.exists()
        w, h = Image.open(p).size
        assert (w, h) == (1080, 1350)


def test_overflow_guard_triggers_and_logs(tmp_path, caplog):
    long_desc = "Очень длинное описание. " * 60
    huge = [{"order": 2, "type": "content", "kicker": "Много текста",
             "title": "Перегруженная карточка",
             "marker": None,
             "items": [{"num": i, "sub": f"Пункт {i}", "desc": long_desc} for i in range(1, 6)]}]
    deck = templates.write_deck(tmp_path, CLIENT, COVER, huge, TOKENS)
    with caplog.at_level(logging.WARNING, logger="carousel.render"):
        render.render_deck_to_png(deck, tmp_path / "out", job_id="overflow-job")
    msgs = [r.getMessage() for r in caplog.records]
    assert any("overflow job=overflow-job" in m for m in msgs), msgs
