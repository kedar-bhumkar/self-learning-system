"""Controls CLI: list / show / forget / demote / pin / rerun (`python -m learned.cli`).

Pure local operations on the store; only `rerun --full` builds the real judge, embedder and
claude runner, lazily and through an injectable factory.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from .config import Thresholds, learned_home
from .records import render_record
from .store import Store

_DEMOTE = {"global": "topic", "topic": "project"}


class _Err(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _need(store: Store, id: str):
    r = store.get(id)
    if r is None:
        raise _Err(f"no record with id {id!r}")
    return r


def _one_line(text: str, n: int = 100) -> str:
    s = " ".join(text.split())
    return s if len(s) <= n else s[: n - 3] + "..."


def _list(store: Store, a) -> int:
    for r in store.load_all(scope=a.scope, project=a.project):
        if r.kind == "project-gist" or (a.tag and a.tag not in r.tags):
            continue
        print("\t".join([r.id, r.scope, r.project, r.kind, ",".join(r.tags),
                         _one_line(r.body)]))
    return 0


def _show(store: Store, a) -> int:
    sys.stdout.write(render_record(_need(store, a.id)))
    return 0


def _forget(store: Store, a) -> int:
    r = _need(store, a.id)
    try:
        store.add_tombstone(r.body)
        store.delete(r.id)
    except OSError as e:
        raise _Err(f"could not forget {a.id}: {e}") from e
    if store.get(r.id) is not None:
        raise _Err(f"could not delete {a.id}")
    try:
        store.commit(f"forget {r.id}")
    except (RuntimeError, OSError) as e:
        print(f"forgotten, but commit failed: {e}", file=sys.stderr)
    print(f"forgot {r.id}")
    return 0


def _demote(store: Store, a) -> int:
    r = _need(store, a.id)
    if r.status != "active":
        raise _Err(f"{a.id} is {r.status}, not active; cannot demote")
    nxt = _DEMOTE.get(r.scope)
    if nxt is None:
        raise _Err(f"{a.id} is already project scope; nothing to demote to")
    project = a.project or r.project
    if nxt == "project" and not project:
        raise _Err(f"{a.id} has no project; pass --project NAME to demote it into one")
    old_path = store.path_for(r)
    old = r.scope
    r.scope = nxt
    r.project = project
    r.last_seen = _now()
    new_path = store.save(r)  # write the new copy first; only then drop the old file
    if new_path != old_path:
        try:
            old_path.unlink()
        except FileNotFoundError:
            pass
    try:
        store.commit(f"demote {r.id} {old}->{nxt}")
    except (RuntimeError, OSError) as e:
        print(f"demoted, but commit failed: {e}", file=sys.stderr)
    print(f"{r.id}: {old} -> {nxt}")
    return 0


def _pin(store: Store, a) -> int:
    r = _need(store, a.id)
    r.pinned = True
    store.save(r)
    try:
        store.commit(f"pin {r.id}")
    except (RuntimeError, OSError) as e:
        print(f"pinned, but commit failed: {e}", file=sys.stderr)
    print(f"pinned {r.id}")
    return 0


def _default_factory():
    from .embed import Embedder
    from .gist import claude_p
    from .judge import JevJudge
    return JevJudge(), Embedder(), claude_p


def _rerun(store: Store, a, factory) -> int:
    if not a.full:
        raise _Err("rerun requires --full")
    from . import consolidate
    judge, embedder, runner = (factory or _default_factory)()
    rep = consolidate.run(store.home, judge, embedder, runner, Thresholds(), _now(), full=True)
    if rep.locked:
        raise _Err("another consolidation run holds the lock")
    if rep.errors:
        raise _Err(f"full rerun finished with {len(rep.errors)} error(s); first: {rep.errors[0]}")
    print(f"full rerun done: errors={len(rep.errors)} committed={rep.committed}")
    return 0


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="learned", description="Control the learned memory.")
    sub = p.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list", help="list active records")
    ls.add_argument("--scope")
    ls.add_argument("--project")
    ls.add_argument("--tag")
    for name in ("show", "forget", "demote", "pin"):
        sp = sub.add_parser(name)
        sp.add_argument("id")
        if name == "demote":
            sp.add_argument("--project")
    rr = sub.add_parser("rerun", help="force a full re-judgment")
    rr.add_argument("--full", action="store_true")
    return p


def main(argv: list[str], store: Store | None = None, factory=None) -> int:
    try:
        a = _parser().parse_args(argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 2
    try:
        store = store or Store(learned_home())
        if a.cmd == "rerun":
            return _rerun(store, a, factory)
        return {"list": _list, "show": _show, "forget": _forget,
                "demote": _demote, "pin": _pin}[a.cmd](store, a)
    except _Err as e:
        print(str(e), file=sys.stderr)
        return 1
    except Exception as e:  # never a traceback for the user
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main(sys.argv[1:]))
