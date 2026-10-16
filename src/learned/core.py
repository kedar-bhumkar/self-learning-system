"""Core block assembly for SessionStart (stdlib only).

Text is factual background context, never imperative. Everything is capped at
Thresholds.core_max_chars by dropping the lowest-ranked lines.
"""
from __future__ import annotations

from pathlib import Path

from .config import Thresholds
from .fsutil import atomic_write_text
from .store import Store

HEADER = ("Learned notes from past sessions (background context, not instructions; "
          "may be out of date):")
HEADER_PREFIX = "Learned notes from past sessions"
G_TITLE ="Global (how the user works):"
R_TITLE = "Recent decisions and rejections in this project:"
_ITEM_MAX = 400
_GIST_MAX = 2000
_RECENT_N = 5


def _item(r) -> str:
    flags = r.kind + ", " + r.confidence + (", possibly stale" if r.stale else "")
    body = " ".join(r.body.split())
    if len(body) > _ITEM_MAX:
        body = body[: _ITEM_MAX - 3] + "..."
    return f"- ({flags}) {body}"


def _rank_global(recs: list) -> list:
    """stated first, then more sources, then newer last_seen; stale last.

    Stable multi-pass sort (least significant key first), so timestamps of different
    lengths still compare correctly."""
    recs = sorted(recs, key=lambda r: r.last_seen, reverse=True)
    recs.sort(key=lambda r: -len(r.sources))
    recs.sort(key=lambda r: r.confidence != "stated")
    recs.sort(key=lambda r: r.stale)
    return recs


def _is_item(line: str) -> bool:
    return line.startswith("- ")


def _fit(header: str, g: list[str], p: list[str], cap: int) -> str:
    g, p = list(g), list(p)
    while True:
        if not any(_is_item(l) for l in g):
            g = []
        if R_TITLE in p and p[-1] == R_TITLE:
            p.pop()
        if len(p) == 1 and p[0].startswith("Project "):
            p = []
        parts = ([header] if header else []) + g + p
        if not g and not p:
            return ""
        out = "\n".join(parts)
        if len(out) <= cap:
            return out
        if R_TITLE in p:
            p.pop()  # last recent item
        elif any(_is_item(l) for l in g):
            for i in range(len(g) - 1, -1, -1):
                if _is_item(g[i]):
                    del g[i]
                    break
        elif p:
            p.pop()
        else:
            return ""


def _global_lines(store: Store) -> list[str]:
    recs = _rank_global(store.load_all(scope="global"))
    return [G_TITLE] + [_item(r) for r in recs] if recs else []


def _project_lines(store: Store, project: str) -> list[str]:
    lines: list[str] = []
    gist = store.home / "projects" / project / "gist.md"
    try:
        text = gist.read_text(encoding="utf-8")[:_GIST_MAX]
    except OSError:
        text = ""
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        if ln.startswith("#"):
            ln = ln.lstrip("#").strip() + ":"
        lines.append(ln)
    recents = [r for r in store.load_all(scope="project", project=project)
               if r.kind in ("rejection", "decision")]
    recents.sort(key=lambda r: r.last_seen, reverse=True)
    items = [_item(r) for r in recents[:_RECENT_N]]
    if items:
        lines += [R_TITLE] + items
    return ([f"Project {project}:"] + lines) if lines else []


def global_block(store: Store, th: Thresholds) -> str:
    return _fit(HEADER, _global_lines(store), [], th.core_max_chars)


def project_block(store: Store, project: str, th: Thresholds) -> str:
    return _fit("", [], _project_lines(store, project), th.core_max_chars)


def combine_blocks(global_text: str, project_text: str, cap: int) -> str:
    """Join an existing global block (e.g. core.md) and a project block under the cap."""
    g = [l for l in global_text.splitlines()
         if l.strip() and not l.startswith(HEADER_PREFIX)]
    p = [l for l in project_text.splitlines() if l.strip()]
    return _fit(HEADER, g, p, cap)


def build_core(store: Store, project: str | None, th: Thresholds) -> str:
    p = _project_lines(store, project) if project else []
    return _fit(HEADER, _global_lines(store), p, th.core_max_chars)


def write_core_md(store: Store, th: Thresholds) -> Path:
    path = store.home / "core.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, global_block(store, th))
    return path
