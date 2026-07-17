"""One-off manual outbound test call, bypassing the retention scheduler.

Places a single outbound call right now through the configured SIP outbound
trunk, reusing the exact same dispatch path as src/retention_worker.py (so it
exercises memory lookup, AMD, and the outbound greeting end-to-end). Does not
touch the patients table (no attempt counters, no eligibility checks) — this
is for manual testing only, not real retention outreach.

Usage:
    python -m scripts.test_call <phone_number_E164> [patient_name] [reason]

Example:
    python -m scripts.test_call +919664613050 "Ruchita" "a quick test call to check the phone setup"
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid

from livekit import api

from src.settings import (
    AGENT_NAME,
    LIVEKIT_API_KEY,
    LIVEKIT_API_SECRET,
    LIVEKIT_URL,
    SIP_OUTBOUND_TRUNK_ID,
)


async def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(
            "Usage: python -m scripts.test_call <phone_number_E164> [patient_name] [reason]"
        )

    phone_number = sys.argv[1]
    patient_name = sys.argv[2] if len(sys.argv) > 2 else ""
    reason = sys.argv[3] if len(sys.argv) > 3 else "a quick test call to check the phone setup"

    if not SIP_OUTBOUND_TRUNK_ID:
        raise SystemExit(
            "SIP_OUTBOUND_TRUNK_ID is not set in .env — run `python -m scripts.provision_sip` first."
        )

    room_name = f"test-call-{uuid.uuid4().hex[:10]}"
    metadata = json.dumps(
        {
            "call_type": "retention_outbound",
            "phone_number": phone_number,
            "patient_key": phone_number,
            "patient_name": patient_name,
            "retention_reason": reason,
        }
    )

    lkapi = api.LiveKitAPI(LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
    try:
        await lkapi.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(agent_name=AGENT_NAME, room=room_name, metadata=metadata)
        )
        print(f"Dispatched test call to {phone_number} in room {room_name}.")
        print("Watch logs/agent.log for progress (dialing, AMD result, outcome).")
    finally:
        await lkapi.aclose()


if __name__ == "__main__":
    asyncio.run(main())
