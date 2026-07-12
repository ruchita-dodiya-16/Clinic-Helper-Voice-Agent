from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env")


def env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None or value == "":
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


LIVEKIT_URL = env("LIVEKIT_URL")
LIVEKIT_API_KEY = env("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = env("LIVEKIT_API_SECRET")

ANTHROPIC_API_KEY = env("ANTHROPIC_API_KEY")
CARTESIA_API_KEY = env("CARTESIA_API_KEY")

AGENT_NAME = env("AGENT_NAME", "healthcare-helper-agent")
ANTHROPIC_MODEL = env("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
CARTESIA_STT_MODEL = env("CARTESIA_STT_MODEL", "ink-2")
CARTESIA_TTS_MODEL = env("CARTESIA_TTS_MODEL", "sonic-3")
CARTESIA_TTS_VOICE = env("CARTESIA_TTS_VOICE", "f786b574-daa5-4673-aa0c-cbe3e8534c02")
CARTESIA_TTS_SPEED = float(env("CARTESIA_TTS_SPEED", "0.92"))

APP_HOST = env("APP_HOST", "127.0.0.1")
APP_PORT = int(env("APP_PORT", "8000"))
DB_PATH = str((ROOT_DIR / env("DB_PATH", "./data/healthcare_helper.sqlite3")).resolve())
