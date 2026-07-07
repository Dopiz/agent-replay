"""Central place for on-disk locations used by agent-replay.

Kept as plain functions (not constants) so tests can monkeypatch them
instead of touching the real ``~/.claude`` or ``~/.agent-replay`` directories.
"""

from __future__ import annotations

from pathlib import Path


def home_dir() -> Path:
    """Root directory for all agent-replay data."""
    return Path.home() / ".agent-replay"


def sessions_dir() -> Path:
    return home_dir() / "sessions"


def pending_dir() -> Path:
    return home_dir() / "sessions" / ".pending"


def reports_dir() -> Path:
    return home_dir() / "reports"


def error_log_path() -> Path:
    return home_dir() / "error.log"


def config_path() -> Path:
    return home_dir() / "config.toml"


def claude_settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def session_jsonl_path(session_id: str) -> Path:
    return sessions_dir() / f"{session_id}.jsonl"
