# act

Local AI calendar scheduling assistant for macOS. Reads your Calendar.app events and answers natural-language scheduling queries — fully offline, no cloud APIs.

## Requirements

- macOS (Apple Silicon recommended)
- Python 3.11+
- [Ollama](https://ollama.com) installed and running

## Setup

```bash
# 1. Install Python dependencies
pip install -e ".[dev]"

# 2. Pull the model (≈2 GB, one-time)
ollama pull qwen2.5:3b

# 3. Start the service
uvicorn act.main:app --port 8765
```

On first run, macOS will prompt for Calendar access — approve it once and it won't ask again.

## Usage

```bash
# Health check (shows calendar count)
curl localhost:8765/health

# List events for a date range
curl "localhost:8765/events?start=2026-04-10&end=2026-04-17"

# Filter by calendar
curl "localhost:8765/events?start=2026-04-10&end=2026-04-17&calendar=Work"

# Natural language query
curl -s localhost:8765/query \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Do I have any conflicts this week?"}' \
  | python3 -m json.tool
```

## Configuration

Create `~/.act/config.yaml` to override defaults:

```yaml
ollama_model: qwen2.5:3b       # or qwen2.5:7b, llama3.1:8b, etc.
ollama_host: http://localhost:11434
calendars: []                   # empty = all calendars
working_hours:
  start: "09:00"
  end: "18:00"
port: 8765
event_lookahead_days: 7
calendar_backend: osascript     # osascript | caldav
```

## Development

```bash
# Run tests (no Ollama or Calendar access needed)
python3.11 -m pytest

# Run with auto-reload
uvicorn act.main:app --port 8765 --reload
```

## Models

Model weights are stored in `~/.ollama/models/` by Ollama — never inside this repo. If you place any `.gguf` or other weight files locally, they are gitignored.

## Milestones

- [x] M1 — Skeleton (FastAPI, config, `/health`)
- [x] M2 — Calendar read (`/events` via AppleScript)
- [x] M3 — Ollama integration (`/query` with retry + JSON validation)
- [ ] M4 — Free slots (`/free-slots`)
- [ ] M5 — Write events (`POST /events`)
- [ ] M6 — launchd auto-start
- [ ] M7 — CalDAV backend
- [ ] M8 — Polish
