from __future__ import annotations

import subprocess
from datetime import date, datetime
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from act.main import app
from act.models import CalendarEvent
from act.scheduling import find_free_slots

DAY = date(2026, 4, 10)
WH_START = "09:00"
WH_END = "18:00"

client = TestClient(app)


def event(title: str, start: str, end: str) -> CalendarEvent:
    """Helper: build a CalendarEvent on DAY from HH:MM strings."""
    return CalendarEvent(
        id=title,
        title=title,
        start=datetime.fromisoformat(f"2026-04-10T{start}:00"),
        end=datetime.fromisoformat(f"2026-04-10T{end}:00"),
        calendar="Work",
    )


# ---------------------------------------------------------------------------
# Core algorithm — no mocking needed
# ---------------------------------------------------------------------------


def test_empty_day_returns_full_window() -> None:
    slots = find_free_slots([], DAY, 60, WH_START, WH_END)
    assert len(slots) == 1
    assert slots[0].start == datetime(2026, 4, 10, 9, 0)
    assert slots[0].end == datetime(2026, 4, 10, 18, 0)
    assert slots[0].duration_minutes == 540


def test_event_in_middle_splits_window() -> None:
    events = [event("Standup", "10:00", "10:30")]
    slots = find_free_slots(events, DAY, 60, WH_START, WH_END)
    assert len(slots) == 2
    assert slots[0].start == datetime(2026, 4, 10, 9, 0)
    assert slots[0].end == datetime(2026, 4, 10, 10, 0)
    assert slots[1].start == datetime(2026, 4, 10, 10, 30)
    assert slots[1].end == datetime(2026, 4, 10, 18, 0)


def test_slot_too_short_excluded() -> None:
    # Event at 09:00–09:45 leaves only 45 min before it; request 60 min
    events = [event("Early", "09:00", "09:45")]
    slots = find_free_slots(events, DAY, 60, WH_START, WH_END)
    assert all(s.duration_minutes >= 60 for s in slots)
    # Only the afternoon slot (09:45–18:00 = 495 min) qualifies
    assert len(slots) == 1
    assert slots[0].start == datetime(2026, 4, 10, 9, 45)


def test_overlapping_events_merged() -> None:
    events = [
        event("A", "10:00", "11:30"),
        event("B", "11:00", "12:00"),  # overlaps A
    ]
    slots = find_free_slots(events, DAY, 30, WH_START, WH_END)
    # Merged busy: 10:00–12:00 → gaps: 09:00–10:00 and 12:00–18:00
    assert len(slots) == 2
    assert slots[0].end == datetime(2026, 4, 10, 10, 0)
    assert slots[1].start == datetime(2026, 4, 10, 12, 0)


def test_adjacent_events_merged() -> None:
    events = [
        event("A", "10:00", "11:00"),
        event("B", "11:00", "12:00"),
    ]
    slots = find_free_slots(events, DAY, 30, WH_START, WH_END)
    # No gap between A and B
    assert all(
        not (s.start <= datetime(2026, 4, 10, 11, 0) <= s.end)
        for s in slots
    )


def test_event_outside_working_hours_ignored() -> None:
    events = [event("Early bird", "06:00", "08:00")]
    slots = find_free_slots(events, DAY, 60, WH_START, WH_END)
    assert len(slots) == 1
    assert slots[0].start == datetime(2026, 4, 10, 9, 0)


def test_event_spanning_full_day_leaves_no_slots() -> None:
    events = [event("All day", "09:00", "18:00")]
    slots = find_free_slots(events, DAY, 30, WH_START, WH_END)
    assert slots == []


def test_exact_fit_included() -> None:
    # Event 09:00–16:00, request 120 min → exactly 120 min left (16:00–18:00)
    events = [event("Long", "09:00", "16:00")]
    slots = find_free_slots(events, DAY, 120, WH_START, WH_END)
    assert len(slots) == 1
    assert slots[0].duration_minutes == 120


def test_duration_minutes_computed_correctly() -> None:
    events = [event("Standup", "09:00", "10:00")]
    slots = find_free_slots(events, DAY, 30, WH_START, WH_END)
    for slot in slots:
        expected = int((slot.end - slot.start).total_seconds() / 60)
        assert slot.duration_minutes == expected


# ---------------------------------------------------------------------------
# GET /free-slots endpoint
# ---------------------------------------------------------------------------


def test_endpoint_returns_slots() -> None:
    with patch(
        "act.main._cal.get_events",
        return_value=[event("Standup", "10:00", "10:30")],
    ):
        resp = client.get("/free-slots?date=2026-04-10&duration_minutes=60")

    assert resp.status_code == 200
    slots = resp.json()
    assert len(slots) == 2
    assert slots[0]["start"] == "2026-04-10T09:00:00"
    assert slots[1]["start"] == "2026-04-10T10:30:00"
    assert all("duration_minutes" in s for s in slots)


def test_endpoint_working_hours_override() -> None:
    with patch("act.main._cal.get_events", return_value=[]):
        resp = client.get(
            "/free-slots?date=2026-04-10&duration_minutes=60"
            "&working_hours_start=10:00&working_hours_end=12:00"
        )

    assert resp.status_code == 200
    slots = resp.json()
    assert len(slots) == 1
    assert slots[0]["start"] == "2026-04-10T10:00:00"
    assert slots[0]["end"] == "2026-04-10T12:00:00"


def test_endpoint_503_on_calendar_failure() -> None:
    with patch(
        "act.main._cal.get_events",
        side_effect=subprocess.CalledProcessError(1, "osascript"),
    ):
        resp = client.get("/free-slots?date=2026-04-10&duration_minutes=60")

    assert resp.status_code == 503
