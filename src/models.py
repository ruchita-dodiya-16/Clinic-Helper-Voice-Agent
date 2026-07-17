from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Patient(Base):
    __tablename__ = "patients"

    patient_key: Mapped[str] = mapped_column(String, primary_key=True)
    phone_number: Mapped[str] = mapped_column(String, default="", server_default="")
    name: Mapped[str] = mapped_column(String, default="", server_default="")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    total_calls: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    memory_summary: Mapped[str] = mapped_column(Text, default="", server_default="")
    next_retention_due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    retention_reason: Mapped[str] = mapped_column(String, default="", server_default="")
    retention_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_retention_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    do_not_call: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    retention_status: Mapped[str] = mapped_column(String, default="", server_default="")


class Case(Base):
    __tablename__ = "cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    patient_key: Mapped[str] = mapped_column(ForeignKey("patients.patient_key"))
    phone_number: Mapped[str] = mapped_column(String, default="", server_default="")
    patient_name: Mapped[str] = mapped_column(String, default="", server_default="")
    room_name: Mapped[str] = mapped_column(String, default="", server_default="")
    case_type: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(String)
    details: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    priority: Mapped[str] = mapped_column(String, default="normal", server_default="normal")
    status: Mapped[str] = mapped_column(String, default="new", server_default="new")
    outcome_tag: Mapped[str] = mapped_column(String, default="", server_default="")


class CallLog(Base):
    __tablename__ = "call_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    room_name: Mapped[str] = mapped_column(String, default="", server_default="")
    patient_key: Mapped[str] = mapped_column(ForeignKey("patients.patient_key"))
    phone_number: Mapped[str] = mapped_column(String, default="", server_default="")
    direction: Mapped[str] = mapped_column(String)
    call_type: Mapped[str] = mapped_column(String)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    outcome: Mapped[str] = mapped_column(String, default="", server_default="")
    transcript_summary: Mapped[str] = mapped_column(Text, default="", server_default="")


class Doctor(Base):
    __tablename__ = "doctors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String)
    specialty: Mapped[str] = mapped_column(String, default="", server_default="")
    working_days: Mapped[str] = mapped_column(
        String, default="Mon,Tue,Wed,Thu,Fri", server_default="Mon,Tue,Wed,Thu,Fri"
    )
    work_start: Mapped[str] = mapped_column(String, default="09:00", server_default="09:00")
    work_end: Mapped[str] = mapped_column(String, default="17:00", server_default="17:00")
    slot_minutes: Mapped[int] = mapped_column(Integer, default=30, server_default="30")
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class Appointment(Base):
    __tablename__ = "appointments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    patient_key: Mapped[str] = mapped_column(ForeignKey("patients.patient_key"))
    patient_name: Mapped[str] = mapped_column(String, default="", server_default="")
    doctor_id: Mapped[int] = mapped_column(ForeignKey("doctors.id"))
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    duration_minutes: Mapped[int] = mapped_column(Integer, default=30, server_default="30")
    reason: Mapped[str] = mapped_column(String, default="", server_default="")
    status: Mapped[str] = mapped_column(String, default="booked", server_default="booked")
