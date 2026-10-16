import json

import pytest

from learned.capture import capture_session
from learned.config import Thresholds
from learned.models import Candidate, WindowVerdict
from learned.store import Store
from learned.testing import FakeJudge


def tline(type_, content, i=0):
    return json.dumps({"type": type_, "message": {"role": type_, "content": content},
                       "timestamp": f"2026-01-01T00:00:{i:02d}Z"}, ensure_ascii=False)


def make_transcript(tmp_path, texts, name="t.jsonl"):
    p = tmp_path / name
    p.write_text("\n".join(tline("user", t, i) for i, t in enumerate(texts)) + "\n",
                 encoding="utf-8")
    return p


def append(p, texts):
    with open(p, "a", encoding="utf-8") as f:
        for i, t in enumerate(texts):
            f.write(tline("user", t, 30 + i) + "\n")


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "store")
    s.init()
    return s


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("LEARNED_EXTRACTOR", raising=False)


def cands(store):
    out = []
    for f in sorted((store.home / "inbox").glob("*.jsonl")):
        out += [Candidate.from_json(ln) for ln in f.read_text(encoding="utf-8").splitlines()]
    return out


def rejecting(text, context):
    return WindowVerdict("rejection", 0.9, 0.0)


def work(tmp_path):
    d = tmp_path / "My Project"
    d.mkdir()
    return d


def test_writes_classified_candidates_above_threshold(tmp_path, store):
    t = make_transcript(tmp_path, ["no, not X", "use Y", "ok"])
    cwd = work(tmp_path)
    n = capture_session(t, "sid1", str(cwd), FakeJudge(classify_window=rejecting), store)
    assert n == 3
    cs = cands(store)
    assert [c.text for c in cs] == ["no, not X", "use Y", "ok"]
    assert cs[0].kind == "rejection" and cs[0].kind_prob == 0.9
    assert cs[0].project == "my project" and cs[0].session_id == "sid1"
    assert cs[0].timestamp.startswith("2026-")
    assert (store.home / "inbox" / "sid1.jsonl").exists()
    assert not list((store.home / "inbox").glob("*.tmp"))


def test_drops_none_low_prob_and_sensitive(tmp_path, store):
    t = make_transcript(tmp_path, ["a-none", "b-low", "c-sens", "d-good"])

    def judge(text, context):
        return {"a-none": WindowVerdict("none", 0.95, 0.0),
                "b-low": WindowVerdict("decision", 0.4, 0.0),
                "c-sens": WindowVerdict("decision", 0.9, 0.9),
                "d-good": WindowVerdict("decision", 0.9, 0.1)}[text]

    n = capture_session(t, "s", str(work(tmp_path)), FakeJudge(classify_window=judge), store)
    assert n == 1 and [c.text for c in cands(store)] == ["d-good"]


def test_text_is_redacted_before_judge_sees_it(tmp_path, store):
    t = make_transcript(tmp_path, ["use key sk-abc123def456ghi789 now", "mail a@b.com", "x"])
    seen = []

    def judge(text, context):
        seen.append(text + context)
        return WindowVerdict("decision", 0.9, 0.0)

    capture_session(t, "s", str(work(tmp_path)), FakeJudge(classify_window=judge), store)
    assert seen and all("sk-abc" not in s and "a@b.com" not in s for s in seen)
    stored = (store.home / "inbox" / "s.jsonl").read_text(encoding="utf-8")
    assert "sk-abc" not in stored and "a@b.com" not in stored


def test_skips_when_extractor_env_set(tmp_path, store, monkeypatch):
    monkeypatch.setenv("LEARNED_EXTRACTOR", "1")
    t = make_transcript(tmp_path, ["a", "b", "c"])
    assert capture_session(t, "s", str(work(tmp_path)),
                           FakeJudge(classify_window=rejecting), store) == 0
    assert not list((store.home / "inbox").glob("*.jsonl"))


def test_skips_when_no_learn_file(tmp_path, store):
    cwd = work(tmp_path)
    (cwd / ".no-learn").write_text("", encoding="utf-8")
    t = make_transcript(tmp_path, ["a", "b", "c"])
    assert capture_session(t, "s", str(cwd), FakeJudge(classify_window=rejecting), store) == 0
    assert not list((store.home / "inbox").glob("*.jsonl"))


def test_skips_short_sessions(tmp_path, store):
    t = make_transcript(tmp_path, ["a", "b"])
    assert capture_session(t, "s", str(work(tmp_path)),
                           FakeJudge(classify_window=rejecting), store, Thresholds()) == 0
    assert not (store.home / "inbox" / ".done" / "s.json").exists()


def test_watermark_only_new_windows_on_second_call(tmp_path, store):
    t = make_transcript(tmp_path, ["one", "two", "three"])
    cwd = str(work(tmp_path))
    j = FakeJudge(classify_window=rejecting)
    assert capture_session(t, "s", cwd, j, store) == 3
    wm = json.loads((store.home / "inbox" / ".done" / "s.json").read_text(encoding="utf-8"))
    assert wm == {"last_line": 3}
    assert capture_session(t, "s", cwd, j, store) == 0
    append(t, ["four", "five", "six"])
    assert capture_session(t, "s", cwd, j, store) == 3
    files = sorted(f.name for f in (store.home / "inbox").glob("*.jsonl"))
    assert files == ["s.1.jsonl", "s.jsonl"]
    second = (store.home / "inbox" / "s.1.jsonl").read_text(encoding="utf-8")
    assert "four" in second and "one" not in second
    assert json.loads((store.home / "inbox" / ".done" / "s.json").read_text(
        encoding="utf-8")) == {"last_line": 6}


def test_judge_failure_stores_unclassified(tmp_path, store):
    t = make_transcript(tmp_path, ["a", "b", "c"])
    calls = []

    def boom(text, context):
        calls.append(text)
        raise RuntimeError("api down")

    n = capture_session(t, "s", str(work(tmp_path)), FakeJudge(classify_window=boom), store)
    assert n == 3
    assert all(c.kind is None and c.kind_prob == 0.0 for c in cands(store))
    assert len(calls) == 1  # stops calling a failing judge


def test_writes_project_meta(tmp_path, store):
    t = make_transcript(tmp_path, ["a", "b", "c"])
    cwd = work(tmp_path)
    capture_session(t, "s", str(cwd), FakeJudge(classify_window=rejecting), store)
    meta = store.home / "projects" / "my project" / "meta.json"
    assert json.loads(meta.read_text(encoding="utf-8")) == {"cwd": str(cwd)}


def test_unicode_text_roundtrip(tmp_path, store):
    t = make_transcript(tmp_path, ["no café ☕ \U0001F600", "b", "c"])
    capture_session(t, "s", str(work(tmp_path)), FakeJudge(classify_window=rejecting), store)
    assert cands(store)[0].text == "no café ☕ \U0001F600"


def test_main_without_api_key_stores_unclassified(tmp_path, monkeypatch):
    from learned import capture
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    home = tmp_path / "home"
    monkeypatch.setenv("LEARNED_HOME", str(home))
    t = make_transcript(tmp_path, ["no not X", "use Y", "ok"])
    cwd = work(tmp_path)
    assert capture.main([str(t), "sid", str(cwd)]) == 0
    cs = cands(Store(home))
    assert [c.text for c in cs] == ["no not X", "use Y", "ok"]
    assert all(c.kind is None for c in cs)
    log = (home / "capture.log").read_text(encoding="utf-8")
    assert "judge failed" in log and len(log.splitlines()) == 1


def test_main_logs_programming_errors_and_exits_zero(tmp_path, monkeypatch):
    from learned import capture
    home = tmp_path / "home"
    monkeypatch.setenv("LEARNED_HOME", str(home))

    def boom(path):
        raise KeyError("kaboom")

    monkeypatch.setattr(capture, "extract_windows", boom)
    assert capture.main([str(tmp_path / "t.jsonl"), "sid", str(tmp_path)]) == 0
    lines = (home / "capture.log").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert "KeyError" in lines[0] and "kaboom" in lines[0] and lines[0][:4].isdigit()
    assert capture.main(["too", "few"]) == 0
    assert len((home / "capture.log").read_text(encoding="utf-8").splitlines()) == 2


def test_capture_log_is_gitignored_in_store(tmp_path, monkeypatch):
    from learned import capture
    capture.log_error(tmp_path, "x")
    s = Store(tmp_path)
    s.init()
    s.commit("c")
    tracked = __import__("subprocess").run(["git", "-C", str(tmp_path), "ls-files"],
                                           capture_output=True, text=True).stdout
    assert "capture.log" not in tracked


def test_two_workers_same_session_do_not_overwrite(tmp_path, store):
    t = make_transcript(tmp_path, ["one", "two", "three"])
    cwd = str(work(tmp_path))
    j = FakeJudge(classify_window=rejecting)
    assert capture_session(t, "s", cwd, j, store) == 3
    # a second worker that started before the first wrote its watermark sees the same windows
    (store.home / "inbox" / ".done" / "s.json").unlink()
    assert capture_session(t, "s", cwd, j, store) == 3
    files = sorted(f.name for f in (store.home / "inbox").glob("*.jsonl"))
    assert files == ["s.1.jsonl", "s.jsonl"]
    assert not list((store.home / "inbox").glob("*.tmp"))
