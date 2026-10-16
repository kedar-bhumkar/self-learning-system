import pytest

from learned.config import Thresholds
from learned.ingest import can_supersede, ingest
from learned.models import Candidate, WindowVerdict
from learned.records import Record, new_id
from learned.store import Store
from learned.testing import FakeEmbedder, FakeJudge

NOW = "2026-10-16T03:00:00Z"
TH = Thresholds()


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "store")
    s.init()
    return s


def cand(text, project="p", kind="rejection", prob=0.9, session="s1"):
    return Candidate(text, "ctx", kind, prob, project, "C:/x/" + project, session,
                     "2026-10-15T10:00:00Z")


def put(store, name, cands):
    p = store.home / "inbox" / name
    p.write_text("".join(c.to_json() + "\n" for c in cands), encoding="utf-8")
    return p


def rec(body, project="p", confidence="stated", pinned=False, status="active",
        scope="project", sources=("old",)):
    return Record(new_id(body, project, scope), "rejection", scope, project, [], list(sources),
                  confidence, status, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", body,
                  pinned=pinned)


def emb(**kw):
    return FakeEmbedder({k.replace("_", " "): v for k, v in kw.items()})


class Counting(FakeJudge):
    def __init__(self, **ov):
        self.calls = []
        super().__init__(**{k: self._wrap(k, f) for k, f in ov.items()})

    def _wrap(self, name, fn):
        def inner(*a):
            self.calls.append(name)
            return fn(*a)
        return inner


def test_new_candidate_becomes_project_record_stated(store):
    put(store, "s1.jsonl", [cand("no em dashes")])
    rep = ingest(store, FakeJudge(), FakeEmbedder({"no em dashes": [1, 0]}), TH, NOW)
    recs = store.load_all()
    assert len(recs) == 1
    r = recs[0]
    assert (r.scope, r.project, r.confidence, r.status, r.kind) == \
        ("project", "p", "stated", "active", "rejection")
    assert r.sources == ["s1"] and r.created == NOW and r.last_seen == NOW
    assert r.id == new_id("no em dashes", "p", "project")
    assert rep.created == [r.id]
    assert not list((store.home / "inbox").glob("*.jsonl"))
    assert (store.home / "inbox" / "processed" / "s1.jsonl").exists()


def test_unclassified_candidate_is_classified_at_ingest(store):
    put(store, "s1.jsonl", [cand("use sqlite", kind=None, prob=0.0)])
    j = FakeJudge(classify_window=lambda t, c: WindowVerdict("decision", 0.8, 0.0))
    ingest(store, j, FakeEmbedder({"use sqlite": [1, 0]}), TH, NOW)
    (r,) = store.load_all()
    assert r.kind == "decision" and r.confidence == "stated"


def test_none_kind_after_classification_is_dropped(store):
    put(store, "s1.jsonl", [cand("hello", kind=None, prob=0.0),
                            cand("sensitive", kind=None, prob=0.0)])

    def classify(t, c):
        return WindowVerdict("none", 0.9, 0.0) if t == "hello" else WindowVerdict("rejection", 0.9, 0.9)

    rep = ingest(store, FakeJudge(classify_window=classify), emb(hello=[1, 0], sensitive=[0, 1]),
                 TH, NOW)
    assert store.load_all() == [] and rep.skipped == 2 and rep.created == []


def test_same_fact_merges_sources_and_bumps_last_seen(store):
    old = rec("never use em dashes")
    store.save(old)
    put(store, "s9.jsonl", [cand("do not write em dashes", session="s9")])
    j = FakeJudge(same_fact=lambda a, b: 0.95)
    e = emb(never_use_em_dashes=[1, 0], do_not_write_em_dashes=[0.99, 0.1])
    rep = ingest(store, j, e, TH, NOW)
    (r,) = store.load_all()
    assert r.id == old.id and r.sources == ["old", "s9"] and r.last_seen == NOW
    assert r.body == "never use em dashes"
    assert rep.merged == [(new_id("do not write em dashes", "p", "project"), old.id)]
    assert rep.created == []


def test_contradiction_supersedes_older_stated_loses_to_newer_stated(store):
    old = rec("use tabs")
    store.save(old)
    put(store, "s2.jsonl", [cand("use spaces", session="s2")])
    j = FakeJudge(contradicts=lambda a, b: 0.9)
    rep = ingest(store, j, emb(use_tabs=[1, 0], use_spaces=[0.9, 0.3]), TH, NOW)
    assert store.get(old.id).status == "superseded"
    assert [r.body for r in store.load_all()] == ["use spaces"]
    assert rep.superseded == [old.id]


def test_inferred_never_supersedes_stated():
    stated, inferred = rec("use tabs"), rec("use spaces", confidence="inferred")
    assert can_supersede(stated, inferred) is True       # stated beats inferred
    assert can_supersede(stated, rec("x", confidence="stated")) is True  # newer stated wins
    assert can_supersede(inferred, stated) is False      # inferred never supersedes stated
    assert can_supersede(inferred, rec("y", confidence="inferred")) is True
    assert can_supersede(stated, rec("z", pinned=True)) is False


def test_pinned_record_not_superseded_but_gains_sources(store):
    pin = rec("use tabs", pinned=True)
    store.save(pin)
    put(store, "s2.jsonl", [cand("use spaces", session="s2"),
                            cand("tabs please", session="s3")])
    j = FakeJudge(contradicts=lambda a, b: 0.95 if "spaces" in a else 0.0,
                  same_fact=lambda a, b: 0.9 if a == "tabs please" else 0.0)
    ingest(store, j, emb(use_tabs=[1, 0], use_spaces=[0.9, 0.3], tabs_please=[0.95, 0.1]),
           TH, NOW)
    r = store.get(pin.id)
    assert r.status == "active" and r.pinned
    assert r.sources == ["old", "s3"]
    assert r.last_seen == "2026-01-01T00:00:00Z"  # merge into pinned only adds sources
    assert [x.body for x in store.load_all()] == ["use tabs"]


def test_neighbors_limited_to_k_and_cutoff(store):
    for i in range(5):
        store.save(rec(f"fact {i}", project="p"))
    store.save(rec("far away", project="p"))
    put(store, "s1.jsonl", [cand("new fact")])
    vecs = {f"fact {i}": [1, 0.01 * i] for i in range(5)}
    vecs["far away"] = [0, 1]
    vecs["new fact"] = [1, 0]
    j = Counting(same_fact=lambda a, b: 0.0, contradicts=lambda a, b: 0.0)
    ingest(store, j, FakeEmbedder(vecs), Thresholds(neighbor_k=3), NOW)
    assert j.calls.count("same_fact") == 3 and j.calls.count("contradicts") == 3


def test_neighbors_are_same_project_then_promoted_never_other_projects(store):
    store.save(rec("other fact", project="q", scope="topic"))
    store.save(rec("other project fact", project="q"))
    store.save(rec("mine", project="p"))
    put(store, "s1.jsonl", [cand("new")])
    seen = []
    j = FakeJudge(same_fact=lambda a, b: seen.append(b) or 0.0)
    ingest(store, j, FakeEmbedder({"other fact": [1, 0], "mine": [0.8, 0.6], "new": [1, 0],
                       "other project fact": [1, 0]}),
           TH, NOW)
    assert seen == ["mine", "other fact"]


def test_judge_exception_keeps_inbox_file_and_flags_failure(store):
    f = put(store, "s1.jsonl", [cand("a fact")])

    def boom(*a):
        raise RuntimeError("api down")

    store.save(rec("an old fact"))
    rep = ingest(store, FakeJudge(same_fact=boom), emb(a_fact=[1, 0], an_old_fact=[1, 0]),
                 TH, NOW)
    assert rep.jev_failed is True
    assert f.exists() and not (store.home / "inbox" / "processed").exists()
    assert [r.body for r in store.load_all()] == ["an old fact"]


def test_tombstoned_text_is_skipped(store):
    store.add_tombstone("forgotten thing")
    put(store, "s1.jsonl", [cand("forgotten thing again")])
    judge = Counting(classify_window=lambda t, c: WindowVerdict("rejection", 0.9, 0.0),
                     same_fact=lambda a, b: 0.0, contradicts=lambda a, b: 0.0)
    rep = ingest(store, judge,
                 emb(forgotten_thing=[1, 0], forgotten_thing_again=[0.99, 0.05]), TH, NOW)
    assert store.load_all() == [] and rep.skipped == 1
    assert (store.home / "inbox" / "processed" / "s1.jsonl").exists()
    assert judge.calls == []


def test_rerun_after_partial_failure_does_not_duplicate(store):
    put(store, "s1.jsonl", [cand("first"), cand("second")])

    def flaky(a, b):
        if a == "second":
            raise RuntimeError("down")
        return 0.0

    e = FakeEmbedder({"first": [1, 0], "second": [0.9, 0.1]})
    store.save(rec("seed"))
    e.vectors["seed"] = [1, 0.1]
    rep = ingest(store, FakeJudge(same_fact=flaky), e, TH, NOW)
    assert rep.jev_failed
    rep2 = ingest(store, FakeJudge(), e, TH, NOW)
    assert not rep2.jev_failed
    assert sorted(r.body for r in store.load_all()) == ["first", "second", "seed"]


def test_same_fact_in_two_projects_stays_two_records(store):
    put(store, "a.jsonl", [cand("always use uv", project="alpha", session="sa")])
    put(store, "b.jsonl", [cand("prefer uv over pip", project="beta", session="sb")])
    j = FakeJudge(same_fact=lambda a, b: 0.95)
    rep = ingest(store, j, FakeEmbedder({"always use uv": [1, 0], "prefer uv over pip": [0.95, 0.1]}),
                 TH, NOW)
    assert sorted(r.project for r in store.load_all()) == ["alpha", "beta"]
    assert rep.merged == [] and len(rep.created) == 2


def test_promoted_scope_records_are_neighbors_for_any_project(store):
    g = rec("always use uv", project="alpha", scope="global")
    store.save(g)
    put(store, "b.jsonl", [cand("prefer uv over pip", project="beta", session="sb")])
    j = FakeJudge(same_fact=lambda a, b: 0.95)
    rep = ingest(store, j, FakeEmbedder({"always use uv": [1, 0], "prefer uv over pip": [0.95, 0.1]}),
                 TH, NOW)
    assert rep.merged and rep.merged[0][1] == g.id and rep.created == []
    assert store.get(g.id).sources == ["old", "sb"]


GOOD_FIELDS = '"context": "", "kind": null, "kind_prob": 0, "project": "p", "cwd": "", ' \
              '"session_id": "s", "timestamp": ""'


@pytest.mark.parametrize("bad", ["[1, 2]", '{"text": "x"}', "not json", "5",
                                 '{"text": 3, ' + GOOD_FIELDS + "}"])
def test_poison_line_moves_file_to_rejected_and_ingest_continues(store, bad):
    good = cand("fine fact")
    p = store.home / "inbox" / "a.jsonl"
    p.write_text(good.to_json() + "\n" + bad + "\n", encoding="utf-8")
    put(store, "b.jsonl", [cand("other fact", session="s2")])
    rep = ingest(store, FakeJudge(), FakeEmbedder({"fine fact": [1, 0], "other fact": [0, 1]}),
                 TH, NOW)
    assert (store.home / "inbox" / "rejected" / "a.jsonl").exists() and not p.exists()
    assert [r.body for r in store.load_all()] == ["other fact"] and rep.created
    assert "a.jsonl" in (store.home / "consolidate.log").read_text(encoding="utf-8")




def _global(body):
    return rec(body, project="", scope="global")


def test_project_statement_never_supersedes_promoted_record(store):
    g = _global("use tabs")
    store.save(g)
    put(store, "s2.jsonl", [cand("use spaces", session="s2")])
    j = Counting(contradicts=lambda a, b: 0.95)
    rep = ingest(store, j, emb(use_tabs=[1, 0], use_spaces=[0.9, 0.3]), TH, NOW)
    assert store.get(g.id).status == "active" and rep.superseded == []
    assert "contradicts" not in j.calls
    (new,) = [r for r in store.load_all() if r.scope == "project"]
    assert new.body == "use spaces" and new.project == "p" and rep.created == [new.id]


def test_same_fact_still_merges_into_promoted_record(store):
    g = _global("use tabs")
    store.save(g)
    put(store, "s2.jsonl", [cand("tabs please", session="s2")])
    j = FakeJudge(same_fact=lambda a, b: 0.95)
    rep = ingest(store, j, emb(use_tabs=[1, 0], tabs_please=[0.95, 0.1]), TH, NOW)
    (r,) = store.load_all()
    assert r.id == g.id and r.sources == ["old", "s2"] and rep.created == []


def test_same_project_contradiction_still_supersedes_with_promoted_neighbour(store):
    g, old = _global("use tabs"), rec("use hard tabs")
    store.save(g)
    store.save(old)
    put(store, "s2.jsonl", [cand("use spaces", session="s2")])
    j = FakeJudge(contradicts=lambda a, b: 0.95)
    rep = ingest(store, j, emb(use_tabs=[1, 0], use_hard_tabs=[0.99, 0.1],
                               use_spaces=[0.9, 0.3]), TH, NOW)
    assert rep.superseded == [old.id]
    assert store.get(g.id).status == "active"
