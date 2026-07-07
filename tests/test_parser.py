import json

from agent_replay.parser import list_sessions, parse_jsonl


def _write_jsonl(path, records):
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def test_parse_jsonl_groups_events_into_chapters(tmp_path):
    session_id = "sess-1"
    path = tmp_path / f"{session_id}.jsonl"
    records = [
        {"ts": "2026-07-07T00:00:00Z", "session_id": session_id, "event_type": "SessionStart",
         "tool_name": None, "tool_input": None, "tool_output": None, "duration_ms": None,
         "cwd": "/proj", "error": None},
        {"ts": "2026-07-07T00:00:01Z", "session_id": session_id, "event_type": "UserPromptSubmit",
         "tool_name": None, "tool_input": "fix the bug", "tool_output": None, "duration_ms": None,
         "cwd": "/proj", "error": None},
        {"ts": "2026-07-07T00:00:02Z", "session_id": session_id, "event_type": "PreToolUse",
         "tool_name": "Bash", "tool_input": {"command": "ls"}, "tool_output": None,
         "duration_ms": None, "cwd": "/proj", "error": None},
        {"ts": "2026-07-07T00:00:03Z", "session_id": session_id, "event_type": "PostToolUse",
         "tool_name": "Bash", "tool_input": {"command": "ls"}, "tool_output": {"stdout": "a.py"},
         "duration_ms": 120.5, "cwd": "/proj", "error": None},
        {"ts": "2026-07-07T00:00:04Z", "session_id": session_id, "event_type": "PostToolUse",
         "tool_name": "Bash", "tool_input": {"command": "boom"}, "tool_output": {"stderr": "nope"},
         "duration_ms": 15.0, "cwd": "/proj", "error": "exit code 1"},
    ]
    _write_jsonl(path, records)

    session = parse_jsonl(path)

    assert session.session_id == "sess-1"
    assert session.cwd == "/proj"
    assert len(session.chapters) == 1
    chapter = session.chapters[0]
    assert chapter.prompt == "fix the bug"
    # PreToolUse + 2 PostToolUse = 3 events in the chapter
    assert len(chapter.events) == 3
    assert session.tool_call_count == 2
    assert session.fail_count == 1
    assert session.tool_counts == {"Bash": 2}
    assert session.total_duration_ms == 120.5 + 15.0


def test_parse_jsonl_skips_malformed_lines(tmp_path):
    path = tmp_path / "sess-2.jsonl"
    path.write_text(
        json.dumps({"ts": "2026-07-07T00:00:00Z", "session_id": "sess-2", "event_type": "SessionStart",
                     "tool_name": None, "tool_input": None, "tool_output": None,
                     "duration_ms": None, "cwd": "/x", "error": None}) + "\n"
        + "not json\n"
        + "\n"
    )
    session = parse_jsonl(path)
    assert session.session_id == "sess-2"
    assert session.tool_call_count == 0


def test_truncated_flags_detected(tmp_path):
    path = tmp_path / "sess-3.jsonl"
    records = [
        {"ts": "t1", "session_id": "sess-3", "event_type": "UserPromptSubmit",
         "tool_name": None, "tool_input": "go", "tool_output": None, "duration_ms": None,
         "cwd": "/x", "error": None},
        {"ts": "t2", "session_id": "sess-3", "event_type": "PostToolUse", "tool_name": "Read",
         "tool_input": {"truncated": True, "preview": "abc"}, "tool_output": {"truncated": True, "preview": "def"},
         "duration_ms": 5.0, "cwd": "/x", "error": None},
    ]
    _write_jsonl(path, records)
    session = parse_jsonl(path)
    event = session.chapters[0].events[0]
    assert event.input_truncated is True
    assert event.output_truncated is True


def test_list_sessions_sorted_by_start_desc(tmp_path):
    older = tmp_path / "old.jsonl"
    newer = tmp_path / "new.jsonl"
    _write_jsonl(older, [{"ts": "2026-01-01T00:00:00Z", "session_id": "old", "event_type": "SessionStart",
                          "tool_name": None, "tool_input": None, "tool_output": None,
                          "duration_ms": None, "cwd": "/a", "error": None}])
    _write_jsonl(newer, [{"ts": "2026-06-01T00:00:00Z", "session_id": "new", "event_type": "SessionStart",
                          "tool_name": None, "tool_input": None, "tool_output": None,
                          "duration_ms": None, "cwd": "/b", "error": None}])
    sessions = list_sessions(tmp_path)
    assert [s.session_id for s in sessions] == ["new", "old"]


def test_list_sessions_empty_dir(tmp_path):
    assert list_sessions(tmp_path / "does-not-exist") == []


def test_legacy_jsonl_without_new_fields_parses_fine(tmp_path):
    """Old JSONL logs written before Stop/agent_id/agent_type existed must
    still parse without error and without those fields present."""
    path = tmp_path / "legacy.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(r)
            for r in [
                {"ts": "2026-01-01T00:00:00Z", "session_id": "legacy", "event_type": "SessionStart",
                 "tool_name": None, "tool_input": None, "tool_output": None,
                 "duration_ms": None, "cwd": "/legacy", "error": None},
                {"ts": "2026-01-01T00:00:01Z", "session_id": "legacy", "event_type": "UserPromptSubmit",
                 "tool_name": None, "tool_input": "do the thing", "tool_output": None,
                 "duration_ms": None, "cwd": "/legacy", "error": None},
                {"ts": "2026-01-01T00:00:02Z", "session_id": "legacy", "event_type": "PostToolUse",
                 "tool_name": "Bash", "tool_input": {"command": "ls"}, "tool_output": {"stdout": "ok"},
                 "duration_ms": 12.0, "cwd": "/legacy", "error": None},
            ]
        )
        + "\n"
    )

    session = parse_jsonl(path)

    assert session.session_id == "legacy"
    assert session.tool_call_count == 1
    event = session.chapters[0].events[0]
    assert event.assistant_text is None
    assert event.agent_id is None
    assert event.agent_type is None
    assert event.assistant_text_display is None


def test_stop_event_with_assistant_text_parses(tmp_path):
    path = tmp_path / "stop-sess.jsonl"
    records = [
        {"ts": "2026-07-07T00:00:00Z", "session_id": "stop-sess", "event_type": "UserPromptSubmit",
         "tool_name": None, "tool_input": "hello", "tool_output": None, "duration_ms": None,
         "cwd": "/proj", "error": None},
        {"ts": "2026-07-07T00:00:01Z", "session_id": "stop-sess", "event_type": "Stop",
         "tool_name": None, "tool_input": None, "tool_output": None, "duration_ms": None,
         "cwd": "/proj", "error": None, "assistant_text": "here is my reply",
         "agent_id": "agent-1", "agent_type": "Explore"},
    ]
    _write_jsonl(path, records)

    session = parse_jsonl(path)

    stop_event = session.chapters[0].events[0]
    assert stop_event.event_type == "Stop"
    assert stop_event.assistant_text == "here is my reply"
    assert stop_event.assistant_text_display == "here is my reply"
    assert stop_event.agent_type == "Explore"
