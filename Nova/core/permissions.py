"""Three-tier permission system.

ORDINARY  - runs without asking.
SENSITIVE - needs explicit confirmation.
CRITICAL  - needs a clear confirmation immediately before execution.

A confirm callback is injected by the assistant (voice yes/no or UI dialog). If no
callback is available the answer is always "no" - fail closed.
"""
from __future__ import annotations

import logging
from enum import IntEnum
from typing import Callable, Optional

log = logging.getLogger("nova.permissions")


class Level(IntEnum):
    ORDINARY = 0
    SENSITIVE = 1
    CRITICAL = 2


YES_WORDS = {"yes", "yeah", "yep", "sure", "confirm", "confirmed", "do it", "go ahead", "proceed", "okay", "ok"}
NO_WORDS = {"no", "nope", "cancel", "stop", "don't", "dont", "abort", "negative"}


def interpret_answer(text: str) -> bool:
    """True only for a clear yes. Anything ambiguous is treated as no."""
    words = set((text or "").lower().replace(",", " ").replace(".", " ").replace("!", " ").split())
    if words & NO_WORDS:
        return False
    return bool(words & YES_WORDS)


class PermissionManager:
    def __init__(self, confirm: Optional[Callable[[str, Level], bool]] = None) -> None:
        self._confirm = confirm
        self.audit: list[dict] = []

    def set_confirm(self, confirm: Callable[[str, Level], bool]) -> None:
        self._confirm = confirm

    def authorize(self, tool_name: str, level: Level, description: str) -> bool:
        if level == Level.ORDINARY:
            allowed = True
        elif self._confirm is None:
            allowed = False
        else:
            try:
                allowed = bool(self._confirm(description, level))
            except Exception:  # a failing prompt must never grant permission
                log.exception("confirmation prompt failed")
                allowed = False
        self.audit.append({"tool": tool_name, "level": level.name, "allowed": allowed})
        self.audit = self.audit[-200:]
        log.info("permission %s %s -> %s", level.name, tool_name, allowed)
        return allowed
