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


class SessionRequest(BaseModel):
    participant_name: str = "Patient"
    room_name: str | None = None


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


async def dispatch_agent(room_name: str, participant_name: str) -> None:
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
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/dashboard")
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
        await dispatch_agent(room_name, participant_name)
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
