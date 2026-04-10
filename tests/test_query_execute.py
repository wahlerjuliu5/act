from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from act.main import app
from act.models import CalendarEvent, QueryResponse

client = TestClient(app)

_BASE_RESPONSE = dict(
    intent="query",
    proposed_action=None,
    human_summary="ok",
    confidence=0.9,
)


def _llm(intent: str, action: dict | None, summary: str = "ok") -> QueryResponse:
    return QueryResponse(intent=intent, proposed_action=action, human_summary=summary, confidence=0.9)


# ---------------------------------------------------------------------------
# execute=False (default) — proposed_action returned but not run
# ---------------------------------------------------------------------------


def test_execute_normalizes_raw_llm_field_names() -> None:
    """_execute_action must work even when proposed_action has raw LLM field names."""
    raw_action = {
        "title": "RL Paper Meeting",
        "start_time": "Sat Apr 15 09:00",  # alias, no year, no timezone
        "end_time": "Sat Apr 15 12:00",
        # no calendar
    }
    llm_resp = _llm("create", raw_action)

    with (
        patch("act.main._cal.list_calendars", return_value=["Work"]),
        patch("act.main._cal.get_events", return_value=[]),
        patch("act.main._ollama_query", new_callable=AsyncMock, return_value=llm_resp),
        patch("act.main._cal.create_event", return_value="NEW-UID") as mock_create,
    ):
        resp = client.post("/query", json={"prompt": "schedule RL Paper tomorrow for 3 hours", "execute": True})

    assert resp.status_code == 200
    result = resp.json()["execution_result"]
    assert result["status"] == "created"
    assert result["title"] == "RL Paper Meeting"
    mock_create.assert_called_once()
    # Verify the datetimes were correctly coerced
    call_kw = mock_create.call_args.kwargs
    assert call_kw["start"].year == 2026
    assert call_kw["start"].month == 4
    assert call_kw["start"].day == 15


def test_execute_false_does_not_call_create() -> None:
    llm_resp = _llm("create", {"title": "X", "start": "2026-04-10T10:00:00",
                                "end": "2026-04-10T11:00:00", "calendar": "Work"})
    with (
        patch("act.main._cal.list_calendars", return_value=[]),
        patch("act.main._cal.get_events", return_value=[]),
        patch("act.main._ollama_query", new_callable=AsyncMock, return_value=llm_resp),
        patch("act.main._cal.create_event") as mock_create,
    ):
        resp = client.post("/query", json={"prompt": "schedule something", "execute": False})

    assert resp.status_code == 200
    assert resp.json()["execution_result"] is None
    mock_create.assert_not_called()


# ---------------------------------------------------------------------------
# execute=True — create
# ---------------------------------------------------------------------------


def test_execute_create_calls_create_event() -> None:
    action = {"title": "Deep Work", "start": "2026-04-10T10:00:00",
               "end": "2026-04-10T12:00:00", "calendar": "Work"}
    llm_resp = _llm("create", action)

    with (
        patch("act.main._cal.list_calendars", return_value=["Work"]),
        patch("act.main._cal.get_events", return_value=[]),
        patch("act.main._ollama_query", new_callable=AsyncMock, return_value=llm_resp),
        patch("act.main._cal.create_event", return_value="NEW-UID") as mock_create,
    ):
        resp = client.post("/query", json={"prompt": "schedule deep work", "execute": True})

    assert resp.status_code == 200
    data = resp.json()
    assert data["execution_result"]["status"] == "created"
    assert data["execution_result"]["event_id"] == "NEW-UID"
    assert data["execution_result"]["calendar"] == "Work"
    mock_create.assert_called_once()


def test_execute_create_auto_reschedules_on_conflict() -> None:
    """When requested time conflicts, the server should reschedule at the next free slot."""
    from act.models import CalendarEvent

    # VPD blocks 10:00–12:00; requested slot 10:30–11:30 conflicts
    existing = CalendarEvent(
        id="E1", title="VPD",
        start=datetime(2026, 4, 10, 10, 0), end=datetime(2026, 4, 10, 12, 0),
        calendar="Work",
    )
    action = {"title": "RL Paper", "start": "2026-04-10T10:30:00",
               "end": "2026-04-10T11:30:00", "calendar": "Work"}  # 1-hour event
    llm_resp = _llm("create", action)

    with (
        patch("act.main._cal.list_calendars", return_value=["Work"]),
        patch("act.main._cal.get_events", return_value=[existing]),
        patch("act.main._ollama_query", new_callable=AsyncMock, return_value=llm_resp),
        patch("act.main._cal.create_event", return_value="RESCHEDULED-UID") as mock_create,
    ):
        resp = client.post("/query", json={"prompt": "schedule RL Paper today", "execute": True})

    assert resp.status_code == 200
    result = resp.json()["execution_result"]
    assert result["status"] == "created"
    mock_create.assert_called_once()
    # Must be scheduled outside the conflict window (not inside 10:00–12:00)
    start_dt = datetime.fromisoformat(result["start"])
    end_dt = datetime.fromisoformat(result["end"])
    assert not (start_dt < datetime(2026, 4, 10, 12, 0) and end_dt > datetime(2026, 4, 10, 10, 0))


def test_execute_create_rescheduled_start_is_first_free_slot() -> None:
    """Auto-reschedule picks the FIRST available gap, not an arbitrary one."""
    from act.models import CalendarEvent

    # Gap before conflict: 09:00–10:00 (1h). Requested is 1h so it fits there.
    existing = CalendarEvent(
        id="E1", title="Morning Block",
        start=datetime(2026, 4, 10, 10, 0), end=datetime(2026, 4, 10, 12, 0),
        calendar="Work",
    )
    action = {"title": "Quick Call", "start": "2026-04-10T10:00:00",
               "end": "2026-04-10T11:00:00", "calendar": "Work"}
    llm_resp = _llm("create", action)

    with (
        patch("act.main._cal.list_calendars", return_value=["Work"]),
        patch("act.main._cal.get_events", return_value=[existing]),
        patch("act.main._ollama_query", new_callable=AsyncMock, return_value=llm_resp),
        patch("act.main._cal.create_event", return_value="UID") as mock_create,
    ):
        resp = client.post("/query", json={"prompt": "schedule Quick Call today", "execute": True})

    assert resp.status_code == 200
    result = resp.json()["execution_result"]
    assert result["status"] == "created"
    start_dt = datetime.fromisoformat(result["start"])
    assert start_dt.hour == 9   # first free gap is 09:00–10:00


def test_execute_create_reports_conflict_when_day_is_full() -> None:
    """When the entire working day is blocked, return conflict (can't reschedule)."""
    from act.models import CalendarEvent

    full_day = CalendarEvent(
        id="E1", title="All Day",
        start=datetime(2026, 4, 10, 9, 0), end=datetime(2026, 4, 10, 18, 0),
        calendar="Work",
    )
    action = {"title": "New Event", "start": "2026-04-10T10:00:00",
               "end": "2026-04-10T13:00:00", "calendar": "Work"}
    llm_resp = _llm("create", action)

    with (
        patch("act.main._cal.list_calendars", return_value=["Work"]),
        patch("act.main._cal.get_events", return_value=[full_day]),
        patch("act.main._ollama_query", new_callable=AsyncMock, return_value=llm_resp),
        patch("act.main._cal.create_event") as mock_create,
    ):
        resp = client.post("/query", json={"prompt": "schedule something", "execute": True})

    assert resp.status_code == 200
    result = resp.json()["execution_result"]
    assert result["status"] == "conflict"
    assert "conflicts" in result
    mock_create.assert_not_called()


def test_proposed_action_shows_rescheduled_times_before_execute() -> None:
    """When execute=False, proposed_action must already reflect conflict-resolved times."""
    existing = CalendarEvent(
        id="E1", title="VPD",
        start=datetime(2026, 4, 10, 9, 0), end=datetime(2026, 4, 10, 12, 0),
        calendar="Work",
    )
    # LLM wants 9:00-12:00, which conflicts with VPD
    action = {"title": "RL Paper", "start": "2026-04-10T09:00:00",
               "end": "2026-04-10T12:00:00", "calendar": "Work"}
    llm_resp = _llm("create", action)

    with (
        patch("act.main._cal.list_calendars", return_value=["Work"]),
        patch("act.main._cal.get_events", return_value=[existing]),
        patch("act.main._ollama_query", new_callable=AsyncMock, return_value=llm_resp),
    ):
        resp = client.post("/query", json={"prompt": "schedule RL Paper today for 3h", "execute": False})

    assert resp.status_code == 200
    data = resp.json()
    # proposed_action should already show the rescheduled time (12:00 after VPD)
    proposed = data["proposed_action"]
    start_dt = datetime.fromisoformat(proposed["start"])
    assert start_dt.hour >= 12, "Proposed start should be after the conflict window"
    assert data["execution_result"] is None  # nothing was executed


def test_execute_create_result_includes_actual_times() -> None:
    """execution_result must include the actual start/end that was written."""
    action = {"title": "Deep Work", "start": "2026-04-10T10:00:00",
               "end": "2026-04-10T12:00:00", "calendar": "Work"}
    llm_resp = _llm("create", action)

    with (
        patch("act.main._cal.list_calendars", return_value=["Work"]),
        patch("act.main._cal.get_events", return_value=[]),
        patch("act.main._ollama_query", new_callable=AsyncMock, return_value=llm_resp),
        patch("act.main._cal.create_event", return_value="UID"),
    ):
        resp = client.post("/query", json={"prompt": "schedule deep work", "execute": True})

    assert resp.status_code == 200
    result = resp.json()["execution_result"]
    assert result["status"] == "created"
    assert result["start"] == "2026-04-10T10:00:00"
    assert result["end"] == "2026-04-10T12:00:00"


# ---------------------------------------------------------------------------
# execute=True — delete
# ---------------------------------------------------------------------------


def test_execute_delete_calls_delete_event() -> None:
    action = {"event_id": "UID-XYZ", "title": "Old Meeting"}
    llm_resp = _llm("delete", action)

    with (
        patch("act.main._cal.list_calendars", return_value=[]),
        patch("act.main._cal.get_events", return_value=[]),
        patch("act.main._ollama_query", new_callable=AsyncMock, return_value=llm_resp),
        patch("act.main._cal.delete_event", return_value="deleted") as mock_delete,
    ):
        resp = client.post("/query", json={"prompt": "delete old meeting", "execute": True})

    assert resp.status_code == 200
    assert resp.json()["execution_result"]["status"] == "deleted"
    mock_delete.assert_called_once_with("UID-XYZ")


def test_execute_delete_missing_event_id_returns_not_found_when_no_match() -> None:
    llm_resp = _llm("delete", {"title": "Something"})  # no event_id, no matching events

    with (
        patch("act.main._cal.list_calendars", return_value=[]),
        patch("act.main._cal.get_events", return_value=[]),
        patch("act.main._ollama_query", new_callable=AsyncMock, return_value=llm_resp),
    ):
        resp = client.post("/query", json={"prompt": "delete something", "execute": True})

    assert resp.status_code == 200
    assert resp.json()["execution_result"]["status"] == "not_found"


def test_execute_delete_by_title_finds_and_deletes() -> None:
    """When event_id is null, server falls back to title-based lookup and deletes."""
    rl_paper = CalendarEvent(
        id="UID-RL",
        title="RL Paper",
        start=datetime(2026, 4, 12, 9, 0),
        end=datetime(2026, 4, 12, 12, 0),
        calendar="Work",
    )
    llm_resp = _llm("delete", {"event_id": None, "title": "RL Paper"})

    with (
        patch("act.main._cal.list_calendars", return_value=["Work"]),
        patch("act.main._cal.get_events", return_value=[rl_paper]),
        patch("act.main._ollama_query", new_callable=AsyncMock, return_value=llm_resp),
        patch("act.main._cal.delete_event", return_value="deleted") as mock_delete,
    ):
        resp = client.post("/query", json={"prompt": "delete RL Paper", "execute": True})

    assert resp.status_code == 200
    result = resp.json()["execution_result"]
    assert result["status"] == "deleted"
    assert result["count"] == 1
    mock_delete.assert_called_once_with("UID-RL")


def test_execute_delete_by_title_deletes_all_matching_events() -> None:
    """Title-based delete removes ALL upcoming events whose title contains the search term."""
    events = [
        CalendarEvent(id=f"UID-{i}", title="RL Paper", start=datetime(2026, 4, 12 + i, 9, 0),
                      end=datetime(2026, 4, 12 + i, 12, 0), calendar="Work")
        for i in range(2)
    ]
    llm_resp = _llm("delete", {"event_id": None, "title": "RL Paper"})

    with (
        patch("act.main._cal.list_calendars", return_value=["Work"]),
        patch("act.main._cal.get_events", return_value=events),
        patch("act.main._ollama_query", new_callable=AsyncMock, return_value=llm_resp),
        patch("act.main._cal.delete_event", return_value="deleted") as mock_delete,
    ):
        resp = client.post("/query", json={"prompt": "delete RL Paper on both days", "execute": True})

    assert resp.status_code == 200
    result = resp.json()["execution_result"]
    assert result["status"] == "deleted"
    assert result["count"] == 2
    assert mock_delete.call_count == 2


# ---------------------------------------------------------------------------
# execute=True — find_slots
# ---------------------------------------------------------------------------


def test_execute_find_slots_returns_slots() -> None:
    action = {"date": "2026-04-10", "duration_minutes": 90}
    llm_resp = _llm("find_slots", action)

    with (
        patch("act.main._cal.list_calendars", return_value=[]),
        patch("act.main._cal.get_events", return_value=[]),
        patch("act.main._ollama_query", new_callable=AsyncMock, return_value=llm_resp),
    ):
        resp = client.post("/query", json={"prompt": "find a slot", "execute": True})

    assert resp.status_code == 200
    result = resp.json()["execution_result"]
    assert result["status"] == "ok"
    assert isinstance(result["slots"], list)
    assert all(s["duration_minutes"] >= 90 for s in result["slots"])


# ---------------------------------------------------------------------------
# execute=True — query intent (no-op)
# ---------------------------------------------------------------------------


def test_execute_query_intent_no_execution() -> None:
    llm_resp = _llm("query", None, summary="You have 2 events.")

    with (
        patch("act.main._cal.list_calendars", return_value=[]),
        patch("act.main._cal.get_events", return_value=[]),
        patch("act.main._ollama_query", new_callable=AsyncMock, return_value=llm_resp),
    ):
        resp = client.post("/query", json={"prompt": "what's on my calendar?", "execute": True})

    assert resp.status_code == 200
    # proposed_action is None so execution_result stays None
    assert resp.json()["execution_result"] is None


# ---------------------------------------------------------------------------
# System prompt — calendar list appears in schema guidance
# ---------------------------------------------------------------------------


def test_system_prompt_schema_includes_calendar_guidance() -> None:
    from act.llm.prompt import build_system_prompt

    prompt = build_system_prompt(["Work", "Personal"], [], "09:00", "18:00")
    assert "pick from the calendars listed above" in prompt
    assert "proposed_action" in prompt
    assert "find_slots" in prompt
