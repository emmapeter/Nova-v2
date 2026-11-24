"""Assistant states and a tiny thread-safe event bus that the UI subscribes to."""
from __future__ import annotations

import queue
from enum import Enum


class State(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    EXECUTING = "executing"
    SUCCESS = "success"
    ERROR = "error"


class EventBus:
    """Worker threads publish; the UI thread drains. No UI calls from worker threads."""

    def __init__(self) -> None:
        self._queue: queue.Queue = queue.Queue()

    def publish(self, kind: str, payload=None) -> None:
        self._queue.put((kind, payload))

    def drain(self, limit: int = 200):
        items = []
        for _ in range(limit):
            try:
                items.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return items
