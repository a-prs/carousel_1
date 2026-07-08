"""Phase 3 — templates: deck HTML contains expected blocks per card type."""
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
    {"order": 2, "type": "content", "kicker": "Что это", "title": "Заголовок",
     "marker": "neutral",
     "items": [{"num": 1, "sub": "Подзаголовок", "desc": "Раскрытие мысли."}]},
    {"order": 3, "type": "compare", "sides": [
        {"marker": "no", "title": "Враг — если ты:",
         "items": [{"num": 1, "sub": "Сам себе доктор", "desc": "Без врача."}]},
        {"marker": "yes", "title": "Друг — если ты:",
         "items": [{"num": 1, "sub": "Болен, а не ленив", "desc": "С врачом."}]},
    ]},
    {"order": 4, "type": "summary", "title": "Итог", "body": "Короткий вывод.",
     "cta": {"present": True, "word": "GLP", "text": "Пиши слово"}},
]


def test_deck_contains_cover_and_all_cards():
    html = templates.render_deck(CLIENT, COVER, CARDS, TOKENS)
    # cover
    assert "GLP-1:" in html
    assert "Друг или враг" in html
    assert '@m_i_h_0' in html
    # content
    assert "Заголовок" in html
    assert "Подзаголовок" in html
    # compare
    assert "Враг — если ты:" in html
    assert "Друг — если ты:" in html
    assert 'class="mark no"' in html
    assert 'class="mark yes"' in html
    # summary + cta
    assert "Короткий вывод." in html
    assert "Пиши слово" in html
    # pagination: total = 1 cover + 3 cards = 4
    assert "/ 04" in html
    # 4 slide sections
    assert html.count('class="slide') == 4


def test_fonts_selfhosted_no_google_link():
    html = templates.render_deck(CLIENT, COVER, CARDS, TOKENS)
    assert "fonts.googleapis.com" not in html
    assert "@font-face" in html
    assert "Fira Sans Extra Condensed" in html


def test_summary_without_cta_omits_badge():
    cards = [{"order": 2, "type": "summary", "title": "Итог", "body": "Вывод.",
              "cta": {"present": False, "word": None, "text": None}}]
    html = templates.render_deck(CLIENT, COVER, cards, TOKENS)
    assert "Вывод." in html
    assert 'class="cta"' not in html


def test_fs_scale_hook_present():
    # overflow-guard hooks --fs-scale; the CSS must reference it.
    html = templates.render_deck(CLIENT, COVER, CARDS, TOKENS)
    assert "--fs-scale" in html
