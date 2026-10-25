"""Bare-reply detection: replies like "Go", "Yes", "1" carry no fact on their own.

Stdlib only; deterministic.
"""
from __future__ import annotations

import re

CONFIRMATION_WORDS = frozenset("""
yes no y n ok okay sure go apply approve approved proceed continue confirm confirmed done
stop thanks thank you please lgtm yep nope nah agreed correct right fine good great cool
a b c d option all both none first second third last again retry next check run do it that
this same as is ahead sounds
""".split())

_WORD = re.compile(r"[^\W_]+(?:'[^\W_]+)?", re.UNICODE)


def words_of(text: str) -> list[str]:
    """Lowercased word tokens; surrounding/inner punctuation and emoji are dropped."""
    return _WORD.findall((text or "").strip().lower())


def is_bare_reply(text: str) -> bool:
    words = words_of(text)
    if not words or len(words) > 3:
        return False
    if all(w.isdigit() for w in words):
        return True
    return all(w in CONFIRMATION_WORDS for w in words)
