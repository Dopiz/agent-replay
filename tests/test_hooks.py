import json

import pytest

from agent_replay import paths
from agent_replay.hooks import handle_hook


def _read_records(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_pretooluse_writes_record(tmp_path):
    sessions_dir = tmp_path / "sessions"
    pending_dir = tmp_path / "pending"
    payload = json.dumps({
        "session_id": "abc123",
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
        "cwd": "/tmp/project",
    })

    record = handle_hook("PreToolUse", payload, sessions_dir, pending_dir)

    log_path = sessions_dir / "abc123.jsonl"
    assert log_path.exists()
    records = _read_records(log_path)
    assert len(records) == 1
    assert records[0]["event_type"] == "PreToolUse"
    assert records[0]["tool_name"] == "Bash"
    assert records[0]["cwd"] == "/tmp/project"
    assert record == records[0]


def test_posttooluse_computes_duration_from_matching_pretooluse(tmp_path, monkeypatch):
    sessions_dir = tmp_path / "sessions"
    pending_dir = tmp_path / "pending"

    times = iter([100.0, 100.25])
    monkeypatch.setattr("agent_replay.hooks.time.time", lambda: next(times))

    pre_payload = json.dumps({"session_id": "s1", "tool_name": "Bash", "tool_input": {}})
    handle_hook("PreToolUse", pre_payload, sessions_dir, pending_dir)

    post_payload = json.dumps({
        "session_id": "s1",
        "tool_name": "Bash",
        "tool_input": {},
        "tool_response": {"stdout": "ok"},
    })
    handle_hook("PostToolUse", post_payload, sessions_dir, pending_dir)

    records = _read_records(sessions_dir / "s1.jsonl")
    post = records[1]
    assert post["duration_ms"] == pytest.approx(250.0)


def test_posttooluse_extracts_error(tmp_path):
    sessions_dir = tmp_path / "sessions"
    pending_dir = tmp_path / "pending"
    payload = json.dumps({
        "session_id": "s2",
        "tool_name": "Bash",
        "tool_response": {"is_error": True, "error": "command not found"},
    })
    handle_hook("PostToolUse", payload, sessions_dir, pending_dir)
    records = _read_records(sessions_dir / "s2.jsonl")
    assert records[0]["error"] == "command not found"


def test_large_tool_input_is_truncated(tmp_path):
    sessions_dir = tmp_path / "sessions"
    pending_dir = tmp_path / "pending"
    big = "x" * 60_000
    payload = json.dumps({"session_id": "s3", "tool_name": "Read", "tool_input": {"content": big}})
    handle_hook("PreToolUse", payload, sessions_dir, pending_dir)
    records = _read_records(sessions_dir / "s3.jsonl")
    assert records[0]["tool_input"]["truncated"] is True


def test_userpromptsubmit_record(tmp_path):
    sessions_dir = tmp_path / "sessions"
    pending_dir = tmp_path / "pending"
    payload = json.dumps({"session_id": "s4", "prompt": "please fix the bug"})
    handle_hook("UserPromptSubmit", payload, sessions_dir, pending_dir)
    records = _read_records(sessions_dir / "s4.jsonl")
    assert records[0]["event_type"] == "UserPromptSubmit"
    assert records[0]["tool_input"] == "please fix the bug"


def _make_transcript(tmp_path, lines):
    path = tmp_path / "transcript.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")
    return path


def test_stop_extracts_last_assistant_text(tmp_path):
    sessions_dir = tmp_path / "sessions"
    pending_dir = tmp_path / "pending"
    transcript_path = _make_transcript(tmp_path, [
        {"type": "user", "message": {"content": [{"type": "text", "text": "hi"}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "first reply"}]}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {}},
            {"type": "text", "text": "final "},
            {"type": "text", "text": "answer"},
        ]}},
    ])
    payload = json.dumps({
        "session_id": "stop1",
        "transcript_path": str(transcript_path),
        "stop_reason": "end_turn",
        "cwd": "/proj",
    })

    handle_hook("Stop", payload, sessions_dir, pending_dir)

    records = _read_records(sessions_dir / "stop1.jsonl")
    assert records[0]["event_type"] == "Stop"
    assert records[0]["assistant_text"] == "final answer"


def test_stop_missing_transcript_does_not_raise_and_logs_error(tmp_path, monkeypatch):
    sessions_dir = tmp_path / "sessions"
    pending_dir = tmp_path / "pending"
    error_log = tmp_path / "home" / "error.log"
    monkeypatch.setattr(paths, "home_dir", lambda: tmp_path / "home")
    monkeypatch.setattr(paths, "error_log_path", lambda: error_log)

    payload = json.dumps({
        "session_id": "stop2",
        "transcript_path": str(tmp_path / "does-not-exist.jsonl"),
        "stop_reason": "end_turn",
    })

    record = handle_hook("Stop", payload, sessions_dir, pending_dir)

    # The Stop event is still written, just without assistant_text.
    assert "assistant_text" not in record
    records = _read_records(sessions_dir / "stop2.jsonl")
    assert records[0]["event_type"] == "Stop"
    assert "assistant_text" not in records[0]

    # The failure was logged via the existing error-log mechanism.
    assert error_log.exists()
    assert "Stop transcript read failed" in error_log.read_text()


def test_stop_malformed_transcript_json_does_not_raise(tmp_path, monkeypatch):
    sessions_dir = tmp_path / "sessions"
    pending_dir = tmp_path / "pending"
    monkeypatch.setattr(paths, "home_dir", lambda: tmp_path / "home")
    monkeypatch.setattr(paths, "error_log_path", lambda: tmp_path / "home" / "error.log")

    transcript_path = tmp_path / "bad_transcript.jsonl"
    transcript_path.write_text("not json at all\n{also not json\n", encoding="utf-8")

    payload = json.dumps({
        "session_id": "stop3",
        "transcript_path": str(transcript_path),
        "stop_reason": "end_turn",
    })

    record = handle_hook("Stop", payload, sessions_dir, pending_dir)

    assert "assistant_text" not in record
    records = _read_records(sessions_dir / "stop3.jsonl")
    assert records[0]["event_type"] == "Stop"


def test_agent_id_and_type_recorded_for_any_event(tmp_path):
    sessions_dir = tmp_path / "sessions"
    pending_dir = tmp_path / "pending"
    payload = json.dumps({
        "session_id": "agentsess",
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
        "agent_id": "agent-42",
        "agent_type": "Explore",
    })

    handle_hook("PreToolUse", payload, sessions_dir, pending_dir)

    records = _read_records(sessions_dir / "agentsess.jsonl")
    assert records[0]["agent_id"] == "agent-42"
    assert records[0]["agent_type"] == "Explore"


def test_malformed_json_raises_for_caller_to_handle(tmp_path):
    sessions_dir = tmp_path / "sessions"
    pending_dir = tmp_path / "pending"
    with pytest.raises(json.JSONDecodeError):
        handle_hook("PreToolUse", "not json at all", sessions_dir, pending_dir)
    # nothing should have been written
    assert not (sessions_dir / "unknown.jsonl").exists()
