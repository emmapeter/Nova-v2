"""Connectivity detection in a background thread; never blocks the UI or voice loop."""
from __future__ import annotations

import socket
import threading
import time
from typing import Callable, Optional


def check_internet(timeout: float = 2.0) -> bool:
    for host in (("1.1.1.1", 53), ("8.8.8.8", 53)):
        try:
            with socket.create_connection(host, timeout=timeout):
                return True
        except OSError:
            continue
    return False


class NetworkMonitor:
    def __init__(self, interval: float = 10.0, on_change: Optional[Callable[[bool], None]] = None,
                 checker: Callable[[], bool] = check_internet) -> None:
        self.interval = interval
        self.on_change = on_change
        self._checker = checker
        self.online = False
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self.online = self._checker()
        self._thread = threading.Thread(target=self._run, name="nova-network", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def refresh(self) -> bool:
        self._update(self._checker())
        return self.online

    def _update(self, now_online: bool) -> None:
        if now_online != self.online:
            self.online = now_online
            if self.on_change:
                try:
                    self.on_change(now_online)
                except Exception:
                    pass

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            self._update(self._checker())
