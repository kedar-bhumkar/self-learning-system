import json
import subprocess
import sys
import tempfile
import time

import pytest

from learned.hooks.session_end import main

BREAKAWAY = 0x01000000
DETACHED = 0x8
NEWGROUP = 0x200


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.delenv("LEARNED_EXTRACTOR", raising=False)
    monkeypatch.setenv("LEARNED_HOME", str(tmp_path / "store"))


class Handle:
    """Process handle that fails the test if the hook waits on it."""

    def _fail(self, *a, **k):
        raise AssertionError("hook must not wait on the worker")

    wait = communicate = poll = _fail


class Spawn:
    def __init__(self, fail_first=0):
        self.calls = []
        self.fail_first = fail_first

    def __call__(self, args, **kw):
        self.calls.append((args, kw))
        if len(self.calls) <= self.fail_first:
            raise OSError("breakaway denied")
        return Handle()


PAYLOAD = json.dumps({"session_id": "abc", "transcript_path": "C:\\a b\\t.jsonl",
                      "cwd": "C:\\My Proj\\caf\u00e9", "hook_event_name": "SessionEnd"})


def test_hook_spawns_detached_and_returns_zero(tmp_path):
    sp = Spawn()
    assert main(PAYLOAD, spawn=sp) == 0
    (args, kw), = sp.calls
    assert args == [sys.executable, "-P", "-m", "learned.capture", "C:\\a b\\t.jsonl", "abc",
                    "C:\\My Proj\\caf\u00e9"]
    flags = kw["creationflags"]
    assert flags & DETACHED and flags & NEWGROUP and flags & BREAKAWAY
    assert kw["close_fds"] is True
    assert isinstance(kw["env"], dict)
    for k in ("stdin", "stdout", "stderr"):
        assert kw[k] == subprocess.DEVNULL
    assert kw["cwd"] == str(tmp_path / "store")  # not the session cwd
    assert (tmp_path / "store").is_dir()


def test_hook_falls_back_to_tempdir_when_store_unusable(monkeypatch, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("LEARNED_HOME", str(blocker / "sub"))
    sp = Spawn()
    assert main(PAYLOAD, spawn=sp) == 0
    assert sp.calls[0][1]["cwd"] == tempfile.gettempdir()


def test_hook_retries_once_without_breakaway_on_oserror():
    sp = Spawn(fail_first=1)
    assert main(PAYLOAD, spawn=sp) == 0
    assert len(sp.calls) == 2
    assert sp.calls[0][1]["creationflags"] & BREAKAWAY
    f2 = sp.calls[1][1]["creationflags"]
    assert not f2 & BREAKAWAY and f2 & DETACHED and f2 & NEWGROUP
    assert sp.calls[0][0] == sp.calls[1][0]


def test_hook_gives_up_quietly_when_both_spawns_fail():
    sp = Spawn(fail_first=5)
    assert main(PAYLOAD, spawn=sp) == 0
    assert len(sp.calls) == 2


@pytest.mark.parametrize("text", ["", "not json", "[]", "{}", '{"session_id": "x"}',
                                  '{"transcript_path": 5, "session_id": "x"}'])
def test_hook_garbage_stdin_returns_zero(text):
    sp = Spawn()
    assert main(text, spawn=sp) == 0
    assert sp.calls == []


def test_hook_skips_when_extractor_env_set(monkeypatch):
    monkeypatch.setenv("LEARNED_EXTRACTOR", "1")
    sp = Spawn()
    assert main(PAYLOAD, spawn=sp) == 0
    assert sp.calls == []


def test_hook_runs_under_half_second():
    sp = Spawn()
    t0 = time.perf_counter()
    rc = main(PAYLOAD, spawn=sp)
    elapsed = time.perf_counter() - t0
    assert rc == 0 and len(sp.calls) == 1  # the real path through main reached spawn
    assert elapsed < 0.5
