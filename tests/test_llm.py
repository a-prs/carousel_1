"""Phase 4 — llm parse/validate/repair + no-crash-on-failure."""
import json
import subprocess

import llm


def _valid_payload():
    return {
        "cover": {"plate": "GLP-1:", "title": "Друг или враг"},
        "cards": [
            {"order": 2, "type": "content", "kicker": "Что", "title": "Заголовок",
             "marker": "neutral",
             "items": [{"num": 1, "sub": "Под", "desc": "Раскрытие."}]},
            {"order": 3, "type": "summary", "title": "Итог", "body": "Вывод.",
             "cta": {"present": False, "word": None, "text": None}},
        ],
    }


# ---- parsing ----

def test_parse_plain_json():
    d = llm.parse_llm_json(json.dumps(_valid_payload()))
    assert d["cover"]["title"] == "Друг или враг"


def test_parse_markdown_fenced():
    raw = "```json\n" + json.dumps(_valid_payload()) + "\n```"
    d = llm.parse_llm_json(raw)
    assert "cover" in d


def test_parse_with_prose_around_json():
    raw = "Вот результат:\n" + json.dumps(_valid_payload()) + "\nГотово."
    d = llm.parse_llm_json(raw)
    assert "cards" in d


def test_parse_broken_raises():
    import pytest
    with pytest.raises((ValueError, json.JSONDecodeError)):
        llm.parse_llm_json("{ not json at all ")
    with pytest.raises(ValueError):
        llm.parse_llm_json("")


# ---- validate/repair ----

def test_validate_happy_path():
    cover, cards, title = llm.validate_and_repair(_valid_payload())
    assert cover["title"] == "Друг или враг"
    assert title == "GLP-1: Друг или враг"
    assert cards[-1]["type"] == "summary"
    assert cards[0]["order"] == 2


def test_clamp_to_19_cards():
    payload = {"cover": {"title": "T"},
               "cards": [{"type": "content", "title": f"C{i}",
                          "items": [{"sub": "s", "desc": "d"}]} for i in range(30)]}
    _, cards, _ = llm.validate_and_repair(payload)
    assert len(cards) <= 19


def test_exactly_one_summary_and_last():
    payload = {"cover": {"title": "T"}, "cards": [
        {"type": "summary", "title": "S1", "body": "b", "cta": {"present": False}},
        {"type": "content", "title": "C", "items": [{"sub": "s", "desc": "d"}]},
        {"type": "summary", "title": "S2", "body": "b2", "cta": {"present": False}},
    ]}
    _, cards, _ = llm.validate_and_repair(payload)
    summaries = [c for c in cards if c["type"] == "summary"]
    assert len(summaries) == 1
    assert cards[-1]["type"] == "summary"
    assert cards[-1]["title"] == "S2"  # last summary kept


def test_bad_card_dropped_not_whole_response():
    payload = {"cover": {"title": "T"}, "cards": [
        {"type": "content", "title": "Good", "items": [{"sub": "s", "desc": "d"}]},
        {"type": "bogus_type", "whatever": 1},
        {"type": "content"},  # empty -> dropped
    ]}
    _, cards, _ = llm.validate_and_repair(payload)
    titles = [c.get("title") for c in cards]
    assert "Good" in titles
    assert all(c["type"] in ("content", "compare", "summary") for c in cards)


def test_desc_trimmed_and_items_clamped():
    long = "x" * 300
    payload = {"cover": {"title": "T"}, "cards": [
        {"type": "content", "title": "C",
         "items": [{"sub": "s" * 100, "desc": long} for _ in range(6)]},
    ]}
    _, cards, _ = llm.validate_and_repair(payload)
    card = cards[0]
    assert len(card["items"]) <= 3
    assert len(card["items"][0]["desc"]) <= llm.DESC_MAX + 1  # +1 for the …
    assert len(card["items"][0]["sub"]) <= llm.SUB_MAX + 1


def test_cta_not_fabricated_without_text():
    payload = {"cover": {"title": "T"}, "cards": [
        {"type": "summary", "title": "Итог", "body": "b",
         "cta": {"present": True, "word": None, "text": None}},  # present but no text
    ]}
    _, cards, _ = llm.validate_and_repair(payload)
    assert cards[-1]["cta"]["present"] is False


def test_marker_whitelist():
    payload = {"cover": {"title": "T"}, "cards": [
        {"type": "content", "title": "C", "marker": "purple",
         "items": [{"sub": "s", "desc": "d"}]}]}
    _, cards, _ = llm.validate_and_repair(payload)
    assert cards[0]["marker"] is None


# ---- segment_text: failure never crashes ----

def test_segment_success_with_injected_runner():
    runner = lambda inp: json.dumps(_valid_payload())
    r = llm.segment_text("some text", "@h", runner=runner)
    assert r.ok
    assert r.cover_title == "GLP-1: Друг или враг"


def test_segment_broken_json_triggers_one_retry():
    calls = {"n": 0}
    def runner(inp):
        calls["n"] += 1
        if calls["n"] == 1:
            return "totally broken {{{"
        return json.dumps(_valid_payload())
    r = llm.segment_text("t", "@h", runner=runner)
    assert r.ok
    assert calls["n"] == 2  # retried exactly once


def test_segment_timeout_returns_typed_error_not_crash():
    def runner(inp):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=1)
    r = llm.segment_text("t", "@h", runner=runner)
    assert r.ok is False
    assert r.error == llm.CLIENT_ERROR


def test_segment_subprocess_failure_returns_typed_error():
    def runner(inp):
        raise llm.LLMError("claude exit 1")
    r = llm.segment_text("t", "@h", runner=runner)
    assert r.ok is False
    assert r.error == llm.CLIENT_ERROR


def test_segment_unparseable_after_retry_returns_error():
    r = llm.segment_text("t", "@h", runner=lambda inp: "no json here")
    assert r.ok is False
