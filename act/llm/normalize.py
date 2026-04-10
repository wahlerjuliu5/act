from __future__ import annotations

import re
from datetime import datetime

# ---------------------------------------------------------------------------
# Field-name aliases the LLM might use instead of the canonical names
# ---------------------------------------------------------------------------

_ALIASES: dict[str, dict[str, list[str]]] = {
    "create": {
        "title":    ["event_name", "name", "event_title", "summary", "subject",
                     "meeting_name", "task", "event", "topic"],
        "start":    ["start_time", "start_datetime", "begin", "begin_time", "start_date"],
        "end":      ["end_time", "end_datetime", "finish", "finish_time", "end_date"],
        "calendar": ["calendar_name", "cal", "calendar_id"],
        "notes":    ["description", "note", "details", "body"],
    },
    "delete": {
        "event_id": ["uid", "id", "event_uid", "event_id"],
        "title":    ["event_name", "name", "event_title", "summary"],
    },
    "find_slots": {
        "date":             ["day", "target_date", "search_date"],
        "duration_minutes": ["duration", "minutes", "length_minutes", "length", "duration_mins"],
    },
}

# Datetime formats to try, in preference order
_DT_FORMATS = [
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d %H:%M",
    "%a %b %d %H:%M:%S %Y",   # Sat Apr 15 12:00:00 2026
    "%a %b %d %H:%M %Y",      # Sat Apr 15 12:00 2026
    "%B %d, %Y %H:%M:%S",     # April 15, 2026 12:00:00
    "%B %d, %Y %H:%M",
    "%b %d, %Y %H:%M:%S",     # Apr 15, 2026 12:00:00
    "%b %d, %Y %H:%M",
    "%d %B %Y %H:%M:%S",
    "%d %B %Y %H:%M",
]

# Weekday prefix pattern for ctime-style strings missing the year
_CTIME_NO_YEAR = re.compile(
    r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+\w{3}\s+\d{1,2}\s+\d{2}:\d{2}(:\d{2})?$",
    re.IGNORECASE,
)


def normalize_proposed_action(intent: str, action: dict) -> dict:
    """Canonicalize field names and datetime strings in a proposed_action dict."""
    if intent not in _ALIASES:
        return action

    result = dict(action)

    # Rename aliased fields to canonical names
    for canonical, aliases in _ALIASES[intent].items():
        if canonical not in result:
            for alias in aliases:
                if alias in result:
                    result[canonical] = result.pop(alias)
                    break

    # Coerce datetime strings for create events
    if intent == "create":
        for field in ("start", "end"):
            if field in result and isinstance(result[field], str):
                result[field] = _to_iso(result[field])

    return result


def _to_iso(s: str) -> str:
    """Best-effort conversion of an arbitrary datetime string to ISO 8601."""
    s = s.strip()

    # Strip trailing timezone abbreviations: CEST, PST, UTC, UTC+2, GMT+01:00
    s = re.sub(r"\s+[A-Z]{2,5}([+-]\d+(:\d+)?)?$", "", s).strip()
    s = re.sub(r"\s+UTC[+-]\d+(:\d+)?$", "", s).strip()

    # ctime-like without year: "Sat Apr 15 12:00:00" → append current year
    if _CTIME_NO_YEAR.match(s):
        s = f"{s} {datetime.now().year}"

    for fmt in _DT_FORMATS:
        try:
            return datetime.strptime(s, fmt).isoformat()
        except ValueError:
            continue

    return s  # return as-is; downstream will surface a clear error
