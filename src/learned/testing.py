"""Test doubles: FakeJudge and FakeEmbedder (used by tests only)."""
from __future__ import annotations

import numpy as np

from .models import PromotionVerdict, TagVerdict, WindowVerdict

_DEFAULTS = {
    "classify_window": lambda text, context: WindowVerdict("none", 0.0, 0.0),
    "same_fact": lambda a, b: 0.0,
    "contradicts": lambda a, b: 0.0,
    "choose_tag": lambda text, tags: TagVerdict(None, {}),
    "promotion": lambda texts: PromotionVerdict(0.0, 0.0, 0.0),
    "fits_tag": lambda text, tag, description: 0.0,
}


class FakeJudge:
    """Neutral Judge; each keyword override is a callable replacing the method of that name."""

    def __init__(self, **overrides):
        unknown = set(overrides) - set(_DEFAULTS)
        if unknown:
            raise TypeError(f"unknown Judge methods: {sorted(unknown)}")
        for name, fn in {**_DEFAULTS, **overrides}.items():
            setattr(self, name, fn)


class FakeEmbedder:
    def __init__(self, vectors: dict[str, list[float]]):
        self.vectors = vectors

    def encode(self, texts) -> np.ndarray:
        m = np.array([self.vectors[t] for t in texts], dtype=float)
        norms = np.linalg.norm(m, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return m / norms
