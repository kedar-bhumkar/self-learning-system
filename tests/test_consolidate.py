import json
import os
import subprocess
import time

import pytest

from learned import consolidate
from learned.config import Thresholds
from learned.consolidate import decay, recheck_new_tags, run
from learned.models import Candidate, PromotionVerdict, TagVerdict
from learned.records import Record, new_id
from learned.store import Store
from learned.testing import FakeEmbedder, FakeJudge

NOW = "2026-10-16T03:00:00Z"
TH = Thresholds()
GIST = "## Purpose\nP\n\n## Architecture\nA\n\n## Status\nS\n"


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "store"
    Store(h).init()
    return h


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("LEARNED_EXTRACTOR", raising=False)


def cand(text, project="alpha", session="s1"):
    return Candidate(text, "ctx", "preference", 0.9, project, "C:/x/" + project, session,
                     "2026-10-15T10:00:00Z")


def put(home, name, cands):
    p = home / "inbox" / name
    p.write_text("".join(c.to_json() + "\n" for c in cands), encoding="utf-8")
    return p


def rec(body, project="alpha", last_seen="2026-10-01T00:00:00Z", pinned=False, kind="preference",
        tags=None, scope="project"):
    return Record(new_id(body, project, scope), kind, scope, project, tags or [], ["old"],
                  "stated", "active", "2026-01-01T00:00:00Z", last_seen, body, pinned=pinned)


class Runner:
    def __init__(self, tag_reply="name: tooling\ndescription: Tooling choices"):
        self.prompts, self.tag_reply = [], tag_reply

    def __call__(self, prompt):
        self.prompts.append(prompt)
        return GIST if "project gist" in prompt else self.tag_reply


def emb(*texts):
    return FakeEmbedder({t: [1.0, 0.0] for t in texts})


def git_log(home):
    r = subprocess.run(["git", "-C", str(home), "log", "--format=%s"], capture_output=True,
                       text=True, encoding="utf-8")
    return r.stdout.split("\n")[:-1] if r.returncode == 0 else []


def test_full_pipeline_end_to_end_with_fakes(home):
    put(home, "s1.jsonl", [cand("always use uv")])
    runner = Runner()
    rep = run(home, FakeJudge(), emb("always use uv"), runner, TH, NOW)
    store = Store(home)
    (r,) = store.load_all()
    assert r.body == "always use uv" and r.tags == ["tooling"]
    assert store.tags() == {"tooling": "Tooling choices"}
    assert (home / "core.md").exists()
    assert (home / "projects" / "alpha" / "gist.md").read_text(encoding="utf-8") == GIST
    assert any("project gist" in p for p in runner.prompts)
    assert len(git_log(home)) == 1 and rep.committed
    assert (home / "inbox" / "processed" / "s1.jsonl").exists()
    assert not (home / ".lock").exists()


def test_lock_prevents_second_run_and_stale_lock_ignored(home):
    put(home, "s1.jsonl", [cand("always use uv")])
    lock = home / ".lock"
    lock.write_text("1", encoding="utf-8")
    rep = run(home, FakeJudge(), emb("always use uv"), Runner(), TH, NOW)
    assert rep.locked and (home / "inbox" / "s1.jsonl").exists() and lock.exists()
    old = time.time() - 7 * 3600
    os.utime(lock, (old, old))
    rep = run(home, FakeJudge(), emb("always use uv"), Runner(), TH, NOW)
    assert not rep.locked and not lock.exists()
    assert len(Store(home).load_all()) == 1


def test_lock_released_after_unexpected_error(home):
    put(home, "s1.jsonl", [cand("always use uv")])

    class Bad:
        def encode(self, texts):
            raise ValueError("model broken")

    rep = run(home, FakeJudge(), Bad(), Runner(), TH, NOW)
    assert not (home / ".lock").exists() and rep.errors
    assert (home / "inbox" / "s1.jsonl").exists()


def test_jev_failure_keeps_inbox_and_still_commits_other_work(home):
    store = Store(home)
    store.save(rec("seed fact"))
    store.save(rec("ancient fact", last_seen="2025-01-01T00:00:00Z"))
    f = put(home, "s1.jsonl", [cand("new fact")])

    def boom(a, b):
        raise RuntimeError("api down")

    rep = run(home, FakeJudge(same_fact=boom), emb("seed fact", "new fact", "ancient fact"),
              Runner(), TH, NOW)
    assert rep.jev_failed and f.exists()
    assert rep.decayed == 1 and store.get(new_id("ancient fact", "alpha")).stale
    assert (home / "core.md").exists() and git_log(home)


def test_commit_failure_is_reported_not_raised(home, monkeypatch):
    put(home, "s1.jsonl", [cand("always use uv")])

    def bad(self, message):
        raise RuntimeError("git exploded")

    monkeypatch.setattr(Store, "commit", bad)
    rep = run(home, FakeJudge(), emb("always use uv"), Runner(), TH, NOW)
    assert not rep.committed and any("git exploded" in e for e in rep.errors)
    assert len(Store(home).load_all()) == 1
    assert "git exploded" in (home / "consolidate.log").read_text(encoding="utf-8")


def test_missing_api_key_main_returns_zero_and_logs(home, monkeypatch):
    monkeypatch.setenv("LEARNED_HOME", str(home))
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    f = put(home, "s1.jsonl", [cand("always use uv")])
    assert consolidate.main([]) == 0
    assert f.exists()
    assert "TYPESAFE_API_KEY" in (home / "consolidate.log").read_text(encoding="utf-8")


def test_decay_marks_stale_not_deleted_and_skips_pinned(home):
    store = Store(home)
    old = "2026-01-01T00:00:00Z"
    a, b = rec("old one", last_seen=old), rec("old pinned", last_seen=old, pinned=True)
    g = rec("a gist", last_seen=old, kind="project-gist")
    fresh = rec("fresh", last_seen="2026-10-10T00:00:00Z")
    for r in (a, b, g, fresh):
        store.save(r)
    assert decay(store, TH, NOW) == 1
    assert store.get(a.id).stale and store.get(a.id).status == "active"
    assert not store.get(b.id).stale and not store.get(g.id).stale
    assert not store.get(fresh.id).stale
    assert decay(store, TH, NOW) == 0


def test_merge_clears_stale(home):
    store = Store(home)
    r = rec("always use uv", last_seen="2026-01-01T00:00:00Z")
    r.stale = True
    store.save(r)
    put(home, "s2.jsonl", [cand("always use uv", session="s2")])
    run(home, FakeJudge(same_fact=lambda a, b: 0.95), emb("always use uv"), Runner(), TH, NOW)
    assert store.get(r.id).stale is False


def test_weekly_sweep_only_when_due(home):
    store = Store(home)
    ancient = rec("ancient fact", last_seen="2025-01-01T00:00:00Z")
    store.save(ancient)
    store.set_config(last_sweep="2026-10-13T00:00:00Z")  # 3 days ago
    rep = run(home, FakeJudge(), FakeEmbedder({}), Runner(), TH, NOW)
    assert not rep.swept and not store.get(ancient.id).stale
    store.set_config(last_sweep="2026-10-08T00:00:00Z")  # 8 days ago
    rep = run(home, FakeJudge(), FakeEmbedder({}), Runner(), TH, NOW)
    assert rep.swept and store.get(ancient.id).stale
    assert store.config()["last_sweep"] == NOW


def test_first_run_sweeps_when_never_swept(home):
    store = Store(home)
    store.save(rec("ancient fact", last_seen="2025-01-01T00:00:00Z"))
    assert run(home, FakeJudge(), FakeEmbedder({}), Runner(), TH, NOW).swept


def test_recheck_new_tags_checks_only_new_tag(home):
    store = Store(home)
    store.add_tag("old-tag", "old")
    store.add_tag("new-tag", "Brand new tag")
    a, b = rec("fact a"), rec("fact b", tags=["new-tag"])
    store.save(a)
    store.save(b)
    seen = []

    def fits(text, tag, desc):
        seen.append((text, tag, desc))
        return 0.9

    n = recheck_new_tags(store, FakeJudge(fits_tag=fits), ["new-tag"], TH)
    assert n == 1 and seen == [("fact a", "new-tag", "Brand new tag")]
    assert store.get(a.id).tags == ["new-tag"] and store.get(b.id).tags == ["new-tag"]


def test_recheck_below_threshold_adds_nothing(home):
    store = Store(home)
    store.add_tag("new-tag", "d")
    a = rec("fact a")
    store.save(a)
    assert recheck_new_tags(store, FakeJudge(fits_tag=lambda t, g, d: 0.2), ["new-tag"], TH) == 0
    assert store.get(a.id).tags == []


def two_project_setup(home):
    store = Store(home)
    store.save(rec("prefer uv over pip", project="beta"))
    put(home, "s1.jsonl", [cand("always use uv", project="alpha")])
    judge = FakeJudge(promotion=lambda t: PromotionVerdict(0.95, 0.95, 0.95))
    e = FakeEmbedder({"always use uv": [1, 0], "prefer uv over pip": [0.95, 0.1]})
    return store, judge, e


def test_shadow_mode_until_date(home):
    store, judge, e = two_project_setup(home)
    store.set_config(shadow_until="2026-10-16")  # inclusive of today
    rep = run(home, judge, e, Runner(), TH, NOW)
    assert [ev.applied for ev in rep.promotions] == [False]
    assert store.load_all(scope="global") == []
    lines = (home / "promotions.log").read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0])["applied"] is False


def test_promotion_applied_after_shadow_period(home):
    store, judge, e = two_project_setup(home)
    store.set_config(shadow_until="2026-10-15")
    rep = run(home, judge, e, Runner(), TH, NOW)
    assert [ev.applied for ev in rep.promotions] == [True]
    assert len(store.load_all(scope="global")) == 1
    assert "(" in (home / "core.md").read_text(encoding="utf-8")


def test_first_run_starts_a_seven_day_shadow_period(home):
    store, judge, e = two_project_setup(home)
    run(home, judge, e, Runner(), TH, NOW)
    assert store.config()["shadow_until"] == "2026-10-23"
    assert store.load_all(scope="global") == []


def test_full_rerun_clears_cached_judgments(home):
    store = Store(home)
    store.set_config(shadow_until="2026-01-01")
    a = rec("always use uv", project="alpha", tags=["stale-tag"])
    a.judgments = {"promotion:x,y": {"personal": 0.1, "same": 0.1, "stable": 0.1}}
    pin = rec("pinned fact", pinned=True, tags=["keep"])
    pin.judgments = {"k": 1}
    store.save(a)
    store.save(pin)
    store.add_tag("stale-tag", "x")
    seen = []
    judge = FakeJudge(choose_tag=lambda t, tags: seen.append(t) or TagVerdict(
        "stale-tag", {"stale-tag": 0.9, "none": 0.05}))
    run(home, judge, emb("always use uv", "pinned fact"), Runner(), TH, NOW, full=True)
    r = store.get(a.id)
    assert r.judgments == {} and r.tags == ["stale-tag"] and seen == ["always use uv"]
    assert store.get(pin.id).judgments == {"k": 1} and store.get(pin.id).tags == ["keep"]


def test_capture_during_run_not_lost(home):
    store = Store(home)
    store.save(rec("seed fact"))
    put(home, "s1.jsonl", [cand("new fact")])

    def same(a, b):
        put(home, "s2.jsonl", [cand("late fact", session="s2")])
        return 0.0

    run(home, FakeJudge(same_fact=same), emb("seed fact", "new fact"), Runner(), TH, NOW)
    assert (home / "inbox" / "s2.jsonl").exists()
    assert (home / "inbox" / "processed" / "s1.jsonl").exists()
    assert "late fact" not in [r.body for r in store.load_all()]
