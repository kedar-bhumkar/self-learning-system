"""Regex secret redaction (stdlib only)."""
from __future__ import annotations

import re

R = "[REDACTED]"

_PATTERNS = [
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{10,}"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"),
    re.compile(r"\b[0-9a-fA-F]{32,}\b"),
]
_ASSIGN = re.compile(
    r"(?i)\b((?:api[_-]?key|secret|token|passwd|password|access[_-]?key)[\w-]*\s*[:=]\s*)(\S+)")
_LONG = re.compile(r"[A-Za-z0-9+/_=-]{40,}")


def _long_secret(m: re.Match) -> str:
    s = m.group(0)
    if re.search(r"\d", s) and re.search(r"[a-z]", s) and re.search(r"[A-Z]", s):
        return R
    return s


def redact(text: str) -> str:
    for p in _PATTERNS:
        text = p.sub(R, text)
    text = _ASSIGN.sub(lambda m: m.group(1) + R, text)
    return _LONG.sub(_long_secret, text)
