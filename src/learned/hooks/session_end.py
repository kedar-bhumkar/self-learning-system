"""SessionEnd hook: spawn a detached capture worker and return at once (stdlib only).

Register with a timeout of 5 seconds. Always exits 0.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

_DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
_NEW_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
_BREAKAWAY = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)


def _worker_cwd() -> str:
    """Neutral working directory for the worker (never the session cwd)."""
    try:
        from ..config import learned_home
        home = learned_home()
        home.mkdir(parents=True, exist_ok=True)
        return str(home)
    except Exception:
        return tempfile.gettempdir()


def main(stdin_text: str, spawn=subprocess.Popen) -> int:
    try:
        if os.environ.get("LEARNED_EXTRACTOR"):
            return 0
        data = json.loads(stdin_text)
        session_id = data.get("session_id")
        transcript = data.get("transcript_path")
        cwd = data.get("cwd") or ""
        if not (isinstance(session_id, str) and session_id
                and isinstance(transcript, str) and transcript and isinstance(cwd, str)):
            return 0
        args = [sys.executable, "-P", "-m", "learned.capture", transcript, session_id, cwd]
        kw = dict(close_fds=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                  stderr=subprocess.DEVNULL, env=dict(os.environ), cwd=_worker_cwd())
        base = _DETACHED_PROCESS | _NEW_GROUP
        try:
            spawn(args, creationflags=base | _BREAKAWAY, **kw)
        except OSError:  # job object forbids breakaway: retry once without it
            spawn(args, creationflags=base, **kw)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    try:
        text = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    except Exception:
        text = ""
    main(text)
    sys.exit(0)
