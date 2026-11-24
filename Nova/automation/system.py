"""System control tools (Windows-first; each degrades with an honest message elsewhere)."""
from __future__ import annotations

import ctypes
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import psutil

from automation import apps
from automation.paths import resolve_location, ensure_inside_home
from automation.registry import ToolResult

IS_WINDOWS = sys.platform == "win32"

SETTINGS_PAGES = {
    "bluetooth": "ms-settings:bluetooth",
    "wifi": "ms-settings:network-wifi",
    "display": "ms-settings:display",
    "sound": "ms-settings:sound",
    "battery": "ms-settings:batterysaver",
    "update": "ms-settings:windowsupdate",
    "privacy": "ms-settings:privacy",
}

_VK = {"volume_up": 0xAF, "volume_down": 0xAE, "mute": 0xAD}


def _press_vk(code: int, times: int = 1) -> None:
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    for _ in range(times):
        user32.keybd_event(code, 0, 0, 0)
        user32.keybd_event(code, 0, 2, 0)
        time.sleep(0.02)


def current_time() -> ToolResult:
    now = datetime.now()
    return ToolResult(True, f"It's {now.strftime('%I:%M %p').lstrip('0')} on {now.strftime('%A, %B %d')}.", verified=True)


def battery_status() -> ToolResult:
    b = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
    if b is None:
        return ToolResult(False, "I can't read a battery on this computer.")
    plugged = "and charging" if b.power_plugged else "and not charging"
    return ToolResult(True, f"Your battery is at {int(b.percent)} percent {plugged}.", verified=True,
                      data={"percent": b.percent})


def storage_status(drive: str = "") -> ToolResult:
    path = drive or str(Path.home().anchor or "/")
    usage = shutil.disk_usage(path)
    gb = 1024 ** 3
    return ToolResult(True, f"You have {usage.free / gb:.0f} gigabytes free out of {usage.total / gb:.0f}.",
                      verified=True, data={"free_gb": usage.free / gb})


def volume(action: str, steps: int = 5) -> ToolResult:
    if not IS_WINDOWS:
        return ToolResult(False, "Volume control is implemented for Windows only.")
    code = {"up": _VK["volume_up"], "down": _VK["volume_down"], "mute": _VK["mute"], "unmute": _VK["mute"]}[action]
    _press_vk(code, 1 if action in ("mute", "unmute") else max(1, min(steps, 25)))
    words = {"up": "Turned the volume up.", "down": "Turned the volume down.", "mute": "Toggled mute.", "unmute": "Toggled mute."}
    return ToolResult(True, words[action])  # Windows gives no readback through key events


def brightness(action: str, level: int = 50) -> ToolResult:
    if not IS_WINDOWS:
        return ToolResult(False, "Brightness control is implemented for Windows only.")
    get_cmd = "(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness).CurrentBrightness"
    try:
        current = int(subprocess.run(["powershell", "-NoProfile", "-Command", get_cmd], capture_output=True,
                                     text=True, timeout=10).stdout.strip().splitlines()[0])
    except (subprocess.SubprocessError, ValueError, IndexError):
        return ToolResult(False, "This display doesn't allow brightness control from software.")
    target = {"up": current + 20, "down": current - 20, "set": level}[action]
    target = max(5, min(100, int(target)))  # int() guarantees nothing but digits reach PowerShell
    set_cmd = f"(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods).WmiSetBrightness(1,{target})"
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", set_cmd], capture_output=True, timeout=10, check=True)
    except (subprocess.SubprocessError, OSError):
        return ToolResult(False, "I couldn't change the brightness.")
    return ToolResult(True, f"Brightness set to about {target} percent.", verified=False)


def screenshot() -> ToolResult:
    try:
        from PIL import ImageGrab
    except ImportError:
        return ToolResult(False, "Screenshots need the Pillow package. Run: pip install pillow")
    folder = ensure_inside_home(resolve_location("pictures") / "Nova Screenshots")
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"Screenshot {datetime.now().strftime('%Y-%m-%d %H-%M-%S')}.png"
    ImageGrab.grab().save(target)
    ok = target.exists() and target.stat().st_size > 0
    return ToolResult(ok, f"Screenshot saved to {target}." if ok else "The screenshot failed.", verified=ok,
                      data={"path": str(target)})


def open_settings(page: str) -> ToolResult:
    uri = SETTINGS_PAGES.get(page)
    if not uri:
        return ToolResult(False, "I don't know that settings page.")
    if not IS_WINDOWS:
        return ToolResult(False, "Settings pages are available on Windows only.")
    ok = apps.open_path(uri)
    return ToolResult(ok, f"Opening {page} settings." if ok else "I couldn't open that settings page.")


def lock_computer() -> ToolResult:
    if not IS_WINDOWS:
        return ToolResult(False, "Locking is implemented for Windows only.")
    ok = bool(ctypes.windll.user32.LockWorkStation())  # type: ignore[attr-defined]
    return ToolResult(ok, "Locking your computer." if ok else "I couldn't lock the computer.")


def shutdown_computer() -> ToolResult:
    if not IS_WINDOWS:
        return ToolResult(False, "Shutdown is implemented for Windows only.")
    subprocess.run(["shutdown", "/s", "/t", "30"], check=False)
    return ToolResult(True, "Shutting down in 30 seconds. Say cancel shutdown to stop it.")


def restart_computer() -> ToolResult:
    if not IS_WINDOWS:
        return ToolResult(False, "Restart is implemented for Windows only.")
    subprocess.run(["shutdown", "/r", "/t", "30"], check=False)
    return ToolResult(True, "Restarting in 30 seconds. Say cancel shutdown to stop it.")


def cancel_shutdown() -> ToolResult:
    if not IS_WINDOWS:
        return ToolResult(False, "Nothing to cancel on this system.")
    done = subprocess.run(["shutdown", "/a"], capture_output=True).returncode == 0
    return ToolResult(done, "Shutdown cancelled." if done else "There was no shutdown pending.")
