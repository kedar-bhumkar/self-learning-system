"""Ingest: inbox candidates -> classified, deduplicated, merged or superseding records.

Judge and embedder are injected. Processed inbox files move to inbox/processed/, files with
malformed lines to inbox/rejected/. If the judge raises, the inbox file stays unprocessed
and report.jev_failed is set.

Dedupe neighbours are the same project's records plus topic/global ones. Near-duplicates in
other projects deliberately stay separate records so promotion can see the cross-project
cluster.
"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field

import numpy as np

from .config import Thresholds
from .logutil import log_line
from .models import Candidate
from .records import Record, new_id
from .store import Store


@dataclass
class IngestReport:
    created: list[str] = field(default_factory=list)
    merged: list[tuple[str, str]] = field(default_factory=list)   # (candidate id, existing id)
    superseded: list[str] = field(default_factory=list)
    skipped: int = 0
    jev_failed: bool = False
    touched: list[str] = field(default_factory=list)              # ids created or merged into
    projects: set[str] = field(default_factory=set)               # projects with created/merged


class _JudgeFailed(Exception):
    pass


class _Vectors:
    """Lazy text -> vector cache over the injected embedder."""

    def __init__(self, embedder):
        self.embedder = embedder
        self.cache: dict[str, np.ndarray] = {}

    def ensure(self, texts: list[str]) -> None:
        missing = list(dict.fromkeys(t for t in texts if t not in self.cache))
        if missing:
            for t, v in zip(missing, self.embedder.encode(missing)):
                self.cache[t] = np.asarray(v, dtype=float)

    def get(self, text: str) -> np.ndarray:
        self.ensure([text])
        return self.cache[text]


def _move_to(store: Store, path, subdir: str) -> None:
    dest_dir = store.home / "inbox" / subdir
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / path.name
    if dest.exists():
        dest = dest_dir / f"{path.stem}.{uuid.uuid4().hex[:6]}{path.suffix}"
    os.replace(path, dest)


def _read_candidates(path) -> list[Candidate]:
    """Parse an inbox file; raises ValueError naming the first malformed line."""
    out = []
    for n, ln in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not ln.strip():
            continue
        try:
            c = Candidate.from_json(ln)
            if not (isinstance(c.text, str) and isinstance(c.project, str)
                    and isinstance(c.session_id, str) and isinstance(c.context, str)
                    and (c.kind is None or isinstance(c.kind, str))
                    and isinstance(c.kind_prob, (int, float))):
                raise TypeError("bad field types")
        except Exception as e:  # json errors, non-objects, missing fields, wrong types
            raise ValueError(f"line {n}: {type(e).__name__}") from e
        out.append(c)
    return out


def can_supersede(new: Record, old: Record) -> bool:
    """A new record may supersede an older one unless the old one is pinned, or is stated
    while the new one is only inferred."""
    if old.pinned:
        return False
    return not (old.confidence == "stated" and new.confidence != "stated")


def _call(fn, *args):
    try:
        return fn(*args)
    except Exception as e:
        raise _JudgeFailed() from e


def ingest(store: Store, judge, embedder, th: Thresholds, now: str) -> IngestReport:
    rep = IngestReport()
    files = sorted((store.home / "inbox").glob("*.jsonl"))  # snapshot: later captures wait
    if not files:
        return rep
    vecs = _Vectors(embedder)
    active = [r for r in store.load_all() if r.kind != "project-gist"]
    tomb_texts = store.tombstones()

    def tombstoned(text: str) -> bool:
        if not tomb_texts:
            return False
        vecs.ensure([text] + tomb_texts)
        v = vecs.get(text)
        return any(float(v @ vecs.get(t)) >= th.tombstone_sim for t in tomb_texts)

    def neighbors(c: Candidate) -> list[Record]:
        pool = [r for r in active if r.scope != "project" or r.project == c.project]
        if not pool:
            return []
        vecs.ensure([c.text] + [r.body for r in pool])
        v = vecs.get(c.text)
        scored = [(float(v @ vecs.get(r.body)), r) for r in pool]
        scored = [(s, r) for s, r in scored if s >= th.neighbor_sim]
        scored.sort(key=lambda x: -x[0])
        same_project = [r for s, r in scored if r.scope == "project"]
        promoted = [r for s, r in scored if r.scope != "project"]
        return (same_project + promoted)[: th.neighbor_k]

    def touch(r: Record) -> None:
        if r.id not in rep.touched:
            rep.touched.append(r.id)
        rep.projects.add(r.project)

    def merge(target: Record, c: Candidate, cand_id: str) -> None:
        if c.session_id not in target.sources:
            target.sources = [*target.sources, c.session_id]
        if not target.pinned:
            target.last_seen = now
            target.stale = False
        store.save(target)
        rep.merged.append((cand_id, target.id))
        touch(target)

    def process(c: Candidate) -> None:
        kind = c.kind
        if kind == "none":
            rep.skipped += 1
            return
        if tombstoned(c.text):
            rep.skipped += 1
            return
        if kind is None:
            v = _call(judge.classify_window, c.text, c.context)
            if v.kind == "none" or v.kind_prob < th.window_min_prob \
                    or v.sensitive_prob >= th.sensitive_drop:
                rep.skipped += 1
                return
            kind = v.kind
        cand_id = new_id(c.text, c.project, "project")
        by_id = next((r for r in active
                      if r.id == cand_id and r.scope == "project"), None)
        if by_id is not None:  # same text already stored (e.g. rerun after a partial failure)
            merge(by_id, c, cand_id)
            return
        nbs = neighbors(c)
        for nb in nbs:
            if _call(judge.same_fact, c.text, nb.body) >= th.same_fact:
                merge(nb, c, cand_id)
                return
        contradicted = [nb for nb in nbs
                        if _call(judge.contradicts, c.text, nb.body) >= th.contradiction]
        # the text is what the user typed, so the record is always 'stated'
        r = Record(cand_id, kind, "project", c.project, [], [c.session_id],
                   "stated", "active", now, now, c.text)
        if any(not can_supersede(r, nb) for nb in contradicted):
            rep.skipped += 1
            return
        for nb in contradicted:
            nb.status = "superseded"
            store.save(nb)
            active.remove(nb)
            rep.superseded.append(nb.id)
        store.save(r)
        active.append(r)
        vecs.ensure([r.body])
        rep.created.append(r.id)
        touch(r)

    for path in files:
        try:
            cands = _read_candidates(path)
        except ValueError as e:
            log_line(store.home, "consolidate.log",
                     f"inbox file {path.name} rejected (malformed): {e}")
            try:
                _move_to(store, path, "rejected")
            except OSError as e2:
                log_line(store.home, "consolidate.log",
                         f"could not move {path.name} to inbox/rejected", e2)
            continue
        except (OSError, UnicodeDecodeError) as e:
            log_line(store.home, "consolidate.log", f"inbox file {path.name} unreadable", e)
            continue
        try:
            for c in cands:
                process(c)
        except _JudgeFailed:
            rep.jev_failed = True
            break  # keep this file (and the rest) for the next run
        try:
            _move_to(store, path, "processed")
        except OSError as e:
            log_line(store.home, "consolidate.log",
                     f"could not move {path.name} to inbox/processed", e)
    return rep
