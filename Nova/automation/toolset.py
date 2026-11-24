"""Builds the full tool registry with its permission level for every tool."""
from __future__ import annotations

from automation import apps, system, ui_control, web
from automation.files import FileTools
from automation.registry import Param, Tool, ToolRegistry
from core.config import Settings
from core.memory import MemoryStore
from core.permissions import Level, PermissionManager


def build_registry(settings: Settings, memory: MemoryStore, permissions: PermissionManager) -> tuple[ToolRegistry, FileTools]:
    reg = ToolRegistry(permissions)
    files = FileTools(settings.notes_folder)
    S, I, B = str, int, bool

    def add(name, description, func, params=None, level=Level.ORDINARY, level_fn=None):
        reg.register(Tool(name, description, func, params or {}, level, level_fn))

    # applications
    add("open_app", "open an installed application", apps.open_application, {"name": Param(S)})
    add("close_app", "close a known running application", apps.close_application, {"name": Param(S)},
        level=Level.SENSITIVE)
    add("open_folder", "open a folder (desktop, downloads, documents, music, pictures)", files.open_folder,
        {"location": Param(S)})

    # files
    add("create_folder", "create a folder", files.create_folder,
        {"name": Param(S), "location": Param(S, False, "desktop")})
    add("write_note", "open Notepad and save dictated text as a note file", files.write_note,
        {"text": Param(S), "location": Param(S, False, "documents"), "folder": Param(S, False, ""),
         "name": Param(S, False, "")})
    add("create_text_file", "create a text file with content", files.create_text_file,
        {"name": Param(S), "content": Param(S, False, ""), "location": Param(S, False, "documents"),
         "folder": Param(S, False, ""), "open_after": Param(B, False, False)})
    add("read_file", "read a text file", files.read_file, {"path": Param(S)})
    add("search_files", "search for files by name", files.search_files,
        {"query": Param(S), "location": Param(S, False, "home")})
    add("rename_path", "rename a file or folder", files.rename_path, {"path": Param(S), "new_name": Param(S)})
    add("move_path", "move a file or folder", files.move_path, {"path": Param(S), "destination": Param(S)},
        level=Level.SENSITIVE)
    add("copy_path", "copy a file or folder", files.copy_path, {"path": Param(S), "destination": Param(S)})
    add("delete_path", "delete a file or folder (goes to Nova's trash)", files.delete_path, {"path": Param(S)},
        level=Level.SENSITIVE,
        level_fn=lambda a: Level.CRITICAL if FileTools.delete_level_count(a["path"]) > 50 else Level.SENSITIVE)

    # system
    add("current_time", "tell the current time and date", system.current_time)
    add("battery_status", "check battery percentage", system.battery_status)
    add("storage_status", "check free disk space", system.storage_status, {"drive": Param(S, False, "")})
    add("volume", "change volume", system.volume,
        {"action": Param(S, choices=("up", "down", "mute", "unmute")), "steps": Param(I, False, 5)})
    add("brightness", "change screen brightness", system.brightness,
        {"action": Param(S, choices=("up", "down", "set")), "level": Param(I, False, 50)})
    add("screenshot", "take a screenshot", system.screenshot)
    add("open_settings", "open a Windows settings page", system.open_settings,
        {"page": Param(S, choices=tuple(system.SETTINGS_PAGES))})
    add("lock_computer", "lock the computer", system.lock_computer, level=Level.SENSITIVE)
    add("shutdown_computer", "shut down the computer", system.shutdown_computer, level=Level.CRITICAL)
    add("restart_computer", "restart the computer", system.restart_computer, level=Level.CRITICAL)
    add("cancel_shutdown", "cancel a pending shutdown or restart", system.cancel_shutdown)

    # web
    add("open_url", "open a web address in the browser", web.open_url, {"url": Param(S)})
    add("search_web", "search the web in the browser", web.search_web, {"query": Param(S)})
    add("web_read", "read the first search result's text (internet required)", web.web_read, {"query": Param(S)})

    # keyboard / mouse / windows
    add("type_text", "type text into the focused window", ui_control.type_text, {"text": Param(S)})
    add("press_keys", "press a keyboard shortcut like ctrl+s", ui_control.press_keys, {"combo": Param(S)})
    add("scroll", "scroll up or down", ui_control.scroll,
        {"direction": Param(S, choices=("up", "down")), "amount": Param(I, False, 5)})
    add("switch_window", "switch to the next window", ui_control.switch_window)
    add("focus_window", "bring a window to the front by title", ui_control.focus_window, {"title": Param(S)})
    add("click_button", "click a named button in a window", ui_control.click_button,
        {"window_title": Param(S), "button_text": Param(S)})

    # memory
    def remember(text: str):
        from automation.registry import ToolResult
        ok, msg = memory.add_fact(text)
        return ToolResult(ok, msg, verified=ok)

    add("remember", "remember a fact the user asks to remember", remember, {"text": Param(S)})
    return reg, files
