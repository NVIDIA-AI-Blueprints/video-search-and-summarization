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

"""Thin MCP transport over the public VSS ingestion and summarization APIs."""

from __future__ import annotations

import json
import os
from collections import OrderedDict
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path, PureWindowsPath
from typing import Any, Literal
from urllib.parse import unquote, urlsplit

from pydantic import BaseModel, TypeAdapter

from mcp.server.fastmcp import FastMCP

DEFAULT_COMPLETED_JOB_RETENTION = 128


def _capability_installed(module_name: str) -> bool:
    try:
        import_module(module_name)
    except ModuleNotFoundError as error:
        if error.name == module_name:
            return False
        raise
    return True


def _json_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _chunk_count(results: dict[str, Any]) -> int:
    return sum(len(result) for result in results.values() if isinstance(result, list))


def _summary_payload(completion: Any) -> dict[str, Any]:
    choices = completion.choices
    if not choices or choices[0].message.content is None:
        raise ValueError("summarization returned no content")
    content = json.loads(choices[0].message.content)
    if not isinstance(content, dict):
        raise ValueError("summarization content must be a JSON object")
    events = content.get("events", [])
    summary = content.get("video_summary", "")
    if not isinstance(events, list) or not isinstance(summary, str):
        raise ValueError("summarization content has an invalid shape")
    return {"events": events, "summary": summary, "model": completion.model}


@dataclass(slots=True)
class _JobRecord:
    job: Any
    error: str | None = None


class VSSMCPAdapter:
    """JSON-safe agent adapter that delegates work to public VSS APIs."""

    def __init__(
        self,
        workspace: Path,
        *,
        completed_job_retention: int = DEFAULT_COMPLETED_JOB_RETENTION,
    ) -> None:
        if completed_job_retention < 0:
            raise ValueError("completed_job_retention must not be negative")
        self.workspace = workspace.expanduser().resolve()
        self.completed_job_retention = completed_job_retention
        self._jobs: OrderedDict[str, _JobRecord] = OrderedDict()

    def _video_source(self, video: str) -> str:
        if not video.strip():
            raise ValueError("video must not be empty")
        windows_path = PureWindowsPath(video)
        if windows_path.drive:
            if os.name != "nt":
                raise ValueError("local video must be inside the configured workspace")
            candidate = Path(video)
            resolved = candidate.resolve()
            if not resolved.is_relative_to(self.workspace):
                raise ValueError("local video must be inside the configured workspace")
            return str(resolved)
        parsed = urlsplit(video)
        if parsed.scheme and parsed.scheme != "file":
            return video
        if parsed.netloc:
            raise ValueError("local video must be inside the configured workspace")
        candidate = Path(unquote(parsed.path) if parsed.scheme == "file" else video)
        if not candidate.is_absolute():
            candidate = self.workspace / candidate
        resolved = candidate.resolve()
        if not resolved.is_relative_to(self.workspace):
            raise ValueError("local video must be inside the configured workspace")
        return str(resolved)

    async def probe_video(self, video: str, decoder: str = "auto") -> dict[str, Any]:
        """Return JSON-safe metadata for a workspace video."""
        from vss.video_processing import DecodeOptions, probe_video

        facts = await probe_video(
            self._video_source(video),
            options=DecodeOptions(decoder=decoder),
        )
        return facts.model_dump(mode="json")

    async def extract_frames(
        self,
        video: str,
        timestamps: list[float],
        decoder: str = "auto",
        image_format: Literal["jpeg", "png"] = "jpeg",
    ) -> list[dict[str, Any]]:
        """Extract and encode frames at source-relative timestamps in seconds."""
        if len(timestamps) > 256:
            raise ValueError("at most 256 timestamps may be requested")
        from vss.video_processing import DecodeOptions, extract_frames

        frames = await extract_frames(
            self._video_source(video),
            timestamps,
            options=DecodeOptions(decoder=decoder),
        )
        return [
            frame.to_encoded(format=image_format).model_dump(mode="json")
            for frame in frames
        ]

    async def storage_read(self, namespace: str, key: str) -> dict[str, Any]:
        """Read a workspace-scoped file-storage snapshot."""
        from vss.storage import FileStorageAdapter

        async with FileStorageAdapter(root=self.workspace / ".vss") as storage:
            snapshot = await storage.read(namespace, key)
        return snapshot.model_dump(mode="json")

    async def storage_write(
        self,
        namespace: str,
        key: str,
        records: list[dict[str, Any]],
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """Create or compare-and-swap a workspace-scoped storage snapshot."""
        from vss.storage import FileStorageAdapter

        async with FileStorageAdapter(root=self.workspace / ".vss") as storage:
            snapshot = await storage.write(
                namespace,
                key,
                records,
                expected_revision=expected_revision,
            )
        return snapshot.model_dump(mode="json")

    async def ingest(
        self,
        video: str,
        pipeline: str = "auto",
        prompt: str | None = None,
        chunk_duration: int = 60,
    ) -> dict[str, Any]:
        """Ingest a video and return its structured backend responses."""
        if chunk_duration <= 0:
            raise ValueError("chunk_duration must be greater than zero")
        selected_pipeline = pipeline.strip()
        if not selected_pipeline:
            raise ValueError("pipeline must not be empty")

        from vss.ingestion import DEFAULT_INGESTION_PROMPT, RequestSource, ingest

        source_path = self._video_source(video)
        source = RequestSource(url=source_path, source_id=video)
        backends = None if selected_pipeline == "auto" else [selected_pipeline]
        job = ingest(
            source,
            backends=backends,
            prompt=prompt if prompt is not None else DEFAULT_INGESTION_PROMPT,
            chunk_duration=chunk_duration,
        )
        job_id = str(job.id)
        record = _JobRecord(job=job)
        self._jobs[job_id] = record
        try:
            raw_results = await job
        except BaseException as error:
            record.error = f"{type(error).__name__}: {error}"
            self._prune_completed_jobs()
            raise
        results = _json_value(raw_results)
        if not isinstance(results, dict):
            raise TypeError("ingestion returned a non-object result")
        self._prune_completed_jobs()
        return {
            "job_id": job_id,
            "state": str(job.state),
            "pipeline": selected_pipeline,
            "chunks": _chunk_count(results),
            "results": results,
        }

    async def ingest_status(self, job_id: str) -> dict[str, Any]:
        """Return process-local status for a job submitted through this server."""
        try:
            record = self._jobs[job_id]
        except KeyError:
            raise KeyError(f"unknown ingestion job: {job_id}") from None
        state = str(record.job.state)
        results = None
        if record.error is None and record.job.done() and not record.job.cancelled():
            results = _json_value(record.job.result())
            if not isinstance(results, dict):
                raise TypeError("ingestion returned a non-object result")
        return {
            "job_id": job_id,
            "state": state,
            "chunks_completed": _chunk_count(results or {}),
            "done": bool(record.job.done()),
            "error": record.error,
            "results": results,
        }

    def _prune_completed_jobs(self) -> None:
        completed = [
            job_id for job_id, record in self._jobs.items() if record.job.done()
        ]
        excess = len(completed) - self.completed_job_retention
        for job_id in completed[: max(0, excess)]:
            del self._jobs[job_id]

    async def summarize(
        self,
        chunks_json: str,
        prompt: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        """Summarize serialized VLM chunks without changing their structure."""
        from vss.ingestion import VlmChunkResponse
        from vss.summarization import summarize_video

        chunks = TypeAdapter(list[VlmChunkResponse]).validate_json(chunks_json)
        completion = await summarize_video(chunks, prompt=prompt, model=model)
        return _summary_payload(completion)

    async def summarize_from_store(
        self,
        job_id: str,
        prompt: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        """Load complete persisted captions and summarize them."""
        from vss.summarization import summarize_from_store

        completion = await summarize_from_store(job_id, prompt=prompt, model=model)
        return _summary_payload(completion)


def create_server(
    namespace: str = "cosmos", workspace: str | Path | None = None
) -> FastMCP:
    """Create a stdio-capable MCP server with the VSS tools registered."""
    if not namespace.strip():
        raise ValueError("namespace must not be empty")
    adapter = VSSMCPAdapter(Path.cwd() if workspace is None else Path(workspace))
    server = FastMCP(name=namespace)
    server.add_tool(adapter.probe_video, name="probe_video")
    server.add_tool(adapter.extract_frames, name="extract_frames")
    server.add_tool(adapter.storage_read, name="storage_read")
    server.add_tool(adapter.storage_write, name="storage_write")
    if _capability_installed("vss.ingestion"):
        server.add_tool(adapter.ingest, name="ingest")
        server.add_tool(adapter.ingest_status, name="ingest_status")
    if _capability_installed("vss.summarization"):
        server.add_tool(adapter.summarize, name="summarize")
        server.add_tool(adapter.summarize_from_store, name="summarize_from_store")
    return server
