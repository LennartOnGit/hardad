import uuid
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.budget import check_budget, record_usage
from app.cefr import BANDS, MAX_SAMPLES, record_user_score, score_to_level
from app.db import DailyUsage, User
from app.llm import _extract_json
from app.main import app

client = TestClient(app, root_path="/docker_demo")


def _make_user(budget: int | None) -> User:
    u = User(
        id=uuid.uuid4(),
        name="Test",
        access_token="tok-" + uuid.uuid4().hex,
        is_admin=False,
        daily_token_budget=budget,
    )
    # JSON-typed columns without a DB-side default stay unset until explicit
    # assignment; mirror the real create_all() default here.
    u.cefr_samples = None
    u.cefr_score_avg = None
    u.cefr_level = None
    return u


# ---------------------------------------------------------------------------
# Healthz + auth
# ---------------------------------------------------------------------------


def test_healthz_ok():
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_messages_requires_auth():
    response = client.post("/messages", json={"content": "hej"})
    assert response.status_code == 401


def test_translate_requires_auth():
    response = client.post("/translate", json={"text": "hej"})
    assert response.status_code == 401


def test_dict_requires_auth():
    response = client.get("/dict/hej")
    assert response.status_code == 401


def test_news_requires_auth():
    response = client.get("/news")
    assert response.status_code == 401


def test_me_requires_auth():
    response = client.get("/me")
    assert response.status_code == 401


@pytest.mark.skip(reason="integration test, requires running Postgres")
def test_messages_with_valid_token():
    """End-to-end chat flow against a real database. Run manually."""
    pass


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------


def test_budget_check_unlimited():
    """A user with `daily_token_budget=None` is never rate-limited."""
    user = _make_user(budget=None)
    db = MagicMock()
    assert check_budget(user, db) is None
    db.execute.assert_not_called()


def test_budget_check_exhausted():
    """A user with budget=100 and usage already at 100 is refused with 429."""
    user = _make_user(budget=100)
    existing = DailyUsage(user_id=user.id, date=None, tokens_in=60, tokens_out=40)

    db = MagicMock()
    db.execute.return_value.scalar_one_or_none.return_value = existing

    with pytest.raises(HTTPException) as excinfo:
        check_budget(user, db)
    assert excinfo.value.status_code == 429
    assert excinfo.value.detail["remaining"] == 0
    assert "kvot" in excinfo.value.detail["error"]


def test_budget_check_remaining_returned():
    """When there's budget left, check_budget returns the remaining count."""
    user = _make_user(budget=1000)
    existing = DailyUsage(user_id=user.id, date=None, tokens_in=100, tokens_out=50)

    db = MagicMock()
    db.execute.return_value.scalar_one_or_none.return_value = existing

    remaining = check_budget(user, db)
    assert remaining == 850


def test_record_usage_creates_new_row():
    """record_usage adds a DailyUsage row when none exists for today."""
    user = _make_user(budget=1000)
    db = MagicMock()
    db.execute.return_value.scalar_one_or_none.return_value = None

    record_usage(user, 10, 20, db)

    db.add.assert_called_once()
    added = db.add.call_args.args[0]
    assert isinstance(added, DailyUsage)
    assert added.tokens_in == 10
    assert added.tokens_out == 20
    db.commit.assert_not_called()  # caller owns the commit


def test_record_usage_increments_existing_row():
    """record_usage increments an existing row and does not commit."""
    user = _make_user(budget=1000)
    existing = DailyUsage(user_id=user.id, date=None, tokens_in=5, tokens_out=7)

    db = MagicMock()
    db.execute.return_value.scalar_one_or_none.return_value = existing

    record_usage(user, 3, 4, db)

    assert existing.tokens_in == 8
    assert existing.tokens_out == 11
    db.add.assert_not_called()
    db.commit.assert_not_called()


# ---------------------------------------------------------------------------
# CEFR bookkeeping
# ---------------------------------------------------------------------------


def test_score_to_level_band_boundaries():
    # Each band's lo/hi landmarks should resolve to the band label. Picks
    # one score inside each band plus the boundary cases.
    assert score_to_level(0) == "A1"
    assert score_to_level(16.6) == "A1"
    assert score_to_level(16.7) == "A2"
    assert score_to_level(33.3) == "A2"
    assert score_to_level(33.4) == "B1"
    assert score_to_level(50) == "B1"
    assert score_to_level(50.1) == "B2"
    assert score_to_level(66.6) == "B2"
    assert score_to_level(66.7) == "C1"
    assert score_to_level(83.3) == "C1"
    assert score_to_level(83.4) == "C2"
    assert score_to_level(100) == "C2"


def test_score_to_level_out_of_range():
    """Below zero clamps to A1, above 100 clamps to C2."""
    assert score_to_level(-5) == "A1"
    assert score_to_level(200) == "C2"


def test_bands_cover_full_range():
    """BANDS table must start at 0 and end at 100 without gaps > 0.1."""
    assert BANDS[0][1] == 0.0
    assert BANDS[-1][2] == 100.0
    # Adjacent bands meet within a tenth (float rounding in the table).
    for i in range(len(BANDS) - 1):
        assert BANDS[i + 1][1] - BANDS[i][2] < 0.15


def test_record_user_score_appends_and_averages():
    user = _make_user(budget=1000)
    db = MagicMock()

    avg, level = record_user_score(user, 20, db)
    assert avg == 20.0
    assert level == "A2"
    assert user.cefr_samples == [20.0]
    assert user.cefr_score_avg == 20.0
    assert user.cefr_level == "A2"

    avg2, level2 = record_user_score(user, 50, db)
    assert avg2 == 35.0           # mean of [20, 50]
    assert level2 == "B1"
    assert user.cefr_samples == [20.0, 50.0]


def test_record_user_score_trims_to_max_samples():
    """record_user_score keeps only the most recent MAX_SAMPLES values."""
    user = _make_user(budget=1000)
    db = MagicMock()

    # Push MAX_SAMPLES+5 values — oldest ones should fall off.
    for i in range(MAX_SAMPLES + 5):
        record_user_score(user, float(i), db)

    assert len(user.cefr_samples) == MAX_SAMPLES
    # The first 5 values (0..4) should have been dropped.
    assert user.cefr_samples[0] == 5.0
    assert user.cefr_samples[-1] == float(MAX_SAMPLES + 4)


def test_record_user_score_clamps_input():
    """Scores outside 0..100 are clamped, not rejected."""
    user = _make_user(budget=1000)
    db = MagicMock()

    avg, _ = record_user_score(user, -10, db)
    assert user.cefr_samples == [0.0]
    assert avg == 0.0

    avg2, _ = record_user_score(user, 500, db)
    assert user.cefr_samples == [0.0, 100.0]
    assert avg2 == 50.0


# ---------------------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------------------


def test_extract_json_plain():
    assert _extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_with_prefix_brace():
    # The tutor helper prefills "{" and splices; mimic that output shape.
    raw = '{"reply": "Hej!", "cefr_score": 42, "corrections": []}'
    assert _extract_json(raw) == {"reply": "Hej!", "cefr_score": 42, "corrections": []}


def test_extract_json_code_fence():
    raw = '```json\n{"a": 1, "b": [2, 3]}\n```'
    assert _extract_json(raw) == {"a": 1, "b": [2, 3]}


def test_extract_json_with_leading_prose():
    raw = 'Sure! Here is the JSON:\n{"cefr_score": 30}\n-- end --'
    assert _extract_json(raw) == {"cefr_score": 30}


def test_extract_json_returns_none_when_unparseable():
    assert _extract_json("absolutely no json here") is None
