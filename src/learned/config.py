"""Paths, thresholds and project naming (stdlib only)."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


def learned_home() -> Path:
    env = os.environ.get("LEARNED_HOME")
    if env:
        return Path(env)
    return Path.home() / ".claude" / "learned"


def _norm(p: str) -> str:
    return p.replace("\\", "/").rstrip("/").lower()


_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_DRIVE = re.compile(r"^[a-z]:$")
_RESERVED = {"con", "prn", "aux", "nul",
             *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def project_name(cwd: str) -> str:
    """Safe, lowercased directory name for cwd; 'home' for the home dir, 'root' for a drive root.

    The result is always a single valid Windows path component (never '..', never contains
    separators or ':'), so it cannot escape projects/.
    """
    norm = _norm(cwd or "")
    homes = {_norm(str(Path.home()))}
    for var in ("USERPROFILE", "HOME"):
        if os.environ.get(var):
            homes.add(_norm(os.environ[var]))
    if not norm or norm in homes:
        return "home"
    base = norm.rsplit("/", 1)[-1]
    if _DRIVE.match(base):
        return "root"
    base = _INVALID.sub("_", base).rstrip(". ")
    if not base:
        return "root"
    if base.split(".", 1)[0].rstrip() in _RESERVED:
        base += "_"
    return base


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
