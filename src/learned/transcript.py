"""Claude Code transcript JSONL -> user-turn Windows (stdlib only)."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .models import Window

CONTEXT_MAX = 1500
_SKIP_PREFIXES = ("<system-reminder>", "<local-command", "<command-name>")
_PASTED = re.compile(r"<pasted_content\b[^>]*>.*?</pasted_content>", re.DOTALL)
_REMINDER = re.compile(r"<system-reminder>.*?(?:</system-reminder>|\Z)", re.DOTALL)


def _blocks(content) -> list:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    return []


def _text_of(blocks: list) -> str:
    parts = []
    for b in blocks:
        t = b.get("text")
        if b.get("type") != "text" or not isinstance(t, str):
            continue
        if t.lstrip().startswith(_SKIP_PREFIXES):
            continue
        t = _REMINDER.sub("", t).strip()
        if t:
            parts.append(t)
    return "\n".join(parts)


def _result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content
                         if isinstance(b, dict) and isinstance(b.get("text"), str))
    return ""


def extract_windows(path: Path) -> list[Window]:
    windows: list[Window] = []
    context = ""
    ask_ids: dict[str, str] = {}  # AskUserQuestion tool_use id -> question text
    try:
        fh = open(path, "r", encoding="utf-8", errors="replace")
    except OSError:
        return windows
    with fh:
        for line_no, raw in enumerate(fh, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(obj, dict) or obj.get("isMeta") or obj.get("isSidechain"):
                continue
            kind = obj.get("type")
            if kind not in ("user", "assistant"):
                continue
            msg = obj.get("message")
            if not isinstance(msg, dict):
                continue
            blocks = _blocks(msg.get("content"))
            ts = obj.get("timestamp") if isinstance(obj.get("timestamp"), str) else ""
            if kind == "assistant":
                text = _text_of(blocks).strip()
                if text:
                    context = text[:CONTEXT_MAX]
                for b in blocks:
                    if b.get("type") == "tool_use" and b.get("name") == "AskUserQuestion" \
                            and isinstance(b.get("id"), str):
                        ask_ids[b["id"]] = json.dumps(b.get("input", {}), ensure_ascii=False)
                continue
            # user line: choice answers first, then plain text
            for b in blocks:
                if b.get("type") == "tool_result" and b.get("tool_use_id") in ask_ids:
                    answer = _result_text(b.get("content")).strip()
                    if answer:
                        ctx = (context + "\n" + ask_ids[b["tool_use_id"]]).strip()
                        windows.append(Window(answer, ctx[:CONTEXT_MAX], True, ts, line_no))
            text = _PASTED.sub("", _text_of(blocks)).strip()
            if not text or text.startswith(_SKIP_PREFIXES):
                continue
            windows.append(Window(text, context, False, ts, line_no))
    return windows
