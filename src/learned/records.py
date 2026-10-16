"""Record dataclass and markdown/frontmatter (de)serialization (stdlib only)."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field


@dataclass
class Record:
    id: str
    kind: str
    scope: str
    project: str
    tags: list
    sources: list
    confidence: str
    status: str
    created: str
    last_seen: str
    body: str
    pinned: bool = False
    stale: bool = False
    promoted_to: str | None = None
    judgments: dict = field(default_factory=dict)


_FM_FIELDS = ("id", "kind", "scope", "project", "tags", "sources", "confidence",
              "status", "created", "last_seen", "pinned", "stale", "promoted_to",
              "judgments")


def new_id(body: str, project: str = "", scope: str = "project") -> str:
    return hashlib.sha1(f"{project}|{scope}|{body}".encode("utf-8")).hexdigest()[:10]


def render_record(r: Record) -> str:
    lines = ["---"]
    for k in _FM_FIELDS:
        lines.append(f"{k}: {json.dumps(getattr(r, k), ensure_ascii=False)}")
    lines.append("---")
    return "\n".join(lines) + "\n" + r.body.strip("\n") + "\n"


def parse_record(text: str) -> Record:
    text = text.lstrip("﻿").replace("\r\n", "\n")
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        raise ValueError("missing frontmatter")
    try:
        end = lines.index("---", 1)
    except ValueError:
        raise ValueError("unterminated frontmatter") from None
    data: dict = {}
    for ln in lines[1:end]:
        if not ln.strip():
            continue
        key, sep, val = ln.partition(":")
        if not sep:
            raise ValueError(f"bad frontmatter line: {ln!r}")
        data[key.strip()] = json.loads(val.strip())
    data["body"] = "\n".join(lines[end + 1:]).strip("\n")
    known = {k: v for k, v in data.items() if k in _FM_FIELDS or k == "body"}
    try:
        return Record(**known)
    except TypeError as e:  # missing required fields
        raise ValueError(f"incomplete record: {e}") from None
