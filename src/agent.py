from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any

from anthropic import Anthropic
from dotenv import load_dotenv
from livekit import agents
from livekit.agents import Agent, AgentSession, RunContext, function_tool
from livekit.agents.beta.workflows import WarmTransferTask
from livekit.agents.voice.amd import AMD, AMDCategory
from livekit.agents.voice.turn import (
    EndpointingOptions,
    InterruptionOptions,
    PreemptiveGenerationOptions,
    TurnHandlingOptions,
)
from livekit.plugins import anthropic as anthropic_plugin
from livekit.plugins import cartesia

from . import repository
from .settings import (
    AGENT_NAME,
    ANTHROPIC_API_KEY,
    ANTHROPIC_MODEL,
    CARTESIA_API_KEY,
    CARTESIA_STT_MODEL,
    CARTESIA_TTS_MODEL,
    CARTESIA_TTS_SPEED,
    CARTESIA_TTS_VOICE,
    CLINIC_KB_PATH,
    ROOT_DIR,
    SIP_OUTBOUND_TRUNK_ID,
    STAFF_TRANSFER_NUMBER,
)

load_dotenv(ROOT_DIR / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("healthcare-helper-agent")

MEMORY_SUMMARY_MAX_CHARS = 800
DEFAULT_GREETING = (
    "Hello! Welcome. I'm a clinic helper here to assist you with appointments, "
    "scheduling, clinic information, and other service questions. How can I help you?"
)


def safe_identity(value: str) -> str:
    value = (value or "").strip() or "Patient"
    value = re.sub(r"[^a-zA-Z0-9_-]+", "-", value)
    return value[:40] or "Patient"


def _load_knowledge_base() -> str:
    try:
        return CLINIC_KB_PATH.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


BASE_INSTRUCTIONS = """
You are CareConnect Voice, a healthcare clinic helper. You are not a doctor and you must never replace a clinician.

Route every user message using this decision order:
1. Medication, diagnosis, dosage, prescriptions, severe symptoms, complications, or anything clinical/urgent -> this is BLOCKED. Do not answer it yourself. Say you cannot provide medical advice, and call create_escalation.
2. Scheduling intent (book, reschedule, cancel, confirm an appointment) -> call create_appointment_request.
3. Retention/service intent (a follow-up visit, maintenance reminder, "should I come back", membership renewal) -> call create_retention_task.
4. Approved non-clinical FAQ (hours, location, pricing, policy, membership, general service info) -> answer directly using ONLY the "Approved knowledge base" section below, then call log_safe_faq_interaction. If it's non-clinical but not covered there, give brief general help and still call log_safe_faq_interaction.
5. The caller wants a callback instead of continuing now -> call create_callback_request.
6. The caller offers feedback, a complaint, or a compliment about their experience -> call log_feedback.

Hard safety rules:
- Never diagnose, prescribe, recommend medication, interpret symptoms clinically, or give emergency instructions beyond telling the user to contact local emergency services or clinic staff.
- Keep responses short, warm, and natural for voice. Usually one or two sentences.
- Ask only one question at a time.
- Do not mention internal tool names.
- Avoid collecting unnecessary PHI. If details are needed, keep them minimal.

Allowed examples:
- I can help request an appointment. What day or time works best?
- The clinic team can call you back. What is the best callback number?
- I cannot provide medical advice, but I can alert the clinic team to follow up.
"""


def build_instructions(*, memory_brief: str, call_type: str) -> str:
    parts = [BASE_INSTRUCTIONS.strip()]
    kb = _load_knowledge_base()
    if kb:
        parts.append("## Approved knowledge base (use this, and only this, for FAQ answers)\n" + kb)
    if memory_brief:
        parts.append(
            "## What you remember about this caller (use naturally, don't recite it verbatim)\n"
            + memory_brief
        )
    if call_type == "retention_outbound":
        parts.append(
            "## Call context\n"
            "You (the clinic) placed this call to the patient as an outbound re-engagement or "
            "reminder call. Be brief and respectful of their time. Confirm it's still a good time "
            "to talk before going further, and offer to call back later if not."
        )
    return "\n\n".join(parts)


def _build_memory_brief(patient_record: dict[str, Any] | None) -> str:
    if not patient_record:
        return ""
    bits: list[str] = []
    if patient_record.get("name"):
        bits.append(f"Name: {patient_record['name']}")
    if patient_record.get("memory_summary"):
        bits.append(f"Notes from previous calls: {patient_record['memory_summary']}")
    if patient_record.get("next_retention_due_date"):
        bits.append(
            f"Open retention reminder due {patient_record['next_retention_due_date']}: "
            f"{patient_record.get('retention_reason', '')}"
        )
    return "\n".join(bits)


def _format_history(session: AgentSession) -> str:
    lines = []
    for msg in session.history.messages():
        if getattr(msg, "role", None) not in ("user", "assistant"):
            continue
        text = getattr(msg, "text_content", None)
        if not text:
            continue
        role = "Caller" if msg.role == "user" else "Assistant"
        lines.append(f"{role}: {text}")
    return "\n".join(lines)


def _summarize_call(history_text: str, previous_summary: str) -> str:
    if not history_text.strip():
        return previous_summary
    try:
        client = Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=220,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Condense the important, durable facts worth remembering about this "
                        "patient into at most 3 short bullet points (name if known, ongoing "
                        "concerns, preferences, appointment or retention context). Do not "
                        "include a transcript or restate everything said.\n\n"
                        f"Previous notes: {previous_summary or '(none)'}\n\n"
                        f"Call transcript:\n{history_text}"
                    ),
                }
            ],
        )
        text = "".join(block.text for block in response.content if hasattr(block, "text")).strip()
        return text[:MEMORY_SUMMARY_MAX_CHARS] if text else previous_summary
    except Exception:
        log.exception("failed to summarize call for patient memory")
        return previous_summary


class HealthcareHelperAgent(Agent):
    def __init__(
        self,
        *,
        room_name: str,
        patient_key: str,
        phone_number: str,
        patient_name: str,
        call_type: str,
        memory_brief: str = "",
    ):
        self.room_name = room_name
        self.patient_key = patient_key
        self.phone_number = phone_number
        self.patient_name = patient_name
        super().__init__(
            instructions=build_instructions(memory_brief=memory_brief, call_type=call_type)
        )

    async def _log_case(self, **kwargs: Any) -> dict[str, Any]:
        return await repository.add_case(
            patient_key=self.patient_key,
            phone_number=self.phone_number,
            patient_name=self.patient_name,
            room_name=self.room_name,
            **kwargs,
        )

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
        task = await self._log_case(
            case_type="appointment",
            title="Appointment request",
            details={
                "preferred_time": preferred_time,
                "reason": reason,
                "patient_name": patient_name or self.patient_name,
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
        task = await self._log_case(
            case_type="callback",
            title="Callback request",
            details={
                "callback_reason": callback_reason,
                "best_time": best_time,
                "phone_number": phone_number or self.phone_number,
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
    ) -> Any:
        """Create a clinical/safety escalation for staff.

        Use this for medication, diagnosis, dosage, prescription, severe symptoms, complications,
        emergency-like issues, or anything outside non-clinical scope.

        Args:
            reason: Why this must be escalated.
            urgency: normal or urgent.
            user_summary: Brief summary of what the user said.
        """
        priority = "urgent" if urgency.lower() == "urgent" else "normal"
        task = await self._log_case(
            case_type="escalation",
            title="Clinical/safety escalation",
            details={"reason": reason, "user_summary": user_summary},
            priority=priority,
        )

        if priority == "urgent" and STAFF_TRANSFER_NUMBER and SIP_OUTBOUND_TRUNK_ID:
            log.info("live-transferring escalation to staff room=%s", self.room_name)
            return (
                {"status": "escalated_transferring_to_staff", "task": task},
                WarmTransferTask(
                    sip_call_to=STAFF_TRANSFER_NUMBER,
                    sip_trunk_id=SIP_OUTBOUND_TRUNK_ID,
                    ringing_timeout=25,
                    chat_ctx=context.session.history,
                ),
            )

        if priority == "urgent":
            log.warning(
                "urgent escalation but STAFF_TRANSFER_NUMBER/SIP_OUTBOUND_TRUNK_ID not configured; "
                "dashboard-alert only"
            )
        return {"status": "created", "task": task}

    @function_tool()
    async def create_retention_task(
        self,
        context: RunContext,
        reason: str,
        due_date: str = "",
    ) -> dict[str, Any]:
        """Schedule a retention/re-engagement follow-up for this patient.

        Use this for maintenance reminders, "should I come back" style questions, or membership
        renewal follow-ups that don't need to be booked as an appointment right now.

        Args:
            reason: Why the follow-up is needed (e.g. "6 month cleaning reminder").
            due_date: ISO date (YYYY-MM-DD) the follow-up should happen, if known.
        """
        await repository.update_patient_fields(
            self.patient_key,
            next_retention_due_date=due_date,
            retention_reason=reason,
            retention_status="pending",
        )
        task = await self._log_case(
            case_type="retention",
            title="Retention/follow-up reminder",
            details={"reason": reason, "due_date": due_date},
        )
        return {"status": "scheduled", "task": task}

    @function_tool()
    async def log_safe_faq_interaction(
        self,
        context: RunContext,
        topic: str,
        summary: str,
    ) -> dict[str, Any]:
        """Log a safe non-clinical FAQ interaction.

        Use this for clinic hours, location, parking, pricing, membership, cancellation policy,
        preparation policy, or general non-clinical service info.

        Args:
            topic: The FAQ topic.
            summary: Short summary of what was answered.
        """
        task = await self._log_case(
            case_type="faq",
            title=f"FAQ: {topic}",
            details={"topic": topic, "summary": summary},
        )
        return {"status": "logged", "task": task}

    @function_tool()
    async def log_feedback(
        self,
        context: RunContext,
        summary: str,
        sentiment: str = "neutral",
    ) -> dict[str, Any]:
        """Log patient feedback, a complaint, or a compliment about their experience.

        Args:
            summary: Short summary of the feedback.
            sentiment: positive, neutral, or negative.
        """
        task = await self._log_case(
            case_type="feedback",
            title="Patient feedback",
            details={"summary": summary, "sentiment": sentiment},
        )
        return {"status": "logged", "task": task}


def _make_session() -> AgentSession:
    return AgentSession(
        stt=cartesia.STT(api_key=CARTESIA_API_KEY, model=CARTESIA_STT_MODEL, language="en"),
        llm=anthropic_plugin.LLM(
            api_key=ANTHROPIC_API_KEY,
            model=ANTHROPIC_MODEL,
            temperature=0.2,
            max_tokens=150,
            # System prompt (clinic KB + patient memory) is static per call and resent every
            # turn; caching it cuts repeated-turn latency and cost.
            caching="ephemeral",
        ),
        tts=cartesia.TTS(
            api_key=CARTESIA_API_KEY,
            model=CARTESIA_TTS_MODEL,
            voice=CARTESIA_TTS_VOICE,
            speed=CARTESIA_TTS_SPEED,
            sample_rate=24000,
        ),
        turn_handling=TurnHandlingOptions(
            turn_detection="stt",
            # Dynamic endpointing adapts the end-of-turn wait to how confident the model is
            # instead of always waiting a fixed delay - this is what makes a fixed-delay bot
            # feel slow. preemptive_tts lets the LLM/TTS start generating speculatively before
            # end-of-turn is finalized, so speech starts almost immediately once the caller
            # stops talking (discarded/redone if the guess was wrong).
            endpointing=EndpointingOptions(mode="dynamic", min_delay=0.2, max_delay=1.5),
            # "adaptive" interruption mode needs a VAD model, which this agent doesn't use
            # (STT-based turn detection only) - explicitly requesting it just logs a warning
            # and falls back anyway, so leave mode unset and keep plain interruption support.
            interruption=InterruptionOptions(enabled=True),
            preemptive_generation=PreemptiveGenerationOptions(enabled=True, preemptive_tts=True),
        ),
    )


def _register_call_end_summary(
    ctx: agents.JobContext,
    session: AgentSession,
    *,
    patient_key: str,
    phone_number: str,
    direction: str,
    call_type: str,
    started_at: str,
    outcome: str = "answered",
) -> None:
    async def _on_shutdown(reason: str) -> None:
        ended_at = repository.utc_now()
        try:
            duration = (
                datetime.fromisoformat(ended_at) - datetime.fromisoformat(started_at)
            ).total_seconds()
        except ValueError:
            duration = 0.0

        patient = await repository.get_patient(patient_key)
        previous_summary = (patient or {}).get("memory_summary", "")
        history_text = _format_history(session)
        summary = _summarize_call(history_text, previous_summary)
        if summary:
            await repository.update_patient_fields(patient_key, memory_summary=summary)

        await repository.add_call_log(
            room_name=ctx.room.name,
            patient_key=patient_key,
            phone_number=phone_number,
            direction=direction,
            call_type=call_type,
            started_at=started_at,
            ended_at=ended_at,
            duration_seconds=duration,
            outcome=outcome,
            transcript_summary=summary,
        )

    ctx.add_shutdown_callback(_on_shutdown)


async def _run_retention_outbound_call(
    ctx: agents.JobContext, session: AgentSession, metadata: dict[str, Any]
) -> None:
    phone_number = metadata.get("phone_number", "")
    patient_key = metadata.get("patient_key") or phone_number
    patient_name = metadata.get("patient_name", "")
    retention_reason = metadata.get("retention_reason", "")
    started_at = repository.utc_now()

    if not phone_number or not SIP_OUTBOUND_TRUNK_ID:
        log.error("retention_outbound call missing phone_number or SIP_OUTBOUND_TRUNK_ID; aborting")
        ctx.shutdown(reason="missing outbound trunk config")
        return

    patient_record = await repository.get_patient(patient_key)
    memory_brief = _build_memory_brief(patient_record)
    agent = HealthcareHelperAgent(
        room_name=ctx.room.name,
        patient_key=patient_key,
        phone_number=phone_number,
        patient_name=patient_name or (patient_record or {}).get("name", ""),
        call_type="retention_outbound",
        memory_brief=memory_brief,
    )

    await ctx.connect()
    await session.start(room=ctx.room, agent=agent)

    participant_identity = f"retention-{safe_identity(phone_number)}"
    outcome = "error"
    try:
        async with AMD(
            session,
            llm=session.llm,
            stt=session.stt,
            suppress_compatibility_warning=True,
            wait_until_finished=True,
        ) as detector:
            await ctx.add_sip_participant(
                call_to=phone_number,
                trunk_id=SIP_OUTBOUND_TRUNK_ID,
                participant_identity=participant_identity,
            )
            await ctx.wait_for_participant(identity=participant_identity)
            result = await detector.execute()

        # retention_worker already recorded the attempt before dispatching this call
        # (so a slow/failed call can't be double-dialed by the next poll cycle).

        if result.category == AMDCategory.HUMAN:
            outcome = "answered"
            greeting_name = f", {patient_name}" if patient_name else ""
            reason_text = retention_reason or "check in and see if you'd like to schedule a visit"
            await session.say(
                f"Hello{greeting_name}! This is CareConnect calling on behalf of the clinic — "
                f"we wanted to {reason_text}. Do you have a moment?"
            )
            _register_call_end_summary(
                ctx,
                session,
                patient_key=patient_key,
                phone_number=phone_number,
                direction="outbound",
                call_type="retention_outbound",
                started_at=started_at,
                outcome=outcome,
            )
            return

        if result.category == AMDCategory.MACHINE_VM:
            outcome = "voicemail_left"
            greeting_name = f", {patient_name}" if patient_name else ""
            reason_text = retention_reason or "scheduling your next visit"
            await session.say(
                f"Hi{greeting_name}, this is CareConnect calling on behalf of the clinic about "
                f"{reason_text}. Please call us back at your convenience. Thank you!",
                allow_interruptions=False,
            )
        else:
            outcome = (
                "no_answer" if result.category == AMDCategory.MACHINE_UNAVAILABLE else "uncertain"
            )
    except Exception:
        log.exception("retention outbound call failed room=%s phone=%s", ctx.room.name, phone_number)
        outcome = "error"

    ended_at = repository.utc_now()
    try:
        duration = (
            datetime.fromisoformat(ended_at) - datetime.fromisoformat(started_at)
        ).total_seconds()
    except ValueError:
        duration = 0.0
    await repository.add_call_log(
        room_name=ctx.room.name,
        patient_key=patient_key,
        phone_number=phone_number,
        direction="outbound",
        call_type="retention_outbound",
        started_at=started_at,
        ended_at=ended_at,
        duration_seconds=duration,
        outcome=outcome,
    )
    ctx.shutdown(reason=f"retention call outcome: {outcome}")


server = agents.AgentServer()


@server.rtc_session(agent_name=AGENT_NAME)
async def entrypoint(ctx: agents.JobContext) -> None:
    raw_meta = ctx.job.metadata or ""
    try:
        metadata: dict[str, Any] = json.loads(raw_meta) if raw_meta else {}
    except json.JSONDecodeError:
        metadata = {}
    call_type = metadata.get("call_type", "browser_demo")
    log.info("starting healthcare helper agent room=%s call_type=%s", ctx.room.name, call_type)

    session = _make_session()

    if call_type == "retention_outbound":
        await _run_retention_outbound_call(ctx, session, metadata)
        return

    phone_number = ""
    patient_name = metadata.get("participant_name", "")

    if call_type == "inbound_phone":
        participant = await ctx.wait_for_participant()
        phone_number = participant.attributes.get("sip.phoneNumber", "") or ""
        if not phone_number:
            log.warning(
                "could not read caller phone number from SIP attributes; identity=%s attrs=%s",
                participant.identity,
                dict(participant.attributes),
            )
        patient_key = phone_number or f"unknown:{participant.identity}"
    else:
        patient_key = f"demo:{safe_identity(patient_name).lower()}"

    patient_record = await repository.upsert_patient_seen(
        patient_key, phone_number=phone_number, name=patient_name
    )
    memory_brief = _build_memory_brief(patient_record)

    agent = HealthcareHelperAgent(
        room_name=ctx.room.name,
        patient_key=patient_key,
        phone_number=phone_number,
        patient_name=patient_record.get("name") or patient_name,
        call_type=call_type,
        memory_brief=memory_brief,
    )
    await session.start(room=ctx.room, agent=agent)

    started_at = repository.utc_now()
    is_returning = int(patient_record.get("total_calls") or 1) > 1 and patient_record.get("name")
    if is_returning:
        await session.say(
            f"Welcome back, {patient_record['name']}! I'm your clinic helper. "
            "How can I help you today?"
        )
    else:
        await session.say(DEFAULT_GREETING)

    _register_call_end_summary(
        ctx,
        session,
        patient_key=patient_key,
        phone_number=phone_number,
        direction="inbound" if call_type == "inbound_phone" else "browser_demo",
        call_type=call_type,
        started_at=started_at,
    )


if __name__ == "__main__":
    agents.cli.run_app(server)
