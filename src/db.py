from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .settings import DB_PATH


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect() -> sqlite3.Connection:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_name TEXT UNIQUE NOT NULL,
                participant_name TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_name TEXT NOT NULL,
                task_type TEXT NOT NULL,
                title TEXT NOT NULL,
                details TEXT NOT NULL,
                priority TEXT NOT NULL DEFAULT 'normal',
                status TEXT NOT NULL DEFAULT 'new',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_name TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )


def upsert_session(room_name: str, participant_name: str, status: str = "active") -> None:
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO sessions(room_name, participant_name, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(room_name) DO UPDATE SET
                participant_name=excluded.participant_name,
                status=excluded.status,
                updated_at=excluded.updated_at
            """,
            (room_name, participant_name, status, now, now),
        )


def add_event(room_name: str, event_type: str, payload: dict[str, Any]) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO events(room_name, event_type, payload, created_at) VALUES (?, ?, ?, ?)",
            (room_name, event_type, json.dumps(payload), utc_now()),
        )


def add_task(
    *,
    room_name: str,
    task_type: str,
    title: str,
    details: dict[str, Any],
    priority: str = "normal",
) -> dict[str, Any]:
    now = utc_now()
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO tasks(room_name, task_type, title, details, priority, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'new', ?, ?)
            """,
            (room_name, task_type, title, json.dumps(details), priority, now, now),
        )
        task_id = int(cur.lastrowid)

    return {
        "id": task_id,
        "room_name": room_name,
        "task_type": task_type,
        "title": title,
        "details": details,
        "priority": priority,
        "status": "new",
        "created_at": now,
    }


def list_tasks(limit: int = 100) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        d = dict(row)
        d["details"] = json.loads(d["details"] or "{}")
        out.append(d)
    return out


def dashboard_summary() -> dict[str, Any]:
    with connect() as conn:
        sessions = conn.execute("SELECT COUNT(*) AS c FROM sessions").fetchone()["c"]
        tasks = conn.execute("SELECT COUNT(*) AS c FROM tasks").fetchone()["c"]
        escalations = conn.execute(
            "SELECT COUNT(*) AS c FROM tasks WHERE task_type='escalation'"
        ).fetchone()["c"]
        callbacks = conn.execute(
            "SELECT COUNT(*) AS c FROM tasks WHERE task_type='callback'"
        ).fetchone()["c"]
        appointments = conn.execute(
            "SELECT COUNT(*) AS c FROM tasks WHERE task_type='appointment'"
        ).fetchone()["c"]
    return {
        "sessions": sessions,
        "tasks": tasks,
        "appointments": appointments,
        "callbacks": callbacks,
        "escalations": escalations,
    }


init_db()
