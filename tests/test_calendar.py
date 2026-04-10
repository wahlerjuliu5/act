from __future__ import annotations

import subprocess
from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pytest

from act.calendar.osascript import get_events, list_calendars, parse_events_output

# ---------------------------------------------------------------------------
# parse_events_output — unit tests (no subprocess)
# ---------------------------------------------------------------------------

SAMPLE_OUTPUT = """\
---EVENT---
id:ABC123-DEF456
title:Team Standup
start:2026-04-10T09:00:00
end:2026-04-10T09:30:00
calendar:Work
location:Zoom
notes:Daily sync
---EVENT---
id:GHI789
title:Lunch
start:2026-04-10T12:00:00
end:2026-04-10T13:00:00
calendar:Personal
"""


def test_parse_two_events() -> None:
    events = parse_events_output(SAMPLE_OUTPUT)
    assert len(events) == 2

    standup = events[0]
    assert standup.id == "ABC123-DEF456"
    assert standup.title == "Team Standup"
    assert standup.start == datetime(2026, 4, 10, 9, 0, 0)
    assert standup.end == datetime(2026, 4, 10, 9, 30, 0)
    assert standup.calendar == "Work"
    assert standup.location == "Zoom"
    assert standup.notes == "Daily sync"

    lunch = events[1]
    assert lunch.title == "Lunch"
    assert lunch.location is None
    assert lunch.notes is None


def test_parse_empty_output() -> None:
    assert parse_events_output("") == []
    assert parse_events_output("   \n  ") == []


def test_parse_incomplete_block_skipped() -> None:
    # Missing required 'end' field — should be skipped
    bad_output = "---EVENT---\nid:X\ntitle:Y\nstart:2026-04-10T09:00:00\ncalendar:Work\n"
    events = parse_events_output(bad_output)
    assert events == []


def test_parse_notes_with_colon() -> None:
    # partition(":") splits at the FIRST colon only, so URLs in values are preserved
    output = (
        "---EVENT---\n"
        "id:X1\n"
        "title:Review\n"
        "start:2026-04-10T10:00:00\n"
        "end:2026-04-10T11:00:00\n"
        "calendar:Work\n"
        "notes:See https://example.com for details\n"
    )
    events = parse_events_output(output)
    assert len(events) == 1
    assert events[0].notes == "See https://example.com for details"


def test_parse_invalid_datetime_skipped() -> None:
    output = (
        "---EVENT---\n"
        "id:X1\n"
        "title:Bad\n"
        "start:not-a-date\n"
        "end:2026-04-10T11:00:00\n"
        "calendar:Work\n"
    )
    events = parse_events_output(output)
    assert events == []


# ---------------------------------------------------------------------------
# list_calendars — subprocess integration (mocked)
# ---------------------------------------------------------------------------


def test_list_calendars_parses_output() -> None:
    mock_result = MagicMock()
    mock_result.stdout = "Work\nPersonal\niCloud\n"
    with patch("act.calendar.osascript.subprocess.run", return_value=mock_result):
        cals = list_calendars()
    assert cals == ["Work", "Personal", "iCloud"]


def test_list_calendars_empty() -> None:
    mock_result = MagicMock()
    mock_result.stdout = "\n\n"
    with patch("act.calendar.osascript.subprocess.run", return_value=mock_result):
        cals = list_calendars()
    assert cals == []


# ---------------------------------------------------------------------------
# get_events — subprocess integration (mocked)
# ---------------------------------------------------------------------------


def test_get_events_passes_correct_dates() -> None:
    mock_result = MagicMock()
    mock_result.stdout = ""
    with patch("act.calendar.osascript.subprocess.run", return_value=mock_result) as mock_run:
        get_events(date(2026, 4, 10), date(2026, 4, 17))

    script = mock_run.call_args.kwargs["input"]
    assert "set year of startDate to 2026" in script
    assert "set month of startDate to 4" in script
    assert "set day of startDate to 10" in script
    assert "set year of endDate to 2026" in script
    assert "set day of endDate to 17" in script


def test_get_events_includes_calendar_filter() -> None:
    mock_result = MagicMock()
    mock_result.stdout = ""
    with patch("act.calendar.osascript.subprocess.run", return_value=mock_result) as mock_run:
        get_events(date(2026, 4, 10), date(2026, 4, 17), calendar_filter="Work")

    script = mock_run.call_args[1]["input"]
    assert 'if calName is "Work" then' in script
    assert "end if" in script


def test_get_events_no_filter_no_conditional() -> None:
    mock_result = MagicMock()
    mock_result.stdout = ""
    with patch("act.calendar.osascript.subprocess.run", return_value=mock_result) as mock_run:
        get_events(date(2026, 4, 10), date(2026, 4, 17), calendar_filter=None)

    script = mock_run.call_args[1]["input"]
    assert "if calName is" not in script


def test_get_events_strips_quotes_from_filter() -> None:
    """Injection guard: double quotes are stripped from calendar filter."""
    mock_result = MagicMock()
    mock_result.stdout = ""
    with patch("act.calendar.osascript.subprocess.run", return_value=mock_result) as mock_run:
        get_events(date(2026, 4, 10), date(2026, 4, 17), calendar_filter='Work"Inject')

    script = mock_run.call_args[1]["input"]
    assert 'if calName is "WorkInject" then' in script


# ---------------------------------------------------------------------------
# /events endpoint — integration via TestClient
# ---------------------------------------------------------------------------


def test_events_endpoint_returns_events() -> None:
    from fastapi.testclient import TestClient

    from act.main import app

    sample_events_output = (
        "---EVENT---\n"
        "id:E1\ntitle:Focus Block\n"
        "start:2026-04-10T10:00:00\nend:2026-04-10T12:00:00\n"
        "calendar:Work\n"
    )
    mock_result = MagicMock()
    mock_result.stdout = sample_events_output

    with patch("act.calendar.osascript.subprocess.run", return_value=mock_result):
        client = TestClient(app)
        resp = client.get("/events?start=2026-04-10&end=2026-04-10")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["title"] == "Focus Block"
    assert data[0]["calendar"] == "Work"


def test_events_endpoint_rejects_inverted_range() -> None:
    from fastapi.testclient import TestClient

    from act.main import app

    client = TestClient(app)
    resp = client.get("/events?start=2026-04-17&end=2026-04-10")
    assert resp.status_code == 400
