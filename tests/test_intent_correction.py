"""Tests for the LLM intent-mismatch correction layer.

The model sometimes returns find_slots when the user says "schedule X" or "book X".
These tests verify that:
  1. The system prompt contains unambiguous intent-classification guidance.
  2. The server corrects find_slots → create when a scheduling verb is present.
  3. Genuine availability queries are NOT corrected.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from act.llm.prompt import build_system_prompt
from act.main import _is_scheduling_prompt, app
from act.models import QueryResponse

client = TestClient(app)


def _find_slots_resp(date: str = "2026-04-11", duration: int = 180) -> QueryResponse:
    return QueryResponse(
        intent="find_slots",
        proposed_action={"date": date, "duration_minutes": duration},
        human_summary="Here are some available time slots.",
        confidence=0.9,
    )


# ---------------------------------------------------------------------------
# System prompt — intent classification guidance
# ---------------------------------------------------------------------------


def test_system_prompt_names_schedule_as_create_trigger() -> None:
    """'schedule' must appear in the system prompt as a create-intent trigger."""
    prompt = build_system_prompt(["Work"], [], "09:00", "18:00")
    # The scheduling verb should appear in a create-intent context
    create_section = prompt[prompt.lower().index("create"):]
    assert "schedule" in create_section.lower()


def test_system_prompt_restricts_find_slots_to_availability() -> None:
    """System prompt must warn that find_slots is only for availability queries."""
    prompt = build_system_prompt(["Work"], [], "09:00", "18:00")
    prompt_lower = prompt.lower()
    # Must have the restriction language
    assert "never" in prompt_lower or "only" in prompt_lower
    assert "find_slots" in prompt_lower


def test_system_prompt_shows_schedule_create_example() -> None:
    """The example in the system prompt must demonstrate schedule → create."""
    prompt = build_system_prompt(["Work"], [], "09:00", "18:00")
    # The concrete example should reference a "schedule … for N hours" → create pattern
    assert "schedule" in prompt.lower()
    assert '"create"' in prompt


# ---------------------------------------------------------------------------
# _is_scheduling_prompt — unit tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prompt",
    [
        "schedule RL Paper tomorrow for 3 hours",
        "book dentist on Friday at 2pm",
        "add team meeting at 10am",
        "create a 1-hour block for deep work",
        "Please schedule a sync with Alice",
        "SCHEDULE something for monday",
    ],
)
def test_is_scheduling_prompt_true(prompt: str) -> None:
    assert _is_scheduling_prompt(prompt) is True


@pytest.mark.parametrize(
    "prompt",
    [
        "when am I free tomorrow?",
        "find me a 2-hour slot on Friday",
        "show available times this week",
        "what's on my calendar?",
        "delete the 3pm meeting",
    ],
)
def test_is_scheduling_prompt_false(prompt: str) -> None:
    assert _is_scheduling_prompt(prompt) is False


# ---------------------------------------------------------------------------
# Intent correction — scheduling verb + find_slots response → create
# ---------------------------------------------------------------------------


def test_schedule_prompt_corrected_from_find_slots_to_create() -> None:
    """'schedule X for N hours' + LLM find_slots → server auto-corrects to create."""
    with (
        patch("act.main._cal.list_calendars", return_value=["Work"]),
        patch("act.main._cal.get_events", return_value=[]),
        patch("act.main._ollama_query", new_callable=AsyncMock, return_value=_find_slots_resp()),
        patch("act.main._cal.create_event", return_value="NEW-UID") as mock_create,
    ):
        resp = client.post(
            "/query",
            json={"prompt": "schedule RL Paper tomorrow for 3 hours", "execute": True},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] == "create", f"Expected create, got {data['intent']}"
    assert data["execution_result"]["status"] == "created"
    assert "RL Paper" in data["execution_result"]["title"]
    mock_create.assert_called_once()


def test_book_prompt_corrected_from_find_slots_to_create() -> None:
    """'book X' triggers the same correction."""
    with (
        patch("act.main._cal.list_calendars", return_value=["Work"]),
        patch("act.main._cal.get_events", return_value=[]),
        patch("act.main._ollama_query", new_callable=AsyncMock, return_value=_find_slots_resp()),
        patch("act.main._cal.create_event", return_value="BOOK-UID") as mock_create,
    ):
        resp = client.post(
            "/query",
            json={"prompt": "book dentist on 2026-04-11 for 1 hour", "execute": True},
        )

    assert resp.status_code == 200
    assert resp.json()["intent"] == "create"
    mock_create.assert_called_once()


def test_add_prompt_corrected_from_find_slots_to_create() -> None:
    """'add X' triggers the same correction."""
    with (
        patch("act.main._cal.list_calendars", return_value=["Work"]),
        patch("act.main._cal.get_events", return_value=[]),
        patch("act.main._ollama_query", new_callable=AsyncMock, return_value=_find_slots_resp()),
        patch("act.main._cal.create_event", return_value="ADD-UID") as mock_create,
    ):
        resp = client.post(
            "/query",
            json={"prompt": "add team sync tomorrow for 30 min", "execute": True},
        )

    assert resp.status_code == 200
    assert resp.json()["intent"] == "create"
    mock_create.assert_called_once()


def test_schedule_corrected_execute_false_shows_proposed_action() -> None:
    """With execute=False the correction still fixes the intent and proposed_action."""
    with (
        patch("act.main._cal.list_calendars", return_value=["Work"]),
        patch("act.main._cal.get_events", return_value=[]),
        patch("act.main._ollama_query", new_callable=AsyncMock, return_value=_find_slots_resp()),
        patch("act.main._cal.create_event") as mock_create,
    ):
        resp = client.post(
            "/query",
            json={"prompt": "schedule RL Paper tomorrow for 3 hours", "execute": False},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] == "create"
    assert data["proposed_action"]["title"] == "RL Paper"
    assert data["proposed_action"]["start"].startswith("2026-04-11")
    assert data["execution_result"] is None
    mock_create.assert_not_called()


def test_corrected_start_time_respects_working_hours() -> None:
    """Auto-scheduled event must start within working hours (09:00 when day is free)."""
    with (
        patch("act.main._cal.list_calendars", return_value=["Work"]),
        patch("act.main._cal.get_events", return_value=[]),
        patch("act.main._ollama_query", new_callable=AsyncMock, return_value=_find_slots_resp()),
        patch("act.main._cal.create_event", return_value="UID") as mock_create,
    ):
        resp = client.post(
            "/query",
            json={"prompt": "schedule RL Paper tomorrow for 3 hours", "execute": True},
        )

    assert resp.status_code == 200
    call_kw = mock_create.call_args.kwargs
    assert call_kw["start"].hour == 9  # first available slot starts at working-hours start
    assert call_kw["end"].hour == 12   # 9am + 3h = 12pm


def test_corrected_end_time_matches_duration() -> None:
    """Auto-scheduled end = start + duration_minutes from the LLM's find_slots action."""
    with (
        patch("act.main._cal.list_calendars", return_value=["Work"]),
        patch("act.main._cal.get_events", return_value=[]),
        patch(
            "act.main._ollama_query",
            new_callable=AsyncMock,
            return_value=_find_slots_resp(duration=90),
        ),
        patch("act.main._cal.create_event", return_value="UID") as mock_create,
    ):
        resp = client.post(
            "/query",
            json={"prompt": "schedule deep work tomorrow for 90 minutes", "execute": True},
        )

    assert resp.status_code == 200
    call_kw = mock_create.call_args.kwargs
    delta = call_kw["end"] - call_kw["start"]
    assert int(delta.total_seconds() / 60) == 90


# ---------------------------------------------------------------------------
# No correction for genuine availability queries
# ---------------------------------------------------------------------------


def test_genuine_availability_prompt_not_corrected() -> None:
    """'when am I free' should NOT be corrected — find_slots is the right intent."""
    llm_resp = _find_slots_resp()
    with (
        patch("act.main._cal.list_calendars", return_value=[]),
        patch("act.main._cal.get_events", return_value=[]),
        patch("act.main._ollama_query", new_callable=AsyncMock, return_value=llm_resp),
    ):
        resp = client.post(
            "/query",
            json={"prompt": "when am I free tomorrow for 3 hours?", "execute": True},
        )

    assert resp.status_code == 200
    assert resp.json()["intent"] == "find_slots"


def test_find_free_slots_prompt_not_corrected() -> None:
    """'find me a slot' should NOT be corrected."""
    llm_resp = _find_slots_resp()
    with (
        patch("act.main._cal.list_calendars", return_value=[]),
        patch("act.main._cal.get_events", return_value=[]),
        patch("act.main._ollama_query", new_callable=AsyncMock, return_value=llm_resp),
    ):
        resp = client.post(
            "/query",
            json={"prompt": "find me a 3-hour slot tomorrow", "execute": True},
        )

    assert resp.status_code == 200
    assert resp.json()["intent"] == "find_slots"


# ---------------------------------------------------------------------------
# Graceful fallback — no free slots available
# ---------------------------------------------------------------------------


def test_correction_falls_back_when_day_is_full() -> None:
    """If no free slots exist on that day, keep original find_slots result gracefully."""
    from datetime import datetime

    from act.models import CalendarEvent

    # A back-to-back day that leaves no 3-hour gap
    busy = [
        CalendarEvent(
            id="1", title="A",
            start=datetime(2026, 4, 11, 9, 0), end=datetime(2026, 4, 11, 18, 0),
            calendar="Work",
        )
    ]
    with (
        patch("act.main._cal.list_calendars", return_value=["Work"]),
        patch("act.main._cal.get_events", return_value=busy),
        patch("act.main._ollama_query", new_callable=AsyncMock, return_value=_find_slots_resp()),
        patch("act.main._cal.create_event") as mock_create,
    ):
        resp = client.post(
            "/query",
            json={"prompt": "schedule RL Paper tomorrow for 3 hours", "execute": True},
        )

    assert resp.status_code == 200
    # Can't create — falls back to find_slots (which reports empty slots)
    data = resp.json()
    assert data["execution_result"]["status"] == "ok"
    assert data["execution_result"]["slots"] == []
    mock_create.assert_not_called()
