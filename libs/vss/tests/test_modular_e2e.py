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

"""Q0 configuration, fail-closed comparisons and real decode/storage/provider wiring."""

import asyncio
import json
import shutil
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from benchmarks.e2e import worker
from benchmarks.e2e.__main__ import (
    baseline_preparation,
    fixture_config,
    frame_admission,
    preflight,
    run,
    schedule,
    statistics,
)
from benchmarks.e2e.__main__ import (
    main as e2e_main,
)
from benchmarks.e2e.config import Recipe, endpoint, fingerprint
from benchmarks.e2e.results import WorkerResult
from benchmarks.e2e.transport import ObservingTransport, ProviderObserver
from benchmarks.media import generated


def recipe_dict():
    provider = {
        "model": "controlled-model",
        "revision": "owned-loopback.v1",
        "base_url_env": "M8_UNUSED",
    }
    return {
        "evidence_kind": "controlled_wiring",
        "swap": "storage",
        "video": "/unused",
        "video_sha256": "0" * 64,
        "duration_s": 2,
        "required_summary_terms": ["test pattern"],
        "arms": [
            {
                "label": store,
                "store": store,
                "vlm": provider.copy(),
                "llm": provider.copy(),
            }
            for store in ("memory", "file")
        ],
    }


def validate(data):
    return Recipe.model_validate_json(json.dumps(data), strict=True)


@pytest.mark.parametrize(
    "field,value",
    [
        ("pairs", True),
        ("pairs", 1),
        ("warmups", 4),
        ("frames_per_chunk", 16),
        ("overlap_s", 1),
        ("duration_s", float("nan")),
        ("summary_max_tokens", 0),
        ("worker_timeout_s", 2),
        ("extra", "unrecognized"),
    ],
)
def test_invalid_or_unimplemented_settings_rejected(field, value):
    data = recipe_dict()
    data[field] = value
    with pytest.raises(ValidationError):
        validate(data)


def test_multiple_slots_and_fake_normal_vss_comparisons_rejected():
    data = recipe_dict()
    data["arms"][1]["captioning"] = "gpu_vlm"
    with pytest.raises(ValidationError):
        validate(data)
    data = recipe_dict()
    data["arms"][1]["captioning"] = "normal_vss"
    with pytest.raises(ValidationError):
        validate(data)
    assert baseline_preparation()["deployable"] is False


def test_pinned_image_does_not_enable_normal_vss_performance_comparisons(
    monkeypatch, capsys
):
    monkeypatch.setattr("sys.argv", ["benchmarks.e2e", "--prepare-baseline"])
    assert e2e_main() == 2
    preparation = json.loads(capsys.readouterr().out)
    assert preparation["status"] == "blocked_comparison_qualification"
    assert preparation["runtime_product_version"] is None
    assert preparation["observed_release_line"] is None
    assert preparation["blueprint_release"] is None
    assert preparation["live_readiness_checked_by_this_command"] is False
    assert preparation["identity_scope"] == "operator_supplied_deployment_required"
    assert preparation["image"] is None
    assert "recorded_pilot_evidence" not in preparation
    assert preparation["deployable"] is False
    assert any("effective" in gate for gate in preparation["missing"])
    assert any("provider completion" in gate for gate in preparation["missing"])
    assert any("complete-operation adapter" in gate for gate in preparation["missing"])


def test_balanced_order_and_no_survivor_or_smoke_statistics():
    recipe = validate(recipe_dict())
    assert schedule(recipe, False) == [
        ("warmup", 0, 0),
        ("warmup", 0, 1),
        ("sample", 0, 0),
        ("sample", 0, 1),
        ("sample", 1, 1),
        ("sample", 1, 0),
    ]
    assert statistics(recipe, [{"status": "failed"}], False) is None
    assert statistics(recipe, [], True) is None
    with pytest.raises(ValidationError):
        WorkerResult.model_validate({"status": "ok"})


@pytest.mark.parametrize(
    "url",
    [
        "https://user:secret@host/v1",
        "https://host?q=secret",
        "https://host/#secret",
        "file:///private",
        "http://host/ white",
    ],
)
def test_endpoint_never_accepts_url_credentials_or_query(monkeypatch, url):
    monkeypatch.setenv("M8_ENDPOINT", url)
    with pytest.raises(ValueError):
        endpoint("M8_ENDPOINT")


def test_failed_worker_is_preserved_without_statistics(tmp_path, monkeypatch):
    import benchmarks.e2e.__main__ as runner

    data = recipe_dict()
    recipe = validate(data)
    monkeypatch.setattr(runner, "preflight", lambda _: None)
    monkeypatch.setattr(
        runner,
        "isolated_trial",
        lambda *a, **kw: b'{"status":"failed","failure":"quality"}',
    )
    report = run(recipe, tmp_path / "private.json", False)
    assert report.status == "incomplete" and report.statistics is None
    assert len(report.trials) == 1 and report.trials[0]["failure"] == "quality"
    assert "/unused" not in report.model_dump_json()


@pytest.fixture(scope="module")
def video(tmp_path_factory):
    pytest.importorskip("cv2")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("requires native FFmpeg")
    return generated(tmp_path_factory.mktemp("q0-media"), "tiny", ffmpeg)


@pytest.fixture
def case(video, monkeypatch):
    import vss.ingestion.backend as backend
    import vss.ingestion.vlm as vlm

    # Direct worker unit tests share a process; production workers do not.
    monkeypatch.setitem(
        vlm._VLM_BACKEND_REGISTRY, "vlm", vlm._VLM_BACKEND_REGISTRY["vlm"]
    )
    monkeypatch.setitem(
        backend._BACKEND_REGISTRY, "rtvi", backend._BACKEND_REGISTRY["rtvi"]
    )
    data = fixture_config(video, 2)
    return data


def mock_transport(monkeypatch, mode="ok"):
    requests = []

    async def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        summary = request.url.path.startswith("/llm")
        if mode == "timeout":
            await asyncio.sleep(10)
        if mode == "error":
            return httpx.Response(400, json={"error": {"message": "PRIVATE_KEY_ECHO"}})
        text = (
            "A test pattern moves."
            if summary
            else json.dumps(
                {
                    "events": [
                        {
                            "start_time": 0,
                            "end_time": 1,
                            "description": "A test pattern moves.",
                        }
                    ]
                }
            )
        )
        result = {
            "id": "chatcmpl-controlled",
            "created": 1,
            "object": "chat.completion",
            "model": body["model"],
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "length"
                    if summary and mode == "truncated"
                    else "stop",
                    "message": {"role": "assistant", "content": text},
                }
            ],
            "usage": {"prompt_tokens": 23, "completion_tokens": 12},
        }
        if mode == "missing_usage":
            del result["usage"]
        return httpx.Response(
            200,
            stream=httpx.ByteStream(json.dumps(result).encode()),
            headers={"content-type": "application/json"},
        )

    monkeypatch.setattr(
        worker,
        "ObservingTransport",
        lambda observer, witness=None: ObservingTransport(
            observer, witness, inner=httpx.MockTransport(respond)
        ),
    )
    return requests


@pytest.mark.parametrize("index", [0, 1])
async def test_real_cpu_decode_complete_workflow_and_fresh_file_read(
    case, tmp_path, monkeypatch, index
):
    requests = mock_transport(monkeypatch)
    monkeypatch.setenv("VSS_STORAGE_BACKEND", "this-must-not-select-checkpointing")
    recipe = validate(case)
    result = await worker.operation(
        recipe,
        recipe.arms[index],
        tmp_path,
        SimpleNamespace(base="https://fixture.invalid"),
    )
    assert result["status"] == "ok", result
    assert result["frames"] == 8 and result["chunks"] == result["events"] == 1
    assert result["lifecycle_s"] >= result["result_ready_s"]
    assert len(requests) == 2
    assert requests[0]["max_tokens"] == recipe.caption_max_tokens
    assert requests[1]["max_tokens"] == recipe.summary_max_tokens
    assert any("image_url" in part for part in requests[0]["messages"][1]["content"])
    assert "test pattern" in requests[1]["messages"][1]["content"]
    assert len(list(tmp_path.rglob("*.jsonl"))) > 0 if index else True


@pytest.mark.parametrize(
    "mode,reason",
    [
        ("truncated", "quality"),
        ("missing_usage", "quality"),
        ("error", "provider_or_storage"),
        ("timeout", "work_timeout"),
    ],
)
async def test_inadequate_provider_results_or_timeouts_never_qualify(
    case, tmp_path, monkeypatch, mode, reason
):
    requests = mock_transport(monkeypatch, mode)
    case["work_timeout_s"] = 0.05 if mode == "timeout" else 10
    recipe = validate(case)
    result = await worker.operation(
        recipe,
        recipe.arms[1],
        tmp_path,
        SimpleNamespace(base="https://fixture.invalid"),
    )
    assert result["status"] == "failed" and result["failure"] == reason
    assert "PRIVATE_KEY_ECHO" not in json.dumps(result)
    assert requests


def test_provider_observer_keeps_unknown_finish_and_missing_usage_unknown():
    observer = ProviderObserver("fixed")
    observer.capture(
        b'{"model":"fixed","choices":[{"finish_reason":"new_reason"}]}', 200
    )
    facts = observer.completions[0]
    assert facts["finish_reason"] == "unknown"
    assert not facts["usage_available"] and facts["output_tokens"] is None


def test_configuration_hash_does_not_depend_on_json_key_order():
    assert fingerprint({"a": 1, "b": 2}) == fingerprint({"b": 2, "a": 1})


def test_interior_caption_gaps_cannot_pass_expected_count_and_final_end():
    from vss.ingestion import ChunkInfo, VlmChunkResponse

    data = recipe_dict()
    data["duration_s"] = 60
    recipe = validate(data)
    chunks = [
        VlmChunkResponse(
            chunk=ChunkInfo(chunkIdx=i, start_pts=start * 10**9, end_pts=end * 10**9),
            vlm_response="caption",
        )
        for i, (start, end) in enumerate([(0, 1), (59, 60)])
    ]
    with pytest.raises(worker.QualityError):
        worker.validate_windows(chunks, recipe)
    chunks[0].chunk.end_pts = 30 * 10**9
    chunks[1].chunk.start_pts = 30 * 10**9
    worker.validate_windows(chunks, recipe)


def test_independent_pts_admission_allows_only_tiny_empty_tail():
    data = recipe_dict()
    data["duration_s"] = 60.033
    recipe = validate(data)
    media = {
        "streams": [{"time_base": "1/1000", "nb_frames": "3", "nb_read_frames": "3"}],
        "frames": [{"best_effort_timestamp": p} for p in (0, 30000, 59999)],
    }
    facts = frame_admission(media, recipe)
    assert facts["occupied_windows"] == [0, 1]
    assert facts["last_frame_pts_s"] == 59.999
    data["duration_s"] = 61
    with pytest.raises(ValueError, match="trailing"):
        frame_admission(media, validate(data))
    media["streams"][0]["nb_frames"] = "4"
    with pytest.raises(ValueError, match="count"):
        frame_admission(media, recipe)
    media["streams"][0]["nb_frames"] = "3"
    media["streams"][0]["nb_read_frames"] = "N/A"
    with pytest.raises(ValueError, match="count was not reported"):
        frame_admission(media, recipe)
    media["streams"][0]["nb_read_frames"] = "3"
    media["frames"][1]["best_effort_timestamp"] = 70000
    with pytest.raises(ValueError, match="ordering"):
        frame_admission(media, recipe)


async def test_rtvi_unqualified_source_binding_never_dispatches(case, tmp_path):
    data = case
    data["evidence_kind"] = "live_provider"
    for arm in data["arms"]:
        arm["captioning"] = "rtvi"
    recipe = validate(data)
    with pytest.raises(ValueError, match="RTVI.*unqualified"):
        preflight(recipe)
    result = await worker.operation(recipe, recipe.arms[0], tmp_path, None)
    assert result == {"status": "failed", "failure": "unsupported_contract"}


async def test_cancellation_drains_job_and_closes_caller_owned_resources(
    case, tmp_path, monkeypatch
):
    started = asyncio.Event()
    settled = asyncio.Event()
    clients_closed = []
    original_close = httpx.AsyncClient.aclose

    async def close(client):
        await original_close(client)
        clients_closed.append(client.is_closed)

    async def respond(request):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            settled.set()

    monkeypatch.setattr(httpx.AsyncClient, "aclose", close)
    monkeypatch.setattr(
        worker,
        "ObservingTransport",
        lambda observer, witness=None: ObservingTransport(
            observer, witness, inner=httpx.MockTransport(respond)
        ),
    )
    recipe = validate(case)
    task = asyncio.create_task(
        worker.operation(
            recipe,
            recipe.arms[0],
            tmp_path,
            SimpleNamespace(base="https://fixture.invalid"),
        )
    )
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    result = await asyncio.wait_for(task, 2)
    assert result["failure"] == "cancelled" and result["status"] == "failed"
    assert settled.is_set() and clients_closed and all(clients_closed)


async def test_cleanup_failure_invalidates_successful_summary(
    case, tmp_path, monkeypatch
):
    from vss.storage import InMemoryStorageAdapter

    requests = mock_transport(monkeypatch)
    clients = []
    original_client = httpx.AsyncClient

    class TrackedClient(original_client):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            clients.append(self)

    monkeypatch.setattr(httpx, "AsyncClient", TrackedClient)

    class BrokenClose(InMemoryStorageAdapter):
        async def aclose(self):
            await super().aclose()
            raise RuntimeError("PRIVATE_CLEANUP_ECHO")

    monkeypatch.setattr(worker, "create_storage", lambda _: BrokenClose())
    recipe = validate(case)
    result = await worker.operation(
        recipe,
        recipe.arms[0],
        tmp_path,
        SimpleNamespace(base="https://fixture.invalid"),
    )
    assert len(requests) == 2 and result["result_ready_s"] > 0
    assert result["failure"] == "cleanup" and result["status"] == "failed"
    assert "PRIVATE_CLEANUP_ECHO" not in json.dumps(result)
    assert len(clients) == 2 and all(client.is_closed for client in clients)


def test_live_reports_never_publish_comparison_statistics(tmp_path, monkeypatch):
    import benchmarks.e2e.__main__ as runner

    data = recipe_dict()
    data["evidence_kind"] = "live_provider"
    recipe = validate(data)
    # Minimal coherent synthetic worker evidence for exercising the parent gate.
    sample = {
        "elapsed_s": 0,
        "self_rss_bytes": 1,
        "tree_rss_bytes": 1,
        "observed_processes": 1,
    }
    raw = json.dumps(
        {
            "status": "ok",
            "result_ready_s": 1,
            "lifecycle_s": 2,
            "stages_s": {
                name: 0.1
                for name in (
                    "captions",
                    "store_write",
                    "store_read",
                    "aggregate_and_summary",
                    "cleanup",
                )
            },
            "captions_sha256": "0" * 64,
            "summary_sha256": "1" * 64,
            "chunks": 1,
            "frames": 8,
            "events": 1,
            "collection_complete": True,
            "providers": {"llm": [{}]},
            "metrics": {
                "summarization.requests": {
                    "count": 1,
                    "sum": 1,
                    "unsuccessful": 0,
                    "unavailable": 0,
                }
            },
            "resources": {
                "interval_s": 0.05,
                "complete": True,
                "samples": [sample, {**sample, "elapsed_s": 2}],
                "self_cpu_time_s": 0,
                "reaped_children_cpu_time_s": 0,
                "io_scope": "unsupported",
            },
        }
    ).encode()
    monkeypatch.setattr(runner, "preflight", lambda _: None)
    monkeypatch.setattr(runner, "isolated_trial", lambda *a, **kw: raw)
    report = runner.run(recipe, tmp_path / "private.json", False)
    assert report.status == "ok" and len(report.trials) == 6
    assert report.statistics is None
    assert report.comparison["status"] == "unqualified_live_workload"
