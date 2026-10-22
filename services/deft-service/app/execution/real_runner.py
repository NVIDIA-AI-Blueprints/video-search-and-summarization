"""Real-mode run orchestrator (DEFT_EXECUTION_MODE=real).

Walks the `aoi_active_learning` workflow plan and executes each stage for
real via app/execution/aoi_pipeline.py, in a worker thread (stages are
blocking docker work). Mirrors the simulator's contract behavior exactly —
one StageEvent per stage, StepLogs, Progress, per-iteration metrics after
each `evaluate`, cancel between stages (plus docker kill mid-stage) — but
every number comes from the actual TAO containers and KPI sweep, and every
transition is persisted so a service restart can't silently lose a run.

Early stop: when the parsed pipeline.kpi_target is met at an evaluate, the
remaining loop iterations are skipped and the run proceeds to post_loop with
convergence_reason="kpi_met".
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path

from app.config import ALLOWED_WORKSPACE_ROOT
from app.data.workflows import WORKFLOWS
from app.execution.aoi_pipeline import STAGES as AOI_STAGES
from app.execution.aoi_pipeline import AoiContext, RunCancelled, StageError, StageResult
from app.execution.build_pool import PoolBuildError
from app.execution.docker_backend import ContainerFailed
from app.execution.materialize import (
    MaterializeError,
    materialize,
    needs_materialization,
)
from app.execution.publish import PublishError, publish_artifacts, publish_enabled
from app.execution.workspace import Workspace, WorkspaceError
from app.schemas.fine_tune_models import (
    BackendInfo,
    IterationMetrics,
    Progress,
    ResultsResponse,
    RunError,
    StageEvent,
    StepLog,
)
from app.store import RunRecord, run_store, utcnow


def _fetch_image_via_presign(key: str) -> bytes | None:
    """Resolve a board/golden image key to bytes via the Storage Backend.

    build_training_pool falls back to sample-as-golden when it cannot load a
    golden reference. Golden images live outside the materialized KPI prefix
    (the KPI set selects tag=kpi; goldens are tag=kpi-golden), so without a
    resolver every KPI comparison degrades silently and FAR_pct becomes
    meaningless. DEFT holds no object-store credentials — presign through the
    Storage Backend, exactly as materialize does.
    """
    import urllib.request

    from app.execution.materialize import _presign_one

    try:
        url = _presign_one(key)
    except Exception:  # noqa: BLE001 - caller treats None as "not fetchable"
        return None
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310
            return resp.read()
    except Exception:  # noqa: BLE001
        return None


async def run_real(record: RunRecord) -> None:
    if record.workflow != "aoi_active_learning":
        _fail(record, step="submit", message=f"real execution is not implemented for workflow {record.workflow!r}")
        return

    workflow = WORKFLOWS[record.workflow]
    total_iterations = record.resolved_config.pipeline.iterations
    record.progress = Progress(
        current_iteration=1, total_iterations=total_iterations, total_steps=len(workflow.loop)
    )
    record.state = "running"

    try:
        # Object-store handoff (DFW contract): s3:// references are pulled to
        # local disk first — the docker backend only mounts local workspaces.
        if needs_materialization(record.resolved_config):
            record.progress.current_step = "materialize"
            run_store.save(record)
            record.resolved_config = await asyncio.to_thread(
                materialize,
                record.resolved_config,
                record.storage_access,
                str(record.id),
            )
            run_store.save(record)
        # A raw AOI snapshot (board images + JSONL manifests) is not yet in the
        # ChangeNet workspace layout; convert it in place — crop each board's
        # components into paired test/golden crops and emit the train/val/kpi/pool
        # CSVs + images — before resolving the workspace. No-op when the workspace
        # already carries a built pool (train/base/training_set.csv present).
        from app.execution.build_pool import build_training_pool, pool_needs_build

        ws = Workspace.resolve(
            record.resolved_config.data_pool.path,
            allowed_root=ALLOWED_WORKSPACE_ROOT,
        )
        pool_path = ws.root
        if pool_needs_build(pool_path):
            record.progress.current_step = "build_training_pool"
            run_store.save(record)
            batch = record.resolved_config.training.batch_size or 8
            # An external KPI set (materialized from kpi.path prefix) becomes the
            # eval set; otherwise build_training_pool carves kpi from the snapshot.
            kpi_src = pool_path / "kpi_source"
            kpi_source = kpi_src if kpi_src.is_dir() and any(kpi_src.glob("manifest*.jsonl")) else None
            await asyncio.to_thread(
                build_training_pool, pool_path, kpi_source=kpi_source, batch_size=batch,
                fetch_image=_fetch_image_via_presign
            )
        run_dir = ws.root / "results" / f"service_run_{str(record.id)[:8]}"
        run_dir.mkdir(parents=True, exist_ok=True)
        record.run_dir = str(run_dir)
        record.backend = BackendInfo(type="docker", run_id=f"dir://{run_dir}")
        ctx = AoiContext(
            record=record,
            cfg=record.resolved_config,
            ws=ws,
            run_dir=run_dir,
            cancel_event=record.cancel_event,
        )
    except MaterializeError as exc:
        _fail(record, step="materialize", message=str(exc))
        return
    except PoolBuildError as exc:
        _fail(record, step="build_training_pool", message=str(exc))
        return
    except (WorkspaceError, OSError) as exc:
        _fail(record, step="validate_training_pool", message=str(exc))
        return
    except Exception as exc:  # defensive: setup must never leave the run stuck in "running"
        record.runner_crash = repr(exc)
        _fail(record, step=record.progress.current_step or "setup", message=f"internal error: {exc}")
        return
    run_store.save(record)

    async def do_stage(stage: str, iteration: int, iter_label: str) -> bool:
        """Execute one stage; returns False when the run must stop."""
        if record.cancel_requested:
            record.state = "cancelled"
            _finalize_disk(ctx, record)
            run_store.save(record)
            return False
        record.progress.current_step = stage
        # Step boundaries are liveness too: without this, updated_at would stay
        # null for any stage that runs no container (materialize, mining filter).
        record.progress.updated_at = utcnow()
        run_store.save(record)
        t0 = time.monotonic()
        try:
            result: StageResult = await asyncio.to_thread(AOI_STAGES[stage], ctx)
        except RunCancelled:
            record.state = "cancelled"
            _append_event(record, iter_label, stage, "error", "cancelled by user", t0)
            _finalize_disk(ctx, record)
            run_store.save(record)
            return False
        except (StageError, ContainerFailed) as exc:
            logs = getattr(exc, "logs", "") or getattr(exc, "log_tail", "")
            exit_code = getattr(exc, "exit_code", 1)
            _append_event(record, iter_label, stage, "error", str(exc), t0)
            record.logs.append(StepLog(iteration=max(iteration, 1), step=stage, stdout="", stderr=logs[-4000:]))
            record.error = RunError(step=stage, exit_code=int(exit_code), message=str(exc), logs=logs[-4000:])
            record.state = "failed"
            _finalize_disk(ctx, record)
            run_store.save(record)
            return False
        except Exception as exc:  # defensive: an orchestrator bug must not hang the run
            _append_event(record, iter_label, stage, "error", f"internal error: {exc}", t0)
            record.error = RunError(step=stage, exit_code=-1, message=f"internal error: {exc}", logs="")
            record.state = "failed"
            record.runner_crash = repr(exc)
            _finalize_disk(ctx, record)
            run_store.save(record)
            return False

        _append_event(record, iter_label, stage, "ok", result.summary, t0)
        record.logs.append(
            StepLog(
                iteration=max(iteration, 1),
                step=stage,
                stdout=(result.stdout_tail or result.summary) + "\n",
                stderr="",
            )
        )
        if stage == "evaluate":
            record.per_iteration_metrics.append(
                IterationMetrics(iteration=iteration, metrics=result.metrics or {})
            )
        run_store.save(record)
        return True

    # pre_loop
    for stage in workflow.pre_loop:
        if not await do_stage(stage, 0, "baseline"):
            return

    # loop
    for iteration in range(1, total_iterations + 1):
        ctx.current_iteration = iteration
        record.progress.current_iteration = iteration
        record.progress.steps_completed = []
        record.progress.completed_steps = 0
        for stage in workflow.loop:
            if not await do_stage(stage, iteration, f"iter{iteration}"):
                return
            record.progress.steps_completed.append(stage)
            record.progress.completed_steps = len(record.progress.steps_completed)
        if ctx.stop_reason == "kpi_met":
            break

    # post_loop
    for stage in workflow.post_loop:
        if not await do_stage(stage, 0, "post_loop"):
            return

    # Publish artifacts to the object store (DFW handoff): the results then
    # carry s3:// URIs valid on any host, not file:// paths on this one.
    published: dict[str, str] = {}
    if publish_enabled():
        record.progress.current_step = "publish"
        run_store.save(record)
        best_ckpt = ctx.best.ckpt_host or ctx.ws.host(ctx.cfg.model.checkpoint)
        try:
            published = await asyncio.to_thread(
                publish_artifacts,
                str(record.id),
                {"best_checkpoint": best_ckpt, "training_spec": ctx.train_spec},
                record.storage_access,
            )
        except PublishError as exc:
            _fail(record, step="publish", message=str(exc))
            _finalize_disk(ctx, record)
            run_store.save(record)
            return

    record.state = "completed"
    record.progress.current_step = None
    record.convergence_reason = "kpi_met" if ctx.stop_reason == "kpi_met" else "max_iterations_reached"
    record.results = _build_results(ctx, published)
    _finalize_disk(ctx, record)
    run_store.save(record)


def _append_event(record: RunRecord, iter_label: str, stage: str, status: str, summary: str, t0: float) -> None:
    record.events.append(
        StageEvent(
            seq=record.next_seq(),
            ts=utcnow(),
            iter=iter_label,
            stage=stage,
            status=status,  # type: ignore[arg-type]
            summary=summary[:500],
            duration_sec=int(time.monotonic() - t0),
            tokens=None,
        )
    )


def _fail(record: RunRecord, *, step: str, message: str) -> None:
    record.error = RunError(step=step, exit_code=1, message=message, logs="")
    record.state = "failed"
    run_store.save(record)


def _sha256_file(path: Path) -> str | None:
    """SHA-256 (hex) of a file, streamed; None if it is not a readable file.

    ``publish_artifacts`` uploads the checkpoint verbatim (``s3.upload_file``),
    so the local file's digest equals the published object's — i.e. exactly the
    bytes the Model Manager downloads and verifies on ``pull``.
    """
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _build_results(ctx: AoiContext, published: dict[str, str] | None = None) -> ResultsResponse:
    best = ctx.best
    published = published or {}
    backbone = ctx.ws.root / "augmentation/backbone/model.safetensors"
    ckpt = best.ckpt_host or (ctx.ws.host(ctx.cfg.model.checkpoint))
    metrics = dict(best.metrics)
    metrics["best_is_baseline"] = 1.0 if best.is_baseline else 0.0
    return ResultsResponse(
        # Published s3:// URI when the publish step ran; file:// host path otherwise.
        best_checkpoint=published.get("best_checkpoint", f"file://{ckpt}"),
        best_iteration=max(best.iteration, 1),
        threshold=best.threshold,
        far_pct=round(best.far_pct, 4),
        backbone=f"file://{backbone}" if backbone.exists() else str(ctx.cfg.model.checkpoint),
        images_dir=f"file://{ctx.ws.root / 'kpi/images'}",
        training_spec=published.get("training_spec", f"file://{ctx.train_spec}"),
        # Digest of the local checkpoint == the published bytes MM verifies on pull.
        best_checkpoint_sha256=_sha256_file(Path(ckpt)),
        metrics=metrics,
    )


def _finalize_disk(ctx: AoiContext, record: RunRecord) -> None:
    """Write the on-disk artifacts contract (state snapshot, event log, report)."""
    try:
        run_dir = Path(record.run_dir) if record.run_dir else ctx.run_dir
        state = {
            "run_id": str(record.id),
            "state": record.state,
            "workflow": record.workflow,
            "config": record.resolved_config.model_dump(mode="json"),
            "best": {
                "iteration": ctx.best.iteration,
                "is_baseline": ctx.best.is_baseline,
                "far_pct": None if ctx.best.far_pct == float("inf") else ctx.best.far_pct,
                "threshold": ctx.best.threshold,
                "checkpoint": str(ctx.best.ckpt_host) if ctx.best.ckpt_host else ctx.best.ckpt_container,
            },
            "per_iteration_metrics": [m.model_dump(mode="json") for m in record.per_iteration_metrics],
        }
        (run_dir / "deft_state.json").write_text(json.dumps(state, indent=2))
        with open(run_dir / "loop_log.jsonl", "w") as fh:
            for event in record.events:
                fh.write(json.dumps(event.model_dump(mode="json")) + "\n")
        rows = "".join(
            f"<tr><td>{e.seq}</td><td>{e.iter}</td><td>{e.stage}</td><td>{e.status}</td>"
            f"<td>{e.duration_sec}s</td><td>{e.summary}</td></tr>"
            for e in record.events
        )
        (run_dir / "DEFT_Loop_Report.html").write_text(
            "<html><head><title>DEFT run report</title></head><body>"
            f"<h1>DEFT run {record.id}</h1><p>state: {record.state}</p>"
            "<table border=1 cellpadding=4><tr><th>#</th><th>iter</th><th>stage</th>"
            f"<th>status</th><th>dur</th><th>summary</th></tr>{rows}</table></body></html>"
        )
    except OSError:
        pass  # artifact write failure must not mask the run outcome
