"""SessionEnd hook: spawn a detached capture worker and return at once (stdlib only).

Register with a timeout of 5 seconds. Always exits 0.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

_DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
_NEW_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)


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
        spawn([sys.executable, "-m", "learned.capture", transcript, session_id, cwd],
              creationflags=_DETACHED_PROCESS | _NEW_GROUP, close_fds=True,
              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
              env=dict(os.environ))
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
