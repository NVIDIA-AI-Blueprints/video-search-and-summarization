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

"""Typed caption records over a transport-independent JSON storage adapter.

This module loads no inference transport. Persistent projections retain caption
content, original source IDs and nanosecond timing, but omit signed URL secrets,
transient artifacts and arbitrary provider metadata. It owns application schemas;
``vss.storage`` owns file framing, atomic replacement and revision checks.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import threading
from collections.abc import Awaitable, Callable
from typing import Any, Literal, cast
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from vss.storage import (
    ElasticsearchStorageAdapter,
    FileStorageAdapter,
    InMemoryStorageAdapter,
    StorageAdapter,
    StorageConfig,
    StorageConfigurationError,
    StorageConflictError,
    StorageCorruptError,
    StorageNotFoundError,
    StorageSnapshot,
)

from .contracts import VlmChunkResponse

_CUSTOM_DOMAIN_ADMISSION = threading.BoundedSemaphore(4)


async def domain_work[T](
    storage: StorageAdapter, function: Callable[..., T], *args: Any
) -> T:
    """Run a domain codec or small blocking check off-loop and settle cancel.

    Built-in adapters route work through their bounded admission/executor. A
    third-party adapter need only implement the public ABC; it uses a shared
    four-operation admission bound and the event loop's worker pool as fallback.
    """
    runner = getattr(storage, "_run", None)
    if isinstance(
        storage,
        (FileStorageAdapter, InMemoryStorageAdapter, ElasticsearchStorageAdapter),
    ) and callable(runner):
        return await cast(Awaitable[T], runner(function, *args))

    admitted = False
    try:
        while not admitted:
            admitted = _CUSTOM_DOMAIN_ADMISSION.acquire(blocking=False)
            if not admitted:
                await asyncio.sleep(0.005)
        task = asyncio.create_task(asyncio.to_thread(function, *args))
        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
            except BaseException:
                break
        try:
            result = task.result()
        except BaseException:
            if cancelled:
                raise asyncio.CancelledError from None
            raise
        if cancelled:
            raise asyncio.CancelledError
        return result
    finally:
        if admitted:
            _CUSTOM_DOMAIN_ADMISSION.release()


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"


class ObservationManifest(_Record):
    """Completion metadata followed by ordinal caption records, not source indices.

    ``original_source_id`` is the caller's identity before upload/adapter mapping.
    Legacy or manually stored lists may omit it; never infer it from an uploaded
    asset ID in a caption response.
    """

    record_type: Literal["observation"] = "observation"
    job_id: str
    completion_state: Literal["complete"] = "complete"
    chunk_count: int = Field(ge=0, strict=True)
    original_source_id: str | None = Field(default=None, strict=True)


class ObservationChunk(_Record):
    """A legacy-compatible response at a fixed position in the returned list."""

    record_type: Literal["chunk"] = "chunk"
    ordinal: int = Field(ge=0, strict=True)
    response: VlmChunkResponse


def durable_response(response: VlmChunkResponse) -> VlmChunkResponse:
    """Return a detached credential-free metadata projection of a caption.

    Free caption/transcript/reasoning text is retained as caller data. Arbitrary
    secrets echoed inside that text cannot be reliably discovered by this codec.
    Known token counts are retained; cached frames and artifact references are
    omitted. The original response is never mutated.
    """
    counters = {
        "input_tokens",
        "output_tokens",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "cached_tokens",
    }
    if any(
        key in counters and (type(value) is not int or value < 0)
        for key, value in response.vlm_stats.items()
    ):
        raise StorageConfigurationError(
            "Known token counters must be nonnegative integers"
        )
    # Project to ordinary fields first. Deep-copying the source model here would
    # traverse cached frame metadata, which may contain tensors or other native
    # objects that are intentionally outside durable storage.
    result = response.model_dump(
        mode="python",
        exclude={
            "chunk": {
                "cached_frames_cv_meta",
                "cv_metadata_json_file",
                "osd_output_video_file",
            },
            "vlm_stats": True,
        },
    )
    chunk = result.get("chunk")
    if chunk is not None:
        chunk.pop("cached_frames_cv_meta", None)
        chunk["cv_metadata_json_file"] = ""
        chunk["osd_output_video_file"] = ""
        if "://" in chunk["file"]:
            try:
                parts = urlsplit(chunk["file"])
                hostname = parts.hostname or ""
                if ":" in hostname:
                    hostname = f"[{hostname}]"
                authority = hostname + (f":{parts.port}" if parts.port else "")
                chunk["file"] = urlunsplit(
                    (parts.scheme, authority, parts.path, "", "")
                )
            except ValueError:
                chunk["file"] = ""
    result["vlm_stats"] = {
        key: value
        for key, value in response.vlm_stats.items()
        if key
        in {
            "input_tokens",
            "output_tokens",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "cached_tokens",
        }
        and type(value) is int
        and value >= 0
    }
    if result["error"]:
        result["error"] = "Provider reported an error"
    try:
        # JSON round-tripping detaches nested fields only after transient objects
        # have been removed, and rejects nonfinite/non-JSON durable values.
        return VlmChunkResponse.model_validate_json(
            json.dumps(result, ensure_ascii=False, allow_nan=False)
        )
    except (ValueError, TypeError):
        raise StorageConfigurationError(
            "Caption contains unsupported durable data"
        ) from None


def observation_records(
    job_id: str,
    chunks: list[VlmChunkResponse],
    original_source_id: str | None = None,
) -> tuple[dict[str, Any], ...]:
    """Encode a complete list preserving caller order, including legacy entries."""
    values = [
        ObservationManifest(
            job_id=job_id,
            chunk_count=len(chunks),
            original_source_id=original_source_id,
        ).model_dump(mode="json", exclude_none=True)
    ]
    values.extend(
        ObservationChunk(ordinal=i, response=durable_response(response)).model_dump(
            mode="json"
        )
        for i, response in enumerate(chunks)
    )
    return tuple(values)


def decode_observations(
    job_id: str, snapshot: StorageSnapshot
) -> list[VlmChunkResponse]:
    """Validate exact list coverage; never salvage a corrupt or incomplete record."""
    try:
        if not snapshot.records:
            raise ValueError
        manifest = ObservationManifest.model_validate(snapshot.records[0])
        if (
            manifest.job_id != job_id
            or len(snapshot.records) != manifest.chunk_count + 1
        ):
            raise ValueError
        chunks = [
            ObservationChunk.model_validate(record) for record in snapshot.records[1:]
        ]
        if [chunk.ordinal for chunk in chunks] != list(range(manifest.chunk_count)):
            raise ValueError
        return [chunk.response.model_copy(deep=True) for chunk in chunks]
    except (ValidationError, ValueError, TypeError):
        raise StorageCorruptError(
            "Invalid or incomplete caption observation snapshot"
        ) from None


class ObservationStore:
    """Caption codec backed by an injected adapter, which remains caller-owned."""

    def __init__(self, storage: StorageAdapter) -> None:
        self.storage = storage

    async def get_chunks(self, job_id: str) -> list[VlmChunkResponse]:
        """Read completed captions; missing raises KeyError, corruption fails closed."""
        try:
            snapshot = await self.storage.read("observations", job_id)
        except StorageNotFoundError:
            raise StorageNotFoundError("unknown summarization job") from None
        return await domain_work(self.storage, decode_observations, job_id, snapshot)

    async def store_chunks(self, job_id: str, chunks: list[VlmChunkResponse]) -> None:
        """Replace one completed list under the shared job claim; preserve order."""
        async with self.storage.exclusive("jobs", job_id):
            records = await domain_work(
                self.storage, observation_records, job_id, chunks
            )
            snapshot = await optional_snapshot(self.storage, "observations", job_id)
            await self.storage.write(
                "observations",
                job_id,
                records,
                expected_revision=snapshot.revision if snapshot else None,
            )

    async def publish(
        self,
        job_id: str,
        chunks: list[VlmChunkResponse],
        *,
        original_source_id: str | None = None,
    ) -> None:
        """Publish under an already-held job claim; conflicting data is preserved.

        A matching existing publication is re-committed so an earlier visible but
        indeterminate write gains a fresh durability acknowledgment.
        Missing legacy origin metadata may be filled from the caller's verified
        request; a known conflicting origin or changed caption remains a conflict.
        """
        records = await domain_work(
            self.storage, observation_records, job_id, chunks, original_source_id
        )
        snapshot = await optional_snapshot(self.storage, "observations", job_id)
        if snapshot is not None and not await domain_work(
            self.storage, _compatible_observations, snapshot.records, records
        ):
            raise StorageConflictError(
                "Completed observations conflict with checkpoint publication"
            )
        await self.storage.write(
            "observations",
            job_id,
            records,
            expected_revision=snapshot.revision if snapshot else None,
        )


async def optional_snapshot(
    storage: StorageAdapter, namespace: str, key: str
) -> StorageSnapshot | None:
    """Treat only a missing snapshot as absent; corruption/I/O errors propagate."""
    try:
        return await storage.read(namespace, key)
    except StorageNotFoundError:
        return None


def _compatible_observations(
    left: tuple[dict[str, Any], ...], right: tuple[dict[str, Any], ...]
) -> bool:
    """Compare records, permitting only addition of missing legacy source identity."""
    if left == right:
        return True
    if not left or not right:
        return False
    previous = dict(left[0])
    if previous.get("original_source_id") is None:
        previous.pop("original_source_id", None)
        identity = right[0].get("original_source_id")
        if identity is not None:
            previous["original_source_id"] = identity
    return previous == right[0] and left[1:] == right[1:]


def environment_storage_config() -> StorageConfig | None:
    """Read explicit storage selection; unset means no persistence, not file default."""
    backend = os.getenv("VSS_STORAGE_BACKEND")
    lock_wait = os.getenv("VSS_STORAGE_LOCK_WAIT_TIMEOUT_S")
    if backend is None:
        if os.getenv("VSS_STORAGE_ROOT") is not None:
            raise StorageConfigurationError(
                "VSS_STORAGE_ROOT requires VSS_STORAGE_BACKEND"
            )
        if lock_wait is not None:
            raise StorageConfigurationError(
                "VSS_STORAGE_LOCK_WAIT_TIMEOUT_S requires VSS_STORAGE_BACKEND"
            )
        return None
    try:
        root = os.getenv("VSS_STORAGE_ROOT")
        values: dict[str, Any] = {"backend": backend.strip().lower(), "root": root}
        if lock_wait is not None:
            if values["backend"] != "file":
                raise StorageConfigurationError(
                    "VSS_STORAGE_LOCK_WAIT_TIMEOUT_S requires the file backend"
                )
            values["lock_wait_timeout_s"] = float(lock_wait)
        if values["backend"] == "elasticsearch":
            values["elasticsearch"] = {
                "url": os.getenv("VSS_STORAGE_ES_URL"),
                "index": os.getenv("VSS_STORAGE_ES_INDEX", "vss-storage"),
                "claim_root": os.getenv("VSS_STORAGE_ES_CLAIM_ROOT"),
                "ca_certs": os.getenv("VSS_STORAGE_ES_CA_CERTS"),
            }
        return StorageConfig.model_validate(values)
    except ValueError:
        raise StorageConfigurationError(
            "Invalid storage configuration; select memory, file, or elasticsearch "
            "with valid backend settings"
        ) from None


class CheckpointWindow(_Record):
    """Frozen half-open source-relative nanosecond window."""

    chunk_idx: int = Field(ge=0, strict=True)
    start_ns: int = Field(ge=0, strict=True)
    end_ns: int = Field(gt=0, strict=True)

    @model_validator(mode="after")
    def bounds(self) -> CheckpointWindow:
        if self.end_ns <= self.start_ns:
            raise ValueError("Invalid checkpoint window")
        return self


class CheckpointManifest(_Record):
    """Request identity and frozen plan; completion is validated against its records."""

    record_type: Literal["checkpoint"] = "checkpoint"
    job_id: str
    original_source_id: str
    source_revision_digest: str
    request_fingerprint: str
    plan_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    pipeline_revision: str
    plan_version: Literal["1.0"] = "1.0"
    sampling_revision: Literal["eight-per-window-v1"] = "eight-per-window-v1"
    backend: str
    model: str
    decoder: str
    completion_state: Literal["running", "complete"] = "running"
    windows: tuple[CheckpointWindow, ...]
    sample_timestamps_ns: tuple[int, ...]


_CHECKPOINT_PLAN_FIELDS = (
    "schema_version",
    "record_type",
    "job_id",
    "original_source_id",
    "source_revision_digest",
    "request_fingerprint",
    "pipeline_revision",
    "plan_version",
    "sampling_revision",
    "backend",
    "model",
    "decoder",
    "windows",
    "sample_timestamps_ns",
)


def checkpoint_plan_fingerprint(
    manifest: CheckpointManifest | dict[str, Any],
) -> str:
    """Hash canonical frozen plan fields, excluding mutable completion state.

    The same helper accepts a plain manifest mapping so callers can compute the
    digest before constructing the validated ``CheckpointManifest``.
    """
    values = (
        manifest.model_dump(mode="json")
        if isinstance(manifest, CheckpointManifest)
        else manifest
    )
    try:
        payload = {field: values[field] for field in _CHECKPOINT_PLAN_FIELDS}
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (KeyError, TypeError, ValueError, UnicodeEncodeError):
        raise StorageConfigurationError(
            "Checkpoint plan is not finite canonical JSON"
        ) from None
    return hashlib.sha256(encoded).hexdigest()


class ConfirmedChunk(_Record):
    """Exactly one successful response or proven empty window, never an error."""

    record_type: Literal["confirmed"] = "confirmed"
    chunk_idx: int = Field(ge=0, strict=True)
    start_ns: int = Field(ge=0, strict=True)
    end_ns: int = Field(gt=0, strict=True)
    state: Literal["success", "empty"]
    response: VlmChunkResponse | None = None

    @model_validator(mode="after")
    def valid(self) -> ConfirmedChunk:
        if self.end_ns <= self.start_ns:
            raise ValueError("Invalid confirmation window")
        if self.state == "empty" and self.response is not None:
            raise ValueError("Empty windows cannot contain a response")
        if self.state == "success" and (
            self.response is None or self.response.error or self.response.chunk is None
        ):
            raise ValueError("Confirmation requires a valid successful chunk")
        return self


def decode_checkpoint(
    job_id: str, snapshot: StorageSnapshot
) -> tuple[CheckpointManifest, dict[int, ConfirmedChunk]]:
    """Verify the frozen plan before accepting confirmation or replay state."""
    try:
        manifest = CheckpointManifest.model_validate(snapshot.records[0])
        if (
            manifest.job_id != job_id
            or manifest.plan_fingerprint != checkpoint_plan_fingerprint(manifest)
        ):
            raise ValueError
        windows = {window.chunk_idx: window for window in manifest.windows}
        if (
            not windows
            or len(windows) != len(manifest.windows)
            or sorted(windows) != list(range(len(windows)))
        ):
            raise ValueError
        if any(
            a.end_ns != b.start_ns
            for a, b in zip(manifest.windows, manifest.windows[1:], strict=False)
        ):
            raise ValueError
        if manifest.windows and manifest.windows[0].start_ns != 0:
            raise ValueError
        if any(
            t < 0 or t >= manifest.windows[-1].end_ns
            for t in manifest.sample_timestamps_ns
        ):
            raise ValueError
        expected_samples = tuple(
            window.start_ns + (sample_idx * (window.end_ns - window.start_ns)) // 8
            for window in manifest.windows
            for sample_idx in range(8)
        )
        if manifest.sample_timestamps_ns != expected_samples:
            raise ValueError
        confirmed = {}
        for record in snapshot.records[1:]:
            item = ConfirmedChunk.model_validate(record)
            window = windows[item.chunk_idx]
            if item.chunk_idx in confirmed or (item.start_ns, item.end_ns) != (
                window.start_ns,
                window.end_ns,
            ):
                raise ValueError
            if item.response is not None:
                chunk = item.response.chunk
                assert chunk is not None
                if (
                    chunk.sourceId != manifest.original_source_id
                    or chunk.chunkIdx != item.chunk_idx
                    or chunk.start_pts != window.start_ns
                    or chunk.end_pts != window.end_ns
                ):
                    raise ValueError
            confirmed[item.chunk_idx] = item
        if manifest.completion_state == "complete" and set(confirmed) != set(windows):
            raise ValueError
        return manifest, confirmed
    except (ValidationError, ValueError, KeyError, IndexError, TypeError):
        raise StorageCorruptError(
            "Invalid checkpoint identity, plan or completion coverage"
        ) from None


def checkpoint_records(
    manifest: CheckpointManifest, confirmed: dict[int, ConfirmedChunk]
) -> tuple[dict[str, Any], ...]:
    """Encode a detached ordered application snapshot for one atomic commit."""
    records = (
        manifest.model_dump(mode="json"),
        *(confirmed[i].model_dump(mode="json") for i in sorted(confirmed)),
    )
    return json.loads(json.dumps(records, allow_nan=False))
