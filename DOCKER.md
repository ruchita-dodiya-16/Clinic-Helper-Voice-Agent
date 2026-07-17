# Running with Docker

Every service runs in its own container from a single shared image. Postgres
runs as its own container with a persistent volume. Database migrations run
automatically before the app services start.

## Prerequisites

1. Docker Desktop (or Docker Engine + Compose v2) installed and running.
2. A filled-in `.env` in this folder (copy from `.env.example`). Required keys:
   - `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`
   - `ANTHROPIC_API_KEY`, `CARTESIA_API_KEY`
   - (optional) Twilio SIP vars for real phone calls, `TEST_PATIENT_PHONE`
   > You do NOT set `DATABASE_URL` in `.env` — Compose points the app at the
   > `db` container automatically.

## The services

| Service     | What it does                                   | Command it runs |
|-------------|------------------------------------------------|-----------------|
| `db`        | PostgreSQL 16 (data persisted in a volume)     | postgres |
| `migrate`   | Applies Alembic migrations, then exits         | `alembic upgrade head` |
| `api`       | Web dashboard + call UI on **http://localhost:8000** | `uvicorn src.api_server:app` |
| `agent`     | LiveKit voice agent worker (inbound/outbound)  | `python -m src.agent start` |
| `retention` | Automated re-engagement/outbound worker        | `python -m src.retention_worker` |
| `seed`      | One-off: seed demo doctors + patients (profile) | `seed_doctors && seed_data` |

## Common commands

Build and start **everything** (db → migrate → api + agent + retention):
```bash
docker compose up --build
```

Run **one service** (its dependencies — db + migrate — start automatically):
```bash
docker compose up api
docker compose up agent
docker compose up retention
```

Run in the background:
```bash
docker compose up -d --build
```

Seed demo data (run once, after the DB is up):
```bash
docker compose --profile seed up seed
```

View logs for a single service:
```bash
docker compose logs -f agent
docker compose logs -f api
```

Rebuild after code changes:
```bash
docker compose up --build api      # or agent / retention
```

Stop everything (keeps the database volume):
```bash
docker compose down
```

Stop and **wipe the database** too:
```bash
docker compose down -v
```

## Notes

- `agent` and `retention` reach LiveKit / Cartesia / Anthropic over the internet;
  they do not expose any ports.
- Only `api` publishes a port (`8000`). Postgres is published on `5433` for local
  inspection but the app talks to it internally on `5432`.
- Restarting a service never stacks duplicate workers the way running the scripts
  by hand did — Compose keeps exactly one container per service.
