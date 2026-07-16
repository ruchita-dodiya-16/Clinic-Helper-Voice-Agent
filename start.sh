#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

LOG_DIR="$ROOT_DIR/logs"
mkdir -p "$LOG_DIR" "$ROOT_DIR/data"

PYTHON_BIN="${PYTHON_BIN:-python3.11}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  PYTHON_BIN="python3"
fi

if [ ! -d ".venv" ]; then
  echo "Creating one shared Python virtual environment at .venv"
  "$PYTHON_BIN" -m venv .venv
fi

source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt

if [ ! -f ".env" ]; then
  cp .env.example .env
  echo ""
  echo "Created .env from .env.example. Fill in your credentials, then run again:"
  echo "  LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET"
  echo "  ANTHROPIC_API_KEY, CARTESIA_API_KEY"
  echo ""
  exit 0
fi

# Basic credential check before starting long-running processes.
if grep -q "your_" .env; then
  echo "Your .env still contains placeholder values. Please edit .env first."
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required to run the local Postgres database (docker-compose.yml). Install Docker and try again."
  exit 1
fi

echo "Starting Postgres (docker compose)..."
docker compose up -d db

echo -n "Waiting for Postgres to be healthy"
for _ in $(seq 1 30); do
  status="$(docker inspect --format='{{.State.Health.Status}}' "$(docker compose ps -q db)" 2>/dev/null || echo "")"
  if [ "$status" = "healthy" ]; then
    echo " done"
    break
  fi
  echo -n "."
  sleep 2
done

echo "Applying database migrations (alembic upgrade head)..."
python -m alembic upgrade head

PIDS=()
cleanup() {
  echo ""
  echo "Stopping services..."
  for pid in "${PIDS[@]:-}"; do
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

start_service() {
  local name="$1"
  shift
  local log_file="$LOG_DIR/$name.log"
  echo "Starting $name... logs: $log_file"
  "$@" > "$log_file" 2>&1 &
  local pid=$!
  PIDS+=("$pid")
  echo "  $name PID: $pid"
}

start_service api python -m uvicorn src.api_server:app --host "${APP_HOST:-127.0.0.1}" --port "${APP_PORT:-8000}"
sleep 2
start_service agent python -m src.agent dev
start_service retention python -m src.retention_worker

cat <<MSG

All services started.

Open: http://${APP_HOST:-127.0.0.1}:${APP_PORT:-8000}

Browser microphone UI works immediately. Real phone calling and automated
retention outreach need Twilio + LiveKit SIP configured first (see README.md).
Press Ctrl+C to stop everything.

MSG

tail -n 60 -f "$LOG_DIR/api.log" "$LOG_DIR/agent.log" "$LOG_DIR/retention.log" &
PIDS+=("$!")
wait
