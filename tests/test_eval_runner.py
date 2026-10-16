import hashlib
import json
from pathlib import Path

import pytest

from eval import run_eval
from eval.run_eval import (capture_metrics, dedupe_metrics, main, promotion_sweep,
                           suggest_promote_global, tag_metrics)
from learned.models import PromotionVerdict, TagVerdict, WindowVerdict
from learned.testing import FakeJudge

ROOT = Path(__file__).resolve().parents[1]


def write_labels(path, rows):
    path.write_text("# comment\n" + "".join(json.dumps(r) + "\n" for r in rows),
                    encoding="utf-8")
    return path


def test_metrics_computation_on_tiny_labeled_set():
    # (expected, predicted): tp rejection, fp decision(expected none), fn preference(pred none),
    # wrong kind (expected decision, predicted rejection), tn
    pairs = [("rejection", "rejection"), ("none", "decision"), ("preference", "none"),
             ("decision", "rejection"), ("none", "none")]
    m = capture_metrics(pairs)
    # overall: TP=1; FP = 2 (decision-vs-none, rejection-vs-decision); FN = 2
    assert m["overall"]["precision"] == pytest.approx(1 / 3)
    assert m["overall"]["recall"] == pytest.approx(1 / 3)
    assert m["overall"]["f1"] == pytest.approx(1 / 3)
    r = m["rejection"]  # tp 1, fp 1, fn 0
    assert (r["precision"], r["recall"], r["f1"]) == (0.5, 1.0, pytest.approx(2 / 3))
    assert m["preference"]["recall"] == 0.0
    assert m["decision"]["precision"] == 0.0


def test_dedupe_metrics():
    m = dedupe_metrics([(True, True), (True, False), (False, False), (False, True)])
    assert m["accuracy"] == 0.5 and m["precision"] == 0.5 and m["recall"] == 0.5


def test_tag_metrics():
    m = tag_metrics([("reuse", "a", "a"), ("reuse", "b", "a"), ("new", None, None),
                     ("none", None, "a")])
    assert m["reuse_rate"] == 0.5 and m["new_rate"] == 0.25 and m["untagged_rate"] == 0.25
    assert m["reuse_accuracy"] == 0.5


def test_sweep_thresholds_and_confusion():
    assert [t for t, _ in promotion_sweep([])] == [0.70, 0.75, 0.80, 0.85, 0.90, 0.95]
    # (personal, expected): a topic fact with personal 0.8 becomes a false-global at <=0.80
    rows = [(0.97, "global"), (0.8, "topic")]
    sweep = dict(promotion_sweep(rows))
    assert sweep[0.80]["global"]["topic"] == 1
    assert sweep[0.85]["global"]["topic"] == 0 and sweep[0.85]["global"]["global"] == 1
    assert sweep[0.95]["topic"]["global"] == 0


def test_suggest_lowest_threshold_with_zero_false_global():
    rows = [(0.97, "global"), (0.8, "topic"), (0.72, "none")]
    t, note = suggest_promote_global(rows)
    assert t == 0.85 and "0.85" in note


def test_suggest_falls_back_to_095_and_says_so():
    rows = [(0.99, "topic")]
    t, note = suggest_promote_global(rows)
    assert t == 0.95 and "fall" in note.lower()


def test_capture_mode_with_fake_judge_prints_banner(tmp_path, capsys):
    labels = write_labels(tmp_path / "l.jsonl", [
        {"mode": "capture", "text": "No uv, use pip", "context": "", "expected_kind": "rejection"},
        {"mode": "capture", "text": "run tests", "context": "", "expected_kind": "none"}])
    judge_patch = FakeJudge(classify_window=lambda t, c: WindowVerdict(
        "rejection" if "uv" in t else "none", 0.9, 0.0))
    rc = main(["--mode", "capture", "--labeled", str(labels)], judge=judge_patch)
    out = capsys.readouterr().out
    assert rc == 0 and "FAKE JUDGE — smoke run only" in out
    assert "suggested" not in out.lower()
    assert "precision" in out.lower()


def test_promote_mode_fake_hides_suggestion_but_real_shows(tmp_path, capsys):
    labels = write_labels(tmp_path / "l.jsonl", [
        {"mode": "promote", "texts": ["a", "b"], "expected": "global"}])
    j = FakeJudge(promotion=lambda t: PromotionVerdict(0.97, 0.95, 0.95))
    assert main(["--mode", "promote", "--labeled", str(labels)], judge=j) == 0
    assert "suggested promote_global" not in capsys.readouterr().out
    assert main(["--mode", "promote", "--labeled", str(labels), "--judge", "jev"],
                judge=j) == 0
    out = capsys.readouterr().out
    assert "FAKE JUDGE" not in out and "suggested promote_global = 0.70" in out
    assert "worse" in out


def test_tag_and_dedupe_modes_run(tmp_path, capsys):
    labels = write_labels(tmp_path / "l.jsonl", [
        {"mode": "tag", "text": "x", "tags": {"a": "A"}, "expected": "a"},
        {"mode": "dedupe", "a": "p", "b": "q", "same": True}])
    j = FakeJudge(choose_tag=lambda t, tags: TagVerdict("a", {"a": 0.9, "none": 0.0}),
                  same_fact=lambda a, b: 0.95)
    assert main(["--mode", "tag", "--labeled", str(labels)], judge=j) == 0
    assert "reuse" in capsys.readouterr().out.lower()
    assert main(["--mode", "dedupe", "--labeled", str(labels)], judge=j) == 0
    assert "accuracy" in capsys.readouterr().out.lower()


def test_bad_args_and_missing_file_exit_nonzero_without_traceback(tmp_path, capsys):
    assert main(["--mode", "bogus", "--labeled", "x"]) != 0
    assert main(["--mode", "capture"]) != 0
    assert main(["--mode", "capture", "--labeled", str(tmp_path / "nope.jsonl")]) != 0
    assert main(["--mode", "capture", "--labeled", str(tmp_path / "nope.jsonl"),
                 "--judge", "x"]) != 0
    err = capsys.readouterr().err
    assert "Traceback" not in err and err.strip()


def test_harness_never_writes_config_or_store(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    (store / "tags.json").write_text("{}", encoding="utf-8")

    def digest():
        h = hashlib.sha256()
        for p in [ROOT / "src/learned/config.py", *sorted(store.rglob("*"))]:
            if p.is_file():
                h.update(p.read_bytes())
        return h.hexdigest()

    labels = write_labels(tmp_path / "l.jsonl", [
        {"mode": "promote", "texts": ["a"], "expected": "none"}])
    before = digest()
    for mode in ("capture", "dedupe", "tag", "promote"):
        main(["--mode", mode, "--labeled", str(labels)], judge=FakeJudge())
    assert digest() == before
    assert run_eval.__doc__ and "never" in run_eval.__doc__.lower()
