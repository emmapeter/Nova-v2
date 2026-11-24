"""Planner + file tools + full pipeline, with the real home folder redirected to a temp dir
and application launching stubbed (these tests never open real windows)."""
from pathlib import Path

import pytest

from automation import apps, paths
from automation import files as files_mod
from automation.files import FileTools
from core.config import Settings
from core.planner import rule_plan


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "HOME", tmp_path)
    monkeypatch.setattr(files_mod, "TRASH_DIR", tmp_path / "_trash")
    opened = []
    monkeypatch.setattr(apps, "open_path", lambda p: opened.append(p) or True)
    monkeypatch.setattr(apps, "IS_WINDOWS", False)
    (tmp_path / "Desktop").mkdir()
    (tmp_path / "Documents").mkdir()
    return tmp_path, opened


# ---------------- planner ----------------
@pytest.mark.parametrize("text,tools", [
    ("open notepad", ["open_app"]),
    ("Open Chrome and then open calculator", ["open_app", "open_app"]),
    ("increase my volume", ["volume"]),
    ("reduce my brightness", ["brightness"]),
    ("mute the laptop", ["volume"]),
    ("take a screenshot", ["screenshot"]),
    ("check my battery percentage", ["battery_status"]),
    ("what time is it", ["current_time"]),
    ("open bluetooth settings", ["open_settings"]),
    ("open my downloads folder", ["open_folder"]),
    ("lock my computer", ["lock_computer"]),
    ("shut down my computer", ["shutdown_computer"]),
    ("restart my computer", ["restart_computer"]),
    ("remember that my exam is on friday", ["remember"]),
    ("search for python tutorials", ["search_web"]),
    ("Create a folder called Engineering Notes on my desktop, open Notepad, write down my fluid mechanics "
     "assignment, and save it inside that folder.", ["create_folder", "write_note", "__save_last_into_last_folder__"]),
])
def test_rule_plan_tools(text, tools):
    assert [s.tool for s in rule_plan(text)] == tools


def test_note_text_is_kept_intact_even_with_and_then():
    steps = rule_plan("Open Notepad and write down this note: buy milk and then call mum")
    assert len(steps) == 1 and steps[0].args["text"] == "buy milk and then call mum"


def test_chat_is_not_mistaken_for_a_command():
    assert rule_plan("how are you today") == []
    assert rule_plan("tell me a joke about open source") == []


def test_shutdown_never_taken_from_chat_about_shutdowns():
    assert rule_plan("why does my laptop take so long to shut down") == []


# ---------------- file tools ----------------
def test_create_folder_and_verify(sandbox):
    home, _ = sandbox
    r = FileTools().create_folder("Engineering Notes", "desktop")
    assert r.ok and r.verified and (home / "Desktop" / "Engineering Notes").is_dir()


def test_files_never_overwrite(sandbox):
    home, _ = sandbox
    ft = FileTools()
    a = ft.create_text_file("todo", "first")
    b = ft.create_text_file("todo", "second")
    assert a.ok and b.ok and a.data["path"] != b.data["path"]
    assert Path(a.data["path"]).read_text() == "first"


def test_paths_outside_home_are_refused(sandbox):
    ft = FileTools()
    with pytest.raises(PermissionError):
        ft.read_file("/etc/passwd")
    with pytest.raises(PermissionError):
        ft.delete_path("/usr/bin")


def test_delete_goes_to_trash_not_erased(sandbox):
    home, _ = sandbox
    target = home / "Documents" / "old.txt"
    target.write_text("x")
    r = FileTools().delete_path(str(target))
    assert r.ok and not target.exists() and Path(r.data["path"]).exists()


def test_rename_refuses_to_clobber(sandbox):
    home, _ = sandbox
    (home / "Documents" / "a.txt").write_text("a")
    (home / "Documents" / "b.txt").write_text("b")
    r = FileTools().rename_path(str(home / "Documents" / "a.txt"), "b.txt")
    assert not r.ok and (home / "Documents" / "a.txt").exists()


def test_search_files(sandbox):
    home, _ = sandbox
    (home / "Documents" / "fluid mechanics.txt").write_text("x")
    r = FileTools().search_files("fluid", "documents")
    assert r.ok and len(r.data["paths"]) == 1


# ---------------- full pipeline (no audio, no model) ----------------
@pytest.fixture()
def assistant(sandbox, tmp_path, monkeypatch):
    from core import assistant as assistant_mod
    from core.assistant import Assistant

    class S(Settings):
        pass
    s = Settings()
    monkeypatch.setattr(Settings, "memory_file", property(lambda self: tmp_path / "memory.json"))
    monkeypatch.setattr(Settings, "facts_file", property(lambda self: tmp_path / "facts.json"))
    monkeypatch.setattr(Settings, "activity_file", property(lambda self: tmp_path / "activity.json"))
    a = Assistant(s, voice=False)
    a.brain.online = False
    a.brain._ollama_ok = False  # simulate: no local model
    return a


def test_end_to_end_folder_note_workflow(assistant, sandbox):
    home, _ = sandbox
    reply = assistant.handle_text("Create a folder called Engineering Notes on my desktop, open Notepad, "
                                  "write down my fluid mechanics assignment, and save it inside that folder.")
    folder = home / "Desktop" / "Engineering Notes"
    notes = list((folder / "Engineering Notes").glob("*.txt")) + list(folder.glob("*.txt"))
    assert folder.is_dir()
    assert any("fluid mechanics assignment" in n.read_text() for n in notes), reply
    assert "went wrong" not in reply.lower()


def test_unverified_failure_is_reported_not_faked(assistant):
    reply = assistant.handle_text("open the folder called nonexistentplace")
    assert "couldn't" in reply.lower() or "didn't" in reply.lower() or "only" in reply.lower()


def test_critical_action_denied_without_confirmation(assistant):
    assert "won't" in assistant.handle_text("shut down my computer").lower()


def test_critical_action_runs_only_after_yes(assistant, monkeypatch):
    from automation import system
    ran = []
    monkeypatch.setattr(system, "IS_WINDOWS", True)
    monkeypatch.setattr(system.subprocess, "run", lambda *a, **k: ran.append(a))
    assistant.ui_confirm = lambda desc, level: True
    assistant.input_mode = "text"
    reply = assistant.handle_text("shut down my computer")
    assert ran and "30 seconds" in reply


def test_chat_without_any_model_is_honest(assistant):
    reply = assistant.handle_text("tell me about black holes")
    assert "language model" in reply.lower()


def test_math_works_fully_offline(assistant):
    assert assistant.handle_text("what is 12 times 8") == "That's 96."


def test_remember_and_secret_refusal(assistant):
    assert "remember" in assistant.handle_text("remember that my exam is on friday").lower()
    assert "won't store" in assistant.handle_text("remember that my password is hunter2").lower()
    assert len(assistant.memory.facts) == 1


def test_clear_history_needs_confirmation(assistant):
    assistant.memory.add_message("user", "hello")
    assistant.ui_confirm = lambda d, l: False
    assistant.handle_text("clear my conversation history")
    assert any(m["content"] == "hello" for m in assistant.memory.conversation)
    assistant.ui_confirm = lambda d, l: True
    assistant.handle_text("clear my conversation history")
    assert not any(m["content"] == "hello" for m in assistant.memory.conversation[:1])
