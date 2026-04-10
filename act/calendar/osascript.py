from __future__ import annotations

import subprocess
from datetime import date, datetime

from act.models import CalendarEvent

# ---------------------------------------------------------------------------
# AppleScript templates
# ---------------------------------------------------------------------------

_LIST_CALENDARS_SCRIPT = """\
tell application "Calendar"
    set output to ""
    repeat with cal in calendars
        set output to output & name of cal & linefeed
    end repeat
    return output
end tell
"""

_GET_EVENTS_SCRIPT_TMPL = """\
tell application "Calendar"
    set startDate to current date
    set year of startDate to {start_year}
    set month of startDate to {start_month}
    set day of startDate to {start_day}
    set time of startDate to 0

    set endDate to current date
    set year of endDate to {end_year}
    set month of endDate to {end_month}
    set day of endDate to {end_day}
    set time of endDate to 86399

    set output to ""

    repeat with cal in calendars
        set calName to name of cal
        {filter_open}
        set matchingEvents to (events of cal whose start date >= startDate and start date <= endDate)
        repeat with ev in matchingEvents
            try
                set evId to uid of ev
                set evTitle to summary of ev
                set sd to start date of ev
                set ed to end date of ev

                set syr to (year of sd) as string
                set smo to (month of sd as integer) as string
                if length of smo is 1 then set smo to "0" & smo
                set sdy to (day of sd) as string
                if length of sdy is 1 then set sdy to "0" & sdy
                set shr to (hours of sd) as string
                if length of shr is 1 then set shr to "0" & shr
                set smn to (minutes of sd) as string
                if length of smn is 1 then set smn to "0" & smn

                set eyr to (year of ed) as string
                set emo to (month of ed as integer) as string
                if length of emo is 1 then set emo to "0" & emo
                set edy to (day of ed) as string
                if length of edy is 1 then set edy to "0" & edy
                set ehr to (hours of ed) as string
                if length of ehr is 1 then set ehr to "0" & ehr
                set emn to (minutes of ed) as string
                if length of emn is 1 then set emn to "0" & emn

                set output to output & "---EVENT---" & linefeed
                set output to output & "id:" & evId & linefeed
                set output to output & "title:" & evTitle & linefeed
                set output to output & "start:" & syr & "-" & smo & "-" & sdy & "T" & shr & ":" & smn & ":00" & linefeed
                set output to output & "end:" & eyr & "-" & emo & "-" & edy & "T" & ehr & ":" & emn & ":00" & linefeed
                set output to output & "calendar:" & calName & linefeed
                try
                    set loc to location of ev
                    if loc is not missing value and loc is not "" then
                        set output to output & "location:" & loc & linefeed
                    end if
                end try
                try
                    set desc to description of ev
                    if desc is not missing value and desc is not "" then
                        set output to output & "notes:" & desc & linefeed
                    end if
                end try
            end try
        end repeat
        {filter_close}
    end repeat

    return output
end tell
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def list_calendars() -> list[str]:
    """Return names of all calendars in macOS Calendar.app."""
    result = _run_script(_LIST_CALENDARS_SCRIPT)
    return [line for line in result.splitlines() if line.strip()]


def get_events(
    start: date,
    end: date,
    calendar_filter: str | None = None,
) -> list[CalendarEvent]:
    """Return events in [start, end] from macOS Calendar.app."""
    if calendar_filter:
        # Basic sanitisation: strip double-quotes to avoid AppleScript injection
        safe_filter = calendar_filter.replace('"', "")
        filter_open = f'if calName is "{safe_filter}" then'
        filter_close = "end if"
    else:
        filter_open = ""
        filter_close = ""

    script = _GET_EVENTS_SCRIPT_TMPL.format(
        start_year=start.year,
        start_month=start.month,
        start_day=start.day,
        end_year=end.year,
        end_month=end.month,
        end_day=end.day,
        filter_open=filter_open,
        filter_close=filter_close,
    )
    output = _run_script(script)
    return parse_events_output(output)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_script(script: str) -> str:
    result = subprocess.run(
        ["osascript", "-"],
        input=script,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def parse_events_output(output: str) -> list[CalendarEvent]:
    """Parse the delimited text output produced by the get_events AppleScript."""
    events: list[CalendarEvent] = []
    for block in output.split("---EVENT---"):
        block = block.strip()
        if not block:
            continue
        fields: dict[str, str] = {}
        for line in block.splitlines():
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            if key:
                # Only store first occurrence (preserves colon-containing values
                # like URLs in notes when field names don't contain colons)
                if key not in fields:
                    fields[key] = value
                else:
                    # Continuation of a multi-segment value (e.g. time in ISO string)
                    # This shouldn't happen with our format, but guard anyway
                    fields[key] = fields[key] + ":" + value

        required = ("id", "title", "start", "end", "calendar")
        if not all(k in fields for k in required):
            continue

        try:
            events.append(
                CalendarEvent(
                    id=fields["id"],
                    title=fields["title"],
                    start=datetime.fromisoformat(fields["start"]),
                    end=datetime.fromisoformat(fields["end"]),
                    calendar=fields["calendar"],
                    location=fields.get("location") or None,
                    notes=fields.get("notes") or None,
                )
            )
        except (ValueError, KeyError):
            continue

    return events
