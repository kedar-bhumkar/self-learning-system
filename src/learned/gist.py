"""Project gist refresh and the headless `claude -p` runner."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .fsutil import atomic_write_text
from .logutil import log_line
from .records import Record
from .store import Store

_DOC_MAX = 6000
_GIST_MAX = 4000


def claude_p(prompt: str, timeout: int = 120) -> str:
    """Run a headless Claude Code call (haiku) and return its text output.

    The prompt goes through stdin (no command-line length limit). LEARNED_EXTRACTOR=1 makes
    our own hooks ignore the run, and the cwd is a throwaway temp dir.
    """
    exe = shutil.which("claude") or "claude"
    env = {**os.environ, "LEARNED_EXTRACTOR": "1"}
    with tempfile.TemporaryDirectory(prefix="learned-claude-") as cwd:
        res = subprocess.run(
            [exe, "-p", "--model", "haiku", "--output-format", "text",
             "--no-session-persistence", "--strict-mcp-config"],
            input=prompt, capture_output=True, encoding="utf-8", errors="replace",
            timeout=timeout, env=env, cwd=cwd)
    if res.returncode != 0:
        raise RuntimeError(f"claude -p failed ({res.returncode}): "
                           f"{(res.stderr or res.stdout).strip()[:500]}")
    return res.stdout.strip()


_HEADINGS = ("Purpose", "Architecture", "Status")
_FENCE = re.compile(r"^\s*```[^\n]*\n(.*?)\n?```\s*$", re.S)


def _valid_gist(out: str) -> str | None:
    """Strip one surrounding code fence; return the text only if all three headings exist."""
    out = (out or "").replace("\r\n", "\n").strip()
    m = _FENCE.match(out)
    if m:
        out = m.group(1).strip()
    for h in _HEADINGS:
        if not re.search(rf"^##[ \t]+{h}[ \t]*$", out, re.M):
            return None
    return out


def _read_doc(directory: Path, name: str) -> str:
    try:
        return (directory / name).read_text(encoding="utf-8", errors="replace")[:_DOC_MAX]
    except OSError:
        return ""


def _project_cwd(store: Store, project: str) -> Path | None:
    try:
        meta = json.loads((store.home / "projects" / project / "meta.json")
                          .read_text(encoding="utf-8"))
        cwd = meta.get("cwd")
        return Path(cwd) if cwd else None
    except (OSError, ValueError, AttributeError):
        return None


def _prompt(project: str, readme: str, claude_md: str, previous: str,
            records: list[Record]) -> str:
    facts = "\n".join(f"- ({r.kind}) {' '.join(r.body.split())}" for r in records) or "(none)"
    return (
        f"Write a short project gist for the project '{project}'. It is background "
        "context for future coding sessions.\n\n"
        "Output plain markdown with exactly these three sections and nothing else "
        "(no preamble, no code fence, no frontmatter):\n"
        "## Purpose\n## Architecture\n## Status\n"
        "Keep it under 150 words. State only what the material below supports.\n\n"
        f"README.md:\n{readme or '(missing)'}\n\n"
        f"CLAUDE.md:\n{claude_md or '(missing)'}\n\n"
        f"Previous gist:\n{previous or '(none)'}\n\n"
        f"Recent decisions, rejections and preferences recorded for this project:\n{facts}\n")


def refresh_gists(store: Store, runner, projects: set[str],
                  records: dict[str, list[Record]] | None = None) -> list[str]:
    """Rewrite projects/<p>/gist.md for each project; returns the projects refreshed.

    `records` maps a project to the records created/merged this run; when omitted the
    project's active records are used. A runner failure keeps the previous gist.
    """
    done: list[str] = []
    for project in sorted(projects):
        gist_path = store.home / "projects" / project / "gist.md"
        cwd = _project_cwd(store, project)
        readme = _read_doc(cwd, "README.md") if cwd else ""
        claude_md = _read_doc(cwd, "CLAUDE.md") if cwd else ""
        try:
            previous = gist_path.read_text(encoding="utf-8")
        except OSError:
            previous = ""
        recs = (records or {}).get(project)
        if recs is None:
            recs = store.load_all(project=project)
        try:
            out = runner(_prompt(project, readme, claude_md, previous, recs))
        except Exception:
            continue
        out = _valid_gist(out)
        if out is None:
            log_line(store.home, "consolidate.log",
                     f"gist for {project} rejected: runner output lacks "
                     "'## Purpose' / '## Architecture' / '## Status'; kept previous gist")
            continue
        atomic_write_text(gist_path, out[:_GIST_MAX] + "\n")
        done.append(project)
    return done
