import json

import pytest

from learned.config import Thresholds
from learned.models import PromotionVerdict
from learned.promotion import promote
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


def rec(body, project, last_seen="2026-10-01T00:00:00Z", sources=None, pinned=False,
        confidence="stated", scope="project"):
    r = Record(new_id(body, project, scope), "preference", scope, project, [],
               sources or [f"s-{project}"], confidence, "active", "2026-09-01T00:00:00Z",
               last_seen, body, pinned=pinned)
    return r


def pair(store, **kw):
    a = rec("always use uv", "alpha", **kw)
    b = rec("prefer uv over pip", "beta", last_seen="2026-10-10T00:00:00Z")
    store.save(a)
    store.save(b)
    return a, b


EMB = FakeEmbedder({"always use uv": [1, 0], "prefer uv over pip": [0.95, 0.1],
                    "unrelated": [0, 1]})


def verdict(personal=0.95, same=0.95, stable=0.95):
    return FakeJudge(promotion=lambda texts: PromotionVerdict(personal, same, stable))


def log_lines(store):
    p = store.home / "promotions.log"
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def test_single_project_cluster_never_promoted(store):
    a = rec("always use uv", "alpha")
    b = rec("prefer uv over pip", "alpha")
    store.save(a)
    store.save(b)
    ev = promote(store, verdict(), EMB, TH, [a], False, NOW)
    assert ev == [] and store.get(a.id).status == "active" and log_lines(store) == []


def test_two_projects_personal_high_promotes_global(store):
    a, b = pair(store)
    ev = promote(store, verdict(), EMB, TH, [a], False, NOW)
    assert len(ev) == 1 and ev[0].scope == "global" and ev[0].applied
    g = store.load_all(scope="global")
    assert len(g) == 1 and g[0].id == new_id(g[0].body, g[0].project, "global")
    assert (store.home / "global" / f"{g[0].id}.md").exists()
    for m in (a, b):
        r = store.get(m.id)
        assert r.status == "superseded" and r.promoted_to == g[0].id


def test_shared_not_personal_promotes_topic(store):
    a, b = pair(store)
    ev = promote(store, verdict(personal=0.5), EMB, TH, [a], False, NOW)
    assert ev[0].scope == "topic"
    (t,) = store.load_all(scope="topic")
    assert (store.home / "topics" / f"{t.id}.md").exists()


def test_below_threshold_leaves_everything(store):
    a, b = pair(store)
    ev = promote(store, verdict(stable=0.8), EMB, TH, [a], False, NOW)
    assert ev == [] and log_lines(store) == []
    assert store.get(a.id).status == "active" and store.get(b.id).status == "active"
    assert store.load_all(scope="global") == [] and store.load_all(scope="topic") == []


def test_shadow_mode_logs_but_does_not_apply(store):
    a, b = pair(store)
    ev = promote(store, verdict(), EMB, TH, [a], True, NOW)
    assert len(ev) == 1 and ev[0].applied is False and ev[0].scope == "global"
    (line,) = log_lines(store)
    assert line["applied"] is False
    assert store.get(a.id).status == "active" and store.get(a.id).promoted_to is None
    assert store.load_all(scope="global") == []


def test_log_line_has_three_probabilities(store):
    a, b = pair(store)
    promote(store, verdict(0.91, 0.92, 0.93), EMB, TH, [a], False, NOW)
    (line,) = log_lines(store)
    assert (line["personal"], line["same"], line["stable"]) == (0.91, 0.92, 0.93)
    assert sorted(line["ids"]) == sorted([a.id, b.id]) and line["scope"] == "global"
    assert line["ts"] == NOW


def test_pinned_members_excluded(store):
    a = rec("always use uv", "alpha")
    b = rec("prefer uv over pip", "beta", pinned=True)
    store.save(a)
    store.save(b)
    assert promote(store, verdict(), EMB, TH, [a], False, NOW) == []
    assert promote(store, verdict(), EMB, TH, [b], False, NOW) == []
    assert store.get(b.id).status == "active"


def test_dissimilar_records_not_clustered(store):
    a = rec("always use uv", "alpha")
    c = rec("unrelated", "beta")
    store.save(a)
    store.save(c)
    assert promote(store, verdict(), EMB, TH, [a], False, NOW) == []


def test_cached_cluster_not_judged_twice(store):
    a, b = pair(store)
    calls = []

    def prom(texts):
        calls.append(texts)
        return PromotionVerdict(0.5, 0.5, 0.5)

    j = FakeJudge(promotion=prom)
    promote(store, j, EMB, TH, [store.get(a.id)], False, NOW)
    promote(store, j, EMB, TH, [store.get(a.id)], False, NOW)
    promote(store, j, EMB, TH, [store.get(b.id)], False, NOW)
    assert len(calls) == 1
    key = "promotion:" + ",".join(sorted([a.id, b.id]))
    assert store.get(b.id).judgments[key]["same"] == 0.5


def test_promoted_record_keeps_origin_and_union_of_sources(store):
    a, b = pair(store, sources=["s1", "s2"])
    promote(store, verdict(), EMB, TH, [a], False, NOW)
    (g,) = store.load_all(scope="global")
    assert g.project == "beta"  # newest member's origin
    assert g.body == "prefer uv over pip"
    assert g.sources == ["s1", "s2", "s-beta"]
    assert g.status == "active" and g.created == "2026-09-01T00:00:00Z" and g.confidence == "stated"


def test_newest_stated_member_body_wins_over_newer_inferred(store):
    a = rec("always use uv", "alpha", last_seen="2026-10-01T00:00:00Z")
    b = rec("prefer uv over pip", "beta", last_seen="2026-10-10T00:00:00Z", confidence="inferred")
    store.save(a)
    store.save(b)
    promote(store, verdict(), EMB, TH, [a], False, NOW)
    (g,) = store.load_all(scope="global")
    assert g.body == "always use uv" and g.project == "alpha"


def test_shadow_then_live_applies_cached_verdict(store):
    a, b = pair(store)
    calls = []

    def prom(texts):
        calls.append(1)
        return PromotionVerdict(0.95, 0.95, 0.95)

    j = FakeJudge(promotion=prom)
    promote(store, j, EMB, TH, [a], True, NOW)
    ev = promote(store, j, EMB, TH, [store.get(a.id)], False, NOW)
    assert len(calls) == 1 and len(ev) == 1 and ev[0].applied
    assert len(log_lines(store)) == 2


def test_promoted_record_created_is_earliest_member_created(store):
    a, b = pair(store)
    a.created = "2026-03-01T00:00:00Z"
    store.save(a)
    promote(store, verdict(), EMB, TH, [a], False, NOW)
    (g,) = store.load_all(scope="global")
    assert g.created == "2026-03-01T00:00:00Z"


def test_unwritable_promotions_log_does_not_abort(store):
    a, b = pair(store)
    (store.home / "promotions.log").mkdir()  # appending to a directory raises OSError
    ev = promote(store, verdict(), EMB, TH, [a], False, NOW)
    assert len(ev) == 1 and store.load_all(scope="global")
    assert "promotions.log" in (store.home / "consolidate.log").read_text(encoding="utf-8")


def test_judge_failure_collects_failed_ids_and_keeps_earlier_events(store):
    a, b = pair(store)
    c = rec("lint with ruff", "alpha")
    d = rec("ruff for linting", "beta")
    store.save(c)
    store.save(d)
    emb = FakeEmbedder({"always use uv": [1, 0], "prefer uv over pip": [0.95, 0.1],
                        "lint with ruff": [0, 1], "ruff for linting": [0.1, 0.95]})

    def prom(texts):
        if "lint with ruff" in texts:
            raise RuntimeError("down")
        return PromotionVerdict(0.95, 0.95, 0.95)

    failed = []
    ev = promote(store, FakeJudge(promotion=prom), emb, TH, [a, c], False, NOW, failed)
    assert len(ev) == 1 and failed == [c.id]
    with pytest.raises(RuntimeError):
        promote(store, FakeJudge(promotion=prom), emb, TH, [c], False, NOW)


def test_promotion_into_existing_target_unions_tags(store):
    a, b = pair(store)
    a.tags, b.tags = ["uv", "python"], ["tooling"]
    store.save(a)
    store.save(b)
    ev = promote(store, verdict(), EMB, TH, [a], False, NOW)
    nid = ev[0].new_id
    g = store.get(nid)
    g.status, g.tags = "superseded", ["old-tag", "uv"]
    store.save(g)
    for r in (a, b):  # reactivate members so the same cluster promotes again
        m = store.get(r.id)
        m.status, m.promoted_to = "active", None
        store.save(m)
    ev = promote(store, verdict(), EMB, TH, [store.get(a.id)], False, NOW)
    g = store.get(ev[0].new_id)
    assert g.id == nid and g.status == "active"
    assert g.tags == ["old-tag", "uv", "python", "tooling"]
