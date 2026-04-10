from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from act.main import app

client = TestClient(app)


def test_health_ok() -> None:
    with patch("act.main._cal.list_calendars", return_value=["Work", "Personal", "iCloud"]):
        resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["calendar_count"] == 3
    assert body["calendar_backend"] == "osascript"


def test_health_degraded_on_osascript_failure() -> None:
    with patch(
        "act.main._cal.list_calendars",
        side_effect=subprocess.CalledProcessError(1, "osascript"),
    ):
        resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["calendar_count"] == 0


def test_health_empty_calendars() -> None:
    with patch("act.main._cal.list_calendars", return_value=[]):
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["calendar_count"] == 0
