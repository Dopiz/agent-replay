"""Hook handler: reads a Claude Code hook JSON payload from stdin and
appends one JSONL record to the session's log file.

This module must never let an exception escape to the caller when invoked
through the CLI's ``hook`` command -- see cli.py's try/except wrapper.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import paths

MAX_BYTES = 50_000  # 50KB
TRANSCRIPT_TAIL_BYTES = 64 * 1024  # how much of the transcript tail to read


def _truncate(value: Any) -> Any:
    """Truncate a value if its JSON representation exceeds MAX_BYTES."""
    if value is None:
        return None
    try:
        text = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(value)
    encoded = text.encode("utf-8")
    if len(encoded) <= MAX_BYTES:
        return value
    preview = encoded[:MAX_BYTES].decode("utf-8", errors="ignore")
    return {"truncated": True, "preview": preview}


def _extract_error(payload: dict) -> str | None:
    resp = payload.get("tool_response")
    if isinstance(resp, dict):
        if resp.get("is_error"):
            return str(resp.get("error") or resp.get("stderr") or "error")
        if resp.get("error"):
            return str(resp["error"])
    if payload.get("error"):
        return str(payload["error"])
    return None


def _log_error(event: str, message: str) -> None:
    """Best-effort error log, mirroring cli.py's top-level hook handler.

    Used for failures that must be swallowed *inside* build_record (e.g. a
    broken transcript file for a Stop event) so the rest of the record can
    still be written -- unlike the top-level try/except in cli.py, which
    aborts the whole hook invocation. Must never raise.
    """
    try:
        paths.home_dir().mkdir(parents=True, exist_ok=True)
        with paths.error_log_path().open("a", encoding="utf-8") as f:
            f.write(f"--- {datetime.now(timezone.utc).isoformat()} event={event} ---\n")
            f.write(message)
            f.write("\n")
    except Exception:  # noqa: BLE001 - even logging must not crash the hook
        pass


def _extract_last_assistant_text(transcript_path: Any, event: str) -> str | None:
    """Read the tail of a Claude Code transcript JSONL file and return the
    text of the LAST assistant message that has any text content.

    Defensively parsed: any failure (missing file, unreadable, malformed
    JSON, unexpected shape) is logged and swallowed -- this never raises.
    """
    if not transcript_path:
        return None
    try:
        path = Path(transcript_path)
        with path.open("rb") as f:
            f.seek(0, 2)
            file_size = f.tell()
            seek_pos = max(0, file_size - TRANSCRIPT_TAIL_BYTES)
            f.seek(seek_pos)
            data = f.read()

        text = data.decode("utf-8", errors="ignore")
        lines = text.splitlines()
        if seek_pos > 0 and lines:
            # The first line of the tail read is likely a partial line.
            lines = lines[1:]

        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict) or obj.get("type") != "assistant":
                continue
            message = obj.get("message")
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if not isinstance(content, list):
                continue
            texts = [
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            combined = "".join(texts).strip()
            if combined:
                return combined
        return None
    except Exception as exc:  # noqa: BLE001 - never let a bad transcript raise
        _log_error(event, f"Stop transcript read failed for {transcript_path!r}: {exc!r}")
        return None


def _pending_file(pending_dir: Path, session_id: str) -> Path:
    return pending_dir / f"{session_id}.json"


def _pop_pending_start(pending_dir: Path, session_id: str, tool_name: str | None) -> float | None:
    """Pop the earliest recorded PreToolUse start time for tool_name (FIFO)."""
    path = _pending_file(pending_dir, session_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    key = tool_name or "__unknown__"
    queue = data.get(key) or []
    if not queue:
        return None
    start_ts = queue.pop(0)
    data[key] = queue
    path.write_text(json.dumps(data))
    return start_ts


def _push_pending_start(pending_dir: Path, session_id: str, tool_name: str | None, start_ts: float) -> None:
    pending_dir.mkdir(parents=True, exist_ok=True)
    path = _pending_file(pending_dir, session_id)
    data: dict[str, list[float]] = {}
    if path.exists():
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            data = {}
    key = tool_name or "__unknown__"
    data.setdefault(key, []).append(start_ts)
    path.write_text(json.dumps(data))


def build_record(
    event: str,
    payload: dict,
    pending_dir_path: Path,
) -> dict:
    """Build the JSONL record for one hook event (no I/O to the log file)."""
    session_id = payload.get("session_id") or "unknown"
    now = time.time()
    tool_name = payload.get("tool_name")

    record: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "event_type": event,
        "tool_name": tool_name,
        "tool_input": None,
        "tool_output": None,
        "duration_ms": None,
        "cwd": payload.get("cwd"),
        "error": None,
    }

    agent_id = payload.get("agent_id")
    if agent_id is not None:
        record["agent_id"] = agent_id
    agent_type = payload.get("agent_type")
    if agent_type is not None:
        record["agent_type"] = agent_type

    if event == "PreToolUse":
        record["tool_input"] = _truncate(payload.get("tool_input"))
        _push_pending_start(pending_dir_path, session_id, tool_name, now)
    elif event == "PostToolUse":
        record["tool_input"] = _truncate(payload.get("tool_input"))
        record["tool_output"] = _truncate(payload.get("tool_response"))
        record["error"] = _extract_error(payload)
        start_ts = _pop_pending_start(pending_dir_path, session_id, tool_name)
        if start_ts is not None:
            record["duration_ms"] = round((now - start_ts) * 1000, 2)
    elif event == "UserPromptSubmit":
        record["tool_input"] = _truncate(payload.get("prompt"))
    elif event in ("SessionStart", "SessionEnd"):
        pass
    elif event == "Stop":
        assistant_text = _extract_last_assistant_text(payload.get("transcript_path"), event)
        if assistant_text is not None:
            record["assistant_text"] = _truncate(assistant_text)
    else:
        # Unknown/unsupported event: still record it generically.
        record["tool_input"] = _truncate(payload.get("tool_input"))

    return record


def append_record(session_id: str, record: dict, sessions_dir_path: Path) -> Path:
    sessions_dir_path.mkdir(parents=True, exist_ok=True)
    path = sessions_dir_path / f"{session_id}.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def handle_hook(event: str, raw_stdin: str, sessions_dir_path: Path, pending_dir_path: Path) -> dict:
    """Parse raw_stdin as JSON, build a record, and append it to the log.

    Raises on malformed input / IO errors -- callers (cli.py) are
    responsible for catching everything and never propagating exceptions
    out of the ``hook`` subcommand.
    """
    payload = json.loads(raw_stdin) if raw_stdin.strip() else {}
    if not isinstance(payload, dict):
        payload = {}
    record = build_record(event, payload, pending_dir_path)
    append_record(record["session_id"], record, sessions_dir_path)
    return record
