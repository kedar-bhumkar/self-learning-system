"""Run the hooks as real subprocesses with UTF-8 bytes on stdin (Review Focus: Windows
code pages, paths with spaces, emoji)."""
import json
import os
import subprocess
import sys

from learned.records import Record, new_id
from learned.store import Store

PROJ = "my caf\u00e9 proj \U0001F600"
CWD = "C:\\Users\\Me\\My Caf\u00e9 Proj \U0001F600"


def run(module, payload: dict, home):
    env = {**os.environ, "LEARNED_HOME": str(home), "PYTHONIOENCODING": "cp1252"}
    env.pop("LEARNED_EXTRACTOR", None)
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return subprocess.run([sys.executable, "-m", module], input=data, capture_output=True,
                          env=env, cwd=str(home.parent), timeout=30)


def test_session_start_subprocess_utf8_in_ascii_json_out(tmp_path):
    home = tmp_path / "store"
    s = Store(home)
    s.init()
    body = "Chose caf\u00e9 layout \U0001F600 \u2615"
    s.save(Record(id=new_id(body, PROJ, "project"), kind="decision", scope="project",
                  project=PROJ, tags=[], sources=["s"], confidence="stated", status="active",
                  created="2026-01-01", last_seen="2026-01-02", body=body))
    r = run("learned.hooks.session_start", {"session_id": "s", "cwd": CWD}, home)
    assert r.returncode == 0
    assert r.stdout.decode("ascii")  # raises if any non-ASCII byte was printed
    ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
    assert body in ctx


def test_session_start_subprocess_garbage_stdin_exits_zero(tmp_path):
    home = tmp_path / "store"
    Store(home).init()
    env = {**os.environ, "LEARNED_HOME": str(home)}
    r = subprocess.run([sys.executable, "-m", "learned.hooks.session_start"],
                       input=b"\xff\xfe not json", capture_output=True, env=env, timeout=30)
    assert r.returncode == 0 and r.stdout == b""


def test_session_end_subprocess_utf8_exit_zero(tmp_path):
    home = tmp_path / "store"
    r = run("learned.hooks.session_end",
            {"session_id": "s", "transcript_path": str(tmp_path / "no caf\u00e9.jsonl"),
             "cwd": CWD}, home)
    assert r.returncode == 0 and r.stdout == b""
    assert home.is_dir()  # the worker's neutral cwd was created


def test_session_end_subprocess_garbage_stdin_exits_zero(tmp_path):
    env = {**os.environ, "LEARNED_HOME": str(tmp_path / "store")}
    r = subprocess.run([sys.executable, "-m", "learned.hooks.session_end"],
                       input=b"\xff\xfe{{", capture_output=True, env=env, timeout=30)
    assert r.returncode == 0
