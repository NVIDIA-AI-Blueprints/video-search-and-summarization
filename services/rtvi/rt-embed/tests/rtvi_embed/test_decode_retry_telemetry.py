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

"""Regression tests for decode-retry telemetry on the processed-chunk watcher.

A batched VLM request is assembled by appending per key across the queued item
dicts, so an item that skips the decoder used to make the batched lists ragged
and leak a list into PipelineChunkResult.decode_retry_count. Feeding that list
to an OpenTelemetry counter raises, and because the counter is emitted from the
processed-chunk watcher thread the raise used to kill chunk delivery for the
rest of the process lifetime.

These tests pin the coercion, the ragged-batch fix, and the guarantee that a
faulting callback cannot stop the watcher.
"""

import queue
import threading
import time

import pytest

from api_models.embeddings import TextEmbeddingsQuery
from common.chunk_info import ChunkInfo
from vlm_pipeline.process_base import ProcessBase
from vlm_pipeline.vlm_pipeline import (
    PipelineChunkResult,
    VlmPipeline,
    _coerce_decode_retry_count,
)


class CaptureQueue:
    def __init__(self):
        self.items = []

    def put(self, item):
        self.items.append(item)


class CaptureProc:
    """Stand-in for a VlmProcess that records the kwargs it is handed."""

    def __init__(self):
        self.enqueued = []

    def enqueue_chunk(self, chunk, **kwargs):
        kwargs["chunk"] = chunk
        self.enqueued.append(kwargs)


def _make_batching_proc():
    """A ProcessBase that batches, without running __init__ (no GPU, no mp)."""
    proc = ProcessBase.__new__(ProcessBase)
    proc._output_queue = CaptureQueue()
    proc._final_output_queue = CaptureQueue()
    proc._supports_batching = lambda: True
    return proc


def _batch(items):
    """Reproduce ProcessBase's per-key input batching."""
    batched = {}
    for item in items:
        for key, value in item.items():
            batched.setdefault(key, []).append(value)
    return batched


def _make_pipeline():
    """A VlmPipeline shell with only the attributes these tests touch."""
    pipeline = VlmPipeline.__new__(VlmPipeline)
    pipeline._enqueue_lock = threading.Lock()
    pipeline._live_stream_lock = threading.Lock()
    pipeline._chunk_counter = 0
    pipeline._chunk_callback_map = {}
    pipeline._live_stream_id_map = {}
    pipeline._processed_chunk_queue = queue.Queue()
    pipeline._processed_chunk_queue_watcher_stop_event = threading.Event()
    return pipeline


class TestCoerceDecodeRetryCount:
    """The value handed to the counter is always an int."""

    def test_int_is_passed_through(self):
        assert _coerce_decode_retry_count(3) == 3

    def test_zero_is_passed_through(self):
        assert _coerce_decode_retry_count(0) == 0

    def test_ragged_batch_list_is_summed(self):
        # A leaked batch list must not reach the counter, and the retries it
        # holds are real, so they are summed rather than dropped.
        assert _coerce_decode_retry_count([2, 1]) == 3

    def test_single_element_list_is_reduced(self):
        assert _coerce_decode_retry_count([0]) == 0

    def test_empty_list_is_zero(self):
        assert _coerce_decode_retry_count([]) == 0

    def test_tuple_is_reduced(self):
        assert _coerce_decode_retry_count((1, 1)) == 2

    @pytest.mark.parametrize("value", [None, "not-a-number", object()])
    def test_unusable_value_falls_back_to_zero(self, value):
        assert _coerce_decode_retry_count(value) == 0


class TestRaggedBatchUnbatching:
    """Document the batching behaviour that produced the list."""

    def test_missing_key_makes_unbatching_pass_a_list(self):
        # An item without decode_retry_count co-batched with a decoded chunk
        # leaves the batched list shorter than the chunk list, and unbatching
        # cannot index it.
        decoded_chunk = {"chunk": "c0", "chunk_id": 0, "decode_retry_count": 0}
        chunk_without_key = {"chunk": "c1", "chunk_id": 1}
        batched = _batch([decoded_chunk, chunk_without_key])

        proc = _make_batching_proc()
        proc._handle_result(dict(batched), **batched)

        retry_counts = [item.get("decode_retry_count") for item in proc._output_queue.items]
        assert retry_counts == [0, [0]]
        # A list is truthy even when it holds only zeros, so the counter fired
        # on chunks that had never retried at all.
        assert bool([0]) is True

    def test_uniform_keys_unbatch_to_ints(self):
        decoded_chunk = {"chunk": "c0", "chunk_id": 0, "decode_retry_count": 0}
        text_chunk = {"chunk": "c1", "chunk_id": 1, "decode_retry_count": 0}
        batched = _batch([decoded_chunk, text_chunk])

        proc = _make_batching_proc()
        proc._handle_result(dict(batched), **batched)

        retry_counts = [item.get("decode_retry_count") for item in proc._output_queue.items]
        assert retry_counts == [0, 0]
        assert all(isinstance(count, int) for count in retry_counts)


class TestTextChunkEnqueueCarriesRetryCount:
    """Chunks that skip the decoder still carry the key, keeping batches even."""

    def test_enqueue_text_chunk_sets_decode_retry_count(self):
        pipeline = _make_pipeline()
        proc = CaptureProc()
        pipeline._vlm_procs = [proc]
        pipeline._args = type("Args", (), {"num_vlm_procs": 1})()

        pipeline.enqueue_text_chunk(
            ChunkInfo(),
            on_chunk_result=lambda result: None,
            text_embeddings_query=TextEmbeddingsQuery(text_input="a query", model="cosmos-embed1"),
        )

        assert len(proc.enqueued) == 1
        assert proc.enqueued[0]["decode_retry_count"] == 0

    def test_text_chunk_batches_evenly_with_a_decoded_chunk(self):
        pipeline = _make_pipeline()
        proc = CaptureProc()
        pipeline._vlm_procs = [proc]
        pipeline._args = type("Args", (), {"num_vlm_procs": 1})()

        pipeline.enqueue_text_chunk(
            ChunkInfo(),
            on_chunk_result=lambda result: None,
            text_embeddings_query=TextEmbeddingsQuery(text_input="a query", model="cosmos-embed1"),
        )
        text_item = proc.enqueued[0]
        # Mirror the keys the decoder emits alongside a decoded chunk.
        decoded_item = dict(text_item)
        decoded_item.update({"chunk": ChunkInfo(), "chunk_id": 99, "decode_retry_count": 1})

        batched = _batch([decoded_item, text_item])
        # The alignment property the fix restores: every batched item carries
        # the key, so the list is as long as the chunk list and unbatching can
        # index it for every chunk.
        assert len(batched["decode_retry_count"]) == len(batched["chunk"])

        handler = _make_batching_proc()
        handler._handle_result(dict(batched), **batched)
        delivered = handler._output_queue.items + handler._final_output_queue.items
        retry_counts = [item.get("decode_retry_count") for item in delivered]
        assert retry_counts == [1, 0]


class TestWatcherSurvivesCallbackFaults:
    """A telemetry fault in a callback must not stop chunk delivery."""

    @staticmethod
    def _run_watcher(pipeline):
        thread = threading.Thread(target=pipeline._watch_processed_chunk_queue, daemon=True)
        thread.start()
        return thread

    @staticmethod
    def _stop_watcher(pipeline, thread):
        pipeline._processed_chunk_queue_watcher_stop_event.set()
        thread.join(timeout=10)

    def test_raising_callback_does_not_kill_the_watcher(self):
        pipeline = _make_pipeline()
        delivered = []

        def failing_callback(chunk_result):
            raise TypeError("'<' not supported between instances of 'list' and 'int'")

        def good_callback(chunk_result):
            delivered.append(chunk_result)

        pipeline._chunk_callback_map = {0: failing_callback, 1: good_callback}
        thread = self._run_watcher(pipeline)
        try:
            pipeline._processed_chunk_queue.put({"chunk": ChunkInfo(), "chunk_id": 0})
            pipeline._processed_chunk_queue.put({"chunk": ChunkInfo(), "chunk_id": 1})

            deadline = time.time() + 10
            while not delivered and time.time() < deadline:
                time.sleep(0.01)

            # The chunk after the faulting one was still delivered, so the
            # watcher thread outlived the fault.
            assert len(delivered) == 1
            assert thread.is_alive()
        finally:
            self._stop_watcher(pipeline, thread)

    def test_invoke_callback_swallows_and_logs(self, caplog):
        pipeline = _make_pipeline()

        def failing_callback(chunk_result):
            raise RuntimeError("metrics exporter exploded")

        with caplog.at_level("ERROR"):
            pipeline._invoke_chunk_result_callback(failing_callback, PipelineChunkResult())

        assert "Chunk result callback failed" in caplog.text

    def test_list_retry_count_reaches_the_callback_as_an_int(self):
        # End to end over the watcher: a leaked list is coerced before any
        # consumer (metrics, admission control) sees it.
        pipeline = _make_pipeline()
        seen = []
        pipeline._chunk_callback_map = {0: seen.append}

        thread = self._run_watcher(pipeline)
        try:
            pipeline._processed_chunk_queue.put(
                {"chunk": ChunkInfo(), "chunk_id": 0, "decode_retry_count": [2, 1]}
            )
            deadline = time.time() + 10
            while not seen and time.time() < deadline:
                time.sleep(0.01)

            assert len(seen) == 1
            assert seen[0].decode_retry_count == 3
            assert isinstance(seen[0].decode_retry_count, int)
        finally:
            self._stop_watcher(pipeline, thread)


class TestCounterAcceptsCoercedValue:
    """The coerced value is something an OpenTelemetry counter will take."""

    def test_real_counter_accepts_coerced_value(self):
        sdk_metrics = pytest.importorskip("opentelemetry.sdk.metrics")
        from opentelemetry.sdk.metrics.export import InMemoryMetricReader

        reader = InMemoryMetricReader()
        provider = sdk_metrics.MeterProvider(metric_readers=[reader])
        counter = provider.get_meter("test").create_counter(
            name="rtvi_decode_retry_total", unit="1"
        )

        # The pre-fix value raises; the coerced value records.
        with pytest.raises(TypeError):
            counter.add([2, 1], {"reason": "first_attempt_error"})

        counter.add(
            _coerce_decode_retry_count([2, 1]),
            {"reason": "first_attempt_error"},
        )

        data = reader.get_metrics_data()
        points = [
            point
            for resource_metric in data.resource_metrics
            for scope_metric in resource_metric.scope_metrics
            for metric in scope_metric.metrics
            for point in metric.data.data_points
        ]
        assert [point.value for point in points] == [3]
