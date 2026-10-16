"""Ingest: inbox candidates -> classified, deduplicated, merged or superseding records.

Judge and embedder are injected. Processed inbox files move to inbox/processed/.
If the judge raises, the inbox file stays unprocessed and report.jev_failed is set.
"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field

import numpy as np

from .config import Thresholds
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


def _move_processed(store: Store, path) -> None:
    dest_dir = store.home / "inbox" / "processed"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / path.name
    if dest.exists():
        dest = dest_dir / f"{path.stem}.{uuid.uuid4().hex[:6]}{path.suffix}"
    os.replace(path, dest)


def _read_candidates(path) -> tuple[list[Candidate], int]:
    out, bad = [], 0
    for ln in path.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        try:
            out.append(Candidate.from_json(ln))
        except (ValueError, TypeError):
            bad += 1
    return out, bad


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
        if not active:
            return []
        vecs.ensure([c.text] + [r.body for r in active])
        v = vecs.get(c.text)
        scored = [(float(v @ vecs.get(r.body)), r) for r in active]
        scored = [(s, r) for s, r in scored if s >= th.neighbor_sim]
        scored.sort(key=lambda x: -x[0])
        ordered = [r for s, r in scored if r.project == c.project] + \
                  [r for s, r in scored if r.project != c.project]
        return ordered[: th.neighbor_k]

    def touch(r: Record) -> None:
        if r.id not in rep.touched:
            rep.touched.append(r.id)
        rep.projects.add(r.project)

    def process(c: Candidate) -> None:
        kind, prob, stated = c.kind, c.kind_prob, True
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
            kind, prob, stated = v.kind, v.kind_prob, False  # classified late: inferred
        cand_id = new_id(c.text, c.project, "project")
        by_id = next((r for r in active if r.id == cand_id), None)
        if by_id is not None:  # same text already stored (e.g. rerun after a partial failure)
            _merge(by_id, c, cand_id)
            return
        nbs = neighbors(c)
        for nb in nbs:
            if _call(judge.same_fact, c.text, nb.body) >= th.same_fact:
                _merge(nb, c, cand_id)
                return
        contradicted = [nb for nb in nbs
                        if _call(judge.contradicts, c.text, nb.body) >= th.contradiction]
        if stated:
            if any(nb.pinned for nb in contradicted):
                rep.skipped += 1
                return
            for nb in contradicted:
                nb.status = "superseded"
                store.save(nb)
                active.remove(nb)
                rep.superseded.append(nb.id)
        elif any(nb.pinned or nb.confidence == "stated" for nb in contradicted):
            rep.skipped += 1
            return
        r = Record(cand_id, kind, "project", c.project, [], [c.session_id],
                   "stated" if stated else "inferred", "active", now, now, c.text)
        store.save(r)
        active.append(r)
        vecs.ensure([r.body])
        rep.created.append(r.id)
        touch(r)

    def _merge(target: Record, c: Candidate, cand_id: str) -> None:
        if c.session_id not in target.sources:
            target.sources = [*target.sources, c.session_id]
        if not target.pinned:
            target.last_seen = now
            target.stale = False
        store.save(target)
        rep.merged.append((cand_id, target.id))
        touch(target)

    for path in files:
        try:
            cands, bad = _read_candidates(path)
        except (OSError, UnicodeDecodeError):
            continue
        rep.skipped += bad
        try:
            for c in cands:
                process(c)
        except _JudgeFailed:
            rep.jev_failed = True
            break  # keep this file (and the rest) for the next run
        try:
            _move_processed(store, path)
        except OSError:
            pass
    return rep
