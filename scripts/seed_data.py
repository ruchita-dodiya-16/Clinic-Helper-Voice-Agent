"""Seed the database with realistic dummy data so the dashboard has something
to show while testing.

All patients use the same test phone number from TEST_PATIENT_PHONE env var,
per current testing setup — this is the only real, verified number available,
so it's what the automated retention call is set up to dial.

Configure the phone number in .env: TEST_PATIENT_PHONE=+your-number-here

Usage (with the venv active, Postgres up, migrations applied):
    python -m scripts.seed_data
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

from src import repository
from src.settings import TEST_PATIENT_PHONE

TEST_PHONE = TEST_PATIENT_PHONE


async def main() -> None:
    today = date.today()

    # Start clean so re-running the seed doesn't stack duplicate cases,
    # call logs, or appointments.
    await repository.reset_demo_data()

    # 1. Ruchita Dodiya — General Physician (routine check-up)
    await repository.upsert_patient_seen(TEST_PHONE, phone_number=TEST_PHONE, name="Ruchita Dodiya")
    await repository.update_patient_fields(
        TEST_PHONE,
        memory_summary="- Prefers afternoon appointments\n- History of high blood pressure, on medication\n- Annual check-up due",
        next_retention_due_date=today.isoformat(),
        retention_reason="Annual health check-up reminder",
        retention_status="pending",
    )
    await repository.add_case(
        patient_key=TEST_PHONE, phone_number=TEST_PHONE, patient_name="Ruchita Dodiya",
        room_name="seed-demo", case_type="appointment", title="High Blood Pressure - Annual Check-up",
        details={"health_issue": "High blood pressure management", "reason": "Annual check-up with Dr. Ananya Mehta (General Physician)", "doctor_name": "Dr. Ananya Mehta", "specialty": "General Physician"},
    )
    await repository.add_call_log(
        room_name="seed-call-1", patient_key=TEST_PHONE, phone_number=TEST_PHONE,
        direction="inbound", call_type="inbound_phone",
        started_at=f"{today.isoformat()}T09:12:00+00:00",
        ended_at=f"{today.isoformat()}T09:15:40+00:00",
        duration_seconds=220, outcome="answered",
        transcript_summary="Booked annual check-up with Dr. Ananya Mehta for blood pressure management.",
    )

    # 2. John Carter — Cardiologist (chest pain - urgent)
    john_key = "demo:john-carter"
    await repository.upsert_patient_seen(john_key, phone_number=TEST_PHONE, name="John Carter")
    await repository.update_patient_fields(
        john_key,
        memory_summary="- History of high cholesterol\n- Previous cardiac event\n- On beta-blockers",
        next_retention_due_date=(today + timedelta(days=3)).isoformat(),
        retention_reason="Post-cardiac event follow-up",
        retention_status="pending",
    )
    await repository.add_case(
        patient_key=john_key, phone_number=TEST_PHONE, patient_name="John Carter",
        room_name="seed-demo", case_type="escalation", title="Severe Chest Pain - Cardiology Urgent",
        details={"health_issue": "Severe chest pain with shortness of breath", "reason": "Urgent cardiology escalation with Dr. Vikram Rao", "doctor_name": "Dr. Vikram Rao", "specialty": "Cardiologist"},
        priority="urgent", status="new",
    )
    await repository.add_call_log(
        room_name="seed-call-2", patient_key=john_key, phone_number=TEST_PHONE,
        direction="inbound", call_type="inbound_phone",
        started_at=f"{today.isoformat()}T10:02:00+00:00",
        ended_at=f"{today.isoformat()}T10:06:12+00:00",
        duration_seconds=252, outcome="transferred",
        transcript_summary="Urgent escalation for severe chest pain, transferred to Dr. Vikram Rao (Cardiologist).",
    )

    # 3. Maria Lopez — Dentist (tooth pain)
    maria_key = "demo:maria-lopez"
    await repository.upsert_patient_seen(maria_key, phone_number=TEST_PHONE, name="Maria Lopez")
    await repository.update_patient_fields(
        maria_key,
        memory_summary="- Prefers morning appointments\n- Sensitive teeth\n- Regular dental care",
        next_retention_due_date=(today + timedelta(days=7)).isoformat(),
        retention_reason="Post-treatment dental follow-up and cleaning",
        retention_status="pending",
    )
    await repository.add_case(
        patient_key=maria_key, phone_number=TEST_PHONE, patient_name="Maria Lopez",
        room_name="seed-demo", case_type="appointment", title="Tooth Pain - Dental Treatment",
        details={"health_issue": "Severe tooth pain in upper left molar", "reason": "Dental consultation and possible root canal with Dr. Rohan Shah", "doctor_name": "Dr. Rohan Shah", "specialty": "Dentist"},
    )
    await repository.add_call_log(
        room_name="seed-call-3", patient_key=maria_key, phone_number=TEST_PHONE,
        direction="inbound", call_type="inbound_phone",
        started_at=f"{today.isoformat()}T08:40:00+00:00",
        ended_at=f"{today.isoformat()}T08:42:30+00:00",
        duration_seconds=150, outcome="answered",
        transcript_summary="Booked dental appointment with Dr. Rohan Shah for tooth pain treatment.",
    )

    # 4. Sarah Singh — Pediatrician (child checkup)
    sarah_key = "demo:sarah-singh"
    await repository.upsert_patient_seen(sarah_key, phone_number=TEST_PHONE, name="Sarah Singh")
    await repository.update_patient_fields(
        sarah_key,
        memory_summary="- Parent of 2-year-old child\n- Up-to-date with vaccinations\n- Monthly wellness checks",
        next_retention_due_date=(today + timedelta(days=5)).isoformat(),
        retention_reason="Child vaccination reminder and wellness check",
        retention_status="pending",
    )
    await repository.add_case(
        patient_key=sarah_key, phone_number=TEST_PHONE, patient_name="Sarah Singh",
        room_name="seed-demo", case_type="appointment", title="Child Vaccination - Pediatric Check-up",
        details={"health_issue": "Routine vaccination and child development checkup", "reason": "6-month wellness check with Dr. Kavita Iyer (Pediatrician)", "doctor_name": "Dr. Kavita Iyer", "specialty": "Pediatrician"},
    )
    await repository.add_call_log(
        room_name="seed-call-4", patient_key=sarah_key, phone_number=TEST_PHONE,
        direction="inbound", call_type="inbound_phone",
        started_at=f"{today.isoformat()}T11:30:00+00:00",
        ended_at=f"{today.isoformat()}T11:35:20+00:00",
        duration_seconds=320, outcome="answered",
        transcript_summary="Booked pediatric wellness check with Dr. Kavita Iyer for child vaccination and checkup.",
    )

    # 5. Robert Chen — General Physician (diabetes management)
    robert_key = "demo:robert-chen"
    await repository.upsert_patient_seen(robert_key, phone_number=TEST_PHONE, name="Robert Chen")
    await repository.update_patient_fields(
        robert_key,
        memory_summary="- Type 2 Diabetes patient\n- Monthly blood sugar monitoring\n- On Metformin and Insulin",
        next_retention_due_date=(today + timedelta(days=15)).isoformat(),
        retention_reason="Monthly diabetes check-in with Dr. Ananya Mehta",
        retention_status="scheduled",
    )
    await repository.add_case(
        patient_key=robert_key, phone_number=TEST_PHONE, patient_name="Robert Chen",
        room_name="seed-demo", case_type="appointment", title="Diabetes Management - Monthly Follow-up",
        details={"health_issue": "Type 2 Diabetes with elevated blood sugar levels", "reason": "Monthly diabetes management with Dr. Ananya Mehta", "doctor_name": "Dr. Ananya Mehta", "specialty": "General Physician"},
    )
    await repository.add_call_log(
        room_name="seed-call-5", patient_key=robert_key, phone_number=TEST_PHONE,
        direction="inbound", call_type="inbound_phone",
        started_at=f"{today.isoformat()}T14:15:00+00:00",
        ended_at=f"{today.isoformat()}T14:20:30+00:00",
        duration_seconds=330, outcome="answered",
        transcript_summary="Scheduled monthly diabetes follow-up with Dr. Ananya Mehta.",
    )

    # --- Real appointments (booked against actual doctor availability) ---
    # Uses the same greedy nearest-slot booker the agent uses, so these land on
    # valid, non-overlapping slots and show up in the dashboard Appointments tab.
    # Book each on a future date (a few days out) so they are genuinely
    # "upcoming" — the outbound reminder call needs something ahead to remind about.
    appointments_to_book = [
        (TEST_PHONE, "Ruchita Dodiya", "Dr. Ananya Mehta", "Annual blood pressure check-up", 2),
        (john_key, "John Carter", "Dr. Vikram Rao", "Cardiology follow-up for chest pain", 1),
        (maria_key, "Maria Lopez", "Dr. Rohan Shah", "Dental treatment for tooth pain", 3),
        (sarah_key, "Sarah Singh", "Dr. Kavita Iyer", "Child vaccination and wellness check", 4),
        (robert_key, "Robert Chen", "Dr. Ananya Mehta", "Monthly diabetes management review", 5),
    ]
    booked = []
    for patient_key, patient_name, doctor_name, reason, days_out in appointments_to_book:
        appt = await repository.find_and_book_appointment(
            patient_key=patient_key,
            patient_name=patient_name,
            reason=reason,
            doctor_name=doctor_name,
            preferred_date=(today + timedelta(days=days_out)).isoformat(),
        )
        if appt:
            booked.append((patient_name, doctor_name, appt.get("scheduled_at", "")))

    print("✅ Seed data inserted with realistic health issues:")
    print(f"  1. Ruchita Dodiya: High Blood Pressure Check-up → Dr. Ananya Mehta (General Physician)")
    print(f"  2. John Carter: Severe Chest Pain (URGENT) → Dr. Vikram Rao (Cardiologist)")
    print(f"  3. Maria Lopez: Tooth Pain Treatment → Dr. Rohan Shah (Dentist)")
    print(f"  4. Sarah Singh: Child Vaccination Check-up → Dr. Kavita Iyer (Pediatrician)")
    print(f"  5. Robert Chen: Diabetes Management → Dr. Ananya Mehta (General Physician)")
    print(f"\n📅 {len(booked)} appointment(s) booked:")
    for patient_name, doctor_name, scheduled_at in booked:
        when = scheduled_at[:16].replace("T", " ") if scheduled_at else "—"
        print(f"  • {patient_name} with {doctor_name} on {when}")
    print("\nOpen http://127.0.0.1:8000/dashboard to view cases and appointments!")


if __name__ == "__main__":
    asyncio.run(main())
