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


def optional_env(name: str, default: str = "") -> str:
    return os.getenv(name, default)


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

DATA_DIR = ROOT_DIR / "data"
CLINIC_KB_PATH = DATA_DIR / "clinic_knowledge_base.md"

DATABASE_URL = env(
    "DATABASE_URL", "postgresql+asyncpg://careconnect:careconnect@localhost:5433/careconnect"
)

# --- Telephony (Twilio Elastic SIP Trunk <-> LiveKit SIP) ---
# Filled in by you (Twilio console) + scripts/provision_sip.py (LiveKit side).
TWILIO_PHONE_NUMBER = optional_env("TWILIO_PHONE_NUMBER")
TWILIO_SIP_TERMINATION_URI = optional_env("TWILIO_SIP_TERMINATION_URI")
TWILIO_SIP_USERNAME = optional_env("TWILIO_SIP_USERNAME")
TWILIO_SIP_PASSWORD = optional_env("TWILIO_SIP_PASSWORD")

# Populated by scripts/provision_sip.py after it creates the trunks in LiveKit.
SIP_INBOUND_TRUNK_ID = optional_env("SIP_INBOUND_TRUNK_ID")
SIP_OUTBOUND_TRUNK_ID = optional_env("SIP_OUTBOUND_TRUNK_ID")

# Staff number for live escalation transfer. Empty = transfer is skipped (dashboard-alert only).
STAFF_TRANSFER_NUMBER = optional_env("STAFF_TRANSFER_NUMBER")

# --- Retention / re-engagement scheduler ---
CLINIC_TIMEZONE = optional_env("CLINIC_TIMEZONE", "America/New_York")
RETENTION_POLL_MINUTES = int(optional_env("RETENTION_POLL_MINUTES", "30"))
RETENTION_QUIET_HOURS_START = optional_env("RETENTION_QUIET_HOURS_START", "09:00")
RETENTION_QUIET_HOURS_END = optional_env("RETENTION_QUIET_HOURS_END", "18:00")
RETENTION_MAX_ATTEMPTS = int(optional_env("RETENTION_MAX_ATTEMPTS", "3"))
RETENTION_RETRY_DAYS = int(optional_env("RETENTION_RETRY_DAYS", "2"))
RETENTION_INACTIVE_DAYS = int(optional_env("RETENTION_INACTIVE_DAYS", "180"))

# Test/Demo patient phone number
TEST_PATIENT_PHONE = optional_env("TEST_PATIENT_PHONE", "+919664613050")
