# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
"""Base for pipeline processes for decode / VLM etc."""

import concurrent.futures
import gc
import multiprocessing
import os
import queue
import time
import traceback
from threading import Thread
from typing import Optional
from uuid import uuid4

import torch
from grpc._channel import _MultiThreadedRendezvous

from common.logger import logger
from utils.env_validation import get_vllm_mm_tensor_ipc
from vlm_pipeline.errors import (
    CUDA_OOM_STATUS_CODE,
    format_cuda_oom_error,
    is_cuda_oom_error,
)

mp_ctx = multiprocessing.get_context("spawn")

# Read once in every spawned pipeline process. When vLLM's torch_shm tensor
# queue is active, DecoderProcess can send its CUDA frame tensor directly to
# VlmProcess through the existing torch-aware multiprocessing queue.
_USE_CUDA_MM_TENSOR_IPC = get_vllm_mm_tensor_ipc() == "torch_shm"
if _USE_CUDA_MM_TENSOR_IPC:
    # PyTorch shares expandable CUDA segments by duplicating allocator file
    # descriptors with pidfd_getfd. The RTVI container's default security
    # policy blocks that syscall. Classic CUDA allocations use portable CUDA
    # IPC handles and need no extra container capabilities.
    _alloc_conf = os.environ.get("PYTORCH_CUDA_ALLOC_CONF", "")
    _alloc_options = [option for option in _alloc_conf.split(",") if option]
    _alloc_options = [
        (
            "expandable_segments:False"
            if option.strip().lower().startswith("expandable_segments:")
            else option
        )
        for option in _alloc_options
    ]
    if not any(
        option.strip().lower().startswith("expandable_segments:") for option in _alloc_options
    ):
        _alloc_options.append("expandable_segments:False")
    os.environ["PYTORCH_CUDA_ALLOC_CONF"] = ",".join(_alloc_options)


_TRUE_ENV_VALUES = {"1", "true", "yes", "on"}
_FALSE_ENV_VALUES = {"0", "false", "no", "off", ""}
QUEUE_DECODED_FRAMES_ON_GPU_ENV = "RTVI_QUEUE_DECODED_FRAMES_ON_GPU"


def _parse_bool_env(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default

    normalized = value.strip().lower()
    if normalized in _TRUE_ENV_VALUES:
        return True
    if normalized in _FALSE_ENV_VALUES:
        return False

    logger.warning("Invalid %s=%s, using default %s", name, value, default)
    return default


def _move_cuda_frames_to_cpu(value):
    """Prepare decoded frame tensors for the downstream process queue."""
    if isinstance(value, torch.Tensor):
        if value.is_cuda and (
            _USE_CUDA_MM_TENSOR_IPC or _parse_bool_env(QUEUE_DECODED_FRAMES_ON_GPU_ENV)
        ):
            return value.detach()
        cpu_value = value.detach().cpu() if value.is_cuda else value
        return cpu_value
    if isinstance(value, list):
        return [_move_cuda_frames_to_cpu(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_move_cuda_frames_to_cpu(item) for item in value)
    return value


def _spill_cuda_frames_to_cpu(value):
    """Move decoded CUDA frames to CPU when the raw transport is backpressured."""
    if isinstance(value, torch.Tensor):
        return value.detach().cpu() if value.is_cuda else value
    if isinstance(value, list):
        return [_spill_cuda_frames_to_cpu(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_spill_cuda_frames_to_cpu(item) for item in value)
    return value


def _contains_cuda_tensor(value):
    if isinstance(value, torch.Tensor):
        return value.is_cuda
    if isinstance(value, (list, tuple)):
        return any(_contains_cuda_tensor(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_cuda_tensor(item) for item in value.values())
    return False


def _safe_cuda_empty_cache(force=False):
    if not force and os.environ.get("RTVI_EMPTY_CUDA_CACHE_ON_RESULT", "false").lower() != "true":
        return
    try:
        torch.cuda.empty_cache()
    except Exception as ex:
        logger.warning("Failed to empty CUDA cache after processing result: %s", ex)


class ProcessBase(mp_ctx.Process):
    """Pipeline Process Base Class

    Handles batching of inputs, queue management etc."""

    def __init__(
        self,
        batch_size=1,
        qsize=0,
        gpu_id=0,
        input_queue: Optional[multiprocessing.Queue] = None,
        input_queue_lock: Optional[multiprocessing.Lock] = None,  # type: ignore
        disabled=False,
        description="ProcessBase",
    ) -> None:
        """ProcessBase constructor

        Args:
            batch_size: Batch size for processing. Defaults to 1.
            qsize: Max input queue size. Defaults to 0 (no limit).
            gpu_id: GPU to run the process on. Defaults to 0.
            disabled: Disable the process. Defaults to False.
        """
        super().__init__()
        self._cmd_queue = mp_ctx.Queue()
        self._cmd_response_queue = mp_ctx.Queue()
        self._command_lock = mp_ctx.Lock()
        self._queue = input_queue if input_queue else mp_ctx.Queue(maxsize=qsize)
        self._qlock = input_queue_lock if input_queue_lock else mp_ctx.Lock()
        self._batch_size = batch_size
        self._gpu_id = gpu_id
        self._stop = mp_ctx.Event()
        self._output_queue = None
        self._disabled = disabled
        self._num_futures_threads = 5
        self._description = description

    def start(self) -> None:
        """Start the process"""

        # Export the gpu to use as CUDA_VISIBLE_DEVICE environment variable before
        # starting the process
        os.environ["CUDA_VISIBLE_DEVICES"] = str(self._gpu_id)

        self._init_done_event = mp_ctx.Event()
        super().start()

    def wait_for_initialization(self, timeout=None):
        """Wait for the process initialization to complete

        Args:
            timeout: Maximum time to wait in seconds. Defaults to
                RTVI_PROCESS_INIT_TIMEOUT_SEC or 600 seconds.

        Returns:
            Boolean indicating if process initialized successfully or encountered
            an error.
        """
        import time

        if timeout is None:
            timeout_env = os.environ.get("RTVI_PROCESS_INIT_TIMEOUT_SEC", "600")
            try:
                timeout = int(timeout_env)
            except ValueError:
                logger.warning(
                    "Invalid RTVI_PROCESS_INIT_TIMEOUT_SEC=%s, using default 600s",
                    timeout_env,
                )
                timeout = 600

        start_time = time.time()
        while not self._init_done_event.wait(1):
            if not self.is_alive():
                return False
            # Check timeout to prevent hanging indefinitely
            if time.time() - start_time > timeout:
                logger.error(
                    f"Timeout waiting for {type(self).__name__}-{self._gpu_id} initialization "
                    f"(>{timeout}s). Process may be stuck."
                )
                return False
        return True

    def stop(self):
        """Stop the process"""
        self._stop.set()
        self.join()

    def set_output_queue(self, queue: multiprocessing.Queue, queue_lock=None):
        """Set the output queue for the process"""
        self._output_queue = queue
        self._output_queue_lock = queue_lock

    def _put_queue_item(self, target, queue_lock, item, block=True, timeout=None):
        """Never hold a queue lock while waiting for capacity.

        Queue compaction and producers share the lock so cancellation can restore
        healthy queued items without another producer taking their slots.
        """
        if queue_lock is None:
            if not block:
                return target.put_nowait(item)
            return target.put(item, timeout=timeout) if timeout is not None else target.put(item)
        deadline = time.monotonic() + timeout if timeout is not None else None
        while True:
            with queue_lock:
                if self._request_is_cancelled(item.get("request_id")):
                    self._finish_cancelled_chunk(item)
                    return
                try:
                    return target.put_nowait(item)
                except queue.Full:
                    if not block:
                        raise
            if deadline is not None and time.monotonic() >= deadline:
                raise queue.Full
            time.sleep(0.001)

    def _put_output_item(self, item, block=True, timeout=None):
        return self._put_queue_item(
            self._output_queue,
            getattr(self, "_output_queue_lock", None),
            item,
            block=block,
            timeout=timeout,
        )

    def set_final_output_queue(self, queue: multiprocessing.Queue):
        """Set the final output queue for the process (for handling errors)"""
        self._final_output_queue = queue

    def set_token_stream_queue(self, queue: multiprocessing.Queue):
        """Set the queue for streaming token deltas (text-only chat)"""
        self._token_stream_queue = queue

    @property
    def input_queue(self):
        return self._queue

    @property
    def input_queue_lock(self):
        return self._qlock

    def send_command(self, command: str, response_timeout_sec=None, **args):
        """Serialize control RPCs and ignore replies from callers that timed out."""
        deadline = (
            time.monotonic() + response_timeout_sec
            if response_timeout_sec is not None
            else None
        )
        def remaining():
            return max(0.0, deadline - time.monotonic()) if deadline is not None else None

        if not self._command_lock.acquire(timeout=remaining()):
            raise TimeoutError(f"Timed out waiting to send worker command {command}")
        try:
            command_id = uuid4().hex
            self._cmd_queue.put({"command": command, "_command_id": command_id, **args})
            while True:
                try:
                    response = self._cmd_response_queue.get(timeout=remaining())
                except queue.Empty as ex:
                    raise TimeoutError(f"Timed out waiting for worker command {command}") from ex
                if response.get("command_id") != command_id:
                    continue
                if response.get("error"):
                    raise RuntimeError(response["error"])
                return response.get("result")
        finally:
            self._command_lock.release()

    def _request_is_cancelled(self, request_id):
        return bool(request_id) and request_id in getattr(self, "_cancelled_request_ids", ())

    def _finish_cancelled_chunk(self, item):
        """Return ownership only; never forward frames or stale model output."""
        self._final_output_queue.put(
            {
                **{
                    key: item[key]
                    for key in ("chunk", "chunk_id", "request_id", "is_live_stream")
                    if key in item
                },
                "cancelled": True,
            }
        )

    def _filter_cancelled_items(self, items):
        active = []
        for item in items:
            if self._request_is_cancelled(item.get("request_id")):
                self._finish_cancelled_chunk(item)
            else:
                active.append(item)
        return active

    def _discard_queued_request(self, request_id, timeout_sec):
        """Cancel queued work even while an unrelated request occupies the model."""
        retained, cancelled = [], []
        deadline = time.monotonic() + timeout_sec
        try:
            with self._qlock:
                try:
                    for _ in range(self._queue.qsize()):
                        item = self._queue.get(timeout=max(0.0, deadline - time.monotonic()))
                        if item.get("request_id") == request_id:
                            cancelled.append(item)
                        else:
                            retained.append(item)
                finally:
                    for item in retained:
                        self._queue.put_nowait(item)
        finally:
            for item in cancelled:
                self._finish_cancelled_chunk(item)

    def _abort_request(self, request_id, timeout_sec):
        """Subclasses may abort active work; queued/results are masked by the base."""

    def _release_request_tracking(self, request_id):
        """Subclasses may release model cancellation markers after producer drain."""

    def _is_busy(self):
        return False

    def _handle_future_result(self, func, *args):

        def wait_for_future(func, *args):
            args = [
                (
                    arg.result()
                    if (
                        isinstance(arg, concurrent.futures.Future)
                        or isinstance(arg, _MultiThreadedRendezvous)
                    )
                    else arg
                )
                for arg in args
            ]
            return func(*args)

        return self._future_result_tpool.submit(wait_for_future, func, *args)

    def _handle_result(self, result, **kwargs):
        num_items = len(kwargs["chunk"]) if self._supports_batching() else 1
        cancelled = False

        if isinstance(result, concurrent.futures.Future):
            if result.cancelled():
                cancelled = True
                result = {}
            elif result.exception():
                result = result.exception()
            else:
                result = result.result()

        if isinstance(result, concurrent.futures.CancelledError):
            cancelled = True
            result = {}
        elif isinstance(result, BaseException):
            logger.error("".join(traceback.format_exception(result)))
            # Preserve ServiceException message and status code if available
            try:
                from common.service_exception import ServiceException as _SE

                if is_cuda_oom_error(result):
                    error_str = format_cuda_oom_error(result, "processing pipeline chunk")
                    error_status_code = CUDA_OOM_STATUS_CODE
                elif isinstance(result, _SE):
                    error_str = result.message
                    error_status_code = result.status_code
                else:
                    error_str = "An unknown error occurred"
                    error_status_code = 500
            except ImportError:
                error_str = "An unknown error occurred"
                error_status_code = 500
            result = {
                "chunk": kwargs["chunk"],
                "chunk_id": kwargs["chunk_id"],
                "error": [error_str] * num_items if self._supports_batching() else error_str,
                "error_status_code": (
                    [error_status_code] * num_items
                    if self._supports_batching()
                    else error_status_code
                ),
            }
            for key in ("is_live_stream", "request_id"):
                if key in kwargs:
                    result[key] = kwargs[key]

        if result:
            # Handle return from the process method, un-batch the returned items
            # and create a dict per chunk. For errors, send returned item to
            # the final output queue
            for idx in range(num_items):

                def _get_item_value(v):
                    if not self._supports_batching():
                        return v
                    # Keep scalar values (ex: error=None) as-is while unbatching.
                    if isinstance(v, (str, bytes, dict)) or v is None:
                        return v
                    try:
                        return v[idx]
                    except (TypeError, KeyError, IndexError):
                        return v

                ret_item = {k: _get_item_value(v) for k, v in result.items()}
                if cancelled or self._request_is_cancelled(ret_item.get("request_id")):
                    self._finish_cancelled_chunk(ret_item)
                    continue
                try:
                    if "frames" in ret_item:
                        ret_item["frames"] = _move_cuda_frames_to_cpu(ret_item["frames"])
                except Exception as ex:
                    logger.error(
                        "Failed to prepare decoded frames for downstream processing: %s",
                        ex,
                        exc_info=True,
                    )
                    ret_item.pop("frames", None)
                    ret_item["error"] = f"Failed to transfer decoded frames from GPU to CPU: {ex}"
                    ret_item["error_status_code"] = 500
                    _safe_cuda_empty_cache(force=True)
                    self._final_output_queue.put(ret_item)
                else:
                    if "error" in ret_item and ret_item["error"]:
                        self._final_output_queue.put(ret_item)
                    else:
                        if _USE_CUDA_MM_TENSOR_IPC and _contains_cuda_tensor(
                            ret_item.get("frames")
                        ):
                            # The multiprocessing queue is logically sized for
                            # 128 inference requests, but CUDA IPC makes every
                            # queued raw frame buffer device-resident. Keep only
                            # one decoded buffer per configured decoder waiting
                            # at this transport boundary; VlmProcess continues
                            # submitting processed requests to EngineCore up to
                            # its independent 128-request capacity.
                            raw_transport_slots = max(
                                1, int(getattr(self, "_num_decoders_per_gpu", 8))
                            )
                            if self._output_queue.qsize() >= raw_transport_slots:
                                # A blocked live decoder otherwise retains one
                                # full GPU chunk per stream while waiting for a
                                # transport slot. At high stream counts that
                                # backpressure storage, rather than inference,
                                # exhausts device memory. Preserve direct CUDA
                                # IPC when a slot is available; spill only the
                                # overloaded tail to host memory.
                                ret_item["frames"] = _spill_cuda_frames_to_cpu(ret_item["frames"])
                                logger.debug(
                                    "Spilled decoded CUDA frames to CPU while the "
                                    "%d-slot decoder-to-VLM queue was full",
                                    raw_transport_slots,
                                )
                            # The multiprocessing transport retains the Python
                            # baseline's global 128-request capacity. Only its
                            # on-device window is bounded above; host-spilled
                            # chunks can continue filling that global queue.
                        self._put_output_item(ret_item)
        elif isinstance(result, dict):
            # Empty dict returned by process method, send the chunk to final output queue
            for idx in range(num_items):
                ret_item = {
                    "chunk": (
                        kwargs["chunk"][idx] if self._supports_batching() else kwargs["chunk"]
                    ),
                    "chunk_id": (
                        kwargs["chunk_id"][idx]
                        if self._supports_batching()
                        else kwargs["chunk_id"]
                    ),
                }
                for key in ("is_live_stream", "request_id"):
                    if key in kwargs:
                        ret_item[key] = (
                            kwargs[key][idx] if self._supports_batching() else kwargs[key]
                        )
                if cancelled or self._request_is_cancelled(ret_item.get("request_id")):
                    self._finish_cancelled_chunk(ret_item)
                else:
                    self._final_output_queue.put(ret_item)
        _safe_cuda_empty_cache()
        # Force Garbage Collect
        if os.environ.get("FORCE_PYTHON_GC"):
            print("Force Garbage Collect in RTVI Server")
            gc.collect()

    def __process_int(self, **kwargs):
        """Process the next batch of inputs"""
        request_ids = kwargs.get("request_id", [])
        if not isinstance(request_ids, list):
            request_ids = [request_ids]
        if request_ids and all(self._request_is_cancelled(rid) for rid in request_ids):
            self._handle_result({}, **kwargs)
            return
        try:
            # Call the actual process method implemented by subclasses.
            result = self._process(**kwargs)
        except Exception as ex:
            result = ex

        if isinstance(result, concurrent.futures.Future):
            # The async result handler only needs identifiers for its error and
            # empty-result paths. Capturing the full kwargs dictionary here kept
            # decoded CUDA frames alive until token generation completed, which
            # prevented the producing process from recycling IPC allocations.
            result_context = {
                "chunk": kwargs["chunk"],
                "chunk_id": kwargs["chunk_id"],
            }
            for key in ("is_live_stream", "request_id"):
                if key in kwargs:
                    result_context[key] = kwargs[key]
            result.add_done_callback(lambda future_: self._handle_result(future_, **result_context))
        else:
            self._handle_result(result, **kwargs)

    def _process(self, **kwargs) -> dict | None:
        """Method to be implemented by subclasses. Inputs are batched"""
        pass

    def _initialize(self):
        """Method to be implemented by subclasses"""
        return True

    def _deinitialize(self):
        """Method to be implemented by subclasses"""
        pass

    def _warmup(self):
        """Method to be implemented by subclasses"""
        return True

    def _supports_batching(self):
        return False

    def _can_batch(self, item1, item2):
        """Method to be implemented by subclasses. Sub classes must return a boolean
        indicating if the two items can be batched"""
        return False

    def _cmd_handler_thread_func(self):
        while not self._stop.is_set():
            try:
                cmd = self._cmd_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            command = cmd.pop("command")
            command_id = cmd.pop("_command_id", None)
            ret = None
            error = None
            try:
                if command == "drop-chunks":
                    # File requests sharing an asset each own a mask; live-delete retries do not.
                    if not cmd.get("idempotent") or cmd["stream_id"] not in self._drop_chunks_stream_list:
                        self._drop_chunks_stream_list.append(cmd["stream_id"])
                elif command == "stop-drop-chunks":
                    if cmd["stream_id"] in self._drop_chunks_stream_list:
                        self._drop_chunks_stream_list.remove(cmd["stream_id"])
                elif command == "abort-request":
                    self._cancelled_request_ids.add(cmd["request_id"])
                    deadline = time.monotonic() + cmd["timeout_sec"]
                    ret = self._abort_request(cmd["request_id"], cmd["timeout_sec"])
                    self._discard_queued_request(
                        cmd["request_id"], max(0.0, deadline - time.monotonic())
                    )
                elif command == "release-request-cancellation":
                    self._release_request_tracking(cmd["request_id"])
                    self._cancelled_request_ids.discard(cmd["request_id"])
                else:
                    ret = self._handle_command(command, **cmd)
            except Exception as ex:
                logger.exception("Worker command %s failed", command)
                error = f"{type(ex).__name__}: {ex}"
            self._cmd_response_queue.put(
                {"command_id": command_id, "result": ret, "error": error}
                if command_id is not None
                else ret
            )

    def run(self) -> None:
        """Process execution method"""

        # Initalize the process - call the _initialize method
        if not self._disabled:
            logger.info(f"Initializing {type(self).__name__}-{self._gpu_id}")
            if not self._initialize():
                return

            if not _parse_bool_env("SKIP_PIPELINE_WARMUP"):
                logger.info(f"Warmup {type(self).__name__}-{self._gpu_id}")
                try:
                    self._warmup()
                except Exception as e:
                    logger.error(f"Error during warmup {type(self).__name__}-{self._gpu_id}: {e}")
                logger.info(f"Warmup {type(self).__name__}-{self._gpu_id} done")
            logger.info(f"Initialized {type(self).__name__}-{self._gpu_id}")
        else:
            # For disabled processes, signal initialization complete immediately
            # to avoid hanging in wait_for_initialization()
            logger.info(
                f"Skipping initialization for disabled {type(self).__name__}-{self._gpu_id}"
            )
            self._init_done_event.set()

        self._drop_chunks_stream_list = []
        self._cancelled_request_ids = set()
        self._cmd_handler_thread = Thread(target=self._cmd_handler_thread_func)
        self._cmd_handler_thread.start()

        if not self._supports_batching():
            self._batch_size = 1

        self._future_result_tpool = concurrent.futures.ThreadPoolExecutor(
            max_workers=self._num_futures_threads
        )
        # Skip GPU operations for disabled processes
        if not self._disabled:
            torch.cuda.empty_cache()
        # Force Garbage Collect
        if os.environ.get("FORCE_PYTHON_GC"):
            print("Force Python Garbage Collect")
            gc.collect()
        # Only set event if not already set (for disabled processes)
        if not self._init_done_event.is_set():
            self._init_done_event.set()

        items = []
        remaining_cached_items = []

        # Run while not signalled to stop
        while not self._stop.is_set():
            items = self._filter_cancelled_items(items)
            if not self._disabled and self._is_busy():
                time.sleep(0.001)
                continue
            with self._qlock:
                qsize = self._queue.qsize()

                if (len(items) + qsize) == 0:
                    time.sleep(0.001)
                    continue

                if self._disabled:
                    self._final_output_queue.put(
                        {
                            k: v
                            for k, v in self._queue.get().items()
                            if not isinstance(v, torch.Tensor)
                        }
                    )
                    continue

                wait_timeout_sec = 0
                while (((qsize + len(items)) < self._batch_size)) and wait_timeout_sec > 0:
                    time.sleep(0.001)
                    wait_timeout_sec -= 0.01
                    qsize = self._queue.qsize()

                # qsize = 1

                for _ in range(min(self._batch_size - len(items), qsize)):
                    item = self._queue.get()
                    if self._request_is_cancelled(item.get("request_id")):
                        self._finish_cancelled_chunk(item)
                        continue
                    if "chunk" in item and item["chunk"].streamId in self._drop_chunks_stream_list:
                        self._final_output_queue.put(
                            {k: v for k, v in item.items() if not isinstance(v, torch.Tensor)}
                        )
                        continue
                    # Check if items can be batched
                    if len(items) == 0 or self._can_batch(items[0], item):
                        items.append(item)
                    else:
                        remaining_cached_items.append(item)
                        break

            items = self._filter_cancelled_items(items)
            if items:
                if self._supports_batching():
                    cached_items = {}
                    for item in items:
                        # Batch together inputs, batch arguments together
                        for k, v in item.items():
                            if k not in cached_items:
                                cached_items[k] = []
                            cached_items[k].append(v)
                    self.__process_int(**cached_items)

                else:
                    self.__process_int(**items[0])
            items = remaining_cached_items
            remaining_cached_items = []

        if not self._disabled:
            self._deinitialize()
            torch.cuda.empty_cache()
            # Force Garbage Collect
            if os.environ.get("FORCE_PYTHON_GC"):
                print("Force Python Garbage Collect")
                gc.collect()
        self._cmd_handler_thread.join()

    def enqueue_chunk(self, chunk, **kwargs):
        """Enqueue a chunk for processing

        Args:
            chunk: Chunk object
            **kwargs: Additional arguments to pass for processing
        """
        kwargs["chunk"] = chunk
        self._put_queue_item(self._queue, self._qlock, kwargs)

    def push_item(self, **kwargs):
        self._put_queue_item(self._queue, self._qlock, kwargs)
