# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# SPDX-License-Identifier: Apache-2.0
"""Regression tests for retry-count alignment in mixed VLM batches."""

from threading import Lock
from types import SimpleNamespace
from unittest.mock import Mock

from vlm_pipeline.vlm_pipeline import VlmPipeline, VlmProcess, VlmRequestParams


def _pipeline_with_mock_vlm_process():
    pipeline = object.__new__(VlmPipeline)
    pipeline._enqueue_lock = Lock()
    pipeline._chunk_counter = 0
    pipeline._chunk_callback_map = {}
    pipeline._vlm_procs = [Mock()]
    pipeline._args = SimpleNamespace(num_vlm_procs=1)
    return pipeline


def test_enqueue_vlm_text_chunk_sets_zero_decode_retry_count(monkeypatch):
    pipeline = _pipeline_with_mock_vlm_process()
    monkeypatch.setattr(
        VlmRequestParams,
        "from_vlm_query",
        staticmethod(lambda _query: VlmRequestParams()),
    )

    pipeline.enqueue_vlm_text_chunk(object(), lambda _result: None, object())

    assert pipeline._vlm_procs[0].enqueue_chunk.call_args.kwargs["decode_retry_count"] == 0


def test_enqueue_text_chunk_sets_zero_decode_retry_count(monkeypatch):
    pipeline = _pipeline_with_mock_vlm_process()
    monkeypatch.setattr(
        VlmRequestParams,
        "from_text_embeddings_query",
        staticmethod(lambda _query: VlmRequestParams()),
    )

    pipeline.enqueue_text_chunk(object(), lambda _result: None, object())

    assert pipeline._vlm_procs[0].enqueue_chunk.call_args.kwargs["decode_retry_count"] == 0


def test_mixed_text_and_video_batch_unbatches_retry_count_to_scalars():
    text_item = {"chunk": "text", "chunk_id": 1, "decode_retry_count": 0}
    video_item = {"chunk": "video", "chunk_id": 2, "decode_retry_count": 0}
    batched_items = {}
    for item in (text_item, video_item):
        for key, value in item.items():
            batched_items.setdefault(key, []).append(value)

    output_queue = SimpleNamespace(items=[])
    output_queue.put = output_queue.items.append
    process = object.__new__(VlmProcess)
    process._output_queue = output_queue
    process._final_output_queue = output_queue

    result = {
        **batched_items,
        "error": [None, None],
    }
    VlmProcess._handle_result(process, result, **batched_items)

    assert [item["decode_retry_count"] for item in output_queue.items] == [0, 0]
    assert all(isinstance(item["decode_retry_count"], int) for item in output_queue.items)
