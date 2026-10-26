# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Sequential matrix waves, balanced rotation and isolated concurrent workers."""

import json
import math
import os
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from benchmarks.common import environment, software_hash
from benchmarks.e2e.config import fingerprint
from benchmarks.matrix.config import admit_environment, cells, workflow
from benchmarks.media import MAX_INPUT_BYTES, file_hash
from benchmarks.workflow.barrier import publish

ROOT = Path(__file__).resolve().parents[2]


def save(path, value):
    """Private atomic checkpoint, replacing only files inside the owned run."""
    temporary = path.with_suffix(path.suffix + ".writing")
    with temporary.open("x", encoding="utf-8") as stream:
        os.chmod(temporary, 0o600)
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


@contextmanager
def cancellation():
    state = {"cancelled": False}

    def cancel(*_):
        state["cancelled"] = True

    previous = {s: signal.signal(s, cancel) for s in (signal.SIGINT, signal.SIGTERM)}
    try:
        yield state
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def plan(recipe):
    matrix = cells(recipe)
    trials = sum(
        c["concurrent_streams"] * (recipe.warmups + recipe.repetitions) for c in matrix
    )
    return dict(
        schema_version="perf_plan.1",
        cells=matrix,
        trials=trials,
        run_timeout_s=recipe.run_timeout_s,
        cleanup_grace_s=8 * recipe.cleanup_timeout_s + 65,
        budget_scope=(
            "wall_admission_and_bounded_client_workflows; "
            "service_internal_requests_tokens_unknown"
        ),
        requested_output_tokens_per_library_call=dict(
            caption=recipe.caption_max_tokens, summary=recipe.summary_max_tokens
        ),
        comparison_scope="task_level_diagnostics_not_modularity_overhead",
        thresholds_approved=False,
    )


def schedule(recipe, matrix):
    """Rotate profile/placement admission order; warmups are never measurements."""
    for kind, rounds in (
        ("warmup", recipe.warmups),
        ("measurement", recipe.repetitions),
    ):
        for repetition in range(rounds):
            offset = repetition % len(matrix)
            order = matrix[offset:] + matrix[:offset]
            if repetition % 2:
                order = list(reversed(order))
            for cell in order:
                yield kind, repetition, cell


def validate_receipt(root, row):
    """A zero exit status is insufficient: bind evidence to its reserved intent."""
    receipt_path, intent_path = root / row["receipt"], root / row["intent"]
    if (
        receipt_path.is_symlink()
        or not receipt_path.is_file()
        or receipt_path.stat().st_size > 16 * 1024**2
    ):
        raise ValueError("missing_or_oversize_receipt")
    if file_hash(intent_path) != row["intent_sha256"]:
        raise ValueError("intent_changed")
    report = json.loads(receipt_path.read_bytes())
    intent = json.loads(intent_path.read_bytes())
    if (
        report.get("schema_version") != "perf_trial.1"
        or report.get("trial_id") != row["trial_id"]
        or report.get("intent_sha256") != row["intent_sha256"]
        or report.get("qualified") is not False
        or report.get("thresholds_approved") is not False
    ):
        raise ValueError("receipt_identity")
    if report["status"] == "completed_diagnostic":
        op = report.get("operation")
        cleanup = op.get("cleanup") if isinstance(op, dict) else None
        if (
            not isinstance(op, dict)
            or not isinstance(cleanup, dict)
            or not cleanup
            or not all(value is True for value in cleanup.values())
            or report.get("software_sha256") != intent["software_sha256"]
            or op.get("status") != "completed_diagnostic"
            or op.get("operational_settlement")
            != "outer_completed_owned_cleanup_complete"
            or op.get("outer_response_completed") is not True
            or report.get("observer_complete") is not True
            or type(op.get("workflow_to_result_s")) not in (int, float)
            or not math.isfinite(op["workflow_to_result_s"])
            or op["workflow_to_result_s"] <= 0
        ):
            raise ValueError("incomplete_success_receipt")
    return report


def run_wave(root, rows, recipe, stop, deadline, barrier):
    active, logs = [], []
    cleanup_grace = 8 * recipe.cleanup_timeout_s + 65
    forced = False
    wave_started = time.monotonic()
    stop_at = min(deadline, wave_started + recipe.work_timeout_s + 180)
    cancel_deadline = None
    try:
        for row in rows:
            if stop["cancelled"] or time.monotonic() >= deadline:
                break
            log = (root / row["log"]).open("xb")
            os.chmod(root / row["log"], 0o600)
            logs.append(log)
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "benchmarks.matrix.worker",
                    "--intent",
                    str(root / row["intent"]),
                    "--receipt",
                    str(root / row["receipt"]),
                ],
                cwd=ROOT,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
            active.append((row, process))
            row["status"] = "running"
        while any(process.poll() is None for _, process in active):
            now = time.monotonic()
            if (
                barrier is not None
                and not (barrier / "go.json").exists()
                and len(list(barrier.glob("*.ready.json"))) == len(rows)
            ):
                publish(
                    barrier / "go.json",
                    dict(barrier_id=barrier.name, release_monotonic_s=now),
                )
            early_failure = any(p.poll() not in (None, 0) for _, p in active)
            if (
                stop["cancelled"] or now >= stop_at or early_failure
            ) and cancel_deadline is None:
                stop["cancelled"] = True
                cancel_deadline = now + cleanup_grace
                # One shared absolute cleanup deadline; all workers cancel together.
                for _, process in active:
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGTERM)
            if cancel_deadline is not None and now >= cancel_deadline:
                for _, process in active:
                    if process.poll() is None:
                        forced = True
                        os.killpg(process.pid, signal.SIGKILL)
                break
            time.sleep(0.05)
        for row, process in active:
            process.wait(timeout=5)
            row["exit_code"] = process.returncode
    except BaseException:
        stop["cancelled"] = True
        # Any supervisor failure cancels every owned child before propagating.
        for _, process in active:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
        until = time.monotonic() + cleanup_grace
        for _, process in active:
            try:
                process.wait(timeout=max(0.01, until - time.monotonic()))
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
        raise
    finally:
        for log in logs:
            log.close()
    return forced


def run(recipe, output):
    if os.name != "posix":
        raise ValueError(
            "matrix execution requires POSIX; plan/export remain available"
        )
    planned = plan(recipe)
    # Freeze the media epoch once. Never repin a changed video between trials.
    declared_recipe_sha = fingerprint(recipe.model_dump(mode="json"))
    frozen_media, media_failures = [], {}
    for media in recipe.media:
        try:
            if (
                not media.video.is_file()
                or not 0 < media.video.stat().st_size <= MAX_INPUT_BYTES
            ):
                raise ValueError("media_regular_file_and_size_required")
            actual = file_hash(media.video)
            if media.sha256 is not None and media.sha256 != actual:
                raise ValueError("media_pin_mismatch")
            frozen_media.append(media.model_copy(update={"sha256": actual}))
        except (OSError, ValueError) as error:
            frozen_media.append(media)
            media_failures[media.name] = "media_admission_" + type(error).__name__
    recipe = recipe.model_copy(update={"media": tuple(frozen_media)})
    for cell in planned["cells"]:
        if cell["workload"] in media_failures:
            cell["blocked"].append(media_failures[cell["workload"]])
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    (output / "trials").mkdir(mode=0o700)
    manifest = dict(
        schema_version="perf_run.1",
        run_id=uuid4().hex,
        status="preparing",
        recipe=recipe.model_dump(mode="json"),
        plan=planned,
        trials=[],
        batches=[],
        software_sha256=software_hash(ROOT),
        started_utc=datetime.now(UTC).isoformat(),
        environment=environment().model_dump(mode="json"),
        declared_recipe_sha256=declared_recipe_sha,
    )
    manifest["recipe_sha256"] = fingerprint(manifest["recipe"])
    save(output / "run.json", manifest)
    run_started = time.monotonic()
    deadline = run_started + recipe.run_timeout_s
    with cancellation() as stop:
        try:
            manifest["status"] = "running"
            for kind, repetition, cell in schedule(recipe, planned["cells"]):
                batch_id = uuid4().hex
                rows, barrier = [], None
                if cell["concurrent_streams"] > 1:
                    barrier = output / "trials" / batch_id
                    barrier.mkdir(mode=0o700)
                blocked = list(cell["blocked"])
                profile = None
                try:
                    if not blocked:
                        profile = workflow(recipe, cell)
                        admit_environment(profile)
                except (OSError, ValueError, KeyError) as error:
                    blocked.append(
                        "configuration_or_prerequisite_" + type(error).__name__
                    )
                for stream in range(cell["concurrent_streams"]):
                    trial_id = f"t{len(manifest['trials']):05d}"
                    row = dict(
                        cell,
                        trial_id=trial_id,
                        kind=kind,
                        repetition=repetition,
                        stream=stream,
                        batch_id=batch_id,
                        status="blocked" if blocked else "not_run",
                        blocked=blocked,
                        intent=f"trials/{trial_id}.intent.json",
                        receipt=f"trials/{trial_id}.receipt.json",
                        log=f"trials/{trial_id}.log",
                    )
                    if profile is not None:
                        p = profile.model_copy(
                            update={
                                "output_capture": output
                                / "trials"
                                / (trial_id + ".output.json")
                            }
                        )
                        if barrier is not None:
                            p = p.model_copy(
                                update={
                                    "barrier_directory": barrier,
                                    "barrier_id": batch_id,
                                    "barrier_member": f"stream-{stream}",
                                }
                            )
                        intent = dict(
                            trial_id=trial_id,
                            run_id=manifest["run_id"],
                            software_sha256=manifest["software_sha256"],
                            workflow=p.model_dump(mode="json"),
                        )
                        intent["workflow_sha256"] = fingerprint(intent["workflow"])
                        publish(output / row["intent"], intent)
                        row["intent_sha256"] = file_hash(output / row["intent"])
                    manifest["trials"].append(row)
                    rows.append(row)
                save(output / "run.json", manifest)  # before any service call
                if blocked or stop["cancelled"] or time.monotonic() >= deadline:
                    if time.monotonic() >= deadline:
                        stop["cancelled"] = True
                    continue
                print(
                    f"{kind} {repetition + 1}: {cell['workload']} "
                    f"{cell['profile']} {cell['placement']} "
                    f"streams={cell['concurrent_streams']}",
                    flush=True,
                )
                forced = run_wave(output, rows, recipe, stop, deadline, barrier)
                completed_ops = []
                for row in rows:
                    try:
                        report = validate_receipt(output, row)
                        row["receipt_sha256"] = file_hash(output / row["receipt"])
                        row["status"] = report["status"]
                        if row.get("exit_code") != (
                            0 if report["status"] == "completed_diagnostic" else 1
                        ):
                            raise ValueError("exit_receipt_disagreement")
                        if report["status"] == "completed_diagnostic":
                            completed_ops.append(report["operation"])
                        if report.get("operational_settlement") not in {
                            "outer_completed_owned_cleanup_complete",
                            "not_started",
                        }:
                            stop["cancelled"] = True
                    except (ValueError, OSError, KeyError) as error:
                        row.update(
                            status="invalid_receipt", failure=type(error).__name__
                        )
                        stop["cancelled"] = True
                batch = dict(
                    batch_id=batch_id,
                    kind=kind,
                    repetition=repetition,
                    cell_id=cell["cell_id"],
                    trial_ids=[r["trial_id"] for r in rows],
                    completed=len(completed_ops),
                    forced_termination=forced,
                    telemetry_scope="device_and_host_shared_not_service_attributed",
                    actual_overlap_streams=None,
                    throughput_workflows_per_s=None,
                )
                if len(completed_ops) == len(rows):
                    starts = [
                        op["operation_started_monotonic_s"] for op in completed_ops
                    ]
                    ends = [op["result_monotonic_s"] for op in completed_ops]
                    batch.update(
                        operation_span_s=max(ends) - min(starts),
                        actual_overlap_streams=max(
                            sum(a <= t < b for a, b in zip(starts, ends, strict=True))
                            for t in starts
                        ),
                        throughput_workflows_per_s=len(rows)
                        / (max(ends) - min(starts)),
                    )
                manifest["batches"].append(batch)
                if forced:
                    stop["cancelled"] = True
                save(output / "run.json", manifest)
            manifest["status"] = "interrupted" if stop["cancelled"] else "finished"
        except BaseException as error:
            manifest.update(status="interrupted", failure=type(error).__name__)
            raise
        finally:
            manifest["elapsed_wall_s"] = time.monotonic() - run_started
            save(output / "run.json", manifest)
            from benchmarks.matrix.export import export

            export(output)
    return manifest
