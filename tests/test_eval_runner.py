import hashlib
import json
from pathlib import Path

import pytest

from learned.config import Thresholds

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


ALL_MODES_ROWS = [
    {"mode": "capture", "text": "No uv", "context": "", "expected_kind": "rejection"},
    {"mode": "dedupe", "a": "x", "b": "y", "same": False},
    {"mode": "tag", "text": "t", "tags": {"a": "A"}, "expected": "a"},
    {"mode": "promote", "texts": ["a", "b"], "expected": "global"},
]
MODES = ("capture", "dedupe", "tag", "promote")


def test_harness_never_writes_config_or_creates_files(tmp_path, monkeypatch):
    # The store is only reachable via learned_home(); main never imports Store, so the
    # no-new-files assertion over tmp_path (cwd and LEARNED_HOME) is the store guarantee.
    work = tmp_path / "work"
    work.mkdir()
    labels = write_labels(work / "l.jsonl", ALL_MODES_ROWS)
    monkeypatch.setenv("LEARNED_HOME", str(work / "home"))
    monkeypatch.chdir(work)
    cfg = ROOT / "src/learned/config.py"
    before_cfg = hashlib.sha256(cfg.read_bytes()).hexdigest()
    before_files = sorted(p for p in tmp_path.rglob("*"))
    for mode in MODES:
        assert main(["--mode", mode, "--labeled", str(labels)], judge=FakeJudge()) == 0
    assert sorted(p for p in tmp_path.rglob("*")) == before_files
    assert Path.cwd() == work
    assert hashlib.sha256(cfg.read_bytes()).hexdigest() == before_cfg
    assert run_eval.__doc__ and "never" in run_eval.__doc__.lower()


@pytest.mark.parametrize("mode", MODES)
def test_fake_banner_in_every_mode(tmp_path, capsys, mode):
    labels = write_labels(tmp_path / "l.jsonl", ALL_MODES_ROWS)
    assert main(["--mode", mode, "--labeled", str(labels)], judge=FakeJudge()) == 0
    assert "FAKE JUDGE — smoke run only" in capsys.readouterr().out


def test_banner_on_cp1252_stream(tmp_path, monkeypatch):
    import io
    buf = io.BytesIO()
    stream = io.TextIOWrapper(buf, encoding="cp1252", errors="replace")
    monkeypatch.setattr("sys.stdout", stream)
    labels = write_labels(tmp_path / "l.jsonl", ALL_MODES_ROWS)
    assert main(["--mode", "capture", "--labeled", str(labels)], judge=FakeJudge()) == 0
    stream.flush()
    assert "FAKE JUDGE" in buf.getvalue().decode("utf-8", errors="replace")


@pytest.mark.parametrize("row,fragment", [
    ({"mode": "capture", "text": "t", "context": "", "expected_kind": "rejecton"},
     "expected_kind"),
    ({"mode": "capture", "text": "t"}, "missing field(s) expected_kind"),
    ({"mode": "dedupe", "a": "x", "b": "y", "same": "false"}, "same must be a JSON boolean"),
    ({"mode": "tag", "text": "t", "tags": ["a"], "expected": None}, "tags must be an object"),
    ({"mode": "promote", "texts": [], "expected": "none"}, "non-empty list"),
    ({"mode": "promote", "texts": ["a"], "expected": "globl"}, "expected"),
])
def test_label_validation_errors(tmp_path, capsys, row, fragment):
    labels = write_labels(tmp_path / "l.jsonl", [row])
    assert main(["--mode", row["mode"], "--labeled", str(labels)], judge=FakeJudge()) == 2
    err = capsys.readouterr().err
    assert fragment in err and f"{labels}:2:" in err and len(err.strip().splitlines()) == 1


def test_empty_mode_is_an_error(tmp_path, capsys):
    labels = write_labels(tmp_path / "l.jsonl", [ALL_MODES_ROWS[0]])
    assert main(["--mode", "tag", "--labeled", str(labels)], judge=FakeJudge()) == 2
    assert "no rows for mode tag" in capsys.readouterr().err


def test_help_returns_zero(capsys):
    assert main(["-h"]) == 0


def test_predict_kind_drops_low_prob_and_sensitive():
    th = Thresholds()
    pk = run_eval._predict_kind
    assert pk(FakeJudge(classify_window=lambda t, c: WindowVerdict("decision", 0.9, 0.0)),
              "t", "", th) == "decision"
    assert pk(FakeJudge(classify_window=lambda t, c: WindowVerdict("decision", 0.59, 0.0)),
              "t", "", th) == "none"
    assert pk(FakeJudge(classify_window=lambda t, c: WindowVerdict("decision", 0.9, 0.5)),
              "t", "", th) == "none"
    assert pk(FakeJudge(classify_window=lambda t, c: WindowVerdict("none", 0.99, 0.0)),
              "t", "", th) == "none"


def test_run_tag_exact_rates():
    verdicts = {
        "r1": TagVerdict("a", {"a": 0.9, "none": 0.05}),
        "r2": TagVerdict("a", {"a": 0.7, "none": 0.1}),
        "n": TagVerdict(None, {"a": 0.1, "none": 0.85}),
        "u": TagVerdict(None, {"a": 0.3, "none": 0.3}),
    }
    rows = [{"text": "r1", "tags": {"a": "A"}, "expected": "a"},
            {"text": "r2", "tags": {"a": "A"}, "expected": "b"},
            {"text": "n", "tags": {"a": "A"}, "expected": None},
            {"text": "u", "tags": {"a": "A"}, "expected": None}]
    judge = FakeJudge(choose_tag=lambda t, tags: verdicts[t])
    out = run_eval.run_tag(rows, judge, Thresholds())
    assert out.splitlines()[3].split() == ["0.500", "0.250", "0.250", "0.500"]


def test_run_promote_gate_and_exact_confusion():
    verdicts = {("A",): PromotionVerdict(0.97, 0.9, 0.9),    # global, passes
                ("B",): PromotionVerdict(0.80, 0.9, 0.9),    # topic, personal low
                ("C",): PromotionVerdict(0.99, 0.5, 0.9)}    # gate fails -> none
    rows = [{"texts": ["A"], "expected": "global"}, {"texts": ["B"], "expected": "topic"},
            {"texts": ["C"], "expected": "none"}]
    judge = FakeJudge(promotion=lambda texts: verdicts[tuple(texts)])
    out = run_eval.run_promote(rows, judge, Thresholds(), show_suggestion=True)
    blocks = out.split("\n\npromote_global >= ")
    b70, b85 = blocks[1], blocks[4]
    assert b70.startswith("0.70   false-global: 1") and b85.startswith("0.85   false-global: 0")

    def cells(b, pred):
        return next(ln.split()[1:] for ln in b.splitlines() if ln.startswith(pred))
    assert cells(b70, "none") == ["1", "0", "0"]
    assert cells(b70, "topic") == ["0", "0", "0"]
    assert cells(b70, "global") == ["0", "1", "1"]
    assert cells(b85, "topic") == ["0", "1", "0"]
    assert "suggested promote_global = 0.85" in out


def test_no_global_rows_gives_no_vacuous_suggestion():
    rows = [{"texts": ["A"], "expected": "topic"}]
    out = run_eval.run_promote(rows, FakeJudge(), Thresholds(), show_suggestion=True)
    assert "not enough labeled global rows" in out and "suggested promote_global" not in out


def test_transcript_matching_counts(tmp_path):
    tdir = tmp_path / "t"
    tdir.mkdir()
    lines = [{"type": "user", "timestamp": "t", "message": {"content": txt}}
             for txt in ("No uv, use pip", "something unlabeled", "another unlabeled")]
    (tdir / "s.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
    rows = [{"text": "No uv, use pip", "context": "", "expected_kind": "rejection"}]
    judge = FakeJudge(classify_window=lambda t, c: WindowVerdict("rejection", 0.9, 0.0))
    out = run_eval.run_capture(rows, judge, Thresholds(), tdir)
    assert "matched to labels: 1; unlabeled: 2" in out
    overall = next(ln for ln in out.splitlines() if ln.startswith("overall"))
    assert overall.split()[1:] == ["1.000", "1.000", "1.000", "1", "0", "0"]


def test_real_judge_setup_failure_is_one_line(tmp_path, capsys, monkeypatch):
    from learned import judge as judge_mod

    def boom(self):
        raise ImportError("no sdk")
    monkeypatch.setattr(judge_mod.JevJudge, "client", property(boom))
    labels = write_labels(tmp_path / "l.jsonl", ALL_MODES_ROWS)
    assert main(["--mode", "capture", "--labeled", str(labels), "--judge", "jev"]) == 2
    err = capsys.readouterr().err
    assert "ImportError" in err and "Traceback" not in err and len(err.strip().splitlines()) == 1


def test_judge_errors_not_reported_as_bad_record(tmp_path, capsys):
    labels = write_labels(tmp_path / "l.jsonl", ALL_MODES_ROWS)

    def boom(t, c):
        raise KeyError("inside judge")
    assert main(["--mode", "capture", "--labeled", str(labels)],
                judge=FakeJudge(classify_window=boom)) == 2
    assert "bad labeled record" not in capsys.readouterr().err
