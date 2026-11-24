"""Pure text helpers for wake words, sleep and interrupt phrases (no audio, easy to test)."""
from __future__ import annotations

import difflib
import re
from typing import Optional


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", (text or "").lower())).strip()


def match_wake(text: str, phrases: list) -> Optional[str]:
    """If text begins with a wake phrase return the remaining command ('' if none), else None.

    Tolerates small recognition errors ("hey noah" for "hey nova") via fuzzy matching of
    the first words, and also allows a short lead-in such as "okay hey nova".
    """
    cleaned = normalize(text)
    if not cleaned:
        return None
    words = cleaned.split()
    for phrase in phrases:
        p = normalize(phrase)
        n = len(p.split())
        for start in (0, 1):  # allow one filler word before the wake phrase
            head = " ".join(words[start:start + n])
            if not head:
                continue
            if head == p or difflib.SequenceMatcher(None, head, p).ratio() >= 0.88:
                return " ".join(words[start + n:])
    return None


def contains_phrase(text: str, phrases: list) -> bool:
    cleaned = normalize(text)
    return any(normalize(p) in cleaned for p in phrases)


def is_interrupt(text: str, phrases: list) -> bool:
    """Short utterances that are essentially 'stop'."""
    cleaned = normalize(text)
    words = cleaned.split()
    if not words or len(words) > 4:
        return False
    return any(normalize(p) in cleaned for p in phrases)
