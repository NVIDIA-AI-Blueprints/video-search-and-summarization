from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import JSONResponse, Response, StreamingResponse

from app.auth import Principal, get_principal, require_owner_or_admin, resolve_owner_filter
from app.config import BACKEND_TYPE, EXECUTION_MODE, STORAGE_ROOT
from app.errors import idempotency_key_conflict, invalid_request, run_not_completed, run_not_found
from app.resolve import resolve_experiment_config
from app.runner import execute_run
from app.schemas.fine_tune_models import (
    ArtifactsResponse,
    EventsResponse,
    FineTuneListResponse,
    FineTuneRequest,
    FineTuneResponse,
    LogsResponse,
    ResultsResponse,
    RunDetailsResponse,
    RunState,
    RunStatus,
)
from app.store import RunRecord, run_store

router = APIRouter(tags=["fine-tune"], dependencies=[Depends(get_principal)])

TERMINAL_STATES: set[RunState] = {"completed", "failed", "cancelled"}
RESUMABLE_STATES: set[RunState] = {"failed", "cancelled"}
SSE_POLL_SECONDS = 0.2


def _to_run_status(record: RunRecord) -> RunStatus:
    return RunStatus(
        id=record.id,
        created_by=record.created_by,
        state=record.state,
        created_at=record.created_at,
        progress=record.progress,
        backend=record.backend,
        error=record.error,
        convergence_reason=record.convergence_reason,
    )


def _to_run_details(record: RunRecord) -> RunDetailsResponse:
    status = _to_run_status(record)
    return RunDetailsResponse(
        **status.model_dump(),
        per_iteration_metrics=list(record.per_iteration_metrics),
        resolved_config=record.resolved_config,
    )


def _get_owned_run(run_id: UUID, principal: Principal) -> RunRecord:
    record = run_store.get(run_id)
    if record is None:
        raise run_not_found(str(run_id))
    require_owner_or_admin(principal, record.created_by, str(run_id))
    return record


def _parse_older_than(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise invalid_request(f"'older_than' is not a valid ISO-8601 timestamp: {value!r}", ["query", "older_than"]) from exc


def _body_hash(req: FineTuneRequest) -> str:
    canonical = json.dumps(req.model_dump(mode="json"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@router.post("/v1/fine-tune")
async def create_fine_tune(
    req: FineTuneRequest,
    principal: Principal = Depends(get_principal),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> JSONResponse:
    body_hash = _body_hash(req)

    if idempotency_key:
        existing = run_store.get_idempotent(idempotency_key)
        if existing is not None:
            if existing.body_hash != body_hash:
                raise idempotency_key_conflict(idempotency_key)
            record = run_store.get(existing.run_id)
            if record is not None:
                return JSONResponse(
                    status_code=200,
                    content=FineTuneResponse(id=record.id, state=record.state).model_dump(mode="json"),
                )

    workflow_name, resolved_config = resolve_experiment_config(req)

    if EXECUTION_MODE == "real":
        # Real execution is implemented for the AOI workflow on the docker
        # backend; reject anything else at submission time instead of failing
        # minutes into a run.
        if workflow_name != "aoi_active_learning":
            raise invalid_request(
                f"DEFT_EXECUTION_MODE=real supports workflow 'aoi_active_learning' only "
                f"(got {workflow_name!r}); use simulate mode for contract testing.",
                ["body", "workflow"],
            )
        if BACKEND_TYPE != "docker":
            raise invalid_request(
                f"DEFT_EXECUTION_MODE=real is implemented for the docker backend only "
                f"(service is configured with DEFT_BACKEND_TYPE={BACKEND_TYPE!r}).",
                ["body"],
            )

    record = run_store.create(created_by=principal.subject, workflow=workflow_name, resolved_config=resolved_config)
    # In-memory only (see RunRecord.storage_access): temporary object-store
    # credentials for materializing s3:// references in real mode.
    record.storage_access = req.storage_access
    record.task = asyncio.create_task(execute_run(record))

    if idempotency_key:
        run_store.put_idempotent(idempotency_key, body_hash, record.id)

    return JSONResponse(
        status_code=201,
        content=FineTuneResponse(id=record.id, state=record.state).model_dump(mode="json"),
    )


@router.get("/v1/fine-tune", response_model=FineTuneListResponse)
async def list_fine_tunes(
    principal: Principal = Depends(get_principal),
    state: RunState | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    owner: str | None = Query(default=None),
) -> FineTuneListResponse:
    owner_filter = resolve_owner_filter(principal, owner)
    records, total = run_store.list(owner=owner_filter, state=state, limit=limit, offset=offset)
    return FineTuneListResponse(items=[_to_run_status(r) for r in records], total=total, limit=limit, offset=offset)


@router.delete("/v1/fine-tune")
async def bulk_delete_fine_tune(
    principal: Principal = Depends(get_principal),
    state: RunState | None = Query(default=None),
    older_than: str | None = Query(default=None),
    keep_artifacts: bool = Query(default=False),
    confirm: bool = Query(...),
) -> dict[str, int]:
    if not confirm:
        raise invalid_request("'confirm' must be true.", ["query", "confirm"])
    older_than_dt = _parse_older_than(older_than)
    # Non-admin callers may only ever bulk-delete their own runs, regardless
    # of the state/older_than filters supplied.
    owner_filter = None if principal.is_admin else principal.subject
    return run_store.bulk_delete(owner=owner_filter, state=state, older_than=older_than_dt)


@router.get("/v1/fine-tune/{id}", response_model=RunDetailsResponse)
async def get_fine_tune_details(id: UUID, principal: Principal = Depends(get_principal)) -> RunDetailsResponse:
    record = _get_owned_run(id, principal)
    return _to_run_details(record)


@router.delete("/v1/fine-tune/{id}", status_code=204)
async def delete_fine_tune(
    id: UUID,
    keep_artifacts: bool = Query(default=False),
    principal: Principal = Depends(get_principal),
) -> Response:
    _get_owned_run(id, principal)
    run_store.delete(id)
    return Response(status_code=204)


@router.get("/v1/fine-tune/{id}/status", response_model=RunStatus)
async def get_fine_tune_status(id: UUID, principal: Principal = Depends(get_principal)) -> RunStatus:
    record = _get_owned_run(id, principal)
    return _to_run_status(record)


@router.get("/v1/fine-tune/{id}/results", response_model=ResultsResponse)
async def get_fine_tune_results(id: UUID, principal: Principal = Depends(get_principal)) -> ResultsResponse:
    record = _get_owned_run(id, principal)
    if record.state != "completed" or record.results is None:
        raise run_not_completed(str(id), record.state)
    return record.results


@router.get("/v1/fine-tune/{id}/logs", response_model=LogsResponse)
async def get_fine_tune_logs(
    id: UUID,
    principal: Principal = Depends(get_principal),
    iteration: int | None = Query(default=None, ge=1),
    step: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> LogsResponse:
    record = _get_owned_run(id, principal)
    filtered = [
        log
        for log in record.logs
        if (iteration is None or log.iteration == iteration) and (step is None or log.step == step)
    ]
    page = filtered[offset : offset + limit]
    return LogsResponse(id=record.id, logs=page, runner_crash=record.runner_crash)


@router.get("/v1/fine-tune/{id}/events", response_model=EventsResponse)
async def get_fine_tune_events(
    id: UUID,
    principal: Principal = Depends(get_principal),
    after_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=1000),
) -> EventsResponse:
    record = _get_owned_run(id, principal)
    filtered = [e for e in record.events if e.seq > after_seq][:limit]
    last_seq = record.events[-1].seq if record.events else 0
    cursor = filtered[-1].seq if filtered else after_seq
    next_seq = None if (record.state in TERMINAL_STATES and cursor >= last_seq) else cursor
    return EventsResponse(id=record.id, events=filtered, next_seq=next_seq)


@router.get("/v1/fine-tune/{id}/events/stream")
async def stream_fine_tune_events(
    id: UUID,
    principal: Principal = Depends(get_principal),
    after_seq: int = Query(default=0, ge=0),
) -> StreamingResponse:
    _get_owned_run(id, principal)

    async def event_source():
        cursor = after_seq
        while True:
            record = run_store.get(id)
            if record is None:
                return
            for event in record.events:
                if event.seq > cursor:
                    cursor = event.seq
                    yield f"data: {json.dumps(event.model_dump(mode='json'))}\n\n"
            if record.state in TERMINAL_STATES:
                yield f"event: done\ndata: {json.dumps({'state': record.state})}\n\n"
                return
            await asyncio.sleep(SSE_POLL_SECONDS)

    return StreamingResponse(event_source(), media_type="text/event-stream")


@router.get("/v1/fine-tune/{id}/logs/stream")
async def stream_fine_tune_logs(
    id: UUID,
    principal: Principal = Depends(get_principal),
    iteration: int | None = Query(default=None, ge=1),
    step: str | None = Query(default=None),
) -> StreamingResponse:
    _get_owned_run(id, principal)

    async def log_source():
        sent = 0
        while True:
            record = run_store.get(id)
            if record is None:
                return
            for log in record.logs[sent:]:
                sent += 1
                if (iteration is None or log.iteration == iteration) and (step is None or log.step == step):
                    yield f"data: {json.dumps(log.model_dump(mode='json'))}\n\n"
            if record.state in TERMINAL_STATES:
                return
            await asyncio.sleep(SSE_POLL_SECONDS)

    return StreamingResponse(log_source(), media_type="text/event-stream")


@router.get("/v1/fine-tune/{id}/artifacts", response_model=ArtifactsResponse)
async def list_fine_tune_artifacts(id: UUID, principal: Principal = Depends(get_principal)) -> ArtifactsResponse:
    record = _get_owned_run(id, principal)
    return ArtifactsResponse(id=record.id, artifacts=record.artifacts(STORAGE_ROOT))


@router.post("/v1/fine-tune/{id}/resume")
async def resume_fine_tune(id: UUID, principal: Principal = Depends(get_principal)) -> JSONResponse:
    record = _get_owned_run(id, principal)
    if record.state not in RESUMABLE_STATES:
        raise invalid_request(
            f"Run {id} is in state '{record.state}' and cannot be resumed (only failed/cancelled runs can be).",
            ["path", "id"],
        )
    record.cancel_requested = False
    record.cancel_event.clear()
    record.error = None
    record.state = "pending"
    record.task = asyncio.create_task(execute_run(record))
    run_store.save(record)
    return JSONResponse(status_code=201, content=FineTuneResponse(id=record.id, state=record.state).model_dump(mode="json"))


@router.post("/v1/fine-tune/{id}/cancel", response_model=FineTuneResponse)
async def cancel_fine_tune(id: UUID, principal: Principal = Depends(get_principal)) -> FineTuneResponse:
    record = _get_owned_run(id, principal)
    if record.state not in TERMINAL_STATES:
        # Cooperative: the runner observes this flag and stops at the next
        # stage boundary; in real mode cancel_event also `docker kill`s the
        # in-flight container so a long training stage aborts promptly.
        record.cancel_requested = True
        record.cancel_event.set()
        run_store.save(record)
    return FineTuneResponse(id=record.id, state=record.state)
