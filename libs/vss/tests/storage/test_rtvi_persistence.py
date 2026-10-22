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

"""RTVI completion persistence preserves error boundaries and upload ownership."""

from __future__ import annotations

import json
import traceback

import httpx
import pytest

import vss.ingestion.backend as registry
from vss.ingestion import RequestSource, RTVIBackend, RTVIBackendError, ingest
from vss.ingestion.rtvi import _RTVITransport

SECRET = "configured-private-provider-secret"
ASSET = "uploaded-asset"


@pytest.fixture
async def rtvi_case(tmp_path, monkeypatch):
    path = tmp_path / "source.mp4"
    path.write_bytes(b"source uploaded through actual HTTP transport")
    state = {"mode": "success", "trace": []}

    def handler(request):
        state["trace"].append((request.method, request.url.path))
        if request.method == "DELETE":
            if state["mode"] in {"delete-error", "request-and-delete-error"}:
                return httpx.Response(500, json={"detail": SECRET})
            return httpx.Response(204)
        if request.url.path == "/v1/files":
            if state["mode"] == "upload-error":
                return httpx.Response(400, json={"detail": SECRET})
            assert path.read_bytes() in request.content
            return httpx.Response(200, json={"id": ASSET})
        assert request.url.path == "/v1/generate_captions"
        if state["mode"] in {"request-error", "request-and-delete-error"}:
            return httpx.Response(503, json={"detail": SECRET})
        chunk = {
            "chunk_id": 0,
            "start_time": "0",
            "end_time": "1",
            "content": "A person waves.",
            "input_tokens": 2,
            "output_tokens": 3,
        }
        if state["mode"] == "chunk-error":
            chunk["error"] = SECRET
        body = {"chunk_responses": [chunk]}
        if state["mode"] == "sse-error":
            body = {"error": SECRET}
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text="data: " + json.dumps(body) + "\n\ndata: [DONE]\n\n",
        )

    monkeypatch.setenv("RTVI_BASE_URL", "https://rtvi.test")
    monkeypatch.setenv("RTVI_API_KEY", SECRET)
    monkeypatch.delenv("VSS_STORAGE_BACKEND", raising=False)
    async with httpx.AsyncClient(
        base_url="https://rtvi.test", transport=httpx.MockTransport(handler)
    ) as client:
        transport = _RTVITransport(client=client, api_key=SECRET)

        class ConfiguredRTVI(RTVIBackend):
            # Existing public registry factories are constructed with no kwargs.
            def __init__(self):
                super().__init__(transport=transport)

        monkeypatch.setitem(registry._BACKEND_REGISTRY, "stored-rtvi", ConfiguredRTVI)
        yield RequestSource.for_file(str(path)), state, ConfiguredRTVI


@pytest.mark.parametrize(
    "mode",
    [
        "upload-error",
        "request-error",
        "sse-error",
        "delete-error",
        "request-and-delete-error",
    ],
)
async def test_rtvi_transport_failures_have_safe_public_backend_category(
    rtvi_case, mode
):
    source, state, backend_type = rtvi_case
    state["mode"] = mode
    async with backend_type() as backend:
        with pytest.raises(RTVIBackendError) as caught:
            await backend.ingest(source, prompt="Describe.", model="pinned-model")
    assert SECRET not in str(caught.value)
    assert SECRET not in "".join(traceback.format_exception(caught.value))
    if mode != "upload-error":
        assert ("DELETE", f"/v1/files/{ASSET}") in state["trace"]
    if mode == "request-and-delete-error":
        assert "request" in str(caught.value).lower()


async def test_explicit_wire_chunk_error_remains_an_error_response(rtvi_case):
    source, state, backend_type = rtvi_case
    state["mode"] = "chunk-error"
    async with backend_type() as backend:
        chunks = await backend.ingest(source, prompt="Describe.", model="pinned-model")
    assert len(chunks) == 1
    assert chunks[0].error
    assert SECRET not in chunks[0].error
    assert chunks[0].chunk.sourceId == ASSET
    assert ("DELETE", f"/v1/files/{ASSET}") in state["trace"]


@pytest.mark.parametrize(
    "mode",
    ["upload-error", "request-error", "sse-error", "delete-error", "chunk-error"],
)
async def test_failed_rtvi_request_never_publishes_completed_observations(
    tmp_path, rtvi_case, mode
):
    from vss.ingestion import IngestionBackendError
    from vss.storage import FileStorageAdapter
    from vss.summarization import AdapterChunkStore

    source, state, _ = rtvi_case
    state["mode"] = mode
    async with FileStorageAdapter(root=tmp_path / "store") as storage:
        job = ingest(
            source, backends=["stored-rtvi"], model="pinned-model", storage=storage
        )
        try:
            with pytest.raises(IngestionBackendError) as caught:
                await job
        finally:
            await job.wait_closed()
        assert SECRET not in "".join(traceback.format_exception(caught.value))
        with pytest.raises(KeyError):
            await AdapterChunkStore(storage).get_chunks(str(job.id))
    if mode != "upload-error":
        assert ("DELETE", f"/v1/files/{ASSET}") in state["trace"]


async def test_existing_zero_argument_rtvi_factory_can_store_completed_results(
    tmp_path, rtvi_case
):
    from vss.storage import FileStorageAdapter
    from vss.summarization import AdapterChunkStore

    source, state, _ = rtvi_case
    async with FileStorageAdapter(root=tmp_path / "store") as storage:
        job = ingest(
            source, backends=["stored-rtvi"], model="pinned-model", storage=storage
        )
        try:
            chunks = (await job)["stored-rtvi"]
        finally:
            await job.wait_closed()
        assert await AdapterChunkStore(storage).get_chunks(str(job.id)) == chunks
        assert chunks[0].chunk.sourceId == ASSET
        assert ("DELETE", f"/v1/files/{ASSET}") in state["trace"]


async def test_rtvi_original_identity_survives_reopen_after_asset_cleanup(
    tmp_path, rtvi_case
):
    from vss.storage import FileStorageAdapter
    from vss.summarization import AdapterChunkStore

    source, state, _ = rtvi_case
    original_id = source.source_id
    assert original_id != ASSET
    root = tmp_path / "store"
    async with FileStorageAdapter(root=root) as storage:
        job = ingest(
            source, backends=["stored-rtvi"], model="pinned-model", storage=storage
        )
        try:
            chunks = (await job)["stored-rtvi"]
        finally:
            await job.wait_closed()
    assert ("DELETE", f"/v1/files/{ASSET}") in state["trace"]
    calls = list(state["trace"])
    async with FileStorageAdapter(root=root) as reopened:
        snapshot = await reopened.read("observations", str(job.id))
        assert snapshot.records[0]["original_source_id"] == original_id
        loaded = await AdapterChunkStore(reopened).get_chunks(str(job.id))
        assert loaded == chunks
        assert loaded[0].chunk.sourceId == ASSET  # preserve the RTVI wire contract
    assert state["trace"] == calls
    assert source.source_id == original_id


async def test_adapter_source_mutation_cannot_replace_persisted_caller_identity(
    tmp_path, rtvi_case, monkeypatch
):
    from vss.storage import FileStorageAdapter

    source, _, backend_type = rtvi_case
    original_id = source.source_id
    original_ingest = backend_type.ingest

    async def remapped_ingest(self, adapter_source, **kwargs):
        adapter_source.source_id = "adapter-remapped-id"
        return await original_ingest(self, adapter_source, **kwargs)

    monkeypatch.setattr(backend_type, "ingest", remapped_ingest)
    async with FileStorageAdapter(root=tmp_path / "store") as storage:
        job = ingest(
            source, backends=["stored-rtvi"], model="pinned-model", storage=storage
        )
        try:
            chunks = (await job)["stored-rtvi"]
        finally:
            await job.wait_closed()
        manifest = (await storage.read("observations", str(job.id))).records[0]
        assert manifest["original_source_id"] == original_id
        assert source.source_id == original_id
        assert chunks[0].chunk.sourceId == ASSET
