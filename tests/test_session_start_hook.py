import json
import subprocess
import sys

import pytest

from learned.core import write_core_md
from learned.config import Thresholds
from learned.hooks.session_start import main
from learned.records import Record, new_id
from learned.store import Store


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("LEARNED_EXTRACTOR", raising=False)


def rec(body, scope="global", project="proj", kind="preference"):
    return Record(id=new_id(body, project, scope), kind=kind, scope=scope, project=project,
                  tags=[], sources=["s"], confidence="stated", status="active",
                  created="2026-01-01", last_seen="2026-01-02", body=body)


def payload(cwd="C:\\work\\proj"):
    return json.dumps({"session_id": "s", "cwd": cwd, "hook_event_name": "SessionStart"})


def test_hook_emits_additional_context_json(tmp_path):
    s = Store(tmp_path)
    s.init()
    s.save(rec("Prefers uv over pip café"))
    s.save(rec("Rejected Redis", scope="project", kind="rejection"))
    out = main(payload(), home=tmp_path)
    assert out.isascii()
    d = json.loads(out)
    assert d["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    ctx = d["hookSpecificOutput"]["additionalContext"]
    assert "Prefers uv over pip café" in ctx and "Rejected Redis" in ctx
    assert ctx.startswith("Learned notes from past sessions")


def test_hook_uses_core_md_when_present(tmp_path):
    s = Store(tmp_path)
    s.init()
    s.save(rec("From records"))
    write_core_md(s, Thresholds())
    (tmp_path / "core.md").write_text(
        "Learned notes from past sessions (x)\nGlobal:\n- FROM CORE MD\n", encoding="utf-8")
    ctx = json.loads(main(payload(), home=tmp_path))["hookSpecificOutput"]["additionalContext"]
    assert "FROM CORE MD" in ctx and "From records" not in ctx


def test_hook_prints_nothing_for_empty_store(tmp_path):
    Store(tmp_path).init()
    assert main(payload(), home=tmp_path) == ""
    assert main(payload(), home=tmp_path / "missing") == ""


def test_hook_swallows_errors_and_exits_clean(tmp_path):
    s = Store(tmp_path)
    s.init()
    s.save(rec("Good one"))
    (tmp_path / "global" / "corrupt.md").write_text("---\nbad", encoding="utf-8")
    (tmp_path / "global" / "binary.md").write_bytes(b"\xff\xfe\x00garbage")
    out = main(payload(), home=tmp_path)
    assert "Good one" in json.loads(out)["hookSpecificOutput"]["additionalContext"]
    for text in ("", "not json", "[]", "null", '{"cwd": 5}'):
        assert isinstance(main(text, home=tmp_path), str)


def test_hook_handles_unicode_and_spaces_in_cwd(tmp_path):
    s = Store(tmp_path)
    s.init()
    s.save(rec("Chose café layout", scope="project", project="my café proj", kind="decision"))
    out = main(payload("C:\\Users\\Me\\My Café Proj"), home=tmp_path)
    assert "Chose caf" in json.loads(out)["hookSpecificOutput"]["additionalContext"]
    assert out.isascii()


def test_hook_skips_when_extractor_env_set(tmp_path, monkeypatch):
    s = Store(tmp_path)
    s.init()
    s.save(rec("Prefers uv"))
    monkeypatch.setenv("LEARNED_EXTRACTOR", "1")
    assert main(payload(), home=tmp_path) == ""


def test_module_entry_exits_zero_on_garbage(tmp_path):
    r = subprocess.run([sys.executable, "-m", "learned.hooks.session_start"], input=b"junk",
                       capture_output=True, env={**__import__("os").environ,
                                                 "LEARNED_HOME": str(tmp_path / "none")})
    assert r.returncode == 0 and r.stdout == b""
