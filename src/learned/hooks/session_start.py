"""SessionStart hook: inject the capped learned-notes block (stdlib only at import).

Always exits 0 and never raises; prints nothing when there is nothing to say.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main(stdin_text: str, home: Path | None = None) -> str:
    try:
        if os.environ.get("LEARNED_EXTRACTOR"):
            return ""
        from ..config import Thresholds, learned_home, project_name
        from ..core import combine_blocks, global_block, project_block
        from ..store import Store

        home = Path(home) if home is not None else learned_home()
        if not home.is_dir():
            return ""
        th = Thresholds()
        store = Store(home)
        cwd = ""
        try:
            data = json.loads(stdin_text)
            if isinstance(data, dict) and isinstance(data.get("cwd"), str):
                cwd = data["cwd"]
        except ValueError:
            pass
        global_text = ""
        core_md = home / "core.md"
        try:
            global_text = core_md.read_text(encoding="utf-8").strip()
        except (OSError, ValueError):
            pass
        if not global_text:
            global_text = global_block(store, th)
        project_text = project_block(store, project_name(cwd), th) if cwd else ""
        text = combine_blocks(global_text, project_text, th.core_max_chars)
        if not text:
            return ""
        return json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                                  "additionalContext": text}},
                          ensure_ascii=True)
    except Exception:
        return ""


if __name__ == "__main__":
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    except Exception:
        raw = ""
    out = main(raw)
    if out:
        sys.stdout.write(out)
        sys.stdout.flush()
    sys.exit(0)
