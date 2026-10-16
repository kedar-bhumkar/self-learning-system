"""Capture pipeline: transcript -> redacted, classified candidates in the inbox.

Stdlib only at import time. Worker entry: python -m learned.capture <transcript> <session_id> <cwd>
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from .config import Thresholds, learned_home, project_name
from .fsutil import atomic_write_text, create_numbered
from .models import Candidate
from .redact import redact
from .store import Store
from .transcript import extract_windows


def log_error(home: Path, what: str, exc: BaseException | None = None) -> None:
    """Append a one-line timestamped entry to <store>/capture.log. Never raises."""
    try:
        detail = ""
        if exc is not None:
            tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            detail = " | " + " / ".join(l.strip() for l in tb.splitlines() if l.strip())
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        line = f"{stamp} {what}{detail}".replace("\r", " ").replace("\n", " ")
        Path(home).mkdir(parents=True, exist_ok=True)
        with open(Path(home) / "capture.log", "a", encoding="utf-8", newline="\n") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _read_watermark(path: Path) -> int:
    try:
        return int(json.loads(path.read_text(encoding="utf-8")).get("last_line", 0))
    except (OSError, ValueError, AttributeError, TypeError):
        return 0


def capture_session(transcript: Path, session_id: str, cwd: str, judge, store: Store,
                    th: Thresholds | None = None) -> int:
    """Write candidate facts for new windows of a session; returns candidates written."""
    th = th or Thresholds()
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
            except Exception as e:
                # API down / no key: store the rest unclassified, no further calls
                judge_ok = False
                log_error(store.home, f"judge failed for session {session_id}; "
                          "storing unclassified", e)
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
        atomic_write_text(meta, json.dumps({"cwd": cwd}, ensure_ascii=False) + "\n")
    if candidates:
        create_numbered(inbox, session_id, ".jsonl",
                        "".join(c.to_json() + "\n" for c in candidates))
    done.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(done, json.dumps({"last_line": max(w.line_no for w in new)}))
    return len(candidates)


def main(argv: list[str]) -> int:
    home = None
    try:
        home = learned_home()
        transcript, session_id, cwd = argv[0], argv[1], argv[2]
        from .judge import JevJudge  # lazy: typesafe_sdk loads only inside JevJudge
        capture_session(Path(transcript), session_id, cwd, JevJudge(), Store(home))
    except Exception as e:
        if home is not None:
            log_error(home, f"capture failed argv={argv!r}", e)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
