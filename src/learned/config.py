"""Paths, thresholds and project naming (stdlib only)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def learned_home() -> Path:
    env = os.environ.get("LEARNED_HOME")
    if env:
        return Path(env)
    return Path.home() / ".claude" / "learned"


def _norm(p: str) -> str:
    return p.replace("\\", "/").rstrip("/").lower()


def project_name(cwd: str) -> str:
    """Lowercased basename of cwd; 'home' if cwd is the user's home directory."""
    norm = _norm(cwd or "")
    homes = {_norm(str(Path.home()))}
    for var in ("USERPROFILE", "HOME"):
        if os.environ.get(var):
            homes.add(_norm(os.environ[var]))
    if not norm or norm in homes:
        return "home"
    return norm.rsplit("/", 1)[-1] or "home"


@dataclass
class Thresholds:
    window_min_prob: float = 0.6
    sensitive_drop: float = 0.5
    min_user_turns: int = 3
    neighbor_k: int = 8
    neighbor_sim: float = 0.75
    same_fact: float = 0.8
    contradiction: float = 0.8
    tag_reuse: float = 0.6
    new_tag_none: float = 0.7
    new_tag_runner_up: float = 0.25
    cluster_sim: float = 0.8
    promote_topic: float = 0.85
    promote_global: float = 0.90
    tombstone_sim: float = 0.9
    stale_days: int = 90
    core_max_chars: int = 6000
