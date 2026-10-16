"""Capture pipeline: transcript -> redacted, classified candidates in the inbox.

Stdlib only at import time. Worker entry: python -m learned.capture <transcript> <session_id> <cwd>
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .config import Thresholds, learned_home, project_name
from .models import Candidate
from .redact import redact
from .store import Store
from .transcript import extract_windows


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def _read_watermark(path: Path) -> int:
    try:
        return int(json.loads(path.read_text(encoding="utf-8")).get("last_line", 0))
    except (OSError, ValueError, AttributeError, TypeError):
        return 0


def _inbox_path(inbox: Path, session_id: str) -> Path:
    p = inbox / f"{session_id}.jsonl"
    n = 0
    while p.exists():
        n += 1
        p = inbox / f"{session_id}.{n}.jsonl"
    return p


def capture_session(transcript: Path, session_id: str, cwd: str, judge, store: Store,
                    th: Thresholds = Thresholds()) -> int:
    """Write candidate facts for new windows of a session; returns candidates written."""
    if os.environ.get("LEARNED_EXTRACTOR"):
        return 0
    if cwd and (Path(cwd) / ".no-learn").exists():
        return 0
    windows = extract_windows(Path(transcript))
    inbox = store.home / "inbox"
    done = inbox / ".done" / f"{session_id}.json"
    last = _read_watermark(done)
    new = [w for w in windows if w.line_no > last]
    if len(new) < th.min_user_turns:
        return 0

    store.init()
    project = project_name(cwd)
    candidates: list[Candidate] = []
    judge_ok = True
    for w in new:
        text, context = redact(w.text), redact(w.context)
        kind: str | None = None
        kind_prob = 0.0
        if judge_ok:
            try:
                v = judge.classify_window(text, context)
            except Exception:
                judge_ok = False  # API down: store the rest unclassified, no further calls
            else:
                if v.kind == "none" or v.kind_prob < th.window_min_prob \
                        or v.sensitive_prob >= th.sensitive_drop:
                    continue
                kind, kind_prob = v.kind, v.kind_prob
        candidates.append(Candidate(text, context, kind, kind_prob, project, cwd,
                                    session_id, w.timestamp))

    meta = store.home / "projects" / project / "meta.json"
    if not meta.exists():
        meta.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(meta, json.dumps({"cwd": cwd}, ensure_ascii=False) + "\n")
    if candidates:
        body = "".join(c.to_json() + "\n" for c in candidates)
        _atomic_write(_inbox_path(inbox, session_id), body)
    done.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(done, json.dumps({"last_line": max(w.line_no for w in new)}))
    return len(candidates)


def main(argv: list[str]) -> int:
    try:
        transcript, session_id, cwd = argv[0], argv[1], argv[2]
        from .judge import JevJudge  # lazy: typesafe_sdk loads only inside JevJudge
        capture_session(Path(transcript), session_id, cwd, JevJudge(), Store(learned_home()))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
