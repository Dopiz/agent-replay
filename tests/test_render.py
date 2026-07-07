import html.parser
import json
import re

from agent_replay.parser import parse_jsonl
from agent_replay.render import DISPLAY_TRUNCATE_LIMIT, render_to_file


def _write_jsonl(path, records):
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _sample_records(session_id):
    big_output = "y" * 60_000
    return [
        {"ts": "2026-07-07T00:00:00Z", "session_id": session_id, "event_type": "UserPromptSubmit",
         "tool_name": None, "tool_input": "please refactor the parser", "tool_output": None,
         "duration_ms": None, "cwd": "/proj", "error": None},
        {"ts": "2026-07-07T00:00:01Z", "session_id": session_id, "event_type": "PostToolUse",
         "tool_name": "Bash", "tool_input": {"command": "ls"}, "tool_output": {"stdout": "a.py"},
         "duration_ms": 42.0, "cwd": "/proj", "error": None},
        {"ts": "2026-07-07T00:00:02Z", "session_id": session_id, "event_type": "PostToolUse",
         "tool_name": "Bash", "tool_input": {"command": "boom"}, "tool_output": {"stderr": "bad"},
         "duration_ms": 5.0, "cwd": "/proj", "error": "exit code 1"},
        {"ts": "2026-07-07T00:00:03Z", "session_id": session_id, "event_type": "PostToolUse",
         "tool_name": "Read", "tool_input": {"file": "big.txt"},
         "tool_output": {"truncated": True, "preview": big_output[:100]},
         "duration_ms": 3.0, "cwd": "/proj", "error": None},
    ]


def test_render_produces_single_offline_html_file(tmp_path):
    session_id = "render-1"
    jsonl_path = tmp_path / f"{session_id}.jsonl"
    _write_jsonl(jsonl_path, _sample_records(session_id))
    session = parse_jsonl(jsonl_path)

    output_path = tmp_path / "reports" / f"{session_id}.html"
    render_to_file(session, output_path)

    assert output_path.exists()
    html = output_path.read_text(encoding="utf-8")

    # single file: no external CDN references
    assert "http://" not in html
    assert "https://" not in html
    assert not re.search(r'<link[^>]+href=', html)
    assert not re.search(r'<script[^>]+src=', html)

    # content sanity
    assert "please refactor the parser" in html
    assert "Bash" in html
    assert "FAILED" in html

    # size well under 2MB for a normal session
    assert output_path.stat().st_size < 2 * 1024 * 1024


class _CardBodyTagCollector(html.parser.HTMLParser):
    """Collects every start/end tag name that the HTML parser sees while
    inside a `.card-body-inner` div. If event data is escaped correctly,
    the parser should never see any tags there beyond the template's own
    `h4`/`pre`/`div` markup -- any extra `div`/`script`/etc tag name means a
    raw, unescaped fragment from tool_input/tool_output leaked into the DOM
    as real markup instead of text.
    """

    ALLOWED = {"h4", "pre", "div"}

    def __init__(self):
        super().__init__()
        self.depth = None  # None == not inside a card-body-inner div
        self.stray_tags = []

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if self.depth is None:
            if tag == "div" and "card-body-inner" in (attrs_dict.get("class") or ""):
                self.depth = 1
            return
        self.depth += 1
        if tag not in self.ALLOWED:
            self.stray_tags.append(tag)

    def handle_endtag(self, tag):
        if self.depth is not None:
            self.depth -= 1
            if self.depth == 0:
                self.depth = None


def test_render_escapes_dangerous_tool_input_fragments(tmp_path):
    """Regression test: a tool_input containing raw HTML/Jinja-looking
    fragments (as happens when the recorded content is itself a .j2
    template's source) must be HTML-escaped, not injected as live markup
    that breaks the page structure or leaks outside the card container.
    """
    session_id = "render-xss"
    dangerous = (
        'plain text\n'
        '</script>\n'
        '"quoted value"\n'
        '{% endfor %}\n'
        '<div>test</div></div></div>\n'
        'trailing line'
    )
    records = [
        {"ts": "2026-07-07T00:00:00Z", "session_id": session_id, "event_type": "UserPromptSubmit",
         "tool_name": None, "tool_input": "reproduce escaping bug", "tool_output": None,
         "duration_ms": None, "cwd": "/proj", "error": None},
        {"ts": "2026-07-07T00:00:01Z", "session_id": session_id, "event_type": "PostToolUse",
         "tool_name": "Write", "tool_input": {"content": dangerous}, "tool_output": {"ok": True},
         "duration_ms": 7.0, "cwd": "/proj", "error": None},
    ]
    jsonl_path = tmp_path / f"{session_id}.jsonl"
    _write_jsonl(jsonl_path, records)
    session = parse_jsonl(jsonl_path)

    output_path = tmp_path / "reports" / f"{session_id}.html"
    render_to_file(session, output_path)
    html_text = output_path.read_text(encoding="utf-8")

    # (a) `</script>` must never appear in a form that would prematurely
    # close a real <script> block. There should be exactly one <script>
    # element in the whole document (the app's own filter script) -- if
    # escaping failed and the fragment ended up right before/inside it,
    # extra/broken script boundaries would appear.
    assert html_text.count("<script>") == 1
    assert html_text.count("</script>") == 1

    # (b) the raw, unescaped fragment must not appear verbatim -- it must
    # show up HTML-escaped instead.
    assert "<div>test</div>" not in html_text
    assert "&lt;div&gt;test&lt;/div&gt;" in html_text
    assert "{% endfor %}" in html_text  # inert text, fine to appear literally

    # (b) overall document nesting must stay balanced: every raw `<div`
    # open must have a matching `</div>` close. An unescaped injected
    # `</div></div>` (as in `dangerous` above) would break this balance.
    assert html_text.count("<div") == html_text.count("</div>")

    # (b) structurally: no stray tags should be observable *inside* the
    # card-body-inner container -- the injected fragment must be plain
    # text there, not parsed-as-markup that leaks/closes elements early.
    collector = _CardBodyTagCollector()
    collector.feed(html_text)
    assert collector.stray_tags == []


def test_render_truncates_oversized_event_content_for_display(tmp_path):
    """An event whose pretty-printed output exceeds DISPLAY_TRUNCATE_LIMIT
    must be capped in the rendered HTML with a distinct "truncated for
    display" marker (separate from the JSONL-layer `truncated` marker),
    and must not blow up the per-event content length."""
    session_id = "render-display-cap"
    big_output = {"stdout": "z" * (DISPLAY_TRUNCATE_LIMIT * 3)}
    small_output = {"stdout": "small and complete"}
    records = [
        {"ts": "2026-07-07T00:00:00Z", "session_id": session_id, "event_type": "UserPromptSubmit",
         "tool_name": None, "tool_input": "do a big thing", "tool_output": None,
         "duration_ms": None, "cwd": "/proj", "error": None},
        {"ts": "2026-07-07T00:00:01Z", "session_id": session_id, "event_type": "PostToolUse",
         "tool_name": "Bash", "tool_input": {"command": "big"}, "tool_output": big_output,
         "duration_ms": 1.0, "cwd": "/proj", "error": None},
        {"ts": "2026-07-07T00:00:02Z", "session_id": session_id, "event_type": "PostToolUse",
         "tool_name": "Bash", "tool_input": {"command": "small"}, "tool_output": small_output,
         "duration_ms": 1.0, "cwd": "/proj", "error": None},
    ]
    jsonl_path = tmp_path / f"{session_id}.jsonl"
    _write_jsonl(jsonl_path, records)
    session = parse_jsonl(jsonl_path)

    output_path = tmp_path / "reports" / f"{session_id}.html"
    render_to_file(session, output_path)
    html_text = output_path.read_text(encoding="utf-8")

    # display-truncation marker present, distinct from the JSONL-layer note
    assert "content truncated for display" in html_text
    assert "z" * DISPLAY_TRUNCATE_LIMIT not in html_text  # capped well below full size

    # the small, non-truncated event is rendered in full with no marker
    assert "small and complete" in html_text
    small_card_idx = html_text.index("small and complete")
    nearby = html_text[max(0, small_card_idx - 500):small_card_idx + 50]
    assert "content truncated for display" not in nearby


def test_render_shows_subagent_badge_when_agent_type_present(tmp_path):
    session_id = "render-agent-badge"
    records = [
        {"ts": "2026-07-07T00:00:00Z", "session_id": session_id, "event_type": "UserPromptSubmit",
         "tool_name": None, "tool_input": "do something", "tool_output": None,
         "duration_ms": None, "cwd": "/proj", "error": None},
        {"ts": "2026-07-07T00:00:01Z", "session_id": session_id, "event_type": "PostToolUse",
         "tool_name": "Bash", "tool_input": {"command": "ls"}, "tool_output": {"stdout": "ok"},
         "duration_ms": 5.0, "cwd": "/proj", "error": None,
         "agent_id": "agent-7", "agent_type": "Explore"},
    ]
    jsonl_path = tmp_path / f"{session_id}.jsonl"
    _write_jsonl(jsonl_path, records)
    session = parse_jsonl(jsonl_path)

    output_path = tmp_path / "reports" / f"{session_id}.html"
    render_to_file(session, output_path)
    html_text = output_path.read_text(encoding="utf-8")

    assert "subagent &middot; Explore" in html_text


def test_render_shows_stop_reply_block(tmp_path):
    session_id = "render-stop-reply"
    records = [
        {"ts": "2026-07-07T00:00:00Z", "session_id": session_id, "event_type": "UserPromptSubmit",
         "tool_name": None, "tool_input": "do something", "tool_output": None,
         "duration_ms": None, "cwd": "/proj", "error": None},
        {"ts": "2026-07-07T00:00:01Z", "session_id": session_id, "event_type": "Stop",
         "tool_name": None, "tool_input": None, "tool_output": None, "duration_ms": None,
         "cwd": "/proj", "error": None, "assistant_text": "the task is complete"},
    ]
    jsonl_path = tmp_path / f"{session_id}.jsonl"
    _write_jsonl(jsonl_path, records)
    session = parse_jsonl(jsonl_path)

    output_path = tmp_path / "reports" / f"{session_id}.html"
    render_to_file(session, output_path)
    html_text = output_path.read_text(encoding="utf-8")

    assert "reply-card" in html_text
    assert "Assistant reply" in html_text
    assert "the task is complete" in html_text


def test_render_chapters_are_collapsed_by_default_with_summary_stats(tmp_path):
    """Each UserPromptSubmit chapter must render as a <details> that starts
    collapsed (no `open` attribute) and whose <summary> header shows the
    tool call count and, when present, a failure count -- so a failing
    chapter is visible at a glance even while collapsed."""
    session_id = "render-chapters"
    records = [
        {"ts": "2026-07-07T00:00:00Z", "session_id": session_id, "event_type": "UserPromptSubmit",
         "tool_name": None, "tool_input": "first prompt", "tool_output": None,
         "duration_ms": None, "cwd": "/proj", "error": None},
        {"ts": "2026-07-07T00:00:01Z", "session_id": session_id, "event_type": "PostToolUse",
         "tool_name": "Bash", "tool_input": {"command": "ls"}, "tool_output": {"stdout": "ok"},
         "duration_ms": 5.0, "cwd": "/proj", "error": None},
        {"ts": "2026-07-07T00:00:02Z", "session_id": session_id, "event_type": "UserPromptSubmit",
         "tool_name": None, "tool_input": "second prompt", "tool_output": None,
         "duration_ms": None, "cwd": "/proj", "error": None},
        {"ts": "2026-07-07T00:00:03Z", "session_id": session_id, "event_type": "PostToolUse",
         "tool_name": "Bash", "tool_input": {"command": "boom"}, "tool_output": {"stderr": "bad"},
         "duration_ms": 5.0, "cwd": "/proj", "error": "exit code 1"},
    ]
    jsonl_path = tmp_path / f"{session_id}.jsonl"
    _write_jsonl(jsonl_path, records)
    session = parse_jsonl(jsonl_path)

    output_path = tmp_path / "reports" / f"{session_id}.html"
    render_to_file(session, output_path)
    html_text = output_path.read_text(encoding="utf-8")

    # chapters are <details> elements, and none of them are pre-opened
    assert html_text.count("<details class=\"chapter\">") == 2
    assert "<details class=\"chapter\" open>" not in html_text
    assert re.search(r'<details class="chapter"[^>]*\bopen\b', html_text) is None

    # header shows per-chapter tool-call count
    assert "1 tool call" in html_text

    # the failing chapter's header surfaces a failure count marker
    assert "chapter-stat-fail" in html_text
    assert "1 failed" in html_text


def test_render_legacy_event_without_new_fields_renders_without_error(tmp_path):
    session_id = "render-legacy"
    records = [
        {"ts": "2026-01-01T00:00:00Z", "session_id": session_id, "event_type": "UserPromptSubmit",
         "tool_name": None, "tool_input": "legacy prompt", "tool_output": None,
         "duration_ms": None, "cwd": "/legacy", "error": None},
        {"ts": "2026-01-01T00:00:01Z", "session_id": session_id, "event_type": "PostToolUse",
         "tool_name": "Bash", "tool_input": {"command": "ls"}, "tool_output": {"stdout": "ok"},
         "duration_ms": 1.0, "cwd": "/legacy", "error": None},
    ]
    jsonl_path = tmp_path / f"{session_id}.jsonl"
    _write_jsonl(jsonl_path, records)
    session = parse_jsonl(jsonl_path)

    output_path = tmp_path / "reports" / f"{session_id}.html"
    render_to_file(session, output_path)
    html_text = output_path.read_text(encoding="utf-8")

    assert "legacy prompt" in html_text
    assert '<span class="agent-badge">' not in html_text
