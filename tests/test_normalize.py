from __future__ import annotations

from datetime import datetime

import pytest

from act.llm.normalize import _to_iso, normalize_proposed_action


# ---------------------------------------------------------------------------
# _to_iso — datetime string coercion
# ---------------------------------------------------------------------------


def test_iso_passthrough() -> None:
    assert _to_iso("2026-04-15T10:00:00") == "2026-04-15T10:00:00"


def test_iso_with_space_separator() -> None:
    assert _to_iso("2026-04-15 10:00:00") == "2026-04-15T10:00:00"


def test_strips_timezone_abbreviation() -> None:
    result = _to_iso("2026-04-15T10:00:00 CEST")
    assert result == "2026-04-15T10:00:00"


def test_strips_cest_from_ctime_format() -> None:
    # The exact format the LLM returned in the user's test
    result = _to_iso("Sat Apr 15 12:00:00 CEST")
    assert result == f"2026-04-15T12:00:00"


def test_ctime_no_year_uses_current_year() -> None:
    result = _to_iso("Sat Apr 15 12:00:00")
    dt = datetime.fromisoformat(result)
    assert dt.year == datetime.now().year
    assert dt.month == 4
    assert dt.day == 15
    assert dt.hour == 12


def test_ctime_with_year() -> None:
    result = _to_iso("Sat Apr 15 14:30:00 2026")
    assert result == "2026-04-15T14:30:00"


def test_long_month_format() -> None:
    result = _to_iso("April 15, 2026 10:00:00")
    assert result == "2026-04-15T10:00:00"


def test_short_month_format() -> None:
    result = _to_iso("Apr 15, 2026 10:00")
    assert result == "2026-04-15T10:00:00"


def test_unknown_format_returned_as_is() -> None:
    garbage = "whenever I feel like it"
    assert _to_iso(garbage) == garbage


# ---------------------------------------------------------------------------
# normalize_proposed_action — field aliases
# ---------------------------------------------------------------------------


def test_event_name_becomes_title() -> None:
    action = {"event_name": "RL Paper", "start": "2026-04-15T12:00:00",
               "end": "2026-04-15T15:00:00", "calendar": "Work"}
    result = normalize_proposed_action("create", action)
    assert result["title"] == "RL Paper"
    assert "event_name" not in result


def test_start_time_becomes_start() -> None:
    action = {"title": "X", "start_time": "2026-04-15T10:00:00",
               "end_time": "2026-04-15T11:00:00"}
    result = normalize_proposed_action("create", action)
    assert "start" in result
    assert "end" in result
    assert "start_time" not in result
    assert "end_time" not in result


def test_description_becomes_notes() -> None:
    action = {"title": "X", "start": "2026-04-15T10:00:00",
               "end": "2026-04-15T11:00:00", "description": "Prep slides"}
    result = normalize_proposed_action("create", action)
    assert result["notes"] == "Prep slides"
    assert "description" not in result


def test_exact_llm_response_from_user() -> None:
    """Reproduce the exact bad output the user's LLM returned."""
    action = {
        "event_name": "RL Paper Meeting",
        "start_time": "Sat Apr 15 12:00:00 CEST",
        "end_time": "Sat Apr 15 15:00:00 CEST",
    }
    result = normalize_proposed_action("create", action)
    assert result["title"] == "RL Paper Meeting"
    assert result["start"] == "2026-04-15T12:00:00"
    assert result["end"] == "2026-04-15T15:00:00"


def test_canonical_fields_not_overwritten() -> None:
    """If the canonical name is already present, don't overwrite with alias."""
    action = {"title": "Correct", "event_name": "Wrong",
               "start": "2026-04-15T10:00:00", "end": "2026-04-15T11:00:00"}
    result = normalize_proposed_action("create", action)
    assert result["title"] == "Correct"


def test_delete_uid_becomes_event_id() -> None:
    action = {"uid": "ABC-123", "title": "Old Meeting"}
    result = normalize_proposed_action("delete", action)
    assert result["event_id"] == "ABC-123"
    assert "uid" not in result


def test_find_slots_duration_alias() -> None:
    action = {"date": "2026-04-15", "duration": 90}
    result = normalize_proposed_action("find_slots", action)
    assert result["duration_minutes"] == 90
    assert "duration" not in result


def test_unknown_intent_passthrough() -> None:
    action = {"foo": "bar"}
    assert normalize_proposed_action("update", action) == {"foo": "bar"}
