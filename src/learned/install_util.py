"""Pure helpers for the installer scripts: settings.json hook merge, CLAUDE.md rule block.

Stdlib only. The pure functions do no I/O; `main` is the thin file-touching CLI the
PowerShell scripts call (`python -m learned.install_util ...`).
"""
from __future__ import annotations

import argparse
import copy
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

START = "<!-- learned-memory:start -->"
END = "<!-- learned-memory:end -->"
RULE_BODY = (
    "## Learned memory\n"
    "Before non-trivial work in a project, or before proposing options, use the `recall` "
    "skill to check ~/.claude/learned for earlier decisions and rejections. Treat results "
    "as background, and mention when one changes the plan."
)
SESSION_END_MODULE = "learned.hooks.session_end"
SESSION_START_MODULE = "learned.hooks.session_start"
START_MATCHER = "startup|resume|clear"


def hook_entry(python: str, module: str, timeout: int = 5) -> dict:
    return {"type": "command", "command": python, "args": ["-m", module], "timeout": timeout}


def session_end_group(python: str) -> dict:
    return {"hooks": [hook_entry(python, SESSION_END_MODULE)]}


def session_start_group(python: str) -> dict:
    return {"matcher": START_MATCHER, "hooks": [hook_entry(python, SESSION_START_MODULE)]}


def _is_ours(h: object, module: str) -> bool:
    return isinstance(h, dict) and h.get("args") == ["-m", module]


def _merge_event(groups: list, group: dict) -> list:
    module = group["hooks"][0]["args"][1]
    for g in groups:
        for h in (g.get("hooks") or []) if isinstance(g, dict) else []:
            if _is_ours(h, module):
                h["command"] = group["hooks"][0]["command"]  # idempotent; follows python moves
                return groups
    return [*groups, group]


def merge_hooks(settings: dict, session_end: dict, session_start: dict) -> dict:
    """Return a copy of `settings` with the two hook groups added (idempotent, additive)."""
    out = copy.deepcopy(settings)
    hooks = out.setdefault("hooks", {})
    for event, group in (("SessionEnd", session_end), ("SessionStart", session_start)):
        hooks[event] = _merge_event(list(hooks.get(event) or []), group)
    return out


def unmerge_hooks(settings: dict) -> dict:
    """Remove only the hooks `merge_hooks` adds; everything else is left as it was."""
    out = copy.deepcopy(settings)
    hooks = out.get("hooks")
    if not isinstance(hooks, dict):
        return out
    for event, module in (("SessionEnd", SESSION_END_MODULE),
                          ("SessionStart", SESSION_START_MODULE)):
        groups = hooks.get(event)
        if not isinstance(groups, list):
            continue
        kept = []
        for g in groups:
            if isinstance(g, dict) and isinstance(g.get("hooks"), list):
                inner = [h for h in g["hooks"] if not _is_ours(h, module)]
                if len(inner) != len(g["hooks"]):
                    if not inner:
                        continue
                    g = {**g, "hooks": inner}
            kept.append(g)
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    if not hooks:
        del out["hooks"]
    return out


def _nl(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def add_rule_block(text: str) -> str:
    """Append the marker-delimited recall rule once (keeps the file's line endings)."""
    if START in text and END in text:
        return text
    nl = _nl(text)
    block = nl.join([START, *RULE_BODY.split("\n"), END]) + nl
    if text and not text.endswith("\n"):
        text += nl
    if text:
        text += nl
    return text + block


def remove_rule_block(text: str) -> str:
    i = text.find(START)
    j = text.find(END)
    if i < 0 or j < i:
        return text
    j += len(END)
    nl = _nl(text)
    tail = text[j:]
    for _ in range(1):
        if tail.startswith(nl):
            tail = tail[len(nl):]
    head = text[:i]
    if head.endswith(nl + nl) or (head.endswith(nl) and not tail):
        head = head[: -len(nl)]
    return head + tail


# ---- file-touching CLI -------------------------------------------------------------------
def _read_settings(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a JSON object")
    return data


def _backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    dest = path.with_name(f"{path.name}.bak-{datetime.now():%Y%m%d-%H%M%S}")
    shutil.copy2(path, dest)
    return dest


def _write(path: Path, text: str, write: bool) -> None:
    if not write:
        return
    b = _backup(path)
    if b:
        print(f"backup: {b}")
    from .fsutil import atomic_write_text
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, text)


def _settings_cmd(a, merge: bool) -> int:
    path = Path(a.settings)
    old = _read_settings(path)
    if merge:
        new = merge_hooks(old, session_end_group(a.python), session_start_group(a.python))
    else:
        new = unmerge_hooks(old)
    shown = {"hooks": {k: v for k, v in new.get("hooks", {}).items()
                       if k in ("SessionEnd", "SessionStart")}}
    verb = "merge" if merge else "remove"
    if new == old:
        print(f"settings: {path}: no change needed")
    else:
        print(f"settings: {path}: would {verb} learned hooks; resulting hook sections:"
              if not a.write else f"settings: {path}: {verb}d learned hooks")
        print(json.dumps(shown, indent=2))
        _write(path, json.dumps(new, indent=2, ensure_ascii=False) + "\n", a.write)
    return 0


def _rule_cmd(a) -> int:
    path = Path(a.claude_md)
    try:
        old = path.read_bytes().decode("utf-8-sig")
    except FileNotFoundError:
        old = ""
    new = add_rule_block(old) if a.action == "add" else remove_rule_block(old)
    if new == old:
        print(f"CLAUDE.md: {path}: no change needed")
        return 0
    print(f"CLAUDE.md: {path}: {'wrote' if a.write else 'would write'} ({a.action} rule block)")
    if a.write:
        b = _backup(path)
        if b:
            print(f"backup: {b}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(new.encode("utf-8"))
    else:
        print(new if a.action == "add" else "(rule block removed)")
    return 0


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="learned.install_util")
    sub = p.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("merge-hooks")
    m.add_argument("--settings", required=True)
    m.add_argument("--python", required=True)
    m.add_argument("--write", action="store_true")
    u = sub.add_parser("unmerge-hooks")
    u.add_argument("--settings", required=True)
    u.add_argument("--write", action="store_true")
    r = sub.add_parser("rule")
    r.add_argument("action", choices=["add", "remove"])
    r.add_argument("--claude-md", required=True)
    r.add_argument("--write", action="store_true")
    a = p.parse_args(argv)
    try:
        if a.cmd == "merge-hooks":
            return _settings_cmd(a, True)
        if a.cmd == "unmerge-hooks":
            return _settings_cmd(a, False)
        return _rule_cmd(a)
    except (OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main(sys.argv[1:]))
