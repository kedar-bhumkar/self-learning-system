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
    assert ".lock" in ign and "inbox/.done" in ign and "capture.log" in ign


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
    assert s.commit("first") is True  # .gitignore + tags.json are new
    assert s.commit("again") is False
    s.save(mk())
    assert s.commit("add") is True
    log = subprocess.run(["git", "-C", str(tmp_path), "log", "--oneline"],
                         capture_output=True, text=True).stdout
    assert "add" in log


def test_project_name_is_always_a_safe_single_component(monkeypatch, tmp_path):
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "h"))
    monkeypatch.setenv("HOME", str(tmp_path / "h"))
    assert project_name("D:\\") == "root"
    assert project_name("D:") == "root"
    assert project_name("/") == "home"  # empty after normalisation
    assert project_name(r"C:\a\b:c") == "b_c"
    assert project_name(r"C:\x\con") == "con_"
    assert project_name(r"C:\x\NUL.txt") == "nul.txt_"
    assert project_name(r"C:\x\lpt3") == "lpt3_"
    assert project_name(r"C:\x\..") == "root"
    assert project_name(r"C:\x\a?b*c") == "a_b_c"
    assert project_name(str(tmp_path / "h")) == "home"
    for bad in ("D:\\", r"C:\a\b:c", "con", "../..", "a/../..", r"x\..\.."):
        n = project_name(bad)
        assert n and n not in (".", "..") and not set(n) & set('/\\:*?"<>|')


import pytest


@pytest.mark.parametrize("fname,call", [
    ("tags.json", lambda s: s.add_tag("a", "b")),
    ("tombstones.json", lambda s: s.add_tombstone("x")),
    ("config.json", lambda s: s.set_config(a=1)),
])
def test_mutators_raise_on_corrupt_json_and_leave_file_untouched(tmp_path, fname, call):
    s = Store(tmp_path)
    s.init()
    f = tmp_path / fname
    f.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        call(s)
    assert f.read_text(encoding="utf-8") == "{not json"


def test_plain_reads_return_defaults_on_corrupt_json(tmp_path):
    s = Store(tmp_path)
    s.init()
    for n in ("tags.json", "tombstones.json", "config.json"):
        (tmp_path / n).write_text("{bad", encoding="utf-8")
    assert s.tags() == {} and s.tombstones() == [] and s.config() == {}


def test_writes_leave_no_temp_files(tmp_path):
    s = Store(tmp_path)
    s.init()
    s.add_tag("a", "b")
    s.set_config(x=1)
    s.save(mk())
    assert not list(tmp_path.rglob("*.tmp"))


def test_init_appends_missing_ignore_lines_to_existing_gitignore(tmp_path):
    (tmp_path / ".gitignore").write_text("custom\n", encoding="utf-8")
    s = Store(tmp_path)
    s.init()
    lines = (tmp_path / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "custom" in lines and "capture.log" in lines and ".lock" in lines


def test_parse_record_missing_fields_is_valueerror_and_skipped(tmp_path):
    with pytest.raises(ValueError):
        parse_record('---\nid: "x"\nkind: "rejection"\n---\nbody\n')
    s = Store(tmp_path)
    s.init()
    (tmp_path / "global" / "x.md").write_text('---\nid: "x"\n---\nbody\n', encoding="utf-8")
    assert s.get("x") is None
    assert s.load_all() == []


def test_commit_raises_when_git_fails(tmp_path):
    s = Store(tmp_path / "nogit")  # directory never initialised: no repo here
    (tmp_path / "nogit").mkdir()
    # a parent directory that is not a repo either, so git add fails
    with pytest.raises(RuntimeError):
        s.commit("x")
