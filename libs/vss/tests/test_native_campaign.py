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

"""Native CPU execution and fail-closed campaign/export controls, without CUDA."""

import asyncio
import copy
import csv
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from benchmarks import media
from benchmarks.diagnostics import tool_diagnostics
from benchmarks.experiments import export, native
from benchmarks.experiments.native_models import POLICIES, NativeTrial, Settings
from benchmarks.gpu_resources import GPUIdentity, GPUIdleCheck, GPUSample, GPUTrace
from benchmarks.media import file_hash, generated
from benchmarks.processing import preflight
from benchmarks.supervisor import TrialCleanupError


@pytest.fixture(scope="module")
def video(tmp_path_factory):
    pytest.importorskip("cv2")
    directory = tmp_path_factory.mktemp("native-campaign-media")
    return generated(directory, "tiny", preflight()[0]["ffmpeg"])


def request(video, *, task="full", policy="minimal", warmups=0):
    settings = Settings(backends=("opencv",), tasks=(task,), blocks=2, warmups=warmups)
    slot = next(s for s in native.schedule(settings) if s.policy == policy)
    return {
        "settings": settings.model_dump(),
        "slot": slot.model_dump(),
        "video": str(video),
        "video_sha256": file_hash(video),
    }


@pytest.fixture(scope="module")
def measured(video, tmp_path_factory):
    directory = tmp_path_factory.mktemp("native-measured")
    trial = asyncio.run(native.worker(request(video), directory))
    return trial


@pytest.fixture(scope="module")
def complete_cpu(video, tmp_path_factory):
    """Actual fresh-process controller success across all four observer policies."""
    root = tmp_path_factory.mktemp("native-complete") / "campaign"
    result = native.run(
        video,
        root,
        Settings(
            backends=("opencv",),
            tasks=("full",),
            blocks=2,
            warmups=0,
            trial_timeout_s=30,
            wall_limit_s=300,
        ),
    )
    assert result["status"] == "complete", result["failure"]
    receipt, trials = native.load_receipt(root)
    assert len(trials) == len(receipt["rows"]) == 8
    return root, receipt, trials


def test_every_task_policy_has_even_balanced_cpu_gpu_order():
    settings = Settings()
    rows = native.schedule(settings)
    assert rows == native.schedule(settings)
    assert len(rows) == 240
    for task in settings.tasks:
        for policy in POLICIES:
            first = [
                [
                    r.backend
                    for r in rows
                    if (r.task, r.policy, r.block) == (task, policy, block)
                ]
                for block in range(settings.blocks)
            ]
            assert first.count(["opencv", "pynv"]) == 5
            assert first.count(["pynv", "opencv"]) == 5
    assert Settings(backends=("opencv",)).backends == ("opencv",)


@pytest.mark.parametrize(
    "settings",
    [
        {"blocks": 3},
        {"backends": ()},
        {"tasks": ("full", "full")},
        {"wall_limit_s": 3601},
        {"trial_timeout_s": float("nan")},
    ],
)
def test_invalid_campaign_admission(settings):
    with pytest.raises(ValidationError):
        Settings(**settings)


@pytest.mark.parametrize("task", ["full", "selected", "prepared"])
def test_real_cpu_task_and_minimal_policy_do_not_construct_samplers(
    video, tmp_path, monkeypatch, task
):
    def forbidden(*args, **kwargs):
        raise AssertionError("observer unexpectedly constructed")

    monkeypatch.setattr(native, "ResourceSampler", forbidden)
    monkeypatch.setattr(native, "GPUSampler", forbidden)
    monkeypatch.setattr(native, "collect_vss_metrics", forbidden)
    trial = asyncio.run(native.worker(request(video, task=task, warmups=1), tmp_path))
    assert trial.resources is None and trial.gpu_trace is None
    assert trial.collection_complete is None and not trial.metrics
    assert trial.frames == (10 if task == "full" else 8)
    assert trial.policy_wall_s >= trial.operation_wall_s > 0
    assert (trial.encoded_base64_bytes > 0) == (task == "prepared")


@pytest.mark.parametrize("task", ["full", "selected", "prepared"])
def test_collection_policy_records_actual_lifecycle_without_resource_sampling(
    video, tmp_path, monkeypatch, task
):
    def forbidden(*args, **kwargs):
        raise AssertionError("sampling unexpectedly constructed")

    monkeypatch.setattr(native, "ResourceSampler", forbidden)
    monkeypatch.setattr(native, "GPUSampler", forbidden)
    trial = asyncio.run(
        native.worker(request(video, task=task, policy="collection"), tmp_path)
    )
    assert trial.collection_complete and trial.trace
    assert trial.metrics["processing.selected_frames"].sum == trial.frames
    assert trial.resources is None


def test_scalar_collection_and_resource_policies_are_independent(video, tmp_path):
    trial = asyncio.run(native.worker(request(video, policy="resources"), tmp_path))
    assert trial.resources is not None and trial.resources.complete
    assert not trial.metrics and trial.trace is None


def test_successful_controller_verification_and_export(complete_cpu, tmp_path):
    root, _, trials = complete_cpu
    manifest = export.export(root, tmp_path / "csv")
    assert manifest["source_status"] == "complete"
    assert len({t.process_id for t in trials}) == 8
    with (tmp_path / "csv/trials.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 8 and all(row["status"] == "completed" for row in rows)
    assert manifest["schema_version"] == "native_csv_export.2"
    assert all(
        row["backend_type"] == "opencv" and row["vlm_model"] == "" for row in rows
    )
    assert all(
        row["frames_per_chunk_status"] == "not_applicable"
        and row["frames_per_chunk"] == ""
        for row in rows
    )
    assert all(row["concurrent_streams"] == "1" for row in rows)
    assert all(
        row["sampled_rss_max_bytes"] == ""
        for row in rows
        if row["policy"] in {"minimal", "collection"}
    )
    assert all(
        row["device_gpu_sample_mean_pct"] == ""
        and row["host_cpu_sample_mean_pct"] == ""
        for row in rows
        if row["policy"] in {"minimal", "collection"}
    )
    with (tmp_path / "csv/groups.csv").open() as stream:
        groups = list(csv.DictReader(stream))
    assert len(groups) == 4 and all(
        row["p95"] == row["interval"] == "" for row in groups
    )


@pytest.mark.parametrize(
    "fault",
    [
        "intent_missing",
        "intent_changed",
        "negative_elapsed",
        "nan_elapsed",
        "wall_overrun",
        "slot_time",
        "trial_bytes",
    ],
)
def test_completed_evidence_rejects_frozen_plan_clock_and_hash_corruption(
    complete_cpu, tmp_path, fault
):
    source, receipt, _ = complete_cpu
    root = tmp_path / "changed"
    shutil.copytree(source, root)
    body = copy.deepcopy(receipt)
    if fault == "intent_missing":
        (root / "intent.json").unlink()
    elif fault == "intent_changed":
        (root / "intent.json").write_text("{}")
    elif fault == "negative_elapsed":
        body["elapsed_s"] = -1
    elif fault == "nan_elapsed":
        body["elapsed_s"] = float("nan")
    elif fault == "wall_overrun":
        body["elapsed_s"] = body["settings"]["wall_limit_s"] + 1
    elif fault == "slot_time":
        body["rows"][0]["finished_elapsed_s"] = body["elapsed_s"] + 1
    else:
        (root / body["rows"][0]["trial_ref"]).write_text("{}")
    (root / "campaign.json").write_text(json.dumps(body))
    with pytest.raises((ValueError, OSError)):
        native.load_receipt(root)


@pytest.mark.parametrize(
    "error", [KeyboardInterrupt(), TrialCleanupError("unconfirmed cleanup")]
)
def test_cancel_or_unconfirmed_cleanup_stops_admission(
    video, tmp_path, monkeypatch, error
):
    def fail(*a, **kw):
        raise error

    monkeypatch.setattr(native, "isolated_trial", fail)
    result = native.run(
        video,
        tmp_path / "campaign",
        Settings(
            backends=("opencv",), tasks=("full",), trial_timeout_s=1, wall_limit_s=120
        ),
    )
    assert result["status"] == "incomplete"
    assert result["rows"][0]["status"] == (
        "cancelled" if isinstance(error, KeyboardInterrupt) else "failed"
    )
    assert all(row["status"] == "not_run" for row in result["rows"][1:])


def test_worker_rejects_native_runtime_drift(video, tmp_path, monkeypatch):
    actual = native.native_identity
    count = 0

    def changed(required):
        nonlocal count
        count += 1
        tools, builds, gpu = actual(required)
        if count == 2:
            builds["opencv"] = "changed"
        return tools, builds, gpu

    monkeypatch.setattr(native, "native_identity", changed)
    with pytest.raises(ValueError, match="input/runtime changed"):
        asyncio.run(native.worker(request(video), tmp_path))


@pytest.mark.parametrize(
    "fault",
    [
        "success",
        "busy_before",
        "dirty_after",
        "wrong_gpu",
        "replayed_pid",
        "software_drift",
    ],
)
def test_gpu_controller_plumbing_with_stubbed_gpu_evidence(
    video, complete_cpu, tmp_path, monkeypatch, fault
):
    """Control-flow coverage only: these stubs never establish CUDA performance."""
    _, _, cpu_trials = complete_cpu
    gpu = GPUIdentity(
        uuid_sha256="a" * 64,
        name="test GPU",
        driver="test",
        memory_bytes=1024,
        image_id="sha256:" + "b" * 64,
        base_digest="sha256:" + "c" * 64,
    )
    calls = []

    def idle(block, phase, diagnostics):
        busy = (fault == "busy_before" and phase == "before") or (
            fault == "dirty_after" and phase == "after"
        )
        identity = (
            gpu.model_copy(update={"uuid_sha256": "d" * 64})
            if fault == "wrong_gpu"
            else gpu
        )
        return GPUIdleCheck(
            pair=block,
            phase=phase,
            gpu=identity,
            elapsed_s=0,
            polls=1,
            quiescent=not busy,
            remaining_processes=int(busy),
        )

    def fake_worker(command, **kwargs):
        body = json.loads(Path(command[-1]).read_bytes())
        slot = body["slot"]
        calls.append(slot)
        trial = next(
            t
            for t in cpu_trials
            if t.slot.policy == slot["policy"] and t.slot.block == slot["block"]
        )
        data = trial.model_dump(mode="json")
        data.update(
            slot=slot,
            settings_sha256=native.digest(body["settings"]),
            process_id=1000000 if fault == "replayed_pid" else 1000000 + slot["index"],
            gpu=gpu.model_dump(mode="json"),
        )
        if fault == "software_drift":
            data["software_sha256"] = "0" * 64
        if slot["policy"] in {"resources", "combined"}:
            data["gpu_trace"] = GPUTrace(
                complete=True,
                samples=tuple(
                    GPUSample(
                        elapsed_s=t,
                        device_used_bytes=0,
                        gpu_utilization_pct=0,
                        unrelated_processes=0,
                    )
                    for t in (0.0, 0.1)
                ),
            ).model_dump(mode="json")
        return NativeTrial.model_validate(data).model_dump_json().encode()

    monkeypatch.setattr(native, "checked_idle", idle)
    monkeypatch.setattr(native, "isolated_trial", fake_worker)
    # Actual CPU trace verification is covered by complete_cpu; these controls
    # intentionally stub the GPU worker and its scalar-trace transport together.
    monkeypatch.setattr(native, "verify_trace", lambda *a, **kw: ())
    root = tmp_path / "gpu-controls"
    result = native.run(
        video,
        root,
        Settings(
            tasks=("full",), blocks=2, warmups=0, trial_timeout_s=1, wall_limit_s=120
        ),
    )
    if fault == "success":
        assert result["status"] == "complete"
        loaded, trials = native.load_receipt(root)
        assert len(trials) == 16
        assert (
            sum(
                c["kind"] == "decoder_operation"
                for c in loaded["summary"]["comparisons"]
            )
            == 4
        )
        # Rehashed idle contradictions must still fail structural validation.
        changed = copy.deepcopy(result)
        changed["rows"][0]["idle_before"]["remaining_processes"] = 1
        native.atomic_json(root / "campaign.json", changed)
        with pytest.raises(ValidationError):
            native.load_receipt(root)
    else:
        assert result["status"] == "incomplete"
        assert result["summary"]["groups"] == []
        assert len(calls) == (
            0 if fault == "busy_before" else 2 if fault == "replayed_pid" else 1
        )


def test_source_mutation_is_rejected_before_timed_work(video, tmp_path):
    body = request(video)
    body["video_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="source hash mismatch"):
        asyncio.run(native.worker(body, tmp_path))


@pytest.mark.parametrize(
    "field,value",
    [
        ("frames", 3),
        ("policy_wall_s", 0),
        ("operation_wall_s", float("inf")),
        ("resources", {"complete": True}),
        ("encoded_base64_bytes", 1),
    ],
)
def test_worker_evidence_cannot_claim_missing_or_different_work(measured, field, value):
    body = measured.model_dump(mode="json")
    body[field] = value
    with pytest.raises(ValidationError):
        NativeTrial.model_validate(body)


def test_campaign_timeout_preserves_every_unrun_slot_and_refuses_retry(
    video, tmp_path, monkeypatch
):
    calls = []

    def timeout(*args, **kwargs):
        calls.append(args)
        raise subprocess.TimeoutExpired("owned worker", 1)

    monkeypatch.setattr(native, "isolated_trial", timeout)
    settings = Settings(
        backends=("opencv",),
        tasks=("full",),
        blocks=2,
        trial_timeout_s=1,
        wall_limit_s=120,
    )
    output = tmp_path / "campaign"
    receipt = native.run(video, output, settings)
    assert receipt["status"] == "incomplete"
    assert len(calls) == 1
    assert [r["status"] for r in receipt["rows"]].count("failed") == 1
    assert [r["status"] for r in receipt["rows"]].count("not_run") == 7
    assert receipt["summary"]["groups"] == []
    before = (output / "campaign.json").read_bytes()
    with pytest.raises(FileExistsError):
        native.run(video, output, settings)
    assert (output / "campaign.json").read_bytes() == before
    exported = export.export(output, tmp_path / "csv")
    assert exported["scheduled_slots"] == 8
    with (tmp_path / "csv/trials.csv").open() as stream:
        slots = list(csv.DictReader(stream))
    assert len(slots) == 8 and all(r["operation_wall_s"] == "" for r in slots)
    assert (tmp_path / "csv/groups.csv").read_text().count("\n") == 1
    with pytest.raises(FileExistsError):
        export.export(output, tmp_path / "csv")


def test_budget_rejects_admission_without_starting_worker(video, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("worker admitted without reserve")

    monkeypatch.setattr(native, "isolated_trial", forbidden)
    result = native.run(
        video,
        tmp_path / "campaign",
        Settings(backends=("opencv",), tasks=("full",), wall_limit_s=1),
    )
    assert result["status"] == "incomplete"
    assert all(r["status"] == "not_run" for r in result["rows"])


@pytest.mark.parametrize(
    "mutation", ["missing_slot", "changed_order", "fake_completion", "fake_summary"]
)
def test_export_rejects_corrupt_or_falsely_completed_receipts(
    video, tmp_path, monkeypatch, mutation
):
    monkeypatch.setattr(native, "isolated_trial", lambda *a, **kw: b'{"invalid": true}')
    output = tmp_path / "campaign"
    body = native.run(
        video,
        output,
        Settings(
            backends=("opencv",), tasks=("full",), trial_timeout_s=1, wall_limit_s=120
        ),
    )
    if mutation == "missing_slot":
        body["rows"].pop()
    elif mutation == "changed_order":
        body["rows"].reverse()
    elif mutation == "fake_completion":
        body.update(status="complete", failure=None)
    else:
        body["summary"]["groups"] = [{"median_fps": 123}]
    native.atomic_json(output / "campaign.json", body)
    with pytest.raises(ValueError):
        export.export(output, tmp_path / "csv")
    assert not (tmp_path / "csv").exists()


@pytest.mark.parametrize(
    "label", ["=HYPERLINK(1)", "+SUM(1)", "  @thing", "\ncmd", "-1+2"]
)
def test_csv_formula_safety_does_not_corrupt_actual_numbers(label):
    assert export.safe_cell(label).startswith("'")
    assert export.safe_cell(-1.25) == -1.25
    assert export.safe_cell(None) == ""


def test_valid_trial_rejects_missing_gpu_identity(measured):
    body = copy.deepcopy(measured.model_dump(mode="json"))
    body["slot"]["backend"] = "pynv"
    with pytest.raises(ValidationError, match="identity"):
        NativeTrial.model_validate(body)


def test_oracle_size_failure_keeps_counts_and_source_hashes(video, monkeypatch):
    original = media.bounded_tool

    def truncated(command, **kwargs):
        raw = original(command, **kwargs)
        return raw[:-3] if Path(command[0]).name == "ffmpeg" else raw

    monkeypatch.setattr(media, "bounded_tool", truncated)
    with pytest.raises(media.MediaError) as error:
        media.inspect(video, preflight()[0])
    assert error.value.reason == "oracle_or_input_changed"
    details = error.value.details
    assert details["actual_rgb_bytes"] == details["expected_rgb_bytes"] - 3
    assert (
        details["source_before_sha256"]
        == details["source_after_sha256"]
        == file_hash(video)
    )


def test_tool_diagnostics_record_byte_counts_without_retaining_binary(tmp_path):
    with tool_diagnostics(tmp_path):
        raw = media.bounded_tool(
            [sys.executable, "-c", "import os; os.write(1, b'x'*50)"],
            limit=50,
            timeout=5,
        )
    assert raw == b"x" * 50
    rows = [json.loads(path.read_text()) for path in tmp_path.glob("*.json")]
    assert len(rows) == 1
    assert rows[0]["stdout_bytes"] == rows[0]["stdout_returned_bytes"] == 50
    assert "ffprobe_json" not in rows[0] and "stdout" not in rows[0]
