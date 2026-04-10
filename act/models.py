from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, computed_field, field_validator

VALID_INTENTS = {"query", "create", "delete", "update", "find_slots"}


class CalendarEvent(BaseModel):
    id: str
    title: str
    start: datetime
    end: datetime
    calendar: str
    location: str | None = None
    notes: str | None = None


class HealthResponse(BaseModel):
    status: str  # "ok" | "degraded"
    calendar_backend: str
    calendar_count: int


class FreeSlot(BaseModel):
    start: datetime
    end: datetime

    @computed_field  # type: ignore[misc]
    @property
    def duration_minutes(self) -> int:
        return int((self.end - self.start).total_seconds() / 60)


class CreateEventRequest(BaseModel):
    title: str
    start: datetime
    end: datetime
    calendar: str | None = None
    notes: str | None = None
    force: bool = False  # skip conflict check


class DeleteEventRequest(BaseModel):
    confirm: bool


class QueryRequest(BaseModel):
    prompt: str
    execute: bool = False  # when True, automatically run the proposed_action


class QueryResponse(BaseModel):
    intent: str
    proposed_action: dict[str, Any] | None = None
    human_summary: str
    confidence: float
    execution_result: dict[str, Any] | None = None  # populated when execute=True

    @field_validator("intent")
    @classmethod
    def validate_intent(cls, v: str) -> str:
        if v not in VALID_INTENTS:
            raise ValueError(f"intent must be one of {VALID_INTENTS}, got {v!r}")
        return v

    @field_validator("confidence")
    @classmethod
    def validate_confidence(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"confidence must be 0.0–1.0, got {v}")
        return v
