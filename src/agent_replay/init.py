"""Logic for `agent-replay init`: merge agent-replay hooks into
~/.claude/settings.json without clobbering any existing hooks.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path

HOOK_EVENTS = [
    "PreToolUse",
    "PostToolUse",
    "UserPromptSubmit",
    "SessionStart",
    "SessionEnd",
    "Stop",
]

# Fallback form, used once agent-replay is published to PyPI and/or the
# executable isn't currently resolvable on PATH.
UVX_COMMAND_PREFIX = "uvx agent-replay hook"

# Recognizes either hook command form:
#   - "uvx agent-replay hook <Event>"           (uvx fallback)
#   - "/abs/path/to/agent-replay hook <Event>"   (resolved executable)
_HOOK_COMMAND_RE = re.compile(r"(?:^|[/\\]|\s)agent-replay hook \S+$")


def _hook_command(event: str) -> str:
    """Build the hook command for `event`.

    Prefers an absolute path to the currently installed `agent-replay`
    executable (found via PATH), since the package may not be published to
    PyPI yet and `uvx agent-replay` would fail. Falls back to the `uvx`
    form (the intended long-term, PyPI-published form) when no executable
    can be found on PATH.
    """
    resolved = shutil.which("agent-replay")
    if resolved:
        return f"{resolved} hook {event}"
    return f"{UVX_COMMAND_PREFIX} {event}"


def _is_agent_replay_hook(hook_entry: dict) -> bool:
    command = hook_entry.get("command", "")
    return bool(_HOOK_COMMAND_RE.search(command))


def merge_hooks(settings: dict) -> dict:
    """Return a new settings dict with agent-replay hooks merged in.

    Existing hooks (for these events or others) are preserved. If an
    agent-replay hook for a given event is already present it is left
    as-is (idempotent), otherwise it is appended to that event's matcher
    group list.
    """
    settings = dict(settings)  # shallow copy is enough at the top level
    hooks = dict(settings.get("hooks", {}))

    for event in HOOK_EVENTS:
        event_groups = list(hooks.get(event, []))

        # Does any existing group already contain our hook command?
        already_present = any(
            _is_agent_replay_hook(h)
            for group in event_groups
            for h in group.get("hooks", [])
        )
        if already_present:
            hooks[event] = event_groups
            continue

        new_group = {
            "matcher": "*",
            "hooks": [
                {
                    "type": "command",
                    "command": _hook_command(event),
                }
            ],
        }
        event_groups.append(new_group)
        hooks[event] = event_groups

    settings["hooks"] = hooks
    return settings


def load_settings(settings_path: Path) -> dict:
    if not settings_path.exists():
        return {}
    text = settings_path.read_text(encoding="utf-8")
    if not text.strip():
        return {}
    return json.loads(text)


def backup_settings(settings_path: Path) -> Path | None:
    if not settings_path.exists():
        return None
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    backup_path = settings_path.with_suffix(settings_path.suffix + f".bak.{timestamp}")
    shutil.copy2(settings_path, backup_path)
    return backup_path


def run_init(settings_path: Path) -> tuple[dict, Path | None]:
    """Perform the init: backup + merge + write. Returns (new_settings, backup_path)."""
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    original = load_settings(settings_path)
    backup_path = backup_settings(settings_path)
    merged = merge_hooks(original)
    settings_path.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return merged, backup_path
