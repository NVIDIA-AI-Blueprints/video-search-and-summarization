# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import threading

from api_models.embeddings import TextEmbeddingsQuery
from common.chunk_info import ChunkInfo
from vlm_pipeline.vlm_pipeline import VlmPipeline


class _CaptureProc:
    def __init__(self):
        self.kwargs = None

    def enqueue_chunk(self, _chunk, **kwargs):
        self.kwargs = kwargs


def test_text_chunk_keeps_batched_retry_counts_aligned():
    pipeline = VlmPipeline.__new__(VlmPipeline)
    pipeline._enqueue_lock = threading.Lock()
    pipeline._chunk_counter = 0
    pipeline._chunk_callback_map = {}
    pipeline._vlm_procs = [_CaptureProc()]
    pipeline._args = type("Args", (), {"num_vlm_procs": 1})()

    pipeline.enqueue_text_chunk(
        ChunkInfo(),
        on_chunk_result=lambda _result: None,
        text_embeddings_query=TextEmbeddingsQuery(text_input="query", model="cosmos-embed1"),
    )

    assert pipeline._vlm_procs[0].kwargs["decode_retry_count"] == 0


def test_callback_failure_is_nonfatal(caplog):
    pipeline = VlmPipeline.__new__(VlmPipeline)

    def fail(_result):
        raise TypeError("telemetry rejected value")

    pipeline._invoke_chunk_result_callback(fail, object())

    assert "Chunk result callback failed" in caplog.text
