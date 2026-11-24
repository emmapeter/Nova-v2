"""Configuration. Defaults come from the original main.py; overrides live in config.json.

Secrets (API keys) are never stored in config.json. They are read from environment
variables or a local .env file.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = BASE_DIR / "config.json"
ENV_FILE = BASE_DIR / ".env"


@dataclass
class Settings:
    assistant_name: str = "Nova"
    wake_phrases: list = field(
        default_factory=lambda: ["hey nova", "hey noah", "hey nora", "hanover"]
    )
    sleep_phrases: list = field(
        default_factory=lambda: ["go to sleep", "sleep nova", "stop listening", "end conversation"]
    )
    interrupt_phrases: list = field(default_factory=lambda: ["stop", "be quiet", "quiet", "cancel"])

    # Audio (defaults taken from original main.py)
    sample_rate: int = 44100
    input_device: int | None = 1
    silence_threshold: int = 300
    silence_duration: float = 0.8
    conversation_listen_seconds: float = 10.0
    wake_listen_seconds: float = 4.0

    # Speech
    tts_rate: int = 180
    tts_volume: float = 1.0
    tts_voice_id: str = ""
    vosk_model_path: str = ""  # optional offline speech recognition model folder

    # Intelligence
    local_model: str = "llama3.2:1b"
    online_enabled: bool = True
    online_provider: str = "anthropic"
    online_model: str = "claude-haiku-5-5"
    force_mode: str = "auto"  # auto | offline | online
    max_reply_tokens: int = 80
    history_messages: int = 8

    # Memory / files
    memory_enabled: bool = True
    notes_folder: str = ""  # empty = Documents/Nova Notes
    push_to_talk: bool = False

    @property
    def memory_file(self) -> Path:
        return BASE_DIR / "memory.json"  # original file, same format (list of messages)

    @property
    def facts_file(self) -> Path:
        return BASE_DIR / "memories.json"

    @property
    def log_dir(self) -> Path:
        return BASE_DIR / "logs"

    @property
    def activity_file(self) -> Path:
        return BASE_DIR / "activity.json"


def _read_env_file() -> None:
    if not ENV_FILE.exists():
        return
    try:
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    except OSError:
        pass


def get_api_key() -> str:
    _read_env_file()
    return os.environ.get("NOVA_API_KEY") or os.environ.get("ANTHROPIC_API_KEY") or ""


def load_settings(path: Path = CONFIG_FILE) -> Settings:
    settings = Settings()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for key, value in data.items():
                    if hasattr(settings, key) and not isinstance(getattr(Settings, key, None), property):
                        setattr(settings, key, value)
        except (json.JSONDecodeError, OSError):
            pass
    return settings


def save_settings(settings: Settings, path: Path = CONFIG_FILE) -> None:
    path.write_text(json.dumps(asdict(settings), indent=4), encoding="utf-8")
