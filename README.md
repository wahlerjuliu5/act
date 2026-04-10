# act

Local AI calendar assistant for macOS. Schedule, query, and manage Calendar.app events with plain English — fully offline, no cloud APIs.

```
act "schedule RL Paper tomorrow for 3 hours"
act "delete RL Paper on Saturday" -y
act "find me a 2-hour slot on Friday"
act "what's on my calendar this week?"
```

## Requirements

- macOS (Apple Silicon recommended)
- Python 3.11+
- [Ollama](https://ollama.com) installed and running

## Setup

```bash
# 1. Install dependencies (creates the `act` command)
pip install -e ".[dev]"

# 2. Pull the model (≈2 GB, one-time)
ollama pull qwen2.5:3b

# 3. Start the server
uvicorn act.main:app --port 8765
```

On first run macOS will prompt for Calendar access — approve it once.

## CLI

After setup, use the `act` command directly from your terminal. The server starts automatically if it is not already running.

```bash
act "what's on my calendar this week?"
act "schedule RL Paper tomorrow for 3 hours"   # shows proposed time, asks to confirm
act "schedule deep work Friday 2pm for 2h" -y  # skip confirmation
act "delete RL Paper on Saturday" -y
act "find me a 3-hour slot tomorrow"
```

**Flags**

| Flag | Description |
|------|-------------|
| `-y` / `--yes` | Execute immediately without confirmation prompt |
| `--host` | Server URL (default `http://localhost:8765`, env `ACT_HOST`) |

**What the CLI does:**
- Shows an animated spinner with status messages while waiting
- For write operations (create / delete / update): displays a confirmation panel with the actual scheduled time — including any auto-rescheduled time if conflicts were found
- For read operations (query / find slots): auto-executes and shows results

## Behaviour

**Conflict resolution** — if the requested time conflicts with an existing event, act automatically reschedules at the next available slot on the same day. If the day is fully blocked it reports the conflict so you can pick a different day.

**Intent correction** — if you say "schedule X" and the model misclassifies it as a slot-search, the server detects the scheduling verb and auto-corrects to a create action.

**Calendar selection** — the model picks a calendar from the ones available in your Calendar.app. If the name doesn't match exactly, the first available calendar is used as a fallback.

## REST API

The server also exposes a full REST API for scripting:

```bash
# Health check
curl localhost:8765/health

# List events
curl "localhost:8765/events?start=2026-04-10&end=2026-04-17"
curl "localhost:8765/events?start=2026-04-10&end=2026-04-17&calendar=Work"

# Free slots
curl "localhost:8765/free-slots?date=2026-04-11&duration_minutes=90"

# Natural language query
curl -s localhost:8765/query \
  -H "Content-Type: application/json" \
  -d '{"prompt": "schedule RL Paper tomorrow for 3 hours", "execute": true}' \
  | python3 -m json.tool

# Create / delete events directly
curl -s -X POST localhost:8765/events \
  -H "Content-Type: application/json" \
  -d '{"title": "Deep Work", "start": "2026-04-11T09:00:00", "end": "2026-04-11T11:00:00", "calendar": "Work"}'
```

## Configuration

Create `~/.act/config.yaml` to override defaults:

```yaml
ollama_model: qwen2.5:3b          # or qwen2.5:7b, llama3.1:8b, etc.
ollama_host: http://localhost:11434
working_hours:
  start: "09:00"
  end: "18:00"
port: 8765
event_lookahead_days: 7
calendar_backend: osascript
```

## Development

```bash
# Run all tests (no Ollama or Calendar access needed — fully mocked)
pytest

# Run server with auto-reload
uvicorn act.main:app --port 8765 --reload
```

## Milestones

- [x] M1 — Skeleton (FastAPI, config, `/health`)
- [x] M2 — Calendar read (`/events` via AppleScript)
- [x] M3 — Ollama integration (`/query`, structured output, retry)
- [x] M4 — Free slots (`/free-slots`)
- [x] M5 — Write events (create, delete, conflict detection, auto-reschedule)
- [x] M6 — CLI (`act "..."` with spinner, confirmation, auto-server-start)
- [ ] M7 — CalDAV backend
- [ ] M8 — Polish
