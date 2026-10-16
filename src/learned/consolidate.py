"""Daily consolidation: lock -> ingest -> tag -> promote -> gists -> weekly sweep -> core -> commit.

run() takes the judge, embedder and claude runner as arguments; main() builds the real ones
lazily (so tests of run() never need the SDK or the embedding model).
"""
from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .config import Thresholds, learned_home
from .core import write_core_md
from .gist import refresh_gists
from .ingest import IngestReport, ingest
from .logutil import log_line
from .promotion import PromotionEvent, promote
from .store import Store
from .tagging import assign_tag

LOCK_STALE_SECONDS = 6 * 3600
SWEEP_DAYS = 7
SHADOW_DAYS = 7
LOG = "consolidate.log"


@dataclass
class RunReport:
    locked: bool = False
    ingest: IngestReport = field(default_factory=IngestReport)
    tagged: int = 0
    new_tags: list[str] = field(default_factory=list)
    promotions: list[PromotionEvent] = field(default_factory=list)
    gists: list[str] = field(default_factory=list)
    swept: bool = False
    decayed: int = 0
    rechecked: int = 0
    committed: bool = False
    jev_failed: bool = False
    errors: list[str] = field(default_factory=list)


def _dt(ts: str) -> datetime:
    d = datetime.fromisoformat(ts)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def decay(store: Store, th: Thresholds, now: str) -> int:
    """Mark unpinned active records not seen for stale_days as stale (never deletes)."""
    cutoff = _dt(now) - timedelta(days=th.stale_days)
    n = 0
    for r in store.load_all():
        if r.pinned or r.stale or r.kind == "project-gist":
            continue
        try:
            seen = _dt(r.last_seen)
        except ValueError:
            continue
        if seen < cutoff:
            r.stale = True
            store.save(r)
            n += 1
    return n


def recheck_new_tags(store: Store, judge, new_tags: list[str], th: Thresholds) -> int:
    """Ask Jev whether older records fit each new tag (that tag only); returns tags added."""
    vocab = store.tags()
    n = 0
    for tag in new_tags:
        if tag not in vocab:
            continue
        for r in store.load_all():
            if tag in r.tags or r.pinned or r.kind == "project-gist":
                continue
            if judge.fits_tag(r.body, tag, vocab[tag]) >= th.tag_reuse:
                r.tags = [*r.tags, tag]
                store.save(r)
                n += 1
    return n


def _acquire(lock: Path) -> bool:
    for _ in range(2):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                age = time.time() - lock.stat().st_mtime
            except OSError:
                continue  # vanished between the two calls: retry
            if age <= LOCK_STALE_SECONDS:
                return False
            try:
                lock.unlink()
            except OSError:
                return False
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
        return True
    return False


def run(home: Path, judge, embedder, runner, th: Thresholds, now: str,
        full: bool = False) -> RunReport:
    home = Path(home)
    rep = RunReport()
    store = Store(home)
    store.init()
    lock = home / ".lock"
    if not _acquire(lock):
        rep.locked = True
        return rep
    try:
        _pipeline(store, judge, embedder, runner, th, now, full, rep)
    finally:
        try:
            lock.unlink()
        except OSError:
            pass
    return rep


def _dedupe(items) -> list:
    return list(dict.fromkeys(items))


def _shadow(cfg: dict, today: date, prepared: bool) -> bool:
    """Fail closed: anything but a parseable shadow_until before today means shadow."""
    if not prepared:
        return True
    try:
        return date.fromisoformat(str(cfg.get("shadow_until"))[:10]) >= today
    except ValueError:
        return True


def _pipeline(store: Store, judge, embedder, runner, th: Thresholds, now: str, full: bool,
              rep: RunReport) -> None:
    def fail(what: str, e: BaseException) -> None:
        rep.errors.append(f"{what}: {type(e).__name__}: {e}")
        log_line(store.home, LOG, f"{what} failed", e)

    def step(what: str, fn) -> None:
        try:
            fn()
        except Exception as e:  # one stage failing must not undo or block the others
            fail(what, e)

    def pending(key: str) -> list:
        return list(store.config().get(key, []))

    today = _dt(now).date()
    state: dict = {"ids": [], "tags_before": None, "prepared": False, "retag": set()}

    def prepare() -> None:
        cfg = store.config()
        if "shadow_until" not in cfg:  # first run: one week of log-only promotions
            store.set_config(shadow_until=(today + timedelta(days=SHADOW_DAYS)).isoformat())
        state["tags_before"] = dict(store.tags())
        if full:
            # cached judgments are dropped; old tags stay until a new assignment succeeds
            for r in store.load_all():
                if r.pinned or r.kind == "project-gist":
                    continue
                r.judgments = {}
                store.save(r)
                state["ids"].append(r.id)
                state["retag"].add(r.id)
        state["prepared"] = True

    step("prepare", prepare)

    def do_ingest() -> None:
        rep.ingest = ingest(store, judge, embedder, th, now)
        rep.jev_failed = rep.ingest.jev_failed
        for i in [*rep.ingest.touched, *rep.ingest.created]:
            if i not in state["ids"]:
                state["ids"].append(i)

    step("ingest", do_ingest)

    def do_tag() -> None:
        ids = _dedupe([*pending("pending_tag"), *state["ids"]])
        for idx, rid in enumerate(ids):
            r = store.get(rid)
            if r is None or r.pinned or r.status != "active":
                continue
            retag = rid in state["retag"]
            if r.tags and not retag:
                continue
            old = list(r.tags)
            r.tags = []
            try:
                assign_tag(store, judge, runner, r, th)
            except Exception as e:
                rep.jev_failed = True
                fail("tagging", e)
                store.set_config(pending_tag=ids[idx:])  # retried next run; old tags intact
                return
            if r.tags != old:
                store.save(r)
                if r.tags:
                    rep.tagged += 1
        store.set_config(pending_tag=[])

    step("tagging", do_tag)

    def note_new_tags() -> None:
        if state["tags_before"] is None:
            return
        rep.new_tags = [t for t in store.tags() if t not in state["tags_before"]]
        if rep.new_tags:
            store.set_config(pending_recheck=_dedupe([*pending("pending_recheck"),
                                                      *rep.new_tags]))

    step("new tags", note_new_tags)

    def do_promote() -> None:
        cfg = store.config()
        shadow = _shadow(cfg, today, state["prepared"])
        ids = _dedupe([*pending("pending_promote"), *state["ids"]])
        sweep_all = not shadow and not cfg.get("shadow_applied")
        if sweep_all:  # shadow just ended: apply verdicts cached during shadow (one pass)
            ids = _dedupe([*ids, *(r.id for r in store.load_all(scope="project")
                                   if not r.pinned and r.kind != "project-gist")])
        touched = [r for r in (store.get(i) for i in ids) if r is not None]
        failed: list[str] = []
        rep.promotions = promote(store, judge, embedder, th, touched, shadow, now, failed)
        if failed:
            rep.jev_failed = True
            rep.errors.append("promotion: judge failed; will retry next run")
            store.set_config(pending_promote=_dedupe(failed))
            return
        store.set_config(pending_promote=[])
        if sweep_all:
            store.set_config(shadow_applied=True)

    step("promotion", do_promote)

    def do_gists() -> None:
        by_project: dict = {}
        for r in (store.get(i) for i in state["ids"]):
            if r is not None and r.status == "active":
                by_project.setdefault(r.project, []).append(r)
        projects = set(by_project) | set(rep.ingest.projects)
        rep.gists = refresh_gists(store, runner, projects, by_project)

    step("gists", do_gists)

    def sweep_due() -> bool:
        last = store.config().get("last_sweep")
        try:
            return last is None or _dt(now) - _dt(last) >= timedelta(days=SWEEP_DAYS)
        except (ValueError, TypeError):
            return True

    def do_sweep() -> None:
        if not sweep_due():
            return
        rep.swept = True
        rep.decayed = decay(store, th, now)
        todo = _dedupe(pending("pending_recheck"))
        if todo:
            try:
                rep.rechecked = recheck_new_tags(store, judge, todo, th)
            except Exception as e:
                rep.jev_failed = True
                fail("tag recheck", e)
                store.set_config(last_sweep=now)  # keep pending for the next sweep
                return
        store.set_config(last_sweep=now, pending_recheck=[])

    step("weekly sweep", do_sweep)
    step("core.md", lambda: write_core_md(store, th))

    def do_commit() -> None:
        i = rep.ingest
        applied = sum(1 for e in rep.promotions if e.applied)
        msg = (f"consolidate: {len(i.created)} new, {len(i.merged)} merged, "
               f"{len(i.superseded)} superseded, {applied} promoted, "
               f"{len(rep.promotions) - applied} shadow-logged, "
               f"{rep.tagged} tagged, {len(rep.new_tags)} new tags"
               + (", jev failed (will retry)" if rep.jev_failed else ""))
        rep.committed = store.commit(msg)

    try:
        do_commit()
    except (RuntimeError, OSError) as e:  # a failed commit never undoes the work
        fail("commit", e)


def main(argv: list[str]) -> int:
    home = None
    try:
        home = learned_home()
        if not os.environ.get("TYPESAFE_API_KEY"):
            log_line(home, LOG, "TYPESAFE_API_KEY is not set; consolidation skipped, "
                                "inbox left untouched")
            return 0
        from .embed import Embedder
        from .gist import claude_p
        from .judge import JevJudge
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        rep = run(home, JevJudge(), Embedder(), claude_p, Thresholds(), now,
                  full="--full" in argv)
        if rep.locked:
            log_line(home, LOG, "another consolidation run holds the lock; skipped")
        return 0
    except Exception as e:
        if home is not None:
            log_line(home, LOG, "consolidation crashed", e)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
