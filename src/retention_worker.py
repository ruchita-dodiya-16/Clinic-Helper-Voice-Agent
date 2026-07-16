from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from livekit import api

from . import repository
from .settings import (
    AGENT_NAME,
    CLINIC_TIMEZONE,
    LIVEKIT_API_KEY,
    LIVEKIT_API_SECRET,
    LIVEKIT_URL,
    RETENTION_INACTIVE_DAYS,
    RETENTION_MAX_ATTEMPTS,
    RETENTION_POLL_MINUTES,
    RETENTION_QUIET_HOURS_END,
    RETENTION_QUIET_HOURS_START,
    RETENTION_RETRY_DAYS,
    ROOT_DIR,
    SIP_OUTBOUND_TRUNK_ID,
)

load_dotenv(ROOT_DIR / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("retention-worker")


def _within_quiet_hours(now_local: datetime) -> bool:
    start_h, start_m = (int(x) for x in RETENTION_QUIET_HOURS_START.split(":"))
    end_h, end_m = (int(x) for x in RETENTION_QUIET_HOURS_END.split(":"))
    start = now_local.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
    end = now_local.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
    return start <= now_local <= end


def _eligible(patient: dict, *, now_utc: datetime) -> bool:
    attempts = int(patient.get("retention_attempts") or 0)
    if attempts >= RETENTION_MAX_ATTEMPTS:
        return False
    last_attempt = patient.get("last_retention_attempt_at") or ""
    if last_attempt:
        try:
            last_dt = datetime.fromisoformat(last_attempt)
        except ValueError:
            last_dt = None
        if last_dt and (now_utc - last_dt) < timedelta(days=RETENTION_RETRY_DAYS):
            return False
    return True


async def _place_call(lkapi: api.LiveKitAPI, patient: dict) -> None:
    phone_number = patient["phone_number"]
    patient_key = patient["patient_key"]
    room_name = f"retention-{uuid.uuid4().hex[:10]}"
    metadata = json.dumps(
        {
            "call_type": "retention_outbound",
            "phone_number": phone_number,
            "patient_key": patient_key,
            "patient_name": patient.get("name", ""),
            "retention_reason": patient.get("retention_reason", ""),
        }
    )
    # Bump the attempt counter before dialing so a slow/failed call can't be
    # double-dialed by the next poll cycle.
    await repository.record_retention_attempt(patient_key)
    await lkapi.agent_dispatch.create_dispatch(
        api.CreateAgentDispatchRequest(agent_name=AGENT_NAME, room=room_name, metadata=metadata)
    )
    log.info("dispatched retention call room=%s patient=%s", room_name, patient_key)


async def run_once() -> None:
    tz = ZoneInfo(CLINIC_TIMEZONE)
    now_local = datetime.now(tz)
    if not _within_quiet_hours(now_local):
        log.info("outside quiet hours (%s), skipping poll", now_local.strftime("%H:%M %Z"))
        return

    if not SIP_OUTBOUND_TRUNK_ID:
        log.warning("SIP_OUTBOUND_TRUNK_ID not configured; skipping retention poll")
        return

    now_utc = datetime.now(tz=ZoneInfo("UTC"))
    today_iso = now_utc.date().isoformat()
    inactive_cutoff_iso = (now_utc - timedelta(days=RETENTION_INACTIVE_DAYS)).isoformat()

    due = await repository.due_retention_patients(
        today_iso=today_iso, inactive_cutoff_iso=inactive_cutoff_iso
    )
    due = [p for p in due if _eligible(p, now_utc=now_utc)]
    if not due:
        log.info("no patients due for retention outreach this cycle")
        return

    log.info("%d patient(s) due for retention outreach", len(due))
    lkapi = api.LiveKitAPI(LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
    try:
        for patient in due:
            try:
                await _place_call(lkapi, patient)
            except Exception:
                log.exception("failed to place retention call for %s", patient.get("patient_key"))
    finally:
        await lkapi.aclose()


async def main() -> None:
    log.info(
        "retention worker started: poll every %d min, quiet hours %s-%s %s",
        RETENTION_POLL_MINUTES,
        RETENTION_QUIET_HOURS_START,
        RETENTION_QUIET_HOURS_END,
        CLINIC_TIMEZONE,
    )
    while True:
        try:
            await run_once()
        except Exception:
            log.exception("retention worker poll failed")
        await asyncio.sleep(RETENTION_POLL_MINUTES * 60)


if __name__ == "__main__":
    asyncio.run(main())
