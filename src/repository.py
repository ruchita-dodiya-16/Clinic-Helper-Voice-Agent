from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import and_, func, or_, select

from . import db
from .models import CallLog, Case, Patient


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


async def get_patient(patient_key: str) -> dict[str, Any] | None:
    async with db.get_session() as session:
        patient = await session.get(Patient, patient_key)
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
