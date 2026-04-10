from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, field_validator

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


class QueryRequest(BaseModel):
    prompt: str


class QueryResponse(BaseModel):
    intent: str
    proposed_action: dict[str, Any] | None = None
    human_summary: str
    confidence: float

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
