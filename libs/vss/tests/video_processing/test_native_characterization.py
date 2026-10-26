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

"""Opted-in real-video pair/HTTP workflow and artifact attack regressions."""

import os
import subprocess
import sys

import pytest
from pydantic import ValidationError

from benchmarks.report import load, summarize
from benchmarks.video_results import VideoTrial


@pytest.mark.skipif(
    os.getenv("RUN_VSS_CPU_TESTS") != "1" or os.name != "posix",
    reason="opted-in native CPU/POSIX profile",
)
def test_real_video_pairs_workflow_trace_and_immutable_output(tmp_path, monkeypatch):
    output = tmp_path / "tiny.json"
    command = [
        sys.executable,
        "-m",
        "benchmarks.video",
        "--recipe",
        "tiny",
        "--warmups",
        "0",
        "--repetitions",
        "2",
        "--output",
        str(output),
    ]
    child = subprocess.run(command, capture_output=True, timeout=60)
    assert child.returncode == 0, (child.stdout, child.stderr)
    result = load(output)
    assert len({t.process_id for t in result.trials}) == 2
    assert all(
        t.workflow
        and t.workflow.client_closed
        and t.workflow.events == t.workflow.chunks == 1
        for t in result.trials
    )
    assert (
        summarize(result)["paired_relative_collection_overhead"]["status"]
        == "insufficient_samples"
    )
    original = output.read_bytes()
    repeated = subprocess.run(command, capture_output=True, timeout=60)
    assert repeated.returncode == 2 and output.read_bytes() == original
    trial = result.trials[0]
    # Parent interruption must retain the earlier complete pair and its traces,
    # while refusing survivor statistics for the incomplete prespecified run.
    import shutil

    from benchmarks import video

    partial = tmp_path / "interrupted.json"
    calls = 0

    def interrupted(command, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise subprocess.TimeoutExpired(command, 1)
        stage = command[command.index("--artifacts") + 1]
        shutil.copytree(output.with_suffix(".artifacts"), stage, dirs_exist_ok=True)
        return trial.model_dump_json().encode()

    with monkeypatch.context() as patch:
        patch.setattr(video, "isolated_trial", interrupted)
        patch.setattr(sys, "argv", ["video"] + command[3:-1] + [str(partial)])
        assert video.main() == 3
    retained = load(partial)
    assert retained.failure == "timeout" and retained.trials == (trial,)
    assert summarize(retained)["statistics"] is None

    # Isolate candidate admission from its copy transaction using the same real
    # artifact. Interrupt copying and corrupt the copy: neither may publish.
    from benchmarks import report

    eligible = result.model_copy(
        update={
            "configuration": result.configuration.model_copy(update={"repetitions": 10})
        }
    )
    original_load = report.load
    original_copytree = shutil.copytree
    for attack in ("interrupted", "corrupted"):
        destination = tmp_path / attack
        with monkeypatch.context() as patch:
            patch.setattr(
                report,
                "load",
                lambda path: eligible if path == output else original_load(path),
            )

            def copying(source, target, *args, attack=attack, **kwargs):
                if attack == "interrupted":
                    raise OSError("injected copy interruption")
                copied = original_copytree(source, target, *args, **kwargs)
                from pathlib import Path

                if Path(target).name == "result.artifacts":
                    (Path(target) / "unexpected.txt").write_text("tampered copy")
                return copied

            patch.setattr(shutil, "copytree", copying)
            with pytest.raises((OSError, ValueError)):
                report.register_candidate(output, destination)
        assert not destination.exists() and not list(tmp_path.glob(".candidate-*"))
        assert not destination.with_suffix(".lock").exists()
    for change in (
        {"periods": trial.periods[:1]},
        {"media": None},
        {"resources": trial.resources.model_dump() | {"complete": False}},
    ):
        with pytest.raises(ValidationError):
            VideoTrial.model_validate(trial.model_dump() | change)
    ref = next(p.trace for p in trial.periods if p.trace)
    trace = output.with_suffix(".artifacts") / ref.name
    trace.write_bytes(trace.read_bytes() + b"tampered")
    with pytest.raises(ValueError):
        load(output)


@pytest.mark.skipif(os.getenv("RUN_VSS_CPU_TESTS") != "1", reason="opted-in native CPU")
async def test_offline_http_rejects_empty_images_and_preserves_client_close(
    tmp_path, monkeypatch
):
    from benchmarks.media import generated, inspect
    from benchmarks.processing import preflight
    from benchmarks.video import offline_workflow
    from benchmarks.video_results import VideoConfiguration
    from vss.ingestion.vlm import OpenAICompatibleVLMBackend

    tools, _ = preflight()
    source = generated(tmp_path, "tiny", tools["ffmpeg"])
    media, _ = inspect(source, tools)

    async def text_only(*args):
        return [{"role": "user", "content": [{"type": "text", "text": "empty"}]}]

    monkeypatch.setattr(OpenAICompatibleVLMBackend, "_messages", text_only)
    result = await offline_workflow(
        source, media, VideoConfiguration(recipe="tiny"), tmp_path / "evidence"
    )
    assert result.status == "error" and result.client_closed
    assert result.jpeg_checks == 0 and "processing.encode" not in result.metrics


@pytest.mark.skipif(
    os.getenv("RUN_VSS_CPU_TESTS") != "1" or os.name != "posix",
    reason="opted-in native CPU/POSIX",
)
def test_workflow_trace_substitution_versions_seed_and_inventory_rejected(tmp_path):
    from benchmarks.artifacts import aggregate, read_trace, write_trace
    from benchmarks.report import load
    from benchmarks.video import verify_trial
    from benchmarks.video_results import VideoResult

    output = tmp_path / "tiny.json"
    child = subprocess.run(
        [
            sys.executable,
            "-m",
            "benchmarks.video",
            "--recipe",
            "tiny",
            "--warmups",
            "0",
            "--repetitions",
            "2",
            "--output",
            str(output),
        ],
        capture_output=True,
        timeout=60,
    )
    assert child.returncode == 0, (child.stdout, child.stderr)
    result = load(output)
    trial = result.trials[0]
    enabled = next(p for p in trial.periods if p.mode == "enabled")
    flow = trial.workflow.model_dump() | {
        "trace": enabled.trace,
        "metrics": enabled.metrics,
    }
    with pytest.raises(ValidationError):
        VideoTrial.model_validate(trial.model_dump() | {"workflow": flow})
    root = output.with_suffix(".artifacts")
    # Exercise the actual verifier with an independently reserialized historical
    # catalog trace, without rewriting the original native evidence.
    historical_root = tmp_path / "historical"

    def historical_trace(ref):
        history = tuple(
            e.model_copy(
                update={
                    "catalog_version": "1.1",
                    "name": e.name.replace("ingestion.vlm.", "processing.vlm."),
                    "library": "processing"
                    if e.name.startswith("ingestion.vlm.")
                    else e.library,
                }
            )
            for e in read_trace(root, ref)
        )
        return {
            "trace": write_trace(historical_root, history),
            "metrics": aggregate(history),
        }

    historical = trial.model_copy(
        update={
            "catalog_version": "1.1",
            "periods": tuple(
                p.model_copy(update=historical_trace(p.trace)) if p.trace else p
                for p in trial.periods
            ),
            "workflow": trial.workflow.model_copy(
                update=historical_trace(trial.workflow.trace)
            ),
        }
    )
    verify_trial(historical, historical_root)
    with pytest.raises(ValueError, match="versions mismatch"):
        verify_trial(
            historical.model_copy(update={"catalog_version": "1.2"}), historical_root
        )
    records = read_trace(root, enabled.trace)
    downgraded = tuple(
        e.model_copy(update={"schema_version": "1.0", "catalog_version": "1.0"})
        for e in records
    )
    ref = write_trace(tmp_path / "attack", downgraded)
    period = enabled.model_copy(update={"trace": ref, "metrics": aggregate(downgraded)})
    changed = trial.model_copy(
        update={
            "periods": tuple(
                period if p.mode == "enabled" else p for p in trial.periods
            ),
            "workflow": None,
        }
    )
    with pytest.raises(ValueError, match="versions mismatch"):
        verify_trial(changed, tmp_path / "attack")
    wrong = result.model_dump()
    wrong["configuration"]["seed"] = 1730
    with pytest.raises(ValidationError):
        VideoResult.model_validate(wrong)
    (root / "unreferenced.txt").write_text("unmanifested")
    with pytest.raises(ValueError, match="inventory"):
        load(output)


@pytest.mark.skipif(os.getenv("RUN_VSS_CPU_TESTS") != "1", reason="opted-in native CPU")
@pytest.mark.parametrize(
    "terminal", ["http_error", "malformed", "cancelled", "corrupt_jpeg"]
)
async def test_offline_terminal_paths_close_client_restore_registry(
    tmp_path, monkeypatch, terminal
):
    import asyncio

    import httpx

    from benchmarks.media import generated, inspect
    from benchmarks.processing import preflight
    from benchmarks.video import offline_workflow
    from benchmarks.video_results import VideoConfiguration
    from vss.ingestion import vlm

    tools, _ = preflight()
    source = generated(tmp_path, "tiny", tools["ffmpeg"])
    media, _ = inspect(source, tools)
    sentinel = object()
    monkeypatch.setitem(vlm._VLM_BACKEND_REGISTRY, "m8_offline", sentinel)
    original_post = httpx.AsyncClient.post
    clients = []
    original_messages = vlm.OpenAICompatibleVLMBackend._messages

    async def corrupt_messages(self, *args, **kwargs):
        messages = await original_messages(self, *args, **kwargs)
        for message in messages:
            for part in message.get("content", []):
                if isinstance(part, dict) and part.get("type") == "image_url":
                    part["image_url"]["url"] = "data:image/jpeg;base64,invalid@@"
        return messages

    async def post(self, *args, **kwargs):
        clients.append(self)
        if terminal == "http_error":
            return httpx.Response(503, request=httpx.Request("POST", args[0]))
        if terminal == "malformed":
            return httpx.Response(
                200, json={"choices": []}, request=httpx.Request("POST", args[0])
            )
        if terminal == "cancelled":
            raise asyncio.CancelledError
        return await original_post(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    if terminal == "corrupt_jpeg":
        monkeypatch.setattr(
            vlm.OpenAICompatibleVLMBackend, "_messages", corrupt_messages
        )
    if terminal == "cancelled":
        with pytest.raises(asyncio.CancelledError):
            await offline_workflow(
                source, media, VideoConfiguration(recipe="tiny"), tmp_path / "trace"
            )
    else:
        result = await offline_workflow(
            source, media, VideoConfiguration(recipe="tiny"), tmp_path / "trace"
        )
        assert result.status == "error" and result.client_closed
    assert clients and all(client.is_closed for client in clients)
    assert vlm._VLM_BACKEND_REGISTRY["m8_offline"] is sentinel


@pytest.mark.skipif(
    os.getenv("RUN_VSS_CPU_TESTS") != "1" or os.name != "posix",
    reason="opted-in native CPU/POSIX",
)
async def test_failed_decode_arm_preserves_pair(tmp_path, monkeypatch):
    from benchmarks import video
    from benchmarks.video_results import VideoConfiguration

    original = video.decode_period

    async def decode(*args, **kwargs):
        period = await original(*args, **kwargs)
        if kwargs["enabled"]:
            return period.model_copy(
                update={
                    "status": "error",
                    "failure": "injected_decode",
                    "correctness": False,
                }
            )
        return period

    monkeypatch.setattr(video, "decode_period", decode)
    result = await video.worker(
        VideoConfiguration(recipe="tiny", warmups=0, offline_e2e=False),
        None,
        tmp_path,
        "disabled_first",
    )
    assert result.status == "error" and result.failure == "decode_pair"
    assert [p.status for p in result.periods] == ["ok", "error"]
    assert all(p.frames == 10 for p in result.periods)


@pytest.mark.skipif(os.getenv("RUN_VSS_CPU_TESTS") != "1", reason="opted-in native CPU")
@pytest.mark.parametrize("attack", ["source", "chunk", "event", "nan", "bool"])
async def test_returned_response_identity_is_required(tmp_path, monkeypatch, attack):
    import json

    from benchmarks.media import generated, inspect
    from benchmarks.processing import preflight
    from benchmarks.video import offline_workflow
    from benchmarks.video_results import VideoConfiguration
    from vss.ingestion import vlm

    tools, _ = preflight()
    source = generated(tmp_path, "tiny", tools["ffmpeg"])
    media, _ = inspect(source, tools)
    original = vlm.OpenAICompatibleVLMBackend.infer

    async def replaced(self, *args, **kwargs):
        response = await original(self, *args, **kwargs)
        if attack == "source":
            response.chunk.sourceId = "different-source"
        elif attack == "chunk":
            response.chunk.chunkIdx = 1
        else:
            payload = json.loads(response.vlm_response)
            if attack == "event":
                payload["events"][0]["description"] = "wrong controlled event"
            elif attack == "nan":
                payload["events"][0]["start_time"] = float("nan")
            else:
                payload["events"][0]["start_time"] = False
            response.vlm_response = json.dumps(payload)
        return response

    monkeypatch.setattr(vlm.OpenAICompatibleVLMBackend, "infer", replaced)
    result = await offline_workflow(
        source, media, VideoConfiguration(recipe="tiny"), tmp_path / "trace"
    )
    assert result.status == "error" and result.client_closed and result.jpeg_checks > 0


@pytest.mark.skipif(os.getenv("RUN_VSS_CPU_TESTS") != "1", reason="opted-in native CPU")
async def test_failed_warmup_retains_bounded_cause_without_measured_arms(
    tmp_path, monkeypatch
):
    from benchmarks import video
    from benchmarks.video_results import VideoConfiguration

    original = video.decode_period

    async def failed(*args, **kwargs):
        period = await original(*args, **kwargs)
        return period.model_copy(
            update={"status": "error", "failure": "correctness", "correctness": False}
        )

    monkeypatch.setattr(video, "decode_period", failed)
    result = await video.worker(
        VideoConfiguration(recipe="tiny", warmups=1), None, tmp_path, "disabled_first"
    )
    assert (
        result.status == "error" and result.failure == "warmup" and not result.periods
    )
    assert result.warmup_diagnostic.failure == "correctness"
    assert (
        result.warmup_diagnostic.frames == 10
        and result.warmup_diagnostic.mode == "disabled"
    )
    assert not list(tmp_path.rglob("*.gz"))
    with pytest.raises(ValidationError):
        video.VideoTrial.model_validate(
            result.model_dump() | {"status": "ok", "failure": None}
        )
