import json
import subprocess
import sys
import time

import pytest

from learned.hooks.session_end import main


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("LEARNED_EXTRACTOR", raising=False)


class Spawn:
    def __init__(self):
        self.calls = []

    def __call__(self, args, **kw):
        self.calls.append((args, kw))
        return object()


PAYLOAD = json.dumps({"session_id": "abc", "transcript_path": "C:\\a b\\t.jsonl",
                      "cwd": "C:\\My Proj\\caf\u00e9", "hook_event_name": "SessionEnd"})


def test_hook_spawns_detached_and_returns_zero():
    sp = Spawn()
    assert main(PAYLOAD, spawn=sp) == 0
    (args, kw), = sp.calls
    assert args == [sys.executable, "-m", "learned.capture", "C:\\a b\\t.jsonl", "abc",
                    "C:\\My Proj\\caf\u00e9"]
    flags = kw["creationflags"]
    assert flags & getattr(subprocess, "DETACHED_PROCESS", 0x8)
    assert flags & getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
    assert kw["close_fds"] is True
    assert isinstance(kw["env"], dict)
    for k in ("stdin", "stdout", "stderr"):
        assert kw[k] == subprocess.DEVNULL


@pytest.mark.parametrize("text", ["", "not json", "[]", "{}", '{"session_id": "x"}',
                                  '{"transcript_path": 5, "session_id": "x"}'])
def test_hook_garbage_stdin_returns_zero(text):
    sp = Spawn()
    assert main(text, spawn=sp) == 0
    assert sp.calls == []


def test_hook_swallows_spawn_errors():
    def boom(*a, **k):
        raise OSError("nope")

    assert main(PAYLOAD, spawn=boom) == 0


def test_hook_skips_when_extractor_env_set(monkeypatch):
    monkeypatch.setenv("LEARNED_EXTRACTOR", "1")
    sp = Spawn()
    assert main(PAYLOAD, spawn=sp) == 0
    assert sp.calls == []


def test_hook_runs_under_half_second():
    sp = Spawn()
    t0 = time.perf_counter()
    main(PAYLOAD, spawn=sp)
    assert time.perf_counter() - t0 < 0.5
