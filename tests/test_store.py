import subprocess
from pathlib import Path

from learned.config import Thresholds, learned_home, project_name
from learned.records import Record, new_id, parse_record, render_record
from learned.store import Store


def mk(body="Rejected X because Y", **kw):
    base = dict(
        id=new_id(body, kw.get("project", "p"), kw.get("scope", "project")),
        kind="rejection", scope="project", project="p", tags=["t"],
        sources=["s1"], confidence="stated", status="active",
        created="2026-01-01", last_seen="2026-01-02", body=body,
    )
    base.update(kw)
    return Record(**base)


def test_render_parse_roundtrip_unicode():
    r = mk("don't use café ☕")
    r2 = parse_record(render_record(r))
    assert r2 == r
    assert r2.body == "don't use café ☕"


def test_new_id_depends_on_project_and_scope():
    a = new_id("x", "p1", "project")
    assert len(a) == 10
    assert a != new_id("x", "p2", "project")
    assert a != new_id("x", "p1", "global")
    assert new_id("x") == new_id("x", "", "project")


def test_init_creates_layout_and_git_repo(tmp_path):
    s = Store(tmp_path / "home")
    s.init()
    h = tmp_path / "home"
    for d in ("inbox", "global", "topics", "projects"):
        assert (h / d).is_dir()
    assert (h / "tags.json").exists()
    assert (h / ".git").exists()
    ign = (h / ".gitignore").read_text(encoding="utf-8")
    assert ".lock" in ign and "inbox/.done" in ign


def test_init_idempotent(tmp_path):
    s = Store(tmp_path / "home")
    s.init()
    s.add_tag("a", "desc")
    s.init()
    assert s.tags() == {"a": "desc"}


def test_save_paths_by_scope(tmp_path):
    s = Store(tmp_path)
    s.init()
    p = s.save(mk())
    assert p == tmp_path / "projects" / "p" / f"{mk().id}.md"
    g = mk("g body", scope="global")
    assert s.save(g).parent == tmp_path / "global"
    t = mk("t body", scope="topic")
    assert s.save(t).parent == tmp_path / "topics"
    assert s.get(g.id) == g
    assert s.get("nope") is None
    s.delete(g.id)
    assert s.get(g.id) is None


def test_load_all_filters_status_and_scope(tmp_path):
    s = Store(tmp_path)
    s.init()
    a = mk("a")
    b = mk("b", status="superseded")
    c = mk("c", scope="global")
    d = mk("d", project="q")
    for r in (a, b, c, d):
        s.save(r)
    (tmp_path / "projects" / "p" / "gist.md").write_text("## Purpose\nx", encoding="utf-8")
    (tmp_path / "projects" / "p" / "meta.json").write_text("{}", encoding="utf-8")
    (tmp_path / "projects" / "p" / "bad.md").write_text("garbage", encoding="utf-8")
    ids = lambda rs: {r.id for r in rs}
    assert ids(s.load_all()) == {a.id, c.id, d.id}
    assert ids(s.load_all(scope="global")) == {c.id}
    assert ids(s.load_all(project="p")) == {a.id, c.id}
    assert ids(s.load_all(status="superseded")) == {b.id}
    assert ids(s.load_all(status=None)) == {a.id, b.id, c.id, d.id}


def test_tags_and_tombstones_and_config(tmp_path):
    s = Store(tmp_path)
    s.init()
    s.add_tag("win", "Windows")
    assert s.tags() == {"win": "Windows"}
    s.add_tombstone("forget café")
    s.add_tombstone("second")
    assert s.tombstones() == ["forget café", "second"]
    s.set_config(a=1)
    s.set_config(b="x")
    assert s.config() == {"a": 1, "b": "x"}


def test_project_name_home_and_basename(monkeypatch, tmp_path):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    assert project_name(str(tmp_path)) == "home"
    assert project_name(str(tmp_path / "My Proj")) == "my proj"
    assert project_name(r"C:\Work\Foo" + "\\") == "foo"


def test_learned_home_env(monkeypatch, tmp_path):
    monkeypatch.setenv("LEARNED_HOME", str(tmp_path / "x"))
    assert learned_home() == tmp_path / "x"
    monkeypatch.delenv("LEARNED_HOME")
    assert learned_home() == Path.home() / ".claude" / "learned"


def test_thresholds_defaults():
    t = Thresholds()
    assert t.core_max_chars == 6000 and t.min_user_turns == 3


def test_commit_returns_false_when_clean(tmp_path):
    s = Store(tmp_path)
    s.init()
    assert s.commit("first") in (True, False)
    assert s.commit("again") is False
    s.save(mk())
    assert s.commit("add") is True
    log = subprocess.run(["git", "-C", str(tmp_path), "log", "--oneline"],
                         capture_output=True, text=True).stdout
    assert "add" in log
