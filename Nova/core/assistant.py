"""The orchestrator: connects voice, planner, tools, AI, memory and the UI event bus."""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Callable, Optional

from automation.toolset import build_registry
from core.config import Settings, save_settings
from core.events import EventBus, State
from core.memory import MemoryStore
from core.network import NetworkMonitor
from core.permissions import Level, PermissionManager, interpret_answer
from core.planner import Planner
from intelligence.brain import Brain
from voice.wake import contains_phrase, match_wake

log = logging.getLogger("nova.assistant")

CONVERSATION_IDLE_TIMEOUT = 90  # seconds of silence before returning to wake-word mode
ACTIVITY_LIMIT = 200


class Assistant:
    def __init__(self, settings: Settings, bus: Optional[EventBus] = None, voice: bool = True) -> None:
        self.settings = settings
        self.bus = bus or EventBus()
        self.voice_enabled = voice
        self.state = State.IDLE
        self.running = False
        self.input_mode = "text"  # "voice" or "text": decides how confirmations are asked
        self.ui_confirm: Optional[Callable[[str, Level], bool]] = None

        self.memory = MemoryStore(settings.memory_file, settings.facts_file, settings.memory_enabled)
        self.permissions = PermissionManager(self._confirm)
        self.registry, self.files = build_registry(settings, self.memory, self.permissions)
        self.brain = Brain(settings, self.memory)
        self.planner = Planner(self.registry, self.files, llm_json=self.brain.complete_json,
                               on_step=lambda tool, msg: self._log_activity(tool, msg, True))
        self.network = NetworkMonitor(on_change=self._network_changed)
        self.activity: list = self._load_activity()

        self.speaker = None
        self.listener = None
        self._ptt = threading.Event()
        self._voice_thread: Optional[threading.Thread] = None
        if voice:
            from voice.listener import Listener
            from voice.speaker import Speaker
            self.speaker = Speaker(settings)
            self.listener = Listener(settings)

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self.running = True
        self.network.start()
        self._apply_network(self.network.online)
        if self.voice_enabled:
            self._voice_thread = threading.Thread(target=self._voice_loop, name="nova-voice", daemon=True)
            self._voice_thread.start()

    def shutdown(self) -> None:
        self.running = False
        self.network.stop()
        if self.listener:
            self.listener.stop_flag.set()
        if self.speaker:
            self.speaker.stop()

    # ------------------------------------------------------------------ state / events
    def set_state(self, state: State) -> None:
        self.state = state
        self.bus.publish("state", state)

    def mode_info(self) -> dict:
        stt = "online" if self.network.online else (
            "offline (Vosk)" if self.listener and self.listener.offline_stt_ready() else "unavailable offline")
        return {"online": self.network.online, "ai": self.brain.active_mode(), "stt": stt,
                "model": self.settings.online_model if self.brain.active_mode() == "online" else self.settings.local_model,
                "memory": self.settings.memory_enabled}

    def _apply_network(self, online: bool) -> None:
        self.brain.online = online
        if self.listener:
            self.listener.online = online
        self.bus.publish("mode", self.mode_info())

    def _network_changed(self, online: bool) -> None:
        self._apply_network(online)
        note = ("Internet is back. Online features restored." if online else
                "Internet connection lost. Switching to offline features.")
        if not online and self.listener and not self.listener.offline_stt_ready():
            note += " Offline speech recognition isn't set up, so voice input pauses; you can still type commands."
        self.bus.publish("notice", note)

    # ------------------------------------------------------------------ activity log
    def _load_activity(self) -> list:
        try:
            data = json.loads(self.settings.activity_file.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def _log_activity(self, what: str, result: str, ok: bool) -> None:
        entry = {"time": time.strftime("%H:%M:%S"), "what": what, "result": result[:160], "ok": ok}
        self.activity = (self.activity + [entry])[-ACTIVITY_LIMIT:]
        self.bus.publish("activity", entry)
        if self.settings.memory_enabled:
            try:
                self.settings.activity_file.write_text(json.dumps(self.activity, indent=2), encoding="utf-8")
            except OSError:
                pass

    def frequent_apps(self, top: int = 5) -> list:
        counts: dict = {}
        for e in self.activity:
            if e["what"] == "open_app" and e["ok"]:
                name = re.sub(r"^(opening|i've asked windows to open)\s+", "", e["result"], flags=re.I)
                name = re.sub(r"( for you\.| but .*)$", "", name).strip(" .")
                counts[name] = counts.get(name, 0) + 1
        return sorted(counts, key=counts.get, reverse=True)[:top]

    # ------------------------------------------------------------------ the pipeline
    def handle_text(self, text: str) -> str:
        """Understand one instruction, act on it, return what Nova says. No audio involved."""
        text = (text or "").strip()
        if not text:
            return ""
        self.bus.publish("user", text)
        self.set_state(State.THINKING)
        try:
            reply = self._memory_commands(text)
            if reply is None:
                reply = self._try_actions(text)
            if reply is None:
                reply, mode = self.brain.respond(text)
                self.bus.publish("mode", self.mode_info())
                self._log_activity("chat", f"answered ({mode})", mode != "none")
            self.memory.add_message("user", text)
            self.memory.add_message("assistant", reply)
            return reply
        except Exception:
            log.exception("handle_text failed")
            self.set_state(State.ERROR)
            return "Sorry, something went wrong while processing that."

    def _memory_commands(self, text: str) -> Optional[str]:
        low = text.lower()
        if re.search(r"\b(clear|delete|erase)\b.*\b(conversation|chat)\b.*\bhistory\b|\bclear (my )?history\b", low):
            if self.permissions.authorize("clear_history", Level.SENSITIVE, "Clear the saved conversation history"):
                self.memory.clear_conversation()
                self._log_activity("clear_history", "cleared", True)
                return "Done. I've cleared our conversation history."
            return "Okay, I'll keep the history."
        if re.search(r"\b(turn off|disable)\b.*\bmemory\b", low):
            self.settings.memory_enabled = self.memory.enabled = False
            save_settings(self.settings)
            self.bus.publish("mode", self.mode_info())
            return "Memory is off. I won't save new conversations or facts."
        if re.search(r"\b(turn on|enable)\b.*\bmemory\b", low):
            self.settings.memory_enabled = self.memory.enabled = True
            save_settings(self.settings)
            self.bus.publish("mode", self.mode_info())
            return "Memory is on."
        if re.search(r"\bwhat (do you|did i ask you to) remember\b|\bwhat do you know about me\b", low):
            facts = [f["text"] for f in self.memory.facts[-5:]]
            return ("You asked me to remember: " + "; ".join(facts) + ".") if facts else "I'm not remembering anything yet."
        if re.search(r"\bfrequent(ly used)? (apps|applications)\b|\bwhat apps do i use\b", low):
            apps = self.frequent_apps()
            return ("You open " + ", ".join(apps) + " most.") if apps else "I haven't seen you open anything yet."
        return None

    def _try_actions(self, text: str) -> Optional[str]:
        steps = self.planner.plan(text)
        if not steps:
            return None
        self.set_state(State.EXECUTING)
        outcome = self.planner.execute(steps)
        reply = outcome.summary or ("Done." if outcome.ok else "That didn't work.")
        if outcome.ok and self.planner.wants_summary:
            page = next((r.data.get("text") for r in outcome.results if r.data.get("text")), None)
            if page:
                self.set_state(State.THINKING)
                reply = self.brain.summarize(page)
        for step, result in zip(steps, outcome.results):
            self._log_activity(step.tool, result.message, result.ok)
        self.set_state(State.SUCCESS if outcome.ok else State.ERROR)
        return reply

    # ------------------------------------------------------------------ speaking
    def say(self, text: str) -> None:
        if not text:
            return
        self.bus.publish("assistant", text)
        if not (self.voice_enabled and self.speaker):
            return
        from voice.listener import InterruptMonitor
        monitor = InterruptMonitor(self.listener, self.settings, self.speaker.stop)
        self.set_state(State.SPEAKING)
        monitor.start()
        try:
            self.speaker.speak(text)
        finally:
            monitor.stop()
            time.sleep(0.4)  # let the room go quiet so Nova never hears her own voice
        self.set_state(State.IDLE)

    def stop_speaking(self) -> None:
        if self.speaker:
            self.speaker.stop()

    def process_and_say(self, text: str) -> None:
        reply = self.handle_text(text)
        self.say(reply)
        if self.state in (State.SUCCESS, State.ERROR):
            time.sleep(0.6 if not self.voice_enabled else 0)
            self.set_state(State.IDLE)

    def submit_text(self, text: str) -> None:
        """Typed input from the UI (runs on a worker thread so the window never freezes)."""
        def work():
            self.input_mode = "text"
            self.process_and_say(text)
        threading.Thread(target=work, name="nova-text", daemon=True).start()

    # ------------------------------------------------------------------ confirmation
    def _confirm(self, description: str, level: Level) -> bool:
        prefix = "This is a serious action. " if level == Level.CRITICAL else ""
        if self.input_mode == "voice" and self.voice_enabled and self.listener:
            self.say(f"{prefix}{description}. Should I go ahead? Say yes or no.")
            self.set_state(State.LISTENING)
            answer = self.listener.listen(6.0, level_cb=lambda v: self.bus.publish("level", v))
            self.bus.publish("user", answer or "(no answer)")
            return interpret_answer(answer)
        if self.ui_confirm:
            return bool(self.ui_confirm(f"{prefix}{description}", level))
        return False  # fail closed

    # ------------------------------------------------------------------ voice loop
    def push_to_talk(self) -> None:
        self._ptt.set()
        if self.listener:
            self.listener.stop_flag.set()  # break out of the wake-word wait immediately

    def _level(self, value: float) -> None:
        self.bus.publish("level", value)

    def _wake(self) -> Optional[str]:
        """Block until the wake phrase (or push-to-talk). Returns the command spoken with it, '' or None."""
        self.set_state(State.IDLE)
        failures = 0
        while self.running:
            if self._ptt.is_set():
                self._ptt.clear()
                self.listener.stop_flag.clear()
                return ""
            if self.settings.push_to_talk:
                time.sleep(0.2)
                continue
            try:
                self.listener.stop_flag.clear() if not self._ptt.is_set() else None
                text = self.listener.listen(self.settings.wake_listen_seconds, 250, 0.7, self._level)
                failures = 0
            except Exception as exc:
                failures += 1
                log.exception("microphone error")
                self.bus.publish("notice", f"Microphone problem: {exc}")
                self.set_state(State.ERROR)
                time.sleep(min(2 * failures, 10))
                continue
            if not text:
                continue
            command = match_wake(text, self.settings.wake_phrases)
            if command is not None:
                return command
        return None

    def _conversation(self, first_command: str) -> None:
        self.input_mode = "voice"
        last_heard = time.time()
        if first_command == "":
            self.say("Yes?")
        while self.running:
            if first_command:
                text, first_command = first_command, ""
            else:
                self.set_state(State.LISTENING)
                try:
                    text = self.listener.listen(self.settings.conversation_listen_seconds, level_cb=self._level)
                except Exception as exc:
                    log.exception("microphone error")
                    self.bus.publish("notice", f"Microphone problem: {exc}")
                    self.set_state(State.ERROR)
                    time.sleep(2)
                    continue
            if not text:
                if time.time() - last_heard > CONVERSATION_IDLE_TIMEOUT:
                    self.bus.publish("notice", "Going back to sleep after a quiet minute.")
                    return
                continue
            last_heard = time.time()
            self.bus.publish("heard", text)
            if contains_phrase(text, self.settings.sleep_phrases):
                self.say(f"Alright, I'll go to sleep now. Say {self.settings.wake_phrases[0].title()} when you need me.")
                return
            self.process_and_say(text)

    def _voice_loop(self) -> None:
        while self.running:
            first = self._wake()
            if first is None:
                break
            self._conversation(first)
            self.set_state(State.IDLE)
