"""Background run simulator.

Stands in for the real DEFT orchestration loop described in
references/pipeline-and-state.md: it walks a workflow's pre_loop, then
`pipeline.iterations` passes over `loop`, then post_loop, appending one
StageEvent per completed stage (mirroring loop_log.jsonl) and one StepLog
with synthetic stdout. It is deliberately simple — fixed short sleeps,
deterministic success — because the point of this reference service is to
exercise the API contract (progress reporting, cursor-based events,
SSE streaming, cancel/resume semantics, results-after-completion), not to
model real GPU training.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass

from app.config import EXECUTION_MODE, MAX_SIMULATED_ITERATIONS, SIMULATED_STEP_SECONDS
from app.data.workflows import WORKFLOWS
from app.schemas.fine_tune_models import (
    BackendInfo,
    IterationMetrics,
    Progress,
    ResultsResponse,
    StageEvent,
    StepLog,
)
from app.store import RunRecord, utcnow


async def execute_run(record: RunRecord) -> None:
    """Entry point for the background run task: real execution when
    DEFT_EXECUTION_MODE=real, otherwise the contract simulator below."""
    if EXECUTION_MODE == "real":
        from app.execution.real_runner import run_real  # lazy: keeps simulate mode import-light

        await run_real(record)
        return
    await run_simulation(record)


@dataclass(frozen=True)
class PlanStep:
    phase: str  # "pre" | "loop" | "post"
    iteration: int  # 1-indexed within the loop phase; 0 for pre/post
    stage: str


def build_plan(workflow_name: str, iterations: int) -> list[PlanStep]:
    workflow = WORKFLOWS[workflow_name]
    plan: list[PlanStep] = [PlanStep(phase="pre", iteration=0, stage=s) for s in workflow.pre_loop]
    for it in range(1, iterations + 1):
        plan.extend(PlanStep(phase="loop", iteration=it, stage=s) for s in workflow.loop)
    plan.extend(PlanStep(phase="post", iteration=0, stage=s) for s in workflow.post_loop)
    return plan


def _iter_label(step: PlanStep) -> str:
    if step.phase == "pre":
        return "baseline"
    if step.phase == "loop":
        return f"iter{step.iteration}"
    return "post_loop"


def _synthetic_summary(step: PlanStep) -> str:
    if step.stage == "train":
        return f"epoch loss decreasing, checkpoint saved ({_iter_label(step)})"
    if step.stage == "evaluate":
        return f"FAR improved this iteration ({_iter_label(step)})"
    return f"{step.stage} completed ({_iter_label(step)})"


async def run_simulation(record: RunRecord) -> None:
    total_iterations = min(record.resolved_config.pipeline.iterations, MAX_SIMULATED_ITERATIONS)
    plan = build_plan(record.workflow, total_iterations)
    loop_len = len(WORKFLOWS[record.workflow].loop)

    record.progress = Progress(current_iteration=1, total_iterations=total_iterations, total_steps=loop_len)
    record.backend = BackendInfo(type="docker", run_id=f"container://{record.id}")

    steps_completed_this_iteration: list[str] = []
    current_loop_iteration = 0

    try:
        while record.next_step_index < len(plan):
            if record.cancel_requested:
                record.state = "cancelled"
                _backfill_tokens(record)
                return

            step = plan[record.next_step_index]
            record.state = "running"
            record.progress.current_step = step.stage

            if step.phase == "loop" and step.iteration != current_loop_iteration:
                current_loop_iteration = step.iteration
                steps_completed_this_iteration = []
                record.progress.current_iteration = step.iteration

            await asyncio.sleep(SIMULATED_STEP_SECONDS)

            async with record.lock:
                seq = record.next_seq()
                record.events.append(
                    StageEvent(
                        seq=seq,
                        ts=utcnow(),
                        iter=_iter_label(step),
                        stage=step.stage,
                        status="ok",
                        summary=_synthetic_summary(step),
                        duration_sec=int(SIMULATED_STEP_SECONDS),
                        tokens=None,
                    )
                )
                record.logs.append(
                    StepLog(
                        iteration=max(step.iteration, 1),
                        step=step.stage,
                        stdout=f"[{_iter_label(step)}] {step.stage} finished successfully\n",
                        stderr="",
                    )
                )

            if step.phase == "loop":
                steps_completed_this_iteration.append(step.stage)
                record.progress.steps_completed = list(steps_completed_this_iteration)
                record.progress.completed_steps = len(steps_completed_this_iteration)
                if step.stage == "evaluate":
                    record.per_iteration_metrics.append(
                        _synthetic_iteration_metrics(step.iteration, total_iterations)
                    )

            record.next_step_index += 1

        record.state = "completed"
        record.progress.current_step = None
        record.convergence_reason = (
            "kpi_met" if record.resolved_config.pipeline.kpi_target else "max_iterations_reached"
        )
        record.results = _synthetic_results(record, total_iterations)
        _backfill_tokens(record)
    except asyncio.CancelledError:
        # The run record itself was deleted mid-flight; nothing left to update.
        raise


def _synthetic_iteration_metrics(iteration: int, total_iterations: int) -> IterationMetrics:
    # Monotonically improving FAR/mAP as iterations progress, purely for a
    # believable demo trajectory.
    progress_fraction = iteration / max(total_iterations, 1)
    far_pct = round(50.0 * (1 - progress_fraction) + 0.5, 3)
    return IterationMetrics(iteration=iteration, metrics={"FAR_pct": far_pct, "mAP50": round(0.6 + 0.3 * progress_fraction, 3)})


def _synthetic_results(record: RunRecord, total_iterations: int) -> ResultsResponse:
    base = f"file:///data/deft/deft_experiments/{record.id}"
    best_iteration = total_iterations
    far_pct = record.per_iteration_metrics[-1].metrics["FAR_pct"] if record.per_iteration_metrics else 5.0
    return ResultsResponse(
        best_checkpoint=f"{base}/iter{best_iteration}/train/train/weights.pt",
        best_iteration=best_iteration,
        threshold=0.237481,
        far_pct=far_pct,
        backbone=f"{base}/backbone/c_radio_v2_b.ckpt",
        images_dir=f"{base}/kpi/images",
        training_spec=f"{base}/specs/baseline_spec.yaml",
        # Simulate mode has no materializable checkpoint file, so this is a
        # deterministic synthetic digest (per run) rather than a real content
        # hash — enough to exercise the DFW->MM sha256 threading end-to-end.
        best_checkpoint_sha256=hashlib.sha256(f"{record.id}:{best_iteration}".encode()).hexdigest(),
        metrics={"FAR_pct": far_pct},
    )


def _backfill_tokens(record: RunRecord) -> None:
    # Mirrors scripts/align_token_usage.py: real per-stage token accounting
    # only exists once the run is over, so every StageEvent gets a placeholder
    # tokens object backfilled at the loop-end sequence instead of at write time.
    for event in record.events:
        if event.tokens is None:
            event.tokens = {
                "input": 0,
                "output": 0,
                "cache_read": 0,
                "cache_create": 0,
                "n_messages": 0,
                "models": [],
            }
