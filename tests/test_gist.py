import json
import subprocess

import pytest

from learned import gist
from learned.records import Record, new_id
from learned.store import Store

GIST = "## Purpose\nA thing.\n\n## Architecture\nParts.\n\n## Status\nWorking.\n"


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "store")
    s.init()
    return s


def setup_project(store, tmp_path, name="alpha", readme="README TEXT", claude_md="CLAUDE TEXT"):
    cwd = tmp_path / "work" / name
    cwd.mkdir(parents=True)
    if readme is not None:
        (cwd / "README.md").write_text(readme, encoding="utf-8")
    if claude_md is not None:
        (cwd / "CLAUDE.md").write_text(claude_md, encoding="utf-8")
    meta = store.home / "projects" / name / "meta.json"
    meta.parent.mkdir(parents=True, exist_ok=True)
    meta.write_text(json.dumps({"cwd": str(cwd)}), encoding="utf-8")
    return cwd


def add_record(store, body, project="alpha"):
    r = Record(new_id(body, project), "decision", "project", project, [], ["s"], "stated",
               "active", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", body)
    store.save(r)
    return r


class Runner:
    def __init__(self, reply=GIST):
        self.reply, self.prompts = reply, []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def test_gist_written_with_readme_and_new_records_in_prompt(store, tmp_path):
    setup_project(store, tmp_path, readme="R" * 7000 + "END")
    r = add_record(store, "use sqlite for state")
    runner = Runner()
    out = gist.refresh_gists(store, runner, {"alpha"}, {"alpha": [r]})
    assert out == ["alpha"]
    p = store.home / "projects" / "alpha" / "gist.md"
    assert p.read_text(encoding="utf-8") == GIST
    prompt = runner.prompts[0]
    assert "use sqlite for state" in prompt and "CLAUDE TEXT" in prompt
    assert "R" * 6000 in prompt and "R" * 6001 not in prompt and "END" not in prompt
    assert "Purpose" in prompt and "Architecture" in prompt and "Status" in prompt


def test_default_records_are_the_projects_active_records(store, tmp_path):
    setup_project(store, tmp_path)
    add_record(store, "fact one")
    add_record(store, "other project fact", project="beta")
    runner = Runner()
    gist.refresh_gists(store, runner, {"alpha"})
    assert "fact one" in runner.prompts[0] and "other project fact" not in runner.prompts[0]


def test_missing_cwd_or_readme_still_works(store, tmp_path):
    setup_project(store, tmp_path, name="alpha", readme=None, claude_md=None)
    add_record(store, "a fact")
    add_record(store, "b fact", project="beta")  # no meta.json at all
    out = gist.refresh_gists(store, Runner(), {"alpha", "beta"})
    assert sorted(out) == ["alpha", "beta"]
    assert (store.home / "projects" / "beta" / "gist.md").exists()


def test_runner_failure_keeps_previous_gist(store, tmp_path):
    setup_project(store, tmp_path)
    add_record(store, "a fact")
    p = store.home / "projects" / "alpha" / "gist.md"
    p.write_text("old gist\n", encoding="utf-8")
    assert gist.refresh_gists(store, Runner(RuntimeError("down")), {"alpha"}) == []
    assert p.read_text(encoding="utf-8") == "old gist\n"
    assert gist.refresh_gists(store, Runner("   \n"), {"alpha"}) == []
    assert p.read_text(encoding="utf-8") == "old gist\n"


def test_only_requested_projects_refreshed(store, tmp_path):
    setup_project(store, tmp_path, "alpha")
    setup_project(store, tmp_path, "beta")
    add_record(store, "a", "alpha")
    add_record(store, "b", "beta")
    runner = Runner()
    assert gist.refresh_gists(store, runner, {"beta"}) == ["beta"]
    assert len(runner.prompts) == 1
    assert not (store.home / "projects" / "alpha" / "gist.md").exists()


def test_claude_p_sets_extractor_env(monkeypatch):
    seen = {}

    def fake_run(args, **kw):
        seen["args"], seen["kw"] = args, kw
        return subprocess.CompletedProcess(args, 0, stdout="hello\n", stderr="")

    monkeypatch.setattr(gist.subprocess, "run", fake_run)
    monkeypatch.delenv("LEARNED_EXTRACTOR", raising=False)
    assert gist.claude_p("the prompt", timeout=7) == "hello"
    a, kw = seen["args"], seen["kw"]
    assert a[1:6] == ["-p", "--model", "haiku", "--output-format", "text"]
    assert kw["env"]["LEARNED_EXTRACTOR"] == "1"
    assert kw["encoding"] == "utf-8" and kw["timeout"] == 7
    assert kw["input"] == "the prompt" and kw["capture_output"] is True
    assert kw["cwd"] and "LEARNED_EXTRACTOR" not in __import__("os").environ


def test_claude_p_nonzero_exit_raises(monkeypatch):
    monkeypatch.setattr(gist.subprocess, "run", lambda a, **kw: subprocess.CompletedProcess(
        a, 2, stdout="", stderr="boom"))
    with pytest.raises(RuntimeError, match="boom"):
        gist.claude_p("x")
