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

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

import vss.ingestion
import vss.mcp.server as mcp_server
import vss.summarization
from vss.ingestion import ChunkInfo, VlmChunkResponse
from vss.mcp import VSSMCPAdapter, create_server

CORE_TOOLS = {"probe_video", "extract_frames", "storage_read", "storage_write"}
DEFAULT_JOB_ID = UUID("147c40d6-98ff-4f7f-8845-9fa62a3a9e8c")


class FakeJob:
    def __init__(
        self,
        results: dict[str, object],
        job_id: UUID = DEFAULT_JOB_ID,
    ) -> None:
        self.id = job_id
        self.state = "pending"
        self._results = results
        self._done = False

    def __await__(self):
        async def wait() -> dict[str, object]:
            self.state = "succeeded"
            self._done = True
            return self._results

        return wait().__await__()

    def done(self) -> bool:
        return self._done

    def cancelled(self) -> bool:
        return False

    def result(self) -> dict[str, object]:
        return self._results


def _completion(
    events: list[dict[str, object]], summary: str, model: str
) -> SimpleNamespace:
    content = json.dumps({"events": events, "video_summary": summary})
    message = SimpleNamespace(content=content)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], model=model)


@pytest.mark.asyncio
async def test_ingest_delegates_and_preserves_structured_results(
    tmp_path, monkeypatch
) -> None:
    chunk = VlmChunkResponse(
        chunk=ChunkInfo(sourceId="camera-1", chunkIdx=0),
        vlm_response='{"events":[]}',
    )
    captured: dict[str, Any] = {}

    def fake_ingest(source, **kwargs):
        captured["source"] = source
        captured.update(kwargs)
        return FakeJob({"cpu_vlm": [chunk]})

    monkeypatch.setattr(vss.ingestion, "ingest", fake_ingest)
    adapter = VSSMCPAdapter(tmp_path)

    result = await adapter.ingest(
        "camera.mp4", pipeline="cpu_vlm", prompt="Find events", chunk_duration=15
    )

    assert captured["source"].url == str(tmp_path / "camera.mp4")
    assert captured["source"].source_id == "camera.mp4"
    assert captured["backends"] == ["cpu_vlm"]
    assert captured["prompt"] == "Find events"
    assert captured["chunk_duration"] == 15
    assert result["job_id"] == "147c40d6-98ff-4f7f-8845-9fa62a3a9e8c"
    assert result["chunks"] == 1
    assert result["results"]["cpu_vlm"][0]["vlm_response"] == '{"events":[]}'

    status = await adapter.ingest_status(result["job_id"])
    assert status["state"] == "succeeded"
    assert status["done"] is True
    assert status["chunks_completed"] == 1


@pytest.mark.asyncio
async def test_ingest_uses_configured_backend_for_auto(tmp_path, monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_ingest(source, **kwargs):
        captured.update(kwargs)
        return FakeJob({"rtvi": []})

    monkeypatch.setattr(vss.ingestion, "ingest", fake_ingest)

    result = await VSSMCPAdapter(tmp_path).ingest("camera.mp4")

    assert captured["backends"] is None
    assert result["pipeline"] == "auto"


@pytest.mark.asyncio
async def test_ingest_rejects_local_path_outside_workspace(tmp_path) -> None:
    adapter = VSSMCPAdapter(tmp_path)

    with pytest.raises(ValueError, match="inside the configured workspace"):
        await adapter.ingest("../private.mp4")


@pytest.mark.asyncio
@pytest.mark.parametrize("video", [r"C:\private\video.mp4", "C:/private/video.mp4"])
async def test_ingest_rejects_windows_drive_path_outside_workspace(
    tmp_path, video
) -> None:
    adapter = VSSMCPAdapter(tmp_path)

    with pytest.raises(ValueError, match="inside the configured workspace"):
        await adapter.ingest(video)


@pytest.mark.asyncio
async def test_ingest_bounds_completed_job_retention(tmp_path, monkeypatch) -> None:
    next_id = 1

    def fake_ingest(source, **kwargs):
        nonlocal next_id
        job = FakeJob({"rtvi": []}, UUID(int=next_id))
        next_id += 1
        return job

    monkeypatch.setattr(vss.ingestion, "ingest", fake_ingest)
    adapter = VSSMCPAdapter(tmp_path, completed_job_retention=1)

    first = await adapter.ingest("first.mp4")
    second = await adapter.ingest("second.mp4")

    with pytest.raises(KeyError, match="unknown ingestion job"):
        await adapter.ingest_status(first["job_id"])
    assert (await adapter.ingest_status(second["job_id"]))["done"] is True


@pytest.mark.asyncio
async def test_summarize_validates_chunks_and_delegates(tmp_path, monkeypatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_summarize(chunks, *, prompt=None, model=None):
        captured["chunks"] = chunks
        captured["prompt"] = prompt
        captured["model"] = model
        return _completion([{"description": "Person enters"}], "One event.", "test")

    monkeypatch.setattr(vss.summarization, "summarize_video", fake_summarize)
    chunks_json = json.dumps(
        [
            VlmChunkResponse(
                chunk=ChunkInfo(sourceId="camera-1", chunkIdx=0),
                vlm_response="Person enters",
            ).model_dump(mode="json")
        ]
    )

    result = await VSSMCPAdapter(tmp_path).summarize(
        chunks_json, prompt="Safety only", model="test"
    )

    assert isinstance(captured["chunks"][0], VlmChunkResponse)
    assert captured["prompt"] == "Safety only"
    assert captured["model"] == "test"
    assert result == {
        "events": [{"description": "Person enters"}],
        "summary": "One event.",
        "model": "test",
    }


@pytest.mark.asyncio
async def test_summarize_from_store_delegates(tmp_path, monkeypatch) -> None:
    async def fake_summarize(job_id, *, prompt=None, model=None):
        assert job_id == "job-1"
        assert prompt == "Incidents only"
        assert model == "test"
        return _completion([], "No incidents.", "test")

    monkeypatch.setattr(vss.summarization, "summarize_from_store", fake_summarize)

    result = await VSSMCPAdapter(tmp_path).summarize_from_store(
        "job-1", prompt="Incidents only", model="test"
    )

    assert result == {"events": [], "summary": "No incidents.", "model": "test"}


@pytest.mark.asyncio
async def test_storage_tools_round_trip_workspace_snapshot(tmp_path) -> None:
    adapter = VSSMCPAdapter(tmp_path)

    written = await adapter.storage_write(
        "agent", "job-1", [{"caption": "Person enters"}]
    )
    read = await adapter.storage_read("agent", "job-1")

    assert written == read
    assert read["revision"] == 1
    assert read["records"] == [{"caption": "Person enters"}]
    assert (tmp_path / ".vss").is_dir()


@pytest.mark.asyncio
async def test_create_server_registers_documented_tools(tmp_path) -> None:
    server = create_server("cosmos-test", tmp_path)

    assert server.name == "cosmos-test"
    assert {tool.name for tool in await server.list_tools()} == CORE_TOOLS | {
        "ingest",
        "ingest_status",
        "summarize",
        "summarize_from_store",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("installed", "expected"),
    [
        ({"vss.ingestion"}, CORE_TOOLS | {"ingest", "ingest_status"}),
        (
            {"vss.summarization"},
            CORE_TOOLS | {"summarize", "summarize_from_store"},
        ),
        (set(), CORE_TOOLS),
    ],
)
async def test_create_server_skips_uninstalled_capabilities(
    tmp_path, monkeypatch, installed: set[str], expected: set[str]
) -> None:
    monkeypatch.setattr(
        mcp_server,
        "_capability_installed",
        lambda module_name: module_name in installed,
    )

    server = mcp_server.create_server(workspace=tmp_path)

    assert {tool.name for tool in await server.list_tools()} == expected
