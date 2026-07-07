import json

import agent_replay.init as init_module
from agent_replay.init import HOOK_EVENTS, merge_hooks, run_init


def _force_uvx_fallback(monkeypatch):
    """Make `_hook_command` resolve to the uvx fallback form, regardless of
    whether `agent-replay` happens to be on PATH in the test environment."""
    monkeypatch.setattr(init_module.shutil, "which", lambda _name: None)


def test_merge_hooks_on_empty_settings(monkeypatch):
    _force_uvx_fallback(monkeypatch)
    result = merge_hooks({})
    for event in HOOK_EVENTS:
        assert event in result["hooks"]
        commands = [h["command"] for g in result["hooks"][event] for h in g["hooks"]]
        assert any(c.startswith("uvx agent-replay hook") for c in commands)


def test_merge_hooks_preserves_existing_hooks_for_other_events(monkeypatch):
    _force_uvx_fallback(monkeypatch)
    existing = {
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "some-other-tool"}]}
            ]
        }
    }
    result = merge_hooks(existing)
    pre = result["hooks"]["PreToolUse"]
    commands = [h["command"] for g in pre for h in g["hooks"]]
    assert "some-other-tool" in commands
    assert any(c.startswith("uvx agent-replay hook") for c in commands)


def test_merge_hooks_preserves_unrelated_top_level_settings(monkeypatch):
    _force_uvx_fallback(monkeypatch)
    existing = {"some_other_setting": "keep-me", "hooks": {}}
    result = merge_hooks(existing)
    assert result["some_other_setting"] == "keep-me"


def test_merge_hooks_is_idempotent(monkeypatch):
    _force_uvx_fallback(monkeypatch)
    once = merge_hooks({})
    twice = merge_hooks(once)
    for event in HOOK_EVENTS:
        assert len(twice["hooks"][event]) == len(once["hooks"][event])


def test_hook_command_uses_resolved_executable_path_when_available(monkeypatch):
    monkeypatch.setattr(init_module.shutil, "which", lambda _name: "/opt/bin/agent-replay")
    assert init_module._hook_command("PreToolUse") == "/opt/bin/agent-replay hook PreToolUse"


def test_hook_command_falls_back_to_uvx_when_not_resolvable(monkeypatch):
    _force_uvx_fallback(monkeypatch)
    assert init_module._hook_command("PreToolUse") == "uvx agent-replay hook PreToolUse"


def test_is_agent_replay_hook_recognizes_both_command_forms():
    assert init_module._is_agent_replay_hook(
        {"command": "uvx agent-replay hook PreToolUse"}
    )
    assert init_module._is_agent_replay_hook(
        {"command": "/opt/bin/agent-replay hook PreToolUse"}
    )
    assert init_module._is_agent_replay_hook(
        {"command": "/Users/dopiz/.local/bin/agent-replay hook SessionEnd"}
    )
    assert not init_module._is_agent_replay_hook({"command": "some-other-tool"})


def test_merge_hooks_is_idempotent_across_command_forms(monkeypatch):
    """A hook registered while the executable was resolvable (absolute path
    form) must still be recognized as already-present when init is re-run
    later with the uvx fallback active (e.g. after uninstalling locally)."""
    monkeypatch.setattr(init_module.shutil, "which", lambda _name: "/opt/bin/agent-replay")
    once = merge_hooks({})

    _force_uvx_fallback(monkeypatch)
    twice = merge_hooks(once)

    for event in HOOK_EVENTS:
        assert twice["hooks"][event] == once["hooks"][event]


def test_run_init_backs_up_and_writes_settings(monkeypatch, tmp_path):
    _force_uvx_fallback(monkeypatch)
    settings_path = tmp_path / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "existing-hook"}]}
    ]}}))

    merged, backup_path = run_init(settings_path)

    assert backup_path is not None
    assert backup_path.exists()
    backed_up = json.loads(backup_path.read_text())
    assert backed_up["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == "existing-hook"

    on_disk = json.loads(settings_path.read_text())
    assert on_disk == merged
    pre_commands = [h["command"] for g in on_disk["hooks"]["PreToolUse"] for h in g["hooks"]]
    assert "existing-hook" in pre_commands
    assert any(c.startswith("uvx agent-replay hook") for c in pre_commands)


def test_merge_hooks_adds_missing_stop_hook_only(monkeypatch, tmp_path):
    """Re-running init with unrelated hooks + partial agent-replay hooks
    (missing Stop) should add Stop only, leaving everything else
    byte-for-byte untouched."""
    _force_uvx_fallback(monkeypatch)
    existing = {
        "some_other_setting": "keep-me",
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "unrelated-tool"}]},
                {"matcher": "*", "hooks": [{"type": "command", "command": "uvx agent-replay hook PreToolUse"}]},
            ],
            "PostToolUse": [
                {"matcher": "*", "hooks": [{"type": "command", "command": "uvx agent-replay hook PostToolUse"}]},
            ],
            "UserPromptSubmit": [
                {"matcher": "*", "hooks": [{"type": "command", "command": "uvx agent-replay hook UserPromptSubmit"}]},
            ],
            "SessionStart": [
                {"matcher": "*", "hooks": [{"type": "command", "command": "uvx agent-replay hook SessionStart"}]},
            ],
            "SessionEnd": [
                {"matcher": "*", "hooks": [{"type": "command", "command": "uvx agent-replay hook SessionEnd"}]},
            ],
            "Notification": [
                {"matcher": "*", "hooks": [{"type": "command", "command": "some-notification-hook"}]},
            ],
            # No "Stop" key at all yet.
        },
    }
    before = json.loads(json.dumps(existing))  # deep copy for comparison

    result = merge_hooks(existing)

    # Stop was added.
    assert "Stop" in result["hooks"]
    stop_commands = [h["command"] for g in result["hooks"]["Stop"] for h in g["hooks"]]
    assert any(c.startswith("uvx agent-replay hook Stop") for c in stop_commands)
    assert len(result["hooks"]["Stop"]) == 1

    # Everything else is untouched, byte-for-byte.
    for event in ("PreToolUse", "PostToolUse", "UserPromptSubmit", "SessionStart", "SessionEnd"):
        assert result["hooks"][event] == before["hooks"][event]
    assert result["hooks"]["Notification"] == before["hooks"]["Notification"]
    assert result["some_other_setting"] == "keep-me"

    # Running merge again is idempotent: Stop isn't duplicated.
    twice = merge_hooks(result)
    assert twice["hooks"]["Stop"] == result["hooks"]["Stop"]


def test_run_init_without_existing_settings_file(tmp_path):
    settings_path = tmp_path / ".claude" / "settings.json"
    merged, backup_path = run_init(settings_path)
    assert backup_path is None
    assert settings_path.exists()
    for event in HOOK_EVENTS:
        assert event in merged["hooks"]
