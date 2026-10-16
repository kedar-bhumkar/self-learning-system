"""Regex secret redaction (stdlib only)."""
from __future__ import annotations

import re

R = "[REDACTED]"

_PATTERNS = [
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{10,}"),
    re.compile(r"\b[rs]k_(?:live|test)_[0-9A-Za-z]{16,}"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}"),
    re.compile(r"\bnpm_[A-Za-z0-9]{20,}"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}"),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),
    # email: dotted domain whose labels are not all digits, with a 2+ letter TLD
    re.compile(r"[A-Za-z0-9._%+-]+@(?:[A-Za-z0-9-]*[A-Za-z][A-Za-z0-9-]*\.)+[A-Za-z]{2,}\b"),
    re.compile(r"\b[0-9a-fA-F]{32,}\b"),
]
# keyword followed by ':', '=' or 'is' and a value; redacted only if the value looks like a token
_ASSIGN = re.compile(
    r"(?i)\b((?:api[_-]?key|secret|token|passwd|password|pwd|access[_-]?key)[\w-]*"
    r"(?:\s*[:=]\s*|\s+is\s+))(\S+)")
_LONG = re.compile(r"[A-Za-z0-9+/_=-]{40,}")


def _plausible_token(v: str) -> bool:
    v = v.strip("\"'`,;.)(")
    return len(v) >= 8 and bool(re.search(r"[A-Za-z]", v)) and bool(re.search(r"\d", v))


def _assign(m: re.Match) -> str:
    return m.group(1) + R if _plausible_token(m.group(2)) else m.group(0)


def _long_secret(m: re.Match) -> str:
    s = m.group(0)
    if re.search(r"\d", s) and re.search(r"[a-z]", s) and re.search(r"[A-Z]", s):
        return R
    return s


def redact(text: str) -> str:
    for p in _PATTERNS:
        text = p.sub(R, text)
    text = _ASSIGN.sub(_assign, text)
    return _LONG.sub(_long_secret, text)
