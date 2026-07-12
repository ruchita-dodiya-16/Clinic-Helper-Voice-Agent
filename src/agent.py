from __future__ import annotations

import logging
from typing import Any

from dotenv import load_dotenv
from livekit import agents
from livekit.agents import Agent, AgentServer, AgentSession, RunContext, function_tool
from livekit.plugins import anthropic, cartesia

from . import db
from .settings import (
    AGENT_NAME,
    ANTHROPIC_API_KEY,
    ANTHROPIC_MODEL,
    CARTESIA_API_KEY,
    CARTESIA_STT_MODEL,
    CARTESIA_TTS_MODEL,
    CARTESIA_TTS_SPEED,
    CARTESIA_TTS_VOICE,
    ROOT_DIR,
)

load_dotenv(ROOT_DIR / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("healthcare-helper-agent")

SYSTEM_INSTRUCTIONS = """
You are CareConnect Voice, a healthcare clinic helper. You are not a doctor and you must never replace a clinician.

Your purpose:
- Help with appointment requests, rescheduling, cancellations, callback requests, clinic hours, location, policies, memberships, and general non-clinical service information.
- Encourage follow-up with clinic staff when appropriate.
- Capture staff tasks through tools when the user asks for scheduling, callback, or escalation.

Hard safety rules:
- Do not diagnose, prescribe, recommend medication, provide dosage guidance, interpret symptoms clinically, or give emergency instructions beyond telling the user to contact local emergency services or clinic staff.
- If the user asks about medication, diagnosis, severe symptoms, prescription, dosage, complications, or clinical advice, politely say you cannot provide medical advice and create an escalation task.
- Keep responses short, warm, and natural for voice. Usually one or two sentences.
- Ask only one question at a time.
- Do not mention internal tool names.
- Avoid collecting unnecessary PHI. If details are needed, keep them minimal.

Allowed examples:
- I can help request an appointment. What day or time works best?
- The clinic team can call you back. What is the best callback number?
- I cannot provide medical advice, but I can alert the clinic team to follow up.
"""


class HealthcareHelperAgent(Agent):
    def __init__(self, room_name: str):
        self.room_name = room_name
        super().__init__(instructions=SYSTEM_INSTRUCTIONS)

    @function_tool()
    async def create_appointment_request(
        self,
        context: RunContext,
        preferred_time: str,
        reason: str = "",
        patient_name: str = "",
    ) -> dict[str, Any]:
        """Create an appointment request for staff follow-up.

        Use this when the user wants to book, reschedule, cancel, or confirm an appointment.

        Args:
            preferred_time: The preferred date/time or scheduling window the user mentioned.
            reason: Short non-clinical reason for the appointment if provided.
            patient_name: Patient name if the user voluntarily provided it.
        """
        task = db.add_task(
            room_name=self.room_name,
            task_type="appointment",
            title="Appointment request",
            details={
                "preferred_time": preferred_time,
                "reason": reason,
                "patient_name": patient_name,
            },
        )
        return {"status": "created", "task": task}

    @function_tool()
    async def create_callback_request(
        self,
        context: RunContext,
        callback_reason: str,
        best_time: str = "",
        phone_number: str = "",
        priority: str = "normal",
    ) -> dict[str, Any]:
        """Create a callback request for clinic staff.

        Use this when the user asks for a callback or when the conversation should be handled by staff.

        Args:
            callback_reason: Why staff should call back.
            best_time: Preferred callback time if provided.
            phone_number: Phone number if the user voluntarily provided it.
            priority: normal or urgent. Use urgent for clinical concern or upset user.
        """
        priority = "urgent" if priority.lower() == "urgent" else "normal"
        task = db.add_task(
            room_name=self.room_name,
            task_type="callback",
            title="Callback request",
            details={
                "callback_reason": callback_reason,
                "best_time": best_time,
                "phone_number": phone_number,
            },
            priority=priority,
        )
        return {"status": "created", "task": task}

    @function_tool()
    async def create_escalation(
        self,
        context: RunContext,
        reason: str,
        urgency: str = "urgent",
        user_summary: str = "",
    ) -> dict[str, Any]:
        """Create a clinical/safety escalation for staff.

        Use this for medication, diagnosis, dosage, prescription, severe symptoms, complications, emergency-like issues, or anything outside non-clinical scope.

        Args:
            reason: Why this must be escalated.
            urgency: normal or urgent.
            user_summary: Brief summary of what the user said.
        """
        priority = "urgent" if urgency.lower() == "urgent" else "normal"
        task = db.add_task(
            room_name=self.room_name,
            task_type="escalation",
            title="Clinical/safety escalation",
            details={"reason": reason, "user_summary": user_summary},
            priority=priority,
        )
        return {"status": "created", "task": task}

    @function_tool()
    async def log_safe_faq_interaction(
        self,
        context: RunContext,
        topic: str,
        summary: str,
    ) -> dict[str, Any]:
        """Log a safe non-clinical FAQ interaction.

        Use this for clinic hours, location, parking, pricing, membership, cancellation policy, preparation policy, or general non-clinical service info.

        Args:
            topic: The FAQ topic.
            summary: Short summary of what was answered.
        """
        db.add_event(self.room_name, "safe_faq", {"topic": topic, "summary": summary})
        return {"status": "logged"}


server = AgentServer()


@server.rtc_session(agent_name=AGENT_NAME)
async def entrypoint(ctx: agents.JobContext):
    room_name = getattr(ctx.room, "name", "unknown-room")
    log.info("starting healthcare helper agent in room=%s", room_name)

    session = AgentSession(
        stt=cartesia.STT(
            api_key=CARTESIA_API_KEY,
            model=CARTESIA_STT_MODEL,
            language="en",
        ),
        llm=anthropic.LLM(
            api_key=ANTHROPIC_API_KEY,
            model=ANTHROPIC_MODEL,
            temperature=0.2,
            max_tokens=220,
        ),
        tts=cartesia.TTS(
            api_key=CARTESIA_API_KEY,
            model=CARTESIA_TTS_MODEL,
            voice=CARTESIA_TTS_VOICE,
            speed=CARTESIA_TTS_SPEED,
            sample_rate=24000,
        ),
        # Cartesia Ink 2 supports STT turn detection, interim results, and streaming.
        turn_handling={"turn_detection": "stt"},
        allow_interruptions=True,
        min_endpointing_delay=0.4,
        max_endpointing_delay=2.5,
    )

    await session.start(
        room=ctx.room,
        agent=HealthcareHelperAgent(room_name=room_name),
    )

    await session.say(
        "Hello! Welcome. I'm a clinic helper here to assist you with appointments, "
        "scheduling, clinic information, and other service questions. How can I help you?"
    )


if __name__ == "__main__":
    agents.cli.run_app(server)
