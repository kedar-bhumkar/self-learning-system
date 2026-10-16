import threading

import pytest

from learned.fsutil import atomic_write_text, create_numbered


def test_atomic_write_text_replaces_and_leaves_no_temp(tmp_path):
    p = tmp_path / "a.json"
    atomic_write_text(p, "one café")
    atomic_write_text(p, "two")
    assert p.read_text(encoding="utf-8") == "two"
    assert [f.name for f in tmp_path.iterdir()] == ["a.json"]


def test_create_numbered_never_overwrites(tmp_path):
    a = create_numbered(tmp_path, "s", ".jsonl", "A")
    b = create_numbered(tmp_path, "s", ".jsonl", "B")
    assert (a.name, b.name) == ("s.jsonl", "s.1.jsonl")
    assert a.read_text(encoding="utf-8") == "A" and b.read_text(encoding="utf-8") == "B"
    assert sorted(f.name for f in tmp_path.iterdir()) == ["s.1.jsonl", "s.jsonl"]


def test_create_numbered_concurrent_writers_do_not_clobber(tmp_path):
    out = []

    def work(i):
        out.append(create_numbered(tmp_path, "s", ".jsonl", f"w{i}"))

    ts = [threading.Thread(target=work, args=(i,)) for i in range(12)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len({p.name for p in out}) == 12
    assert sorted(p.read_text(encoding="utf-8") for p in out) == sorted(f"w{i}" for i in range(12))
    assert not list(tmp_path.glob("*.tmp"))


def test_atomic_write_text_retries_permission_error(tmp_path, monkeypatch):
    import os
    from learned import fsutil
    real = os.replace
    calls = []

    def flaky(src, dst):
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError("held open")
        return real(src, dst)

    monkeypatch.setattr(fsutil.os, "replace", flaky)
    p = tmp_path / "a.txt"
    atomic_write_text(p, "ok")
    assert p.read_text(encoding="utf-8") == "ok" and len(calls) == 3
    assert [f.name for f in tmp_path.iterdir()] == ["a.txt"]


def test_atomic_write_text_gives_up_after_three_tries(tmp_path, monkeypatch):
    from learned import fsutil
    calls = []

    def locked(src, dst):
        calls.append(1)
        raise PermissionError("held open")

    monkeypatch.setattr(fsutil.os, "replace", locked)
    with pytest.raises(PermissionError):
        atomic_write_text(tmp_path / "a.txt", "x")
    assert len(calls) == 3
    assert list(tmp_path.iterdir()) == []
