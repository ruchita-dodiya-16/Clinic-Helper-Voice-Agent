"""One-time, idempotent LiveKit SIP provisioning for the Twilio Elastic SIP Trunk.

Run this AFTER completing the Twilio-side setup (buy a number, create an Elastic
SIP Trunk, set its Origination URI to your LiveKit project's SIP inbound address)
and filling in these values in `.env`:

    TWILIO_PHONE_NUMBER=+1...
    TWILIO_SIP_TERMINATION_URI=your-trunk.pstn.twilio.com
    TWILIO_SIP_USERNAME=...
    TWILIO_SIP_PASSWORD=...

Then, from the project root with the venv active:

    python -m scripts.provision_sip

It creates (or reuses, if already present):
  - a SIP inbound trunk accepting calls to TWILIO_PHONE_NUMBER
  - a SIP outbound trunk (used for both retention calls and staff live-transfer)
  - a dispatch rule that routes inbound calls into a room and auto-dispatches
    the agent worker with call_type=inbound_phone metadata

At the end it prints SIP_INBOUND_TRUNK_ID / SIP_OUTBOUND_TRUNK_ID — copy those
into `.env`.
"""

from __future__ import annotations

import asyncio

from livekit import api

from src.settings import (
    AGENT_NAME,
    LIVEKIT_API_KEY,
    LIVEKIT_API_SECRET,
    LIVEKIT_URL,
    TWILIO_PHONE_NUMBER,
    TWILIO_SIP_PASSWORD,
    TWILIO_SIP_TERMINATION_URI,
    TWILIO_SIP_USERNAME,
)

INBOUND_TRUNK_NAME = "healthcare-helper-inbound"
OUTBOUND_TRUNK_NAME = "healthcare-helper-outbound"
DISPATCH_RULE_NAME = "healthcare-helper-inbound-dispatch"


async def main() -> None:
    missing = [
        name
        for name, value in (
            ("TWILIO_PHONE_NUMBER", TWILIO_PHONE_NUMBER),
            ("TWILIO_SIP_TERMINATION_URI", TWILIO_SIP_TERMINATION_URI),
            ("TWILIO_SIP_USERNAME", TWILIO_SIP_USERNAME),
            ("TWILIO_SIP_PASSWORD", TWILIO_SIP_PASSWORD),
        )
        if not value
    ]
    if missing:
        raise SystemExit(
            "Missing Twilio env vars in .env, complete the Twilio console steps first: "
            + ", ".join(missing)
        )

    lkapi = api.LiveKitAPI(LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
    try:
        existing_inbound = await lkapi.sip.list_sip_inbound_trunk(api.ListSIPInboundTrunkRequest())
        inbound_id = next(
            (t.sip_trunk_id for t in existing_inbound.items if t.name == INBOUND_TRUNK_NAME), None
        )
        if inbound_id:
            print(f"Inbound trunk already exists: {inbound_id}")
        else:
            inbound = await lkapi.sip.create_sip_inbound_trunk(
                api.CreateSIPInboundTrunkRequest(
                    trunk=api.SIPInboundTrunkInfo(
                        name=INBOUND_TRUNK_NAME,
                        numbers=[TWILIO_PHONE_NUMBER],
                    )
                )
            )
            inbound_id = inbound.sip_trunk_id
            print(f"Created inbound trunk: {inbound_id}")

        existing_outbound = await lkapi.sip.list_sip_outbound_trunk(
            api.ListSIPOutboundTrunkRequest()
        )
        outbound_id = next(
            (t.sip_trunk_id for t in existing_outbound.items if t.name == OUTBOUND_TRUNK_NAME), None
        )
        if outbound_id:
            print(f"Outbound trunk already exists: {outbound_id}")
        else:
            outbound = await lkapi.sip.create_sip_outbound_trunk(
                api.CreateSIPOutboundTrunkRequest(
                    trunk=api.SIPOutboundTrunkInfo(
                        name=OUTBOUND_TRUNK_NAME,
                        address=TWILIO_SIP_TERMINATION_URI,
                        numbers=[TWILIO_PHONE_NUMBER],
                        auth_username=TWILIO_SIP_USERNAME,
                        auth_password=TWILIO_SIP_PASSWORD,
                    )
                )
            )
            outbound_id = outbound.sip_trunk_id
            print(f"Created outbound trunk: {outbound_id}")

        existing_rules = await lkapi.sip.list_sip_dispatch_rule(api.ListSIPDispatchRuleRequest())
        if any(r.name == DISPATCH_RULE_NAME for r in existing_rules.items):
            print("Inbound dispatch rule already exists, skipping.")
        else:
            await lkapi.sip.create_sip_dispatch_rule(
                api.CreateSIPDispatchRuleRequest(
                    name=DISPATCH_RULE_NAME,
                    trunk_ids=[inbound_id],
                    rule=api.SIPDispatchRule(
                        dispatch_rule_individual=api.SIPDispatchRuleIndividual(
                            room_prefix="call-inbound-"
                        )
                    ),
                    room_config=api.RoomConfiguration(
                        agents=[
                            api.RoomAgentDispatch(
                                agent_name=AGENT_NAME,
                                metadata='{"call_type": "inbound_phone"}',
                            )
                        ]
                    ),
                )
            )
            print("Created inbound dispatch rule.")

        print()
        print("Done. Add/confirm these in your .env:")
        print(f"SIP_INBOUND_TRUNK_ID={inbound_id}")
        print(f"SIP_OUTBOUND_TRUNK_ID={outbound_id}")
    finally:
        await lkapi.aclose()


if __name__ == "__main__":
    asyncio.run(main())
