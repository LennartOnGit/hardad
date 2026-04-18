"""CEFR rolling-average bookkeeping.

A user message receives a score on the 0-100 axis. We map that axis into the
six CEFR bands evenly:

    A1: 0-16.6
    A2: 16.7-33.3
    B1: 33.4-50.0
    B2: 50.1-66.6
    C1: 66.7-83.3
    C2: 83.4-100

The user-level level shown on the progress bar is the rolling mean of the
last 20 scored user messages. We keep those 20 values as a JSON list on the
user row so recomputation is O(20) and trivial.

Scoring penalties for mistakes are applied by the scoring model itself
(see the tutor system prompt), not here — this module just records the
final score.
"""

from __future__ import annotations

from sqlalchemy.orm import Session as DBSession

from app.db import User

BANDS: list[tuple[str, float, float]] = [
    ("A1", 0.0, 16.6),
    ("A2", 16.7, 33.3),
    ("B1", 33.4, 50.0),
    ("B2", 50.1, 66.6),
    ("C1", 66.7, 83.3),
    ("C2", 83.4, 100.0),
]

MAX_SAMPLES = 20


def score_to_level(score: float) -> str:
    """Return the CEFR band for a 0-100 score."""
    for label, lo, hi in BANDS:
        if lo <= score <= hi:
            return label
    return "A1" if score < 0 else "C2"


def record_user_score(user: User, score: float, db: DBSession) -> tuple[float, str]:
    """Append `score` to the user's CEFR sample buffer, trim to 20, recompute
    the rolling average and CEFR level. Does NOT commit.

    Returns the updated ``(avg, level)``.
    """
    score = max(0.0, min(100.0, float(score)))
    samples = list(user.cefr_samples or [])
    samples.append(score)
    if len(samples) > MAX_SAMPLES:
        samples = samples[-MAX_SAMPLES:]

    avg = sum(samples) / len(samples)
    level = score_to_level(avg)

    user.cefr_samples = samples
    user.cefr_score_avg = avg
    user.cefr_level = level
    return avg, level
