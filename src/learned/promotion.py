"""Cross-project promotion of facts to topic/global scope (judge and embedder injected)."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass

import numpy as np

from .config import Thresholds
from .logutil import log_line
from .models import PromotionVerdict
from .records import Record, new_id
from .store import Store


@dataclass
class PromotionEvent:
    ids: list[str]
    scope: str
    personal: float
    same: float
    stable: float
    applied: bool
    new_id: str | None = None


def _key(ids: list[str]) -> str:
    return "promotion:" + ",".join(sorted(ids))


def _log(store: Store, now: str, ev: PromotionEvent) -> None:
    line = json.dumps({"ts": now, **asdict(ev)}, ensure_ascii=False)
    try:
        with open(store.home / "promotions.log", "a", encoding="utf-8", newline="\n") as f:
            f.write(line + "\n")
    except OSError as e:
        log_line(store.home, "consolidate.log", "could not append to promotions.log", e)


def _eligible(r: Record | None) -> bool:
    return r is not None and r.status == "active" and not r.pinned and r.scope == "project" \
        and r.kind != "project-gist"


def _apply(store: Store, members: list[Record], scope: str, now: str) -> str:
    stated = [m for m in members if m.confidence == "stated"] or members
    src = max(stated, key=lambda m: m.last_seen)
    nid = new_id(src.body, src.project, scope)
    sources: list = []
    for m in sorted(members, key=lambda m: m.id):
        for s in m.sources:
            if s not in sources:
                sources.append(s)
    tags: list = []
    for m in members:
        for t in m.tags:
            if t not in tags:
                tags.append(t)
    existing = store.get(nid)
    if existing is not None:
        for s in sources:
            if s not in existing.sources:
                existing.sources.append(s)
        for t in tags:
            if t not in existing.tags:
                existing.tags.append(t)
        existing.status, existing.last_seen = "active", now
        new = existing
    else:
        new = Record(nid, src.kind, scope, src.project, tags, sources, src.confidence,
                     "active", min(m.created for m in members), now, src.body)
    store.save(new)
    for m in members:
        m.status, m.promoted_to = "superseded", nid
        store.save(m)
    return nid


def promote(store: Store, judge, embedder, th: Thresholds, touched: list[Record],
            shadow: bool, now: str, failed: list[str] | None = None) -> list[PromotionEvent]:
    """Promote clusters around `touched` records.

    When `failed` is given, a judge error does not raise: the id of the record being judged
    and of all remaining touched records are appended to it (to retry later), the loop stops,
    and the events produced so far are still returned.
    """
    events: list[PromotionEvent] = []
    vec: dict[str, np.ndarray] = {}

    def vecs(records: list[Record]) -> None:
        need = list(dict.fromkeys(r.body for r in records if r.body not in vec))
        if need:
            for t, v in zip(need, embedder.encode(need)):
                vec[t] = np.asarray(v, dtype=float)

    for idx, t0 in enumerate(touched):
        t = store.get(t0.id)
        if not _eligible(t):
            continue
        others = [r for r in store.load_all(scope="project")
                  if r.id != t.id and r.project != t.project and _eligible(r)]
        if not others:
            continue
        vecs([t, *others])
        members = [t] + [r for r in others
                         if float(vec[t.body] @ vec[r.body]) >= th.cluster_sim]
        if len({m.project for m in members}) < 2:
            continue
        ids = sorted(m.id for m in members)
        key = _key(ids)
        cached = next((m.judgments[key] for m in members if key in m.judgments), None)
        if cached is not None:
            if shadow:
                continue  # already judged (and logged when it passed); nothing new in shadow
            v = PromotionVerdict(cached["personal"], cached["same"], cached["stable"])
        else:
            try:
                v = judge.promotion([m.body for m in sorted(members, key=lambda m: m.id)])
            except Exception as e:
                if failed is None:
                    raise
                log_line(store.home, "consolidate.log", "promotion judgment failed", e)
                failed.extend(x.id for x in touched[idx:])
                break
            stored = {"personal": v.personal, "same": v.same, "stable": v.stable}
            for m in members:
                m.judgments[key] = stored
                store.save(m)
        if not (v.same >= th.promote_topic and v.stable >= th.promote_topic):
            continue
        scope = "global" if v.personal >= th.promote_global else "topic"
        ev = PromotionEvent(ids, scope, v.personal, v.same, v.stable, applied=not shadow)
        if not shadow:
            fresh = [store.get(i) for i in ids]
            ev.new_id = _apply(store, [m for m in fresh if m is not None], scope, now)
        _log(store, now, ev)
        events.append(ev)
    return events
