from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class WorkingHours:
    start: str = "09:00"
    end: str = "18:00"


@dataclass
class ActConfig:
    ollama_model: str = "qwen2.5:3b"
    ollama_host: str = "http://localhost:11434"
    calendars: list[str] = field(default_factory=list)
    working_hours: WorkingHours = field(default_factory=WorkingHours)
    port: int = 8765
    event_lookahead_days: int = 7
    calendar_backend: str = "osascript"


def load_config(config_dir: Path | None = None) -> ActConfig:
    """Load config from ~/.act/config.yaml, falling back to defaults."""
    if config_dir is None:
        config_dir = Path.home() / ".act"

    config_file = config_dir / "config.yaml"
    if not config_file.exists():
        return ActConfig()

    with config_file.open() as f:
        raw = yaml.safe_load(f) or {}

    wh_raw = raw.get("working_hours", {})
    working_hours = WorkingHours(
        start=wh_raw.get("start", "09:00"),
        end=wh_raw.get("end", "18:00"),
    )

    return ActConfig(
        ollama_model=raw.get("ollama_model", "qwen2.5:7b"),
        ollama_host=raw.get("ollama_host", "http://localhost:11434"),
        calendars=raw.get("calendars", []),
        working_hours=working_hours,
        port=raw.get("port", 8765),
        event_lookahead_days=raw.get("event_lookahead_days", 7),
        calendar_backend=raw.get("calendar_backend", "osascript"),
    )
