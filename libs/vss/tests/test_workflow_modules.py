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

"""Actual module dispatch, frozen inputs and adverse comparison boundaries."""

import asyncio
import hashlib
import json
import os
from contextlib import asynccontextmanager, contextmanager
from uuid import uuid4

import pytest
from test_workflow_qualification import caption_event, running
from test_workflow_qualification import workflow as workflow

from benchmarks.workflow.config import Workflow
from benchmarks.workflow.operation import operation
from benchmarks.workflow.relay import Relay


@contextmanager
def fresh_route(workflow, monkeypatch):
    _, fixture, _ = workflow
    with (
        Relay(
            f"http://127.0.0.1:{fixture.server_port}/llm/v1",
            "test-llm",
            str(uuid4()),
            deadline_s=2,
        ) as relay,
        running(relay) as base,
    ):
        monkeypatch.setenv("M8_LLM", base + "/" + relay.token + "/v1")
        monkeypatch.setenv("M8_RELAY", base + "/control/" + relay.token)
        yield


@pytest.mark.parametrize("store", ["memory", "file"])
async def test_actual_module_storage_dispatch_closes_and_deletes_owned_roots(
    workflow, store
):
    profile, fixture, _ = workflow
    result = await operation(profile.model_copy(update={"store_backend": store}), 1)
    assert result["status"] == "completed_diagnostic", result
    assert result["modules"]["storage"] == store and result["cleanup"]["store_closed"]
    assert "library_index_deleted" not in result["cleanup"]
    if store == "file":
        assert result["cleanup"]["file_root_deleted"]
    assert not fixture.assets


@pytest.mark.skipif(os.name != "posix", reason="POSIX diagnostic runtime")
async def test_private_capsule_replay_preserves_source_and_llm_inputs_across_stores(
    workflow, tmp_path, monkeypatch
):
    from benchmarks.workflow.modules import replay
    from benchmarks.workflow.swaps import compare, contract

    profile, fixture, _ = workflow
    capsule = tmp_path / "capsule-private.json"
    capture_profile = profile.model_copy(
        update={
            "store_backend": "memory",
            "caption_contract": "final_file_chunk_set.v1",
            "caption_capture": capsule,
        }
    )
    with fresh_route(workflow, monkeypatch):
        origin = await operation(capture_profile, 1)
    assert origin["status"] == "completed_diagnostic", origin
    assert capsule.stat().st_mode & 0o777 == 0o600
    assert "PRIVATE_CAPTION" in capsule.read_text() and "PRIVATE_" not in json.dumps(
        origin
    )
    sha = hashlib.sha256(capsule.read_bytes()).hexdigest()
    reports = []
    for store in ("memory", "file"):
        p = profile.model_copy(
            update={
                "captioning": "replay",
                "store_backend": store,
                "caption_input": capsule,
                "caption_input_sha256": sha,
            }
        )
        source = replay(p, 1)[0].chunk.sourceId
        with fresh_route(workflow, monkeypatch):
            result = await operation(p, 1)
        assert result["status"] == "completed_diagnostic", result
        assert replay(p, 1)[0].chunk.sourceId == source
        assert (
            "captions" not in result["stages_s"]
            and "upload_and_lookup" not in result["stages_s"]
        )
        assert (
            "upload_to_result_s" not in result
            and result["timing_boundary"] == "store_to_summary"
        )
        reports.append(
            {
                "status": "completed_diagnostic",
                "qualified": False,
                "comparative_statistics": None,
                "software_sha256": "0" * 64,
                "lockfile_sha256": "1" * 64,
                "harness_sha256": "2" * 64,
                "observation_policy": {"scope": "controlled_fixture"},
                "module_comparison_contract": contract(p),
                "operation": result,
            }
        )
    comparison = compare(reports, "store_backend")
    assert (
        comparison["one_requested_slot_verified"]
        and comparison["effective_caption_inputs_equal"]
        and comparison["summary_requests_equal"]
    )
    assert fixture.llm_calls == 3 and not fixture.assets
    assert all("PRIVATE_" not in json.dumps(report) for report in reports)
    drift = json.loads(json.dumps(reports))
    drift[1]["module_comparison_contract"]["summary_max_tokens"] += 1
    with pytest.raises(ValueError, match="more_than_one_slot_changed"):
        compare(drift, "store_backend")
    drift = json.loads(json.dumps(reports))
    drift[1]["operation"]["llm_relay"]["records"][0]["messages_sha256"] = "different"
    assert not compare(drift, "store_backend")["summary_requests_equal"]
    drift = json.loads(json.dumps(reports))
    drift[1]["operation"]["llm_relay"]["records"][0]["requested_controls"][
        "settings_sha256"
    ] = "f" * 64
    assert not compare(drift, "store_backend")["summary_requests_equal"]
    for report in drift:
        del report["operation"]["llm_relay"]["records"][0]["requested_controls"]
    assert not compare(drift, "store_backend")["summary_requests_equal"]
    assert not compare(drift, "store_backend")["summary_request_controls_complete"]
    drift = json.loads(json.dumps(reports))
    drift[1]["operation"]["llm_relay"]["records"][0]["usage"]["completion_tokens"] += 10
    comparison = compare(drift, "store_backend")
    assert (
        comparison["summary_requests_equal"]
        and not comparison["output_equality_required"]
    )
    for field in ("software_sha256", "lockfile_sha256", "harness_sha256"):
        drift = json.loads(json.dumps(reports))
        drift[1][field] = "3" * 64
        with pytest.raises(ValueError, match="software_or_lock_identity"):
            compare(drift, "store_backend")
    drift = json.loads(json.dumps(reports))
    drift[1]["observation_policy"] = {"scope": "different"}
    with pytest.raises(ValueError, match="observation_policy_changed"):
        compare(drift, "store_backend")


@pytest.mark.skipif(os.name != "posix", reason="POSIX diagnostic runtime")
@pytest.mark.parametrize(
    "mode",
    [
        "hash",
        "source",
        "content",
        "malformed",
        "permissions",
        "symlink",
        "oversize",
        "capture_collision",
    ],
)
async def test_capsule_admission_fails_before_summary(
    workflow, tmp_path, monkeypatch, mode
):
    from benchmarks.workflow.modules import capture, replay
    from vss.ingestion import RequestSource
    from vss.ingestion.backend import _to_chunk_response

    profile, fixture, _ = workflow
    profile = profile.model_copy(
        update={"duration_s": 30, "caption_capture": tmp_path / "capsule.json"}
    )
    chunks = [
        _to_chunk_response(RequestSource(source_id=str(uuid4())), caption_event(0))
    ]
    capture(chunks, profile, 1)
    path = profile.caption_capture
    if mode == "capture_collision":
        original = path.read_bytes()
        with pytest.raises(FileExistsError):
            capture(chunks, profile, 1)
        assert path.read_bytes() == original
        return
    if mode in ("source", "content"):
        body = json.loads(path.read_bytes())
        if mode == "source":
            body["task"]["source_sha256"] = "0" * 64
        else:
            body["chunks"][0]["vlm_response"] = "changed"
        path.write_text(json.dumps(body))
    if mode == "malformed":
        path.write_text("bad JSON")
    if mode == "oversize":
        path.write_bytes(b" " * (1024**2 + 1))
    if mode == "permissions":
        path.chmod(0o644)
    if mode == "symlink":
        link = tmp_path / "link"
        link.symlink_to(path)
        path = link
    p = profile.model_copy(
        update={
            "captioning": "replay",
            "caption_capture": None,
            "caption_input": path,
            "caption_input_sha256": "0" * 64
            if mode == "hash"
            else hashlib.sha256(path.read_bytes()).hexdigest(),
            "store_backend": "memory",
        }
    )
    with pytest.raises((ValueError, OSError)):
        replay(p, 1)
    with fresh_route(workflow, monkeypatch):
        result = await operation(p, 1)
    assert (
        result["status"] == "failed" and result["failure_stage"] == "replay_admission"
    )
    assert fixture.llm_calls == 0 and not fixture.assets


@pytest.mark.parametrize(
    "drift",
    [
        {"arm": "normal", "store_backend": "memory"},
        {"captioning": "replay"},
        {"caption_input_sha256": "0" * 64},
        {"arm": "normal", "captioning": "cpu_vlm"},
    ],
)
def test_module_configuration_rejects_ignored_or_incomplete_selections(workflow, drift):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Workflow.model_validate(workflow[0].model_dump() | drift)


async def test_native_cpu_module_records_actual_vlm_images_and_selections(workflow):
    import numpy as np

    cv2 = pytest.importorskip("cv2")
    profile, fixture, _ = workflow
    writer = cv2.VideoWriter(
        str(profile.video), cv2.VideoWriter.fourcc(*"mp4v"), 10, (64, 64)
    )
    assert writer.isOpened()
    for i in range(20):
        writer.write(np.full((64, 64, 3), i, dtype=np.uint8))
    writer.release()
    result = await operation(
        profile.model_copy(update={"captioning": "cpu_vlm", "store_backend": "memory"}),
        1,
    )
    assert result["status"] == "completed_diagnostic", result
    observation = result["frame_provider_observation"]
    assert len(observation["selected"]) == fixture.frame_calls == 1
    assert (
        observation["inputs"][0]["image_count"]
        == len(observation["selected"][0]["pts_ns"])
        == 8
    )
    assert len(observation["inputs"][0]["image_sha256"]) == 8
    assert observation["finish_scope"] == "compatibility_outer_not_native"
    assert "upload_to_result_s" not in result and not fixture.assets
    assert (
        fixture.llm_calls == 1 and result["native_caption_completion"] == "unqualified"
    )
    assert "PRIVATE_" not in json.dumps(result)


@pytest.mark.parametrize("field", ["content", "source", "frames", "usage"])
async def test_corrupt_storage_readback_prevents_final_summary(
    workflow, monkeypatch, field
):
    from benchmarks.workflow.operation import AdapterChunkStore

    profile, fixture, _ = workflow
    original = AdapterChunkStore.get_chunks

    async def corrupt(self, key):
        chunks = await original(self, key)
        if field == "content":
            chunks[0].vlm_response = "different"
        if field == "source":
            chunks[0].chunk.sourceId = "different"
        if field == "frames":
            chunks[0].rtvi_frame_count += 1
        if field == "usage":
            chunks[0].vlm_stats["input_tokens"] += 1
        return chunks

    monkeypatch.setattr(AdapterChunkStore, "get_chunks", corrupt)
    result = await operation(profile.model_copy(update={"store_backend": "file"}), 1)
    assert result["status"] == "failed" and fixture.llm_calls == 0
    assert result["cleanup"]["store_closed"] and result["cleanup"]["file_root_deleted"]


@pytest.mark.skipif(os.name != "posix", reason="POSIX diagnostic runtime")
def test_capsule_fifo_rejected_without_blocking(workflow, tmp_path):
    import os
    import time

    from benchmarks.workflow.modules import replay

    fifo = tmp_path / "fifo"
    os.mkfifo(fifo, 0o600)
    p = workflow[0].model_copy(
        update={
            "captioning": "replay",
            "caption_input": fifo,
            "caption_input_sha256": "0" * 64,
        }
    )
    start = time.monotonic()
    with pytest.raises(ValueError, match="permissions"):
        replay(p, 1)
    assert time.monotonic() - start < 1


@pytest.mark.parametrize("mode", ["success", "error", "cancel", "timeout", "setup"])
async def test_http_clients_close_within_shared_cleanup_budget(
    workflow, monkeypatch, mode
):
    import importlib

    module = importlib.import_module("benchmarks.workflow.operation")
    original = module.client
    clients, attempts, blocked = [], [], []
    closing, release = asyncio.Event(), asyncio.Event()

    @asynccontextmanager
    async def observed_client(*args, **kwargs):
        index = len(clients)
        if mode == "setup" and index == 2:
            raise ValueError("client setup failed")
        value = original(*args, **kwargs)
        clients.append(value)
        async with value:
            try:
                yield value
            finally:
                attempts.append(index)
                if mode == "success" and index == 2:
                    closing.set()
                    await release.wait()
                if mode == "timeout" and index in {2, 3}:
                    blocked.append(index)
                    await asyncio.Event().wait()
                if index == 2 and mode == "error":
                    raise RuntimeError("client close failed")
                if index == 2 and mode == "cancel":
                    raise asyncio.CancelledError("client close cancelled")

    monkeypatch.setattr(module, "client", observed_client)
    profile, fixture, _ = workflow
    profile = profile.model_copy(
        update={"store_backend": "memory", "cleanup_timeout_s": 1}
    )
    task = asyncio.create_task(operation(profile, 1))
    try:
        if mode == "success":
            await asyncio.wait_for(closing.wait(), 5)
            assert not task.done(), "success must wait for HTTP client closure"
            release.set()
        result = await asyncio.wait_for(task, 5)
        assert len(clients) == (2 if mode == "setup" else 5)
        assert attempts == list(reversed(range(len(clients))))
        assert all(value.is_closed for value in clients)
        assert not fixture.assets
        if mode == "success":
            assert result["status"] == "completed_diagnostic"
            assert result["cleanup"]["http_clients_closed"] is True
            assert (
                result["operational_settlement"]
                == "outer_completed_owned_cleanup_complete"
            )
        else:
            assert result["status"] == "failed"
            assert (
                result["operational_settlement"]
                != "outer_completed_owned_cleanup_complete"
            )
            if mode == "setup":
                assert result["failure_stage"] == "client_setup"
                assert result["cleanup"]["http_clients_closed"] is True
            else:
                assert result["cleanup"]["http_clients_closed"] is False
        if mode == "timeout":
            assert blocked == [3, 2]
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        for value in clients:
            await value.aclose()


async def test_file_root_retained_when_storage_drain_fails(
    workflow, monkeypatch, tmp_path
):
    import importlib

    from vss.storage import FileStorageAdapter

    module = importlib.import_module("benchmarks.workflow.operation")
    root = tmp_path / "owned"
    root.mkdir()
    monkeypatch.setattr(module.tempfile, "mkdtemp", lambda **kwargs: str(root))
    close = FileStorageAdapter.aclose
    adapter = []

    async def failure(self):
        adapter.append(self)
        raise RuntimeError("close failed")

    monkeypatch.setattr(FileStorageAdapter, "aclose", failure)
    removed = []
    monkeypatch.setattr(module.shutil, "rmtree", lambda path: removed.append(path))
    try:
        result = await operation(
            workflow[0].model_copy(update={"store_backend": "file"}), 1
        )
        assert result["status"] == "failed" and not result["cleanup"]["store_closed"]
        assert (
            not result["cleanup"]["file_root_deleted"] and root.exists() and not removed
        )
    finally:
        for value in adapter:
            await close(value)
        # pytest removes this bounded owned root after the real adapter drains.


@pytest.mark.parametrize(
    "mode", ["missing_usage", "length", "at_cap", "wrong_model", "unknown_finish"]
)
async def test_bad_frame_completion_prevents_store_and_summary(workflow, mode):
    import numpy as np

    cv2 = pytest.importorskip("cv2")
    p, fixture, _ = workflow
    writer = cv2.VideoWriter(
        str(p.video), cv2.VideoWriter.fourcc(*"mp4v"), 10, (64, 64)
    )
    for _ in range(20):
        writer.write(np.zeros((64, 64, 3), dtype=np.uint8))
    writer.release()
    if mode == "missing_usage":
        fixture.usage = {}
    if mode == "length":
        fixture.reason = "length"
    if mode == "at_cap":
        fixture.usage = {"prompt_tokens": 8, "completion_tokens": 256}
    if mode == "wrong_model":
        fixture.frame_model_override = "different"
    if mode == "unknown_finish":
        fixture.reason = "unknown"
    result = await operation(
        p.model_copy(update={"captioning": "cpu_vlm", "store_backend": "memory"}), 1
    )
    assert result["status"] == "failed" and fixture.llm_calls == 0
    assert "store_write" not in result["stages_s"] and result["cleanup"]["store_closed"]
