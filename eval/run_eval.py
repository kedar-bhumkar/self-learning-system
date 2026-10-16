"""Evaluation harness for the learned-memory judgments.

It prints plain-text tables (and, with a real judge, suggested thresholds) and NEVER edits
src/learned/config.py or touches the store; copy chosen values into config by hand.

Usage:
    python -m eval.run_eval --mode {capture,dedupe,tag,promote} --labeled eval/labeled.jsonl
        [--transcripts DIR]   (capture only: replay transcripts, match windows to labels by text)
        [--judge {fake,jev}]  (default fake = smoke run only; jev needs TYPESAFE_API_KEY)

Labeled file schema (JSON Lines; blank lines and lines starting with '#' are ignored; every
line has a "mode" field and only lines of the selected mode are used):
    capture: {"mode":"capture","text":str,"context":str,"expected_kind":
              "rejection"|"decision"|"preference"|"correction"|"none"}
    dedupe:  {"mode":"dedupe","a":str,"b":str,"same":bool}
    tag:     {"mode":"tag","text":str,"tags":{name:description},"expected":name|null}
    promote: {"mode":"promote","texts":[str,...],"expected":"none"|"topic"|"global"}

eval/labeled.jsonl shipped in the repo is a SYNTHETIC example only. The user must replace it
with real labels (30-50 facts) before trusting any number.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from learned.config import Thresholds
from learned.transcript import extract_windows

KINDS = ("rejection", "decision", "preference", "correction")
SWEEP = [round(0.70 + 0.05 * i, 2) for i in range(6)]
LABELS = ("none", "topic", "global")
FAKE_BANNER = "FAKE JUDGE — smoke run only"


def _prf(tp: int, fp: int, fn: int) -> dict:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return {"precision": p, "recall": r, "f1": f, "tp": tp, "fp": fp, "fn": fn}


def capture_metrics(pairs) -> dict:
    """pairs: (expected_kind, predicted_kind); 'none' means not a fact."""
    out = {}
    for kind in KINDS:
        tp = sum(1 for e, p in pairs if e == kind and p == kind)
        fp = sum(1 for e, p in pairs if p == kind and e != kind)
        fn = sum(1 for e, p in pairs if e == kind and p != kind)
        out[kind] = _prf(tp, fp, fn)
    tp = sum(1 for e, p in pairs if e != "none" and e == p)
    fp = sum(1 for e, p in pairs if p != "none" and e != p)
    fn = sum(1 for e, p in pairs if e != "none" and e != p)
    out["overall"] = _prf(tp, fp, fn)
    return out


def dedupe_metrics(pairs) -> dict:
    """pairs: (expected_same, predicted_same)."""
    tp = sum(1 for e, p in pairs if e and p)
    fp = sum(1 for e, p in pairs if p and not e)
    fn = sum(1 for e, p in pairs if e and not p)
    out = _prf(tp, fp, fn)
    out["accuracy"] = sum(1 for e, p in pairs if e == p) / len(pairs) if pairs else 0.0
    return out


def tag_metrics(rows) -> dict:
    """rows: (outcome in reuse|new|none, predicted_tag, expected_tag)."""
    n = len(rows) or 1
    reuse = [r for r in rows if r[0] == "reuse"]
    return {
        "reuse_rate": len(reuse) / n,
        "new_rate": sum(1 for r in rows if r[0] == "new") / n,
        "untagged_rate": sum(1 for r in rows if r[0] == "none") / n,
        "reuse_accuracy": (sum(1 for r in reuse if r[1] == r[2]) / len(reuse)) if reuse else 0.0,
        "n": len(rows),
    }


def promotion_sweep(rows) -> list:
    """rows: (personal, expected[, passes_gate=True]). Returns [(threshold, cm)] where
    cm[predicted][expected] counts; predicted global iff gate passes and personal >= threshold,
    topic if the gate passes otherwise, none if the gate fails."""
    out = []
    for t in SWEEP:
        cm = {p: {e: 0 for e in LABELS} for p in LABELS}
        for row in rows:
            personal, expected = row[0], row[1]
            gate = row[2] if len(row) > 2 else True
            pred = "none" if not gate else ("global" if personal >= t else "topic")
            cm[pred][expected] += 1
        out.append((t, cm))
    return out


def false_global(cm) -> int:
    return cm["global"]["none"] + cm["global"]["topic"]


def suggest_promote_global(rows) -> tuple[float, str]:
    for t, cm in promotion_sweep(rows):
        if false_global(cm) == 0:
            return t, f"lowest threshold with zero false-global on the labeled set: {t:.2f}"
    return 0.95, "no threshold in 0.70..0.95 has zero false-global; falling back to 0.95"


# ---- output ----------------------------------------------------------------------------

def _table(headers, rows) -> str:
    cells = [[str(c) for c in r] for r in rows]
    w = [max([len(h), *(len(r[i]) for r in cells)]) for i, h in enumerate(headers)]

    def line(r):
        return "  ".join(c.ljust(w[i]) for i, c in enumerate(r)).rstrip()

    return "\n".join([line(headers), line(["-" * x for x in w]), *map(line, cells)])


def _f(x) -> str:
    return f"{x:.3f}"


# ---- modes -----------------------------------------------------------------------------

def _load(path: Path, mode: str) -> list[dict]:
    rows = []
    for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        raw = raw.strip()
        if not raw or raw.startswith("#"):
            continue
        try:
            obj = json.loads(raw)
        except ValueError:
            raise ValueError(f"{path}:{n}: invalid JSON") from None
        if isinstance(obj, dict) and obj.get("mode") == mode:
            rows.append(obj)
    return rows


def _predict_kind(judge, text, context, th) -> str:
    v = judge.classify_window(text, context)
    if v.kind == "none" or v.kind_prob < th.window_min_prob \
            or v.sensitive_prob >= th.sensitive_drop:
        return "none"
    return v.kind


def run_capture(rows, judge, th, transcripts: Path | None) -> str:
    notes = []
    if transcripts is not None:
        by_text = {r["text"].strip(): r for r in rows}
        rows, unmatched = [], 0
        for f in sorted(transcripts.glob("*.jsonl")):
            for w in extract_windows(f):
                lab = by_text.get(w.text.strip())
                if lab is None:
                    unmatched += 1
                else:
                    rows.append({"text": w.text, "context": w.context,
                                 "expected_kind": lab["expected_kind"]})
        notes.append(f"transcript windows matched to labels: {len(rows)}; unlabeled: {unmatched}")
    pairs = [(r["expected_kind"], _predict_kind(judge, r["text"], r.get("context", ""), th))
             for r in rows]
    m = capture_metrics(pairs)
    body = _table(["kind", "precision", "recall", "f1", "tp", "fp", "fn"],
                  [[k, _f(v["precision"]), _f(v["recall"]), _f(v["f1"]), v["tp"], v["fp"], v["fn"]]
                   for k, v in m.items()])
    return "\n".join([f"capture: {len(pairs)} labeled windows", *notes, body,
                      f"(window_min_prob={th.window_min_prob}, "
                      f"sensitive_drop={th.sensitive_drop})"])


def run_dedupe(rows, judge, th) -> str:
    pairs = [(bool(r["same"]), judge.same_fact(r["a"], r["b"]) >= th.same_fact) for r in rows]
    m = dedupe_metrics(pairs)
    return "\n".join([f"dedupe: {len(pairs)} labeled pairs",
                      _table(["accuracy", "precision", "recall", "f1"],
                             [[_f(m["accuracy"]), _f(m["precision"]), _f(m["recall"]),
                               _f(m["f1"])]]),
                      f"(same_fact={th.same_fact})"])


def run_tag(rows, judge, th) -> str:
    out = []
    for r in rows:
        tags = r.get("tags", {})
        v = judge.choose_tag(r["text"], tags)
        probs = {k: p for k, p in v.probabilities.items() if k != "none"}
        top = max(probs.values(), default=0.0)
        if v.tag is not None and v.tag in tags and probs.get(v.tag, 0.0) >= th.tag_reuse:
            out.append(("reuse", v.tag, r.get("expected")))
        elif v.probabilities.get("none", 0.0) >= th.new_tag_none and top < th.new_tag_runner_up:
            out.append(("new", None, r.get("expected")))
        else:
            out.append(("none", None, r.get("expected")))
    m = tag_metrics(out)
    return "\n".join([f"tag: {m['n']} labeled facts",
                      _table(["reuse_rate", "new_tag_rate", "untagged_rate", "reuse_accuracy"],
                             [[_f(m["reuse_rate"]), _f(m["new_rate"]), _f(m["untagged_rate"]),
                               _f(m["reuse_accuracy"])]]),
                      f"(tag_reuse={th.tag_reuse}, new_tag_none={th.new_tag_none}, "
                      f"new_tag_runner_up={th.new_tag_runner_up})"])


def run_promote(rows, judge, th, show_suggestion: bool) -> str:
    data = []
    for r in rows:
        v = judge.promotion(list(r["texts"]))
        gate = v.same >= th.promote_topic and v.stable >= th.promote_topic
        data.append((v.personal, r["expected"], gate))
    lines = [f"promote: {len(data)} labeled clusters (rows = predicted, cols = expected)"]
    for t, cm in promotion_sweep(data):
        lines.append(f"\npromote_global >= {t:.2f}   false-global: {false_global(cm)}")
        lines.append(_table(["pred\\exp", *LABELS],
                            [[p, *(cm[p][e] for e in LABELS)] for p in LABELS]))
    lines.append("\ncost note: a wrong global promotion is worse than a missed one, so the "
                 "suggestion is the lowest threshold with zero false-global.")
    if show_suggestion:
        t, note = suggest_promote_global(data)
        lines.append(f"suggested promote_global = {t:.2f}  ({note})")
    else:
        lines.append("(threshold suggestions withheld: fake judge)")
    return "\n".join(lines)


# ---- entry point -----------------------------------------------------------------------

def _fail(msg: str) -> int:
    print(f"error: {msg}", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None, judge=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    class _Parser(argparse.ArgumentParser):
        def error(self, message):  # one-line message instead of usage dump
            print(f"error: {message}", file=sys.stderr)
            raise SystemExit(2)

    ap = _Parser(prog="run_eval")
    ap.add_argument("--mode", required=True, choices=["capture", "dedupe", "tag", "promote"])
    ap.add_argument("--labeled", required=True)
    ap.add_argument("--transcripts")
    ap.add_argument("--judge", default="fake", choices=["fake", "jev"])
    try:
        args = ap.parse_args(argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) and e.code else 2
    path = Path(args.labeled)
    if not path.is_file():
        return _fail(f"labeled file not found: {path}")
    if args.transcripts and args.mode != "capture":
        return _fail("--transcripts is only valid with --mode capture")
    tdir = Path(args.transcripts) if args.transcripts else None
    if tdir is not None and not tdir.is_dir():
        return _fail(f"transcripts directory not found: {tdir}")
    try:
        rows = _load(path, args.mode)
    except (ValueError, OSError) as e:
        return _fail(str(e))
    if judge is None:
        if args.judge == "jev":
            from learned.judge import JevJudge  # lazy: only when the user runs it
            judge = JevJudge()
        else:
            from learned.testing import FakeJudge
            judge = FakeJudge()
    fake = args.judge == "fake"
    th = Thresholds()
    try:
        if args.mode == "capture":
            text = run_capture(rows, judge, th, tdir)
        elif args.mode == "dedupe":
            text = run_dedupe(rows, judge, th)
        elif args.mode == "tag":
            text = run_tag(rows, judge, th)
        else:
            text = run_promote(rows, judge, th, show_suggestion=not fake)
    except (KeyError, TypeError) as e:
        return _fail(f"bad labeled record for mode {args.mode}: {e!r}")
    except Exception as e:  # judge/API failure: one line, no traceback
        return _fail(f"{type(e).__name__}: {e}")
    if fake:
        print(FAKE_BANNER)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
