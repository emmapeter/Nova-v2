"""Safe path handling. File tools may only touch the user's own home folder."""
from __future__ import annotations

import os
import re
from pathlib import Path

HOME = Path.home()

_INVALID_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _known_folder(name: str) -> Path:
    for base in (HOME / "OneDrive", HOME):
        candidate = base / name
        if candidate.exists():
            return candidate
    return HOME / name


def resolve_location(location: str, notes_folder: str = "") -> Path:
    key = (location or "").strip().lower().replace("my ", "").replace("the ", "")
    if key in ("", "documents", "document"):
        return _known_folder("Documents")
    mapping = {
        "desktop": "Desktop",
        "downloads": "Downloads",
        "download": "Downloads",
        "pictures": "Pictures",
        "music": "Music",
        "videos": "Videos",
    }
    if key in mapping:
        return _known_folder(mapping[key])
    if key in ("home", "user"):
        return HOME
    if key == "notes" and notes_folder:
        return Path(notes_folder)
    path = Path(os.path.expandvars(os.path.expanduser(location)))
    return path


def ensure_inside_home(path: Path) -> Path:
    resolved = path.resolve()
    home = HOME.resolve()
    if resolved != home and home not in resolved.parents:
        raise PermissionError("I only work inside your user folder.")
    return resolved


def clean_name(name: str, default: str = "untitled") -> str:
    name = _INVALID_NAME.sub("", (name or "").strip()).strip(". ")
    return name[:100] or default


def unique_path(path: Path) -> Path:
    """Never overwrite: add ' (2)', ' (3)'... if the name is taken."""
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for i in range(2, 1000):
        candidate = path.with_name(f"{stem} ({i}){suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError("too many files with that name")
