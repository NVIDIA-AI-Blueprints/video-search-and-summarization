# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Excluded transient graphs must never enter durable serialization."""


def test_projection_ignores_circular_cached_frames_and_unknown_stats():
    from vss.ingestion.contracts import ChunkInfo, VlmChunkResponse
    from vss.ingestion.persistence import durable_response

    circular = {}
    circular["self"] = circular
    response = VlmChunkResponse(
        chunk=ChunkInfo(cached_frames_cv_meta=[circular]),
        vlm_response="caption",
        vlm_stats={"input_tokens": 2, "transient": circular},
    )
    result = durable_response(response)
    assert result.vlm_response == "caption"
    assert result.chunk.cached_frames_cv_meta == []
    assert result.vlm_stats == {"input_tokens": 2}


def test_projection_never_invokes_transient_object_serializers():
    from pydantic import BaseModel, model_serializer

    from vss.ingestion.contracts import ChunkInfo, VlmChunkResponse
    from vss.ingestion.persistence import durable_response

    class Transient(BaseModel):
        @model_serializer
        def forbidden(self):
            raise AssertionError("Transient serializer must never run")

    response = VlmChunkResponse(
        chunk=ChunkInfo(cached_frames_cv_meta=[Transient()]),
        vlm_response="caption",
        vlm_stats={"input_tokens": 2, "transient": Transient()},
    )
    result = durable_response(response)
    assert result.chunk.cached_frames_cv_meta == []
    assert result.vlm_stats == {"input_tokens": 2}
