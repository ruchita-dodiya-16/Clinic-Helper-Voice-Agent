from __future__ import annotations

import json
import re
import uuid
from datetime import timedelta

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from livekit import api

from . import repository
from .settings import (
    AGENT_NAME,
    LIVEKIT_API_KEY,
    LIVEKIT_API_SECRET,
    LIVEKIT_URL,
    ROOT_DIR,
    SIP_OUTBOUND_TRUNK_ID,
    TEST_PATIENT_PHONE,
)

app = FastAPI(title="Healthcare Helper Mic Demo")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = ROOT_DIR / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# In-memory registry of outbound calls started from the dashboard, keyed by
# room_name. Lets the dashboard poll /api/call-status to show a live "IN CALL"
# state that clears only when the call actually ends (not on a fixed timer).
ACTIVE_CALLS: dict[str, dict] = {}


class SessionRequest(BaseModel):
    participant_name: str = "Patient"
    room_name: str | None = None
    patient_type: str = ""  # "new" or "returning" (from the call page selection)


class OutboundCallRequest(BaseModel):
    phone_number: str
    patient_name: str
    patient_key: str | None = None
    retention_reason: str | None = None


def safe_identity(value: str) -> str:
    value = value.strip() or "Patient"
    value = re.sub(r"[^a-zA-Z0-9_-]+", "-", value)
    return value[:40] or "Patient"


def create_join_token(room_name: str, participant_name: str) -> str:
    return (
        api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        .with_identity(safe_identity(participant_name))
        .with_name(participant_name.strip() or "Patient")
        .with_ttl(timedelta(hours=2))
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
            )
        )
        .to_jwt()
    )


async def dispatch_agent(room_name: str, participant_name: str, patient_type: str = "") -> None:
    lkapi = api.LiveKitAPI(LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
    try:
        await lkapi.room.create_room(api.CreateRoomRequest(name=room_name))

        existing = await lkapi.agent_dispatch.list_dispatch(room_name=room_name)
        for dispatch in existing:
            if getattr(dispatch, "agent_name", None) == AGENT_NAME:
                return

        metadata = json.dumps(
            {
                "call_type": "browser_demo",
                "participant_name": participant_name,
                "patient_type": patient_type,
                "source": "browser_microphone_ui",
                "scope": "healthcare_helper_not_doctor",
            }
        )
        await lkapi.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name=AGENT_NAME,
                room=room_name,
                metadata=metadata,
            )
        )
    finally:
        await lkapi.aclose()


@app.get("/")
async def dashboard_page():
    return FileResponse(STATIC_DIR / "dashboard.html")


@app.get("/api/health")
async def health():
    return {"ok": True, "agent_name": AGENT_NAME}


@app.post("/api/session")
async def create_session(req: SessionRequest):
    participant_name = req.participant_name.strip() or "Patient"
    room_name = req.room_name or f"clinic-helper-{uuid.uuid4().hex[:10]}"

    try:
        await dispatch_agent(room_name, participant_name, req.patient_type)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not dispatch LiveKit agent. Is the agent worker running? {exc}",
        ) from exc

    return {
        "livekit_url": LIVEKIT_URL,
        "token": create_join_token(room_name, participant_name),
        "room_name": room_name,
        "participant_name": participant_name,
        "agent_name": AGENT_NAME,
    }


@app.get("/api/dashboard")
async def dashboard():
    return await repository.dashboard_summary()


@app.get("/api/tasks")
async def tasks(case_type: str | None = None):
    return {"tasks": await repository.list_cases(case_type=case_type)}


@app.get("/api/patients")
async def patients():
    return {"patients": await repository.list_patients()}


@app.get("/api/retention-queue")
async def retention_queue():
    return {"retention_queue": await repository.retention_queue()}


@app.get("/api/call-log")
async def call_log():
    return {"calls": await repository.list_call_log()}


@app.get("/api/doctors")
async def doctors():
    return {"doctors": await repository.list_doctors()}


@app.get("/api/appointments")
async def appointments():
    return {"appointments": await repository.list_appointments()}


@app.post("/api/outbound-call")
async def outbound_call(req: OutboundCallRequest):
    """Initiate a real outbound phone call to a patient via the SIP trunk.

    Dispatches the agent with call_type="retention_outbound" so the agent
    actually dials the patient's phone (ctx.add_sip_participant), rather than
    waiting for browser microphone input.
    """
    phone_number = req.phone_number.strip()
    patient_name = req.patient_name.strip()

    if not phone_number or not patient_name:
        raise HTTPException(status_code=400, detail="Phone number and patient name are required")

    if not SIP_OUTBOUND_TRUNK_ID:
        raise HTTPException(
            status_code=500,
            detail="SIP_OUTBOUND_TRUNK_ID is not configured — cannot place outbound phone calls.",
        )

    room_name = f"outbound-{uuid.uuid4().hex[:10]}"
    metadata = json.dumps(
        {
            "call_type": "dashboard_outbound",
            "phone_number": phone_number,
            "patient_key": req.patient_key or phone_number,
            "patient_name": patient_name,
            "retention_reason": req.retention_reason or "check in and see how you're doing",
        }
    )

    lkapi = api.LiveKitAPI(LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
    try:
        await lkapi.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name=AGENT_NAME,
                room=room_name,
                metadata=metadata,
            )
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not dispatch outbound call. Is the agent worker running? {exc}",
        ) from exc
    finally:
        await lkapi.aclose()

    ACTIVE_CALLS[room_name] = {
        "phone_number": phone_number,
        "patient_name": patient_name,
        "patient_key": req.patient_key or phone_number,
        "status": "ringing",
    }

    return {
        "success": True,
        "message": f"Calling {patient_name} at {phone_number}",
        "room_name": room_name,
        "phone_number": phone_number,
        "patient_name": patient_name,
    }


@app.get("/api/config")
async def config():
    """Client-facing config. The patient phone number is sourced from the
    TEST_PATIENT_PHONE env var so it can be changed in one place (.env) and
    reflected everywhere in the UI, independent of what's stored in the DB."""
    return {"test_patient_phone": TEST_PATIENT_PHONE}


@app.get("/api/call-status/{room_name}")
async def call_status(room_name: str):
    """Report the live state of an outbound call by inspecting the LiveKit room.

    Returns raw signals so the client can decide, with a grace period, when the
    call is really over (vs. the room just not being ready yet):
    - room_exists: could we read the room at all
    - has_agent:   the AI agent is in the room
    - sip_active:  the callee's SIP leg is ACTIVE (answered, media flowing)
    - sip_present: the callee's SIP leg exists but may still be ringing
    """
    entry = ACTIVE_CALLS.get(room_name)

    lkapi = api.LiveKitAPI(LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
    try:
        res = await lkapi.room.list_participants(api.ListParticipantsRequest(room=room_name))
        participants = list(res.participants)
        room_exists = True
    except Exception:
        # Room not found — either not created yet, or already torn down.
        room_exists = False
        participants = []
    finally:
        await lkapi.aclose()

    def _is_sip(p) -> bool:
        return (
            getattr(p, "kind", None) == api.ParticipantInfo.Kind.SIP
            or str(getattr(p, "identity", "")).startswith(("retention-", "dashboard-"))
        )

    has_agent = any(getattr(p, "kind", None) == api.ParticipantInfo.Kind.AGENT for p in participants)
    sip_parts = [p for p in participants if _is_sip(p)]
    sip_present = len(sip_parts) > 0
    sip_active = any(
        getattr(p, "state", None) == api.ParticipantInfo.State.ACTIVE for p in sip_parts
    )

    if entry is not None:
        if sip_active:
            entry["status"] = "in_call"
        elif has_agent:
            entry["status"] = "ringing"

    return {
        "known": entry is not None,
        "room_exists": room_exists,
        "has_agent": has_agent,
        "sip_present": sip_present,
        "sip_active": sip_active,
    }
