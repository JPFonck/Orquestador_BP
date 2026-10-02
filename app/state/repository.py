"""Persistencia del estado de cada sesión de onboarding.

El orquestador guarda el estado después de cada paso, así una sesión puede reanudarse
(p. ej. cuando el cliente aporta evidencia o un revisor resuelve un escalamiento).
"""

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Protocol

from app.state.models import OnboardingState, utcnow


class StateRepository(Protocol):
    def get(self, session_id: str) -> OnboardingState | None: ...

    def save(self, state: OnboardingState) -> OnboardingState: ...


def _touch(state: OnboardingState) -> None:
    state.version += 1
    state.updated_at = utcnow()


class InMemoryStateRepository:
    def __init__(self) -> None:
        self._items: dict[str, str] = {}
        self._lock = threading.Lock()

    def get(self, session_id: str) -> OnboardingState | None:
        with self._lock:
            raw = self._items.get(session_id)
        # Se guarda serializado para devolver copias y no compartir referencias mutables.
        return OnboardingState.model_validate_json(raw) if raw else None

    def save(self, state: OnboardingState) -> OnboardingState:
        _touch(state)
        with self._lock:
            self._items[state.session_id] = state.model_dump_json()
        return state


class SQLiteStateRepository:
    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS onboarding_sessions (
                    session_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    current_step TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    state_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._path)
        try:
            with conn:  # commit / rollback
                yield conn
        finally:
            conn.close()

    def get(self, session_id: str) -> OnboardingState | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT state_json FROM onboarding_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        return OnboardingState.model_validate_json(row[0]) if row else None

    def save(self, state: OnboardingState) -> OnboardingState:
        _touch(state)
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO onboarding_sessions (session_id, status, current_step, version, state_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    status = excluded.status,
                    current_step = excluded.current_step,
                    version = excluded.version,
                    state_json = excluded.state_json,
                    updated_at = excluded.updated_at
                """,
                (
                    state.session_id,
                    state.status.value,
                    state.current_step.value,
                    state.version,
                    state.model_dump_json(),
                    state.updated_at.isoformat(),
                ),
            )
        return state


def build_repository(backend: str, sqlite_path: str) -> StateRepository:
    if backend == "sqlite":
        return SQLiteStateRepository(sqlite_path)
    return InMemoryStateRepository()
