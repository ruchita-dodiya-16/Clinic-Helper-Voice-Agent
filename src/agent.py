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

from zoneinfo import ZoneInfo

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
    CLINIC_TIMEZONE,
    ROOT_DIR,
    SIP_OUTBOUND_TRUNK_ID,
    STAFF_TRANSFER_NUMBER,
)

# Clinic-local timezone for speaking dates/times to callers (e.g. Asia/Kolkata).
CLINIC_TZ = ZoneInfo(CLINIC_TIMEZONE)


def _friendly_dt(iso_str: str) -> str:
    """Format an ISO datetime string in clinic-local time, e.g.
    'Monday, July 20 at 02:30 PM'. Converts from whatever tz it's stored in."""
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.astimezone(CLINIC_TZ).strftime("%A, %B %d at %I:%M %p")
    except (ValueError, TypeError):
        return iso_str or ""

load_dotenv(ROOT_DIR / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("healthcare-helper-agent")

MEMORY_SUMMARY_MAX_CHARS = 800
DEFAULT_GREETING = (
    "Thank you for calling the clinic. This is your CareConnect assistant. "
    "How can I help you today?"
)


def safe_identity(value: str) -> str:
    value = (value or "").strip() or "Patient"
    value = re.sub(r"[^a-zA-Z0-9_-]+", "-", value)
    return value[:40] or "Patient"


def first_name(name: str) -> str:
    """Return just the caller's first name for a warm, natural greeting."""
    return (name or "").strip().split(" ")[0] if name else ""


def _format_appointment(appt: dict[str, Any]) -> str:
    """Human-friendly phrase for an appointment, e.g.
    'Dr. Ananya Mehta (General Physician) on Friday, July 18 at 11:00 AM'."""
    doctor = appt.get("doctor_name") or "your doctor"
    specialty = appt.get("specialty") or ""
    specialty_part = f" ({specialty})" if specialty else ""
    when = _friendly_dt(appt.get("scheduled_at", ""))
    reason = appt.get("reason") or ""
    reason_part = f" for {reason}" if reason else ""
    return f"{doctor}{specialty_part} on {when}{reason_part}".strip()


def _load_knowledge_base() -> str:
    try:
        return CLINIC_KB_PATH.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


BASE_INSTRUCTIONS = """
You are CareConnect Voice, a healthcare clinic helper. You are not a doctor and you must never replace a clinician.

## NEVER leave the caller in silence (CRITICAL)
This is a live phone call — silence longer than a couple of seconds feels broken and the
caller will think the line dropped. So:
- When you call a tool (booking, availability, callback, etc.), the SYSTEM automatically speaks a
  short "one moment…" line for you the instant the tool starts — so you do NOT need to add your own
  "let me check" before a tool call. Just call the tool, then speak the RESULT when it returns.
- For anything that does NOT use a tool, always respond right away — never go quiet.
- If you need more details before you can act, ask ONE short relevant question immediately (e.g.
  "Sure — what day works best for you?") rather than pausing.
- When a tool result comes back, immediately tell the caller the outcome (the confirmed doctor/time,
  etc.). Keep every spoken line short and natural — one sentence is ideal for voice.

Route every user message using this decision order:

1. A HEALTH CONCERN where the caller wants to be seen or asks "what should I do?" — a toothache,
   a cold, a check-up, blood-pressure follow-up, a child's vaccination, general aches, etc. This is
   the COMMON, NORMAL case and it is NOT blocked. Do NOT escalate it. Instead:
   - Briefly acknowledge with empathy ("Sorry you're dealing with that.").
   - Route them to the RIGHT doctor for it (see "Recommending the right doctor" below), then
     check availability and BOOK the appointment. Booking them with the correct doctor IS the help.
   - Don't diagnose or give treatment advice — just get them in with the right doctor.

2. TRUE clinical-advice or emergency requests ONLY — asking you to diagnose ("what's wrong with me"),
   recommend/adjust medication or dosage, give a prescription, or a SEVERE/EMERGENCY symptom
   (chest pain, trouble breathing, heavy bleeding, fainting, stroke signs) -> this is BLOCKED.
   You are NOT a doctor and must never give medical advice or replace one.
   - Say it must be assessed by a doctor IN PERSON / at the hospital:
     "I can't advise on that over the phone — it needs a doctor in person, so please come in."
   - For a real emergency: tell them to call local emergency services NOW, and call create_escalation.
   - If not an emergency, still offer to book them with the right doctor.
   - NEVER answer the medical question itself.

3. Scheduling intent -> use the RIGHT tool:
   - NEW appointment (book / schedule) -> create_appointment_request.
   - RESCHEDULE / move / change an existing appointment to a new day or time -> reschedule_appointment
     (it moves their existing appointment; keeps the same doctor unless they ask for a different one).
   - CANCEL an appointment -> cancel_appointment.
   These automatically use real available slots — never invent a time yourself; only tell the caller
   the doctor/time the tool actually confirms back to you.
   - If the caller asks WHICH doctors you have, what a doctor's specialty is, or who handles a certain issue -> call get_clinic_doctors and share it naturally.
   - If the caller asks for a SPECIFIC doctor or wants to know WHEN they can be seen -> call check_doctor_availability first to see the soonest slot, tell them, and only book (create_appointment_request) once they agree. If that doctor has nothing open, say so and offer the next available doctor/time.
4. Retention/service intent (a follow-up visit, maintenance reminder, "should I come back", membership renewal) -> call create_retention_task.
5. Approved non-clinical FAQ (hours, location, pricing, policy, membership, general service info) -> answer directly using ONLY the "Approved knowledge base" section below, then call log_safe_faq_interaction. If it's non-clinical but not covered there, give brief general help and still call log_safe_faq_interaction.
6. The caller wants a callback instead of continuing now -> call create_callback_request.
7. The caller offers feedback, a complaint, or a compliment about their experience -> Say "Thanks for letting us know" and call log_feedback.

## Recommending the right doctor (route the concern to the best specialist)
When a caller mentions a health concern that is NOT an emergency and wants to be seen, guide them to
the MOST RELEVANT doctor rather than just any slot. Match their concern to the clinic's specialties
(call get_clinic_doctors if you're unsure who's available):
- Teeth, gums, tooth pain, dental cleaning -> Dentist
- Heart, palpitations, blood-pressure management, cholesterol -> Cardiologist
- Child / baby / vaccination / pediatric care -> Pediatrician
- General check-up, fever, cold, diabetes, fatigue, or anything unclear/general -> General Physician
Suggest that doctor by name + specialty and why they fit ("For a tooth issue, our dentist Dr. Rohan
Shah would be the best person to see — would you like me to check his availability?"), then use
check_doctor_availability and book once they agree. This is routing to the right specialist, NOT
diagnosing — never tell them what condition they have.

Hard safety rules:
- Never diagnose, prescribe, recommend medication, interpret symptoms clinically, or give emergency instructions beyond telling the user to visit the hospital / contact local emergency services or clinic staff.
- Never position yourself as a replacement for a doctor. Clinical judgment always happens in person.
- Keep responses short, warm, and natural for voice. Usually one or two sentences.
- Ask only one question at a time.
- Do not mention internal tool names.
- Avoid collecting unnecessary PHI. If details are needed, keep them minimal.
- ALWAYS acknowledge FIRST before doing any tool calls — this makes latency feel natural.

Allowed examples:
- "Got it. Let me check our availability for you. I can help request an appointment. What day or time works best?"
- "For a tooth problem, our dentist Dr. Rohan Shah is the right person — shall I check when he's free?"
- "I'm not able to advise on that over the phone — that needs a doctor in person, so I'd recommend coming in. Would you like me to book you with the right doctor?"
- "That sounds urgent — please contact emergency services right away, and I'll alert our clinical team now."
"""


def build_instructions(*, memory_brief: str, call_type: str, is_returning: bool = False) -> str:
    parts = [BASE_INSTRUCTIONS.strip()]

    kb = _load_knowledge_base()
    if kb:
        parts.append("## Approved knowledge base (use this, and only this, for FAQ answers)\n" + kb)

    if is_returning and memory_brief:
        parts.append(
            "## Caller status: RETURNING (they called us)\n"
            "This caller has called before — you already know who they are. Do not ask who they "
            "are or re-introduce the clinic.\n"
            "IMPORTANT — let the caller lead. Do NOT proactively recite their appointment, health "
            "history, or other stored details. Just greet them and ask how you can help, then talk "
            "normally. ONLY bring up their appointment or past info if THEY ask about it (e.g. "
            "'when is my appointment?', 'what did we discuss?'). When they do ask, use the details "
            "below to answer accurately.\n\n"
            "What you know about this caller (reference only when relevant/asked):\n" + memory_brief
        )
    else:
        parts.append(
            "## Caller status: NEW\n"
            "This is this caller's first call — you have no history for them.\n"
            "FIRST, near the start of the call, ask for their name in a warm, natural way "
            "(\"May I get your name, please?\"). As SOON as they tell you, call save_patient_name "
            "with it so we greet them by name and remember them next time. Use their name naturally "
            "for the rest of the call.\n"
            "Then find out what they'd like help with today and help them as much as you can:\n"
            "- If they ask about doctors or specialties, use get_clinic_doctors to tell them who's available.\n"
            "- If they want to see a specific doctor, use check_doctor_availability to find the soonest "
            "slot, tell them, and book with create_appointment_request once they agree.\n"
            "- If they're not sure who they need, ask what the visit is about and suggest the right "
            "specialty, then check availability and book.\n"
            "- Answer any allowed non-clinical questions (hours, location, pricing, prep) from the "
            "knowledge base. Make them feel welcomed and well taken care of."
        )

    if call_type == "dashboard_outbound":
        parts.append(
            "## Call context: OUTBOUND REMINDER CALL\n"
            "You (the clinic) placed this call to REMIND the patient about their care. You already "
            "have all their information above (upcoming appointment, health concerns, history).\n"
            "Your job on this call:\n"
            "1. Warmly remind them of their UPCOMING APPOINTMENT — say the doctor, day, and time "
            "clearly, and what it's for.\n"
            "2. Ask if they have any questions or concerns about it.\n"
            "3. If they DO have a question or a problem (need to reschedule, a worry, etc.), help "
            "them and give a solution using your tools (e.g. rebook via create_appointment_request, "
            "escalate anything clinical). \n"
            "4. If they DON'T have anything — that's fine. Just confirm the reminder, tell them we "
            "look forward to seeing them, and politely wrap up. Do NOT drag the call out.\n"
            "Keep it short, warm, and natural. Do not re-ask who they are — you already know them."
        )
    elif call_type == "retention_outbound":
        parts.append(
            "## Call context\n"
            "You (the clinic) placed this call to the patient as an outbound re-engagement or "
            "reminder call. Be brief and respectful of their time. Confirm it's still a good time "
            "to talk before going further, and offer to call back later if not."
        )
    return "\n\n".join(parts)


async def _build_memory_brief(patient_record: dict[str, Any] | None) -> str:
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

    # Pull the last few real calls so the agent has concrete recent history to
    # reference naturally ("last time you called about your tooth pain..."),
    # not just the condensed 3-bullet summary.
    patient_key = patient_record.get("patient_key")
    if patient_key:
        try:
            recent = await repository.recent_calls_for_patient(patient_key, limit=3)
        except Exception:
            recent = []
        history_lines = []
        for call in recent:
            summary = (call.get("transcript_summary") or "").strip()
            if not summary:
                continue
            when = (call.get("started_at") or "")[:10]
            direction = call.get("direction", "")
            history_lines.append(f"- [{when}, {direction}] {summary}")
        if history_lines:
            bits.append(
                "Recent call history (most recent first):\n" + "\n".join(history_lines)
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
        is_returning: bool = False,
    ):
        self.room_name = room_name
        self.patient_key = patient_key
        self.phone_number = phone_number
        self.patient_name = patient_name
        super().__init__(
            instructions=build_instructions(
                memory_brief=memory_brief, call_type=call_type, is_returning=is_returning
            )
        )

    async def _log_case(self, **kwargs: Any) -> dict[str, Any]:
        return await repository.add_case(
            patient_key=self.patient_key,
            phone_number=self.phone_number,
            patient_name=self.patient_name,
            room_name=self.room_name,
            **kwargs,
        )

    def _filler(self, context: RunContext, text: str) -> None:
        """Speak a short 'hang on' line the INSTANT a task starts, so the caller
        never sits in silence while the DB/LLM work happens. Fire-and-forget
        (say() returns immediately); not added to chat history so it doesn't
        confuse the conversation."""
        try:
            context.session.say(text, add_to_chat_ctx=False)
        except Exception:
            log.debug("filler say failed", exc_info=True)

    @function_tool()
    async def save_patient_name(self, context: RunContext, name: str) -> dict[str, Any]:
        """Save the caller's name to their record. Call this as soon as a new
        caller tells you their name, so we greet them by name and remember them
        on their next call."""
        name = (name or "").strip()
        if not name:
            return {"status": "no_name"}
        fields: dict[str, Any] = {"name": name}
        # Also persist the phone number so this record is findable by caller-ID on
        # their next call (guarantees "remember me" even if the record was fresh).
        if self.phone_number:
            fields["phone_number"] = self.phone_number
        await repository.update_patient_fields(self.patient_key, **fields)
        self.patient_name = name
        return {"status": "saved", "name": name}

    @function_tool()
    async def get_clinic_doctors(self, context: RunContext) -> dict[str, Any]:
        """List the clinic's doctors, their specialties and working hours.

        Use this when a caller asks what doctors are available, which doctor
        handles a certain issue, or the clinic's specialties.
        """
        self._filler(context, "Let me pull up our doctors for you, one moment.")
        doctors = await repository.list_doctors()
        simplified = [
            {
                "name": d.get("name"),
                "specialty": d.get("specialty"),
                "working_days": d.get("working_days"),
                "hours": f"{d.get('work_start')}–{d.get('work_end')}",
            }
            for d in doctors
        ]
        return {"doctors": simplified}

    @function_tool()
    async def check_doctor_availability(
        self,
        context: RunContext,
        doctor_name: str = "",
        specialty: str = "",
        preferred_date: str = "",
        preferred_time_of_day: str = "",
    ) -> dict[str, Any]:
        """Check the SOONEST open slot for a doctor/specialty WITHOUT booking.

        Use this when a caller asks whether a specific doctor is available or
        when they can be seen, before you actually book. If they then want it,
        call create_appointment_request to book.
        """
        self._filler(context, "Sure, let me check the schedule for you — one moment.")
        slot = await repository.next_available_slot(
            doctor_name=doctor_name,
            specialty=specialty,
            preferred_date=preferred_date,
            preferred_time_of_day=preferred_time_of_day,
        )
        if not slot:
            return {
                "status": "no_availability",
                "message": "No open slot in the next two weeks matching that request.",
            }
        slot["scheduled_at_friendly"] = _friendly_dt(slot["scheduled_at"])
        return {"status": "available", "slot": slot}

    @function_tool()
    async def create_appointment_request(
        self,
        context: RunContext,
        reason: str = "",
        preferred_date: str = "",
        preferred_time_of_day: str = "",
        doctor_name: str = "",
        specialty: str = "",
    ) -> dict[str, Any]:
        """Book appointment with available doctor and time slot."""
        self._filler(context, "Great, let me get that booked for you — just a moment.")
        booking = await repository.find_and_book_appointment(
            patient_key=self.patient_key,
            patient_name=self.patient_name,
            reason=reason,
            preferred_date=preferred_date,
            preferred_time_of_day=preferred_time_of_day,
            doctor_name=doctor_name,
            specialty=specialty,
        )
        if not booking:
            return {
                "status": "no_availability",
                "message": "No doctor had an open slot in the next two weeks matching that request.",
            }
        booking["scheduled_at_friendly"] = _friendly_dt(booking["scheduled_at"])

        # Log a CASE recording that this appointment was CONFIRMED on the call.
        # (The appointments table drives future reminders; this case is the
        # "patient confirmed it on the call" record shown in the Cases section.)
        await self._log_case(
            case_type="appointment",
            title=f"Appointment confirmed — {booking.get('doctor_name', 'doctor')}",
            details={
                "confirmed": True,
                "health_issue": reason or "Appointment",
                "reason": reason,
                "doctor_name": booking.get("doctor_name", ""),
                "specialty": booking.get("specialty", ""),
                "scheduled_at": booking.get("scheduled_at", ""),
                "scheduled_at_friendly": booking["scheduled_at_friendly"],
            },
            status="confirmed",
        )
        return {"status": "booked", "appointment": booking}

    @function_tool()
    async def reschedule_appointment(
        self,
        context: RunContext,
        preferred_date: str = "",
        preferred_time_of_day: str = "",
        doctor_name: str = "",
        specialty: str = "",
    ) -> dict[str, Any]:
        """Move the caller's existing upcoming appointment to a new time/date.

        Use this when the caller wants to RESCHEDULE / move / change an existing
        appointment. Keeps the same doctor unless they ask for a different one.
        """
        self._filler(context, "Sure, let me move that appointment for you — one moment.")
        result = await repository.reschedule_appointment(
            patient_key=self.patient_key,
            preferred_date=preferred_date,
            preferred_time_of_day=preferred_time_of_day,
            doctor_name=doctor_name,
            specialty=specialty,
        )
        if not result:
            return {
                "status": "no_availability",
                "message": "Couldn't find an open slot for that request, or no upcoming appointment to move.",
            }
        result["scheduled_at_friendly"] = _friendly_dt(result["scheduled_at"])
        prev = result.get("previous_scheduled_at") or ""
        await self._log_case(
            case_type="appointment",
            title=f"Appointment rescheduled — {result.get('doctor_name', 'doctor')}",
            details={
                "confirmed": True,
                "health_issue": result.get("reason") or "Appointment reschedule",
                "reason": result.get("reason", ""),
                "doctor_name": result.get("doctor_name", ""),
                "specialty": result.get("specialty", ""),
                "scheduled_at": result.get("scheduled_at", ""),
                "scheduled_at_friendly": result["scheduled_at_friendly"],
                "previous_scheduled_at": prev,
                "previous_friendly": _friendly_dt(prev) if prev else "",
            },
            status="confirmed",
        )
        return {"status": "rescheduled", "appointment": result}

    @function_tool()
    async def cancel_appointment(self, context: RunContext) -> dict[str, Any]:
        """Cancel the caller's upcoming appointment. Use when they want to cancel."""
        self._filler(context, "Okay, let me take care of that cancellation.")
        cancelled = await repository.cancel_appointment(patient_key=self.patient_key)
        if not cancelled:
            return {"status": "none_found", "message": "No upcoming appointment to cancel."}
        cancelled["scheduled_at_friendly"] = _friendly_dt(cancelled["scheduled_at"])
        await self._log_case(
            case_type="appointment",
            title=f"Appointment cancelled — {cancelled.get('doctor_name', 'doctor')}",
            details={
                "confirmed": True,
                "health_issue": "Appointment cancelled",
                "doctor_name": cancelled.get("doctor_name", ""),
                "scheduled_at": cancelled.get("scheduled_at", ""),
                "scheduled_at_friendly": cancelled["scheduled_at_friendly"],
            },
            status="cancelled",
        )
        return {"status": "cancelled", "appointment": cancelled}

    @function_tool()
    async def create_callback_request(
        self,
        context: RunContext,
        callback_reason: str,
        best_time: str = "",
        priority: str = "normal",
    ) -> dict[str, Any]:
        """Create callback request for staff."""
        self._filler(context, "Okay, I'll set that callback up for you.")
        priority = "urgent" if priority.lower() == "urgent" else "normal"
        task = await self._log_case(
            case_type="callback",
            title="Callback request",
            details={"callback_reason": callback_reason, "best_time": best_time},
            priority=priority,
        )
        return {"status": "created", "task": task}

    @function_tool()
    async def create_escalation(
        self,
        context: RunContext,
        reason: str,
        urgency: str = "urgent",
    ) -> Any:
        """Create clinical/safety escalation for urgent review."""
        priority = "urgent" if urgency.lower() == "urgent" else "normal"
        task = await self._log_case(
            case_type="escalation",
            title="Clinical/safety escalation",
            details={"reason": reason},
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
        """Schedule retention/re-engagement follow-up."""
        self._filler(context, "Sure, let me set that reminder up for you.")
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
        """Log FAQ interaction (hours, pricing, policy, etc)."""
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
        """Log patient feedback or complaint."""
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
            # Shorter cap = fewer tokens to generate = faster first-audio and quicker
            # turn completion. Voice replies are 1-2 sentences, so 120 is plenty.
            max_tokens=120,
            # System prompt (clinic KB + patient memory) is static per call and resent every
            # turn; caching it cuts repeated-turn latency and cost.
            caching="ephemeral",
            # Disable strict tool-schema compilation. With our set of tools + optional
            # params, Anthropic's strict grammar compilation raises 400 "Schema is too
            # complex for compilation", which silently killed every reply after the
            # greeting. Non-strict tool schemas work fine for this use case.
            _strict_tool_schema=False,
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
            # Snappier end-of-turn: shorter waits before the agent starts responding.
            # Lower min/max delay = the agent jumps in faster once you stop talking.
            # 0.5s max keeps the pause human-short without cutting people off.
            endpointing=EndpointingOptions(mode="dynamic", min_delay=0.05, max_delay=0.5),
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


async def _run_dashboard_outbound_call(
    ctx: agents.JobContext, session: AgentSession, metadata: dict[str, Any]
) -> None:
    """Staff-initiated 'call this patient now' from the dashboard.

    Unlike the automated retention flow, we KNOW a human is expected to answer,
    so we skip Answering-Machine Detection entirely and greet immediately once
    the patient picks up. (AMD waits to classify the answerer before speaking,
    which is exactly why the agent was silent on pickup.)
    """
    phone_number = metadata.get("phone_number", "")
    patient_key = metadata.get("patient_key") or phone_number
    patient_name = metadata.get("patient_name", "")
    reason = metadata.get("retention_reason", "")
    started_at = repository.utc_now()

    if not phone_number or not SIP_OUTBOUND_TRUNK_ID:
        log.error("dashboard_outbound call missing phone_number or SIP_OUTBOUND_TRUNK_ID; aborting")
        ctx.shutdown(reason="missing outbound trunk config")
        return

    patient_record = await repository.get_patient(patient_key)
    memory_brief = await _build_memory_brief(patient_record)

    # Pull their upcoming appointment(s) so the agent can remind them of the exact
    # doctor/date/time, and so this info is in the agent's instructions.
    upcoming = await repository.upcoming_appointments_for_patient(patient_key, limit=3)
    next_appt = upcoming[0] if upcoming else None
    if upcoming:
        appt_lines = "\n".join(f"- {_format_appointment(a)}" for a in upcoming)
        memory_brief = (memory_brief + "\n\nUpcoming appointment(s):\n" + appt_lines).strip()

    agent = HealthcareHelperAgent(
        room_name=ctx.room.name,
        patient_key=patient_key,
        phone_number=phone_number,
        patient_name=patient_name or (patient_record or {}).get("name", ""),
        call_type="dashboard_outbound",
        memory_brief=memory_brief,
        is_returning=True,
    )

    await ctx.connect()
    await session.start(room=ctx.room, agent=agent)

    participant_identity = f"dashboard-{safe_identity(phone_number)}"
    try:
        await ctx.add_sip_participant(
            call_to=phone_number,
            trunk_id=SIP_OUTBOUND_TRUNK_ID,
            participant_identity=participant_identity,
        )
        # Waits until the callee actually answers and joins the room.
        await ctx.wait_for_participant(identity=participant_identity)
    except Exception:
        log.exception("dashboard outbound dial failed room=%s phone=%s", ctx.room.name, phone_number)
        ctx.shutdown(reason="dial failed")
        return

    # Greet right away — no AMD, so the caller hears the agent the moment they answer.
    # Lead with the appointment reminder since that's the point of this call.
    fname = first_name(patient_name or (patient_record or {}).get("name", ""))
    greeting_name = f", {fname}" if fname else ""
    if next_appt:
        await session.say(
            f"Hello{greeting_name}! This is CareConnect calling from the clinic with a friendly "
            f"reminder about your upcoming appointment — {_format_appointment(next_appt)}. "
            "Do you have any questions about it?"
        )
    else:
        reason_text = reason or "check in and see how you're doing"
        await session.say(
            f"Hello{greeting_name}! This is CareConnect calling on behalf of the clinic — "
            f"we wanted to {reason_text}. How are you doing today?"
        )

    _register_call_end_summary(
        ctx,
        session,
        patient_key=patient_key,
        phone_number=phone_number,
        direction="outbound",
        call_type="dashboard_outbound",
        started_at=started_at,
        outcome="answered",
    )


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
    memory_brief = await _build_memory_brief(patient_record)
    agent = HealthcareHelperAgent(
        room_name=ctx.room.name,
        patient_key=patient_key,
        phone_number=phone_number,
        patient_name=patient_name or (patient_record or {}).get("name", ""),
        call_type="retention_outbound",
        memory_brief=memory_brief,
        is_returning=True,
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
            fname = first_name(patient_name)
            greeting_name = f", {fname}" if fname else ""
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
            fname = first_name(patient_name)
            greeting_name = f", {fname}" if fname else ""
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

    if call_type == "dashboard_outbound":
        await _run_dashboard_outbound_call(ctx, session, metadata)
        return

    if call_type == "retention_outbound":
        await _run_retention_outbound_call(ctx, session, metadata)
        return

    phone_number = ""
    patient_name = metadata.get("participant_name", "")
    patient_type = (metadata.get("patient_type") or "").lower()  # "new" | "returning" | ""

    existing_record: dict[str, Any] | None = None
    if call_type == "inbound_phone":
        participant = await ctx.wait_for_participant()
        phone_number = participant.attributes.get("sip.phoneNumber", "") or ""
        if not phone_number:
            log.warning(
                "could not read caller phone number from SIP attributes; identity=%s attrs=%s",
                participant.identity,
                dict(participant.attributes),
            )
        # Match the incoming caller ID to a known patient so we reuse their
        # existing record + history rather than creating a new stranger each time.
        existing_record = await repository.get_patient_by_phone(phone_number)
        if existing_record:
            patient_key = existing_record["patient_key"]
            patient_name = existing_record.get("name") or patient_name
        else:
            patient_key = phone_number or f"unknown:{participant.identity}"
    else:
        # Browser call. If the caller identified as returning and gave a name,
        # try to match an existing patient by that name so we reuse their record.
        if patient_type == "returning" and patient_name:
            existing_record = await repository.get_patient_by_name(patient_name)
        if existing_record:
            patient_key = existing_record["patient_key"]
            patient_name = existing_record.get("name") or patient_name
        elif patient_name:
            patient_key = f"demo:{safe_identity(patient_name).lower()}"
        else:
            # New caller with no name yet — give this call its own record (keyed by
            # room) so unnamed callers don't collide. The agent will ask for their
            # name and save_patient_name updates THIS record.
            patient_key = f"demo:{safe_identity(ctx.room.name).lower()}"

    # Record/refresh the patient (bumps total_calls, updates last_seen) so the
    # inbound call is properly captured in the DB.
    patient_record = await repository.upsert_patient_seen(
        patient_key, phone_number=phone_number, name=patient_name
    )
    memory_brief = await _build_memory_brief(patient_record)
    # Returning if: the caller explicitly said so, we matched an existing record,
    # or this isn't their first call. Any of these + a known name -> greet by name.
    is_returning = bool(
        (
            patient_type == "returning"
            or existing_record is not None
            or int(patient_record.get("total_calls") or 1) > 1
        )
        and patient_record.get("name")
    )

    # For a known/returning caller, load their upcoming appointment so the agent
    # already has it (can remind them and answer questions about it) — same info
    # the outbound reminder call uses.
    if is_returning:
        upcoming = await repository.upcoming_appointments_for_patient(patient_key, limit=3)
        if upcoming:
            appt_lines = "\n".join(f"- {_format_appointment(a)}" for a in upcoming)
            memory_brief = (memory_brief + "\n\nUpcoming appointment(s):\n" + appt_lines).strip()

    agent = HealthcareHelperAgent(
        room_name=ctx.room.name,
        patient_key=patient_key,
        phone_number=phone_number,
        patient_name=patient_record.get("name") or patient_name,
        call_type=call_type,
        memory_brief=memory_brief,
        is_returning=is_returning,
    )
    await session.start(room=ctx.room, agent=agent)

    started_at = repository.utc_now()
    fname = first_name(patient_record.get("name") or patient_name)
    if is_returning and fname:
        # Inbound = the patient called US. Greet warmly by name and let THEM lead.
        # Do NOT recite their appointment/history up front — the agent still has
        # all of it in context and will share only if the patient asks.
        await session.say(
            f"Hi {fname}, welcome back! I'm your CareConnect clinic helper. "
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
