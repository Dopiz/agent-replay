import json

from typer.testing import CliRunner

from agent_replay import paths
from agent_replay.cli import app

runner = CliRunner()


def _patch_paths(monkeypatch, tmp_path):
    home = tmp_path / "agent-replay-home"
    settings_path = tmp_path / "claude-settings" / "settings.json"

    monkeypatch.setattr(paths, "home_dir", lambda: home)
    monkeypatch.setattr(paths, "sessions_dir", lambda: home / "sessions")
    monkeypatch.setattr(paths, "pending_dir", lambda: home / "sessions" / ".pending")
    monkeypatch.setattr(paths, "reports_dir", lambda: home / "reports")
    monkeypatch.setattr(paths, "error_log_path", lambda: home / "error.log")
    monkeypatch.setattr(paths, "config_path", lambda: home / "config.toml")
    monkeypatch.setattr(paths, "claude_settings_path", lambda: settings_path)
    return home, settings_path


def test_hook_command_writes_jsonl(monkeypatch, tmp_path):
    home, _ = _patch_paths(monkeypatch, tmp_path)
    payload = json.dumps({"session_id": "cli-sess", "tool_name": "Bash", "tool_input": {"command": "ls"}})

    result = runner.invoke(app, ["hook", "PreToolUse"], input=payload)

    assert result.exit_code == 0
    log_path = home / "sessions" / "cli-sess.jsonl"
    assert log_path.exists()
    record = json.loads(log_path.read_text().splitlines()[0])
    assert record["tool_name"] == "Bash"


def test_hook_command_bad_input_still_exits_zero_and_logs_error(monkeypatch, tmp_path):
    home, _ = _patch_paths(monkeypatch, tmp_path)

    result = runner.invoke(app, ["hook", "PreToolUse"], input="not valid json {{{")

    assert result.exit_code == 0
    assert result.exception is None
    error_log = home / "error.log"
    assert error_log.exists()
    assert "JSONDecodeError" in error_log.read_text()


def test_list_command_shows_recorded_session(monkeypatch, tmp_path):
    home, _ = _patch_paths(monkeypatch, tmp_path)
    payload = json.dumps({"session_id": "list-sess", "prompt": "hello"})
    runner.invoke(app, ["hook", "UserPromptSubmit"], input=payload)

    result = runner.invoke(app, ["list"])

    assert result.exit_code == 0
    assert "list-sess"[:8] in result.stdout


def test_list_command_formats_start_time_and_aligns_columns(monkeypatch, tmp_path):
    from datetime import datetime

    home, _ = _patch_paths(monkeypatch, tmp_path)
    sessions_dir = home / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    long_cwd = "/very/long/path/that/definitely/exceeds/thirty/characters/project"
    (sessions_dir / "fmt-sess.jsonl").write_text(
        json.dumps(
            {
                "session_id": "fmt-sess",
                "event_type": "UserPromptSubmit",
                "tool_input": "hi",
                "ts": "2026-07-07T03:04:54.123456+00:00",
                "cwd": "/some/project",
            }
        )
        + "\n"
    )
    (sessions_dir / "long-sess.jsonl").write_text(
        json.dumps(
            {
                "session_id": "long-sess",
                "event_type": "UserPromptSubmit",
                "tool_input": "hi",
                "ts": "2026-07-07T04:05:06+00:00",
                "cwd": long_cwd,
            }
        )
        + "\n"
    )

    result = runner.invoke(app, ["list"])

    assert result.exit_code == 0
    lines = result.stdout.splitlines()
    header, rows = lines[0], lines[2:4]

    # FAILED column is gone.
    assert "FAILED" not in header

    # START is local time at second precision (no microseconds, no offset).
    expected_local = (
        datetime.fromisoformat("2026-07-07T03:04:54.123456+00:00")
        .astimezone()
        .strftime("%Y-%m-%d %H:%M:%S")
    )
    fmt_row = next(r for r in rows if r.startswith("fmt-sess"))
    long_row = next(r for r in rows if r.startswith("long-ses"))
    assert expected_local in fmt_row
    assert "+00:00" not in fmt_row

    # Fixed-width SESSION/START/EVENTS columns line up with the header;
    # CWD is the last column and shows the full, untruncated path.
    start_col = header.index("START")
    events_end = header.index("EVENTS") + len("EVENTS")
    cwd_col = header.index("CWD")
    for row in (fmt_row, long_row):
        assert len(row[start_col:events_end].split("  ")[0]) == 19  # second precision
        assert row[events_end - 1] == "0"  # EVENTS right-aligned at same offset
    assert fmt_row[cwd_col:] == "/some/project"
    assert long_row[cwd_col:] == long_cwd
    assert "…" not in long_row


def test_open_command_renders_html_without_browser(monkeypatch, tmp_path):
    home, _ = _patch_paths(monkeypatch, tmp_path)
    payload = json.dumps({"session_id": "open-sess", "prompt": "hello there"})
    runner.invoke(app, ["hook", "UserPromptSubmit"], input=payload)

    result = runner.invoke(app, ["open", "open-sess", "--no-browser"])

    assert result.exit_code == 0
    output_path = home / "reports" / "open-sess.html"
    assert output_path.exists()
    assert "hello there" in output_path.read_text(encoding="utf-8")


def test_open_no_id_picks_latest_session_under_cwd(monkeypatch, tmp_path):
    home, _ = _patch_paths(monkeypatch, tmp_path)
    other_dir = tmp_path / "other-project"
    here_dir = tmp_path / "here-project"
    other_dir.mkdir()
    here_dir.mkdir()

    other_payload = json.dumps({"session_id": "other-sess", "cwd": str(other_dir), "prompt": "other"})
    runner.invoke(app, ["hook", "UserPromptSubmit"], input=other_payload)
    here_payload = json.dumps({"session_id": "here-sess", "cwd": str(here_dir), "prompt": "here"})
    runner.invoke(app, ["hook", "UserPromptSubmit"], input=here_payload)

    monkeypatch.chdir(here_dir)
    result = runner.invoke(app, ["open", "--no-browser"])

    assert result.exit_code == 0
    assert "here-sess"[:8] in result.stdout
    assert (home / "reports" / "here-sess.html").exists()


def test_open_no_id_picks_latest_session_under_cwd_subdirectory(monkeypatch, tmp_path):
    home, _ = _patch_paths(monkeypatch, tmp_path)
    project_dir = tmp_path / "project"
    sub_dir = project_dir / "sub" / "deeper"
    sub_dir.mkdir(parents=True)

    payload = json.dumps({"session_id": "sub-sess", "cwd": str(sub_dir), "prompt": "hi"})
    runner.invoke(app, ["hook", "UserPromptSubmit"], input=payload)

    monkeypatch.chdir(project_dir)
    result = runner.invoke(app, ["open", "--no-browser"])

    assert result.exit_code == 0
    assert "sub-sess"[:8] in result.stdout
    assert (home / "reports" / "sub-sess.html").exists()


def test_open_no_id_no_match_prints_hint_and_exits_nonzero(monkeypatch, tmp_path):
    home, _ = _patch_paths(monkeypatch, tmp_path)
    other_dir = tmp_path / "other-project"
    empty_dir = tmp_path / "empty-project"
    other_dir.mkdir()
    empty_dir.mkdir()

    payload = json.dumps({"session_id": "elsewhere-sess", "cwd": str(other_dir), "prompt": "hi"})
    runner.invoke(app, ["hook", "UserPromptSubmit"], input=payload)

    monkeypatch.chdir(empty_dir)
    result = runner.invoke(app, ["open", "--no-browser"])

    assert result.exit_code == 1
    output = result.output
    assert "No sessions recorded under" in output
    assert "agent-replay open <session_id>" in output
    assert "agent-replay list" in output
    # It must NOT fall back to the global latest.
    assert not (home / "reports" / "elsewhere-sess.html").exists()


def test_open_no_id_matches_trailing_slash_and_symlinked_cwd(monkeypatch, tmp_path):
    home, _ = _patch_paths(monkeypatch, tmp_path)
    real_dir = tmp_path / "real-project"
    real_dir.mkdir()
    link_dir = tmp_path / "link-project"
    link_dir.symlink_to(real_dir)

    # Recorded cwd has a trailing slash and goes through the symlink.
    payload = json.dumps({"session_id": "sym-sess", "cwd": str(link_dir) + "/", "prompt": "hi"})
    runner.invoke(app, ["hook", "UserPromptSubmit"], input=payload)

    monkeypatch.chdir(real_dir)
    result = runner.invoke(app, ["open", "--no-browser"])

    assert result.exit_code == 0
    assert "Opening latest session for" in result.stdout
    assert (home / "reports" / "sym-sess.html").exists()


def test_open_with_id_ignores_cwd_matching(monkeypatch, tmp_path):
    home, _ = _patch_paths(monkeypatch, tmp_path)
    other_dir = tmp_path / "other-project"
    empty_dir = tmp_path / "empty-project"
    other_dir.mkdir()
    empty_dir.mkdir()

    payload = json.dumps({"session_id": "explicit-sess", "cwd": str(other_dir), "prompt": "hi"})
    runner.invoke(app, ["hook", "UserPromptSubmit"], input=payload)

    monkeypatch.chdir(empty_dir)
    result = runner.invoke(app, ["open", "explicit-sess", "--no-browser"])

    assert result.exit_code == 0
    assert (home / "reports" / "explicit-sess.html").exists()


def test_open_latest_flag_skips_cwd_matching(monkeypatch, tmp_path):
    home, _ = _patch_paths(monkeypatch, tmp_path)
    other_dir = tmp_path / "other-project"
    empty_dir = tmp_path / "empty-project"
    other_dir.mkdir()
    empty_dir.mkdir()

    payload = json.dumps({"session_id": "global-latest-sess", "cwd": str(other_dir), "prompt": "hi"})
    runner.invoke(app, ["hook", "UserPromptSubmit"], input=payload)

    monkeypatch.chdir(empty_dir)
    result = runner.invoke(app, ["open", "--latest", "--no-browser"])

    assert result.exit_code == 0
    assert (home / "reports" / "global-latest-sess.html").exists()


def test_init_command_merges_hooks(monkeypatch, tmp_path):
    home, settings_path = _patch_paths(monkeypatch, tmp_path)
    # Force the uvx fallback form so this test doesn't depend on whether
    # `agent-replay` happens to be resolvable on PATH in the test environment.
    monkeypatch.setattr("agent_replay.init.shutil.which", lambda _name: None)
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "existing-hook"}]}
    ]}}))

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    written = json.loads(settings_path.read_text())
    pre_commands = [h["command"] for g in written["hooks"]["PreToolUse"] for h in g["hooks"]]
    assert "existing-hook" in pre_commands
    assert any(c.startswith("uvx agent-replay hook") for c in pre_commands)
    backups = list(settings_path.parent.glob("settings.json.bak.*"))
    assert len(backups) == 1
