"""Persistent local memory.

* Conversation history stays in memory.json in the ORIGINAL format (a JSON list of
  {"role", "content"} messages), so existing data keeps working.
* Explicit facts ("remember that ...") live separately in memories.json and can be
  viewed, added, edited and deleted.
* Anything that looks like a password / API key / token is refused.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from pathlib import Path

log = logging.getLogger("nova.memory")

_SECRET_PATTERNS = [
    re.compile(r"\bpass(word|code|phrase)\b", re.I),
    re.compile(r"\bapi[\s_-]?key\b", re.I),
    re.compile(r"\b(secret|token|pin|cvv|credit card|card number)\b", re.I),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\b[A-Za-z0-9]{32,}\b"),
]


def looks_sensitive(text: str) -> bool:
    return any(p.search(text or "") for p in _SECRET_PATTERNS)


def _load_json_list(path: Path) -> list:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        log.warning("could not read %s; starting empty (file left untouched)", path)
        return []


def _atomic_write(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


class MemoryStore:
    def __init__(self, conversation_file: Path, facts_file: Path, enabled: bool = True) -> None:
        self.conversation_file = conversation_file
        self.facts_file = facts_file
        self.enabled = enabled
        self._lock = threading.RLock()
        self.conversation: list = _load_json_list(conversation_file)
        self.facts: list = _load_json_list(facts_file)

    # ---- conversation -------------------------------------------------
    def add_message(self, role: str, content: str) -> None:
        with self._lock:
            self.conversation.append({"role": role, "content": content})
            if self.enabled:
                self._save_conversation()

    def recent(self, count: int) -> list:
        with self._lock:
            msgs = [m for m in self.conversation if m.get("role") in ("user", "assistant")]
            return msgs[-count:]

    def clear_conversation(self) -> None:
        with self._lock:
            self.conversation = []
            _atomic_write(self.conversation_file, [])

    def _save_conversation(self) -> None:
        try:
            _atomic_write(self.conversation_file, self.conversation)
        except OSError:
            log.exception("could not save conversation")

    # ---- explicit facts -----------------------------------------------
    def add_fact(self, text: str) -> tuple[bool, str]:
        text = (text or "").strip()
        if not text:
            return False, "There was nothing to remember."
        if looks_sensitive(text):
            return False, "That looks like a password or secret, so I won't store it."
        if not self.enabled:
            return False, "Memory is turned off."
        with self._lock:
            self.facts.append({"id": int(time.time() * 1000), "text": text, "created": time.strftime("%Y-%m-%d %H:%M")})
            self._save_facts()
        return True, f"Okay, I'll remember that {text}."

    def edit_fact(self, fact_id: int, text: str) -> bool:
        if looks_sensitive(text):
            return False
        with self._lock:
            for fact in self.facts:
                if fact["id"] == fact_id:
                    fact["text"] = text.strip()
                    self._save_facts()
                    return True
        return False

    def delete_fact(self, fact_id: int) -> bool:
        with self._lock:
            before = len(self.facts)
            self.facts = [f for f in self.facts if f["id"] != fact_id]
            if len(self.facts) != before:
                self._save_facts()
                return True
        return False

    def _save_facts(self) -> None:
        try:
            _atomic_write(self.facts_file, self.facts)
        except OSError:
            log.exception("could not save facts")

    def facts_as_prompt(self) -> str:
        if not self.enabled or not self.facts:
            return ""
        lines = "\n".join(f"- {f['text']}" for f in self.facts[-20:])
        return "Things the user asked you to remember:\n" + lines
