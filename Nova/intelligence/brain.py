"""AI reasoning: online model when available, local Ollama model otherwise.

Nova never pretends to have intelligence it doesn't have: if neither backend is usable
the reply says so plainly.
"""
from __future__ import annotations

import ast
import logging
import operator
import re
from typing import Optional

import requests

from core.config import Settings, get_api_key
from core.memory import MemoryStore

log = logging.getLogger("nova.brain")

SYSTEM_PROMPT = (
    "You are {name}, a fast and friendly personal AI assistant running on the user's laptop. "
    "Speak naturally and conversationally. Give short, direct answers, usually 1 to 3 sentences. "
    "Avoid unnecessary explanations and do not use markdown, lists or emojis because your reply is spoken aloud. "
    "Never claim to have performed a computer action; actions are handled separately."
)

NO_MODEL_MESSAGE = (
    "I can run commands and open things offline, but I don't have a language model available right now. "
    "Start Ollama with a small model, or connect to the internet with an API key, and I can chat."
)

# ---- safe offline calculator --------------------------------------------------------
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.Mod: operator.mod, ast.USub: operator.neg, ast.FloorDiv: operator.floordiv}


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 100:
            raise ValueError("exponent too large")
        return _OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    raise ValueError("unsupported expression")


_WORD_OPS = [(r"\bto the power of\b", "**"), (r"\bsquared\b", "**2"), (r"\bcubed\b", "**3"),
             (r"\bmultiplied by\b|\btimes\b|\bx\b", "*"), (r"\bdivided by\b|\bover\b", "/"),
             (r"\bplus\b", "+"), (r"\bminus\b", "-"), (r"\bmod(?:ulo)?\b", "%")]


def try_math(text: str) -> Optional[str]:
    """Answer simple arithmetic locally and exactly. Returns None if it isn't arithmetic."""
    t = text.lower().strip().rstrip("?.!")
    t = re.sub(r"^(what is|what's|calculate|compute|work out|how much is)\s+", "", t)
    for pattern, repl in _WORD_OPS:
        t = re.sub(pattern, repl, t)
    t = t.replace(",", "").replace("^", "**")
    if not re.fullmatch(r"[\d\s+\-*/().%]+", t) or not re.search(r"\d\s*[-+*/%]", t):
        return None
    try:
        value = _eval(ast.parse(t, mode="eval"))
    except (ValueError, SyntaxError, ZeroDivisionError, OverflowError):
        return None
    if isinstance(value, float):
        value = round(value, 6)
        if value == int(value):
            value = int(value)
    return f"That's {value}."


def clean_reply(text: str) -> str:
    text = re.sub(r"[*#`_]+", "", text or "")
    return re.sub(r"\s+", " ", text).strip()


class Brain:
    def __init__(self, settings: Settings, memory: MemoryStore) -> None:
        self.settings = settings
        self.memory = memory
        self.online = False  # updated by the network monitor
        self._ollama_ok: Optional[bool] = None

    # ---- backend selection -------------------------------------------------
    def has_api_key(self) -> bool:
        return bool(get_api_key())

    def local_available(self) -> bool:
        if self._ollama_ok is None:
            try:
                import ollama
                ollama.list()
                self._ollama_ok = True
            except Exception:
                self._ollama_ok = False
        return bool(self._ollama_ok)

    def recheck_local(self) -> None:
        self._ollama_ok = None

    def active_mode(self) -> str:
        """'online', 'local' or 'none' - exactly what the next reply will use."""
        force = self.settings.force_mode
        can_online = self.settings.online_enabled and self.online and self.has_api_key()
        if force == "online":
            return "online" if can_online else ("local" if self.local_available() else "none")
        if force == "offline":
            return "local" if self.local_available() else "none"
        if can_online:
            return "online"
        return "local" if self.local_available() else "none"

    # ---- chat ----------------------------------------------------------------
    def _messages(self, user_text: str) -> list:
        history = self.memory.recent(self.settings.history_messages)
        history = history + [{"role": "user", "content": user_text}]
        # Online API needs strictly alternating roles beginning with "user".
        cleaned: list = []
        for m in history:
            msg = {"role": m["role"], "content": str(m.get("content", ""))}
            if cleaned and cleaned[-1]["role"] == msg["role"]:
                cleaned[-1]["content"] += " " + msg["content"]
            elif cleaned or msg["role"] == "user":
                cleaned.append(msg)
        return cleaned

    def _system(self) -> str:
        extra = self.memory.facts_as_prompt()
        base = SYSTEM_PROMPT.format(name=self.settings.assistant_name)
        return base + ("\n\n" + extra if extra else "")

    def respond(self, user_text: str) -> tuple[str, str]:
        """Returns (reply, mode_used)."""
        math = try_math(user_text)
        if math:
            return math, "offline-calc"
        mode = self.active_mode()
        if mode == "online":
            try:
                return clean_reply(self._online(self._system(), self._messages(user_text), 300)), "online"
            except Exception as exc:
                log.warning("online model failed (%s); trying local", exc)
                if self.local_available():
                    mode = "local"
                else:
                    return "I couldn't reach the online model and no local model is available.", "none"
        if mode == "local":
            try:
                return clean_reply(self._local(self._system(), self._messages(user_text),
                                               self.settings.max_reply_tokens)), "local"
            except Exception:
                log.exception("local model failed")
                self.recheck_local()
                return "My local model isn't responding. Check that Ollama is running.", "none"
        return NO_MODEL_MESSAGE, "none"

    def complete_json(self, prompt: str) -> Optional[str]:
        """Used by the planner; low-token, no memory. None if no model is available."""
        mode = self.active_mode()
        msgs = [{"role": "user", "content": prompt}]
        try:
            if mode == "online":
                return self._online("You output only JSON.", msgs, 400)
            if mode == "local":
                return self._local("You output only JSON.", msgs, 300)
        except Exception:
            log.exception("complete_json failed")
        return None

    def summarize(self, text: str) -> str:
        prompt = f"Summarize this in 3 short spoken sentences:\n{text[:4000]}"
        reply, _mode = self.respond(prompt) if self.active_mode() != "none" else (NO_MODEL_MESSAGE, "none")
        return reply

    # ---- backends --------------------------------------------------------------
    def _local(self, system: str, messages: list, max_tokens: int) -> str:
        import ollama
        response = ollama.chat(
            model=self.settings.local_model,
            messages=[{"role": "system", "content": system}] + messages,
            keep_alive="10m",
            options={"num_predict": max_tokens, "num_ctx": 2048, "temperature": 0.5},
        )
        return response["message"]["content"]

    def _online(self, system: str, messages: list, max_tokens: int) -> str:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": get_api_key(), "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": self.settings.online_model, "max_tokens": max_tokens, "system": system,
                  "messages": messages},
            timeout=20,
        )
        r.raise_for_status()
        return "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")
