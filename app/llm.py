from pathlib import Path

import anthropic

from app.config import settings

_system_prompt: str | None = None


def load_system_prompt() -> str:
    """Load the tutor system prompt from disk. Cached at module level."""
    global _system_prompt
    if _system_prompt is None:
        _system_prompt = (Path(__file__).parent / "prompts" / "system_v1.txt").read_text()
    return _system_prompt


def call_anthropic(
    messages: list[dict], system_prompt: str
) -> tuple[str, int, int]:
    """Call the Anthropic API and return (reply_text, input_tokens, output_tokens)."""
    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    response = client.messages.create(
        model=settings.ANTHROPIC_MODEL,
        max_tokens=2048,
        system=system_prompt,
        messages=messages,
    )
    reply = response.content[0].text
    return reply, response.usage.input_tokens, response.usage.output_tokens
