# CareConnect Voice - Healthcare Helper

A voice agent for a clinic front desk: scheduling, FAQs, callbacks, clinical
escalation (with live transfer to staff), and automated retention/re-engagement
outreach. Talk to it from a browser microphone (for testing) or over a real
phone number (once Twilio + LiveKit SIP are configured). It remembers
returning callers — name and the important facts from past calls, not the
whole transcript.

- LiveKit room for realtime audio (browser mic or phone call via SIP)
- Cartesia STT for speech-to-text
- Anthropic Claude Haiku for response, tool routing, and call-memory summarization,
  tuned for low latency (dynamic endpointing, preemptive generation, prompt caching)
- Cartesia TTS for speech output
- FastAPI for the token/session API, staff dashboard, and static UI
- **Postgres** for all storage, managed with **SQLAlchemy (async) + Alembic migrations**
  (local via `docker-compose.yml` by default; point `DATABASE_URL` at a hosted
  instance for production)
- One shared Python virtual environment only: `.venv`

## Architecture

```text
Browser mic UI  ---\
                     -> LiveKit room -> LiveKit Agent worker -> Cartesia STT -> Claude Haiku -> Cartesia TTS -> audio out
Phone call (SIP) ---/                        |
                                              v
                                   Postgres: patients   (memory: name, condensed notes, retention due date)
                                             cases       (every appointment/callback/escalation/retention/faq/feedback)
                                             call_log    (call outcomes: answered/voicemail/no-answer/transferred)

src/retention_worker.py  -> polls patients for due/inactive patients -> dispatches the agent to place outbound calls

/            -> patient-facing browser mic demo
/dashboard   -> staff operations dashboard (live KPIs, case queue, retention queue, call log)
```

## Setup (browser mic only, no phone needed)

Requires [Docker](https://docs.docker.com/get-docker/) for the local Postgres database.

```bash
cd healthcare-helper-mic-livekit-cartesia-anthropic
bash start.sh
```

First run creates `.venv`, installs dependencies, and creates `.env` from
`.env.example`. Fill in at minimum:

```env
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=your_livekit_api_key
LIVEKIT_API_SECRET=your_livekit_api_secret
ANTHROPIC_API_KEY=your_anthropic_api_key
CARTESIA_API_KEY=your_cartesia_api_key
```

Then run again:

```bash
bash start.sh
```

This brings up Postgres (`docker compose up -d db`), applies migrations
(`alembic upgrade head`), then starts the API, agent worker, and retention
worker. Open `http://127.0.0.1:8000`, click **Start voice session**, allow
microphone access. This path works with no SIP/Twilio setup at all — useful
for testing memory, tools, and the dashboard before wiring up real phone
calls. Staff can view `http://127.0.0.1:8000/dashboard` at any time.

## Setup (real phone calling)

This needs a Twilio account (or your own SIP trunk provider) and a LiveKit
Cloud project with SIP enabled (on by default for LiveKit Cloud).

### 1. Twilio console (you do this — real account, real cost)

1. Buy a phone number in the Twilio Console (Phone Numbers -> Buy a number).
2. Create an **Elastic SIP Trunk** (Voice -> Elastic SIP Trunking -> Trunks -> Create new).
3. Under **Origination**, add an Origination URI pointing at your LiveKit
   project's SIP inbound address (find this in your LiveKit Cloud project's
   SIP settings — looks like `sip:<your-project>.sip.livekit.cloud`).
4. Under **Termination**, note the termination SIP URI (e.g.
   `your-trunk-name.pstn.twilio.com`) and set up either IP access control or
   a SIP username/password (Credential List) — this project uses
   username/password auth.
5. Assign your purchased number to the trunk.

### 2. Fill in `.env`

```env
TWILIO_PHONE_NUMBER=+1...
TWILIO_SIP_TERMINATION_URI=your-trunk-name.pstn.twilio.com
TWILIO_SIP_USERNAME=...
TWILIO_SIP_PASSWORD=...
```

### 3. Provision the LiveKit side (this project does this for you)

```bash
source .venv/bin/activate
python -m scripts.provision_sip
```

This is idempotent — safe to re-run. It creates a SIP inbound trunk, a SIP
outbound trunk (used for both retention calls and staff transfer), and a
dispatch rule that auto-routes inbound calls to the agent worker. Copy the
printed `SIP_INBOUND_TRUNK_ID` / `SIP_OUTBOUND_TRUNK_ID` into `.env`.

### 4. Restart services

```bash
bash start.sh
```

Call your Twilio number — the agent should answer.

### 5. (Optional) Live escalation transfer to staff

Set `STAFF_TRANSFER_NUMBER` in `.env` (E.164, e.g. `+14155551234`) and restart.
Until this is set, urgent escalations still log to the dashboard, they just
don't attempt a live transfer.

### 6. (Optional but on by default) Automated retention/re-engagement calls

The `retention` background service (started by `start.sh`) polls
`data/patients.csv` every `RETENTION_POLL_MINUTES` (default 30) during
`RETENTION_QUIET_HOURS_START`-`RETENTION_QUIET_HOURS_END` clinic-local time
(`CLINIC_TIMEZONE`), and places an outbound call to any patient whose
`next_retention_due_date` has arrived, or who's gone quiet for
`RETENTION_INACTIVE_DAYS` (default 180) with no reminder set. It stops after
`RETENTION_MAX_ATTEMPTS` (default 3), waiting `RETENTION_RETRY_DAYS` between
attempts, and skips any row with `do_not_call=true`.

Staff can queue a reminder by hand-editing `data/patients.csv`
(`next_retention_due_date`, `retention_reason`) — no code needed. The agent
also queues these itself during a call via its retention tool.

**This dials real people automatically.** Test it first with a row you
control: set `next_retention_due_date` to today for a test patient, watch
`logs/retention.log`.

## Storage: Postgres + SQLAlchemy + Alembic

Three tables, defined in `src/models.py`:

- **`patients`** — one row per patient (memory): name, condensed notes from
  past calls (`memory_summary`, capped ~800 chars, not a full transcript),
  retention due date/reason, attempt counts, `do_not_call`. Identity key
  (`patient_key`) is the caller's phone number for real calls, or
  `demo:<name>` for browser-mic sessions.
- **`cases`** — every appointment, callback, escalation, retention task, FAQ,
  and feedback item, across all callers (`details` is a JSONB column).
- **`call_log`** — one row per call with a best-effort outcome
  (`answered` / `voicemail_left` / `no_answer` / `uncertain` / `error`).
  Outbound answering-machine detection uses `livekit-agents`' built-in AMD
  (LLM-based greeting classification) — it is not perfect; treat `uncertain`
  outcomes as inconclusive.

`src/repository.py` is the only place that talks to the database — `agent.py`,
`api_server.py`, and `retention_worker.py` all go through it. Postgres handles
concurrent writes from all three processes natively (no manual locking needed).

Local Postgres runs via `docker-compose.yml` (port `5433` on the host, to
avoid clashing with any existing local Postgres on the default `5432`).
Schema changes go through Alembic:

```bash
# after changing src/models.py
python -m alembic revision --autogenerate -m "describe the change"
python -m alembic upgrade head
```

## Latency

The main design goal is a call that feels like talking to a person, not a
laggy bot. `src/agent.py:_make_session()`:

- **Dynamic endpointing** — the end-of-turn wait adapts to model confidence
  instead of always pausing a fixed delay.
- **Preemptive generation** (`preemptive_tts=True`) — the LLM/TTS start
  generating speculatively before end-of-turn is fully confirmed, so speech
  starts almost immediately once the caller stops talking.
- **Anthropic prompt caching** (`caching="ephemeral"`) — the system prompt
  (clinic KB + patient memory) is resent every turn; caching it cuts
  repeated-turn latency and cost.
- Claude Haiku (fastest Anthropic tier) with a tight `max_tokens=150`, since
  voice answers are already instructed to be 1-2 sentences.

Realistically this narrows the gap to a human call, it doesn't erase it —
there's still real STT/LLM/TTS round-trip time. If it still feels slow, check
`logs/agent.log` for which leg (STT/LLM/TTS) is the bottleneck before tuning
further.

## Staff dashboard

`http://127.0.0.1:8000/dashboard` — live KPI tiles (patients, open
escalations, callbacks pending, retention due today, calls today, total
cases), a filterable case queue, a retention queue, and a recent call log
with outcome badges. Auto-refreshes every 10s, plus a manual refresh button.
Separate from `/`, the patient-facing voice demo page.

## Safety behavior

The agent is a helper, not a doctor replacement. Every response is routed
through this order: blocked clinical topics -> scheduling -> retention ->
approved FAQ (from `data/clinic_knowledge_base.md`, edit that file to update
hours/pricing/policy) -> callback -> feedback.

Escalate or block: diagnosis, medication advice, dosage guidance, severe
symptoms, emergency issues, prescription guidance.

## Logs

```bash
tail -f logs/api.log logs/agent.log logs/retention.log
```

## Common issues

### Agent does not join

Check `.env` and make sure `AGENT_NAME` matches in both API and worker. It should be:

```env
AGENT_NAME=healthcare-helper-agent
```

### Browser cannot hear agent

Make sure the browser has microphone permission and the tab is not muted. Because the session starts from a click, browser autoplay should usually work.

### Python dependency failure

Use Python 3.11:

```bash
rm -rf .venv
PYTHON_BIN=python3.11 bash start.sh
```

### Inbound calls connect but the agent doesn't recognize the caller's number

`sip.phoneNumber` is the LiveKit SIP participant attribute this project reads
for caller ID. If your LiveKit/SIP provider version exposes it under a
different key, check `logs/agent.log` for the warning that logs the actual
participant attributes seen on a real call, and adjust `src/agent.py`
(`entrypoint`, the `inbound_phone` branch) accordingly.

### Retention calls aren't going out

Check `logs/retention.log` — it logs why each poll cycle skipped (outside
quiet hours, `SIP_OUTBOUND_TRUNK_ID` missing, or no patients currently due).

### Database connection errors / "role does not exist"

The Postgres container publishes on host port `5433` (not `5432`) specifically
to avoid clashing with any Postgres already running natively on your machine.
If you changed `DATABASE_URL` yourself and hit connection/auth errors, check
`lsof -nP -iTCP:5433 -sTCP:LISTEN` to confirm nothing else is bound there, and
that `docker compose ps` shows the `db` service healthy.
