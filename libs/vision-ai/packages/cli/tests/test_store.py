# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The CLI's SqliteJobStore: upsert, get, list by type/status, ordering."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from pathlib import Path
import subprocess
import sys

from nvidia_vision_ai.memory import JobError
from nvidia_vision_ai.memory import JobQuery
from nvidia_vision_ai.memory import JobRecord
from nvidia_vision_ai.memory import JobStore
from nvidia_vision_ai.memory import new_job_id
from nvidia_vision_ai_cli.store import SqliteJobStore

T0 = datetime(2026, 10, 14, tzinfo=UTC)


def rec(job_type: str, minutes: int, status: str = "completed") -> JobRecord:
    return JobRecord(
        job_id=new_job_id(job_type),
        type=job_type,
        status=status,  # type: ignore[arg-type]
        created_at=T0 + timedelta(minutes=minutes),
        input={"prompt": "q"},
        result={"answer": "a"} if status == "completed" else None,
        error=JobError(code="backend_error", message="x") if status == "failed" else None,
    )


def test_store_satisfies_protocol_and_creates_parent(tmp_path: Path) -> None:
    store = SqliteJobStore(tmp_path / "memory" / "main.db")
    assert isinstance(store, JobStore)
    assert store.path.exists()


def test_put_is_upsert_and_get_round_trips(tmp_path: Path) -> None:
    store = SqliteJobStore(tmp_path / "main.db")
    running = rec("vlm", 0, "running")
    store.put(running)
    done = running.model_copy(update={"status": "completed", "result": {"answer": "a"}, "finished_at": T0})
    store.put(done)
    assert store.get(running.job_id) == done
    assert store.get("vlm-missing") is None
    assert len(store.list(JobQuery())) == 1


def test_list_filters_and_orders_newest_first(tmp_path: Path) -> None:
    store = SqliteJobStore(tmp_path / "main.db")
    old, new, failed, other = rec("vlm", 1), rec("vlm", 5), rec("vlm", 3, "failed"), rec("search", 9)
    for record in (old, new, failed, other):
        store.put(record)
    assert [r.job_id for r in store.list(JobQuery(type="vlm"))] == [new.job_id, failed.job_id, old.job_id]
    assert [r.job_id for r in store.list(JobQuery(type="vlm", status="failed"))] == [failed.job_id]
    assert [r.job_id for r in store.list(JobQuery(limit=2))] == [other.job_id, new.job_id]


def test_store_reopens_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "main.db"
    record = rec("vlm", 0)
    SqliteJobStore(path).put(record)
    assert SqliteJobStore(path).get(record.job_id) == record


def test_mixed_offsets_sort_chronologically(tmp_path: Path) -> None:
    store = SqliteJobStore(tmp_path / "main.db")
    earlier = rec("vlm", 0).model_copy(
        update={"created_at": datetime(2026, 10, 14, 10, tzinfo=timezone(timedelta(hours=2)))}
    )
    later = rec("vlm", 0).model_copy(update={"created_at": datetime(2026, 10, 14, 9, tzinfo=UTC)})
    store.put(JobRecord.model_validate(earlier.model_dump()))
    store.put(JobRecord.model_validate(later.model_dump()))
    assert [r.job_id for r in store.list(JobQuery())] == [later.job_id, earlier.job_id]
    assert store.get(earlier.job_id).created_at.tzinfo == UTC


def test_new_store_files_are_owner_only(tmp_path: Path) -> None:
    store = SqliteJobStore(tmp_path / "memory" / "main.db")
    assert store.path.stat().st_mode & 0o777 == 0o600
    assert store.path.parent.stat().st_mode & 0o777 == 0o700


_HAMMER = """
import sys
from datetime import UTC, datetime
from pathlib import Path
from nvidia_vision_ai.memory import JobRecord, new_job_id
from nvidia_vision_ai_cli.store import SqliteJobStore

store = SqliteJobStore(Path(sys.argv[1]))
for _ in range(20):
    record = JobRecord(job_id=new_job_id("vlm"), type="vlm", status="running", created_at=datetime.now(UTC))
    store.put(record)
    store.put(record.model_copy(update={"status": "completed", "result": {"w": sys.argv[2]}}))
"""


def test_concurrent_processes_on_a_fresh_db(tmp_path: Path) -> None:
    # Separate interpreters, all racing to create the schema and switch to WAL.
    path = tmp_path / "fresh" / "main.db"
    procs = [
        subprocess.Popen([sys.executable, "-c", _HAMMER, str(path), str(w)], stderr=subprocess.PIPE) for w in range(8)
    ]
    errors = [p.communicate(timeout=120)[1].decode() for p in procs]
    assert [p.returncode for p in procs] == [0] * 8, errors
    jobs = SqliteJobStore(path).list(JobQuery(limit=10_000))
    assert len(jobs) == 160
    assert {job.status for job in jobs} == {"completed"}
