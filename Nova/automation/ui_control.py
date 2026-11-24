"""Keyboard / mouse / window automation.

Prefers the Windows UI Automation API (pywinauto) for buttons and windows, which is far
more stable than coordinates. pyautogui is used only for typing, scrolling and shortcuts.
Both are optional imports so the rest of Nova still runs without them.
"""
from __future__ import annotations

import time

from automation.registry import ToolResult

BLOCKED_COMBOS = {"win+r", "ctrl+alt+delete", "ctrl+shift+esc"}
ALLOWED_KEYS = {
    "ctrl", "alt", "shift", "win", "enter", "esc", "tab", "space", "backspace", "delete",
    "up", "down", "left", "right", "home", "end", "pageup", "pagedown",
    *"abcdefghijklmnopqrstuvwxyz0123456789", *(f"f{i}" for i in range(1, 13)),
}


def _pyautogui():
    try:
        import pyautogui
        pyautogui.FAILSAFE = True  # slam mouse into a corner to abort
        pyautogui.PAUSE = 0.05
        return pyautogui
    except ImportError:
        return None


def type_text(text: str) -> ToolResult:
    pg = _pyautogui()
    if pg is None:
        return ToolResult(False, "Typing needs the pyautogui package. Run: pip install pyautogui")
    time.sleep(0.4)
    pg.write(text, interval=0.01)
    return ToolResult(True, "Typed that for you.")


def press_keys(combo: str) -> ToolResult:
    keys = [k.strip().lower() for k in combo.replace(" ", "").split("+") if k.strip()]
    normalized = "+".join(keys)
    if not keys or any(k not in ALLOWED_KEYS for k in keys):
        return ToolResult(False, "I don't recognise that shortcut.")
    if normalized in BLOCKED_COMBOS:
        return ToolResult(False, "I won't press that shortcut.")
    pg = _pyautogui()
    if pg is None:
        return ToolResult(False, "Shortcuts need the pyautogui package. Run: pip install pyautogui")
    pg.hotkey(*keys)
    return ToolResult(True, f"Pressed {normalized}.")


def scroll(direction: str, amount: int = 5) -> ToolResult:
    pg = _pyautogui()
    if pg is None:
        return ToolResult(False, "Scrolling needs the pyautogui package.")
    pg.scroll(max(1, min(amount, 30)) * (120 if direction == "up" else -120))
    return ToolResult(True, f"Scrolled {direction}.")


def switch_window() -> ToolResult:
    return press_keys("alt+tab")


def _uia_desktop():
    try:
        from pywinauto import Desktop
        return Desktop(backend="uia")
    except ImportError:
        return None


def focus_window(title: str) -> ToolResult:
    desktop = _uia_desktop()
    if desktop is None:
        return ToolResult(False, "Window control needs pywinauto. Run: pip install pywinauto")
    for w in desktop.windows():
        try:
            if title.lower() in w.window_text().lower():
                w.set_focus()
                return ToolResult(True, f"Switched to {w.window_text()}.", verified=True)
        except Exception:
            continue
    return ToolResult(False, f"I couldn't find a window called {title}.")


def click_button(window_title: str, button_text: str) -> ToolResult:
    desktop = _uia_desktop()
    if desktop is None:
        return ToolResult(False, "Button clicking needs pywinauto. Run: pip install pywinauto")
    for w in desktop.windows():
        try:
            if window_title.lower() not in w.window_text().lower():
                continue
            button = w.child_window(title_re=f"(?i).*{button_text}.*", control_type="Button")
            if button.exists(timeout=2):
                button.invoke()
                return ToolResult(True, f"Clicked {button_text}.", verified=True)
        except Exception:
            continue
    return ToolResult(False, f"I couldn't find a {button_text} button in {window_title}.")
