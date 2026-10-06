"""Cola SQLite local compatible con el control plane original de DataVIZ."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from uuid import uuid4


class JobStatus(StrEnum):
    """Estados persistidos de un trabajo local."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Job:
    """Trabajo de migración leído desde la cola SQLite."""

    id: str
    source: Path
    output: Path
    name: str | None
    status: JobStatus
    created_at: str
    started_at: str | None
    finished_at: str | None
    error: str | None
    lease_id: str | None
    heartbeat_at: str | None
    lease_expires_at: str | None


class JobStore:
    """Persistencia SQLite para la ejecución local y compatible con CLI."""

    def __init__(self, database: Path) -> None:
        self.database = Path(database).resolve()

    def initialize(self) -> None:
        """Crea o migra el esquema de trabajos de forma idempotente."""
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS migration_jobs (
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    output TEXT NOT NULL,
                    name TEXT,
                    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    error TEXT,
                    lease_id TEXT,
                    heartbeat_at TEXT,
                    lease_expires_at TEXT
                )
                """
            )
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(migration_jobs)")
            }
            for column in ("lease_id", "heartbeat_at", "lease_expires_at"):
                if column not in columns:
                    connection.execute(f"ALTER TABLE migration_jobs ADD COLUMN {column} TEXT")

    def enqueue(self, source: Path, output: Path, name: str | None = None) -> Job:
        """Valida y encola un TWB/TWBX existente."""
        source = Path(source).resolve()
        if not source.is_file() or source.suffix.lower() not in {".twb", ".twbx"}:
            raise ValueError("source debe ser un archivo .twb o .twbx existente")
        job_id = str(uuid4())
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO migration_jobs (id, source, output, name, status, created_at) VALUES (?, ?, ?, ?, 'queued', ?)",
                (job_id, str(source), str(Path(output).resolve()), name, _utc_now()),
            )
        job = self.get(job_id)
        assert job is not None
        return job

    def claim_next(self, lease_seconds: int = 60) -> Job | None:
        """Reclama atómicamente el trabajo pendiente más antiguo."""
        _validate_lease_seconds(lease_seconds)
        claimed_at = datetime.now(UTC)
        lease_id = str(uuid4())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM migration_jobs WHERE status = 'queued' ORDER BY created_at, id LIMIT 1"
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            connection.execute(
                """UPDATE migration_jobs SET status='running', started_at=?, lease_id=?, heartbeat_at=?, lease_expires_at=?
                   WHERE id=? AND status='queued'""",
                (
                    claimed_at.isoformat(),
                    lease_id,
                    claimed_at.isoformat(),
                    (claimed_at + timedelta(seconds=lease_seconds)).isoformat(),
                    row["id"],
                ),
            )
        return self.get(row["id"])

    def heartbeat(self, job_id: str, lease_id: str, lease_seconds: int = 60) -> Job:
        """Renueva un lease activo y rechaza workers obsoletos."""
        _validate_lease_seconds(lease_seconds)
        now = datetime.now(UTC)
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE migration_jobs SET heartbeat_at=?, lease_expires_at=? WHERE id=? AND status='running' AND lease_id=?",
                (
                    now.isoformat(),
                    (now + timedelta(seconds=lease_seconds)).isoformat(),
                    job_id,
                    lease_id,
                ),
            )
        if cursor.rowcount != 1:
            raise ValueError(f"lease inválido para job {job_id}")
        job = self.get(job_id)
        assert job is not None
        return job

    def recover_expired(self, now: str | None = None) -> int:
        """Devuelve a la cola los leases vencidos."""
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE migration_jobs SET status='queued', started_at=NULL, lease_id=NULL,
                   heartbeat_at=NULL, lease_expires_at=NULL WHERE status='running' AND lease_expires_at < ?""",
                (now or _utc_now(),),
            )
        return cursor.rowcount

    def succeed(self, job_id: str, lease_id: str) -> Job:
        return self._finish(job_id, lease_id, JobStatus.SUCCEEDED, None)

    def fail(self, job_id: str, lease_id: str, error: str) -> Job:
        return self._finish(job_id, lease_id, JobStatus.FAILED, error[:2000])

    def get(self, job_id: str) -> Job | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM migration_jobs WHERE id=?", (job_id,)
            ).fetchone()
        return _row_to_job(row) if row else None

    def ready(self) -> bool:
        try:
            with self._connect() as connection:
                connection.execute("SELECT 1 FROM migration_jobs LIMIT 1")
            return True
        except sqlite3.Error:
            return False

    def _finish(self, job_id: str, lease_id: str, status: JobStatus, error: str | None) -> Job:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE migration_jobs SET status=?, finished_at=?, error=?, lease_expires_at=NULL WHERE id=? AND status='running' AND lease_id=?",
                (status.value, _utc_now(), error, job_id, lease_id),
            )
        if cursor.rowcount != 1:
            raise ValueError(f"lease inválido para job {job_id}")
        job = self.get(job_id)
        assert job is not None
        return job

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _validate_lease_seconds(lease_seconds: int) -> None:
    if lease_seconds <= 0:
        raise ValueError("lease_seconds debe ser mayor que cero")


def _row_to_job(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"],
        source=Path(row["source"]),
        output=Path(row["output"]),
        name=row["name"],
        status=JobStatus(row["status"]),
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        error=row["error"],
        lease_id=row["lease_id"],
        heartbeat_at=row["heartbeat_at"],
        lease_expires_at=row["lease_expires_at"],
    )
