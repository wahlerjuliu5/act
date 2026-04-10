from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from act.config import ActConfig, load_config


def test_defaults_when_no_file(tmp_path: Path) -> None:
    cfg = load_config(config_dir=tmp_path)
    assert cfg.ollama_model == "qwen2.5:3b"
    assert cfg.ollama_host == "http://localhost:11434"
    assert cfg.calendars == []
    assert cfg.working_hours.start == "09:00"
    assert cfg.working_hours.end == "18:00"
    assert cfg.port == 8765
    assert cfg.event_lookahead_days == 7
    assert cfg.calendar_backend == "osascript"


def test_partial_override(tmp_path: Path) -> None:
    config_data = {
        "port": 9000,
        "ollama_model": "llama3.1:8b",
        "calendars": ["Work", "Personal"],
    }
    (tmp_path / "config.yaml").write_text(yaml.dump(config_data))

    cfg = load_config(config_dir=tmp_path)
    assert cfg.port == 9000
    assert cfg.ollama_model == "llama3.1:8b"
    assert cfg.calendars == ["Work", "Personal"]
    # Unspecified fields keep their defaults
    assert cfg.ollama_host == "http://localhost:11434"
    assert cfg.event_lookahead_days == 7


def test_working_hours_override(tmp_path: Path) -> None:
    config_data = {"working_hours": {"start": "08:00", "end": "17:00"}}
    (tmp_path / "config.yaml").write_text(yaml.dump(config_data))

    cfg = load_config(config_dir=tmp_path)
    assert cfg.working_hours.start == "08:00"
    assert cfg.working_hours.end == "17:00"


def test_empty_yaml_file(tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text("")
    cfg = load_config(config_dir=tmp_path)
    assert isinstance(cfg, ActConfig)
    assert cfg.port == 8765
