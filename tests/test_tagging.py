import json

import pytest

from learned.config import Thresholds
from learned.models import TagVerdict
from learned.records import Record, new_id
from learned.store import Store
from learned.tagging import assign_tag
from learned.testing import FakeJudge

TH = Thresholds()


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "store")
    s.init()
    return s


def record(body="always use ffmpeg with -y for video"):
    return Record(new_id(body), "decision", "project", "p", [], ["s"], "stated", "active",
                  "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", body)


class Runner:
    def __init__(self, reply="name: hyperframes\ndescription: Video pipeline work"):
        self.reply, self.prompts = reply, []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def judge_with(tag, probs, calls=None):
    def choose(text, tags):
        if calls is not None:
            calls.append(tags)
        return TagVerdict(tag, probs)
    return FakeJudge(choose_tag=choose)


def test_confident_existing_tag_reused(store):
    store.add_tag("ffmpeg", "ffmpeg usage")
    runner = Runner()
    r = assign_tag(store, judge_with("ffmpeg", {"ffmpeg": 0.8, "none": 0.1}), runner,
                   record(), TH)
    assert r.tags == ["ffmpeg"] and runner.prompts == []


def test_none_with_weak_runner_up_creates_tag_via_runner(store):
    store.add_tag("ffmpeg", "ffmpeg usage")
    runner = Runner()
    r = assign_tag(store, judge_with(None, {"ffmpeg": 0.1, "none": 0.85}), runner, record(), TH)
    assert r.tags == ["hyperframes"]
    assert json.loads((store.home / "tags.json").read_text(encoding="utf-8"))["hyperframes"] \
        == "Video pipeline work"
    assert "use ffmpeg with -y" in runner.prompts[0]


def test_none_with_strong_runner_up_creates_no_tag(store):
    store.add_tag("ffmpeg", "ffmpeg usage")
    runner = Runner()
    r = assign_tag(store, judge_with(None, {"ffmpeg": 0.3, "none": 0.7}), runner, record(), TH)
    assert r.tags == [] and runner.prompts == [] and list(store.tags()) == ["ffmpeg"]


def test_empty_vocabulary_skips_judge_and_creates_tag(store):
    calls = []
    r = assign_tag(store, judge_with(None, {}, calls), Runner(), record(), TH)
    assert calls == [] and r.tags == ["hyperframes"]


def test_low_confidence_leaves_untagged(store):
    store.add_tag("ffmpeg", "ffmpeg usage")
    runner = Runner()
    r = assign_tag(store, judge_with("ffmpeg", {"ffmpeg": 0.4, "none": 0.4}), runner, record(), TH)
    assert r.tags == [] and runner.prompts == []


def test_new_tag_name_is_slugified_and_unique(store):
    store.add_tag("video-pipeline", "x")
    runner = Runner("name: Video Pipeline!\ndescription: Videos")
    r = assign_tag(store, judge_with(None, {"video-pipeline": 0.0, "none": 0.9}), runner,
                   record(), TH)
    assert r.tags == ["video-pipeline-2"]
    assert store.tags()["video-pipeline-2"] == "Videos"


def test_tag_named_none_never_created(store):
    runner = Runner("name: None\ndescription: nothing")
    r = assign_tag(store, judge_with(None, {}), runner, record(), TH)
    # "none" is a confirmation-vocabulary word, so it is now rejected as a junk name
    assert r.tags == [] and "none" not in store.tags()


def test_runner_failure_leaves_untagged(store):
    r = assign_tag(store, judge_with(None, {}), Runner(RuntimeError("down")), record(), TH)
    assert r.tags == [] and store.tags() == {}


def test_unparseable_runner_output_leaves_untagged(store):
    r = assign_tag(store, judge_with(None, {}), Runner("sorry, no"), record(), TH)
    assert r.tags == [] and store.tags() == {}


def test_vocabulary_over_254_skips_judge_and_logs(store):
    for i in range(255):
        store.add_tag(f"t{i}", "d")
    calls = []
    runner = Runner()
    r = assign_tag(store, judge_with("t1", {"t1": 0.9}, calls), runner, record(), TH)
    assert r.tags == [] and calls == [] and runner.prompts == []
    log = (store.home / "consolidate.log").read_text(encoding="utf-8")
    assert "255" in log and len(log.splitlines()) == 1


JUNK = ["digit-one-fact", "go-word-fact", "yes-word-fact", "apply-word-fact",
        "storage-location-approval", "two-fact", "ok-approach"]
LONG = "always render the hyperframes video with the pipeline script"


@pytest.mark.parametrize("name", JUNK)
def test_junk_tag_names_rejected(store, name):
    runner = Runner(f"name: {name}\ndescription: whatever")
    r = assign_tag(store, FakeJudge(), runner, record(LONG), TH)
    assert r.tags == [] and store.tags() == {}
    log = (store.home / "consolidate.log").read_text(encoding="utf-8")
    assert len(log.strip().splitlines()) == 1


def test_substantive_tag_still_created(store):
    runner = Runner("name: hyperframes video pipeline\ndescription: Video rendering")
    r = assign_tag(store, FakeJudge(), runner, record(LONG), TH)
    assert r.tags == ["hyperframes-video-pipeline"]


@pytest.mark.parametrize("body", ["Go", "use ffmpeg with -y"])
def test_runner_not_called_for_bare_or_short(store, body):
    runner = Runner()
    r = assign_tag(store, FakeJudge(), runner, record(body), TH)
    assert r.tags == [] and runner.prompts == []


def test_short_fact_reuses_existing_tag(store):
    store.add_tag("ffmpeg", "ffmpeg usage")
    runner = Runner()
    r = assign_tag(store, judge_with("ffmpeg", {"ffmpeg": 0.8, "none": 0.1}), runner,
                   record("use ffmpeg"), TH)
    assert r.tags == ["ffmpeg"] and runner.prompts == []
