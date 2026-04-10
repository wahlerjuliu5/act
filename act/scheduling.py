from __future__ import annotations

from datetime import date, datetime, timedelta

from act.models import CalendarEvent, FreeSlot


def find_free_slots(
    events: list[CalendarEvent],
    day: date,
    duration_minutes: int,
    working_start: str,
    working_end: str,
) -> list[FreeSlot]:
    """Return free time windows on `day` that fit `duration_minutes` within working hours."""
    ws_h, ws_m = map(int, working_start.split(":"))
    we_h, we_m = map(int, working_end.split(":"))
    work_start = datetime(day.year, day.month, day.day, ws_h, ws_m)
    work_end = datetime(day.year, day.month, day.day, we_h, we_m)

    # Collect busy intervals that overlap the working window, clipped to it
    busy: list[tuple[datetime, datetime]] = []
    for e in events:
        clipped_start = max(e.start.replace(tzinfo=None), work_start)
        clipped_end = min(e.end.replace(tzinfo=None), work_end)
        if clipped_end > clipped_start:
            busy.append((clipped_start, clipped_end))

    # Sort then merge overlapping/adjacent busy periods
    busy.sort()
    merged: list[tuple[datetime, datetime]] = []
    for start, end in busy:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    # Walk the working window and collect gaps >= duration_minutes
    min_delta = timedelta(minutes=duration_minutes)
    slots: list[FreeSlot] = []
    cursor = work_start

    for busy_start, busy_end in merged:
        if busy_start > cursor and (busy_start - cursor) >= min_delta:
            slots.append(FreeSlot(start=cursor, end=busy_start))
        cursor = max(cursor, busy_end)

    if work_end > cursor and (work_end - cursor) >= min_delta:
        slots.append(FreeSlot(start=cursor, end=work_end))

    return slots
