"""Application launching. Names resolve through an allowlist, then installed Start Menu
shortcuts. Nothing the user says is ever passed to a shell."""
from __future__ import annotations

import difflib
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import psutil

from automation.registry import ToolResult

log = logging.getLogger("nova.apps")
IS_WINDOWS = sys.platform == "win32"

# spoken name -> (launch command, process name used for verification)
KNOWN_APPS = {
    "notepad": ("notepad.exe", "notepad.exe"),
    "calculator": ("calc.exe", "calculatorapp.exe"),
    "calc": ("calc.exe", "calculatorapp.exe"),
    "paint": ("mspaint.exe", "mspaint.exe"),
    "file explorer": ("explorer.exe", "explorer.exe"),
    "explorer": ("explorer.exe", "explorer.exe"),
    "task manager": ("taskmgr.exe", "taskmgr.exe"),
    "command prompt": ("cmd.exe", "cmd.exe"),
    "powershell": ("powershell.exe", "powershell.exe"),
    "chrome": ("chrome", "chrome.exe"),
    "google chrome": ("chrome", "chrome.exe"),
    "edge": ("msedge", "msedge.exe"),
    "microsoft edge": ("msedge", "msedge.exe"),
    "firefox": ("firefox", "firefox.exe"),
    "vs code": ("code", "code.exe"),
    "vscode": ("code", "code.exe"),
    "visual studio code": ("code", "code.exe"),
    "word": ("winword", "winword.exe"),
    "excel": ("excel", "excel.exe"),
    "powerpoint": ("powerpnt", "powerpnt.exe"),
}

MUSIC_PLAYER_CANDIDATES = ["spotify", "media player", "groove music", "vlc", "itunes"]

_start_menu_cache: Optional[dict] = None


def normalize(name: str) -> str:
    name = (name or "").lower().strip()
    name = re.sub(r"^(the|my|an?)\s+", "", name)
    name = re.sub(r"\s+(app|application|program)$", "", name)
    return re.sub(r"\s+", " ", name)


def _start_menu_index() -> dict:
    global _start_menu_cache
    if _start_menu_cache is not None:
        return _start_menu_cache
    index: dict = {}
    roots = [
        os.path.expandvars(r"%ProgramData%\Microsoft\Windows\Start Menu\Programs"),
        os.path.expandvars(r"%AppData%\Microsoft\Windows\Start Menu\Programs"),
    ]
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                if f.lower().endswith(".lnk"):
                    index.setdefault(f[:-4].lower(), os.path.join(dirpath, f))
    _start_menu_cache = index
    return index


def find_installed(name: str) -> Optional[str]:
    """Best Start Menu shortcut for a spoken name, or None."""
    index = _start_menu_index()
    key = normalize(name)
    if not key or not index:
        return None
    if key in index:
        return index[key]
    contains = [k for k in index if key in k]
    if contains:
        return index[min(contains, key=len)]
    close = difflib.get_close_matches(key, list(index), n=1, cutoff=0.75)
    return index[close[0]] if close else None


def _process_names() -> set:
    names = set()
    for p in psutil.process_iter(["name"]):
        try:
            names.add((p.info["name"] or "").lower())
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return names


def _wait_for_process(expected: Optional[str], before: set, timeout: float = 5.0) -> Optional[bool]:
    """True = verified running; False = never appeared; None = couldn't tell."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        now = _process_names()
        if expected and expected in now and (expected not in before or expected == "explorer.exe"):
            return True
        if not expected and now - before:
            return True
        time.sleep(0.4)
    return False if expected else None


def _find_executable(command: str) -> Optional[str]:
    found = shutil.which(command)
    if found:
        return found
    if IS_WINDOWS and command == "chrome":
        for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), os.environ.get("LocalAppData")):
            if base:
                p = Path(base) / "Google/Chrome/Application/chrome.exe"
                if p.exists():
                    return str(p)
    return None


def open_path(path: str) -> bool:
    """Open a file/folder with its default app (used for notes, folders, URLs)."""
    try:
        if IS_WINDOWS:
            os.startfile(path)  # noqa: S606 - path is validated by the calling tool
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
        return True
    except OSError:
        log.exception("could not open %s", path)
        return False


def open_application(name: str) -> ToolResult:
    key = normalize(name)
    if not key:
        return ToolResult(False, "Which application should I open?")
    if not IS_WINDOWS:
        return ToolResult(False, "Application launching is implemented for Windows only.")

    before = _process_names()
    candidates = [key] + (MUSIC_PLAYER_CANDIDATES if key in ("music player", "music") else [])
    for cand in candidates:
        cand = normalize(cand)
        entry = KNOWN_APPS.get(cand)
        launched = False
        expected = None
        if entry:
            command, expected = entry
            exe = _find_executable(command) or command
            try:
                subprocess.Popen([exe])
                launched = True
            except OSError:
                launched = False
        if not launched:
            shortcut = find_installed(cand)
            if shortcut and open_path(shortcut):
                launched = True
                expected = None
        if launched:
            state = _wait_for_process(expected, before)
            pretty = name.strip().title()
            if state is True:
                return ToolResult(True, f"Opening {pretty} for you.", verified=True)
            if state is False:
                return ToolResult(False, f"I tried to open {pretty}, but it didn't start.")
            return ToolResult(True, f"I've asked Windows to open {pretty}, but I couldn't confirm it started.")
    return ToolResult(False, f"I couldn't find an application called {name}.")


def close_application(name: str) -> ToolResult:
    key = normalize(name)
    entry = KNOWN_APPS.get(key)
    if not entry:
        return ToolResult(False, f"I only close applications I know how to identify; {name} isn't one of them.")
    target = entry[1]
    closed = 0
    for p in psutil.process_iter(["name"]):
        try:
            if (p.info["name"] or "").lower() == target and target != "explorer.exe":
                p.terminate()
                closed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    if closed:
        return ToolResult(True, f"Closed {name}.", verified=False)
    return ToolResult(False, f"{name} doesn't seem to be running.")
