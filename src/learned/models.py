"""Plain data classes shared across modules (stdlib only)."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields


@dataclass(frozen=True)
class Window:
    text: str
    context: str
    is_choice_answer: bool
    timestamp: str
    line_no: int


@dataclass
class Candidate:
    text: str
    context: str
    kind: str | None
    kind_prob: float
    project: str
    cwd: str
    session_id: str
    timestamp: str

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, s: str) -> "Candidate":
        d = json.loads(s)
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})


@dataclass(frozen=True)
class WindowVerdict:
    kind: str
    kind_prob: float
    sensitive_prob: float


@dataclass(frozen=True)
class TagVerdict:
    tag: str | None
    probabilities: dict[str, float]


@dataclass(frozen=True)
class PromotionVerdict:
    personal: float
    same: float
    stable: float
