"""Controlled tool registry.

Spoken or model-generated text is NEVER executed as a shell command. The only way to
act on the computer is to call a registered tool with validated arguments, and every
call passes through the permission manager first.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from core.permissions import Level, PermissionManager

log = logging.getLogger("nova.tools")

MAX_STR = 5000


@dataclass
class ToolResult:
    ok: bool
    message: str
    verified: bool = False  # True only when the outcome was actually checked
    data: dict = field(default_factory=dict)


@dataclass
class Param:
    type: type = str
    required: bool = True
    default: Any = None
    choices: Optional[tuple] = None


@dataclass
class Tool:
    name: str
    description: str
    func: Callable[..., ToolResult]
    params: dict = field(default_factory=dict)
    level: Level = Level.ORDINARY
    level_fn: Optional[Callable[[dict], Level]] = None  # dynamic escalation

    def effective_level(self, args: dict) -> Level:
        if self.level_fn:
            try:
                return max(self.level, self.level_fn(args))
            except Exception:
                return Level.CRITICAL
        return self.level


class ValidationError(ValueError):
    pass


def validate_args(tool: Tool, args: dict) -> dict:
    if not isinstance(args, dict):
        raise ValidationError("arguments must be an object")
    unknown = set(args) - set(tool.params)
    if unknown:
        raise ValidationError(f"unknown argument(s): {', '.join(sorted(unknown))}")
    clean = {}
    for name, p in tool.params.items():
        if name not in args or args[name] is None:
            if p.required:
                raise ValidationError(f"missing argument: {name}")
            clean[name] = p.default
            continue
        value = args[name]
        if p.type is int:
            try:
                value = int(value)
            except (TypeError, ValueError):
                raise ValidationError(f"{name} must be a number")
        elif p.type is bool:
            if isinstance(value, str):
                value = value.strip().lower() in ("true", "yes", "1")
            value = bool(value)
        else:
            value = str(value)
            if len(value) > MAX_STR:
                raise ValidationError(f"{name} is too long")
            if "\x00" in value:
                raise ValidationError(f"{name} contains invalid characters")
        if p.choices is not None and value not in p.choices:
            raise ValidationError(f"{name} must be one of {', '.join(map(str, p.choices))}")
        clean[name] = value
    return clean


class ToolRegistry:
    def __init__(self, permissions: PermissionManager) -> None:
        self.permissions = permissions
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool: {tool.name}")
        self._tools[tool.name] = tool

    def names(self) -> list:
        return sorted(self._tools)

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def catalog(self) -> str:
        """Compact tool list for the LLM planner prompt."""
        lines = []
        for t in self._tools.values():
            args = ", ".join(
                f"{n}{'' if p.required else '?'}:{p.type.__name__}" for n, p in t.params.items()
            )
            lines.append(f"{t.name}({args}) - {t.description}")
        return "\n".join(lines)

    def call(self, name: str, args: dict) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(False, f"I don't have a tool called {name}.")
        try:
            clean = validate_args(tool, args or {})
        except ValidationError as exc:
            return ToolResult(False, f"I couldn't run {name}: {exc}.")
        level = tool.effective_level(clean)
        if not self.permissions.authorize(name, level, self._describe(tool, clean)):
            return ToolResult(False, "Okay, I won't do that.", data={"denied": True})
        try:
            return tool.func(**clean)
        except Exception as exc:  # tools must never crash the assistant
            log.exception("tool %s failed", name)
            return ToolResult(False, f"That failed: {exc}")

    @staticmethod
    def _describe(tool: Tool, args: dict) -> str:
        detail = ", ".join(f"{k}={str(v)[:60]}" for k, v in args.items() if v not in (None, ""))
        return f"{tool.description} ({detail})" if detail else tool.description
