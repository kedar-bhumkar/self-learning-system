import pytest

from learned import cli
from learned.config import Thresholds
from learned.consolidate import decay
from learned.ingest import ingest
from learned.models import Candidate
from learned.records import Record, new_id
from learned.store import Store
from learned.testing import FakeEmbedder, FakeJudge

NOW = "2026-10-16T03:00:00Z"


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "store")
    s.init()
    return s


def rec(body, scope="project", project="p", tags=(), pinned=False, last_seen="2026-01-01T00:00:00Z"):
    return Record(new_id(body, project, scope), "rejection", scope, project, list(tags),
                  ["s1"], "stated", "active", "2026-01-01T00:00:00Z", last_seen, body,
                  pinned=pinned)


def test_list_filters_and_output_columns(store, capsys):
    a = rec("no semicolons", "global", "")
    b = rec("use tabs", "project", "p", tags=["style"])
    c = rec("no jquery", "project", "q")
    for r in (a, b, c):
        store.save(r)
    assert cli.main(["list"], store=store) == 0
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 3
    row = next(l for l in out if b.id in l).split("\t")
    assert row[:4] == [b.id, "project", "p", "rejection"]
    assert "style" in row[4] and "use tabs" in row[5]
    cli.main(["list", "--scope", "global"], store=store)
    out = capsys.readouterr().out
    assert a.id in out and b.id not in out
    cli.main(["list", "--project", "q"], store=store)
    out = capsys.readouterr().out
    assert c.id in out and b.id not in out
    cli.main(["list", "--tag", "style"], store=store)
    out = capsys.readouterr().out
    assert b.id in out and c.id not in out


def test_show_prints_frontmatter_and_body(store, capsys):
    r = rec("no semicolons", "global", "")
    store.save(r)
    assert cli.main(["show", r.id], store=store) == 0
    out = capsys.readouterr().out
    assert out.startswith("---") and f'id: "{r.id}"' in out and "no semicolons" in out


def test_forget_deletes_and_tombstones_so_reingest_skips(store, capsys):
    r = rec("never use jquery")
    store.save(r)
    assert cli.main(["forget", r.id], store=store) == 0
    assert store.get(r.id) is None
    assert store.tombstones() == ["never use jquery"]
    p = store.home / "inbox" / "a.jsonl"
    p.write_text(Candidate("never use jquery", "c", "rejection", 0.9, "p", "C:/p", "s2",
                           "2026-10-15T10:00:00Z").to_json() + "\n", encoding="utf-8")
    rep = ingest(store, FakeJudge(), FakeEmbedder({"never use jquery": [1.0, 0.0]}),
                 Thresholds(), NOW)
    assert rep.created == [] and rep.skipped == 1


def test_forget_commit_failure_reports_but_succeeds(store, capsys, monkeypatch):
    r = rec("x fact")
    store.save(r)

    def boom(msg):
        raise RuntimeError("git broke")
    monkeypatch.setattr(store, "commit", boom)
    assert cli.main(["forget", r.id], store=store) == 0
    assert "git broke" in capsys.readouterr().err
    assert store.get(r.id) is None


def test_demote_moves_one_level_and_keeps_sources(store, capsys):
    r = rec("use tabs", "global", "", tags=["style"])
    store.save(r)
    assert cli.main(["demote", r.id], store=store) == 0
    t = store.get(r.id)
    assert t.scope == "topic" and t.sources == ["s1"] and t.last_seen > "2026-01-01T00:00:00Z"
    assert not (store.home / "global" / f"{r.id}.md").exists()
    assert (store.home / "topics" / f"{r.id}.md").exists()
    t2 = store.get(r.id)
    t2.project = "p"
    store.save(t2)
    assert cli.main(["demote", r.id], store=store) == 0
    q = store.get(r.id)
    assert q.scope == "project" and q.sources == ["s1"]
    assert (store.home / "projects" / "p" / f"{r.id}.md").exists()
    assert not (store.home / "topics" / f"{r.id}.md").exists()
    assert cli.main(["demote", r.id], store=store) != 0
    assert "project" in capsys.readouterr().err


def test_demote_to_project_without_project_is_error(store, capsys):
    r = rec("t fact", "topic", "")
    store.save(r)
    assert cli.main(["demote", r.id], store=store) != 0
    assert store.get(r.id).scope == "topic"


def test_pin_sets_flag_and_pinned_survives_decay(store):
    r = rec("old fact")
    store.save(r)
    assert cli.main(["pin", r.id], store=store) == 0
    assert store.get(r.id).pinned is True
    decay(store, Thresholds(), NOW)
    got = store.get(r.id)
    assert got.pinned and not got.stale and got.status == "active"


def test_unknown_id_returns_nonzero_without_traceback(store, capsys):
    for cmd in ("show", "forget", "demote", "pin"):
        assert cli.main([cmd, "nope"], store=store) != 0
    err = capsys.readouterr().err
    assert "Traceback" not in err and err.count("\n") == 4 and "nope" in err


def test_rerun_full_uses_factory_and_runs_full(store, capsys):
    a = rec("fact a")
    store.save(a)
    calls = []

    def factory():
        calls.append(1)
        return FakeJudge(), FakeEmbedder({}), lambda prompt: ""
    assert cli.main(["rerun", "--full"], store=store, factory=factory) == 0
    assert calls == [1]
    assert "full" in capsys.readouterr().out.lower()
    assert cli.main(["rerun"], store=store, factory=factory) != 0
