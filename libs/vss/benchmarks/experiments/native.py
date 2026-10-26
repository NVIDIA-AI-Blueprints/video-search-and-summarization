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

"""Balanced native decode/preparation and observer-cost campaign (POSIX only)."""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import importlib.metadata
import json
import math
import os
import random
import re
import statistics
import sys
import tempfile
import time
from contextlib import ExitStack, aclosing
from pathlib import Path

import numpy as np

from benchmarks.artifacts import aggregate, bundle_bytes, verify_trace, write_trace
from benchmarks.common import environment, import_origins, software_hash
from benchmarks.diagnostics import tool_diagnostics
from benchmarks.experiments.native_models import POLICIES, NativeTrial, Settings, Slot
from benchmarks.gpu_resources import GPUIdleCheck, GPUSampler, idle_check
from benchmarks.media import MAX_INPUT_BYTES, MediaError, file_hash, inspect
from benchmarks.processing import preflight
from benchmarks.resources import ResourceSampler
from benchmarks.results import Environment
from benchmarks.supervisor import isolated_trial
from benchmarks.video_results import balanced_orders
from vss import video_processing as processing
from vss.observability import collect_vss_metrics

ROOT = Path(__file__).resolve().parents[2]
RESERVE_S = 30.0
MAX_JSON_BYTES = 8 * 1024**2


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def atomic_json(path, value):
    raw = json.dumps(value, indent=2, allow_nan=False).encode() + b"\n"
    if len(raw) > MAX_JSON_BYTES:
        raise ValueError("receipt byte budget")
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(raw)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def schedule(settings):
    rng = random.Random(settings.seed)
    cells = [(task, policy) for task in settings.tasks for policy in POLICIES]
    orders = {
        cell: balanced_orders(settings.blocks, settings.seed + index)
        for index, cell in enumerate(cells)
    }
    rows = []
    for block in range(settings.blocks):
        ordered = list(cells)
        rng.shuffle(ordered)
        for task, policy in ordered:
            backends = list(settings.backends)
            if orders[task, policy][block] == "enabled_first":
                backends.reverse()
            for backend in backends:
                rows.append(
                    Slot(
                        index=len(rows),
                        block=block,
                        backend=backend,
                        task=task,
                        policy=policy,
                    )
                )
    return rows


def origins():
    return import_origins(ROOT) | {"benchmark": str(Path(__file__).resolve())}


def native_identity(gpu_required):
    tools, builds = preflight()
    device = None
    if gpu_required:
        sampler = GPUSampler()
        try:
            device = sampler.identity
        finally:
            sampler.close_unused()
        distribution = importlib.metadata.distribution("pynvvideocodec")
        binaries = sorted(
            Path(distribution.locate_file(f)).resolve()
            for f in distribution.files or ()
            if ".so" in Path(f).name
        )
        if not binaries or len(binaries) > 64:
            raise ValueError("native binary inventory")
        builds["pynv_binary_sha256"] = digest([file_hash(p) for p in binaries])
        for package in ("pynvvideocodec", "nvidia-ml-py"):
            builds[package] = importlib.metadata.version(package)
    return tools, builds, device


async def operation(video, media, slot):
    """Retain scalars and <=8 host RGB references; validate after both clocks."""
    indices = (
        tuple(range(len(media.pts_ns))) if slot.task == "full" else media.oracle_indices
    )
    targets = (
        None if slot.task == "full" else tuple(media.pts_ns[index] for index in indices)
    )
    plan = processing.DecodePlan(sample_timestamps_ns=targets)
    metadata, pixels, encoded = [], [], []
    encoded_bytes = 0
    wanted = set(media.oracle_indices)
    stream = processing.decode_frames(
        video,
        plan,
        source_id=media.sha256,
        options=processing.DecodeOptions(decoder=slot.backend, batch_size=4),
    )
    started = time.perf_counter()
    async with aclosing(stream):
        async for frame in stream:
            if len(metadata) >= len(indices):
                raise ValueError("extra decoded output")
            array = frame.tensor
            metadata.append(
                (
                    frame.index,
                    frame.timestamp_ns,
                    frame.decoder,
                    frame.device,
                    frame.timestamp_quality,
                    array.shape,
                    str(array.dtype),
                    array.flags.c_contiguous,
                    array.flags.owndata,
                    array.flags.writeable,
                )
            )
            if frame.index in wanted:
                pixels.append((frame.index, array))
            if slot.task == "prepared":
                image = frame.to_encoded(format="jpeg")
                encoded_bytes += len(image.data_base64)
                encoded.append(image)
    elapsed = time.perf_counter() - started
    return elapsed, metadata, pixels, encoded_bytes, indices, encoded


def validate_output(raw, media, oracle, backend):
    elapsed, metadata, pixels, encoded_bytes, indices, encoded = raw
    if len(metadata) != len(indices):
        raise ValueError("missing decoded output")
    for row, index in zip(metadata, indices, strict=True):
        if (
            row[0] != index
            or abs(row[1] - media.pts_ns[index]) > 2_000_000
            or row[2] != backend
            or row[3] != ("gpu" if backend == "pynv" else "cpu")
            or row[4] == "estimated"
            or row[5:] != ((media.height, media.width, 3), "uint8", True, True, False)
        ):
            raise ValueError("frame metadata mismatch")
    if [index for index, _ in pixels] != list(media.oracle_indices):
        raise ValueError("pixel oracle positions mismatch")
    maximum = max(
        float(np.abs(array.astype(np.int16) - expected.astype(np.int16)).mean())
        for (_, array), expected in zip(pixels, oracle, strict=True)
    )
    if maximum > 3:
        raise ValueError("RGB oracle mismatch")
    jpeg_mae = None
    if encoded:
        import cv2

        errors = []
        for image, (_, array) in zip(encoded, pixels, strict=True):
            if image.mime_type != "image/jpeg":
                raise ValueError("JPEG MIME mismatch")
            data = base64.b64decode(image.data_base64, validate=True)
            decoded = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            if decoded is None or decoded.shape != array.shape:
                raise ValueError("JPEG dimensions mismatch")
            errors.append(
                float(
                    np.abs(
                        cv2.cvtColor(decoded, cv2.COLOR_BGR2RGB).astype(np.int16)
                        - array.astype(np.int16)
                    ).mean()
                )
            )
        jpeg_mae = max(errors)
        if jpeg_mae >= 10:
            raise ValueError("JPEG RGB mismatch")
    return (
        elapsed,
        len(metadata),
        maximum,
        digest(metadata),
        encoded_bytes,
        len(encoded),
        jpeg_mae,
    )


async def worker(request, artifacts):
    atomic_json(artifacts / "phase.json", {"phase": "preflight"})
    settings = Settings.model_validate(request["settings"])
    slot = Slot.model_validate(request["slot"])
    if slot not in schedule(settings):
        raise ValueError("slot outside frozen schedule")
    video = Path(request["video"])
    imported = origins()
    source = software_hash(ROOT)
    lock = file_hash(ROOT / "uv.lock")
    env = environment()
    tools, builds, gpu = native_identity("pynv" in settings.backends)
    atomic_json(artifacts / "phase.json", {"phase": "media_admission"})
    with tool_diagnostics(artifacts / "tools"):
        media, oracle = inspect(video, tools)
    if media.sha256 != request["video_sha256"]:
        raise ValueError("source hash mismatch")
    atomic_json(artifacts / "phase.json", {"phase": "warmup"})
    for _ in range(settings.warmups):
        warm = await operation(video, media, slot)
        validate_output(warm, media, oracle, slot.backend)
        del warm
    collector = resources = gpu_sampler = None
    atomic_json(artifacts / "phase.json", {"phase": "measurement"})
    start_cpu = os.times()
    started = time.perf_counter()
    with ExitStack() as stack:
        if slot.policy in {"resources", "combined"}:
            resources = stack.enter_context(ResourceSampler(0.05))
            if gpu is not None:
                gpu_sampler = stack.enter_context(GPUSampler())
                if gpu_sampler.identity != gpu:
                    raise ValueError("GPU identity changed")
        if slot.policy in {"collection", "combined"}:
            collector = stack.enter_context(collect_vss_metrics(max_events=100000))
        measured = await operation(video, media, slot)
    policy_wall = time.perf_counter() - started
    end_cpu = os.times()
    atomic_json(artifacts / "phase.json", {"phase": "output_validation"})
    elapsed, count, maximum, metadata_hash, encoded_bytes, jpeg_checks, jpeg_mae = (
        validate_output(measured, media, oracle, slot.backend)
    )
    del measured
    records = collector.observations if collector is not None else ()
    trace = write_trace(artifacts, records) if collector is not None else None
    atomic_json(artifacts / "phase.json", {"phase": "runtime_validation"})
    _, final_builds, final_gpu = native_identity("pynv" in settings.backends)
    if (
        file_hash(video) != media.sha256
        or source != software_hash(ROOT)
        or lock != file_hash(ROOT / "uv.lock")
        or env != environment()
        or builds != final_builds
        or gpu != final_gpu
        or imported != origins()
    ):
        raise ValueError("input/runtime changed")
    atomic_json(artifacts / "phase.json", {"phase": "evidence"})
    return NativeTrial(
        slot=slot,
        settings_sha256=digest(settings.model_dump()),
        process_id=os.getpid(),
        media=media,
        software_sha256=source,
        lock_sha256=lock,
        environment=env,
        native_builds=builds,
        import_origins=imported,
        gpu=gpu,
        operation_wall_s=elapsed,
        policy_wall_s=policy_wall,
        policy_cpu_s=max(
            0, end_cpu.user + end_cpu.system - start_cpu.user - start_cpu.system
        ),
        frames=count,
        pixel_checks=len(media.oracle_indices),
        maximum_rgb_mae=maximum,
        output_metadata_sha256=metadata_hash,
        encoded_base64_bytes=encoded_bytes,
        jpeg_checks=jpeg_checks,
        maximum_jpeg_mae=jpeg_mae,
        collection_complete=collector.complete if collector is not None else None,
        metrics=aggregate(records),
        trace=trace,
        resources=resources.trace if resources is not None else None,
        gpu_trace=gpu_sampler.trace if gpu_sampler is not None else None,
    )


def trial_identity(trial):
    return (
        trial.media,
        trial.software_sha256,
        trial.lock_sha256,
        trial.environment,
        trial.native_builds,
        trial.import_origins,
        trial.gpu,
    )


def checked_idle(block, phase, diagnostics):
    """Bound even a blocked NVML call in a separately supervised process."""
    raw = isolated_trial(
        [
            sys.executable,
            "-m",
            "benchmarks.experiments.native",
            "--idle-block",
            str(block),
            "--idle-phase",
            phase,
        ],
        cwd=ROOT,
        timeout=8,
        diagnostics=diagnostics,
    )
    check = GPUIdleCheck.model_validate_json(raw)
    if check.pair != block or check.phase != phase:
        raise ValueError("idle helper echo mismatch")
    return check


def summarize(receipt, trials):
    if receipt["status"] != "complete":
        return {"status": "incomplete_fixed_plan", "groups": [], "comparisons": []}
    settings = Settings.model_validate(receipt["settings"])
    groups, comparisons = [], []
    for task in settings.tasks:
        for backend in settings.backends:
            for policy in POLICIES:
                selected = [
                    t
                    for t in trials
                    if (t.slot.task, t.slot.backend, t.slot.policy)
                    == (task, backend, policy)
                ]
                groups.append(
                    {
                        "task": task,
                        "backend": backend,
                        "policy": policy,
                        "n": len(selected),
                        "mean_operation_s": statistics.mean(
                            t.operation_wall_s for t in selected
                        ),
                        "median_operation_s": statistics.median(
                            t.operation_wall_s for t in selected
                        ),
                        "median_policy_s": statistics.median(
                            t.policy_wall_s for t in selected
                        ),
                        "median_fps": statistics.median(
                            t.frames / t.operation_wall_s for t in selected
                        ),
                        "interval": None,
                        "p95": None,
                    }
                )
            for policy in POLICIES[1:]:
                paired = []
                for block in range(settings.blocks):
                    arms = {
                        t.slot.policy: t
                        for t in trials
                        if (t.slot.task, t.slot.backend, t.slot.block)
                        == (task, backend, block)
                    }
                    paired.append(
                        arms[policy].policy_wall_s / arms["minimal"].policy_wall_s
                    )
                comparisons.append(
                    {
                        "kind": "observer_policy_cost",
                        "task": task,
                        "backend": backend,
                        "candidate": policy,
                        "baseline": "minimal",
                        "median_paired_ratio": statistics.median(paired),
                        "interval": None,
                    }
                )
        if len(settings.backends) == 2:
            for policy in POLICIES:
                paired = []
                for block in range(settings.blocks):
                    arms = {
                        t.slot.backend: t
                        for t in trials
                        if (t.slot.task, t.slot.policy, t.slot.block)
                        == (task, policy, block)
                    }
                    paired.append(
                        arms["pynv"].operation_wall_s / arms["opencv"].operation_wall_s
                    )
                comparisons.append(
                    {
                        "kind": "decoder_operation",
                        "task": task,
                        "policy": policy,
                        "baseline": "opencv",
                        "candidate": "pynv",
                        "median_paired_ratio": statistics.median(paired),
                        "interval": None,
                    }
                )
    return {"status": "descriptive_only", "groups": groups, "comparisons": comparisons}


def run(video, output, settings):
    started = time.monotonic()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    rows = [slot.model_dump() | {"status": "not_run"} for slot in schedule(settings)]
    receipt = {
        "schema_version": "native_campaign.1",
        "settings": settings.model_dump(),
        "rows": rows,
        "status": "incomplete",
        "failure": None,
        "acceptance_eligible": False,
        "thresholds_approved": False,
        "harness_overhead_qualified": False,
    }
    atomic_json(output / "intent.json", receipt)
    receipt["intent_sha256"] = file_hash(output / "intent.json")
    try:
        if os.name != "posix":
            raise ValueError("POSIX supervision required")
        video = video.resolve(strict=True)
        if not 0 < video.stat().st_size <= MAX_INPUT_BYTES:
            raise ValueError("source byte budget")
        source = file_hash(video)
        software = software_hash(ROOT)
        lock = file_hash(ROOT / "uv.lock")
        imported = origins()
        env = environment()
        receipt.update(
            video_sha256=source,
            software_sha256=software,
            lock_sha256=lock,
            environment=env.model_dump(mode="json"),
            import_origins=imported,
        )
        atomic_json(
            output / "admission.json",
            {
                k: receipt[k]
                for k in (
                    "video_sha256",
                    "software_sha256",
                    "lock_sha256",
                    "environment",
                    "import_origins",
                )
            },
        )
        receipt["admission_sha256"] = file_hash(output / "admission.json")
        reference, pids, trials = None, set(), []
        for row in rows:
            if (
                time.monotonic() - started + settings.trial_timeout_s + RESERVE_S
                > settings.wall_limit_s
            ):
                receipt["failure"] = "budget_admission"
                break
            slot = Slot.model_validate({k: row[k] for k in Slot.model_fields})
            directory = output / f"slot-{slot.index:03d}"
            directory.mkdir(mode=0o700)
            request = {
                "settings": settings.model_dump(),
                "slot": slot.model_dump(),
                "video": str(video),
                "video_sha256": source,
            }
            atomic_json(directory / "request.json", request)
            row.update(status="running", started_elapsed_s=time.monotonic() - started)
            atomic_json(output / "progress.json", receipt)
            try:
                if "pynv" in settings.backends:
                    check = checked_idle(
                        slot.block, "before", directory / "idle-diagnostics"
                    )
                    row["idle_before"] = check.model_dump(mode="json")
                    if not check.quiescent:
                        raise ValueError("GPU not idle")
                try:
                    raw = isolated_trial(
                        [
                            sys.executable,
                            "-m",
                            "benchmarks.experiments.native",
                            "--worker-request",
                            str(directory / "request.json"),
                        ],
                        cwd=ROOT,
                        timeout=settings.trial_timeout_s,
                        diagnostics=directory / "worker-diagnostics",
                    )
                finally:
                    if "pynv" in settings.backends:
                        check = checked_idle(
                            slot.block, "after", directory / "idle-diagnostics"
                        )
                        row["idle_after"] = check.model_dump(mode="json")
                        if not check.quiescent:
                            raise ValueError("GPU cleanup not confirmed")
                trial = NativeTrial.model_validate_json(raw)
                if (
                    trial.slot != slot
                    or trial.process_id in pids
                    or trial.settings_sha256 != digest(settings.model_dump())
                    or trial.media.sha256 != source
                    or trial.software_sha256 != software
                    or trial.lock_sha256 != lock
                    or trial.import_origins != imported
                    or trial.environment != env
                    or (reference is not None and trial_identity(trial) != reference)
                ):
                    raise ValueError("worker identity mismatch")
                if trial.trace:
                    verify_trace(directory, trial.trace, trial.metrics)
                if "pynv" in settings.backends and (
                    trial.gpu is None
                    or trial.gpu.model_dump(mode="json") != row["idle_before"]["gpu"]
                    or trial.gpu.model_dump(mode="json") != row["idle_after"]["gpu"]
                ):
                    raise ValueError("GPU idle/worker identity mismatch")
                reference = trial_identity(trial)
                pids.add(trial.process_id)
                atomic_json(directory / "trial.json", trial.model_dump(mode="json"))
                bundle_bytes(output)
                row.update(
                    status="completed",
                    trial_ref=f"slot-{slot.index:03d}/trial.json",
                    trial_sha256=file_hash(directory / "trial.json"),
                    trial_bytes=(directory / "trial.json").stat().st_size,
                )
                trials.append(trial)
            except BaseException as error:
                row.update(
                    status="cancelled"
                    if isinstance(error, KeyboardInterrupt)
                    else "failed",
                    failure=type(error).__name__,
                )
                receipt["failure"] = "trial_failure"
                failure_path = directory / "failure.json"
                if (
                    failure_path.is_file()
                    and not failure_path.is_symlink()
                    and failure_path.stat().st_size <= 1024
                ):
                    failure = json.loads(failure_path.read_bytes())
                    if all(
                        isinstance(failure.get(k), str)
                        and re.fullmatch(r"[a-zA-Z_]{1,64}", failure[k])
                        for k in ("phase", "failure")
                    ):
                        row["worker_failure"] = failure
                break
            finally:
                row["finished_elapsed_s"] = time.monotonic() - started
                atomic_json(output / "progress.json", receipt)
        if receipt["failure"] is None:
            if (
                source != file_hash(video)
                or software != software_hash(ROOT)
                or lock != file_hash(ROOT / "uv.lock")
            ):
                raise ValueError("campaign source changed")
            receipt["status"] = "complete"
    except BaseException as error:
        receipt.update(status="incomplete", failure=type(error).__name__)
    receipt["elapsed_s"] = time.monotonic() - started
    if receipt["elapsed_s"] > settings.wall_limit_s:
        receipt.update(status="incomplete", failure="wall_limit_exceeded")
    receipt["summary"] = summarize(receipt, trials if "trials" in locals() else [])
    atomic_json(output / "campaign.json", receipt)
    return receipt


def load_receipt(root):
    """Verify frozen schedule, trial identities, hashes and referenced scalar traces."""
    path = root / "campaign.json"
    if path.is_symlink() or not 0 < path.stat().st_size <= MAX_JSON_BYTES:
        raise ValueError("receipt path/size")
    receipt = json.loads(path.read_bytes())
    if receipt["schema_version"] != "native_campaign.1" or any(
        receipt[k] is not False
        for k in (
            "acceptance_eligible",
            "thresholds_approved",
            "harness_overhead_qualified",
        )
    ):
        raise ValueError("campaign schema/qualification")
    settings = Settings.model_validate(receipt["settings"])
    rows = receipt["rows"]
    elapsed = receipt["elapsed_s"]
    if type(elapsed) not in {float, int} or not math.isfinite(elapsed) or elapsed < 0:
        raise ValueError("invalid campaign elapsed time")
    if receipt["status"] == "complete" and elapsed > settings.wall_limit_s:
        raise ValueError("completed campaign exceeded wall limit")
    intent_path = root / "intent.json"
    if (
        intent_path.is_symlink()
        or not 0 < intent_path.stat().st_size <= MAX_JSON_BYTES
        or file_hash(intent_path) != receipt["intent_sha256"]
    ):
        raise ValueError("frozen intent identity")
    intent = json.loads(intent_path.read_bytes())
    if any(
        intent[k] != receipt[k]
        for k in (
            "schema_version",
            "settings",
            "acceptance_eligible",
            "thresholds_approved",
            "harness_overhead_qualified",
        )
    ) or intent["rows"] != [
        s.model_dump() | {"status": "not_run"} for s in schedule(settings)
    ]:
        raise ValueError("changed frozen intent")
    admission_keys = (
        "video_sha256",
        "software_sha256",
        "lock_sha256",
        "environment",
        "import_origins",
    )
    if any(k in receipt for k in admission_keys):
        admission_path = root / "admission.json"
        if (
            admission_path.is_symlink()
            or not 0 < admission_path.stat().st_size <= MAX_JSON_BYTES
            or file_hash(admission_path) != receipt["admission_sha256"]
            or json.loads(admission_path.read_bytes())
            != {k: receipt[k] for k in admission_keys}
        ):
            raise ValueError("frozen admission identity")
    if len(rows) != len(schedule(settings)):
        raise ValueError("missing scheduled slots")
    trials, pids, reference, previous_end, halted = [], set(), None, 0.0, False
    for row, expected in zip(rows, schedule(settings), strict=True):
        if Slot.model_validate({k: row[k] for k in Slot.model_fields}) != expected:
            raise ValueError("changed schedule")
        if row["status"] not in {
            "completed",
            "not_run",
            "running",
            "failed",
            "cancelled",
        }:
            raise ValueError("unknown slot status")
        if row["status"] == "not_run":
            halted = True
            if any(k in row for k in ("started_elapsed_s", "finished_elapsed_s")):
                raise ValueError("unrun slot has execution clocks")
        else:
            start, end = row["started_elapsed_s"], row["finished_elapsed_s"]
            if (
                halted
                or any(
                    type(t) not in {float, int} or not math.isfinite(t)
                    for t in (start, end)
                )
                or not previous_end <= start <= end <= elapsed
            ):
                raise ValueError("slot timeline mismatch")
            previous_end = end
            halted = row["status"] != "completed"
        if row["status"] != "completed":
            if any(k in row for k in ("trial_ref", "trial_sha256", "trial_bytes")):
                raise ValueError("trial evidence on noncompleted slot")
            continue
        name = f"slot-{expected.index:03d}/trial.json"
        trial_path = root / name
        if (
            row["trial_ref"] != name
            or trial_path.is_symlink()
            or trial_path.parent.is_symlink()
            or not trial_path.resolve().is_relative_to(root.resolve())
            or not 0 < trial_path.stat().st_size <= MAX_JSON_BYTES
            or trial_path.stat().st_size != row["trial_bytes"]
            or file_hash(trial_path) != row["trial_sha256"]
        ):
            raise ValueError("trial identity/path")
        trial = NativeTrial.model_validate_json(trial_path.read_bytes())
        if (
            trial.slot != expected
            or trial.process_id in pids
            or trial.settings_sha256 != digest(settings.model_dump())
            or any(
                not re.fullmatch(r"[a-f0-9]{64}", receipt[k])
                for k in ("video_sha256", "software_sha256", "lock_sha256")
            )
            or trial.media.sha256 != receipt["video_sha256"]
            or trial.software_sha256 != receipt["software_sha256"]
            or trial.lock_sha256 != receipt["lock_sha256"]
            or trial.environment != Environment.model_validate(receipt["environment"])
            or trial.import_origins != receipt["import_origins"]
            or (reference is not None and trial_identity(trial) != reference)
        ):
            raise ValueError("trial runtime/configuration mismatch")
        if "pynv" in settings.backends:
            for phase in ("before", "after"):
                check = GPUIdleCheck.model_validate(row["idle_" + phase])
                if (
                    not check.quiescent
                    or check.pair != expected.block
                    or check.phase != phase
                    or trial.gpu is None
                    or check.gpu != trial.gpu
                ):
                    raise ValueError("missing matched GPU idle evidence")
        if trial.trace:
            verify_trace(trial_path.parent, trial.trace, trial.metrics)
        pids.add(trial.process_id)
        reference = trial_identity(trial)
        trials.append(trial)
    if receipt["status"] == "complete":
        if receipt["failure"] is not None or len(trials) != len(rows):
            raise ValueError("incomplete fixed plan marked complete")
    elif receipt["status"] != "incomplete" or not receipt["failure"]:
        raise ValueError("campaign status/failure")
    if receipt["summary"] != summarize(receipt, trials):
        raise ValueError("summary differs from trial evidence")
    return receipt, trials


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--backends", nargs="+", choices=("opencv", "pynv"), default=["opencv", "pynv"]
    )
    parser.add_argument(
        "--tasks",
        nargs="+",
        choices=("full", "selected", "prepared"),
        default=["full", "selected", "prepared"],
    )
    parser.add_argument("--blocks", type=int, default=10)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--seed", type=int, default=1729)
    parser.add_argument("--trial-timeout", type=float, default=180)
    parser.add_argument("--wall-limit", type=float, default=3600)
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--worker-request", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--idle-block", type=int, help=argparse.SUPPRESS)
    parser.add_argument(
        "--idle-phase", choices=("before", "after"), help=argparse.SUPPRESS
    )
    args = parser.parse_args()
    if args.idle_block is not None:
        if not 0 <= args.idle_block < 20 or args.idle_phase is None:
            parser.error("invalid idle helper protocol")
        try:
            print(idle_check(args.idle_block, args.idle_phase).model_dump_json())
            return 0
        except Exception as error:
            print(json.dumps({"failure": type(error).__name__}), file=sys.stderr)
            return 3
    if args.worker_request:
        try:
            if args.worker_request.stat().st_size > 16384:
                raise ValueError("worker request budget")
            result = asyncio.run(
                worker(
                    json.loads(args.worker_request.read_bytes()),
                    args.worker_request.parent,
                )
            )
            print(result.model_dump_json())
            return 0
        except Exception as error:
            phase = "preflight"
            phase_path = args.worker_request.parent / "phase.json"
            if phase_path.is_file() and phase_path.stat().st_size <= 1024:
                phase = json.loads(phase_path.read_bytes())["phase"]
            failure: dict[str, object] = {
                "phase": phase,
                "failure": type(error).__name__,
            }
            if isinstance(error, MediaError):
                failure.update(reason=error.reason, details=error.details)
            atomic_json(
                args.worker_request.parent / "failure.json",
                failure,
            )
            print(json.dumps({"failure": type(error).__name__}))
            return 3
    try:
        settings = Settings(
            backends=tuple(args.backends),
            tasks=tuple(args.tasks),
            blocks=args.blocks,
            warmups=args.warmups,
            seed=args.seed,
            trial_timeout_s=args.trial_timeout,
            wall_limit_s=args.wall_limit,
        )
    except ValueError:
        parser.error("invalid bounded configuration")
    if args.plan:
        print(
            json.dumps(
                {
                    "settings": settings.model_dump(),
                    "schedule": [s.model_dump() for s in schedule(settings)],
                    "inference_executed": False,
                }
            )
        )
        return 0
    if args.video is None or args.output is None:
        parser.error("--video and --output required")
    try:
        receipt = run(args.video, args.output.resolve(), settings)
    except FileExistsError:
        parser.error("immutable campaign directory already exists; no retry")
    print(
        json.dumps(
            {
                "status": receipt["status"],
                "failure": receipt["failure"],
                "slots": len(receipt["rows"]),
            }
        )
    )
    return 0 if receipt["status"] == "complete" else 3


if __name__ == "__main__":
    raise SystemExit(main())
