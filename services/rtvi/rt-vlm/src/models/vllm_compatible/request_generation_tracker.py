# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Request ownership across the worker thread and the model's asyncio loop."""

import asyncio
import concurrent.futures
import threading
from dataclasses import dataclass, field


async def await_preprocessing(operation):
    """Keep a non-cancellable thread's inputs alive until the thread settles."""
    pending = asyncio.ensure_future(operation)
    try:
        return await asyncio.shield(pending)
    except asyncio.CancelledError:
        while not pending.done():
            try:
                await asyncio.shield(pending)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        # Retrieve a possible failure without replacing the cancellation.
        if not pending.cancelled():
            pending.exception()
        raise


@dataclass(eq=False)
class _Generation:
    parent_id: str
    engine_id: str
    task: asyncio.Task | None = None
    cancel_requested: bool = False
    drained: concurrent.futures.Future = field(default_factory=concurrent.futures.Future)


class RequestGenerationTracker:
    """Keep cancellation intent until the pipeline explicitly releases a parent.

    A concurrent Future becomes cancelled *before* its asyncio coroutine unwinds.
    The separate drained future is only completed after the coroutine's finally
    blocks have run. All task operations run on the model event loop.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._generations: dict[str, dict[str, _Generation]] = {}
        self._cancelled: set[str] = set()
        self._aborts: dict[str, concurrent.futures.Future] = {}

    def register(self, parent_id, engine_id):
        generation = _Generation(parent_id, engine_id)
        with self._lock:
            if parent_id in self._cancelled:
                raise concurrent.futures.CancelledError(f"Request {parent_id} was cancelled")
            self._generations.setdefault(parent_id, {})[engine_id] = generation
        return generation

    def started(self, generation):
        """Called as the first operation in the model coroutine."""
        with self._lock:
            generation.task = asyncio.current_task()
            cancelled = generation.cancel_requested or generation.parent_id in self._cancelled
        if cancelled:
            raise asyncio.CancelledError

    def finished(self, generation, error=None):
        """Called only after the operation and its resource cleanup have ended."""
        with self._lock:
            owned = self._generations.get(generation.parent_id)
            if owned is not None and error is None:
                owned.pop(generation.engine_id, None)
                if not owned:
                    self._generations.pop(generation.parent_id, None)
        if error is None:
            generation.drained.set_result(None)
        else:
            generation.drained.set_exception(error)

    def submit(self, parent_id, engine_id, factory, loop, cleanup=None):
        """Submit without exposing the actual task's lifetime as a cancelled Future."""
        result = concurrent.futures.Future()
        try:
            generation = self.register(parent_id, engine_id)
        except concurrent.futures.CancelledError:
            if cleanup is not None:
                cleanup()
            result.cancel()
            return result

        async def run():
            value = error = None
            cleanup_error = None
            cancelled = False
            try:
                self.started(generation)
                value = await factory()
            except asyncio.CancelledError:
                cancelled = True
            except BaseException as exc:
                error = exc
            finally:
                try:
                    if cleanup is not None:
                        cleanup()
                except BaseException as exc:
                    error = cleanup_error = exc
                finally:
                    with self._lock:
                        aborting = generation.cancel_requested or parent_id in self._cancelled
                    # An engine abort can fail inside factory()'s cancellation
                    # finally block. Retain that failure as undrained too, not
                    # only failures in the wrapper's own cleanup callback.
                    self.finished(generation, cleanup_error or (error if aborting else None))
            if cancelled:
                result.cancel()
            else:
                try:
                    if error is not None:
                        result.set_exception(error)
                    else:
                        result.set_result(value)
                except concurrent.futures.InvalidStateError:
                    # A worker/client may cancel its public future concurrently
                    # with a successful completion. Cleanup has already run.
                    pass

        def cancel_if_requested(future):
            if not future.cancelled():
                return
            with self._lock:
                generation.cancel_requested = True
            loop.call_soon_threadsafe(self._cancel_task, generation)

        result.add_done_callback(cancel_if_requested)
        # Scheduling a callback (not cancelling run_coroutine_threadsafe's
        # Future) guarantees that run() starts and executes its finally block.
        try:
            loop.call_soon_threadsafe(lambda: loop.create_task(run()))
        except BaseException:
            try:
                if cleanup is not None:
                    cleanup()
            finally:
                self.finished(generation)
            raise
        return result

    @staticmethod
    def _cancel_task(generation):
        task = generation.task
        if task is not None and not task.done() and not task.cancelling():
            task.cancel()

    def abort(self, parent_id, loop, abort_engine, timeout_sec=30.0):
        """Abort only this parent's engine IDs; timeout never means drained."""
        if timeout_sec <= 0:
            raise ValueError("timeout_sec must be positive")
        with self._lock:
            self._cancelled.add(parent_id)
            generations = tuple(self._generations.get(parent_id, {}).values())
            for generation in generations:
                generation.cancel_requested = True
            operation = self._aborts.get(parent_id)
            if operation is None:
                if not generations:
                    return 0
                operation = asyncio.run_coroutine_threadsafe(
                    self._abort_and_drain(generations, abort_engine), loop
                )
                self._aborts[parent_id] = operation

        def forget_operation(future):
            if future.cancelled() or future.exception() is not None:
                return
            with self._lock:
                if self._aborts.get(parent_id) is future:
                    self._aborts.pop(parent_id)

        operation.add_done_callback(forget_operation)
        return operation.result(timeout=timeout_sec)

    async def _abort_and_drain(self, generations, abort_engine):
        engine_ids = tuple(generation.engine_id for generation in generations)
        for generation in generations:
            self._cancel_task(generation)
        await abort_engine(engine_ids)
        await asyncio.gather(
            *(asyncio.wrap_future(generation.drained) for generation in generations)
        )
        # Also covers admission that was already crossing the engine RPC
        # boundary when cancellation was delivered. This API is idempotent.
        await abort_engine(engine_ids)
        return len(generations)

    def release(self, parent_id):
        """Only the drained pipeline may allow this parent ID to be reused."""
        with self._lock:
            if self._generations.get(parent_id):
                raise RuntimeError(f"Request {parent_id} still owns model work")
            operation = self._aborts.get(parent_id)
            if operation is not None:
                if not operation.done():
                    raise RuntimeError(f"Request {parent_id} is still aborting model work")
                operation.result()
            self._cancelled.discard(parent_id)
