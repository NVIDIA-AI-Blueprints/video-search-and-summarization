# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Serialized decoder execution, async lifetime, selection, and cancellation."""

from __future__ import annotations

import asyncio
import concurrent.futures
import math
import weakref
from collections.abc import AsyncGenerator, Callable
from contextlib import aclosing
from dataclasses import dataclass
from typing import Any

import numpy as np

from .contracts import (
    DecodeOptions,
    DecodePlan,
    MediaFacts,
    SamplingPolicy,
    SourceInput,
    TimeWindow,
)
from .decoders import registry
from .exceptions import (
    ClipError,
    DecodeError,
    DecoderUnavailableError,
    ProcessingError,
    TimestampUnavailableError,
    UnsupportedSourceError,
)
from .frames import Frame
from .sources import ResolvedSource, resolve_source

_LIMITERS: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, weakref.WeakValueDictionary[int, asyncio.Semaphore]
] = weakref.WeakKeyDictionary()


def _limiter(loop: asyncio.AbstractEventLoop, value: int) -> asyncio.Semaphore:
    groups = _LIMITERS.get(loop)
    if groups is None:
        groups = weakref.WeakValueDictionary()
        _LIMITERS[loop] = groups
    permit = groups.get(value)
    if permit is None:
        permit = asyncio.Semaphore(value)
        groups[value] = permit
    return permit


@dataclass(frozen=True, slots=True)
class _Failure:
    """Only inert diagnostic data crosses a failed native call boundary."""

    category: str
    message: str


_ERROR_TYPES = (
    TimestampUnavailableError,
    DecoderUnavailableError,
    UnsupportedSourceError,
    ClipError,
    DecodeError,
    ProcessingError,
)


def _detach_errors(error: BaseException) -> None:
    pending = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        for linked in (current.__cause__, current.__context__):
            if linked is not None:
                pending.append(linked)
        current.__traceback__ = None
        current.__cause__ = None
        current.__context__ = None


def _invoke(fn: Callable[..., Any], *args: Any) -> Any:
    """Destroy native traceback locals on the session's worker, before return."""
    try:
        return fn(*args)
    except BaseException as error:
        category = "DecodeError"
        message = "Native decoder operation failed; verify the source and installation"
        if isinstance(error, asyncio.CancelledError):
            category = "CancelledError"
            message = "Native decoder operation was cancelled"
        else:
            for error_type in _ERROR_TYPES:
                if isinstance(error, error_type):
                    category = error_type.__name__
                    try:
                        # A str subclass can carry native objects as attributes.
                        message = str.__str__(str(error))
                    except BaseException as formatting_error:
                        _detach_errors(formatting_error)
                    break
        _detach_errors(error)
        failure = _Failure(category, message)
    # Raising here would put a new exception/traceback cycle in the Future.
    return failure


def _unwrap(value: Any) -> Any:
    if isinstance(value, _Failure):
        if value.category == "CancelledError":
            raise asyncio.CancelledError(value.message)
        for error_type in _ERROR_TYPES:
            if value.category == error_type.__name__:
                raise error_type(value.message)
        raise DecodeError(value.message)
    return value


class _Worker:
    """One executor thread owns a backend and all calls to its session."""

    def __init__(self) -> None:
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)

    def submit(self, fn: Callable[..., Any], *args: Any) -> asyncio.Future[Any]:
        loop = asyncio.get_running_loop()
        return asyncio.wrap_future(self.executor.submit(_invoke, fn, *args), loop=loop)

    async def close_executor(self) -> bool:
        shutdown = asyncio.create_task(
            asyncio.to_thread(self.executor.shutdown, True, cancel_futures=False)
        )
        _, cancelled = await _settle(shutdown)
        return cancelled


async def _settle(future: asyncio.Future[Any]) -> tuple[Any, bool]:
    """Wait out repeated cancellation without cancelling a native thread call."""
    cancelled = False
    while True:
        try:
            return await asyncio.shield(future), cancelled
        except asyncio.CancelledError:
            cancelled = True
            if future.done():
                try:
                    return future.result(), True
                except BaseException:
                    raise


async def _native(worker: _Worker, fn: Callable[..., Any], *args: Any) -> Any:
    result, cancelled = await _settle(worker.submit(fn, *args))
    if cancelled:
        raise asyncio.CancelledError
    return _unwrap(result)


@dataclass(slots=True)
class _Opened:
    source: ResolvedSource
    options: DecodeOptions
    backend_name: str
    backend: Any
    session: Any
    worker: _Worker
    facts: MediaFacts
    fallback_reason: str | None
    limiter: asyncio.Semaphore


def _backend_order(name: str) -> list[str]:
    if name != "auto":
        if name not in registry._DECODER_REGISTRY:
            raise DecoderUnavailableError(
                f"Unknown decoder {name!r}; register it or choose a built-in"
            )
        return [name]
    return [
        candidate
        for candidate in ("pynv", "opencv")
        if candidate in registry._DECODER_REGISTRY
    ]


async def _open(source: ResolvedSource, options: DecodeOptions) -> _Opened:
    loop = asyncio.get_running_loop()
    permit = _limiter(loop, options.max_concurrency)
    await permit.acquire()
    errors: list[str] = []
    try:
        names = _backend_order(options.decoder)
        if not names:
            raise DecoderUnavailableError("No decoder backends are registered")
        for position, name in enumerate(names):
            worker = _Worker()

            def make_open(backend_name: str = name) -> tuple[Any, Any]:
                factory = registry._DECODER_REGISTRY[backend_name]
                backend = factory()
                session = backend.open(source.path, options)
                return backend, session

            try:
                future = worker.submit(make_open)
                result, cancelled = await _settle(future)
                if cancelled:
                    if not isinstance(result, _Failure):
                        _, session = result
                        # Cancellation wins over a simultaneous native failure.
                        # A successful open still requires worker-owned cleanup.
                        await _settle(worker.submit(session.close))
                    raise asyncio.CancelledError
                backend, session = _unwrap(result)
            except DecoderUnavailableError as exc:
                errors.append(f"{name}: {exc}")
                await worker.close_executor()
                if options.decoder != "auto" or position == len(names) - 1:
                    remedies = (
                        "Install a compatible PyNvVideoCodec >=2.2 or "
                        "OpenCV >=4.13 decoder."
                    )
                    raise DecoderUnavailableError(
                        f"{'; '.join(errors)}. {remedies}"
                    ) from exc
                continue
            except BaseException:
                await worker.close_executor()
                raise
            # Probe is deliberately outside the fallback boundary. Only open()
            # availability failures may switch backends; probe/read failures
            # from a successfully initialized backend must remain visible.
            try:
                facts = await _native(worker, session.probe)
            except BaseException as primary_error:
                try:
                    close_future = worker.submit(session.close)
                    await _settle(close_future)
                except BaseException:
                    pass
                try:
                    await worker.close_executor()
                except BaseException:
                    pass
                raise primary_error
            return _Opened(
                source,
                options,
                name,
                backend,
                session,
                worker,
                facts.model_copy(update={"source_id": source.source_id}),
                "; ".join(errors) or None,
                permit,
            )
        remedies = "Install a compatible PyNvVideoCodec >=2.2 or OpenCV >=4.13 decoder."
        raise DecoderUnavailableError(
            f"No decoder could open the source: {'; '.join(errors)}. {remedies}"
        )
    except BaseException:
        permit.release()
        raise


async def _close(opened: _Opened, primary_failure: bool = False) -> None:
    error: BaseException | None = None
    cancelled = False
    try:
        future = opened.worker.submit(opened.session.close)
        try:
            close_result, close_cancelled = await _settle(future)
            cancelled = cancelled or close_cancelled
            _unwrap(close_result)
        except asyncio.CancelledError:
            cancelled = True
            if future.done() and not future.cancelled():
                error = future.exception()
        except BaseException as exc:
            error = exc
    finally:
        try:
            try:
                cancelled = (await opened.worker.close_executor()) or cancelled
            except BaseException as exc:
                if error is None:
                    error = exc
        finally:
            opened.limiter.release()
    if error is not None and not primary_failure:
        raise DecodeError("Decoder cleanup failed") from error
    if cancelled and not primary_failure:
        raise asyncio.CancelledError


async def probe_video(
    source: SourceInput,
    *,
    source_id: str | None = None,
    options: DecodeOptions | None = None,
) -> MediaFacts:
    """Probe local video facts through the selected decoder.

    ``source`` is a filesystem path, local ``file://`` URI, or object exposing
    ``url`` and ``source_id``. The result reports coded dimensions, codec tag,
    duration/rate estimates and timing quality where the decoder provides them;
    audio and container facts may remain unknown. The default remains OpenCV
    for compatibility; pass ``DecodeOptions(decoder="auto")`` to select an
    available GPU or CPU decoder. Missing or unusable decoders raise
    :class:`DecoderUnavailableError`; missing files raise ``FileNotFoundError``
    and malformed or remote locators raise ``UnsupportedSourceError``.
    """
    resolved = resolve_source(source, source_id)
    opened = await _open(resolved, options or DecodeOptions(decoder="opencv"))
    try:
        return opened.facts
    finally:
        await _close(opened)


def probe_video_sync(
    source: SourceInput,
    *,
    source_id: str | None = None,
    options: DecodeOptions | None = None,
) -> MediaFacts:
    """Probe facts synchronously when no event loop is running.

    Inputs, output, decoder selection, and errors match
    :func:`probe_video`. Calling this wrapper inside an event loop raises
    ``RuntimeError``; await :func:`probe_video` in async code.
    """
    _require_no_running_loop()
    return asyncio.run(probe_video(source, source_id=source_id, options=options))


def _require_no_running_loop() -> None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    raise RuntimeError(
        "synchronous processing wrappers cannot run inside an event loop"
    )


def decode_frames(
    source: SourceInput,
    plan: DecodePlan,
    *,
    source_id: str | None = None,
    options: DecodeOptions | None = None,
) -> AsyncGenerator[Frame]:
    """Create a lazy async iterator of source-relative RGB frames.

    ``source`` accepts a local path, local ``file://`` URI, or object with
    ``url`` and ``source_id``. ``plan.window`` and sparse targets are integer
    nanoseconds; windows use inclusive start and exclusive end. ``options``
    defaults to ``decoder="auto"`` (usable PyNvVideoCodec then OpenCV), device
    0, batch size 8, best-effort timing, and four active sessions per event
    loop. Creating the iterator opens no decoder. Frames own contiguous,
    read-only CPU ``uint8`` RGB arrays shaped ``(height, width, 3)`` and remain
    valid after native decoder closure. Use ``contextlib.aclosing`` when
    stopping early. Caller-installed OpenCV 4.13+ enables CPU decoding;
    PyNvVideoCodec 2.2+ enables NVIDIA decoding. Missing plugins raise
    :class:`DecoderUnavailableError`, decoder failures raise :class:`DecodeError`,
    and strict timing rejects estimates with :class:`TimestampUnavailableError`.

    Example::

        stream = decode_frames(path, DecodePlan())
        async with aclosing(stream):
            async for frame in stream:
                consume(frame.tensor)
    """
    return _decode_stream(source, plan, source_id=source_id, options=options)


async def _decode_stream(
    source: SourceInput,
    plan: DecodePlan,
    *,
    source_id: str | None,
    options: DecodeOptions | None,
    generated: bool = False,
    deduplicate: bool = False,
    facts_callback: Callable[[MediaFacts], None] | None = None,
) -> AsyncGenerator[Frame]:
    resolved = resolve_source(source, source_id)
    opts = options or DecodeOptions()
    opened = await _open(resolved, opts)
    primary_failure = False
    try:
        facts = opened.facts
        if facts_callback is not None:
            facts_callback(facts)
        origin = facts.timestamp_origin_ns or 0
        window = plan.window
        # Reported duration is metadata, not a reliable EOF boundary. In
        # particular, OpenCV derives it from frame count / FPS, which can
        # underestimate variable-rate media. Only an explicit caller window
        # may stop continuous decoding before the decoder reports native EOF.
        effective_end = window.end_ns
        targets = plan.sample_timestamps_ns
        if targets is not None and facts.duration_s is not None:
            duration_ns = round(facts.duration_s * 1_000_000_000)
            if any(target >= duration_ns for target in targets):
                raise ValueError("sample target is at or beyond known video duration")
        if targets == ():
            return
        seek_target = targets[0] if targets is not None and targets else window.start_ns
        await _native(opened.worker, opened.session.seek, origin + seek_target)
        target_index = 0
        last_raw_time: int | None = None
        last_frame_key: object = object()
        generated_identity = generated
        while True:
            batch = await _native(
                opened.worker, opened.session.read_frames, opts.batch_size
            )
            if not batch:
                if (
                    targets is not None
                    and target_index < len(targets)
                    and not generated_identity
                ):
                    raise DecodeError(
                        "No decodable frame at or after requested time "
                        f"{targets[target_index]} ns"
                    )
                break
            # Copy every member before issuing another native operation. A batch
            # is the only decoder output retained ahead of the consumer.
            snapshots = [
                (
                    np.array(raw.tensor, copy=True, order="C"),
                    raw.timestamp_ns,
                    raw.index,
                    raw.timestamp_quality,
                )
                for raw in batch
            ]
            for pixels, raw_ns, index, quality in snapshots:
                if raw_ns is None:
                    if facts.fps is None or index is None:
                        raise TimestampUnavailableError(
                            "Decoder supplied no PTS and no reliable FPS/index estimate"
                        )
                    raw_ns = origin + round(index * 1_000_000_000 / facts.fps)
                    quality = "estimated"
                if isinstance(raw_ns, bool) or not isinstance(raw_ns, int):
                    raise TimestampUnavailableError(
                        "Decoder timestamps must be integer nanoseconds"
                    )
                timestamp = raw_ns - origin
                if timestamp < 0 or (
                    last_raw_time is not None and timestamp < last_raw_time
                ):
                    raise TimestampUnavailableError(
                        "Presentation timestamps moved backwards or before "
                        "source origin"
                    )
                last_raw_time = timestamp
                if quality not in {"pts", "backend", "estimated"}:
                    quality = "backend"
                if quality == "estimated" and opts.timestamp_mode == "strict":
                    raise TimestampUnavailableError(
                        "Strict timing mode rejects estimated timestamps"
                    )
                if timestamp < window.start_ns:
                    continue
                if effective_end is not None and timestamp >= effective_end:
                    # Do not cross the exclusive source-time end.
                    break

                if targets is None:
                    yield Frame(
                        pixels,
                        resolved.source_id,
                        timestamp,
                        index,
                        quality,
                        opened.backend_name,
                        "gpu"
                        if opened.backend.capability_map().get("device") == "gpu"
                        else "cpu",
                        fallback_reason=opened.fallback_reason,
                    )
                    continue

                while (
                    target_index < len(targets) and timestamp >= targets[target_index]
                ):
                    target = targets[target_index]
                    key: object = index if index is not None else timestamp
                    if not deduplicate or key != last_frame_key:
                        yield Frame(
                            pixels,
                            resolved.source_id,
                            timestamp,
                            index,
                            quality,
                            opened.backend_name,
                            "gpu"
                            if opened.backend.capability_map().get("device") == "gpu"
                            else "cpu",
                            requested_timestamp_ns=target,
                            fallback_reason=opened.fallback_reason,
                        )
                        last_frame_key = key
                    target_index += 1
                    if target_index >= len(targets):
                        break
            if targets is not None and target_index >= len(targets):
                break
            if (
                effective_end is not None
                and last_raw_time is not None
                and last_raw_time >= effective_end
            ):
                break
        if (
            targets is not None
            and target_index < len(targets)
            and not generated_identity
        ):
            raise DecodeError(
                "No decodable frame at or after requested time "
                f"{targets[target_index]} ns"
            )
    except BaseException:
        # Keeping an exception here creates a traceback -> generator frame ->
        # exception cycle that retains entire batches after early aclose().
        # Cleanup only needs to know that a primary failure already exists.
        primary_failure = True
        raise
    finally:
        await _close(opened, primary_failure)


async def extract_frames(
    source: SourceInput,
    timestamps: list[float] | tuple[float, ...],
    *,
    source_id: str | None = None,
    options: DecodeOptions | None = None,
) -> list[Frame]:
    """Select one owned frame at or after each requested timestamp in seconds.

    The source can be a local path, file URI, or structural source object.
    Targets must be finite, nonnegative seconds before known duration. Results
    preserve input order and duplicates, each with separately owned pixels and
    both requested and observed source nanoseconds. A valid target with no
    decodable frame raises ``DecodeError``; invalid targets raise ``ValueError``.
    The returned list scales with the number of requested timestamps.
    OpenCV currently decodes the source prefix in one sorted forward pass to
    avoid inaccurate time seeking on variable-frame-rate media.
    """
    targets = tuple(_seconds_to_ns(value) for value in timestamps)
    if not targets:
        resolved = resolve_source(source, source_id)
        opts = options or DecodeOptions()
        opened = await _open(resolved, opts)
        await _close(opened)
        return []
    order = sorted(enumerate(targets), key=lambda item: item[1])
    sorted_targets = tuple(target for _, target in order)
    selected = [
        frame
        async for frame in _decode_stream(
            source,
            DecodePlan(sample_timestamps_ns=sorted_targets),
            source_id=source_id,
            options=options,
        )
    ]
    if len(selected) != len(targets):
        raise DecodeError(
            "Decoder did not return a frame for every requested timestamp"
        )
    by_original: list[Frame | None] = [None] * len(targets)
    for (original_index, _), frame in zip(order, selected, strict=True):
        by_original[original_index] = frame
    # Reconstruct each result so duplicate requests never share pixel storage.
    output = []
    for frame in by_original:
        assert frame is not None
        output.append(
            Frame(
                frame.tensor,
                frame.source_id,
                frame.timestamp_ns,
                frame.index,
                frame.timestamp_quality,
                frame.decoder,
                frame.device,
                frame.requested_timestamp_ns,
                frame.fallback_reason,
            )
        )
    return output


def _seconds_to_ns(value: float) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ValueError("timestamps in seconds must be finite numbers")
    if value < 0:
        raise ValueError("timestamps must be nonnegative")
    return round(value * 1_000_000_000)


def extract_frames_sync(
    source: SourceInput,
    timestamps: list[float] | tuple[float, ...],
    *,
    source_id: str | None = None,
    options: DecodeOptions | None = None,
) -> list[Frame]:
    """Synchronous sparse extraction wrapper for code without an event loop."""
    _require_no_running_loop()
    return asyncio.run(
        extract_frames(source, timestamps, source_id=source_id, options=options)
    )


async def subsample_frames(
    source: SourceInput,
    policy: SamplingPolicy,
    *,
    window: TimeWindow | None = None,
    source_id: str | None = None,
    options: DecodeOptions | None = None,
) -> AsyncGenerator[Frame]:
    """Lazily emit frames selected by a ``SamplingPolicy``.

    The source can be a local path, file URI, or structural source object.
    ``window`` is a half-open source-nanosecond range. Count sampling requires
    a finite end or known duration; FPS sampling can run online when duration
    is unknown. Explicit timestamps fail if any target has no in-window frame.
    Generated count/FPS grids may return fewer frames when targets coalesce or
    fall after the final decodable frame. Physical frames are emitted once.
    Decoder prerequisites and frame ownership match :func:`decode_frames`.
    """
    opts = options or DecodeOptions()
    effective_window = window or TimeWindow()
    if policy.mode == "all":
        plan = DecodePlan(window=effective_window)
        stream = _decode_stream(source, plan, source_id=source_id, options=opts)
        async with aclosing(stream):
            async for frame in stream:
                yield frame
        return
    if policy.mode == "timestamps":
        plan = DecodePlan(
            window=effective_window, sample_timestamps_ns=policy.timestamps_ns
        )
        stream = _decode_stream(
            source,
            plan,
            source_id=source_id,
            options=opts,
            generated=policy.allow_missing_timestamps,
            deduplicate=True,
        )
        async with aclosing(stream):
            async for frame in stream:
                yield frame
        return

    resolved = resolve_source(source, source_id)
    probe_options = opts.model_copy(update={"decoder": opts.decoder})
    probe_opened = await _open(resolved, probe_options)
    facts = probe_opened.facts
    await _close(probe_opened)
    duration_end = (
        round(facts.duration_s * 1_000_000_000)
        if facts.duration_s is not None
        else None
    )
    end = effective_window.end_ns
    if duration_end is not None:
        end = min(end, duration_end) if end is not None else duration_end
    if policy.mode == "count":
        if end is None:
            raise DecodeError(
                "Count sampling requires a known duration or finite window end"
            )
        count = policy.count
        assert count is not None
        targets = tuple(
            effective_window.start_ns
            + (index * (end - effective_window.start_ns)) // count
            for index in range(count)
        )
    elif end is not None:
        fps = policy.fps
        assert fps is not None
        interval_count = max(
            1,
            math.floor(fps * (end - effective_window.start_ns) / 1_000_000_000),
        )
        targets = tuple(
            effective_window.start_ns
            + (index * (end - effective_window.start_ns)) // interval_count
            for index in range(interval_count)
        )
    else:
        # Online FPS mode is generated from the requested window start and stops
        # naturally at EOF. A finite known duration uses the target grid above.
        opened_plan = DecodePlan(window=effective_window)
        fps = policy.fps
        assert fps is not None
        interval = max(1, round(1_000_000_000 / fps))
        targets = None
        current = effective_window.start_ns
        stream = _decode_stream(
            source,
            opened_plan,
            source_id=source_id,
            options=opts,
        )
        async with aclosing(stream):
            async for frame in stream:
                if current <= frame.timestamp_ns:
                    requested = current
                    while current <= frame.timestamp_ns:
                        current += interval
                    yield Frame(
                        frame.tensor,
                        frame.source_id,
                        frame.timestamp_ns,
                        frame.index,
                        frame.timestamp_quality,
                        frame.decoder,
                        frame.device,
                        requested,
                        frame.fallback_reason,
                    )
        return
    plan = DecodePlan(window=effective_window, sample_timestamps_ns=targets)
    stream = _decode_stream(
        source,
        plan,
        source_id=source_id,
        options=opts,
        generated=True,
        deduplicate=True,
    )
    async with aclosing(stream):
        async for frame in stream:
            yield frame
