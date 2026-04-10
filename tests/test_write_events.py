from __future__ import annotations

import subprocess
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from act.calendar.osascript import _esc, create_event, delete_event
from act.main import app
from act.models import CalendarEvent

client = TestClient(app)


# ---------------------------------------------------------------------------
# _esc — injection guard
# ---------------------------------------------------------------------------


def test_esc_leaves_plain_string() -> None:
    assert _esc("Work") == "Work"


def test_esc_escapes_double_quotes() -> None:
    assert _esc('say "hello"') == 'say \\"hello\\"'


def test_esc_escapes_backslash() -> None:
    assert _esc("C:\\path") == "C:\\\\path"


# ---------------------------------------------------------------------------
# create_event — AppleScript generation (mocked subprocess)
# ---------------------------------------------------------------------------


def _mock_run(uid: str = "NEW-UID-123") -> MagicMock:
    mock = MagicMock()
    mock.stdout = uid + "\n"
    return mock


def test_create_event_injects_title_and_calendar() -> None:
    with patch("act.calendar.osascript.subprocess.run", return_value=_mock_run()) as mock_run:
        create_event(
            title="Deep Work",
            start=datetime(2026, 4, 10, 10, 0),
            end=datetime(2026, 4, 10, 12, 0),
            calendar="Work",
        )
    script = mock_run.call_args[1]["input"]
    assert '"Deep Work"' in script
    assert '"Work"' in script


def test_create_event_calculates_time_as_seconds() -> None:
    with patch("act.calendar.osascript.subprocess.run", return_value=_mock_run()) as mock_run:
        create_event(
            title="X",
            start=datetime(2026, 4, 10, 9, 30),  # 9*3600 + 30*60 = 34200
            end=datetime(2026, 4, 10, 10, 0),   # 10*3600 = 36000
            calendar="Cal",
        )
    script = mock_run.call_args[1]["input"]
    assert "34200" in script
    assert "36000" in script


def test_create_event_injects_notes() -> None:
    with patch("act.calendar.osascript.subprocess.run", return_value=_mock_run()) as mock_run:
        create_event(
            title="X",
            start=datetime(2026, 4, 10, 10, 0),
            end=datetime(2026, 4, 10, 11, 0),
            calendar="Cal",
            notes="Prep slides",
        )
    script = mock_run.call_args[1]["input"]
    assert "Prep slides" in script


def test_create_event_no_notes_omits_set_description() -> None:
    with patch("act.calendar.osascript.subprocess.run", return_value=_mock_run()) as mock_run:
        create_event(
            title="X",
            start=datetime(2026, 4, 10, 10, 0),
            end=datetime(2026, 4, 10, 11, 0),
            calendar="Cal",
        )
    script = mock_run.call_args[1]["input"]
    assert "set description" not in script


def test_create_event_returns_uid() -> None:
    with patch("act.calendar.osascript.subprocess.run", return_value=_mock_run("ABC-123")):
        uid = create_event("X", datetime(2026, 4, 10, 10, 0), datetime(2026, 4, 10, 11, 0), "Cal")
    assert uid == "ABC-123"


# ---------------------------------------------------------------------------
# delete_event — AppleScript generation (mocked subprocess)
# ---------------------------------------------------------------------------


def test_delete_event_injects_event_id() -> None:
    mock = MagicMock()
    mock.stdout = "deleted\n"
    with patch("act.calendar.osascript.subprocess.run", return_value=mock) as mock_run:
        delete_event("MY-UID-456")
    script = mock_run.call_args[1]["input"]
    assert '"MY-UID-456"' in script


def test_delete_event_returns_result() -> None:
    for outcome in ("deleted", "not_found"):
        mock = MagicMock()
        mock.stdout = outcome + "\n"
        with patch("act.calendar.osascript.subprocess.run", return_value=mock):
            assert delete_event("X") == outcome


# ---------------------------------------------------------------------------
# POST /events endpoint
# ---------------------------------------------------------------------------

_EXISTING = CalendarEvent(
    id="E1",
    title="Existing",
    start=datetime(2026, 4, 10, 10, 0),
    end=datetime(2026, 4, 10, 11, 0),
    calendar="Work",
)

_NEW_PAYLOAD = {
    "title": "Deep Work",
    "start": "2026-04-10T14:00:00",
    "end": "2026-04-10T16:00:00",
    "calendar": "Work",
}


def test_post_events_creates_and_returns_event() -> None:
    with (
        patch("act.main._cal.get_events", return_value=[]),
        patch("act.main._cal.create_event", return_value="NEW-UID") as mock_create,
    ):
        resp = client.post("/events", json=_NEW_PAYLOAD)

    assert resp.status_code == 201
    data = resp.json()
    assert data["id"] == "NEW-UID"
    assert data["title"] == "Deep Work"
    assert data["calendar"] == "Work"
    mock_create.assert_called_once()


def test_post_events_409_on_conflict() -> None:
    with patch("act.main._cal.get_events", return_value=[_EXISTING]):
        resp = client.post("/events", json={
            "title": "Clash",
            "start": "2026-04-10T10:30:00",
            "end": "2026-04-10T11:30:00",
            "calendar": "Work",
        })

    assert resp.status_code == 409
    assert "conflicts" in resp.json()["detail"]


def test_post_events_force_skips_conflict_check() -> None:
    with (
        patch("act.main._cal.get_events", return_value=[_EXISTING]),
        patch("act.main._cal.create_event", return_value="NEW-UID"),
    ):
        resp = client.post("/events", json={**_NEW_PAYLOAD, "force": True,
                                             "start": "2026-04-10T10:30:00",
                                             "end": "2026-04-10T11:30:00"})

    assert resp.status_code == 201


def test_post_events_defaults_to_first_calendar() -> None:
    payload = {k: v for k, v in _NEW_PAYLOAD.items() if k != "calendar"}
    with (
        patch("act.main._cal.list_calendars", return_value=["Personal", "Work"]),
        patch("act.main._cal.get_events", return_value=[]),
        patch("act.main._cal.create_event", return_value="UID") as mock_create,
    ):
        resp = client.post("/events", json=payload)

    assert resp.status_code == 201
    assert mock_create.call_args.kwargs["calendar"] == "Personal"


def test_post_events_503_on_calendar_failure() -> None:
    with patch(
        "act.main._cal.get_events",
        side_effect=subprocess.CalledProcessError(1, "osascript"),
    ):
        resp = client.post("/events", json=_NEW_PAYLOAD)

    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# DELETE /events/{event_id} endpoint
# ---------------------------------------------------------------------------


def test_delete_event_success() -> None:
    with patch("act.main._cal.delete_event", return_value="deleted"):
        resp = client.request("DELETE", "/events/MY-UID", json={"confirm": True})

    assert resp.status_code == 204


def test_delete_event_404_when_not_found() -> None:
    with patch("act.main._cal.delete_event", return_value="not_found"):
        resp = client.request("DELETE", "/events/GHOST-UID", json={"confirm": True})

    assert resp.status_code == 404


def test_delete_event_400_without_confirm() -> None:
    with patch("act.main._cal.delete_event", return_value="deleted"):
        resp = client.request("DELETE", "/events/MY-UID", json={"confirm": False})

    assert resp.status_code == 400


def test_delete_event_503_on_calendar_failure() -> None:
    with patch(
        "act.main._cal.delete_event",
        side_effect=subprocess.CalledProcessError(1, "osascript"),
    ):
        resp = client.request("DELETE", "/events/MY-UID", json={"confirm": True})

    assert resp.status_code == 503
