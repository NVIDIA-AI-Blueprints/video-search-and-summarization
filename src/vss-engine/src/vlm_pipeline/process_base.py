######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2024-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.
######################################################################################################
"""Base for VIA processes for decode / embedding / VLM etc."""

import concurrent.futures
import gc
import multiprocessing
import os
import queue
import time
import traceback
from threading import Thread
from typing import Optional

import torch
from grpc._channel import _MultiThreadedRendezvous

from via_logger import logger

mp_ctx = multiprocessing.get_context("spawn")


class ViaProcessBase(mp_ctx.Process):
    """VIA Process Base Class

    Handles batching of inputs, queue management etc."""

    def __init__(
        self,
        batch_size=1,
        qsize=0,
        gpu_id=0,
        input_queue: Optional[multiprocessing.Queue] = None,
        input_queue_lock: Optional[multiprocessing.Lock] = None,  # type: ignore
        disabled=False,
    ) -> None:
        """ViaProcessBase constructor

        Args:
            batch_size: Batch size for processing. Defaults to 1.
            qsize: Max input queue size. Defaults to 0 (no limit).
            gpu_id: GPU to run the process on. Defaults to 0.
            disabled: Disable the process. Defaults to False.
        """
        super().__init__()
        self._cmd_queue = mp_ctx.Queue()
        self._cmd_response_queue = mp_ctx.Queue()
        self._queue = input_queue if input_queue else mp_ctx.Queue(maxsize=qsize)
        self._qlock = input_queue_lock if input_queue_lock else mp_ctx.Lock()
        self._batch_size = batch_size
        self._gpu_id = gpu_id
        self._stop = mp_ctx.Event()
        self._output_queue = None
        self._disabled = disabled
        self._num_futures_threads = 5

    def start(self) -> None:
        """Start the process"""

        # Export the gpu to use as CUDA_VISIBLE_DEVICE environment variable before
        # starting the process
        os.environ["CUDA_VISIBLE_DEVICES"] = str(self._gpu_id)

        self._init_done_event = mp_ctx.Event()
        super().start()

    def wait_for_initialization(self):
        """Wait for the process initialization to complete

        Returns:
            Boolean indicating if process initialized successfully or encountered
            an error.
        """
        while not self._init_done_event.wait(1):
            if not self.is_alive():
                return False
        return True

    def stop(self):
        """Stop the process"""
        self._stop.set()
        self.join()

    def set_output_queue(self, queue: multiprocessing.Queue):
        """Set the output queue for the process"""
        self._output_queue = queue

    def set_final_output_queue(self, queue: multiprocessing.Queue):
        """Set the final output queue for the process (for handling errors)"""
        self._final_output_queue = queue

    @property
    def input_queue(self):
        return self._queue

    def send_command(self, command: str, **args):
        self._cmd_queue.put({"command": command, **args})
        return self._cmd_response_queue.get()

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

        if isinstance(result, concurrent.futures.Future):
            if result.exception():
                result = result.exception()
            else:
                result = result.result()

        if isinstance(result, BaseException):
            logger.error("".join(traceback.format_exception(result)))
            error_str = "An unknown error occurred"
            result = {
                "chunk": kwargs["chunk"],
                "chunk_id": kwargs["chunk_id"],
                "error": [error_str] * num_items if self._supports_batching() else error_str,
            }

        if result:
            # Handle return from the process method, un-batch the returned items
            # and create a dict per chunk. For errors, send returned item to
            # the final output queue
            for idx in range(num_items):
                ret_item = {
                    k: (v[idx] if self._supports_batching() else v) for k, v in result.items()
                }
                if "error" in ret_item and ret_item["error"]:
                    self._final_output_queue.put(ret_item)
                else:
                    self._output_queue.put(ret_item)
        elif isinstance(result, dict):
            # Empty dict returned by process method, send the chunk to final output queue
            for idx in range(num_items):
                self._final_output_queue.put(
                    {
                        "chunk": (
                            kwargs["chunk"][idx] if self._supports_batching() else kwargs["chunk"]
                        ),
                        "chunk_id": (
                            kwargs["chunk_id"][idx]
                            if self._supports_batching()
                            else kwargs["chunk_id"]
                        ),
                    }
                )
        torch.cuda.empty_cache()
        # Force Garbage Collect
        if os.environ.get("VSS_FORCE_GC"):
            print("Force Garbage Collect in VIA Server")
            gc.collect()

    def __process_int(self, **kwargs):
        """Process the next batch of inputs"""
        try:
            # Call the actual process method implemented by subclasses.
            result = self._process(**kwargs)
        except Exception as ex:
            result = ex

        if isinstance(result, concurrent.futures.Future):
            result.add_done_callback(lambda future_: self._handle_result(future_, **kwargs))
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
            ret = None
            if command == "drop-chunks":
                self._drop_chunks_stream_list.append(cmd["stream_id"])
            elif command == "stop-drop-chunks":
                self._drop_chunks_stream_list.remove(cmd["stream_id"])
            else:
                ret = self._handle_command(command, **cmd)
            self._cmd_response_queue.put(ret)

    def run(self) -> None:
        """Process execution method"""

        # Initalize the process - call the _initialize method
        if not self._disabled:
            logger.info(f"Initializing {type(self).__name__}-{self._gpu_id}")
            if not self._initialize():
                return

            if not bool(os.environ.get("VIA_SKIP_PIPELINE_WARMUP", False)):
                logger.info(f"Warmup {type(self).__name__}-{self._gpu_id}")
                self._warmup()
                logger.info(f"Warmup {type(self).__name__}-{self._gpu_id} done")
            logger.info(f"Initialized {type(self).__name__}-{self._gpu_id}")

        self._drop_chunks_stream_list = []
        self._cmd_handler_thread = Thread(target=self._cmd_handler_thread_func)
        self._cmd_handler_thread.start()

        if not self._supports_batching():
            self._batch_size = 1

        self._future_result_tpool = concurrent.futures.ThreadPoolExecutor(
            max_workers=self._num_futures_threads
        )
        torch.cuda.empty_cache()
        # Force Garbage Collect
        if os.environ.get("VSS_FORCE_GC"):
            print("Force Garbage Collect in VIA Server")
            gc.collect()
        self._init_done_event.set()

        items = []
        remaining_cached_item = None

        # Run while not signalled to stop
        while not self._stop.is_set():
            if not self._disabled and self._is_busy():
                time.sleep(0.01)
                continue
            with self._qlock:
                qsize = self._queue.qsize()

                if (len(items) + qsize) == 0:
                    time.sleep(0.01)
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
                    time.sleep(0.01)
                    wait_timeout_sec -= 0.01
                    qsize = self._queue.qsize()

                qsize = 1

                for _ in range(min(self._batch_size - len(items), qsize)):
                    item = self._queue.get()
                    if "chunk" in item and item["chunk"].streamId in self._drop_chunks_stream_list:
                        self._final_output_queue.put(
                            {k: v for k, v in item.items() if not isinstance(v, torch.Tensor)}
                        )
                        continue
                    # Check if items can be batched
                    if len(items) == 0 or self._can_batch(items[0], item):
                        items.append(item)
                    else:
                        remaining_cached_item = item
                        break

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
            items = [remaining_cached_item] if remaining_cached_item else []
            remaining_cached_item = None

        if not self._disabled:
            self._deinitialize()
            torch.cuda.empty_cache()
            # Force Garbage Collect
            if os.environ.get("VSS_FORCE_GC"):
                print("Force Garbage Collect in VIA Server")
                gc.collect()
        self._cmd_handler_thread.join()

    def enqueue_chunk(self, chunk, **kwargs):
        """Enqueue a chunk for processing

        Args:
            chunk: Chunk object
            **kwargs: Additional arguments to pass for processing
        """
        kwargs["chunk"] = chunk
        self._queue.put(kwargs)

    def push_item(self, **kwargs):
        self._queue.put(kwargs)
