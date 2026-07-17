# Testing CareConnect Voice

All tests below assume the stack is running:

```bash
bash start.sh
```

This brings up Postgres, applies migrations, and starts the API, agent worker, and retention worker. Watch logs live with:

```bash
tail -f logs/api.log logs/agent.log logs/retention.log
```

Dummy data (6 patients covering every case type, phone number `+919664613050` for all of them) can be (re-)loaded any time with:

```bash
python -m scripts.seed_data
```

Doctors (needed for real appointment booking) are seeded with:

```bash
python -m scripts.seed_doctors
```

---

## 1. Dashboard (`/`)

**Test:** Open `http://127.0.0.1:8000/`.

**Expect:** KPI tiles (total patients, open escalations, callbacks pending, appointments today, retention due today, calls today, total cases), a case table with tabs (All/Callbacks/Escalations/Retention/FAQ/Feedback), a Doctors panel, an Appointments panel, a retention queue table, and a recent calls table. After running `scripts.seed_data` + `scripts.seed_doctors`, you should see 6 patients, 4 doctors, and one row in each remaining case-type tab. The page auto-refreshes every 10s — a manual **Refresh now** button is also there.

---

## 2. Patient call page (`/call`)

**Test:** Open `http://127.0.0.1:8000/call`, optionally type a name, click **Start call**, allow microphone access.

**Expect:** A short, fixed greeting, then a natural conversation. Try each of:
- "I'd like to book an appointment" → the agent finds a real doctor and open time slot automatically and confirms it back to you (e.g. "You're booked with Dr. Mehta on Friday at 2:00 PM"). Check `/` dashboard's **Appointments** panel for the new row with a real doctor and time — not just a logged request.
- "What are your clinic hours?" → answered from `data/clinic_knowledge_base.md`, logged under **FAQ**.
- "Can someone call me back?" → logged under **Callbacks**.
- "I have severe chest pain" → agent should decline to advise and say it's escalating → logged under **Escalations** with `priority: urgent`. If `STAFF_TRANSFER_NUMBER` is set, it also attempts a live transfer.
- "The clinic experience was great" → logged under **Feedback**.
- "I should come back for a check-up in 3 months" → logged under **Retention**, and `next_retention_due_date` gets set on the patient row.

**Memory test:** End the call, start a **second** call with the **same name**. Expect the agent to greet you as a returning caller ("Welcome back, ...") and skip straight to helping instead of asking what kind of caller you are.

---

## 3. New vs. returning caller behavior

**Test:** Call (browser or phone) with a name/number that has **never** called before.

**Expect:** The agent doesn't assume you're an existing patient — it asks what you need help with today before doing anything else.

**Test:** Call again with the **same** name/number.

**Expect:** The agent skips that framing entirely and goes straight to helping, referencing what it remembers from the first call if relevant.

---

## 4. Doctor-aware appointment booking (no double-booking)

**Test:** Book two appointments back-to-back, both without specifying a doctor, on the same day/time-of-day preference (e.g. both "tomorrow morning").

**Expect:** Each gets a real, different time slot — check `/api/appointments` or the dashboard's Appointments panel; the two `scheduled_at` values should never collide for the same doctor. If you ask for a specific doctor or specialty (e.g. "book me with the dentist"), the booking should land on that doctor specifically (see `/api/doctors` for the seeded names/specialties).

---

## 5. Real inbound phone call

**Test:** Call your Twilio number (`+18575780970` in this setup) from a verified phone.

**Expect:** Same behavior as the browser call — greeting, memory lookup by phone number, all tools work identically. Check `logs/agent.log` for `call_type=inbound_phone`, and `/api/call-log` (or the dashboard's **Recent calls** table) for a new row with `direction: inbound`.

---

## 6. Escalation live transfer

**Prerequisite:** `STAFF_TRANSFER_NUMBER` set in `.env` and services restarted.

**Test:** During a call, say something clearly urgent/clinical (e.g. "I think I'm having an allergic reaction").

**Expect:** The agent creates the escalation case, then dials `STAFF_TRANSFER_NUMBER` on a separate line, briefs whoever answers with the conversation so far, and only bridges you in once they say to. If `STAFF_TRANSFER_NUMBER` is empty, the escalation still logs — just without attempting a transfer (check `logs/agent.log` for the warning saying so).

---

## 7. Automated, time-based retention calling

This is the "call the patient automatically" feature — the retention worker polls `patients` on a timer and dials anyone due.

**Set up a due patient** (already done by `scripts.seed_data` for `+919664613050`), or manually:
```bash
docker exec -it healthcare-helper-mic-livekit-cartesia-anthropic-db-1 \
  psql -U careconnect -d careconnect -c \
  "UPDATE patients SET next_retention_due_date = CURRENT_DATE WHERE patient_key = '+919664613050';"
```

**Expect:** Within `RETENTION_POLL_MINUTES` (currently set to **3 minutes** for testing — see note below), `logs/retention.log` shows a dispatch, and `logs/agent.log` shows the outbound dial + an AMD prediction (`human` / `machine-vm` / `machine-unavailable` / `uncertain`). The patient's `retention_attempts` increments and `retention_status` becomes `contacted`. Verify via:
```bash
curl -s http://127.0.0.1:8000/api/retention-queue | python3 -m json.tool
```

**⚠️ Before going live:** `RETENTION_POLL_MINUTES=3` in `.env` right now is a testing convenience so you don't have to wait 30 minutes to see it fire. Set it back to something sane (e.g. `30`) once you're done testing, or it'll poll (and potentially dial) far more often than a real clinic would want.

---

## 8. Quiet hours / safety gates on retention calling

**Test:** Temporarily set `RETENTION_QUIET_HOURS_START`/`END` in `.env` to a window that excludes the current time, restart, and check `logs/retention.log`.

**Expect:** `"outside quiet hours (...), skipping poll"` — no call placed. Also test `do_not_call=true` on a patient row (via `psql`) and confirm the worker skips them even if due.

---

## 9. Verify storage end-to-end

```bash
docker exec -it healthcare-helper-mic-livekit-cartesia-anthropic-db-1 \
  psql -U careconnect -d careconnect -c "SELECT patient_key, name, retention_status FROM patients;"
docker exec -it healthcare-helper-mic-livekit-cartesia-anthropic-db-1 \
  psql -U careconnect -d careconnect -c "SELECT case_type, title, priority FROM cases ORDER BY id DESC LIMIT 10;"
```

**Expect:** Rows matching whatever you've tested above — this is the same data the dashboard reads.

---

## 10. One-off manual outbound test call (bypasses the scheduler)

```bash
python -m scripts.test_call +919664613050 "Test Name" "reason for the call"
```

**Expect:** Immediate dial-out through the same path as retention calls, without touching the `patients` table's attempt counters — useful for testing without disturbing real retention scheduling.
