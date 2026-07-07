# agent-replay

Record every tool call made by a [Claude Code](https://docs.claude.com/en/docs/claude-code) session
via hooks, and replay it afterwards as a single, offline-friendly, static HTML timeline.

> **Note**: This is a practice project, built for fun and for observing how AI coding agents
> actually work under the hood — not a production-grade tool.

## Why

Claude Code hooks let you observe `PreToolUse`, `PostToolUse`, `UserPromptSubmit`, `SessionStart`,
`SessionEnd`, and `Stop` events. `agent-replay` wires itself into those hooks, appends a JSONL log
per session, and renders it into one self-contained HTML file (all CSS/JS inlined, no CDN
dependency) that you can open offline or share with a teammate.

## Features

- **Vertical timeline** of every tool call in a session, in order.
- **Prompt chapters** — each `UserPromptSubmit` starts a collapsible chapter (collapsed by default)
  with a summary of tool-call count and failure count in its header, so you can scan a long session
  without opening every turn.
- **Assistant reply bubbles** — the assistant's final reply for each turn (captured via the `Stop`
  hook) is shown alongside the tool calls it made.
- **Subagent badges** — tool calls made by a subagent are tagged so you can tell them apart from the
  main conversation.
- **Tool filter** — chips in the header let you filter the timeline down to a single tool name.
- **Summary bar** — total duration, tool call count, failure count, and per-tool usage counts at a
  glance.
- **Failure marks** — failed tool calls are flagged in red.
- **Expandable cards** — click a card to see the full, pretty-printed JSON input/output; long
  content scrolls instead of blowing up the page.
- **Truncation, twice over** — raw JSONL entries are capped at 50KB when written (with
  `truncated: true` recorded), and the HTML renderer applies its own display-time cap so a single
  huge tool call can't balloon the report file.
- **Auto-report on session end** — `SessionEnd` renders the HTML automatically and prints its path.
  Turn this off with `~/.agent-replay/config.toml`:
  ```toml
  auto_report = false
  ```
- **Single-file, offline HTML** — no external assets, no CDN, works fully offline, safe to email or
  drop in a shared drive.

## Installation

`agent-replay` isn't published to PyPI yet, so install straight from GitHub:

```bash
uv tool install git+https://github.com/Dopiz/agent-replay
```

Or clone and install locally:

```bash
git clone https://github.com/Dopiz/agent-replay
cd agent-replay
uv tool install .
```

Either way this puts an `agent-replay` executable on your `PATH`.

> Once this is published to PyPI, the plan is to make `uvx agent-replay ...` (no install step at
> all) the primary way to run it. Until then, use one of the commands above.

For local development instead of a tool install:

```bash
uv sync
uv run agent-replay --help
```

## Usage

### 1. `agent-replay init`

```bash
agent-replay init
```

Registers the agent-replay hooks into `~/.claude/settings.json`:

- Existing hooks (yours or from other tools) are preserved — agent-replay's hooks are merged in,
  never overwritten.
- A timestamped backup of the original `settings.json` (e.g. `settings.json.bak.20260707120000`) is
  written next to it before any change.
- Re-running `init` is idempotent: it recognizes agent-replay hooks already registered (in either
  the resolved-executable-path form or the `uvx` fallback form — see below) and won't duplicate
  them; it only adds whichever hook events are still missing.
- The registered command points at whatever form of `agent-replay` is actually runnable right now:
  if the executable can be resolved on `PATH` it's registered by absolute path, otherwise it falls
  back to `uvx agent-replay hook <event>` (the form that will work once this package is on PyPI).

**Claude Code needs to pick up the new hooks before they take effect** — either start a new session,
or run `/hooks` inside an existing session to reload the hook configuration.

After that, just use Claude Code normally — every tool call is recorded to
`~/.agent-replay/sessions/{session_id}.jsonl`.

### 2. `agent-replay list`

Lists recorded sessions in aligned columns: id, start time (local time, `YYYY-MM-DD HH:MM:SS`),
number of events, and the full project directory path (last column, never truncated).

```bash
agent-replay list
```

### 3. `agent-replay open [session_id]`

Renders a session's JSONL log into a single HTML file under `~/.agent-replay/reports/` and opens it
in your default browser. If no `session_id` is given, the most recent session recorded under the
current directory (or a subdirectory of it) is used; if none match, it exits with a hint to use
`agent-replay open <session_id>` or `agent-replay list`. Pass `--latest` to skip the cwd matching
and open the most recent session overall.

```bash
agent-replay open
agent-replay open a1b2c3d4
agent-replay open a1b2c3d4 --no-browser
agent-replay open --latest
```

## How it works

```
Claude Code hooks  ─▶  ~/.agent-replay/sessions/{session_id}.jsonl  ─▶  single-file HTML report
(PreToolUse, PostToolUse,     one JSON object per line,                 (Jinja2 template,
 UserPromptSubmit,             one line per event                        CSS/JS inlined)
 SessionStart, SessionEnd,
 Stop)
```

The hook handler (`agent-replay hook <event>`) is invoked by Claude Code itself. It reads the event
payload from stdin, appends a record to that session's JSONL log, and — deliberately — never raises
or exits non-zero. Any unexpected error is caught and written silently to
`~/.agent-replay/error.log` so a bug in agent-replay can never interrupt your Claude Code session.

## Data locations

- `~/.agent-replay/sessions/*.jsonl` — one JSONL log per session.
- `~/.agent-replay/reports/*.html` — rendered timelines.
- `~/.agent-replay/error.log` — hook errors are logged here silently; the hook itself never raises
  or interrupts Claude Code.
- `~/.agent-replay/config.toml` — optional settings (e.g. `auto_report`).

## Privacy

Recorded JSONL logs and rendered HTML reports contain the full input/output of every tool call in
the session — file contents, command output, prompts, and assistant replies included. Review a
report before sharing it with anyone; it can easily contain secrets or sensitive data from your
project.
