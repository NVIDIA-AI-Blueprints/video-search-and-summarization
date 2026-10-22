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
