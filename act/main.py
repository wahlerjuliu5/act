from __future__ import annotations

import subprocess
from datetime import date, timedelta

import httpx
from fastapi import FastAPI, HTTPException, Query

import act.calendar.osascript as _cal
from act.config import ActConfig, load_config
from act.llm.ollama import query as _ollama_query
from act.llm.prompt import build_system_prompt
from act.models import CalendarEvent, HealthResponse, QueryRequest, QueryResponse

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
        raise HTTPException(status_code=503, detail="Calendar backend unavailable") from exc


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
        return await _ollama_query(
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
