"""act CLI — natural-language calendar assistant."""
from __future__ import annotations

import subprocess
import sys
import threading
import time
from datetime import datetime

import click
import httpx
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.text import Text

console = Console()

# Register a custom pulsating-plus spinner
try:
    from rich._spinners import SPINNERS as _SPINNERS  # noqa: PLC2701
    _SPINNERS["pulse_plus"] = {
        "interval": 200,
        "frames": ["  +  ", " +++ ", "+++++" , " +++ ", "  +  "],
    }
    _SPINNER = "pulse_plus"
except Exception:
    _SPINNER = "dots"  # graceful fallback

# Status messages that cycle during the query phase
_QUERY_STAGES: list[tuple[float, str]] = [
    (0.0, "Checking your calendar…"),
    (2.5, "Thinking…"),
    (7.0, "Almost there…"),
]

_EXEC_MSG: dict[str, str] = {
    "create": "Scheduling…",
    "delete": "Removing from calendar…",
    "find_slots": "Finding free slots…",
    "update": "Updating event…",
}

# Only these intents modify the calendar and need confirmation
_WRITE_INTENTS = {"create", "delete", "update"}


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _call_api(url: str, payload: dict, out: list, err: list) -> None:
    try:
        r = httpx.post(url, json=payload, timeout=90.0)
        r.raise_for_status()
        out.append(r.json())
    except Exception as exc:  # noqa: BLE001
        err.append(exc)


def _run_with_spinner(url: str, payload: dict, stages: list[tuple[float, str]]) -> dict:
    """POST to *url* while showing an animated spinner with cycling stage messages."""
    out: list = []
    err: list = []
    t = threading.Thread(target=_call_api, args=(url, payload, out, err))
    t.start()

    start = time.time()
    with console.status(stages[0][1], spinner=_SPINNER) as status:
        while t.is_alive():
            elapsed = time.time() - start
            msg = stages[0][1]
            for threshold, label in stages:
                if elapsed >= threshold:
                    msg = label
            status.update(msg)
            time.sleep(0.05)

    t.join()
    if err:
        raise err[0]
    return out[0]


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------


def _ensure_server(host: str) -> None:
    """Start the act server in the background if it is not already running."""
    try:
        httpx.get(f"{host}/health", timeout=2.0)
        return
    except (httpx.ConnectError, httpx.TimeoutException):
        pass

    console.print("[dim]  Starting act server…[/dim]")
    subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "act.main:app",
         "--port", "8765", "--log-level", "error"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(20):
        time.sleep(0.5)
        try:
            httpx.get(f"{host}/health", timeout=1.0)
            return
        except (httpx.ConnectError, httpx.TimeoutException):
            continue

    console.print(
        "[red]Could not start act server.[/red]  "
        "Run manually: [bold]uvicorn act.main:app --port 8765[/bold]"
    )
    raise SystemExit(1)


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------


def _fmt_range(start_iso: str, end_iso: str) -> str:
    try:
        s = datetime.fromisoformat(start_iso)
        e = datetime.fromisoformat(end_iso)
        return f"{s.strftime('%A, %b %d')}  {s.strftime('%H:%M')} – {e.strftime('%H:%M')}"
    except Exception:
        return f"{start_iso} – {end_iso}"


def _show_proposed(result: dict) -> None:
    intent = result.get("intent", "")
    action = result.get("proposed_action") or {}

    if intent == "create":
        title = action.get("title", "?")
        calendar = action.get("calendar") or "?"
        time_range = _fmt_range(action.get("start", ""), action.get("end", ""))

        body = Text()
        body.append("📅 Create  ", style="bold cyan")
        body.append(title, style="bold white")
        body.append(f"\n   {time_range}", style="")
        body.append(f"\n   Calendar: {calendar}", style="dim")
        console.print(Panel(body, border_style="cyan", padding=(0, 1)))

    elif intent == "delete":
        title = action.get("title") or action.get("event_id") or "?"
        body = Text()
        body.append("🗑  Delete  ", style="bold red")
        body.append(title, style="bold white")
        console.print(Panel(body, border_style="red", padding=(0, 1)))

    elif intent == "find_slots":
        day = action.get("date", "?")
        dur = action.get("duration_minutes", "?")
        body = Text()
        body.append("🔍 Find slots  ", style="bold yellow")
        body.append(f"{dur} min on {day}", style="bold white")
        console.print(Panel(body, border_style="yellow", padding=(0, 1)))

    else:
        summary = result.get("human_summary", "")
        if summary:
            console.print(f"\n  {summary}\n")


def _show_execution(result: dict) -> None:
    er = result.get("execution_result") or {}
    status = er.get("status", "")
    summary = result.get("human_summary", "")

    if status == "created":
        title = er.get("title", "Event")
        time_range = _fmt_range(er.get("start", ""), er.get("end", ""))
        console.print(f"\n  [bold green]✓[/bold green]  Scheduled: [bold]{title}[/bold]")
        console.print(f"     [dim]{time_range}  ·  {er.get('calendar', '')}[/dim]\n")

    elif status == "deleted":
        count = er.get("count", 1)
        deleted = er.get("deleted", [])
        console.print(f"\n  [bold green]✓[/bold green]  Deleted {count} event(s).")
        for d in deleted:
            console.print(f"     [dim]– {d.get('title', '')}  {d.get('start', '')[:10]}[/dim]")
        console.print()

    elif status in ("ok",) and "slots" in er:
        slots = er.get("slots", [])
        if not slots:
            console.print("\n  [yellow]No free slots found for that duration.[/yellow]\n")
        else:
            console.print(f"\n  [bold]Free slots:[/bold]")
            for s in slots:
                start = datetime.fromisoformat(s["start"])
                end = datetime.fromisoformat(s["end"])
                console.print(
                    f"     [cyan]{start.strftime('%H:%M')} – {end.strftime('%H:%M')}[/cyan]"
                    f"  [dim]({s['duration_minutes']} min)[/dim]"
                )
            console.print()

    elif status == "conflict":
        console.print(f"\n  [yellow]⚠  Conflict[/yellow]  {er.get('hint', '')}")
        for c in er.get("conflicts", []):
            ts = c.get("start", "")[:16].replace("T", " ")
            console.print(f"     [dim]– {c.get('title', '')}  {ts}[/dim]")
        console.print()

    elif status == "not_found":
        console.print(f"\n  [yellow]Not found:[/yellow]  {er.get('detail', '')}\n")

    elif status == "error":
        console.print(f"\n  [red]✗  Error:[/red]  {er.get('detail', '')}\n")

    elif status == "no_action":
        console.print(f"\n  {summary}\n")

    else:
        if summary:
            console.print(f"\n  {summary}\n")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


@click.command()
@click.argument("prompt")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation and execute immediately.")
@click.option(
    "--host",
    default="http://localhost:8765",
    envvar="ACT_HOST",
    show_default=True,
    help="act server base URL.",
)
def main(prompt: str, yes: bool, host: str) -> None:
    """Natural language calendar assistant.

    \b
    Examples:
      act "what's on my calendar this week?"
      act "schedule RL Paper tomorrow for 3 hours"
      act "delete RL Paper on Saturday" -y
      act "find me a 2-hour slot on Friday"
    """
    _ensure_server(host)
    url = f"{host}/query"

    # ── Fast path: -y flag → execute immediately ────────────────────────────
    if yes:
        try:
            result = _run_with_spinner(url, {"prompt": prompt, "execute": True}, _QUERY_STAGES)
        except (httpx.ConnectError, httpx.TimeoutException):
            console.print("[red]act server not reachable.[/red]")
            raise SystemExit(1)
        _show_execution(result)
        return

    # ── Step 1: get proposed action without executing ───────────────────────
    try:
        result = _run_with_spinner(url, {"prompt": prompt, "execute": False}, _QUERY_STAGES)
    except (httpx.ConnectError, httpx.TimeoutException):
        console.print("[red]act server not reachable.[/red]")
        raise SystemExit(1)
    except httpx.HTTPStatusError as exc:
        console.print(f"[red]Server error {exc.response.status_code}:[/red]  {exc.response.text}")
        raise SystemExit(1)

    intent = result.get("intent", "query")
    proposed = result.get("proposed_action")

    # ── Read-only or no action → show and exit ──────────────────────────────
    if intent not in _WRITE_INTENTS:
        if proposed:
            # find_slots: auto-execute, no confirmation needed
            _show_proposed(result)
            try:
                result = _run_with_spinner(
                    url,
                    {"prompt": prompt, "execute": True},
                    [(0.0, _EXEC_MSG.get(intent, "Working…"))],
                )
            except Exception as exc:
                console.print(f"[red]Error:[/red]  {exc}")
                raise SystemExit(1)
            _show_execution(result)
        else:
            # plain query
            console.print(f"\n  {result.get('human_summary', '')}\n")
        return

    # ── Write intent → show proposed action + confirm ───────────────────────
    console.print()
    _show_proposed(result)

    if not Confirm.ask("  Execute?", default=False):
        console.print("  [dim]Cancelled.[/dim]\n")
        return

    exec_msg = _EXEC_MSG.get(intent, "Working…")
    try:
        result = _run_with_spinner(
            url,
            {"prompt": prompt, "execute": True},
            [(0.0, exec_msg)],
        )
    except Exception as exc:
        console.print(f"[red]Error:[/red]  {exc}")
        raise SystemExit(1)

    _show_execution(result)
