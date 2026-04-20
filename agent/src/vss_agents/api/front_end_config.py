# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""
Custom FastAPI front-end config that extends NAT's FastApiFrontEndConfig
"""

from collections.abc import AsyncGenerator

from nat.cli.register_workflow import register_front_end
from nat.data_models.config import Config
from nat.front_ends.fastapi.fastapi_front_end_config import FastApiFrontEndConfig
from nat.front_ends.fastapi.fastapi_front_end_plugin import FastApiFrontEndPlugin
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


class StreamingIngestConfig(BaseModel):
    """Configuration for the streaming video ingest and RTSP stream endpoints."""

    model_config = ConfigDict(extra="allow")

    vst_internal_url: str = Field(default="", description="Internal URL for VST service")
    rtvi_embed_base_url: str = Field(default="", description="Base URL for RTVI embedding service")
    rtvi_embed_model: str = Field(default="cosmos-embed1-448p", description="Embedding model name")
    rtvi_embed_chunk_duration: int = Field(default=5, description="Chunk duration in seconds for embedding")
    rtvi_cv_base_url: str = Field(default="", description="Base URL for RTVI CV service")
    vlm_mode: str = Field(default="", description="VLM mode (remote/local/local_shared)")
    internal_ip: str = Field(default="", description="Internal IP address of the host")
    external_ip: str = Field(default="", description="External IP address for public-facing URLs")
    elasticsearch_url: str = Field(default="", description="Elasticsearch endpoint URL")
    rtvi_embed_es_index: str = Field(default="", description="Elasticsearch index for embeddings")
    stream_mode: str = Field(default="search", description="'search' for search profile, 'other' for VST only")


class VSSFastApiFrontEndConfig(FastApiFrontEndConfig, name="vss_fastapi"):  # type: ignore[call-arg]
    """
    Extends NAT's FastAPI front-end config with a streaming_ingest section
    used by the custom video upload, RTSP stream, and video delete routes.
    """

    streaming_ingest: StreamingIngestConfig | None = Field(
        default=None,
        description="Configuration for streaming video ingest and RTSP stream management endpoints",
    )


@register_front_end(config_type=VSSFastApiFrontEndConfig)
async def register_vss_fastapi_front_end(
    _config: VSSFastApiFrontEndConfig, full_config: Config
) -> AsyncGenerator[FastApiFrontEndPlugin]:
    yield FastApiFrontEndPlugin(full_config=full_config)
