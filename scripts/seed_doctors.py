"""Seed a starter list of doctors so appointment booking has real availability
to schedule against. Placeholder data — edit directly in Postgres (`doctors`
table) to match your actual clinic staff and hours.

Usage:
    python -m scripts.seed_doctors
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from src import db
from src.models import Doctor

DOCTORS = [
    dict(
        name="Dr. Ananya Mehta",
        specialty="General Physician",
        working_days="Mon,Tue,Wed,Thu,Fri",
        work_start="09:00",
        work_end="17:00",
        slot_minutes=30,
    ),
    dict(
        name="Dr. Rohan Shah",
        specialty="Dentist",
        working_days="Mon,Tue,Wed,Thu,Fri,Sat",
        work_start="10:00",
        work_end="18:00",
        slot_minutes=30,
    ),
    dict(
        name="Dr. Kavita Iyer",
        specialty="Pediatrician",
        working_days="Mon,Wed,Fri",
        work_start="09:00",
        work_end="13:00",
        slot_minutes=20,
    ),
    dict(
        name="Dr. Vikram Rao",
        specialty="Cardiologist",
        working_days="Tue,Thu,Sat",
        work_start="11:00",
        work_end="16:00",
        slot_minutes=45,
    ),
]


async def main() -> None:
    async with db.get_session() as session:
        existing = {row[0] for row in (await session.execute(select(Doctor.name))).all()}
        created = 0
        for entry in DOCTORS:
            if entry["name"] in existing:
                continue
            session.add(Doctor(active=True, **entry))
            created += 1
        await session.commit()
    print(f"Added {created} new doctor(s) ({len(DOCTORS) - created} already existed).")


if __name__ == "__main__":
    asyncio.run(main())
