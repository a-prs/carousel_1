"""llm.py — claude -p wrapper + JSON parse/validate/repair (plan §5a).

BLOCKER B3: unlike the meta-content reference this NEVER raises SystemExit or lets
a subprocess failure escape uncaught. `segment_text` always returns a typed
`SegmentResult` (ok / error). The worker turns ok=False into generations.status
='error' with a client-facing message.

BLOCKER B4: overflow is fought in three places — the prompt's hard field limits,
per-field trim-with-priority here (desc first, then extra items), and the render
overflow-guard. Per-card schema validation repairs/drops a single bad card rather
than throwing away the whole (possibly partially valid) response.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

PROMPT_PATH = Path(__file__).parent / "prompts" / "segment_system.md"

TITLE_MAX = 30
KICKER_MAX = 24
SUB_MAX = 40
DESC_MAX = 90
BODY_MAX = 220
ITEMS_MAX = 3
CARDS_MAX = 19  # + cover = 20 slides max
MARKERS = {"yes", "no", "neutral"}

CLIENT_ERROR = "Сервис ИИ временно недоступен, попробуйте позже"


class LLMError(Exception):
    pass


@dataclass
class SegmentResult:
    ok: bool
    cover: dict = field(default_factory=dict)
    cards: list = field(default_factory=list)
    cover_title: str = ""
    error: Optional[str] = None


def _load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------

def parse_llm_json(raw: str) -> dict:
    """Strip ```-fences then json.loads; fall back to first {...} block."""
    if not raw or not raw.strip():
        raise ValueError("empty output")
    s = raw.strip()
    s = re.sub(r"^```(?:json)?\s*\n?", "", s)
    s = re.sub(r"\n?```\s*$", "", s)
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", s, re.DOTALL)
        if m:
            return json.loads(m.group(0))  # may still raise -> caller catches
        raise ValueError("no JSON object found")


# --------------------------------------------------------------------------
# validation / repair
# --------------------------------------------------------------------------

def _trim(s, n: int) -> str:
    if not isinstance(s, str):
        return ""
    s = s.strip()
    if len(s) <= n:
        return s
    cut = s[:n].rstrip()
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut + "…"


def _clean_items(items) -> list:
    if not isinstance(items, list):
        return []
    out = []
    for it in items[:ITEMS_MAX]:  # clamp extra items (trim priority: after desc)
        if not isinstance(it, dict):
            continue
        sub = _trim(it.get("sub", ""), SUB_MAX)
        desc = _trim(it.get("desc", ""), DESC_MAX)  # desc trimmed first
        if not sub and not desc:
            continue
        out.append({"num": len(out) + 1, "sub": sub, "desc": desc})
    return out


def _marker(m) -> Optional[str]:
    return m if m in MARKERS else None


def _validate_card(card) -> Optional[dict]:
    """Return a repaired card, or None to drop it (per-card schema validation)."""
    if not isinstance(card, dict):
        return None
    ctype = card.get("type")
    if ctype == "content":
        title = _trim(card.get("title", ""), TITLE_MAX)
        items = _clean_items(card.get("items"))
        if not title and not items:
            return None
        return {
            "type": "content",
            "kicker": _trim(card.get("kicker", ""), KICKER_MAX) or None,
            "title": title,
            "marker": _marker(card.get("marker")),
            "items": items,
        }
    if ctype == "compare":
        raw_sides = card.get("sides")
        if not isinstance(raw_sides, list):
            return None
        sides = []
        for sd in raw_sides[:2]:
            if not isinstance(sd, dict):
                continue
            title = _trim(sd.get("title", ""), TITLE_MAX)
            items = _clean_items(sd.get("items"))
            if not title and not items:
                continue
            sides.append({"marker": _marker(sd.get("marker")), "title": title, "items": items})
        if len(sides) < 1:
            return None
        return {"type": "compare", "sides": sides}
    if ctype == "summary":
        cta_in = card.get("cta") or {}
        present = bool(cta_in.get("present")) and bool(cta_in.get("text"))
        cta = {
            "present": present,
            "text": _trim(cta_in.get("text", ""), TITLE_MAX) if present else None,
            "word": (cta_in.get("word") or None) if present else None,
        }
        return {
            "type": "summary",
            "title": _trim(card.get("title", "") or "Итог", TITLE_MAX),
            "body": _trim(card.get("body", ""), BODY_MAX),
            "cta": cta,
        }
    return None  # unknown type -> drop


def validate_and_repair(data: dict) -> tuple[dict, list, str]:
    """Return (cover, cards, cover_title). Never trusts the LLM blindly."""
    cover_in = data.get("cover") or {}
    cover = {
        "plate": (str(cover_in.get("plate")).strip() if cover_in.get("plate") else None),
        "title": _trim(cover_in.get("title", ""), TITLE_MAX + 4),
    }

    raw_cards = data.get("cards") if isinstance(data.get("cards"), list) else []
    repaired = [c for c in (_validate_card(c) for c in raw_cards) if c]

    # Exactly one summary, and it is last. Keep the LAST summary if several.
    summaries = [c for c in repaired if c["type"] == "summary"]
    body = [c for c in repaired if c["type"] != "summary"]
    summary = summaries[-1] if summaries else None

    # clamp: cover + <=19 cards
    if summary:
        body = body[: CARDS_MAX - 1]
        cards = body + [summary]
    else:
        cards = body[:CARDS_MAX]

    # renumber order (cover=1, cards 2..N)
    for i, c in enumerate(cards, start=2):
        c["order"] = i

    # cover fallback: derive from first content card if LLM gave no cover title
    if not cover["title"] and cards:
        first = cards[0]
        cover["title"] = _trim(first.get("title") or "Карусель", TITLE_MAX + 4)

    parts = [p for p in [cover.get("plate"), cover.get("title")] if p]
    cover_title = " ".join(parts).strip()
    return cover, cards, cover_title


# --------------------------------------------------------------------------
# claude -p invocation
# --------------------------------------------------------------------------

def _default_runner(input_text: str, model: str, timeout: int) -> str:
    result = subprocess.run(
        ["claude", "-p", "--output-format", "text", "--model", model],
        input=input_text, capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        raise LLMError(f"claude exit {result.returncode}: {result.stderr[:300]}")
    return result.stdout


def segment_text(
    text: str,
    ig_handle: str = "",
    model: Optional[str] = None,
    timeout: int = 300,
    runner: Optional[Callable[[str], str]] = None,
) -> SegmentResult:
    """Segment raw text into validated carousel cards. ALWAYS returns a
    SegmentResult — a subprocess failure/timeout yields ok=False, never a crash."""
    model = model or os.environ.get("MODEL", "claude-sonnet-5")
    _run = runner or (lambda inp: _default_runner(inp, model, timeout))
    prompt = _load_prompt()
    base = f"{prompt}\n\n## IG-хэндл\n{ig_handle}\n\n## Текст клиента\n{text}"
    attempts = [base, base + "\n\nВЕРНИ СТРОГО ВАЛИДНЫЙ JSON без markdown и пояснений."]

    for inp in attempts:
        try:
            raw = _run(inp)
        except subprocess.TimeoutExpired:
            return SegmentResult(ok=False, error=CLIENT_ERROR)
        except Exception:
            # Any subprocess/runner failure -> typed error, never SystemExit.
            return SegmentResult(ok=False, error=CLIENT_ERROR)
        try:
            data = parse_llm_json(raw)
        except (ValueError, json.JSONDecodeError):
            continue  # one retry with stricter instruction
        cover, cards, title = validate_and_repair(data)
        if cover.get("title") and cards:
            return SegmentResult(ok=True, cover=cover, cards=cards, cover_title=title)
        # parseable but structurally empty -> retry once
    return SegmentResult(ok=False, error="Не удалось разобрать ответ ИИ")
