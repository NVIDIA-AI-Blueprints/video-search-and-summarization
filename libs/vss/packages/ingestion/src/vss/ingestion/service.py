# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Public ingestion entry points."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import AsyncExitStack
from dataclasses import replace
from typing import Any, Literal, overload
from uuid import UUID, uuid4

from vss.storage import StorageAdapter, StorageConfig
from vss.video_processing import DecodeOptions
from vss.video_processing.shared_decode import (
    FrameBuffer,
    SharedDecodeCoordinator,
    SharedFrameCapability,
    shared_capabilities_match,
)

from .backend import (
    IngestionBackend,
    available_backends,
    get_backend,
    is_vlm_backend,
)
from .contracts import (
    EmbeddingCompletionResponse,
    IngestionResponse,
    RequestSource,
    VlmCaptionsCompletionResponse,
)
from .exceptions import IngestionBackendError, VLMBackendError
from .queue import IngestionJob, IngestionQueue, get_default_ingestion_queue
from .source_identity import SourceIdentityProvider

DEFAULT_INGESTION_PROMPT = (
    "Describe the important visible events with their start time, end time, "
    "type, and description."
)
_BACKEND_ALIASES = {
    "rt_embed": "rtvi_embed",
    "rtvi-embed": "rtvi_embed",
    "rt-cv": "rtvi_cv",
    "rtvi-cv": "rtvi_cv",
}
_LOGGER = logging.getLogger(__name__)


def _canonical_backend(name: str) -> str:
    return _BACKEND_ALIASES.get(name, name)


def _normalize_backend(name: str, registered: set[str]) -> str:
    """Normalize builtins and aliases without changing exact custom names."""
    stripped = name.strip()
    if stripped in registered:
        return stripped
    lowered = stripped.lower()
    canonical = _canonical_backend(lowered)
    return lowered if canonical in registered else stripped


@overload
def ingest(
    source: RequestSource,
    *,
    backends: list[Literal["vlm", "cpu_vlm", "gpu_vlm", "rtvi"]],
    prompt: str = DEFAULT_INGESTION_PROMPT,
    model: str | None = None,
    decoder: str | None = None,
    single_decode: bool = False,
    cuda_ipc: bool | None = None,
    chunk_duration: int = 60,
    chunk_overlap_duration: int = 0,
    generation_config: dict[str, Any] | None = None,
    queue: IngestionQueue | None = None,
    storage: StorageAdapter | None = None,
    storage_config: StorageConfig | None = None,
    job_id: UUID | None = None,
    resume: bool = False,
    source_revision: str | None = None,
    pipeline_revision: str | None = None,
    source_identity_provider: SourceIdentityProvider | None = None,
) -> IngestionJob[dict[str, VlmCaptionsCompletionResponse]]: ...


@overload
def ingest(
    source: RequestSource,
    *,
    backends: list[Literal["rt_embed", "rtvi_embed", "rtvi-embed"]],
    prompt: str = DEFAULT_INGESTION_PROMPT,
    model: str | None = None,
    decoder: str | None = None,
    single_decode: bool = False,
    cuda_ipc: bool | None = None,
    chunk_duration: int = 60,
    chunk_overlap_duration: int = 0,
    generation_config: dict[str, Any] | None = None,
    queue: IngestionQueue | None = None,
    storage: StorageAdapter | None = None,
    storage_config: StorageConfig | None = None,
    job_id: UUID | None = None,
    resume: bool = False,
    source_revision: str | None = None,
    pipeline_revision: str | None = None,
    source_identity_provider: SourceIdentityProvider | None = None,
) -> IngestionJob[dict[str, EmbeddingCompletionResponse]]: ...


@overload
def ingest(
    source: RequestSource,
    *,
    backends: list[str] | None = None,
    prompt: str = DEFAULT_INGESTION_PROMPT,
    model: str | None = None,
    decoder: str | None = None,
    single_decode: bool = False,
    cuda_ipc: bool | None = None,
    chunk_duration: int = 60,
    chunk_overlap_duration: int = 0,
    generation_config: dict[str, Any] | None = None,
    queue: IngestionQueue | None = None,
    storage: StorageAdapter | None = None,
    storage_config: StorageConfig | None = None,
    job_id: UUID | None = None,
    resume: bool = False,
    source_revision: str | None = None,
    pipeline_revision: str | None = None,
    source_identity_provider: SourceIdentityProvider | None = None,
) -> IngestionJob[dict[str, IngestionResponse]]: ...


def ingest(
    source: RequestSource,
    *,
    backends: list[Any] | None = None,
    prompt: str = DEFAULT_INGESTION_PROMPT,
    model: str | None = None,
    decoder: str | None = None,
    single_decode: bool = False,
    cuda_ipc: bool | None = None,
    chunk_duration: int = 60,
    chunk_overlap_duration: int = 0,
    generation_config: dict[str, Any] | None = None,
    queue: IngestionQueue | None = None,
    storage: StorageAdapter | None = None,
    storage_config: StorageConfig | None = None,
    job_id: UUID | None = None,
    resume: bool = False,
    source_revision: str | None = None,
    pipeline_revision: str | None = None,
    source_identity_provider: SourceIdentityProvider | None = None,
) -> IngestionJob[Any]:
    """Enqueue video ingestion; await the job for backend-keyed responses.

    Ordinary ingestion uses RTVI, a registered whole-request backend, or one or
    more registered frame-level VLM backends. ``decoder`` selects local decode.
    Set ``single_decode=True`` with an explicit decoder to fan one decode stream
    out to compatible backends; otherwise each backend runs its normal path.
    Set ``cuda_ipc=True`` or ``False`` to select the shared-frame transport for
    this request. When omitted, ``VSS_SHARED_FRAME_TRANSPORT`` remains the
    compatibility default.
    Checkpointed VLM work delegates decode/inference to vss.video_processing.
    Chunk durations/overlap use seconds. Storage is opt-in through an injected
    adapter, config, or VSS_STORAGE_BACKEND/ROOT. An injected adapter stays
    caller-owned; config-created adapters close after worker cleanup. Preserve
    job.id and source.source_id across restarts. File checkpoint mode requires
    explicit model, concrete decoder and pipeline_revision for immutable local
    media; resume=True also requires a UUID job_id and a compatible checkpoint.
    By default, each checkpointed attempt hashes the entire local source.
    Optional source_identity_provider must implement async resolve(source) and
    promise exact immutable bytes; it skips hashing and remains caller-owned.
    Provider use requires persistent local checkpoint mode. Invalid providers
    fail before queueing; invalid results or provider failures surface on await.
    Whole-request RTVI supports completed-result storage but not resume. Existing
    calls do not write files. Invalid selection raises ValueError before queueing;
    storage/inference failures are raised by awaiting the job. After cancellation
    await job.wait_closed() before closing injected storage or restarting its ID.
    """
    from vss.storage import StorageConfigurationError

    from .persistence import environment_storage_config

    selected_backends = _select_backends(backends)
    if not isinstance(single_decode, bool):
        raise TypeError("single_decode must be a boolean")
    if cuda_ipc is not None and not isinstance(cuda_ipc, bool):
        raise TypeError("cuda_ipc must be a boolean or None")
    if single_decode and (not isinstance(decoder, str) or not decoder.strip()):
        raise ValueError("single_decode requires an explicit decoder")
    if cuda_ipc is True and not single_decode:
        raise ValueError("cuda_ipc=True requires single_decode=True")
    if storage is not None and storage_config is not None:
        raise StorageConfigurationError("Supply storage or storage_config, not both")
    selected_config = (
        storage_config
        if storage_config is not None
        else (environment_storage_config() if storage is None else None)
    )
    if not isinstance(resume, bool):
        raise StorageConfigurationError("resume must be a boolean")
    if resume and (job_id is None or (storage is None and selected_config is None)):
        raise StorageConfigurationError(
            "Resume requires explicit job_id and persistent storage"
        )
    if (
        source_identity_provider is not None
        and storage is None
        and selected_config is None
    ):
        raise StorageConfigurationError(
            "A source identity provider requires persistent checkpoint storage"
        )
    if storage is not None or selected_config is not None:
        if single_decode:
            raise StorageConfigurationError(
                "single_decode is only supported for ordinary ingestion"
            )
        if len(selected_backends) != 1:
            raise StorageConfigurationError(
                "Persistent ingestion requires exactly one backend"
            )
        # Freeze only the new persistence path; legacy adapters still receive
        # the exact caller-owned RequestSource object as their contract promises.
        source = source.model_copy(deep=True)
        from ._persistent import capture_spec, validate_checkpoint_options

        spec = capture_spec(
            _canonical_backend(selected_backends[0]),
            prompt=prompt,
            model=model,
            decoder=decoder,
            chunk_duration=chunk_duration,
            chunk_overlap_duration=chunk_overlap_duration,
            generation_config=generation_config,
        )
        persistent = (
            storage.persistent
            if storage is not None
            else selected_config is not None
            and selected_config.backend in {"file", "elasticsearch"}
        )
        validate_checkpoint_options(
            spec,
            resume=resume,
            persistent=persistent,
            explicit_model=model,
            pipeline_revision=pipeline_revision,
        )
        if source_identity_provider is not None:
            from ._persistent import validate_source_identity_provider

            validate_source_identity_provider(
                source_identity_provider,
                source,
                checkpointed=bool(persistent and spec.local),
            )
        identity = job_id if job_id is not None else uuid4()

        async def persistent_job() -> dict[str, IngestionResponse]:
            from vss.storage import StorageError, create_storage

            from ._persistent import execute

            if storage is not None:
                selected = storage
            else:
                assert selected_config is not None
                selected = create_storage(selected_config)
            try:
                return {
                    selected_backends[0]: await execute(
                        source,
                        spec,
                        selected,
                        str(identity),
                        resume=resume,
                        pipeline_revision=pipeline_revision,
                        source_revision=source_revision,
                        source_identity_provider=source_identity_provider,
                    )
                }
            except (IngestionBackendError, VLMBackendError) as error:
                # Provider error bodies may echo credentials; durable-mode errors
                # deliberately carry actionable categories instead of raw bodies.
                raise type(error)(
                    "Inference failed; check the provider and retry unconfirmed work"
                ) from None
            except StorageError:
                raise
            finally:
                if storage is None:
                    await selected.aclose()

        selected_queue = queue if queue is not None else get_default_ingestion_queue()
        return selected_queue.submit(persistent_job, job_id=identity)

    selected_queue = queue if queue is not None else get_default_ingestion_queue()
    shared_buffer_depth = _shared_decode_buffer_depth() if single_decode else 8
    return selected_queue.submit(
        lambda: _execute_ingestion(
            source,
            selected_backends,
            prompt=prompt,
            model=model,
            chunk_duration=chunk_duration,
            chunk_overlap_duration=chunk_overlap_duration,
            generation_config=generation_config,
            decoder=decoder,
            single_decode=single_decode,
            cuda_ipc=cuda_ipc,
            shared_buffer_depth=shared_buffer_depth,
        ),
        job_id=job_id,
    )


async def _execute_ingestion(
    source: RequestSource,
    backends: list[str],
    *,
    prompt: str,
    model: str | None,
    chunk_duration: int,
    chunk_overlap_duration: int,
    generation_config: dict[str, Any] | None,
    decoder: str | None = None,
    single_decode: bool = False,
    cuda_ipc: bool | None = None,
    shared_buffer_depth: int = 8,
) -> dict[str, IngestionResponse]:
    """Execute one queued request through all selected backends."""

    stack = AsyncExitStack()
    primary_error: BaseException | None = None
    try:
        instances = [
            await stack.enter_async_context(
                _get_backend(_canonical_backend(name), decoder)
            )
            for name in backends
        ]
        selected_transport = (
            None if cuda_ipc is None else ("cuda_ipc" if cuda_ipc else "owned_cpu")
        )
        capabilities = (
            [
                _shared_capability(
                    backend,
                    selected_transport,
                    requested_decoder=decoder,
                )
                for backend in instances
            ]
            if single_decode
            else []
        )
        common = shared_capabilities_match(capabilities) if single_decode else None
        if common is not None:
            _LOGGER.info(
                "ingestion decode path selected",
                extra={
                    "decode_mode": f"shared_{common.transport}",
                    "decoder": common.decoder,
                    "backend_count": len(backends),
                },
            )
            return await _execute_shared_ingestion(
                source,
                backends,
                instances,
                common,
                prompt=prompt,
                model=model,
                chunk_duration=chunk_duration,
                chunk_overlap_duration=chunk_overlap_duration,
                generation_config=generation_config,
                buffer_depth=min(
                    shared_buffer_depth,
                    *(
                        capability.max_buffer_depth
                        for capability in capabilities
                        if capability is not None
                    ),
                ),
            )
        if single_decode:
            raise IngestionBackendError(
                "selected backends do not support a compatible shared-frame transport"
            )
        _LOGGER.info(
            "ingestion decode path selected",
            extra={
                "decode_mode": "independent",
                "single_decode": single_decode,
                "backend_count": len(backends),
            },
        )
        return await _execute_independent_ingestion(
            source,
            backends,
            instances,
            prompt=prompt,
            model=model,
            chunk_duration=chunk_duration,
            chunk_overlap_duration=chunk_overlap_duration,
            generation_config=generation_config,
        )
    except BaseException as error:
        primary_error = error
        raise
    finally:
        if primary_error is None:
            await stack.aclose()
        else:
            try:
                await stack.aclose()
            except BaseException:
                _LOGGER.exception(
                    "backend cleanup failed after ingestion failure",
                    extra={"decode_mode": "cleanup"},
                )


async def _execute_independent_ingestion(
    source: RequestSource,
    requested_names: list[str],
    backends: list[IngestionBackend],
    *,
    prompt: str,
    model: str | None,
    chunk_duration: int,
    chunk_overlap_duration: int,
    generation_config: dict[str, Any] | None,
) -> dict[str, IngestionResponse]:
    async def execute_one(
        requested_name: str,
        backend: IngestionBackend,
    ) -> tuple[str, IngestionResponse]:
        return requested_name, await backend.ingest(
            source,
            prompt=prompt,
            model=model,
            chunk_duration=chunk_duration,
            chunk_overlap_duration=chunk_overlap_duration,
            generation_config=generation_config,
        )

    tasks = [
        asyncio.create_task(execute_one(name, backend))
        for name, backend in zip(requested_names, backends, strict=True)
    ]
    try:
        return dict(await asyncio.gather(*tasks))
    except BaseException:
        await _cancel_and_settle(tasks)
        raise


async def _execute_shared_ingestion(
    source: RequestSource,
    requested_names: list[str],
    backends: list[IngestionBackend],
    capability: SharedFrameCapability,
    *,
    prompt: str,
    model: str | None,
    chunk_duration: int,
    chunk_overlap_duration: int,
    generation_config: dict[str, Any] | None,
    buffer_depth: int,
) -> dict[str, IngestionResponse]:
    from .vlm import _processing_limiter

    async with _processing_limiter():
        if capability.transport == "cuda_ipc":
            return await _run_cuda_ipc_ingestion(
                source,
                requested_names,
                backends,
                capability,
                prompt=prompt,
                model=model,
                chunk_duration=chunk_duration,
                chunk_overlap_duration=chunk_overlap_duration,
                generation_config=generation_config,
                buffer_depth=buffer_depth,
            )
        return await _run_shared_ingestion(
            source,
            requested_names,
            backends,
            capability,
            prompt=prompt,
            model=model,
            chunk_duration=chunk_duration,
            chunk_overlap_duration=chunk_overlap_duration,
            generation_config=generation_config,
            buffer_depth=buffer_depth,
        )


async def _run_shared_ingestion(
    source: RequestSource,
    requested_names: list[str],
    backends: list[IngestionBackend],
    capability: SharedFrameCapability,
    *,
    prompt: str,
    model: str | None,
    chunk_duration: int,
    chunk_overlap_duration: int,
    generation_config: dict[str, Any] | None,
    buffer_depth: int,
) -> dict[str, IngestionResponse]:
    coordinator = SharedDecodeCoordinator(
        source,
        source_id=source.source_id,
        options=DecodeOptions(
            decoder=capability.decoder,
            device_id=capability.device_id or 0,
        ),
        buffer_depth=buffer_depth,
    )
    buffers: list[FrameBuffer] = []
    for index, requested_name in enumerate(requested_names):
        buffers.append(await coordinator.register(f"{index}:{requested_name}"))

    async def consume(
        requested_name: str,
        backend: IngestionBackend,
        buffer: FrameBuffer,
    ) -> tuple[str, IngestionResponse]:
        try:
            return requested_name, await backend.ingest_shared(
                source,
                buffer,
                prompt=prompt,
                model=model,
                chunk_duration=chunk_duration,
                chunk_overlap_duration=chunk_overlap_duration,
                generation_config=generation_config,
            )
        finally:
            await buffer.aclose()

    consumer_tasks = [
        asyncio.create_task(consume(name, backend, buffer))
        for name, backend, buffer in zip(
            requested_names,
            backends,
            buffers,
            strict=True,
        )
    ]
    decode_task = asyncio.create_task(coordinator.run())
    tasks: list[asyncio.Task[Any]] = [*consumer_tasks, decode_task]
    primary_error: BaseException | None = None
    try:
        await asyncio.gather(*tasks)
        return dict(task.result() for task in consumer_tasks)
    except BaseException as error:
        primary_error = error
        await _cancel_and_settle(tasks)
        raise
    finally:
        try:
            await coordinator.aclose()
        except BaseException:
            if primary_error is None:
                raise
            _LOGGER.exception(
                "shared decode cleanup failed after ingestion failure",
                extra={"decode_mode": "shared_owned_cpu"},
            )


async def _run_cuda_ipc_ingestion(
    source: RequestSource,
    requested_names: list[str],
    backends: list[IngestionBackend],
    capability: SharedFrameCapability,
    *,
    prompt: str,
    model: str | None,
    chunk_duration: int,
    chunk_overlap_duration: int,
    generation_config: dict[str, Any] | None,
    buffer_depth: int,
) -> dict[str, IngestionResponse]:
    from pathlib import Path

    from vss.video_processing.cuda_ipc import CudaIpcDecodeSession

    socket_root = Path(os.getenv("VSS_CUDA_IPC_SOCKET_DIR", "/run/vss/frame-broker"))
    socket_path = socket_root / f"{uuid4().hex}.sock"
    session = CudaIpcDecodeSession(
        source,
        source_id=source.source_id,
        socket_path=socket_path,
        device_id=capability.device_id or 0,
        slot_count=buffer_depth + 1,
    )
    await session.prepare()
    ready_events = [asyncio.Event() for _ in backends]

    async def consume(
        index: int,
        requested_name: str,
        backend: IngestionBackend,
    ) -> tuple[str, IngestionResponse]:
        frame_source = session.frame_source(f"{index}:{requested_name}")
        return requested_name, await backend.ingest_cuda_ipc(
            source,
            frame_source,
            ready_events[index],
            prompt=prompt,
            model=model,
            chunk_duration=chunk_duration,
            chunk_overlap_duration=chunk_overlap_duration,
            generation_config=generation_config,
        )

    consumer_tasks = [
        asyncio.create_task(consume(index, name, backend))
        for index, (name, backend) in enumerate(
            zip(requested_names, backends, strict=True)
        )
    ]

    async def wait_until_ready() -> None:
        await asyncio.gather(*(event.wait() for event in ready_events))

    readiness = asyncio.create_task(wait_until_ready())
    primary_error: BaseException | None = None
    decode_task: asyncio.Task[None] | None = None
    try:
        done, _ = await asyncio.wait(
            [readiness, *consumer_tasks],
            return_when=asyncio.FIRST_COMPLETED,
        )
        early_consumer = next((task for task in done if task in consumer_tasks), None)
        if early_consumer is not None:
            await early_consumer
            raise IngestionBackendError(
                "CUDA IPC backend completed before attaching to the broker"
            )
        await readiness
        decode_task = asyncio.create_task(session.run())
        await asyncio.gather(decode_task, *consumer_tasks)
        return dict(task.result() for task in consumer_tasks)
    except BaseException as error:
        primary_error = error
        session.stop()
        await _cancel_and_settle(
            [*consumer_tasks, readiness]
            + ([] if decode_task is None else [decode_task])
        )
        raise
    finally:
        try:
            await session.aclose(failed=primary_error is not None)
        except BaseException:
            if primary_error is None:
                raise
            _LOGGER.exception(
                "CUDA IPC decode cleanup failed after ingestion failure",
                extra={"decode_mode": "shared_cuda_ipc"},
            )


async def _cancel_and_settle(tasks: list[asyncio.Task[Any]]) -> None:
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def _shared_capability(
    backend: object,
    transport: Literal["owned_cpu", "cuda_ipc"] | None = None,
    *,
    requested_decoder: str | None = None,
) -> SharedFrameCapability | None:
    if transport is not None:
        selector = getattr(backend, "shared_frame_capability_for_transport", None)
        if selector is not None:
            capability = selector(transport)
            if isinstance(capability, SharedFrameCapability):
                return _capability_for_decoder(capability, requested_decoder)
            return None
    capability_getter = getattr(backend, "shared_frame_capability", None)
    if capability_getter is None:
        return None
    capability = capability_getter()
    if not isinstance(capability, SharedFrameCapability):
        return None
    if transport is not None and capability.transport != transport:
        return None
    return _capability_for_decoder(capability, requested_decoder)


def _capability_for_decoder(
    capability: SharedFrameCapability,
    requested_decoder: str | None,
) -> SharedFrameCapability | None:
    if requested_decoder is None or capability.decoder == requested_decoder:
        return capability
    if capability.transport == "owned_cpu" and capability.decoder_agnostic:
        # Owned RGB consumers are decoder-agnostic. The request's decoder owns
        # production, while the capability describes the consumer contract.
        return replace(capability, decoder=requested_decoder)
    return None


def _get_backend(name: str, decoder: str | None) -> IngestionBackend:
    if is_vlm_backend(name) and decoder is not None:
        return get_backend(name, decoder=decoder)
    return get_backend(name)


def _shared_decode_buffer_depth() -> int:
    depth_text = os.getenv("VSS_SHARED_DECODE_BUFFER_DEPTH", "8").strip()
    try:
        depth = int(depth_text)
    except ValueError:
        raise ValueError(
            "VSS_SHARED_DECODE_BUFFER_DEPTH must be a positive integer"
        ) from None
    if depth < 1:
        raise ValueError("VSS_SHARED_DECODE_BUFFER_DEPTH must be a positive integer")
    return depth


def _select_backends(backends: list[str] | None) -> list[str]:
    registered = set(available_backends())
    if backends is None:
        configured = os.getenv("VSS_INGESTION_BACKEND", "rtvi").strip() or "rtvi"
        selected = [
            _normalize_backend(name, registered) for name in configured.split(",")
        ]
    else:
        if not isinstance(backends, list):
            raise TypeError("backends must be a list of backend names")
        if any(not isinstance(name, str) for name in backends):
            raise TypeError("backends must contain only strings")
        selected = [_normalize_backend(name, registered) for name in backends]
    if not selected or any(not name for name in selected):
        raise ValueError("backends must contain at least one non-empty backend name")
    canonical = [_canonical_backend(name) for name in selected]
    unknown = [
        name
        for name, canonical_name in zip(selected, canonical, strict=True)
        if canonical_name not in registered
    ]
    if unknown:
        raise ValueError(
            "backends contain unknown selections: " + ", ".join(sorted(set(unknown)))
        )
    if len(set(canonical)) != len(canonical):
        raise ValueError("backends must not contain duplicate backend selections")
    return selected


def ingest_sync(
    source: RequestSource,
    *,
    backends: list[str] | None = None,
    prompt: str = DEFAULT_INGESTION_PROMPT,
    model: str | None = None,
    decoder: str | None = None,
    single_decode: bool = False,
    chunk_duration: int = 60,
    chunk_overlap_duration: int = 0,
    generation_config: dict[str, Any] | None = None,
    storage: StorageAdapter | None = None,
    storage_config: StorageConfig | None = None,
    job_id: UUID | None = None,
    resume: bool = False,
    source_revision: str | None = None,
    pipeline_revision: str | None = None,
    source_identity_provider: SourceIdentityProvider | None = None,
) -> dict[str, IngestionResponse]:
    """Synchronous wrapper for :func:`ingest`."""

    try:
        asyncio.get_running_loop()
    except RuntimeError:

        async def run() -> dict[str, IngestionResponse]:
            return await ingest(
                source,
                backends=backends,
                prompt=prompt,
                model=model,
                decoder=decoder,
                single_decode=single_decode,
                chunk_duration=chunk_duration,
                chunk_overlap_duration=chunk_overlap_duration,
                generation_config=generation_config,
                storage=storage,
                storage_config=storage_config,
                job_id=job_id,
                resume=resume,
                source_revision=source_revision,
                pipeline_revision=pipeline_revision,
                source_identity_provider=source_identity_provider,
            )

        return asyncio.run(run())
    raise RuntimeError(
        "ingest_sync cannot run inside an active event loop; await ingest instead"
    )
