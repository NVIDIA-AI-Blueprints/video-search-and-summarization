"""SQLite-backed run store.

The pre-0.2 reference kept runs in process dicts, which meant a restart (or
a second replica) lost every run. This store keeps the exact same interface
the routers/runners already use — live `RunRecord` objects mutated in place —
and adds write-through persistence to SQLite (WAL mode) so run state,
events, logs, results, and idempotency keys survive restarts.

Model:
  * A process-local cache of live RunRecord objects is the working set
    (the routers hold references and mutate them, as before).
  * `save(record)` serializes the record to the `runs` table. The runner
    calls it after every stage transition; routers call it after any direct
    mutation (cancel/resume).
  * On startup, `recover_orphans()` loads persisted runs and marks any that
    were mid-flight (`pending`/`running`) as failed — the asyncio task that
    was driving them died with the previous process. Resume-from-disk of a
    half-finished pipeline is future work (the on-disk run directory keeps
    every completed stage's artifacts, so nothing is lost).

Scale-out note: SQLite is deliberate for the single-node blueprint. For
multi-replica deployments swap this module for a Postgres/Redis-backed
implementation — the routers only see `RunStore`.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID, uuid4

from app.config import DB_PATH
from app.schemas.config_models import ExperimentConfig
from app.schemas.fine_tune_models import (
    Artifact,
    BackendInfo,
    IterationMetrics,
    Progress,
    ResultsResponse,
    RunError,
    RunState,
    StageEvent,
    StepLog,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class RunRecord:
    id: UUID
    created_by: str
    created_at: datetime
    workflow: str
    resolved_config: ExperimentConfig
    state: RunState = "pending"
    backend: BackendInfo = field(default_factory=lambda: BackendInfo(type="docker", run_id=None))
    progress: Progress = field(
        default_factory=lambda: Progress(current_iteration=1, total_iterations=1, total_steps=1)
    )
    error: RunError | None = None
    convergence_reason: str | None = None

    events: list[StageEvent] = field(default_factory=list)
    logs: list[StepLog] = field(default_factory=list)
    per_iteration_metrics: list[IterationMetrics] = field(default_factory=list)
    results: ResultsResponse | None = None
    runner_crash: str | None = None

    next_step_index: int = 0
    cancel_requested: bool = False
    run_dir: str | None = None  # real-mode artifact directory on disk
    # Temporary object-store credentials from the request. IN-MEMORY ONLY —
    # to_json()'s allow-list deliberately excludes it (never persist creds);
    # a run resumed after restart re-fails at materialization if it needed them.
    storage_access: object | None = field(default=None, repr=False)
    task: asyncio.Task | None = field(default=None, repr=False)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)

    def next_seq(self) -> int:
        return len(self.events) + 1

    def artifacts(self, storage_root: str) -> list[Artifact]:
        # Real runs point at the on-disk run directory; simulated runs keep
        # the synthetic storage_root-based URIs so the contract shape holds.
        base = f"file://{self.run_dir}" if self.run_dir else f"{storage_root}/deft_experiments/{self.id}"
        now = utcnow()
        artifacts: list[Artifact] = []
        if self.events:
            artifacts.append(Artifact(name="state_snapshot", uri=f"{base}/deft_state.json", updated_at=now, size_bytes=None))
            artifacts.append(Artifact(name="event_log", uri=f"{base}/loop_log.jsonl", updated_at=now, size_bytes=None))
            artifacts.append(Artifact(name="report_html", uri=f"{base}/DEFT_Loop_Report.html", updated_at=now, size_bytes=None))
        if self.state == "completed":
            artifacts.append(Artifact(name="best_model_metadata", uri=f"{base}/best_model.json", updated_at=now, size_bytes=None))
            artifacts.append(
                Artifact(
                    name="best_model_inference_spec",
                    uri=f"{base}/best_model_inference_spec.yaml",
                    updated_at=now,
                    size_bytes=None,
                )
            )
        return artifacts

    # -- serialization -----------------------------------------------------
    def to_json(self) -> str:
        return json.dumps(
            {
                "id": str(self.id),
                "created_by": self.created_by,
                "created_at": self.created_at.isoformat(),
                "workflow": self.workflow,
                "resolved_config": self.resolved_config.model_dump(mode="json"),
                "state": self.state,
                "backend": self.backend.model_dump(mode="json"),
                "progress": self.progress.model_dump(mode="json"),
                "error": self.error.model_dump(mode="json") if self.error else None,
                "convergence_reason": self.convergence_reason,
                "events": [e.model_dump(mode="json") for e in self.events],
                "logs": [l.model_dump(mode="json") for l in self.logs],
                "per_iteration_metrics": [m.model_dump(mode="json") for m in self.per_iteration_metrics],
                "results": self.results.model_dump(mode="json") if self.results else None,
                "runner_crash": self.runner_crash,
                "next_step_index": self.next_step_index,
                "cancel_requested": self.cancel_requested,
                "run_dir": self.run_dir,
            }
        )

    @staticmethod
    def from_json(blob: str) -> "RunRecord":
        d = json.loads(blob)
        return RunRecord(
            id=UUID(d["id"]),
            created_by=d["created_by"],
            created_at=datetime.fromisoformat(d["created_at"]),
            workflow=d["workflow"],
            resolved_config=ExperimentConfig.model_validate(d["resolved_config"]),
            state=d["state"],
            backend=BackendInfo.model_validate(d["backend"]),
            progress=Progress.model_validate(d["progress"]),
            error=RunError.model_validate(d["error"]) if d.get("error") else None,
            convergence_reason=d.get("convergence_reason"),
            events=[StageEvent.model_validate(e) for e in d.get("events", [])],
            logs=[StepLog.model_validate(l) for l in d.get("logs", [])],
            per_iteration_metrics=[IterationMetrics.model_validate(m) for m in d.get("per_iteration_metrics", [])],
            results=ResultsResponse.model_validate(d["results"]) if d.get("results") else None,
            runner_crash=d.get("runner_crash"),
            next_step_index=d.get("next_step_index", 0),
            cancel_requested=d.get("cancel_requested", False),
            run_dir=d.get("run_dir"),
        )


@dataclass
class IdempotencyEntry:
    body_hash: str
    run_id: UUID


class RunStore:
    def __init__(self, db_path: str = DB_PATH) -> None:
        self._runs: dict[UUID, RunRecord] = {}
        self._db_lock = threading.Lock()
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS runs ("
            " id TEXT PRIMARY KEY, created_by TEXT NOT NULL, state TEXT NOT NULL,"
            " created_at TEXT NOT NULL, blob TEXT NOT NULL)"
        )
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS idempotency ("
            " key TEXT PRIMARY KEY, body_hash TEXT NOT NULL, run_id TEXT NOT NULL)"
        )
        self._db.commit()
        self._load_all()

    # -- persistence internals ---------------------------------------------
    def _load_all(self) -> None:
        for (blob,) in self._db.execute("SELECT blob FROM runs"):
            try:
                record = RunRecord.from_json(blob)
            except Exception:  # a corrupt row must not brick the service
                continue
            self._runs[record.id] = record

    def save(self, record: RunRecord) -> None:
        """Write-through persist. Call after any mutation worth surviving a restart."""
        blob = record.to_json()
        with self._db_lock:
            self._db.execute(
                "INSERT INTO runs (id, created_by, state, created_at, blob) VALUES (?,?,?,?,?)"
                " ON CONFLICT(id) DO UPDATE SET state=excluded.state, blob=excluded.blob",
                (str(record.id), record.created_by, record.state, record.created_at.isoformat(), blob),
            )
            self._db.commit()

    def recover_orphans(self) -> int:
        """Mark runs that were mid-flight when the previous process died as failed."""
        n = 0
        for record in self._runs.values():
            if record.state in ("pending", "running"):
                record.state = "failed"
                record.error = RunError(
                    step=record.progress.current_step or "service",
                    exit_code=-1,
                    message="service restarted while the run was executing; resume to retry",
                    logs="",
                )
                self.save(record)
                n += 1
        return n

    # -- public interface (unchanged shape) ----------------------------------
    def create(self, created_by: str, workflow: str, resolved_config: ExperimentConfig) -> RunRecord:
        record = RunRecord(
            id=uuid4(),
            created_by=created_by,
            created_at=utcnow(),
            workflow=workflow,
            resolved_config=resolved_config,
        )
        self._runs[record.id] = record
        self.save(record)
        return record

    def get(self, run_id: UUID) -> RunRecord | None:
        return self._runs.get(run_id)

    def list(
        self,
        *,
        owner: str | None,
        state: RunState | None,
        limit: int,
        offset: int,
    ) -> tuple[list[RunRecord], int]:
        records = list(self._runs.values())
        if owner is not None:
            records = [r for r in records if r.created_by == owner]
        if state is not None:
            records = [r for r in records if r.state == state]
        records.sort(key=lambda r: r.created_at, reverse=True)
        total = len(records)
        return records[offset : offset + limit], total

    def delete(self, run_id: UUID) -> RunRecord | None:
        record = self._runs.pop(run_id, None)
        if record:
            record.cancel_event.set()
            if record.task and not record.task.done():
                record.task.cancel()
            with self._db_lock:
                self._db.execute("DELETE FROM runs WHERE id=?", (str(run_id),))
                self._db.commit()
        return record

    def bulk_delete(
        self,
        *,
        owner: str | None,
        state: RunState | None,
        older_than: datetime | None,
    ) -> dict[str, int]:
        to_delete = [
            r
            for r in self._runs.values()
            if (owner is None or r.created_by == owner)
            and (state is None or r.state == state)
            and (older_than is None or r.created_at < older_than)
        ]
        counts: dict[str, int] = {}
        for record in to_delete:
            counts[record.state] = counts.get(record.state, 0) + 1
            self.delete(record.id)
        return counts

    def get_idempotent(self, key: str) -> IdempotencyEntry | None:
        row = self._db.execute("SELECT body_hash, run_id FROM idempotency WHERE key=?", (key,)).fetchone()
        if row is None:
            return None
        return IdempotencyEntry(body_hash=row[0], run_id=UUID(row[1]))

    def put_idempotent(self, key: str, body_hash: str, run_id: UUID) -> None:
        with self._db_lock:
            self._db.execute(
                "INSERT OR REPLACE INTO idempotency (key, body_hash, run_id) VALUES (?,?,?)",
                (key, body_hash, str(run_id)),
            )
            self._db.commit()


run_store = RunStore()
