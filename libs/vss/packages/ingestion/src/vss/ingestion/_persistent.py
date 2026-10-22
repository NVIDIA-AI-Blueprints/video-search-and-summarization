# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Private persistence orchestration; transport configuration remains ephemeral."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import math
import os
import stat as stat_module
import threading
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast
from urllib.parse import unquote, urlsplit

from pydantic import ValidationError

from vss.storage import (
    StorageAdapter,
    StorageConfigurationError,
    StorageConflictError,
    StorageCorruptError,
    StorageLimitError,
    StorageSnapshot,
)

from .backend import _BACKEND_REGISTRY, IngestionBackend
from .contracts import RequestSource, VlmChunkResponse
from .exceptions import IngestionBackendError, VLMBackendError
from .persistence import (
    CheckpointManifest,
    CheckpointWindow,
    ConfirmedChunk,
    ObservationStore,
    checkpoint_plan_fingerprint,
    checkpoint_records,
    decode_checkpoint,
    domain_work,
    durable_response,
    optional_snapshot,
)
from .source_identity import ImmutableSourceIdentity, SourceIdentityProvider
from .vlm import (
    _VLM_BACKEND_REGISTRY,
    DEFAULT_SYSTEM_PROMPT,
    DEFAULT_VLM_BASE_URL,
    OpenAICompatibleVLMBackend,
    VLMBackend,
)


@dataclass(frozen=True)
class ResolvedIngestionSpec:
    """One submission-time inference specification; never serialized directly."""

    backend: str
    factory: type[VLMBackend] | type[IngestionBackend]
    local: bool
    endpoint: str = field(repr=False)
    api_key: str | None = field(repr=False)
    model: str | None
    decoder: str
    prompt: str = field(repr=False)
    generation: dict[str, Any] | None = field(repr=False)
    chunk_duration: int
    overlap: int
    system_prompt: str = field(default=DEFAULT_SYSTEM_PROMPT, repr=False)

    def identity(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "implementation": f"{self.factory.__module__}.{self.factory.__qualname__}",
            "endpoint_digest": hashlib.sha256(self.endpoint.encode()).hexdigest(),
            "model": self.model,
            "decoder": self.decoder,
            "prompt_digest": hashlib.sha256(self.prompt.encode()).hexdigest(),
            "system_prompt_digest": hashlib.sha256(
                self.system_prompt.encode()
            ).hexdigest(),
            "generation": {"max_tokens": 1024, **(self.generation or {})},
            "chunk_duration": self.chunk_duration,
            "overlap": self.overlap,
            "sampling_revision": "eight-per-window-v1",
            "plan_version": "1.0",
        }


def capture_spec(
    pipeline: str,
    *,
    prompt: str,
    model: str | None,
    decoder: str | None,
    chunk_duration: int,
    chunk_overlap_duration: int,
    generation_config: dict[str, Any] | None,
) -> ResolvedIngestionSpec:
    """Resolve builtin runtime values once, keeping secrets only in RAM."""
    requested = (
        os.getenv("VSS_INGESTION_BACKEND", "rtvi").strip().lower() or "rtvi"
        if pipeline == "auto"
        else pipeline
    )
    selected = "vlm" if requested in {"cpu_vlm", "gpu_vlm"} else requested
    local = selected in _VLM_BACKEND_REGISTRY
    registry: Any = _VLM_BACKEND_REGISTRY if local else _BACKEND_REGISTRY
    try:
        factory = registry[selected]
    except KeyError:
        raise StorageConfigurationError("Unknown ingestion backend") from None
    try:
        # Disallow opaque/nonfinite request objects before queueing or network I/O.
        generation = json.loads(json.dumps(generation_config, allow_nan=False))
    except (TypeError, ValueError):
        raise StorageConfigurationError(
            "Generation settings must be finite JSON"
        ) from None
    if generation is not None and not isinstance(generation, dict):
        raise StorageConfigurationError("generation_config must be a JSON object")
    if generation and {"messages", "model", "stream"} & generation.keys():
        raise StorageConfigurationError(
            "Generation settings override reserved request fields"
        )
    if requested == "rtvi":
        endpoint = os.getenv("RTVI_BASE_URL") or "http://127.0.0.1:8018"
        api_key = os.getenv("RTVI_API_KEY")
        resolved_model = model or os.getenv("RTVI_MODEL")
    elif requested == "rtvi_embed":
        endpoint = os.getenv("RTVI_EMBED_BASE_URL") or "http://127.0.0.1:8017"
        api_key = os.getenv("RTVI_EMBED_API_KEY")
        resolved_model = model or os.getenv("RTVI_EMBED_MODEL")
    else:
        endpoint = os.getenv("VLM_BASE_URL") or (
            DEFAULT_VLM_BASE_URL if local else "http://127.0.0.1:8018"
        )
        api_key = os.getenv("VLM_API_KEY")
        resolved_model = model or os.getenv("VLM_MODEL")
    endpoint = endpoint.rstrip("/")
    parts = urlsplit(endpoint)
    if parts.username or parts.password or parts.query or parts.fragment:
        raise StorageConfigurationError(
            "Persisted ingestion requires a credential-free endpoint URL"
        )
    if requested == "cpu_vlm":
        selected_decoder = "opencv"
    elif requested == "gpu_vlm":
        selected_decoder = "pynv"
    else:
        selected_decoder = (
            decoder or os.getenv("VLM_DECODER", "auto").strip().lower() or "auto"
        )
    return ResolvedIngestionSpec(
        requested,
        factory,
        local,
        endpoint,
        api_key,
        resolved_model,
        selected_decoder,
        prompt,
        generation,
        chunk_duration,
        chunk_overlap_duration,
    )


def validate_checkpoint_options(
    spec: ResolvedIngestionSpec,
    *,
    resume: bool,
    persistent: bool,
    explicit_model: str | None,
    pipeline_revision: str | None,
) -> None:
    if resume and not persistent:
        raise StorageConfigurationError("Resume requires persistent storage")
    if resume and not spec.local:
        raise StorageConfigurationError(
            "This whole-request backend does not support resume; "
            "retrieve completed captions instead"
        )
    if spec.local and persistent:
        if not issubclass(spec.factory, OpenAICompatibleVLMBackend):
            raise StorageConfigurationError(
                "Custom VLM backend has no reproducible checkpoint specification"
            )
        if (
            not explicit_model
            or not explicit_model.strip()
            or not pipeline_revision
            or not pipeline_revision.strip()
        ):
            raise StorageConfigurationError(
                "Checkpoint mode requires explicit model and pipeline_revision"
            )
        if not spec.decoder or spec.decoder == "auto":
            raise StorageConfigurationError(
                "Checkpoint mode requires a concrete decoder such as opencv or pynv"
            )
        if spec.overlap != 0 or not 1 <= spec.chunk_duration <= 3600:
            raise StorageConfigurationError(
                "Checkpoint mode requires nonoverlapping windows of 1–3600 seconds"
            )


def _source_path(source: RequestSource) -> Path:
    if not source.url:
        raise StorageConfigurationError(
            "Checkpoint mode requires a local immutable source"
        )
    parts = urlsplit(source.url)
    if parts.scheme == "file":
        if parts.netloc not in ("", "localhost"):
            raise StorageConfigurationError("Nonlocal file URI is unsupported")
        return Path(unquote(parts.path))
    # POSIX filenames can contain ':'. Match processing's existing-local-path
    # precedence before interpreting a relative name as a URL scheme.
    path = Path(source.url)
    if path.is_file():
        return path
    if parts.scheme and not (
        len(parts.scheme) == 1 and len(source.url) > 2 and source.url[1] == ":"
    ):
        raise StorageConfigurationError(
            "Checkpoint mode requires local media; URL fetching is separate"
        )
    return Path(source.url)


def _stat(path: Path) -> tuple[int, ...]:
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _regular_file_stat(path: Path) -> tuple[int, ...]:
    info = path.stat()
    if not stat_module.S_ISREG(info.st_mode):
        raise StorageConfigurationError(
            "Checkpoint source must be a regular local file"
        )
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def validate_source_identity_provider(
    provider: SourceIdentityProvider,
    source: RequestSource,
    *,
    checkpointed: bool,
) -> None:
    """Reject unsupported provider use synchronously, before queue submission."""
    if not checkpointed:
        raise StorageConfigurationError(
            "A source identity provider requires persistent local checkpoint mode"
        )
    resolve = getattr(provider, "resolve", None)
    if not callable(resolve) or not inspect.iscoroutinefunction(resolve):
        raise StorageConfigurationError(
            "Source identity provider must implement async resolve(source)"
        )
    _source_path(source)


async def resolve_source_identity(
    source: RequestSource, provider: SourceIdentityProvider
) -> tuple[ImmutableSourceIdentity, tuple[int, ...]]:
    """Resolve a trusted identity while checking the bound local file metadata."""
    try:
        path = _source_path(source)
        before = await asyncio.to_thread(_regular_file_stat, path)
    except OSError:
        raise StorageConfigurationError(
            "Immutable source is unavailable or unreadable"
        ) from None

    try:
        raw_identity = await provider.resolve(source.model_copy(deep=True))
    except asyncio.CancelledError:
        raise
    except Exception:
        raise StorageConfigurationError(
            "Trusted source identity provider failed or returned an invalid identity"
        ) from None

    try:
        after = await asyncio.to_thread(_regular_file_stat, path)
    except OSError:
        raise StorageConflictError(
            "Source changed or disappeared during identity resolution"
        ) from None
    if before != after:
        raise StorageConflictError("Source changed during identity resolution")

    try:
        identity = ImmutableSourceIdentity.model_validate(raw_identity)
    except ValidationError:
        raise StorageConfigurationError(
            "Trusted source identity provider failed or returned an invalid identity"
        ) from None
    if identity.size_bytes != before[2]:
        raise StorageConflictError("Source size changed during identity resolution")
    return identity, before


def _hash_source(
    source: RequestSource, stop: threading.Event
) -> tuple[str, tuple[int, ...]]:
    """Hash complete content, stopping between bounded reads on cancellation."""
    if stop.is_set():
        raise asyncio.CancelledError
    try:
        path = _source_path(source)
        before = _stat(path)
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while not stop.is_set():
                block = stream.read(1024 * 1024)
                if not block:
                    break
                digest.update(block)
        if stop.is_set():
            raise asyncio.CancelledError
        if before != _stat(path):
            raise StorageConflictError("Source changed during content hashing")
        return digest.hexdigest(), before
    except OSError:
        raise StorageConfigurationError(
            "Immutable source is unavailable or unreadable"
        ) from None


async def source_identity(source: RequestSource) -> tuple[str, tuple[int, ...]]:
    """Read full content for resume identity; cancel after the in-flight read."""
    # Only running ingestion jobs submit hashes; admission is bounded by the queue.
    stop = threading.Event()
    task = asyncio.create_task(asyncio.to_thread(_hash_source, source, stop))
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
            stop.set()
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


class CheckpointRun:
    """Confirmed inference and explicit empty windows committed before advancing."""

    def __init__(
        self,
        storage: StorageAdapter,
        manifest: CheckpointManifest,
        revision: int,
        confirmed: dict[int, ConfirmedChunk],
        source: RequestSource,
        stat: tuple[int, ...],
    ) -> None:
        self.storage, self.manifest, self.revision = storage, manifest, revision
        self.confirmed, self.source, self.stat = confirmed, source, stat
        self._advanced_until = 0

    def _commit_records(self) -> tuple[dict[str, Any], ...]:
        try:
            if _stat(_source_path(self.source)) != self.stat:
                raise StorageConflictError("Source changed during checkpoint attempt")
        except OSError:
            raise StorageConflictError(
                "Source changed or disappeared during checkpoint attempt"
            ) from None
        return checkpoint_records(self.manifest, self.confirmed)

    async def commit(self) -> None:
        records = await domain_work(self.storage, self._commit_records)
        snapshot = await self.storage.write(
            "checkpoints",
            self.manifest.job_id,
            records,
            expected_revision=self.revision,
        )
        self.revision = snapshot.revision

    async def advance(self, next_idx: int) -> None:
        """Chronological frame progress proves earlier unsampled windows empty."""
        while self._advanced_until < next_idx:
            window = self.manifest.windows[self._advanced_until]
            if window.chunk_idx not in self.confirmed:
                self.confirmed[window.chunk_idx] = ConfirmedChunk(
                    chunk_idx=window.chunk_idx,
                    start_ns=window.start_ns,
                    end_ns=window.end_ns,
                    state="empty",
                )
                await self.commit()
            self._advanced_until += 1

    @property
    def duration_s(self) -> float:
        return self.manifest.windows[-1].end_ns / 1_000_000_000

    @property
    def sample_timestamps_ns(self) -> tuple[int, ...]:
        return self.manifest.sample_timestamps_ns

    def is_confirmed(self, index: int) -> bool:
        return index in self.confirmed

    async def get_responses(self) -> list[VlmChunkResponse]:
        return await domain_work(self.storage, self.responses)

    async def confirm(self, idx: int, response: VlmChunkResponse) -> None:
        window = self.manifest.windows[idx]
        try:
            item = ConfirmedChunk(
                chunk_idx=idx,
                start_ns=window.start_ns,
                end_ns=window.end_ns,
                state="success",
                response=await domain_work(self.storage, durable_response, response),
            )
            chunk = item.response.chunk if item.response else None
            if chunk is None or (
                chunk.sourceId,
                chunk.chunkIdx,
                chunk.start_pts,
                chunk.end_pts,
            ) != (
                self.manifest.original_source_id,
                idx,
                window.start_ns,
                window.end_ns,
            ):
                raise ValueError
        except (ValidationError, ValueError, TypeError):
            raise StorageCorruptError(
                "VLM response cannot confirm the planned source/window"
            ) from None
        self.confirmed[idx] = item
        await self.commit()

    def responses(self) -> list[VlmChunkResponse]:
        results = []
        for i in sorted(self.confirmed):
            response = self.confirmed[i].response
            if response is not None:
                results.append(response.model_copy(deep=True))
        for i, result in enumerate(results):
            assert result.chunk is not None
            result.chunk.is_first = result.chunk.chunkIdx == 0
            result.chunk.is_last = i == len(results) - 1
        return results

    async def complete(self) -> list[VlmChunkResponse]:
        """Publish only after clean EOF and exact success/empty coverage."""
        await self.advance(len(self.manifest.windows))
        if not any(item.response is not None for item in self.confirmed.values()):
            raise VLMBackendError("video processing produced no frames for vlm")
        self.manifest = self.manifest.model_copy(
            update={"completion_state": "complete"}
        )
        await self.commit()
        # Apply the same read validation to our own complete state transition.
        records = await domain_work(
            self.storage, checkpoint_records, self.manifest, self.confirmed
        )
        await domain_work(
            self.storage,
            decode_checkpoint,
            self.manifest.job_id,
            StorageSnapshot(revision=self.revision, records=records),
        )
        return await domain_work(self.storage, self.responses)


def _fingerprint(
    spec: ResolvedIngestionSpec,
    source: RequestSource,
    digest: str,
    pipeline_revision: str,
    source_revision: str | None,
    trusted_identity: ImmutableSourceIdentity | None = None,
) -> str:
    value = {
        **spec.identity(),
        "source_id": source.source_id,
        "camera_id": source.camera_id,
        "source_digest": digest,
        "source_revision": source_revision,
        "pipeline_revision": pipeline_revision,
    }
    if trusted_identity is not None:
        value["trusted_source_identity"] = {
            "sha256": trusted_identity.sha256,
            "size_bytes": trusted_identity.size_bytes,
            "provider_namespace": trusted_identity.provider_namespace,
            "revision": trusted_identity.revision,
        }
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _local_backend(spec: ResolvedIngestionSpec) -> OpenAICompatibleVLMBackend:
    """Construct the supported adapter with its exact submission-time settings."""
    factory = cast(type[OpenAICompatibleVLMBackend], spec.factory)
    instance = factory(
        base_url=spec.endpoint,
        api_key=spec.api_key,
        model=spec.model,
        system_prompt=spec.system_prompt,
    )
    # These constructors conventionally resolve None from the environment.
    # Persistence has already resolved it: absence must remain absent even if
    # the environment changes while the job waits in the queue.
    instance._api_key = spec.api_key
    instance._model = spec.model
    return instance


async def execute(
    source: RequestSource,
    spec: ResolvedIngestionSpec,
    storage: StorageAdapter,
    job_id: str,
    *,
    resume: bool,
    pipeline_revision: str | None,
    source_revision: str | None,
    source_identity_provider: SourceIdentityProvider | None = None,
) -> list[VlmChunkResponse]:
    """Hold shared ownership across checkpointing, inference and publication."""
    from . import vlm as processing

    original_source_id = source.source_id
    async with AsyncExitStack() as cleanup, storage.exclusive("jobs", job_id):
        saved = await optional_snapshot(storage, "checkpoints", job_id)
        observations = await optional_snapshot(storage, "observations", job_id)
        if not resume and (saved is not None or observations is not None):
            raise StorageConflictError(
                "This durable job already exists; "
                "use resume or retrieve its completed captions"
            )
        if spec.local and storage.persistent:
            if resume and saved is None:
                from vss.storage import StorageNotFoundError

                raise StorageNotFoundError("No checkpoint exists for the requested job")
            if source_identity_provider is None:
                digest, stat = await source_identity(source)
                trusted_identity = None
            else:
                trusted_identity, stat = await resolve_source_identity(
                    source, source_identity_provider
                )
                digest = trusted_identity.sha256
            assert pipeline_revision is not None and spec.model is not None
            fingerprint = _fingerprint(
                spec,
                source,
                digest,
                pipeline_revision,
                source_revision,
                trusted_identity,
            )
            if saved is not None:
                manifest, confirmed = await domain_work(
                    storage, decode_checkpoint, job_id, saved
                )
                if (
                    manifest.request_fingerprint != fingerprint
                    or manifest.original_source_id != source.source_id
                ):
                    raise StorageConflictError(
                        "Checkpoint source or inference settings changed"
                    )
                run = CheckpointRun(
                    storage, manifest, saved.revision, confirmed, source, stat
                )
                # Visibility after a failed flush is insufficient: re-commit first.
                await run.commit()
                if manifest.completion_state == "complete":
                    results = await domain_work(storage, run.responses)
                    await ObservationStore(storage).publish(
                        job_id, results, original_source_id=original_source_id
                    )
                    return results
            else:
                await cleanup.enter_async_context(processing._processing_limiter())
                facts = await processing.probe_video(
                    source,
                    source_id=source.source_id,
                    options=processing.DecodeOptions(decoder=spec.decoder),
                )
                if facts.duration_s is not None and (
                    facts.duration_s <= 0 or not math.isfinite(facts.duration_s)
                ):
                    raise VLMBackendError(
                        "vlm ingestion requires a video with a positive duration"
                    )
                end = (
                    round(facts.duration_s * 1_000_000_000)
                    if facts.duration_s is not None
                    else await processing._duration_ns_from_decode(
                        source,
                        source_id=source.source_id,
                        decoder=spec.decoder,
                    )
                )
                size = spec.chunk_duration * 1_000_000_000
                count = (end + size - 1) // size
                max_records = getattr(storage, "max_records", 100_000)
                if count + 1 > max_records:
                    raise StorageLimitError(
                        "Checkpoint plan exceeds storage record limit"
                    )
                windows = tuple(
                    CheckpointWindow(
                        chunk_idx=i, start_ns=i * size, end_ns=min((i + 1) * size, end)
                    )
                    for i in range(count)
                )
                manifest = CheckpointManifest(
                    plan_fingerprint="0" * 64,
                    job_id=job_id,
                    original_source_id=source.source_id,
                    source_revision_digest=digest,
                    request_fingerprint=fingerprint,
                    pipeline_revision=pipeline_revision,
                    backend=spec.backend,
                    model=spec.model,
                    decoder=spec.decoder,
                    windows=windows,
                    sample_timestamps_ns=processing._chunk_sample_timestamps_ns(
                        end / 1_000_000_000, spec.chunk_duration
                    ),
                )
                manifest = manifest.model_copy(
                    update={
                        "plan_fingerprint": await domain_work(
                            storage, checkpoint_plan_fingerprint, manifest
                        )
                    }
                )
                initial_records = await domain_work(
                    storage, checkpoint_records, manifest, {}
                )
                initial = await storage.write(
                    "checkpoints",
                    job_id,
                    initial_records,
                    expected_revision=None,
                )
                run = CheckpointRun(
                    storage, manifest, initial.revision, {}, source, stat
                )
            if saved is not None:
                await cleanup.enter_async_context(processing._processing_limiter())
            backend = _local_backend(spec)
            await processing._process_video(
                source,
                source_id=source.source_id,
                chunk_duration=spec.chunk_duration,
                prompt=spec.prompt,
                model=spec.model,
                backend=spec.backend,
                decoder=spec.decoder,
                generation_config=spec.generation,
                backend_instance=backend,
                checkpoint=run,
            )
            results = await run.complete()
        elif spec.local:
            await cleanup.enter_async_context(processing._processing_limiter())
            if issubclass(spec.factory, OpenAICompatibleVLMBackend):
                instance = _local_backend(spec)
            else:
                instance = cast(type[VLMBackend], spec.factory)()
            results = await processing._process_video(
                source,
                source_id=source.source_id,
                chunk_duration=spec.chunk_duration,
                prompt=spec.prompt,
                model=spec.model,
                backend=spec.backend,
                decoder=spec.decoder,
                generation_config=spec.generation,
                backend_instance=instance,
            )
        else:
            from .backend import RTEmbedBackend, RTVIBackend
            from .rtvi import _RTVIEmbedTransport, _RTVITransport

            if spec.factory in {RTVIBackend, RTEmbedBackend}:
                transport_type = (
                    _RTVITransport
                    if spec.factory is RTVIBackend
                    else _RTVIEmbedTransport
                )
                transport = transport_type(
                    base_url=spec.endpoint, api_key=spec.api_key, model=spec.model
                )
                transport._model = spec.model
                transport._headers = (
                    {"Authorization": f"Bearer {spec.api_key}"} if spec.api_key else {}
                )
                try:
                    async with cast(Any, spec.factory)(transport=transport) as backend:
                        results = await backend.ingest(
                            source,
                            prompt=spec.prompt,
                            model=spec.model,
                            chunk_duration=spec.chunk_duration,
                            chunk_overlap_duration=spec.overlap,
                            generation_config=spec.generation,
                        )
                finally:
                    await transport.aclose()
            else:
                async with cast(type[IngestionBackend], spec.factory)() as backend:
                    results = cast(
                        list[VlmChunkResponse],
                        await backend.ingest(
                            source,
                            prompt=spec.prompt,
                            model=spec.model,
                            chunk_duration=spec.chunk_duration,
                            chunk_overlap_duration=spec.overlap,
                            generation_config=spec.generation,
                        ),
                    )
        if any(response.error for response in results):
            error_type = VLMBackendError if spec.local else IngestionBackendError
            raise error_type("Provider reported an unsuccessful caption response")
        await ObservationStore(storage).publish(
            job_id, results, original_source_id=original_source_id
        )
        return results
