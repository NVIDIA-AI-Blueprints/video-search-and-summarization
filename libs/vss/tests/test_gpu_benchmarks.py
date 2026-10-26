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

"""GPU evidence and diagnostic failure gates without requiring a GPU in CI."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from benchmarks import gpu, gpu_resources
from benchmarks.artifacts import aggregate, publish_bundle, read_trace, write_trace
from benchmarks.diagnostics import tool_diagnostics
from benchmarks.gpu import inventory, summary, verify, verify_bundle
from benchmarks.gpu_resources import GPUIdleCheck, GPUSample, GPUSampler, GPUTrace
from benchmarks.gpu_results import GPUConfiguration, GPUResult, GPUTrial
from benchmarks.media import MediaError, bounded_tool
from benchmarks.supervisor import TrialCleanupError, isolated_trial
from benchmarks.video_results import balanced_orders
from vss.observability import collect_vss_metrics, metric_scope, record


@pytest.mark.parametrize("failure", ["nonzero", "error_stderr", "timeout", "overflow"])
def test_native_failure_logs_survive_cleanup_and_remain_bounded(
    tmp_path, monkeypatch, failure
):
    original = subprocess.Popen

    def start(*args, **kwargs):
        child = original(*args, **kwargs)
        # Exclude interpreter startup from this diagnostic-retention fault.
        # Keep startup bounded, including on shared Windows runners.
        deadline = time.monotonic() + 10
        while (
            kwargs["stderr"].tell() == 0
            and child.poll() is None
            and time.monotonic() < deadline
        ):
            time.sleep(0.005)
        return child

    monkeypatch.setattr(subprocess, "Popen", start)
    code = (
        "import sys; sys.stderr.write('/private/input/video.mp4: decode failed\\n'); "
        "sys.stderr.flush(); "
    )
    if failure == "nonzero":
        code += "sys.exit(3)"
    elif failure == "timeout":
        code += "sys.stderr.flush(); import time; time.sleep(10)"
    elif failure == "overflow":
        code += "sys.stderr.write('x'*2_000_000); sys.stderr.flush()"
    with tool_diagnostics(tmp_path), pytest.raises(MediaError):
        bounded_tool([sys.executable, "-c", code], limit=1024, timeout=0.1)
    record = json.loads(next(tmp_path.glob("*.json")).read_bytes())
    assert "decode failed" in record["stderr"]
    assert "/private/input/video.mp4" not in record["stderr"]
    assert record["reason"] != "ok" and "command" not in record
    assert sum(p.stat().st_size for p in tmp_path.iterdir()) <= 16 * 1024**2


@pytest.mark.skipif(os.name != "posix", reason="owned POSIX worker supervision")
@pytest.mark.parametrize("failure", ["nonzero", "timeout", "overflow"])
def test_worker_failure_stderr_survives_process_group_cleanup(tmp_path, failure):
    code = "import sys,time; sys.stderr.write('worker failed\\n'); sys.stderr.flush(); "
    code += {
        "nonzero": "sys.exit(3)",
        "timeout": "time.sleep(10)",
        "overflow": (
            "sys.stderr.write('x'*2_000_000); sys.stderr.flush(); time.sleep(10)"
        ),
    }[failure]
    with pytest.raises(
        (
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            ValueError,
            TrialCleanupError,
        )
    ):
        isolated_trial(
            [sys.executable, "-c", code],
            cwd=tmp_path,
            timeout=0.1,
            diagnostics=tmp_path / "logs",
        )
    record = json.loads(next((tmp_path / "logs").glob("*.json")).read_bytes())
    assert "worker failed" in record["stderr"] and record["reason"] != "ok"


@pytest.mark.parametrize("fault", ["pid_mode", "capabilities", "uuid", "cuda", "mig"])
def test_gpu_identity_rejects_wrong_namespace_device_and_mig(
    tmp_path, monkeypatch, fault
):
    uuid = "GPU-11111111-1111-1111-1111-111111111111"
    manifest = {
        "pid_mode": "host",
        "capabilities": "compute,utility,video",
        "gpu_uuid_sha256": hashlib.sha256(uuid.lower().encode()).hexdigest(),
        "image_id": "sha256:" + "a" * 64,
        "base_digest": "sha256:" + "b" * 64,
    }
    if fault == "pid_mode":
        manifest["pid_mode"] = "private"
    if fault == "capabilities":
        manifest["capabilities"] = "compute,utility"
    if fault == "uuid":
        manifest["gpu_uuid_sha256"] = "0" * 64
    path = tmp_path / "runner.json"
    path.write_text(json.dumps(manifest))
    monkeypatch.setenv("M8_RUNNER_MANIFEST", str(path))
    monkeypatch.setattr(
        gpu_resources, "cuda_uuid", lambda: "different" if fault == "cuda" else uuid
    )

    class Unsupported(Exception):
        pass

    n = SimpleNamespace(
        nvmlDeviceGetUUID=lambda h: uuid,
        nvmlDeviceGetMigMode=lambda h: (1 if fault == "mig" else 0, 0),
        NVMLError_NotSupported=Unsupported,
    )
    with pytest.raises(ValueError):
        gpu_resources.runner_identity(n, object())


def fake_sampler(rows):
    sampler = GPUSampler.__new__(GPUSampler)
    sampler.process = SimpleNamespace(
        pid=123, children=lambda **kw: [SimpleNamespace(pid=124)]
    )
    sampler.handle = object()
    sampler.samples = []
    sampler.complete = True
    sampler.stop = threading.Event()
    sampler.started = time.perf_counter()

    class Unsupported(Exception):
        pass

    sampler.nvml = SimpleNamespace(
        nvmlDeviceGetComputeRunningProcesses=lambda h: rows,
        nvmlDeviceGetGraphicsRunningProcesses=lambda h: rows,
        nvmlDeviceGetDecoderUtilization=lambda h: (5, 100000),
        nvmlDeviceGetMemoryInfo=lambda h: SimpleNamespace(used=500),
        nvmlDeviceGetUtilizationRates=lambda h: SimpleNamespace(gpu=3),
        NVMLError_NotSupported=Unsupported,
    )
    return sampler


def test_gpu_process_attribution_deduplicates_and_keeps_unknown_distinct():
    rows = [
        SimpleNamespace(pid=123, usedGpuMemory=100),
        SimpleNamespace(pid=124, usedGpuMemory=50),
    ]
    sampler = fake_sampler(rows)
    sampler._sample()
    assert sampler.samples[0].worker_tree_used_bytes == 150 and sampler.complete
    rows.append(SimpleNamespace(pid=999, usedGpuMemory=None))
    sampler = fake_sampler(rows)
    sampler._sample()
    assert sampler.samples[0].worker_tree_used_bytes is None
    assert sampler.samples[0].unrelated_processes == 1 and not sampler.complete
    sampler = fake_sampler([])
    sampler._sample()
    assert sampler.samples[0].worker_tree_used_bytes == 0


def test_gpu_sampler_failure_overflow_and_trace_gap_invalidate_evidence():
    sampler = fake_sampler([])
    sampler.nvml.nvmlDeviceGetMemoryInfo = lambda h: (_ for _ in ()).throw(OSError())
    sampler._sample()
    assert not sampler.complete
    sampler = fake_sampler([])
    sampler.samples = [None] * 6000
    sampler._sample()
    assert not sampler.complete and sampler.stop.is_set()
    sample = GPUSample(
        elapsed_s=0,
        device_used_bytes=0,
        worker_tree_used_bytes=None,
        gpu_utilization_pct=0,
        unrelated_processes=0,
    )
    with pytest.raises(ValidationError):
        GPUTrace(
            complete=True,
            samples=(sample, sample.model_copy(update={"elapsed_s": 1.1})),
        )


def test_failed_gpu_reports_no_statistics_and_binds_diagnostics(tmp_path):
    root = tmp_path / "bundle"
    logs = root / "diagnostics/parent"
    logs.mkdir(parents=True)
    path = logs / ("a" * 32 + ".json")
    path.write_text("{}")
    result = GPUResult(
        configuration=GPUConfiguration(recipe="tiny"),
        status="incomplete",
        failure="timeout",
        artifacts=inventory(root),
    )
    verify_bundle(result, root)
    assert summary(result)["statistics"] is None
    path.write_text("changed")
    with pytest.raises(ValueError):
        verify_bundle(result, root)
    with pytest.raises(ValidationError):
        GPUResult.model_validate(
            result.model_dump() | {"status": "ok", "failure": None}
        )
    with pytest.raises(ValidationError):
        GPUConfiguration(recipe="tiny", decoder="auto")
    with pytest.raises(ValidationError):
        GPUConfiguration(recipe="tiny", offline_e2e=True)


@pytest.fixture
def successful_pair(tmp_path):
    # An independent two-frame ledger exercises batched native delivery and EOF:
    # pending is a gauge (1 then 0), delivered includes the terminal read.
    with (
        collect_vss_metrics() as collection,
        metric_scope(
            source_id="fixture-source",
            request_id="a" * 32,
            backend="pynv",
            device="gpu",
        ),
    ):
        for name in ("open", "close", "decode", "decode.lifetime", "consumer_wait"):
            record("processing." + name, 0.01, library="processing")
        for name, value in (
            ("selected_frames", 2),
            ("copied_bytes", 24),
            ("sessions", 1),
            ("select", 1),
        ):
            record("processing." + name, value, library="processing")
        record("processing.native.sessions", 1, library="processing")
        record("processing.native.discarded_frames", 0, library="processing")
        for produced, delivered, pending, flush in ((2, 2, 1, 0), (0, 1, 0, 1)):
            for name in ("read", "decode", "copy"):
                record("processing.native." + name, 0.01, library="processing")
            for name, value in (
                ("produced_frames", produced),
                ("delivered_frames", delivered),
                ("pending_frames", pending),
                ("flushes", flush),
            ):
                record("processing.native." + name, value, library="processing")
    records = collection.observations
    trace = write_trace(tmp_path, records)
    common = dict(
        status="ok",
        failure=None,
        wall_s=0.1,
        cpu_s=0.05,
        frames=2,
        pts_checks=2,
        pixel_checks=2,
        maximum_rgb_mae=0.1,
        correctness=True,
    )
    disabled = common | dict(
        mode="disabled", collection_complete=None, metrics={}, trace=None
    )
    enabled = common | dict(
        mode="enabled",
        collection_complete=True,
        metrics=aggregate(records),
        trace=trace,
    )
    config = GPUConfiguration(recipe="tiny", repetitions=1, warmups=0)
    order = balanced_orders(1, config.seed)[0]
    data = dict(
        status="ok",
        process_id=1234,
        order=order,
        configuration=config,
        environment=dict(
            python="3.13.15",
            system="Linux",
            architecture="x86_64",
            os_release="fixture",
            cpu_count=16,
            dependencies={
                p: "1.0"
                for p in (
                    "pynvvideocodec",
                    "nvidia-ml-py",
                    "opencv-python-headless",
                    "psutil",
                )
            },
        ),
        native_builds={
            k: "a" * 64 if k.endswith("_sha256") else "1.0"
            for k in (
                "opencv",
                "psutil",
                "opencv_build_sha256",
                "ffmpeg_build_sha256",
                "ffprobe_build_sha256",
                "pynv_binary_sha256",
            )
        },
        software_sha256="b" * 64,
        lockfile_sha256="c" * 64,
        gpu=dict(
            uuid_sha256="d" * 64,
            name="fixture",
            driver="1",
            memory_bytes=1000,
            image_id="sha256:" + "e" * 64,
            base_digest="sha256:" + "f" * 64,
        ),
        media=dict(
            sha256="0" * 64,
            bytes=100,
            width=2,
            height=2,
            codec="h264",
            duration_s=1,
            pts_ns=(0, 500_000_000),
            oracle_indices=(0, 1),
            oracle_rgb_sha256="1" * 64,
            container_frame_count=2,
        ),
        periods=(disabled, enabled)
        if order == "disabled_first"
        else (enabled, disabled),
        resources=dict(
            interval_s=0.05,
            complete=True,
            samples=[
                dict(
                    elapsed_s=t,
                    self_rss_bytes=1,
                    tree_rss_bytes=1,
                    observed_processes=1,
                )
                for t in (0, 0.2)
            ],
            self_cpu_time_s=0.1,
            reaped_children_cpu_time_s=0,
            self_read_bytes=0,
            self_write_bytes=0,
            io_scope="worker_only",
        ),
        gpu_trace=dict(
            complete=True,
            samples=[
                dict(
                    elapsed_s=t,
                    device_used_bytes=10,
                    worker_tree_used_bytes=5,
                    gpu_utilization_pct=0,
                    unrelated_processes=0,
                )
                for t in (0, 0.2)
            ],
        ),
    )
    return GPUTrial.model_validate(data), tmp_path


def complete_result(trial, root):
    return GPUResult(
        configuration=trial.configuration,
        status="ok",
        trials=(trial,),
        artifacts=inventory(root),
        idle_checks=tuple(
            GPUIdleCheck(
                pair=0,
                phase=phase,
                gpu=trial.gpu,
                elapsed_s=0,
                polls=1,
                quiescent=True,
                remaining_processes=0,
            )
            for phase in ("before", "after")
        ),
    )


def test_valid_gpu_native_ledger_opaque_lineage_and_immutable_bundle(
    successful_pair, tmp_path
):
    trial, root = successful_pair
    assert verify(trial, root)
    result = complete_result(trial, root)
    verify_bundle(result, root)
    assert summary(result)["acceptance_eligible"] is False
    # Publication consumes staging; a second publication cannot overwrite it.
    destination = tmp_path.parent / (tmp_path.name + "-sealed.json")
    publish_bundle(destination, root, result)
    with pytest.raises(FileExistsError):
        publish_bundle(destination, root, result)
    verify_bundle(result, destination.with_suffix(".artifacts"))


@pytest.mark.parametrize(
    "fault",
    [
        "produced",
        "delivered",
        "flush",
        "discard",
        "copy_bytes",
        "read_missing",
        "role_count",
        "build_missing",
        "build_hash",
        "package_missing",
        "pts",
        "rgb",
        "sampler",
        "idle_missing",
        "idle_busy",
    ],
)
def test_gpu_success_rejects_missing_or_corrupt_evidence(successful_pair, fault):
    trial, root = successful_pair
    data = trial.model_dump()
    enabled = next(p for p in data["periods"] if p["mode"] == "enabled")
    names = {
        "produced": "processing.native.produced_frames",
        "delivered": "processing.native.delivered_frames",
        "flush": "processing.native.flushes",
        "discard": "processing.native.discarded_frames",
        "copy_bytes": "processing.copied_bytes",
    }
    if fault in names:
        enabled["metrics"][names[fault]]["sum"] += 1
    elif fault == "read_missing":
        del enabled["metrics"]["processing.native.read"]
    elif fault == "role_count":
        enabled["metrics"]["processing.native.decode"]["count"] += 1
    elif fault == "build_missing":
        data["native_builds"] = {}
    elif fault == "build_hash":
        data["native_builds"]["pynv_binary_sha256"] = "bad"
    elif fault == "package_missing":
        del data["environment"]["dependencies"]["pynvvideocodec"]
    elif fault == "pts":
        enabled["pts_checks"] -= 1
    elif fault == "rgb":
        enabled["maximum_rgb_mae"] = 4
    elif fault == "sampler":
        data["gpu_trace"]["complete"] = False
    elif fault.startswith("idle_"):
        result = complete_result(trial, root).model_dump()
        if fault == "idle_missing":
            result["idle_checks"] = ()
        else:
            result["idle_checks"][0].update(quiescent=False, remaining_processes=1)
        with pytest.raises(ValidationError):
            GPUResult.model_validate(result)
        return
    with pytest.raises(ValidationError):
        GPUTrial.model_validate(data)


@pytest.mark.parametrize(
    "fault",
    ["device", "backend", "unknown_source", "duplicate_span", "pending", "hash"],
)
def test_gpu_trace_corruption_rejected_even_with_recomputed_aggregates(
    successful_pair, fault
):
    trial, root = successful_pair
    period = next(p for p in trial.periods if p.trace)
    events = list(read_trace(root, period.trace))
    if fault == "hash":
        (root / period.trace.name).write_bytes(b"changed")
    else:
        index = next(
            i
            for i, r in enumerate(events)
            if r.name == "processing.native.pending_frames"
        )
        if fault == "device":
            events[index] = events[index].model_copy(update={"device": "cpu"})
        elif fault == "backend":
            events[index] = events[index].model_copy(update={"backend": "unknown"})
        elif fault == "unknown_source":
            events = [r.model_copy(update={"source_id": "unknown"}) for r in events]
        elif fault == "duplicate_span":
            events[index] = events[index].model_copy(
                update={"span_id": events[0].span_id}
            )
        else:
            index = max(
                i
                for i, r in enumerate(events)
                if r.name == "processing.native.pending_frames"
            )
            events[index] = events[index].model_copy(update={"value": 1.0})
        changed = period.model_copy(
            update={
                "trace": write_trace(root, tuple(events)),
                "metrics": aggregate(tuple(events)),
            }
        )
        trial = GPUTrial.model_validate(
            trial.model_dump()
            | {"periods": tuple(changed if p.trace else p for p in trial.periods)}
        )
    with pytest.raises(ValueError):
        verify(trial, root)


@pytest.mark.parametrize(
    "fault", ["timeout", "cleanup", "death", "cancel", "invalid", "reuse"]
)
@pytest.mark.skipif(os.name != "posix", reason="POSIX GPU controller runtime")
def test_gpu_parent_keeps_valid_prefix_and_failure_inventory(
    successful_pair, monkeypatch, fault
):
    trial, root = successful_pair
    output = root / "parent.json"
    calls = 0
    config = GPUConfiguration(recipe="tiny", repetitions=2)
    orders = balanced_orders(2, config.seed)

    def check(pair, phase):
        return GPUIdleCheck(
            pair=pair,
            phase=phase,
            gpu=trial.gpu,
            elapsed_s=0,
            polls=1,
            quiescent=True,
            remaining_processes=0,
        )

    def isolated(command, *, cwd, timeout, diagnostics):
        nonlocal calls
        calls += 1
        if calls == 2 and fault != "reuse":
            diagnostics.mkdir(parents=True, exist_ok=True)
            (diagnostics / ("a" * 32 + ".json")).write_text(
                '{"reason":"fixture_failure"}'
            )
            raise {
                "timeout": subprocess.TimeoutExpired(command, timeout),
                "cleanup": TrialCleanupError("fixture cleanup"),
                "death": subprocess.CalledProcessError(9, command),
                "cancel": KeyboardInterrupt(),
                "invalid": ValueError("fixture protocol"),
            }[fault]
        artifacts = diagnostics.parent.parent
        (artifacts / "traces").mkdir(exist_ok=True)
        for period in trial.periods:
            if period.trace:
                shutil.copyfile(root / period.trace.name, artifacts / period.trace.name)
        data = trial.model_dump()
        data.update(
            configuration=config, order=orders[calls - 1], process_id=1234 + calls
        )
        data["periods"] = sorted(
            data["periods"],
            key=lambda p: (
                p["mode"]
                == ("enabled" if data["order"] == "disabled_first" else "disabled")
            ),
        )
        return GPUTrial.model_validate(data).model_dump_json().encode()

    monkeypatch.setattr(gpu, "idle_check", check)
    monkeypatch.setattr(gpu, "isolated_trial", isolated)
    monkeypatch.setattr(
        sys,
        "argv",
        ["gpu", "--recipe", "tiny", "--repetitions", "2", "--output", str(output)],
    )
    assert gpu.main() == 3
    result = GPUResult.model_validate_json(output.read_bytes())
    assert result.status == "incomplete" and len(result.trials) == 1
    assert len(result.idle_checks) == 4
    assert result.failure == {
        "timeout": "timeout",
        "cleanup": "cleanup_failure",
        "cancel": "cancelled",
    }.get(fault, "invalid_child")
    verify_bundle(result, output.with_suffix(".artifacts"))
    assert summary(result)["statistics"] is None


def test_gpu_idle_gate_retains_busy_device_evidence(monkeypatch):
    sampler = fake_sampler([SimpleNamespace(pid=999, usedGpuMemory=None)])
    sampler.identity = SimpleNamespace()
    # Use a real schema identity; this test does not invoke CUDA or NVML.
    sampler.identity = gpu_resources.GPUIdentity(
        uuid_sha256="a" * 64,
        name="fixture",
        driver="1",
        memory_bytes=1,
        image_id="sha256:" + "b" * 64,
        base_digest="sha256:" + "c" * 64,
    )
    sampler.close_unused = lambda: None
    monkeypatch.setattr(gpu_resources, "GPUSampler", lambda: sampler)
    clock = iter((0, 5.1))
    monkeypatch.setattr(gpu_resources.time, "perf_counter", lambda: next(clock))
    check = gpu_resources.idle_check(0, "before")
    assert not check.quiescent and check.remaining_processes == 1


@pytest.mark.parametrize(
    "helper",
    [
        "native",
        pytest.param(
            "worker",
            marks=pytest.mark.skipif(
                os.name != "posix", reason="owned POSIX worker supervision"
            ),
        ),
    ],
)
def test_helper_interrupt_reaps_child_and_retains_diagnostic(
    tmp_path, monkeypatch, helper
):
    original = subprocess.Popen
    children = []

    def start(*args, **kwargs):
        child = original(*args, **kwargs)
        children.append(child)
        # Confirm a real child wrote its diagnostic before injecting interruption.
        deadline = time.monotonic() + 2
        while kwargs["stderr"].tell() == 0 and time.monotonic() < deadline:
            time.sleep(0.005)
        wait = child.wait
        interrupted = False

        def interrupt_once(*args, **kwargs):
            nonlocal interrupted
            if not interrupted:
                interrupted = True
                raise KeyboardInterrupt()
            return wait(*args, **kwargs)

        child.wait = interrupt_once
        return child

    monkeypatch.setattr(subprocess, "Popen", start)
    command = [
        sys.executable,
        "-c",
        "import sys,time; sys.stderr.write('interrupted native worker\\n'); "
        "sys.stderr.flush(); time.sleep(10)",
    ]
    with (
        pytest.raises((KeyboardInterrupt, TrialCleanupError)),
        tool_diagnostics(tmp_path),
    ):
        if helper == "native":
            bounded_tool(command, limit=1024, timeout=2)
        else:
            isolated_trial(command, cwd=tmp_path, timeout=2, diagnostics=tmp_path)
    assert children[0].poll() is not None
    diagnostic = json.loads(next(tmp_path.glob("*.json")).read_bytes())
    assert "interrupted native worker" in diagnostic["stderr"]
    assert diagnostic["reason"] != "ok"


@pytest.mark.parametrize(
    "field",
    ["gpu", "software_sha256", "lockfile_sha256", "media", "configuration", "profile"],
)
def test_success_reader_rejects_mixed_identity_or_profile(successful_pair, field):
    trial, root = successful_pair
    if field == "profile":
        data = complete_result(trial, root).model_dump() | {
            "profile": "video.cpu.paired.v1"
        }
    else:
        config = GPUConfiguration(recipe="tiny", repetitions=2, warmups=0)
        orders = balanced_orders(2, config.seed)
        trials = []
        for i in range(2):
            item = trial.model_dump()
            item.update(configuration=config, process_id=1234 + i, order=orders[i])
            item["periods"] = sorted(
                item["periods"],
                key=lambda p: (
                    p["mode"]
                    == ("enabled" if orders[i] == "disabled_first" else "disabled")
                ),
            )
            if i:
                enabled = next(p for p in item["periods"] if p["trace"])
                records = tuple(
                    r.model_copy(update={"run_id": "b" * 32})
                    for r in read_trace(
                        root, trial.periods[0 if trial.periods[0].trace else 1].trace
                    )
                )
                enabled["trace"] = write_trace(root, records).model_dump()
                if field == "gpu":
                    item["gpu"]["uuid_sha256"] = "f" * 64
                elif field == "media":
                    item["media"]["sha256"] = "f" * 64
                elif field == "configuration":
                    item["configuration"] = config.model_dump() | {"seed": 42}
                else:
                    item[field] = "f" * 64
            trials.append(item)
        data = dict(
            configuration=config,
            status="ok",
            trials=trials,
            artifacts=inventory(root),
            idle_checks=[
                dict(
                    pair=i,
                    phase=phase,
                    gpu=trial.gpu,
                    elapsed_s=0,
                    polls=1,
                    quiescent=True,
                    remaining_processes=0,
                )
                for i in range(2)
                for phase in ("before", "after")
            ],
        )
    with pytest.raises(ValidationError):
        GPUResult.model_validate(data)
