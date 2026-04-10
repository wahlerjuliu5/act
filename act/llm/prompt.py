from __future__ import annotations

from datetime import datetime

from act.models import CalendarEvent

_RESPONSE_SCHEMA = """\
{
  "intent": "<query|create|delete|update|find_slots>",
  "proposed_action": <object with action parameters, or null>,
  "human_summary": "<plain language response for the user>",
  "confidence": <float between 0.0 and 1.0>
}"""


def build_system_prompt(
    calendars: list[str],
    events: list[CalendarEvent],
    working_hours_start: str,
    working_hours_end: str,
) -> str:
    now = datetime.now()
    date_str = now.strftime("%A, %B %d, %Y")
    time_str = now.strftime("%H:%M")

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
        f"Calendars: {cal_list}\n"
        f"Working hours: {working_hours_start} – {working_hours_end}\n\n"
        f"Upcoming events:\n{event_lines}\n\n"
        f"Respond ONLY with valid JSON matching this exact schema — no other text:\n"
        f"{_RESPONSE_SCHEMA}"
    )
