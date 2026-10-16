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
_OUR_PREFIXES = (["-P", "-m"], ["-m"])  # new form first; the bare form is the old one


def hook_entry(python: str, module: str, timeout: int = 5) -> dict:
    return {"type": "command", "command": python, "args": ["-P", "-m", module],
            "timeout": timeout}


def session_end_group(python: str) -> dict:
    return {"hooks": [hook_entry(python, SESSION_END_MODULE)]}


def session_start_group(python: str) -> dict:
    return {"matcher": START_MATCHER, "hooks": [hook_entry(python, SESSION_START_MODULE)]}


def _is_ours(h: object, module: str) -> bool:
    if not isinstance(h, dict):
        return False
    args = h.get("args")
    return (isinstance(args, list) and len(args) >= 1 and args[-1] == module
            and args[:-1] in _OUR_PREFIXES)


def _check(settings: dict) -> dict:
    """Validate the parts of settings we touch; raise ValueError naming the problem."""
    hooks = settings.get("hooks")
    if hooks is None:
        return {}
    if not isinstance(hooks, dict):
        raise ValueError("settings 'hooks' is not an object")
    for event in ("SessionEnd", "SessionStart"):
        groups = hooks.get(event)
        if groups is None:
            continue
        if not isinstance(groups, list):
            raise ValueError(f"settings hooks.{event} is not a list")
        for g in groups:
            if not isinstance(g, dict):
                raise ValueError(f"settings hooks.{event} contains a non-object group")
            if "hooks" in g and not isinstance(g["hooks"], list):
                raise ValueError(f"settings hooks.{event} group has a non-list 'hooks'")
    return hooks


def _merge_event(groups: list, group: dict) -> list:
    new_hook = group["hooks"][0]
    module = new_hook["args"][-1]
    for g in groups:
        for h in g.get("hooks") or []:
            if _is_ours(h, module):  # re-install: update in place, never duplicate
                h["command"] = new_hook["command"]
                h["args"] = list(new_hook["args"])
                h["timeout"] = new_hook["timeout"]
                return groups
    return [*groups, group]


def merge_hooks(settings: dict, session_end: dict, session_start: dict) -> dict:
    """Return a copy of `settings` with the two hook groups added (idempotent, additive)."""
    if not isinstance(settings, dict):
        raise ValueError("settings is not an object")
    out = copy.deepcopy(settings)
    _check(out)
    hooks = out.setdefault("hooks", {})
    for event, group in (("SessionEnd", session_end), ("SessionStart", session_start)):
        hooks[event] = _merge_event(list(hooks.get(event) or []), group)
    return out


def unmerge_hooks(settings: dict) -> dict:
    """Remove only the hooks `merge_hooks` adds (old and new arg forms); leave the rest,
    including empty containers the user already had."""
    if not isinstance(settings, dict):
        raise ValueError("settings is not an object")
    out = copy.deepcopy(settings)
    hooks = _check(out)
    removed_any = False
    for event, module in (("SessionEnd", SESSION_END_MODULE),
                          ("SessionStart", SESSION_START_MODULE)):
        groups = hooks.get(event)
        if not groups:
            continue
        kept, removed = [], False
        for g in groups:
            inner = g.get("hooks")
            if isinstance(inner, list):
                left = [h for h in inner if not _is_ours(h, module)]
                if len(left) != len(inner):
                    removed = True
                    if not left:
                        continue
                    g = {**g, "hooks": left}
            kept.append(g)
        if removed:
            removed_any = True
            if kept:
                hooks[event] = kept
            else:
                del hooks[event]
    if removed_any and not hooks:
        del out["hooks"]
    return out


def _nl(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _markers(text: str) -> tuple[int, int] | None:
    """(start, end_exclusive) of the block, None when absent; ValueError when unbalanced."""
    i, j = text.find(START), text.find(END)
    if i < 0 and j < 0:
        return None
    if i < 0 or j < 0 or j < i:
        raise ValueError("CLAUDE.md has an unbalanced learned-memory marker; fix it by hand")
    return i, j + len(END)


def rule_block(nl: str = "\n") -> str:
    return nl.join([START, *RULE_BODY.split("\n"), END])


def add_rule_block(text: str) -> str:
    """Append the marker-delimited recall rule once, or refresh an outdated one in place
    (keeps the file's line endings)."""
    nl = _nl(text)
    found = _markers(text)
    if found:
        return text[:found[0]] + rule_block(nl) + text[found[1]:]
    if text and not text.endswith("\n"):
        text += nl
    if text:
        text += nl
    return text + rule_block(nl) + nl


def remove_rule_block(text: str) -> str:
    found = _markers(text)
    if not found:
        return text
    i, j = found
    nl = _nl(text)
    tail = text[j:]
    if tail.startswith(nl):
        tail = tail[len(nl):]
    head = text[:i]
    if head.endswith(nl + nl) or (head.endswith(nl) and not tail):
        head = head[: -len(nl)]
    return head + tail


# ---- file-touching CLI -------------------------------------------------------------------
def _read_text(path: Path) -> str | None:
    try:
        return path.read_bytes().decode("utf-8-sig")
    except FileNotFoundError:
        return None


def _backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    dest = path.with_name(f"{path.name}.bak-{datetime.now():%Y%m%d-%H%M%S-%f}")
    shutil.copy2(path, dest)
    return dest


def _write(path: Path, text: str) -> None:
    b = _backup(path)
    if b:
        print(f"backup: {b}")
    from .fsutil import atomic_write_text
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, text)  # written verbatim: line endings are decided by the caller


def _settings_cmd(a, merge: bool) -> int:
    path = Path(a.settings)
    raw = _read_text(path)
    try:
        old = json.loads(raw) if raw is not None else {}
    except ValueError as e:
        raise ValueError(f"{path} is not valid JSON: {e}") from e
    if not isinstance(old, dict):
        raise ValueError(f"{path} is not a JSON object")
    if merge:
        new = merge_hooks(old, session_end_group(a.python), session_start_group(a.python))
    else:
        new = unmerge_hooks(old)
    if new == old:
        print(f"settings: {path}: no change needed")
        return 0
    shown = {"hooks": {k: v for k, v in new.get("hooks", {}).items()
                       if k in ("SessionEnd", "SessionStart")}}
    verb = "merge" if merge else "remove"
    print(f"settings: {path}: {verb}d learned hooks" if a.write else
          f"settings: {path}: would {verb} learned hooks; resulting hook sections:")
    print(json.dumps(shown, indent=2))
    if a.write:
        nl = _nl(raw or "")
        body = json.dumps(new, indent=2, ensure_ascii=False)
        if nl != "\n":
            body = body.replace("\n", nl)
        trailing = raw is None or raw.endswith("\n")
        _write(path, body + (nl if trailing else ""))
    return 0


def _rule_cmd(a) -> int:
    path = Path(a.claude_md)
    old = _read_text(path) or ""
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
    elif a.action == "add":
        print(rule_block())
    else:
        print("(learned-memory block removed; rest of file untouched)")
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
