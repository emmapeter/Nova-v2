"""File and folder tools. Home-folder only, never overwrite, delete = move to Nova trash."""
from __future__ import annotations

import shutil
import time
from pathlib import Path

from automation import apps
from automation.paths import clean_name, ensure_inside_home, resolve_location, unique_path
from automation.registry import ToolResult
from core.config import BASE_DIR

TRASH_DIR = BASE_DIR / "trash"
READABLE = {".txt", ".md", ".py", ".csv", ".json", ".log", ".ini", ".cfg", ".html", ".js", ".yaml", ".yml"}


class FileTools:
    def __init__(self, notes_folder: str = "") -> None:
        self.notes_folder = notes_folder
        self.last_file: Path | None = None  # lets "save it" / "open it" follow-ups work

    def _folder(self, location: str, folder: str = "") -> Path:
        base = resolve_location(location, self.notes_folder)
        if folder:
            base = base / clean_name(folder)
        return ensure_inside_home(base)

    # ---- folders ------------------------------------------------------
    def create_folder(self, name: str, location: str = "desktop") -> ToolResult:
        target = self._folder(location) / clean_name(name, "New Folder")
        ensure_inside_home(target)
        if target.is_dir():
            return ToolResult(True, f"The folder {target.name} already exists in {location}.", verified=True,
                              data={"path": str(target)})
        target.mkdir(parents=True)
        ok = target.is_dir()
        return ToolResult(ok, f"I created the folder {target.name} in {location}." if ok else "I couldn't create that folder.",
                          verified=ok, data={"path": str(target)})

    def open_folder(self, location: str) -> ToolResult:
        path = self._folder(location)
        if not path.is_dir():
            return ToolResult(False, f"I couldn't find the folder {location}.")
        ok = apps.open_path(str(path))
        return ToolResult(ok, f"Opening {location}." if ok else "I couldn't open that folder.", data={"path": str(path)})

    # ---- text files ---------------------------------------------------
    def create_text_file(self, name: str, content: str = "", location: str = "documents",
                         folder: str = "", open_after: bool = False) -> ToolResult:
        directory = self._folder(location, folder)
        directory.mkdir(parents=True, exist_ok=True)
        fname = clean_name(name, "note")
        if "." not in fname:
            fname += ".txt"
        target = unique_path(directory / fname)  # never overwrites
        target.write_text(content, encoding="utf-8")
        verified = target.exists() and target.read_text(encoding="utf-8") == content
        if not verified:
            return ToolResult(False, "I wrote the file but couldn't verify its contents.")
        self.last_file = target
        if open_after:
            apps.open_path(str(target))
        suffix = "" if target.name == fname else " (a file with that name already existed, so I used a new name)"
        return ToolResult(True, f"Saved {target.name} in {target.parent.name}{suffix}.", verified=True,
                          data={"path": str(target)})

    def write_note(self, text: str, location: str = "documents", folder: str = "", name: str = "") -> ToolResult:
        """Write dictated text to a .txt file and open it in Notepad (reliable; no blind typing)."""
        fname = name or f"Note {time.strftime('%Y-%m-%d %H-%M')}"
        result = self.create_text_file(fname, text, location, folder or "Nova Notes", open_after=False)
        if not result.ok:
            return result
        path = result.data["path"]
        if apps.IS_WINDOWS:
            import subprocess
            try:
                subprocess.Popen(["notepad.exe", path])
            except OSError:
                apps.open_path(path)
        else:
            apps.open_path(path)
        return ToolResult(True, f"I opened Notepad and saved your note to {path}.", verified=True,
                          data={"path": path})

    def read_file(self, path: str) -> ToolResult:
        p = ensure_inside_home(Path(path))
        if p.suffix.lower() not in READABLE:
            return ToolResult(False, f"I can't read {p.suffix or 'that'} files yet.")
        if not p.is_file():
            return ToolResult(False, "I couldn't find that file.")
        text = p.read_text(encoding="utf-8", errors="replace")[:6000]
        return ToolResult(True, text, verified=True, data={"path": str(p), "text": text})

    # ---- rename / move / copy / delete ----------------------------------
    def rename_path(self, path: str, new_name: str) -> ToolResult:
        src = ensure_inside_home(Path(path))
        if not src.exists():
            return ToolResult(False, "I couldn't find that file or folder.")
        dst = src.with_name(clean_name(new_name))
        if dst.exists():
            return ToolResult(False, f"{dst.name} already exists, so I didn't rename anything.")
        src.rename(dst)
        ok = dst.exists() and not src.exists()
        return ToolResult(ok, f"Renamed it to {dst.name}." if ok else "The rename didn't work.", verified=ok,
                          data={"path": str(dst)})

    def move_path(self, path: str, destination: str) -> ToolResult:
        return self._transfer(path, destination, move=True)

    def copy_path(self, path: str, destination: str) -> ToolResult:
        return self._transfer(path, destination, move=False)

    def _transfer(self, path: str, destination: str, move: bool) -> ToolResult:
        src = ensure_inside_home(Path(path))
        if not src.exists():
            return ToolResult(False, "I couldn't find that file or folder.")
        dest_dir = ensure_inside_home(resolve_location(destination, self.notes_folder))
        dest_dir.mkdir(parents=True, exist_ok=True)
        dst = dest_dir / src.name
        if dst.exists():
            return ToolResult(False, f"{src.name} already exists there, so I left everything as it was.")
        if src.is_dir():
            shutil.copytree(src, dst)
            if move:
                shutil.rmtree(src)
        else:
            shutil.copy2(src, dst)
            if move:
                src.unlink()
        ok = dst.exists() and (not move or not src.exists())
        verb = "Moved" if move else "Copied"
        return ToolResult(ok, f"{verb} {src.name} to {destination}." if ok else "That didn't work.", verified=ok,
                          data={"path": str(dst)})

    def delete_path(self, path: str) -> ToolResult:
        """Recoverable delete: moves to Nova's trash folder instead of erasing."""
        src = ensure_inside_home(Path(path))
        if not src.exists():
            return ToolResult(False, "I couldn't find that file or folder.")
        TRASH_DIR.mkdir(exist_ok=True)
        dst = unique_path(TRASH_DIR / src.name)
        shutil.move(str(src), str(dst))
        ok = dst.exists() and not src.exists()
        return ToolResult(ok, f"Moved {src.name} to Nova's trash folder." if ok else "That didn't work.",
                          verified=ok, data={"path": str(dst)})

    @staticmethod
    def delete_level_count(path: str) -> int:
        p = Path(path)
        if p.is_dir():
            return sum(1 for _ in p.rglob("*"))
        return 1

    # ---- search ---------------------------------------------------------
    def search_files(self, query: str, location: str = "home") -> ToolResult:
        root = self._folder(location)
        q = query.lower().strip()
        if not q:
            return ToolResult(False, "What should I search for?")
        found = []
        deadline = time.time() + 6
        for p in root.rglob("*"):
            if time.time() > deadline or len(found) >= 8:
                break
            try:
                if q in p.name.lower():
                    found.append(str(p))
            except OSError:
                continue
        if not found:
            return ToolResult(True, f"I didn't find anything matching {query}.", verified=True)
        names = ", ".join(Path(f).name for f in found[:3])
        return ToolResult(True, f"I found {len(found)} match(es), including {names}.", verified=True,
                          data={"paths": found})
