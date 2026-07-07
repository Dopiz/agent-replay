"""Parse an agent-replay session JSONL log into a Session model that the
renderer can turn into HTML.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Event:
    ts: str
    event_type: str
    tool_name: str | None
    tool_input: Any
    tool_output: Any
    duration_ms: float | None
    cwd: str | None
    error: str | None
    assistant_text: Any = None
    agent_id: str | None = None
    agent_type: str | None = None

    @property
    def is_failed(self) -> bool:
        return bool(self.error)

    @property
    def input_truncated(self) -> bool:
        return isinstance(self.tool_input, dict) and self.tool_input.get("truncated") is True

    @property
    def output_truncated(self) -> bool:
        return isinstance(self.tool_output, dict) and self.tool_output.get("truncated") is True

    @property
    def assistant_text_truncated(self) -> bool:
        return isinstance(self.assistant_text, dict) and self.assistant_text.get("truncated") is True

    @property
    def assistant_text_display(self) -> str | None:
        if self.assistant_text is None:
            return None
        if isinstance(self.assistant_text, dict) and self.assistant_text.get("truncated"):
            return self.assistant_text.get("preview", "")
        return self.assistant_text


@dataclass
class Chapter:
    """A UserPromptSubmit event and the tool events that follow it, up to
    the next UserPromptSubmit."""

    prompt: str | None
    ts: str
    events: list[Event] = field(default_factory=list)


@dataclass
class Session:
    session_id: str
    start_ts: str | None
    end_ts: str | None
    cwd: str | None
    chapters: list[Chapter]
    tool_counts: dict[str, int]
    fail_count: int
    tool_call_count: int
    total_duration_ms: float


def _parse_line(line: str) -> dict | None:
    line = line.strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def parse_jsonl(path: Path) -> Session:
    session_id = path.stem
    records = []
    with path.open("r", encoding="utf-8") as f:
        for raw_line in f:
            record = _parse_line(raw_line)
            if record is not None:
                records.append(record)

    records.sort(key=lambda r: r.get("ts") or "")

    chapters: list[Chapter] = []
    # Implicit leading chapter for tool events that happen before the
    # first UserPromptSubmit (e.g. SessionStart-triggered activity).
    current_chapter = Chapter(prompt=None, ts=records[0]["ts"] if records else "")

    tool_counts: dict[str, int] = {}
    fail_count = 0
    tool_call_count = 0
    total_duration_ms = 0.0
    start_ts: str | None = None
    end_ts: str | None = None
    cwd: str | None = None

    for record in records:
        event_type = record.get("event_type")
        ts = record.get("ts")
        if start_ts is None:
            start_ts = ts
        end_ts = ts
        if record.get("cwd"):
            cwd = record["cwd"]

        if event_type == "UserPromptSubmit":
            if current_chapter.events or current_chapter.prompt is not None:
                chapters.append(current_chapter)
            elif current_chapter.prompt is None and not current_chapter.events:
                # drop the empty implicit leading chapter, we have a real one now
                pass
            current_chapter = Chapter(prompt=record.get("tool_input"), ts=ts)
            continue

        if event_type in ("SessionStart", "SessionEnd"):
            continue

        event = Event(
            ts=ts,
            event_type=event_type,
            tool_name=record.get("tool_name"),
            tool_input=record.get("tool_input"),
            tool_output=record.get("tool_output"),
            duration_ms=record.get("duration_ms"),
            cwd=record.get("cwd"),
            error=record.get("error"),
            assistant_text=record.get("assistant_text"),
            agent_id=record.get("agent_id"),
            agent_type=record.get("agent_type"),
        )
        current_chapter.events.append(event)

        if event_type == "PostToolUse":
            tool_call_count += 1
            name = event.tool_name or "unknown"
            tool_counts[name] = tool_counts.get(name, 0) + 1
            if event.is_failed:
                fail_count += 1
            if event.duration_ms:
                total_duration_ms += event.duration_ms

    if current_chapter.events or current_chapter.prompt is not None:
        chapters.append(current_chapter)

    return Session(
        session_id=session_id,
        start_ts=start_ts,
        end_ts=end_ts,
        cwd=cwd,
        chapters=chapters,
        tool_counts=tool_counts,
        fail_count=fail_count,
        tool_call_count=tool_call_count,
        total_duration_ms=total_duration_ms,
    )


def session_cwd(path: Path) -> str | None:
    """Extract a session's cwd without building the full Session model.

    Cheap enough to call once per candidate file when picking a default
    session for ``agent-replay open``.
    """
    cwd: str | None = None
    with path.open("r", encoding="utf-8") as f:
        for raw_line in f:
            record = _parse_line(raw_line)
            if record and record.get("cwd"):
                cwd = record["cwd"]
    return cwd


def list_sessions(sessions_dir: Path) -> list[Session]:
    if not sessions_dir.exists():
        return []
    sessions = []
    for path in sorted(sessions_dir.glob("*.jsonl")):
        try:
            sessions.append(parse_jsonl(path))
        except Exception:
            continue
    sessions.sort(key=lambda s: s.start_ts or "", reverse=True)
    return sessions
