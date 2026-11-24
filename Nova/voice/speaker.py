"""Text-to-speech with instant interruption (offline pyttsx3 in a killable child process)."""
from __future__ import annotations

import logging
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from core.config import Settings

log = logging.getLogger("nova.speaker")
WORKER = Path(__file__).with_name("tts_worker.py")


def list_voices() -> list:
    """[(id, name)] of installed offline voices (runs pyttsx3 in-process once)."""
    try:
        import pyttsx3
        engine = pyttsx3.init()
        voices = [(v.id, v.name) for v in engine.getProperty("voices")]
        engine.stop()
        return voices
    except Exception:
        log.exception("could not list voices")
        return []


class Speaker:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self.speaking = False
        self.interrupted = False
        self.last_finished = 0.0

    def speak(self, text: str) -> bool:
        """Blocks until speech ends or is stopped. Returns True if it finished normally."""
        text = (text or "").strip()
        if not text:
            return True
        self.interrupted = False
        try:
            with self._lock:
                self._proc = subprocess.Popen(
                    [sys.executable, str(WORKER), str(self.settings.tts_rate), str(self.settings.tts_volume),
                     self.settings.tts_voice_id],
                    stdin=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                )
                proc = self._proc
                self.speaking = True
            proc.stdin.write(text)
            proc.stdin.close()
            proc.wait()
        except (OSError, ValueError, BrokenPipeError):
            log.exception("speech failed")
            return False
        finally:
            self.speaking = False
            self.last_finished = time.time()
        return not self.interrupted

    def stop(self) -> None:
        """Silence the assistant immediately."""
        with self._lock:
            proc = self._proc
        if proc and proc.poll() is None:
            self.interrupted = True
            try:
                proc.kill()
            except OSError:
                pass
