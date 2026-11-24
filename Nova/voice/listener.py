"""Microphone input: voice-activity detection (from the original listen()), speech
recognition (Google online, optional Vosk offline) and a barge-in monitor.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

import numpy as np

from core.config import Settings
from voice.wake import is_interrupt

log = logging.getLogger("nova.listener")

CHUNK_SECONDS = 0.1


def resample(samples: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    if src_rate == dst_rate or len(samples) == 0:
        return samples
    n = int(len(samples) * dst_rate / src_rate)
    x_old = np.linspace(0, 1, len(samples), endpoint=False)
    x_new = np.linspace(0, 1, n, endpoint=False)
    return np.interp(x_new, x_old, samples.astype(np.float32)).astype(np.int16)


class Listener:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._device = settings.input_device
        self._vosk_model = None
        self._vosk_failed = False
        self._recognizer = None
        self.online = False  # set from the network monitor
        self.stop_flag = threading.Event()  # set to abort an in-progress listen

    # ---- recording -------------------------------------------------------------
    def _open_stream(self):
        import sounddevice as sd
        try:
            return sd.InputStream(samplerate=self.settings.sample_rate, device=self._device,
                                  channels=1, dtype="int16")
        except Exception as first_error:
            log.warning("input device %s failed (%s); falling back to system default", self._device, first_error)
            self._device = None
            return sd.InputStream(samplerate=self.settings.sample_rate, device=None, channels=1, dtype="int16")

    def record(self, max_duration: float, silence_threshold: Optional[int] = None,
               silence_duration: Optional[float] = None,
               level_cb: Optional[Callable[[float], None]] = None) -> Optional[np.ndarray]:
        """Record one utterance. Returns int16 samples, or None if nobody spoke."""
        threshold = silence_threshold or self.settings.silence_threshold
        silence_len = silence_duration or self.settings.silence_duration
        rate = self.settings.sample_rate
        chunk = int(CHUNK_SECONDS * rate)
        max_chunks = int(max_duration / CHUNK_SECONDS)
        calibration_chunks = 3
        chunks, noise = [], []
        started, silent = False, 0
        with self._open_stream() as stream:
            for index in range(max_chunks):
                if self.stop_flag.is_set():
                    return None
                data, _overflow = stream.read(chunk)
                chunks.append(data.copy())
                volume = float(np.sqrt(np.mean(data.astype(np.float32) ** 2)))
                if level_cb:
                    level_cb(volume)
                if index < calibration_chunks:
                    noise.append(volume)
                    continue
                active = max(threshold, float(np.mean(noise)) * 2.5)
                if volume > active:
                    started, silent = True, 0
                elif started:
                    silent += 1
                    if silent >= int(silence_len / CHUNK_SECONDS):
                        break
        return np.concatenate(chunks) if started else None

    # ---- recognition -------------------------------------------------------------
    def transcribe(self, samples: np.ndarray) -> str:
        if samples is None or len(samples) == 0:
            return ""
        if self.online:
            text = self._google(samples)
            if text is not None:
                return text
        return self._vosk(samples)

    def _google(self, samples: np.ndarray) -> Optional[str]:
        import speech_recognition as sr
        if self._recognizer is None:
            self._recognizer = sr.Recognizer()
        audio = sr.AudioData(samples.tobytes(), self.settings.sample_rate, 2)
        try:
            return self._recognizer.recognize_google(audio).strip()
        except sr.UnknownValueError:
            return ""
        except sr.RequestError:
            log.info("online speech recognition unavailable; trying offline")
            return None

    def _vosk(self, samples: np.ndarray) -> str:
        path = self.settings.vosk_model_path
        if not path or self._vosk_failed:
            return ""
        try:
            if self._vosk_model is None:
                import vosk
                vosk.SetLogLevel(-1)
                self._vosk_model = vosk.Model(path)
            import json
            import vosk
            rec = vosk.KaldiRecognizer(self._vosk_model, 16000)
            pcm = resample(samples.flatten(), self.settings.sample_rate, 16000)
            rec.AcceptWaveform(pcm.tobytes())
            return json.loads(rec.FinalResult()).get("text", "").strip()
        except Exception:
            log.exception("offline recognition failed")
            self._vosk_failed = True
            return ""

    def offline_stt_ready(self) -> bool:
        return bool(self.settings.vosk_model_path) and not self._vosk_failed

    def listen(self, max_duration: float, silence_threshold: Optional[int] = None,
               silence_duration: Optional[float] = None,
               level_cb: Optional[Callable[[float], None]] = None) -> str:
        samples = self.record(max_duration, silence_threshold, silence_duration, level_cb)
        return self.transcribe(samples) if samples is not None else ""


class InterruptMonitor:
    """While Nova speaks, watch the mic for the user saying 'stop' and silence Nova at once.

    Works best with headphones; on open speakers Nova's own voice raises the noise floor,
    so the monitor only reacts to sound clearly louder than that baseline.
    """

    def __init__(self, listener: Listener, settings: Settings, on_interrupt: Callable[[], None]) -> None:
        self.listener, self.settings, self.on_interrupt = listener, settings, on_interrupt
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="nova-interrupt", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.5)

    def _run(self) -> None:
        try:
            rate = self.settings.sample_rate
            chunk = int(CHUNK_SECONDS * rate)
            with self.listener._open_stream() as stream:
                baseline, loud, captured = [], 0, []
                while not self._stop.is_set():
                    data, _ = stream.read(chunk)
                    vol = float(np.sqrt(np.mean(data.astype(np.float32) ** 2)))
                    if len(baseline) < 8:  # first 0.8s: learn how loud Nova herself is
                        baseline.append(vol)
                        continue
                    limit = max(self.settings.silence_threshold * 2, float(np.mean(baseline)) * 2.2)
                    if vol > limit:
                        loud += 1
                        captured.append(data.copy())
                        if loud >= 2 and len(captured) >= 6:  # ~0.6s of clearly louder sound
                            text = self.listener.transcribe(np.concatenate(captured))
                            if is_interrupt(text, self.settings.interrupt_phrases):
                                self.on_interrupt()
                                return
                            loud, captured = 0, []
                    else:
                        if captured and loud:
                            captured.append(data.copy())
                        loud = max(0, loud - 1)
                        if len(captured) > 25:
                            captured = []
        except Exception:
            log.exception("interrupt monitor stopped")
