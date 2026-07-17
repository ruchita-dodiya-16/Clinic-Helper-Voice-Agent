# Shared image for all Python services (api, agent, retention, migrate, seed).
# Each service runs the same image with a different command (see docker-compose.yml).
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Build deps for asyncpg/greenlet wheels + curl for healthchecks.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first so this layer caches unless requirements change.
COPY requirements.txt .
RUN pip install --upgrade pip setuptools wheel \
    && pip install -r requirements.txt

# App code.
COPY . .

# Default command is overridden per-service in docker-compose.yml.
CMD ["python", "-m", "uvicorn", "src.api_server:app", "--host", "0.0.0.0", "--port", "8000"]
