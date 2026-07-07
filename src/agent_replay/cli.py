from __future__ import annotations

import sys
import traceback
import webbrowser
from datetime import datetime
from pathlib import Path

import typer

from . import paths
from .config import auto_report_enabled
from .hooks import handle_hook
from .init import run_init
from .parser import list_sessions, parse_jsonl, session_cwd
from .render import render_to_file

app = typer.Typer(add_completion=False, help="Record and replay Claude Code sessions.")


@app.command()
def init() -> None:
    """Register agent-replay hooks into ~/.claude/settings.json (merges, never overwrites)."""
    settings_path = paths.claude_settings_path()
    _merged, backup_path = run_init(settings_path)
    if backup_path is not None:
        typer.echo(f"Backed up existing settings to {backup_path}")
    typer.echo(f"agent-replay hooks registered in {settings_path}")


@app.command()
def hook(event: str = typer.Argument(..., help="Hook event name, e.g. PreToolUse")) -> None:
    """Hook entrypoint invoked by Claude Code. Never fails, never raises."""
    try:
        raw_stdin = sys.stdin.read()
        sessions_dir_path = paths.sessions_dir()
        pending_dir_path = paths.pending_dir()
        record = handle_hook(event, raw_stdin, sessions_dir_path, pending_dir_path)

        if event == "SessionEnd" and auto_report_enabled(paths.config_path()):
            session_id = record.get("session_id", "unknown")
            log_path = paths.session_jsonl_path(session_id)
            if log_path.exists():
                session = parse_jsonl(log_path)
                output_path = paths.reports_dir() / f"{session_id}.html"
                render_to_file(session, output_path)
                typer.echo(str(output_path))
    except Exception:  # noqa: BLE001 - hook must never crash Claude Code
        try:
            paths.home_dir().mkdir(parents=True, exist_ok=True)
            with paths.error_log_path().open("a", encoding="utf-8") as f:
                f.write(f"--- {datetime.now().isoformat()} event={event} ---\n")
                f.write(traceback.format_exc())
                f.write("\n")
        except Exception:  # noqa: BLE001 - even logging must not crash the hook
            pass
    raise typer.Exit(code=0)


_LIST_START_WIDTH = 19  # len("YYYY-MM-DD HH:MM:SS")


def _format_start(ts: str | None) -> str:
    """Render an ISO-8601 timestamp as second-precision local time,
    e.g. ``2026-07-07 11:04:54``. Falls back to "-" if `ts` is missing
    or unparseable."""
    if not ts:
        return "-"
    try:
        parsed = datetime.fromisoformat(ts)
    except ValueError:
        return "-"
    return parsed.astimezone().strftime("%Y-%m-%d %H:%M:%S")


@app.command(name="list")
def list_cmd() -> None:
    """List recorded sessions."""
    sessions = list_sessions(paths.sessions_dir())
    if not sessions:
        typer.echo("No sessions recorded yet.")
        return
    header = f"{'SESSION':<10} {'START':<{_LIST_START_WIDTH}} {'EVENTS':>7} CWD"
    typer.echo(header)
    typer.echo("-" * len(header))
    for s in sessions:
        short_id = s.session_id[:8]
        start = _format_start(s.start_ts)
        cwd = s.cwd or "-"
        typer.echo(
            f"{short_id:<10} {start:<{_LIST_START_WIDTH}} {s.tool_call_count:>7} {cwd}"
        )


@app.command()
def open(
    session_id: str = typer.Argument(None, help="Session id (or prefix). Defaults to the most recent session under the current directory."),
    no_browser: bool = typer.Option(False, "--no-browser", help="Do not launch a browser after rendering."),
    latest: bool = typer.Option(
        False, "--latest", help="Skip cwd matching and open the most recent session overall."
    ),
) -> None:
    """Render a session's timeline to HTML and open it in the browser."""
    sessions_dir_path = paths.sessions_dir()

    if session_id is None and not latest:
        cwd = Path.cwd().resolve()
        log_path = _resolve_default_session_for_cwd(sessions_dir_path, cwd)
        if log_path is None:
            typer.echo(
                f"No sessions recorded under {cwd}.\n"
                "Try `agent-replay open <session_id>` to open a specific session, "
                "or `agent-replay list` to see all recorded sessions.",
                err=True,
            )
            raise typer.Exit(code=1)
        typer.echo(f"Opening latest session for {cwd}: {log_path.stem[:8]}")
    else:
        log_path = _resolve_session_path(sessions_dir_path, session_id)

    if log_path is None:
        typer.echo("No matching session found.", err=True)
        raise typer.Exit(code=1)

    session = parse_jsonl(log_path)
    output_path = paths.reports_dir() / f"{session.session_id}.html"
    render_to_file(session, output_path)
    typer.echo(str(output_path))

    if not no_browser:
        webbrowser.open(output_path.resolve().as_uri())


def _resolve_session_path(sessions_dir_path: Path, session_id: str | None) -> Path | None:
    if not sessions_dir_path.exists():
        return None
    if session_id is None:
        candidates = sorted(sessions_dir_path.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        return candidates[0] if candidates else None

    exact = sessions_dir_path / f"{session_id}.jsonl"
    if exact.exists():
        return exact
    matches = [p for p in sessions_dir_path.glob("*.jsonl") if p.stem.startswith(session_id)]
    return matches[0] if matches else None


def _cwd_is_under(candidate_cwd: str, target: Path) -> bool:
    try:
        resolved = Path(candidate_cwd).expanduser().resolve()
    except OSError:
        return False
    return resolved == target or target in resolved.parents


def _resolve_default_session_for_cwd(sessions_dir_path: Path, cwd: Path) -> Path | None:
    """Pick the most recent session whose recorded cwd is `cwd` or a
    subdirectory of it. Returns None if none match."""
    if not sessions_dir_path.exists():
        return None
    target = cwd.resolve()
    candidates = sorted(sessions_dir_path.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in candidates:
        recorded_cwd = session_cwd(path)
        if recorded_cwd and _cwd_is_under(recorded_cwd, target):
            return path
    return None


def main() -> None:
    app()
