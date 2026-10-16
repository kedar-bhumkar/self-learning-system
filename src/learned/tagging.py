"""Tag reuse and creation (judge and runner injected)."""
from __future__ import annotations

import re
from collections.abc import Callable

from .config import Thresholds
from .logutil import log_line
from .records import Record
from .store import Store

ClaudeRunner = Callable[[str], str]

MAX_VOCAB = 254  # Choice allows 255 options; one is reserved for 'none'
_NAME = re.compile(r"^\s*name\s*:\s*(.+?)\s*$", re.I | re.M)
_DESC = re.compile(r"^\s*description\s*:\s*(.+?)\s*$", re.I | re.M)


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40].strip("-")


def _unique(slug: str, existing: dict) -> str:
    if slug == "none":
        slug = "none-tag"
    out, n = slug, 2
    while out in existing:
        out = f"{slug}-{n}"
        n += 1
    return out


def _create_tag(store: Store, runner: ClaudeRunner, text: str, tags: dict) -> str | None:
    vocab = "\n".join(f"- {k}: {v}" for k, v in tags.items()) or "(none yet)"
    prompt = (
        "Invent a short topic tag for a stored fact, for a tag vocabulary.\n"
        f"Existing tags:\n{vocab}\n\nFact:\n{text}\n\n"
        "Reply with exactly two lines and nothing else:\n"
        "name: <1-3 lowercase words>\n"
        "description: <one short sentence describing what the tag covers>")
    try:
        out = runner(prompt)
    except Exception:
        return None
    m_name, m_desc = _NAME.search(out or ""), _DESC.search(out or "")
    if not m_name or not m_desc:
        return None
    slug = slugify(m_name.group(1))
    desc = " ".join(m_desc.group(1).split())[:200]
    if not slug or not desc:
        return None
    name = _unique(slug, tags)
    store.add_tag(name, desc)
    return name


def assign_tag(store: Store, judge, runner: ClaudeRunner, record: Record,
               th: Thresholds) -> Record:
    """Return `record` with tags set to one tag, or left empty. Does not save the record."""
    tags = store.tags()
    if len(tags) > MAX_VOCAB:
        log_line(store.home, "consolidate.log",
                 f"tagging skipped: vocabulary has {len(tags)} tags (limit {MAX_VOCAB})")
        return record
    if tags:
        v = judge.choose_tag(record.body, tags)
        probs = {k: p for k, p in v.probabilities.items() if k != "none"}
        top = max(probs.values(), default=0.0)
        if v.tag is not None and v.tag in tags and probs.get(v.tag, 0.0) >= th.tag_reuse:
            record.tags = [v.tag]
            return record
        none_p = v.probabilities.get("none", 0.0)
        if not (none_p >= th.new_tag_none and top < th.new_tag_runner_up):
            return record
    name = _create_tag(store, runner, record.body, tags)
    if name:
        record.tags = [name]
    return record
