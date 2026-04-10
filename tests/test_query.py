from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from act.llm.ollama import extract_json
from act.llm.prompt import build_system_prompt
from act.main import app
from act.models import CalendarEvent, QueryResponse

client = TestClient(app)

# ---------------------------------------------------------------------------
# extract_json — unit tests (no network)
# ---------------------------------------------------------------------------

VALID_JSON = '{"intent": "query", "proposed_action": null, "human_summary": "ok", "confidence": 0.9}'


def test_extract_json_plain() -> None:
    data = extract_json(VALID_JSON)
    assert data["intent"] == "query"


def test_extract_json_with_code_fence() -> None:
    text = f"```json\n{VALID_JSON}\n```"
    data = extract_json(text)
    assert data["intent"] == "query"


def test_extract_json_with_plain_fence() -> None:
    text = f"```\n{VALID_JSON}\n```"
    data = extract_json(text)
    assert data["confidence"] == 0.9


def test_extract_json_strips_whitespace() -> None:
    data = extract_json(f"  \n{VALID_JSON}\n  ")
    assert data["human_summary"] == "ok"


def test_extract_json_raises_on_garbage() -> None:
    with pytest.raises(json.JSONDecodeError):
        extract_json("not json at all")


# ---------------------------------------------------------------------------
# QueryResponse validation
# ---------------------------------------------------------------------------


def test_query_response_valid_intent() -> None:
    for intent in ("query", "create", "delete", "update", "find_slots"):
        r = QueryResponse(intent=intent, human_summary="x", confidence=0.5)
        assert r.intent == intent


def test_query_response_invalid_intent() -> None:
    with pytest.raises(Exception):
        QueryResponse(intent="unknown", human_summary="x", confidence=0.5)


def test_query_response_confidence_bounds() -> None:
    with pytest.raises(Exception):
        QueryResponse(intent="query", human_summary="x", confidence=1.5)
    with pytest.raises(Exception):
        QueryResponse(intent="query", human_summary="x", confidence=-0.1)


# ---------------------------------------------------------------------------
# build_system_prompt — unit tests
# ---------------------------------------------------------------------------


def test_system_prompt_contains_tomorrow_iso_date() -> None:
    """System prompt must include the ISO date for 'tomorrow' so the model doesn't compute it."""
    tomorrow_iso = (date.today() + timedelta(days=1)).isoformat()
    prompt = build_system_prompt([], [], "09:00", "18:00")
    assert tomorrow_iso in prompt, f"Expected {tomorrow_iso} in prompt"
    assert "Tomorrow" in prompt


def test_system_prompt_date_anchors_cover_7_days() -> None:
    """All 8 date anchors (today through +7 days) must appear as ISO strings."""
    today = date.today()
    prompt = build_system_prompt([], [], "09:00", "18:00")
    for offset in range(8):
        iso = (today + timedelta(days=offset)).isoformat()
        assert iso in prompt, f"Missing date anchor for +{offset} days: {iso}"


def test_system_prompt_tells_model_not_to_compute_dates() -> None:
    """Prompt must instruct the model to use the provided dates, not compute them."""
    prompt = build_system_prompt([], [], "09:00", "18:00")
    assert "do NOT compute" in prompt or "do not compute" in prompt.lower()


def test_system_prompt_contains_calendars() -> None:
    prompt = build_system_prompt(
        calendars=["Work", "Personal"],
        events=[],
        working_hours_start="09:00",
        working_hours_end="18:00",
    )
    assert "Work" in prompt
    assert "Personal" in prompt


def test_system_prompt_contains_events() -> None:
    event = CalendarEvent(
        id="1",
        title="Standup",
        start=datetime(2026, 4, 10, 9, 0),
        end=datetime(2026, 4, 10, 9, 30),
        calendar="Work",
    )
    prompt = build_system_prompt(
        calendars=["Work"],
        events=[event],
        working_hours_start="09:00",
        working_hours_end="18:00",
    )
    assert "Standup" in prompt
    assert "09:00" in prompt


def test_system_prompt_no_events_message() -> None:
    prompt = build_system_prompt(
        calendars=["Work"],
        events=[],
        working_hours_start="09:00",
        working_hours_end="18:00",
    )
    assert "No events" in prompt


def test_system_prompt_includes_schema() -> None:
    prompt = build_system_prompt([], [], "09:00", "18:00")
    assert "intent" in prompt
    assert "human_summary" in prompt
    assert "confidence" in prompt


# ---------------------------------------------------------------------------
# POST /query endpoint — mocked at _ollama_query level
# ---------------------------------------------------------------------------

_GOOD_RESPONSE = QueryResponse(
    intent="query",
    proposed_action=None,
    human_summary="You have 2 events this week.",
    confidence=0.92,
)


def test_query_endpoint_success() -> None:
    with (
        patch("act.main._cal.list_calendars", return_value=["Work"]),
        patch("act.main._cal.get_events", return_value=[]),
        patch("act.main._ollama_query", new_callable=AsyncMock, return_value=_GOOD_RESPONSE),
    ):
        resp = client.post("/query", json={"prompt": "What's on my calendar?"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] == "query"
    assert data["human_summary"] == "You have 2 events this week."
    assert data["confidence"] == 0.92


def test_query_endpoint_passes_prompt_to_ollama() -> None:
    mock_ollama = AsyncMock(return_value=_GOOD_RESPONSE)
    with (
        patch("act.main._cal.list_calendars", return_value=[]),
        patch("act.main._cal.get_events", return_value=[]),
        patch("act.main._ollama_query", mock_ollama),
    ):
        client.post("/query", json={"prompt": "Find me a free slot"})

    call_kwargs = mock_ollama.call_args.kwargs
    assert call_kwargs["prompt"] == "Find me a free slot"


def test_query_endpoint_returns_503_when_ollama_unreachable() -> None:
    import httpx as _httpx

    with (
        patch("act.main._cal.list_calendars", return_value=[]),
        patch("act.main._cal.get_events", return_value=[]),
        patch(
            "act.main._ollama_query",
            new_callable=AsyncMock,
            side_effect=_httpx.ConnectError("refused"),
        ),
    ):
        resp = client.post("/query", json={"prompt": "hello"})

    assert resp.status_code == 503
    assert "Ollama" in resp.json()["detail"]


def test_query_endpoint_returns_502_on_bad_json() -> None:
    with (
        patch("act.main._cal.list_calendars", return_value=[]),
        patch("act.main._cal.get_events", return_value=[]),
        patch(
            "act.main._ollama_query",
            new_callable=AsyncMock,
            side_effect=ValueError("LLM returned invalid JSON after 3 attempts"),
        ),
    ):
        resp = client.post("/query", json={"prompt": "hello"})

    assert resp.status_code == 502


def test_query_endpoint_graceful_when_calendar_unavailable() -> None:
    """Calendar errors should not prevent the query — context is just empty."""
    with (
        patch(
            "act.main._cal.list_calendars",
            side_effect=__import__("subprocess").CalledProcessError(1, "osascript"),
        ),
        patch(
            "act.main._cal.get_events",
            side_effect=__import__("subprocess").CalledProcessError(1, "osascript"),
        ),
        patch("act.main._ollama_query", new_callable=AsyncMock, return_value=_GOOD_RESPONSE),
    ):
        resp = client.post("/query", json={"prompt": "hello"})

    assert resp.status_code == 200
