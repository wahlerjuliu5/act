from __future__ import annotations

from datetime import datetime, timedelta

from act.models import CalendarEvent

_RESPONSE_SCHEMA = """\
CHOOSE YOUR INTENT — read the rules carefully before deciding:

"create"     : User wants to ADD a new event to the calendar.
               Trigger words: schedule, book, add, create, block, put on my calendar, set up.
               "for N hours" or "for N minutes" means the EVENT DURATION — NOT a slot to search for.
               NEVER use find_slots when these words appear; always use create.
               ✓ "schedule RL Paper tomorrow for 3 hours"  → create (title="RL Paper", duration=3 h)
               ✓ "book dentist on Friday at 2 pm"          → create
               ✓ "add team sync at 10 am for 1 hour"       → create

"find_slots" : User ONLY asks for free time or availability — no specific event to add.
               Trigger phrases: "when am I free", "find a gap", "show available times",
               "what slots exist", "is there time for".
               ✗ NEVER use find_slots if the prompt contains schedule, book, add, create,
                 or names a specific event to put on the calendar.

"delete"     : User wants to REMOVE an existing event (needs event_id).
"query"      : User asks what is already on the calendar — read-only, no changes.
"update"     : User wants to CHANGE details of an existing event.

Use EXACTLY these field names — no variations, no extras.

{"intent": "...", "proposed_action": ..., "human_summary": "...", "confidence": 0.0}

proposed_action shape per intent:

"create"     -> {"title": "...", "start": "YYYY-MM-DDTHH:MM:SS", "end": "YYYY-MM-DDTHH:MM:SS", "calendar": "<pick from the calendars listed above>", "notes": null}
"delete"     -> {"event_id": "...", "title": "..."}
"find_slots" -> {"date": "YYYY-MM-DD", "duration_minutes": 60}
"query"      -> null
"update"     -> null

IMPORTANT RULES:
- Use "title" not "event_name", "name", or "summary"
- Use "start" not "start_time" or "begin"
- Use "end" not "end_time" or "finish"
- Dates MUST be ISO 8601: YYYY-MM-DDTHH:MM:SS — no timezones, no other formats
- "calendar" must be one of the calendars listed above
- For "create": compute end = start + duration

EXAMPLE for create (schedule X for N hours):
{"intent": "create", "proposed_action": {"title": "RL Paper", "start": "2026-04-11T09:00:00", "end": "2026-04-11T12:00:00", "calendar": "Work", "notes": null}, "human_summary": "Scheduled RL Paper on April 11 from 9 am to 12 pm.", "confidence": 0.95}"""


def build_system_prompt(
    calendars: list[str],
    events: list[CalendarEvent],
    working_hours_start: str,
    working_hours_end: str,
) -> str:
    now = datetime.now()
    today = now.date()
    date_str = now.strftime("%A, %B %d, %Y")
    time_str = now.strftime("%H:%M")

    # Explicit date anchors so the model never has to do arithmetic
    date_anchors = "\n".join(
        f"{label}: {(today + timedelta(days=offset)).strftime('%A, %B %d, %Y')} "
        f"({(today + timedelta(days=offset)).isoformat()})"
        for label, offset in [
            ("Today", 0), ("Tomorrow", 1),
            ("Day after tomorrow", 2),
            ("In 3 days", 3), ("In 4 days", 4),
            ("In 5 days", 5), ("In 6 days", 6), ("In 7 days", 7),
        ]
    )

    cal_list = ", ".join(calendars) if calendars else "none"

    if events:
        event_lines = "\n".join(
            f"- {e.title}"
            f" | {e.start.strftime('%a %b %d')} {e.start.strftime('%H:%M')}–{e.end.strftime('%H:%M')}"
            f" [{e.calendar}]"
            + (f" @ {e.location}" if e.location else "")
            for e in sorted(events, key=lambda e: e.start)
        )
    else:
        event_lines = "No events in this period."

    return (
        f"You are a calendar scheduling assistant. "
        f"Today is {date_str} at {time_str}.\n\n"
        f"Date reference (use these ISO dates exactly — do NOT compute dates yourself):\n"
        f"{date_anchors}\n\n"
        f"Calendars: {cal_list}\n"
        f"Working hours: {working_hours_start} – {working_hours_end}\n\n"
        f"Upcoming events:\n{event_lines}\n\n"
        f"Respond ONLY with valid JSON matching this exact schema — no other text:\n"
        f"{_RESPONSE_SCHEMA}"
    )
