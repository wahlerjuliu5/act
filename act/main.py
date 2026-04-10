from __future__ import annotations

import subprocess
from datetime import date, datetime, timedelta

import httpx
from fastapi import FastAPI, HTTPException, Query

import act.calendar.osascript as _cal
from act.config import ActConfig, load_config
from act.llm.ollama import query as _ollama_query
from act.llm.prompt import build_system_prompt
from act.models import (
    CalendarEvent,
    CreateEventRequest,
    DeleteEventRequest,
    FreeSlot,
    HealthResponse,
    QueryRequest,
    QueryResponse,
)
from act.scheduling import find_free_slots

app = FastAPI(title="act", version="0.1.0")
_config: ActConfig = load_config()


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    try:
        calendars = _cal.list_calendars()
        return HealthResponse(
            status="ok",
            calendar_backend=_config.calendar_backend,
            calendar_count=len(calendars),
        )
    except subprocess.CalledProcessError:
        return HealthResponse(
            status="degraded",
            calendar_backend=_config.calendar_backend,
            calendar_count=0,
        )


@app.get("/events", response_model=list[CalendarEvent])
def events(
    start: date | None = Query(default=None, description="Start date (YYYY-MM-DD)"),
    end: date | None = Query(default=None, description="End date (YYYY-MM-DD)"),
    calendar: str | None = Query(default=None, description="Filter by calendar name"),
) -> list[CalendarEvent]:
    today = date.today()
    start = start or today
    end = end or (today + timedelta(days=_config.event_lookahead_days))

    if end < start:
        raise HTTPException(status_code=400, detail="end must be >= start")

    try:
        return _cal.get_events(start, end, calendar_filter=calendar)
    except subprocess.CalledProcessError as exc:
        raise HTTPException(status_code=503, detail=(exc.stderr or "").strip() or "Calendar backend unavailable") from exc


@app.get("/free-slots", response_model=list[FreeSlot])
def free_slots(
    day: date = Query(..., alias="date", description="Date to search (YYYY-MM-DD)"),
    duration_minutes: int = Query(default=60, description="Required slot length in minutes"),
    working_hours_start: str | None = Query(default=None, description="Override working hours start (HH:MM)"),
    working_hours_end: str | None = Query(default=None, description="Override working hours end (HH:MM)"),
) -> list[FreeSlot]:
    wh_start = working_hours_start or _config.working_hours.start
    wh_end = working_hours_end or _config.working_hours.end

    try:
        events = _cal.get_events(day, day)
    except subprocess.CalledProcessError as exc:
        raise HTTPException(status_code=503, detail=(exc.stderr or "").strip() or "Calendar backend unavailable") from exc

    return find_free_slots(events, day, duration_minutes, wh_start, wh_end)


@app.post("/events", response_model=CalendarEvent, status_code=201)
def create_event(req: CreateEventRequest) -> CalendarEvent:
    # Resolve calendar name — default to first available
    calendar_name = req.calendar
    if not calendar_name:
        try:
            cals = _cal.list_calendars()
            calendar_name = cals[0] if cals else "Calendar"
        except subprocess.CalledProcessError:
            calendar_name = "Calendar"

    # Conflict check
    if not req.force:
        try:
            existing = _cal.get_events(req.start.date(), req.end.date())
        except subprocess.CalledProcessError as exc:
            raise HTTPException(status_code=503, detail=(exc.stderr or "").strip() or "Calendar backend unavailable") from exc
        conflicts = [e for e in existing if e.start < req.end and e.end > req.start]
        if conflicts:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "Event conflicts with existing events",
                    "conflicts": [e.model_dump(mode="json") for e in conflicts],
                },
            )

    try:
        event_id = _cal.create_event(
            title=req.title,
            start=req.start,
            end=req.end,
            calendar=calendar_name,
            notes=req.notes,
        )
    except subprocess.CalledProcessError as exc:
        raise HTTPException(status_code=503, detail=(exc.stderr or "").strip() or "Calendar backend unavailable") from exc

    return CalendarEvent(
        id=event_id,
        title=req.title,
        start=req.start,
        end=req.end,
        calendar=calendar_name,
        notes=req.notes,
    )


@app.delete("/events/{event_id}", status_code=204)
def delete_event(event_id: str, req: DeleteEventRequest) -> None:
    if not req.confirm:
        raise HTTPException(status_code=400, detail="confirm must be true to delete an event")

    try:
        result = _cal.delete_event(event_id)
    except subprocess.CalledProcessError as exc:
        raise HTTPException(status_code=503, detail=(exc.stderr or "").strip() or "Calendar backend unavailable") from exc

    if result == "not_found":
        raise HTTPException(status_code=404, detail=f"Event {event_id!r} not found")


@app.post("/query", response_model=QueryResponse)
async def query_endpoint(req: QueryRequest) -> QueryResponse:
    today = date.today()

    try:
        calendars = _cal.list_calendars()
    except subprocess.CalledProcessError:
        calendars = []

    try:
        events = _cal.get_events(today, today + timedelta(days=_config.event_lookahead_days))
    except subprocess.CalledProcessError:
        events = []

    system_prompt = build_system_prompt(
        calendars=calendars,
        events=events,
        working_hours_start=_config.working_hours.start,
        working_hours_end=_config.working_hours.end,
    )

    try:
        result = await _ollama_query(
            prompt=req.prompt,
            system_prompt=system_prompt,
            model=_config.ollama_model,
            host=_config.ollama_host,
        )
    except httpx.ConnectError:
        raise HTTPException(
            status_code=503,
            detail=f"Ollama not reachable at {_config.ollama_host}",
        )
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    # Correct intent mismatch: user said "schedule/book/add X" but LLM returned find_slots.
    # Auto-schedule at the first free slot on the requested day.
    if _is_scheduling_prompt(req.prompt) and result.intent == "find_slots" and result.proposed_action:
        result = _correct_find_slots_to_create(result, req.prompt)

    # Always preview-resolve the create times so the CLI shows accurate times
    # before asking the user to confirm (even when execute=False).
    if result.intent == "create" and result.proposed_action:
        result = _preview_create_times(result, events)

    if req.execute and result.proposed_action:
        result.execution_result = _execute_action(
            result.intent, result.proposed_action, original_prompt=req.prompt
        )

    return result


# Strips common scheduling verbs from the front of a prompt to recover the event title.
# e.g. "schedule RL Paper tomorrow for 3 hours" → "RL Paper"
import re as _re
_SCHED_PREFIX = _re.compile(
    r"^(please\s+)?(schedule|add|create|book|set up|block out?|put in|add to calendar)\s+(an?\s+|me\s+a\s+)?",
    _re.IGNORECASE,
)
_TIME_SUFFIX = _re.compile(
    r"\s+(tomorrow|today|this\s+\w+|next\s+\w+|on\s+\w+|at\s+[\d:]+|for\s+\d+\s*\w+|from\s+\d+).*$",
    _re.IGNORECASE,
)
# Scheduling-intent verbs used for intent-mismatch correction
_SCHED_VERB = _re.compile(
    r"\b(schedule|book|add|create|block|set\s+up|put\s+on|put\s+in)\b",
    _re.IGNORECASE,
)


def _title_from_prompt(prompt: str) -> str:
    t = _SCHED_PREFIX.sub("", prompt.strip())
    t = _TIME_SUFFIX.sub("", t).strip()
    return t or prompt[:60]


def _is_scheduling_prompt(prompt: str) -> bool:
    """Return True if the prompt contains a scheduling verb (schedule, book, add, …)."""
    return bool(_SCHED_VERB.search(prompt))


def _correct_find_slots_to_create(result: QueryResponse, prompt: str) -> QueryResponse:
    """When LLM returns find_slots for a scheduling-verb prompt, convert to create.

    Finds the first free slot on the requested day using the duration from the
    LLM's proposed_action, then returns a corrected QueryResponse with intent=create.
    If no free slot is available the original result is returned unchanged so the
    caller still gets a useful (empty-slots) response.
    """
    action = result.proposed_action or {}
    date_str = action.get("date", date.today().isoformat())
    duration = int(action.get("duration_minutes", 60))
    title = _title_from_prompt(prompt)

    try:
        day = date.fromisoformat(date_str)
        free = find_free_slots(
            _cal.get_events(day, day),
            day,
            duration,
            _config.working_hours.start,
            _config.working_hours.end,
        )
    except Exception:
        return result  # can't correct — keep original

    if not free:
        return result  # no free slot; keep find_slots so caller sees empty slots

    slot_start = free[0].start
    slot_end = slot_start + timedelta(minutes=duration)

    return QueryResponse(
        intent="create",
        proposed_action={
            "title": title,
            "start": slot_start.isoformat(),
            "end": slot_end.isoformat(),
            "calendar": action.get("calendar"),
        },
        human_summary=result.human_summary,
        confidence=result.confidence,
    )


def _preview_create_times(result: QueryResponse, context_events: list[CalendarEvent]) -> QueryResponse:
    """Resolve conflicts in proposed_action.start/end before the user sees them.

    Uses already-fetched events (filtered to the target day) so no extra
    calendar call is needed.  If the proposed slot is free the result is
    unchanged; if it conflicts the times are updated to the first free slot,
    matching exactly what _execute_action will do later.
    """
    from act.llm.normalize import normalize_proposed_action

    action = normalize_proposed_action("create", result.proposed_action or {})
    if "start" not in action or "end" not in action:
        return result

    try:
        start = datetime.fromisoformat(action["start"])
        end = datetime.fromisoformat(action["end"])
        duration = int((end - start).total_seconds() / 60)

        day_events = [e for e in context_events if e.start.date() == start.date()]
        conflicts = [e for e in day_events if e.start < end and e.end > start]

        if conflicts:
            free = find_free_slots(
                day_events, start.date(), duration,
                _config.working_hours.start, _config.working_hours.end,
            )
            if free:
                start = free[0].start
                end = start + timedelta(minutes=duration)
                updated = dict(action)
                updated["start"] = start.isoformat()
                updated["end"] = end.isoformat()
                return QueryResponse(
                    intent=result.intent,
                    proposed_action=updated,
                    human_summary=result.human_summary,
                    confidence=result.confidence,
                )
    except Exception:
        pass

    return result


def _execute_action(intent: str, action: dict, original_prompt: str = "") -> dict:
    """Dispatch a proposed_action from the LLM to the appropriate calendar operation."""
    from act.llm.normalize import normalize_proposed_action

    # Always normalize here — this is the last line of defence regardless of
    # what the LLM returned or whether ollama.py's normalization ran.
    action = normalize_proposed_action(intent, action)

    try:
        if intent == "create":
            # Validate / recover required fields before touching the calendar
            missing = [f for f in ("start", "end") if f not in action]
            if missing:
                return {"status": "error", "detail": f"LLM did not provide: {', '.join(missing)}. Try again."}

            start = datetime.fromisoformat(action["start"])
            end = datetime.fromisoformat(action["end"])
            duration = int((end - start).total_seconds() / 60)
            title = action.get("title") or _title_from_prompt(original_prompt)

            # Validate calendar name — LLM sometimes picks names that don't exist
            requested_cal = action.get("calendar")
            available_cals = _cal.list_calendars()
            if requested_cal and requested_cal in available_cals:
                calendar_name = requested_cal
            else:
                calendar_name = available_cals[0] if available_cals else "Calendar"

            existing = _cal.get_events(start.date(), end.date())
            conflicts = [e for e in existing if e.start < end and e.end > start]
            if conflicts:
                # Try to reschedule at the first free slot on the same day
                free = find_free_slots(
                    existing, start.date(), duration,
                    _config.working_hours.start, _config.working_hours.end,
                )
                if free:
                    start = free[0].start
                    end = start + timedelta(minutes=duration)
                else:
                    conflict_titles = ", ".join(e.title for e in conflicts)
                    return {
                        "status": "conflict",
                        "conflicts": [e.model_dump(mode="json") for e in conflicts],
                        "hint": f"Conflicts with: {conflict_titles}. No free {duration}-minute slot on that day.",
                    }

            event_id = _cal.create_event(
                title=title,
                start=start,
                end=end,
                calendar=calendar_name,
                notes=action.get("notes"),
            )
            return {
                "status": "created",
                "event_id": event_id,
                "title": title,
                "calendar": calendar_name,
                "start": start.isoformat(),
                "end": end.isoformat(),
            }

        if intent == "delete":
            event_id = action.get("event_id") or ""
            title = action.get("title", "")

            if not event_id:
                # Title-based fallback: find and delete all upcoming events matching the title
                if not title:
                    return {"status": "error", "detail": "proposed_action missing event_id and title"}
                lookahead = date.today() + timedelta(days=_config.event_lookahead_days)
                all_events = _cal.get_events(date.today(), lookahead)
                matches = [e for e in all_events if title.lower() in e.title.lower()]
                if not matches:
                    return {"status": "not_found", "detail": f"No upcoming event found matching '{title}'"}
                deleted = []
                for e in matches:
                    outcome = _cal.delete_event(e.id)
                    deleted.append({"title": e.title, "start": e.start.isoformat(), "status": outcome})
                return {"status": "deleted", "count": len(deleted), "deleted": deleted}

            outcome = _cal.delete_event(event_id)
            return {"status": outcome}  # "deleted" or "not_found"

        if intent == "find_slots":
            day = date.fromisoformat(action["date"])
            duration = int(action.get("duration_minutes", 60))
            slots = find_free_slots(
                _cal.get_events(day, day),
                day,
                duration,
                _config.working_hours.start,
                _config.working_hours.end,
            )
            return {
                "status": "ok",
                "slots": [s.model_dump(mode="json") for s in slots],
            }

    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip() or "Calendar backend unavailable"
        return {"status": "error", "detail": detail}
    except (KeyError, ValueError) as exc:
        return {"status": "error", "detail": str(exc)}

    return {"status": "no_action", "detail": f"intent '{intent}' requires no execution"}



