import os
from types import SimpleNamespace

import numpy as np
import pytest

from learned.judge import JevJudge
from learned.models import PromotionVerdict, TagVerdict, WindowVerdict
from learned.testing import FakeEmbedder, FakeJudge


class StubClient:
    def __init__(self, nouls=None, choices=None):
        self.calls = []
        self._nouls = nouls or {}
        self._choices = choices or {}

    def system_one(self, state, questions, model=None, **kw):
        self.calls.append(SimpleNamespace(state=state, questions=questions, model=model))
        return SimpleNamespace(
            nouls={k: SimpleNamespace(noul=v) for k, v in self._nouls.items()},
            choices={k: SimpleNamespace(choice=c, probabilities=p)
                     for k, (c, p) in self._choices.items()},
        )


def test_choose_tag_builds_choice_with_none_and_descriptions():
    stub = StubClient(choices={"tag": ("win", {"win": 0.8, "none": 0.1, "ff": 0.1})})
    v = JevJudge(client=stub).choose_tag("use backslashes", {"win": "Windows quirks", "ff": "ffmpeg"})
    q = stub.calls[0].questions["tag"]
    assert q.type == "choice"
    assert set(q.criteria) == {"win", "ff", "none"}
    assert "Windows quirks" in q.criteria["win"]
    assert "use backslashes" in str(stub.calls[0].state)
    assert stub.calls[0].model == "jev-latest"
    assert v == TagVerdict("win", {"win": 0.8, "none": 0.1, "ff": 0.1})


def test_choose_tag_none_maps_to_none():
    stub = StubClient(choices={"tag": ("none", {"none": 0.9, "win": 0.1})})
    assert JevJudge(client=stub).choose_tag("x", {"win": "W"}).tag is None


def test_promotion_sends_three_separate_noul_questions():
    stub = StubClient(nouls={"personal": 0.9, "same": 0.8, "stable": 0.7})
    v = JevJudge(client=stub).promotion(["a", "b"])
    qs = stub.calls[0].questions
    assert len(stub.calls) == 1 and len(qs) == 3
    assert all(q.type == "noul" for q in qs.values())
    assert len({q.instructions for q in qs.values()}) == 3
    assert "a" in str(stub.calls[0].state) and "b" in str(stub.calls[0].state)
    assert v == PromotionVerdict(0.9, 0.8, 0.7)


def test_classify_window_sends_choice_and_sensitive_noul():
    stub = StubClient(nouls={"sensitive": 0.05},
                      choices={"kind": ("rejection", {"rejection": 0.9, "none": 0.1})})
    v = JevJudge(client=stub).classify_window("no, not X", "I suggest X")
    qs = stub.calls[0].questions
    assert qs["kind"].type == "choice" and qs["sensitive"].type == "noul"
    assert set(qs["kind"].criteria) == {"rejection", "decision", "preference", "correction", "none"}
    assert "I suggest X" in str(stub.calls[0].state) and "no, not X" in str(stub.calls[0].state)
    assert v == WindowVerdict("rejection", 0.9, 0.05)


def test_kind_prob_read_from_probabilities():
    stub = StubClient(nouls={"sensitive": 0.0},
                      choices={"kind": ("preference", {"preference": 0.42, "none": 0.58})})
    assert JevJudge(client=stub).classify_window("t", "").kind_prob == 0.42


def test_same_fact_contradicts_fits_tag_return_noul():
    stub = StubClient(nouls={"same": 0.7, "contradicts": 0.2, "fits": 0.6})
    j = JevJudge(client=stub)
    assert j.same_fact("a", "b") == 0.7
    assert j.contradicts("a", "b") == 0.2
    assert j.fits_tag("a", "t", "desc") == 0.6
    for c in stub.calls:
        assert len(c.questions) == 1


def test_fake_judge_defaults_and_overrides():
    j = FakeJudge(same_fact=lambda a, b: 0.95)
    assert j.same_fact("a", "b") == 0.95
    assert j.contradicts("a", "b") == 0.0
    assert j.classify_window("t", "c") == WindowVerdict("none", 0.0, 0.0)
    assert j.choose_tag("t", {}).tag is None
    assert j.promotion(["a"]) == PromotionVerdict(0.0, 0.0, 0.0)
    assert j.fits_tag("t", "x", "d") == 0.0
    with pytest.raises(TypeError):
        FakeJudge(bogus=lambda: 1)


def test_fake_embedder_normalizes_rows():
    e = FakeEmbedder({"a": [3.0, 4.0], "b": [0.0, 2.0]})
    m = e.encode(["a", "b"])
    assert isinstance(m, np.ndarray) and m.shape == (2, 2)
    assert np.allclose(np.linalg.norm(m, axis=1), 1.0)
    assert np.allclose(m[0], [0.6, 0.8])


live = pytest.mark.skipif(not os.environ.get("TYPESAFE_API_KEY"), reason="no TYPESAFE_API_KEY")


@pytest.mark.live
@live
def test_live_noul_in_unit_interval():
    p = JevJudge().same_fact("Never use em dashes in prose.", "Do not write em dashes.")
    assert 0.0 <= p <= 1.0
    assert p > 0.5


@pytest.mark.live
@live
def test_live_choice_returns_listed_option():
    v = JevJudge().choose_tag("ffmpeg is not on PATH on this Windows box",
                              {"windows": "Windows OS quirks", "cooking": "Recipes"})
    assert v.tag in (None, "windows", "cooking")
    assert v.probabilities
    assert abs(sum(v.probabilities.values()) - 1.0) < 0.05
