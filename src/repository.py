from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import and_, delete, func, or_, select

from . import db
from .models import Appointment, CallLog, Case, Doctor, Patient
from .settings import CLINIC_TIMEZONE

# Clinic-local timezone (e.g. Asia/Kolkata). Doctor working hours like "09:00"
# are interpreted in THIS zone, so a 9 AM slot really means 9 AM locally, not UTC.
CLINIC_TZ = ZoneInfo(CLINIC_TIMEZONE)

WEEKDAY_ABBR = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
TIME_OF_DAY_RANGES = {
    "morning": (time(0, 0), time(12, 0)),
    "afternoon": (time(12, 0), time(17, 0)),
    "evening": (time(17, 0), time(23, 59)),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    value = (value or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _coerce_field(key: str, value: Any) -> Any:
    if key == "next_retention_due_date":
        return _parse_date(value)
    if key == "do_not_call":
        return value if isinstance(value, bool) else str(value).strip().lower() == "true"
    return value


def _patient_to_dict(p: Patient) -> dict[str, Any]:
    return {
        "patient_key": p.patient_key,
        "phone_number": p.phone_number,
        "name": p.name,
        "first_seen_at": p.first_seen_at.isoformat() if p.first_seen_at else "",
        "last_seen_at": p.last_seen_at.isoformat() if p.last_seen_at else "",
        "total_calls": p.total_calls,
        "memory_summary": p.memory_summary,
        "next_retention_due_date": (
            p.next_retention_due_date.isoformat() if p.next_retention_due_date else ""
        ),
        "retention_reason": p.retention_reason,
        "retention_attempts": p.retention_attempts,
        "last_retention_attempt_at": (
            p.last_retention_attempt_at.isoformat() if p.last_retention_attempt_at else ""
        ),
        "do_not_call": p.do_not_call,
        "retention_status": p.retention_status,
    }


def _case_to_dict(c: Case) -> dict[str, Any]:
    return {
        "id": c.id,
        "created_at": c.created_at.isoformat() if c.created_at else "",
        "patient_key": c.patient_key,
        "phone_number": c.phone_number,
        "patient_name": c.patient_name,
        "room_name": c.room_name,
        "case_type": c.case_type,
        "title": c.title,
        "details": c.details or {},
        "priority": c.priority,
        "status": c.status,
        "outcome_tag": c.outcome_tag,
    }


def _call_log_to_dict(cl: CallLog) -> dict[str, Any]:
    return {
        "room_name": cl.room_name,
        "patient_key": cl.patient_key,
        "phone_number": cl.phone_number,
        "direction": cl.direction,
        "call_type": cl.call_type,
        "started_at": cl.started_at.isoformat() if cl.started_at else "",
        "ended_at": cl.ended_at.isoformat() if cl.ended_at else "",
        "duration_seconds": cl.duration_seconds,
        "outcome": cl.outcome,
        "transcript_summary": cl.transcript_summary,
    }


def _parse_hhmm(value: str) -> time:
    hour, minute = (int(x) for x in value.split(":"))
    return time(hour, minute)


def _doctor_to_dict(d: Doctor) -> dict[str, Any]:
    return {
        "id": d.id,
        "name": d.name,
        "specialty": d.specialty,
        "working_days": d.working_days,
        "work_start": d.work_start,
        "work_end": d.work_end,
        "slot_minutes": d.slot_minutes,
        "active": d.active,
    }


def _appointment_to_dict(a: Appointment, *, doctor_name: str = "", specialty: str = "") -> dict[str, Any]:
    return {
        "id": a.id,
        "created_at": a.created_at.isoformat() if a.created_at else "",
        "patient_key": a.patient_key,
        "patient_name": a.patient_name,
        "doctor_id": a.doctor_id,
        "doctor_name": doctor_name,
        "specialty": specialty,
        "scheduled_at": a.scheduled_at.isoformat() if a.scheduled_at else "",
        "duration_minutes": a.duration_minutes,
        "reason": a.reason,
        "status": a.status,
    }


async def _ensure_patient(session, patient_key: str, *, phone_number: str = "", name: str = "") -> Patient:
    patient = await session.get(Patient, patient_key)
    if not patient:
        now = datetime.now(timezone.utc)
        patient = Patient(
            patient_key=patient_key,
            phone_number=phone_number,
            name=name,
            first_seen_at=now,
            last_seen_at=now,
            total_calls=0,
        )
        session.add(patient)
        await session.flush()
    return patient


# --- patients (memory) ---


async def reset_demo_data() -> None:
    """Wipe patient-facing demo data (appointments, cases, call logs, patients)
    so re-seeding produces a clean state instead of stacking duplicates.

    Leaves the `doctors` table untouched (seeded separately by seed_doctors).
    Deletes children before parents to respect foreign keys.
    """
    async with db.get_session() as session:
        await session.execute(delete(Appointment))
        await session.execute(delete(Case))
        await session.execute(delete(CallLog))
        await session.execute(delete(Patient))
        await session.commit()


async def get_patient(patient_key: str) -> dict[str, Any] | None:
    async with db.get_session() as session:
        patient = await session.get(Patient, patient_key)
        return _patient_to_dict(patient) if patient else None


async def get_patient_by_phone(phone_number: str) -> dict[str, Any] | None:
    """Find an existing patient by their phone number (most recently seen wins).

    Used for inbound calls: the caller ID is a phone number, so we match it to
    a known patient record to reuse their history/name instead of creating a
    brand-new stranger record every time they call.
    """
    if not phone_number:
        return None
    async with db.get_session() as session:
        result = await session.execute(
            select(Patient)
            .where(Patient.phone_number == phone_number)
            .order_by(Patient.last_seen_at.desc().nulls_last())
            .limit(1)
        )
        patient = result.scalars().first()
        return _patient_to_dict(patient) if patient else None


async def get_patient_by_name(name: str) -> dict[str, Any] | None:
    """Find an existing patient by name (case-insensitive, most recent wins).

    Used for the browser call flow where a returning caller identifies themself
    by name rather than a phone caller-ID.
    """
    if not name or not name.strip():
        return None
    async with db.get_session() as session:
        result = await session.execute(
            select(Patient)
            .where(Patient.name.ilike(name.strip()))
            .order_by(Patient.last_seen_at.desc().nulls_last())
            .limit(1)
        )
        patient = result.scalars().first()
        return _patient_to_dict(patient) if patient else None


async def upsert_patient_seen(
    patient_key: str, *, phone_number: str = "", name: str = ""
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    async with db.get_session() as session:
        patient = await session.get(Patient, patient_key)
        if patient:
            patient.last_seen_at = now
            patient.total_calls = (patient.total_calls or 0) + 1
            if name and not patient.name:
                patient.name = name
            if phone_number and not patient.phone_number:
                patient.phone_number = phone_number
        else:
            patient = Patient(
                patient_key=patient_key,
                phone_number=phone_number,
                name=name,
                first_seen_at=now,
                last_seen_at=now,
                total_calls=1,
            )
            session.add(patient)
        await session.commit()
        await session.refresh(patient)
        return _patient_to_dict(patient)


async def update_patient_fields(patient_key: str, **fields: Any) -> None:
    async with db.get_session() as session:
        patient = await _ensure_patient(session, patient_key)
        for key, value in fields.items():
            setattr(patient, key, _coerce_field(key, value))
        await session.commit()


async def record_retention_attempt(patient_key: str) -> None:
    async with db.get_session() as session:
        patient = await _ensure_patient(session, patient_key)
        patient.retention_attempts = (patient.retention_attempts or 0) + 1
        patient.last_retention_attempt_at = datetime.now(timezone.utc)
        patient.retention_status = "contacted"
        await session.commit()


async def list_patients(limit: int = 200) -> list[dict[str, Any]]:
    async with db.get_session() as session:
        result = await session.execute(
            select(Patient).order_by(Patient.last_seen_at.desc().nulls_last()).limit(limit)
        )
        return [_patient_to_dict(p) for p in result.scalars().all()]


async def due_retention_patients(
    *, today_iso: str, inactive_cutoff_iso: str
) -> list[dict[str, Any]]:
    today = _parse_date(today_iso) or date.today()
    inactive_cutoff = _parse_dt(inactive_cutoff_iso)
    async with db.get_session() as session:
        result = await session.execute(
            select(Patient).where(
                Patient.do_not_call.is_(False),
                Patient.phone_number != "",
                or_(
                    Patient.next_retention_due_date <= today,
                    and_(
                        Patient.next_retention_due_date.is_(None),
                        Patient.last_seen_at <= inactive_cutoff,
                    ),
                ),
            )
        )
        return [_patient_to_dict(p) for p in result.scalars().all()]


async def retention_queue(limit: int = 100) -> list[dict[str, Any]]:
    async with db.get_session() as session:
        result = await session.execute(
            select(Patient)
            .where(
                or_(
                    Patient.next_retention_due_date.is_not(None),
                    Patient.retention_status != "",
                )
            )
            .order_by(Patient.next_retention_due_date.asc().nulls_last())
            .limit(limit)
        )
        return [_patient_to_dict(p) for p in result.scalars().all()]


# --- cases (appointment/callback/escalation/retention/faq/feedback) ---


async def add_case(
    *,
    patient_key: str,
    phone_number: str = "",
    patient_name: str = "",
    room_name: str = "",
    case_type: str,
    title: str,
    details: dict[str, Any],
    priority: str = "normal",
    status: str = "new",
    outcome_tag: str = "",
) -> dict[str, Any]:
    async with db.get_session() as session:
        await _ensure_patient(session, patient_key, phone_number=phone_number, name=patient_name)
        case = Case(
            created_at=datetime.now(timezone.utc),
            patient_key=patient_key,
            phone_number=phone_number,
            patient_name=patient_name,
            room_name=room_name,
            case_type=case_type,
            title=title,
            details=details,
            priority=priority,
            status=status,
            outcome_tag=outcome_tag,
        )
        session.add(case)
        await session.commit()
        await session.refresh(case)
        return _case_to_dict(case)


async def list_cases(limit: int = 100, case_type: str | None = None) -> list[dict[str, Any]]:
    async with db.get_session() as session:
        stmt = select(Case).order_by(Case.id.desc()).limit(limit)
        if case_type:
            stmt = stmt.where(Case.case_type == case_type)
        result = await session.execute(stmt)
        return [_case_to_dict(c) for c in result.scalars().all()]


async def dashboard_summary() -> dict[str, Any]:
    async with db.get_session() as session:
        patients_count = (
            await session.execute(select(func.count()).select_from(Patient))
        ).scalar_one()
        total_cases = (await session.execute(select(func.count()).select_from(Case))).scalar_one()
        rows = await session.execute(select(Case.case_type, func.count()).group_by(Case.case_type))
        by_type = dict(rows.all())

    return {
        "patients": patients_count,
        "tasks": total_cases,
        "appointments": by_type.get("appointment", 0),
        "callbacks": by_type.get("callback", 0),
        "escalations": by_type.get("escalation", 0),
        "retention": by_type.get("retention", 0),
        "faq": by_type.get("faq", 0),
        "feedback": by_type.get("feedback", 0),
    }


# --- call log ---


async def add_call_log(
    *,
    room_name: str,
    patient_key: str,
    phone_number: str = "",
    direction: str,
    call_type: str,
    started_at: str,
    ended_at: str,
    duration_seconds: float,
    outcome: str,
    transcript_summary: str = "",
) -> None:
    async with db.get_session() as session:
        await _ensure_patient(session, patient_key, phone_number=phone_number)
        call = CallLog(
            room_name=room_name,
            patient_key=patient_key,
            phone_number=phone_number,
            direction=direction,
            call_type=call_type,
            started_at=_parse_dt(started_at),
            ended_at=_parse_dt(ended_at),
            duration_seconds=duration_seconds,
            outcome=outcome,
            transcript_summary=transcript_summary,
        )
        session.add(call)
        await session.commit()


async def list_call_log(limit: int = 100) -> list[dict[str, Any]]:
    async with db.get_session() as session:
        result = await session.execute(
            select(CallLog).order_by(CallLog.started_at.desc()).limit(limit)
        )
        return [_call_log_to_dict(c) for c in result.scalars().all()]


async def recent_calls_for_patient(patient_key: str, limit: int = 3) -> list[dict[str, Any]]:
    """Most recent calls for one patient, newest first — used to give the agent
    real conversation history (not just the condensed memory summary)."""
    async with db.get_session() as session:
        result = await session.execute(
            select(CallLog)
            .where(CallLog.patient_key == patient_key)
            .order_by(CallLog.started_at.desc())
            .limit(limit)
        )
        return [_call_log_to_dict(c) for c in result.scalars().all()]


# --- doctors + appointments ---


async def list_doctors() -> list[dict[str, Any]]:
    async with db.get_session() as session:
        result = await session.execute(
            select(Doctor).where(Doctor.active.is_(True)).order_by(Doctor.name)
        )
        doctors = result.scalars().all()
        out = []
        for d in doctors:
            count_result = await session.execute(
                select(func.count()).select_from(Appointment).where(
                    Appointment.doctor_id == d.id,
                    Appointment.status == "booked",
                    Appointment.scheduled_at >= datetime.now(timezone.utc),
                )
            )
            item = _doctor_to_dict(d)
            item["upcoming_appointments"] = count_result.scalar_one()
            out.append(item)
        return out


async def _find_available_slot(
    session,
    doctor: Doctor,
    *,
    preferred_date: date | None,
    preferred_time_of_day: str,
    horizon_days: int = 14,
) -> datetime | None:
    slot_delta = timedelta(minutes=doctor.slot_minutes)
    work_start = _parse_hhmm(doctor.work_start)
    work_end = _parse_hhmm(doctor.work_end)
    working_days = {d.strip() for d in doctor.working_days.split(",") if d.strip()}
    tod_range = TIME_OF_DAY_RANGES.get((preferred_time_of_day or "").lower())

    now_local = datetime.now(CLINIC_TZ)
    start_date = preferred_date or now_local.date()

    # ONE query for the whole horizon (instead of one per day). Working hours are
    # clinic-local (IST); stored times are tz-aware so we compare by instant.
    horizon_start = datetime.combine(start_date, time.min, tzinfo=CLINIC_TZ)
    horizon_end = datetime.combine(start_date + timedelta(days=horizon_days), time.max, tzinfo=CLINIC_TZ)
    result = await session.execute(
        select(Appointment.scheduled_at).where(
            Appointment.doctor_id == doctor.id,
            Appointment.status == "booked",
            Appointment.scheduled_at >= horizon_start,
            Appointment.scheduled_at <= horizon_end,
        )
    )
    booked_times = {row[0].astimezone(CLINIC_TZ) for row in result.all()}

    for day_offset in range(horizon_days):
        candidate_date = start_date + timedelta(days=day_offset)
        if WEEKDAY_ABBR[candidate_date.weekday()] not in working_days:
            continue
        cursor = datetime.combine(candidate_date, work_start, tzinfo=CLINIC_TZ)
        end_of_day = datetime.combine(candidate_date, work_end, tzinfo=CLINIC_TZ)
        while cursor + slot_delta <= end_of_day:
            in_range = tod_range is None or (tod_range[0] <= cursor.time() < tod_range[1])
            # Never offer a slot in the past (e.g. earlier today).
            if in_range and cursor > now_local and cursor not in booked_times:
                return cursor
            cursor += slot_delta
    return None


async def next_available_slot(
    *,
    doctor_name: str = "",
    specialty: str = "",
    preferred_date: str = "",
    preferred_time_of_day: str = "",
) -> dict[str, Any] | None:
    """Find the soonest open slot for a doctor/specialty WITHOUT booking it.

    Used so the agent can tell a caller whether a specific doctor is available
    (and when) before actually booking.
    """
    pref_date = _parse_date(preferred_date)
    async with db.get_session() as session:
        stmt = select(Doctor).where(Doctor.active.is_(True))
        if doctor_name:
            stmt = stmt.where(Doctor.name.ilike(f"%{doctor_name}%"))
        elif specialty:
            stmt = stmt.where(Doctor.specialty.ilike(f"%{specialty}%"))
        candidates = (await session.execute(stmt.order_by(Doctor.name))).scalars().all()
        if not candidates:
            return None

        best: tuple[Doctor, datetime] | None = None
        for doctor in candidates:
            slot = await _find_available_slot(
                session, doctor, preferred_date=pref_date, preferred_time_of_day=preferred_time_of_day
            )
            if slot and (best is None or slot < best[1]):
                best = (doctor, slot)
        if best is None and preferred_time_of_day:
            for doctor in candidates:
                slot = await _find_available_slot(
                    session, doctor, preferred_date=pref_date, preferred_time_of_day=""
                )
                if slot and (best is None or slot < best[1]):
                    best = (doctor, slot)
        if best is None:
            return None

        doctor, slot = best
        return {
            "doctor_name": doctor.name,
            "specialty": doctor.specialty,
            "scheduled_at": slot.isoformat(),
            "duration_minutes": doctor.slot_minutes,
        }


async def find_and_book_appointment(
    *,
    patient_key: str,
    patient_name: str,
    reason: str,
    preferred_date: str = "",
    preferred_time_of_day: str = "",
    doctor_name: str = "",
    specialty: str = "",
) -> dict[str, Any] | None:
    pref_date = _parse_date(preferred_date)
    async with db.get_session() as session:
        stmt = select(Doctor).where(Doctor.active.is_(True))
        if doctor_name:
            stmt = stmt.where(Doctor.name.ilike(f"%{doctor_name}%"))
        elif specialty:
            stmt = stmt.where(Doctor.specialty.ilike(f"%{specialty}%"))
        candidates = (await session.execute(stmt.order_by(Doctor.name))).scalars().all()

        if not candidates:
            # requested doctor/specialty didn't match anyone active — fall back to anyone available
            candidates = (
                (await session.execute(select(Doctor).where(Doctor.active.is_(True)).order_by(Doctor.name)))
                .scalars()
                .all()
            )
        if not candidates:
            return None

        best: tuple[Doctor, datetime] | None = None
        for doctor in candidates:
            slot = await _find_available_slot(
                session, doctor, preferred_date=pref_date, preferred_time_of_day=preferred_time_of_day
            )
            if slot and (best is None or slot < best[1]):
                best = (doctor, slot)

        if best is None and preferred_time_of_day:
            # relax the time-of-day preference if nothing matched it
            for doctor in candidates:
                slot = await _find_available_slot(
                    session, doctor, preferred_date=pref_date, preferred_time_of_day=""
                )
                if slot and (best is None or slot < best[1]):
                    best = (doctor, slot)

        if best is None:
            return None

        doctor, scheduled_at = best
        await _ensure_patient(session, patient_key, name=patient_name)
        appointment = Appointment(
            created_at=datetime.now(timezone.utc),
            patient_key=patient_key,
            patient_name=patient_name,
            doctor_id=doctor.id,
            scheduled_at=scheduled_at,
            duration_minutes=doctor.slot_minutes,
            reason=reason,
            status="booked",
        )
        session.add(appointment)
        await session.commit()
        await session.refresh(appointment)
        return _appointment_to_dict(appointment, doctor_name=doctor.name, specialty=doctor.specialty)


async def reschedule_appointment(
    *,
    patient_key: str,
    preferred_date: str = "",
    preferred_time_of_day: str = "",
    doctor_name: str = "",
    specialty: str = "",
    reason: str = "",
) -> dict[str, Any] | None:
    """Move the patient's soonest upcoming appointment to a new slot.

    Marks the old appointment 'rescheduled' and books a new one. Keeps the same
    doctor unless a different doctor/specialty is requested. Returns the new
    booking (with previous time) or None if no slot / no existing appointment.
    """
    pref_date = _parse_date(preferred_date)
    now = datetime.now(timezone.utc)
    async with db.get_session() as session:
        existing = (
            await session.execute(
                select(Appointment)
                .where(
                    Appointment.patient_key == patient_key,
                    Appointment.status == "booked",
                    Appointment.scheduled_at >= now,
                )
                .order_by(Appointment.scheduled_at.asc())
                .limit(1)
            )
        ).scalars().first()

        # Pick the doctor: requested one, else keep the existing appointment's doctor.
        doctor = None
        if doctor_name or specialty:
            stmt = select(Doctor).where(Doctor.active.is_(True))
            if doctor_name:
                stmt = stmt.where(Doctor.name.ilike(f"%{doctor_name}%"))
            elif specialty:
                stmt = stmt.where(Doctor.specialty.ilike(f"%{specialty}%"))
            doctor = (await session.execute(stmt)).scalars().first()
        elif existing:
            doctor = await session.get(Doctor, existing.doctor_id)
        if doctor is None:
            return None

        slot = await _find_available_slot(
            session, doctor, preferred_date=pref_date, preferred_time_of_day=preferred_time_of_day
        )
        if slot is None and preferred_time_of_day:
            slot = await _find_available_slot(
                session, doctor, preferred_date=pref_date, preferred_time_of_day=""
            )
        if slot is None:
            return None

        previous_iso = existing.scheduled_at.isoformat() if existing else ""
        patient_name = existing.patient_name if existing else ""
        if existing:
            existing.status = "rescheduled"

        appointment = Appointment(
            created_at=now,
            patient_key=patient_key,
            patient_name=patient_name,
            doctor_id=doctor.id,
            scheduled_at=slot,
            duration_minutes=doctor.slot_minutes,
            reason=reason or (existing.reason if existing else ""),
            status="booked",
        )
        session.add(appointment)
        await session.commit()
        await session.refresh(appointment)
        out = _appointment_to_dict(appointment, doctor_name=doctor.name, specialty=doctor.specialty)
        out["previous_scheduled_at"] = previous_iso
        out["had_existing"] = existing is not None
        return out


async def cancel_appointment(*, patient_key: str) -> dict[str, Any] | None:
    """Cancel the patient's soonest upcoming appointment. Returns its details or None."""
    now = datetime.now(timezone.utc)
    async with db.get_session() as session:
        row = (
            await session.execute(
                select(Appointment, Doctor.name, Doctor.specialty)
                .join(Doctor, Appointment.doctor_id == Doctor.id)
                .where(
                    Appointment.patient_key == patient_key,
                    Appointment.status == "booked",
                    Appointment.scheduled_at >= now,
                )
                .order_by(Appointment.scheduled_at.asc())
                .limit(1)
            )
        ).first()
        if not row:
            return None
        appointment, dname, dspec = row
        appointment.status = "cancelled"
        await session.commit()
        return _appointment_to_dict(appointment, doctor_name=dname, specialty=dspec)


async def list_appointments(limit: int = 100) -> list[dict[str, Any]]:
    async with db.get_session() as session:
        result = await session.execute(
            select(Appointment, Doctor.name, Doctor.specialty)
            .join(Doctor, Appointment.doctor_id == Doctor.id)
            .order_by(Appointment.scheduled_at.desc())
            .limit(limit)
        )
        return [
            _appointment_to_dict(appointment, doctor_name=doctor_name, specialty=specialty)
            for appointment, doctor_name, specialty in result.all()
        ]


async def upcoming_appointments_for_patient(
    patient_key: str, limit: int = 3
) -> list[dict[str, Any]]:
    """A patient's future booked appointments, soonest first — used to remind
    them on an outbound call."""
    now = datetime.now(timezone.utc)
    async with db.get_session() as session:
        result = await session.execute(
            select(Appointment, Doctor.name, Doctor.specialty)
            .join(Doctor, Appointment.doctor_id == Doctor.id)
            .where(
                Appointment.patient_key == patient_key,
                Appointment.status == "booked",
                Appointment.scheduled_at >= now,
            )
            .order_by(Appointment.scheduled_at.asc())
            .limit(limit)
        )
        return [
            _appointment_to_dict(appointment, doctor_name=doctor_name, specialty=specialty)
            for appointment, doctor_name, specialty in result.all()
        ]
