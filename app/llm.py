"""Anthropic API helpers.

Every function here returns ``(result, tokens_in, tokens_out)`` so callers
can attribute cost to the requesting user's daily budget via
``app.budget.record_usage``. Two models are used:

  - ``ANTHROPIC_MODEL`` (Sonnet by default) for the main tutor reply —
    quality matters for pedagogy.
  - ``ANTHROPIC_FAST_MODEL`` (Haiku by default) for translations, news
    topic generation, and dictionary fallbacks — volume matters, reply
    is short, cost matters more than nuance.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import anthropic

from app.config import settings

_PROMPT_DIR = Path(__file__).parent / "prompts"
_prompt_cache: dict[str, str] = {}


def _load_prompt(name: str) -> str:
    if name not in _prompt_cache:
        _prompt_cache[name] = (_PROMPT_DIR / name).read_text()
    return _prompt_cache[name]


def load_system_prompt() -> str:
    """Backwards-compatible alias used by legacy tests/callers."""
    return _load_prompt("tutor_system_v2.txt")


def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)


# ---------------------------------------------------------------------------
# Robust JSON extraction — the tutor prompt asks for a bare JSON object but
# models occasionally add a code fence or a stray leading word. Pull the
# first ``{...}`` balanced block out.
# ---------------------------------------------------------------------------
def _extract_json(text: str) -> dict | None:
    text = text.strip()
    # Fast path: whole thing parses.
    try:
        return json.loads(text)
    except Exception:
        pass
    # Strip code fences.
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    # Scan for the first balanced {...}.
    depth = 0
    start = None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                candidate = text[start : i + 1]
                try:
                    return json.loads(candidate)
                except Exception:
                    start = None
    return None


# ---------------------------------------------------------------------------
# Tutor reply — structured JSON with reply + CEFR score + corrections.
# ---------------------------------------------------------------------------
def tutor_reply(
    messages: list[dict],
) -> tuple[dict, int, int]:
    """Call the tutor model and return ``(parsed, tokens_in, tokens_out)``.

    ``parsed`` is always a dict with keys ``reply`` (str), ``cefr_score``
    (float), and ``corrections`` (list). If the model's JSON can't be
    parsed we fall back to treating the whole output as ``reply`` with
    ``cefr_score=0`` and no corrections — conversation keeps working.
    """
    client = _client()
    # Pre-filled ``{`` nudges the model into JSON mode. We re-attach the
    # brace before parsing.
    response = client.messages.create(
        model=settings.ANTHROPIC_MODEL,
        max_tokens=1024,
        system=_load_prompt("tutor_system_v2.txt"),
        messages=[
            *messages,
            {"role": "assistant", "content": "{"},
        ],
    )
    raw = "{" + response.content[0].text
    tokens_in = response.usage.input_tokens
    tokens_out = response.usage.output_tokens

    parsed = _extract_json(raw)
    if not isinstance(parsed, dict) or "reply" not in parsed:
        # Graceful degradation.
        parsed = {"reply": raw, "cefr_score": 0.0, "corrections": []}

    parsed.setdefault("cefr_score", 0.0)
    parsed.setdefault("corrections", [])
    # Coerce types.
    try:
        parsed["cefr_score"] = float(parsed["cefr_score"])
    except (TypeError, ValueError):
        parsed["cefr_score"] = 0.0
    if not isinstance(parsed["corrections"], list):
        parsed["corrections"] = []

    return parsed, tokens_in, tokens_out


# ---------------------------------------------------------------------------
# Translation — Haiku, plain text out.
# ---------------------------------------------------------------------------
def translate_text(text: str) -> tuple[str, int, int]:
    client = _client()
    response = client.messages.create(
        model=settings.ANTHROPIC_FAST_MODEL,
        max_tokens=600,
        system=_load_prompt("translate_system.txt"),
        messages=[{"role": "user", "content": text}],
    )
    out = response.content[0].text.strip()
    return out, response.usage.input_tokens, response.usage.output_tokens


# ---------------------------------------------------------------------------
# Dictionary lookup fallback — Haiku, JSON entry out.
# ---------------------------------------------------------------------------
def lookup_word(word: str) -> tuple[dict, int, int]:
    client = _client()
    response = client.messages.create(
        model=settings.ANTHROPIC_FAST_MODEL,
        max_tokens=180,
        system=_load_prompt("dict_system.txt"),
        messages=[
            {"role": "user", "content": word},
            {"role": "assistant", "content": "{"},
        ],
    )
    raw = "{" + response.content[0].text
    parsed = _extract_json(raw) or {"en": "?", "pos": "unknown"}
    return parsed, response.usage.input_tokens, response.usage.output_tokens


# ---------------------------------------------------------------------------
# News topics — Haiku, JSON with three topic cards.
# ---------------------------------------------------------------------------
def generate_news_topics(cefr_level: str) -> tuple[list, int, int]:
    client = _client()
    response = client.messages.create(
        model=settings.ANTHROPIC_FAST_MODEL,
        max_tokens=1200,
        system=_load_prompt("news_system.txt"),
        messages=[
            {"role": "user", "content": f"CEFR level: {cefr_level}"},
            {"role": "assistant", "content": "{"},
        ],
    )
    raw = "{" + response.content[0].text
    parsed = _extract_json(raw) or {}
    topics = parsed.get("topics") if isinstance(parsed, dict) else None
    if not isinstance(topics, list):
        topics = []
    return topics, response.usage.input_tokens, response.usage.output_tokens


# ---------------------------------------------------------------------------
# Legacy helper kept for any test/caller that imports it.
# ---------------------------------------------------------------------------
def call_anthropic(
    messages: list[dict], system_prompt: str
) -> tuple[str, int, int]:
    """Deprecated: plain text reply. Prefer :func:`tutor_reply`."""
    client = _client()
    response = client.messages.create(
        model=settings.ANTHROPIC_MODEL,
        max_tokens=2048,
        system=system_prompt,
        messages=messages,
    )
    reply = response.content[0].text
    return reply, response.usage.input_tokens, response.usage.output_tokens
