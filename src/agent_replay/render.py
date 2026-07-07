"""Render a Session into a single self-contained HTML file."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from .parser import Session

# Max size (in bytes, UTF-8 encoded) of a single event's pretty-printed
# input/output that we'll embed in the HTML. This is a *display* limit,
# independent of the 50KB truncation already applied at JSONL-write time
# (see hooks.py) -- it exists to keep the single-file HTML report from
# ballooning on long sessions with many large tool calls. Bump this if you
# need more content visible per card.
DISPLAY_TRUNCATE_LIMIT = 8 * 1024


def _truncate_for_display(text: str, limit: int = DISPLAY_TRUNCATE_LIMIT) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    head = encoded[:limit].decode("utf-8", errors="ignore")
    return (
        f"{head}\n\n"
        f"... [content truncated for display: showing {limit:,} of "
        f"{len(encoded):,} bytes]"
    )


def _pretty_json(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, indent=2, ensure_ascii=False, sort_keys=False)
        except (TypeError, ValueError):
            text = str(value)
    return _truncate_for_display(text)


def _get_env() -> Environment:
    templates_dir = resources.files("agent_replay") / "templates"
    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        # NOTE: select_autoescape(["html"]) matches on the template *name's*
        # final extension, which for "report.html.j2" is ".j2" -- not
        # ".html" -- so it silently disabled escaping entirely. Force it on.
        autoescape=True,
    )
    env.filters["prettyjson"] = _pretty_json
    return env


def render_session(session: Session) -> str:
    env = _get_env()
    template = env.get_template("report.html.j2")
    return template.render(session=session)


def render_to_file(session: Session, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    html = render_session(session)
    output_path.write_text(html, encoding="utf-8")
    return output_path
