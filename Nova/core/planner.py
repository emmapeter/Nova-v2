"""Task planner: natural language -> ordered tool steps -> execution with verification.

Two layers:
1. A fast rule-based parser (works fully offline, no model needed).
2. An optional LLM planner (used only if the rules find nothing and a model is available).
The LLM may only choose registered tools; its output is validated like any other call.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import Callable, Optional

from automation.registry import ToolRegistry, ToolResult

log = logging.getLogger("nova.planner")


@dataclass
class Step:
    tool: str
    args: dict = field(default_factory=dict)


@dataclass
class PlanOutcome:
    handled: bool
    messages: list = field(default_factory=list)
    ok: bool = True
    results: list = field(default_factory=list)

    @property
    def summary(self) -> str:
        return " ".join(m for m in self.messages if m)


LOCATIONS = r"(desktop|downloads|documents|pictures|music|videos|home)"
SETTINGS = ("bluetooth", "wifi", "display", "sound", "battery", "update", "privacy")


def _clean(text: str) -> str:
    text = text.strip().strip(".!?").strip()
    return re.sub(r"\s+", " ", text)


_VERBS = (r"(?=(?:open|write|note|save|put|create|make|search|close|type|press|take|click|scroll|switch|"
          r"remember|lock|mute|unmute|set|increase|reduce|lower|check|tell)\b)")


def _split_clauses(text: str) -> list:
    """Split a multi-step instruction at commas / 'then' / 'and' - but only before a new verb."""
    parts = re.split(r"\s*(?:,\s*(?:and\s+)?(?:then\s+)?|;\s*|\band then\s+|\bthen\s+|\s+and\s+)" + _VERBS,
                     text, flags=re.I)
    return [p.strip(" ,") for p in parts if p and p.strip(" ,")]


def parse_clause(raw: str) -> list:
    """Return the steps for ONE clause, or [] if the rules don't recognise it."""
    t = _clean(raw)
    low = t.lower()
    prefix = re.match(r"^(please|can you|could you|would you|nova)\s+", low)
    if prefix:
        low = low[prefix.end():]
        t = t[prefix.end():]

    # --- open X and write/note ...  (must come first: note text may contain "and")
    m = re.match(r"open (?:notepad|a text editor)(?: and)? (?:write(?: down)?|type|note(?: down)?|jot down)"
                 r"(?: this)?(?: note| information| text)?\s*:?\s*(.+)", low)
    if m:
        text_start = t.lower().find(m.group(1))
        return [Step("write_note", {"text": t[text_start:].strip()})]
    m = re.match(r"(?:write|note|jot) down (?:this )?(?:note )?:?\s*(.+)", low)
    if m:
        text_start = t.lower().find(m.group(1))
        return [Step("write_note", {"text": t[text_start:].strip()})]

    # --- create folder (optionally "called X on my desktop")
    m = re.match(rf"(?:create|make) (?:a |new )?folder (?:called|named)? ?(.+?)(?: (?:on|in) (?:my |the )?{LOCATIONS})?$", low)
    if m:
        name_start = t.lower().find(m.group(1))
        name = t[name_start:name_start + len(m.group(1))]
        return [Step("create_folder", {"name": name, "location": m.group(2) or "desktop"})]

    # --- save inside folder: "write down X and save it inside that folder"
    m = re.match(r"(?:save|put) (?:it|that|the note) (?:in|inside) (?:that |the )?folder", low)
    if m:
        return [Step("__save_last_into_last_folder__")]

    # --- remember
    m = re.match(r"remember(?: that)? (.+)", low)
    if m:
        return [Step("remember", {"text": t[t.lower().find(m.group(1)):]})]

    # --- web search
    m = re.match(r"(?:search|google|look up)(?: (?:the web|google|online))?(?: for)? (.+)", low)
    if m:
        return [Step("search_web", {"query": t[t.lower().find(m.group(1)):]})]
    m = re.match(r"(?:go to|open) (?:the )?(?:website |site )?((?:https?://)?[\w-]+(?:\.[\w-]+)+\S*)$", low)
    if m:
        return [Step("open_url", {"url": m.group(1)})]

    # --- settings
    m = re.match(r"open (?:the )?(\w+) settings", low)
    if m and m.group(1) in SETTINGS:
        return [Step("open_settings", {"page": m.group(1)})]
    if re.match(r"open (?:my )?bluetooth", low):
        return [Step("open_settings", {"page": "bluetooth"})]

    # --- folders
    m = re.match(rf"open (?:my |the )?{LOCATIONS}(?: folder)?$", low)
    if m:
        return [Step("open_folder", {"location": m.group(1)})]

    # --- system
    if re.search(r"\b(increase|raise|turn up|louder)\b.*\bvolume\b|\bvolume up\b|\bturn it up\b", low):
        return [Step("volume", {"action": "up"})]
    if re.search(r"\b(reduce|lower|decrease|turn down)\b.*\bvolume\b|\bvolume down\b|\bquieter\b", low):
        return [Step("volume", {"action": "down"})]
    if re.search(r"\bunmute\b", low):
        return [Step("volume", {"action": "unmute"})]
    if re.search(r"\bmute\b", low):
        return [Step("volume", {"action": "mute"})]
    if re.search(r"\b(increase|raise|turn up|more)\b.*\bbrightness\b|\bbrightness up\b", low):
        return [Step("brightness", {"action": "up"})]
    if re.search(r"\b(reduce|lower|decrease|dim|turn down)\b.*\bbrightness\b|\bbrightness down\b", low):
        return [Step("brightness", {"action": "down"})]
    m = re.search(r"brightness (?:to|at) (\d{1,3})", low)
    if m:
        return [Step("brightness", {"action": "set", "level": int(m.group(1))})]
    if "screenshot" in low or "screen shot" in low:
        return [Step("screenshot")]
    if "battery" in low:
        return [Step("battery_status")]
    if re.search(r"\b(storage|disk space|free space)\b", low):
        return [Step("storage_status")]
    if re.search(r"\bwhat(?:'s| is)? the (?:current )?time\b|\bwhat time is it\b|\btell me the time\b|\btoday'?s date\b", low):
        return [Step("current_time")]
    if re.search(r"\bcancel (?:the )?(?:shutdown|restart)\b", low):
        return [Step("cancel_shutdown")]
    if re.search(r"\block (?:my |the )?(?:computer|laptop|pc|screen)\b", low):
        return [Step("lock_computer")]
    if re.search(r"\b(shut ?down|turn off|power off)\b.*\b(computer|laptop|pc)\b|^shut ?down$", low):
        return [Step("shutdown_computer")]
    if re.search(r"\brestart\b.*\b(computer|laptop|pc)\b|^restart$|^reboot$", low):
        return [Step("restart_computer")]

    # --- keyboard / windows
    m = re.match(r"(?:press|hit) ([\w+ ]+)$", low)
    if m:
        return [Step("press_keys", {"combo": m.group(1).replace(" plus ", "+").replace(" ", "+")})]
    m = re.match(r"type (.+)", low)
    if m:
        return [Step("type_text", {"text": t[t.lower().find(m.group(1)):]})]
    if re.match(r"scroll (up|down)", low):
        return [Step("scroll", {"direction": re.match(r"scroll (up|down)", low).group(1)})]
    if low in ("switch window", "switch windows", "next window"):
        return [Step("switch_window")]
    m = re.match(r"(?:switch to|focus|go to) (?:the )?(.+?) window$", low)
    if m:
        return [Step("focus_window", {"title": m.group(1)})]
    m = re.match(r"click (?:the )?(.+?) button(?: in (.+))?$", low)
    if m and m.group(2):
        return [Step("click_button", {"window_title": m.group(2), "button_text": m.group(1)})]

    # --- close / open app (last: generic)
    m = re.match(r"close (?:the )?(.+)", low)
    if m:
        return [Step("close_app", {"name": m.group(1)})]
    m = re.match(r"(?:open|launch|start|run) (?:the )?(.+)", low)
    if m:
        return [Step("open_app", {"name": m.group(1)})]
    return []


def rule_plan(text: str) -> list:
    """Whole utterance -> steps. Returns [] unless EVERY clause was understood."""
    # A note's dictated text is kept intact: don't split inside it.
    first = parse_clause(text)
    if first and first[0].tool == "write_note":
        return first
    steps: list = []
    for clause in _split_clauses(text):
        parsed = parse_clause(clause)
        if not parsed:
            return []
        steps.extend(parsed)
    # "open Notepad" directly before a note is redundant: write_note opens Notepad itself.
    return [st for i, st in enumerate(steps)
            if not (st.tool == "open_app" and st.args.get("name", "").lower() == "notepad"
                    and i + 1 < len(steps) and steps[i + 1].tool == "write_note")]


class Planner:
    def __init__(self, registry: ToolRegistry, files, llm_json: Optional[Callable[[str], Optional[str]]] = None,
                 on_step: Optional[Callable[[str, str], None]] = None) -> None:
        self.registry = registry
        self.files = files
        self.llm_json = llm_json
        self.on_step = on_step
        self.last_folder: Optional[str] = None  # folder created earlier in the CURRENT plan only
        self.wants_summary = False

    def plan(self, text: str) -> list:
        self.wants_summary = bool(re.search(r"summari[sz]e", text, re.I))
        chain = re.search(r"search (?:for )?(.+?)[,]?\s+(?:and |then |and then )?(?:open|read) the first"
                          r"(?: relevant)? (?:result|link)", text, re.I)
        if chain:
            steps = rule_plan(text[:chain.start()].strip(" ,")) if text[:chain.start()].strip(" ,") else []
            if text[:chain.start()].strip(" ,") and not steps:
                return []
            return steps + [Step("web_read", {"query": chain.group(1).strip()})]
        steps = rule_plan(text)
        if steps:
            return steps
        if self.llm_json and self._looks_like_command(text):
            return self._llm_plan(text)
        return []

    @staticmethod
    def _looks_like_command(text: str) -> bool:
        return bool(re.match(r"(?i)\s*(please\s+)?(open|create|make|write|save|move|copy|rename|delete|find|search|"
                             r"close|take|set|turn|launch|start|type|press|click)\b", text))

    def _llm_plan(self, text: str) -> list:
        prompt = (
            "Convert the request into a JSON list of tool calls. Use ONLY these tools:\n"
            f"{self.registry.catalog()}\n"
            'Reply with JSON only, like [{"tool": "open_app", "args": {"name": "notepad"}}]. '
            "If the request is not a computer action, reply [].\n"
            f"Request: {text}"
        )
        try:
            raw = self.llm_json(prompt)
            match = re.search(r"\[.*\]", raw or "", re.S)
            items = json.loads(match.group(0)) if match else []
        except Exception:
            log.exception("LLM planning failed")
            return []
        steps = []
        for item in items[:8]:
            if isinstance(item, dict) and self.registry.get(str(item.get("tool"))):
                steps.append(Step(item["tool"], item.get("args") or {}))
            else:
                return []  # unknown tool -> reject the whole plan
        return steps

    def execute(self, steps: list) -> PlanOutcome:
        outcome = PlanOutcome(handled=True)
        self.last_folder = None
        for step in steps:
            result = self._run_step(step)
            outcome.results.append(result)
            outcome.messages.append(result.message)
            if self.on_step:
                self.on_step(step.tool, result.message)
            if not result.ok:
                outcome.ok = False
                break  # stop the chain; later steps depend on this one
            if step.tool == "create_folder" and result.data.get("path"):
                self.last_folder = result.data["path"]
        if len(steps) > 1 and outcome.ok:
            outcome.messages = [" ".join(outcome.messages)] + []
        return outcome

    def _run_step(self, step: Step) -> ToolResult:
        if step.tool == "__save_last_into_last_folder__":
            return self._save_last_into_folder()
        if step.tool == "write_note" and self.last_folder:
            # a note written right after creating a folder goes inside that folder
            folder = Path(self.last_folder)
            args = dict(step.args)
            args.update({"location": str(folder.parent), "folder": folder.name})
            result = self.registry.call("write_note", args)
            if result.ok and result.data.get("path"):
                self.files.last_file = Path(result.data["path"])
            return result
        return self.registry.call(step.tool, step.args)

    def _save_last_into_folder(self) -> ToolResult:
        if not (self.files.last_file and self.last_folder):
            return ToolResult(False, "I don't have a note and a folder to put together yet.")
        if self.files.last_file.parent == Path(self.last_folder):
            return ToolResult(True, "The note is already saved inside that folder.", verified=True)
        return self.registry.call("move_path", {"path": str(self.files.last_file), "destination": self.last_folder})
