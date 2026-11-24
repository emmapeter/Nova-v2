import json

import pytest

from automation.registry import Param, Tool, ToolRegistry, ToolResult
from core.memory import MemoryStore, looks_sensitive
from core.permissions import Level, PermissionManager, interpret_answer
from intelligence.brain import try_math
from voice.wake import is_interrupt, match_wake


# ---------------- permissions ----------------
@pytest.mark.parametrize("text,expected", [
    ("yes", True), ("Yeah, go ahead", True), ("no", False), ("yes no", False),
    ("", False), ("maybe", False), ("don't", False),
])
def test_interpret_answer(text, expected):
    assert interpret_answer(text) is expected


def test_permissions_fail_closed_without_callback():
    pm = PermissionManager()
    assert pm.authorize("x", Level.ORDINARY, "d") is True
    assert pm.authorize("x", Level.SENSITIVE, "d") is False
    assert pm.authorize("x", Level.CRITICAL, "d") is False


def test_permissions_callback_exception_denies():
    def boom(*_):
        raise RuntimeError("dialog crashed")
    assert PermissionManager(boom).authorize("x", Level.CRITICAL, "d") is False


# ---------------- registry ----------------
def make_registry(confirm=None):
    pm = PermissionManager(confirm)
    reg = ToolRegistry(pm)
    calls = []

    def act(name, count=1):
        calls.append((name, count))
        return ToolResult(True, "ok")

    reg.register(Tool("safe", "safe thing", act, {"name": Param(str), "count": Param(int, False, 1)}))
    reg.register(Tool("risky", "risky thing", act, {"name": Param(str)}, level=Level.SENSITIVE))
    reg.register(Tool("choice", "choice", act, {"name": Param(str, choices=("a", "b"))}))
    return reg, calls


def test_registry_validates_arguments():
    reg, calls = make_registry()
    assert not reg.call("safe", {}).ok                      # missing
    assert not reg.call("safe", {"name": "x", "evil": 1}).ok  # unknown arg
    assert not reg.call("choice", {"name": "c"}).ok          # not an allowed choice
    assert not reg.call("nope", {}).ok                       # unknown tool
    assert not reg.call("safe", {"name": "a\x00b"}).ok       # null byte
    assert calls == []


def test_registry_runs_valid_call_and_coerces_ints():
    reg, calls = make_registry()
    assert reg.call("safe", {"name": "x", "count": "3"}).ok
    assert calls == [("x", 3)]


def test_sensitive_tool_needs_confirmation():
    reg, calls = make_registry(confirm=lambda d, l: False)
    result = reg.call("risky", {"name": "x"})
    assert not result.ok and result.data.get("denied") and calls == []
    reg2, calls2 = make_registry(confirm=lambda d, l: True)
    assert reg2.call("risky", {"name": "x"}).ok and len(calls2) == 1


def test_tool_exceptions_do_not_crash():
    pm = PermissionManager()
    reg = ToolRegistry(pm)
    reg.register(Tool("bad", "bad", lambda: 1 / 0))
    assert not reg.call("bad", {}).ok


# ---------------- memory ----------------
def test_memory_keeps_original_format_and_data(tmp_path):
    conv = tmp_path / "memory.json"
    original = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    conv.write_text(json.dumps(original), encoding="utf-8")
    mem = MemoryStore(conv, tmp_path / "facts.json")
    assert mem.conversation == original
    mem.add_message("user", "again")
    assert json.loads(conv.read_text(encoding="utf-8"))[-1] == {"role": "user", "content": "again"}


def test_memory_corrupt_file_is_not_clobbered_on_load(tmp_path):
    conv = tmp_path / "memory.json"
    conv.write_text("{not json", encoding="utf-8")
    mem = MemoryStore(conv, tmp_path / "facts.json")
    assert mem.conversation == []
    assert conv.read_text(encoding="utf-8") == "{not json"


def test_memory_facts_crud_and_secret_refusal(tmp_path):
    mem = MemoryStore(tmp_path / "m.json", tmp_path / "f.json")
    ok, _ = mem.add_fact("my favourite colour is blue")
    assert ok and len(mem.facts) == 1
    fid = mem.facts[0]["id"]
    assert mem.edit_fact(fid, "my favourite colour is green")
    assert "green" in mem.facts_as_prompt()
    assert not mem.add_fact("my password is hunter2")[0]
    assert not mem.add_fact("api key sk-abcdefghijklmnopqrstuvwxyz")[0]
    assert mem.delete_fact(fid) and mem.facts == []


def test_memory_disabled_saves_nothing(tmp_path):
    mem = MemoryStore(tmp_path / "m.json", tmp_path / "f.json", enabled=False)
    mem.add_message("user", "secret chat")
    assert not (tmp_path / "m.json").exists()
    assert not mem.add_fact("a fact")[0]


def test_looks_sensitive():
    assert looks_sensitive("my pin is 1234")
    assert not looks_sensitive("I like jazz")


# ---------------- math ----------------
@pytest.mark.parametrize("text,answer", [
    ("what is 12 times 8", "That's 96."), ("calculate 10 / 4", "That's 2.5."),
    ("what's 2 plus 2?", "That's 4."), ("5 squared", None),
])
def test_try_math(text, answer):
    result = try_math(text)
    if answer:
        assert result == answer
    else:
        assert result in (None, "That's 25.")


def test_math_rejects_code_and_huge_powers():
    assert try_math("__import__('os').system('x')") is None
    assert try_math("9 ** 9999999") is None
    assert try_math("hello there") is None


# ---------------- wake words ----------------
@pytest.mark.parametrize("text,expected", [
    ("Hey Nova, open notepad", "open notepad"), ("hey nova", ""), ("hey noah what time is it", "what time is it"),
    ("okay hey nova stop", "stop"), ("what a nice day", None), ("", None),
])
def test_match_wake(text, expected):
    assert match_wake(text, ["hey nova", "hey noah", "hey nora", "hanover"]) == expected


def test_is_interrupt():
    phrases = ["stop", "be quiet"]
    assert is_interrupt("stop", phrases) and is_interrupt("Nova stop!", phrases)
    assert not is_interrupt("please do not stop the music from playing later", phrases)
