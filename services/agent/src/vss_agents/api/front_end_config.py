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
    """Configuration for the streaming video ingest and RTSP stream endpoints.

    Each profile YAML declares which custom routes it wants registered via the
    ``enable_*`` capability flags. Per-route runtime behavior is driven by the
    presence of the relevant integration URL (e.g. RTVI cleanup steps self-skip
    when their URL is empty), with one explicit policy flag for the only
    decision that isn't reducible to URL presence:
    ``delete_vst_storage_on_stream_remove``.

    Profiles add a new YAML; they don't add code.
    """

    model_config = ConfigDict(extra="allow")

    vst_internal_url: str = Field(default="", description="Internal URL for VST service")

    enable_videos_for_search: bool = Field(
        default=False,
        description="Register PUT/POST /api/v1/videos-for-search/* (search-only chunked upload)",
    )
    enable_rtsp_streams: bool = Field(
        default=False,
        description="Register POST /api/v1/rtsp-streams/add and DELETE /api/v1/rtsp-streams/delete/{name}",
    )
    enable_video_delete: bool = Field(
        default=False,
        description="Register DELETE /api/v1/videos/{video_id}",
    )

    delete_vst_storage_on_stream_remove: bool = Field(
        default=True,
        description=(
            "Whether DELETE /api/v1/rtsp-streams/delete/{name} also removes the VST storage. "
            "Search-style deployments leave this False (RTVI manages the storage lifecycle); "
            "alerts-style and VST-only deployments set it True."
        ),
    )

    rtvi_embed_base_url: str = Field(default="", description="Base URL for RTVI embedding service")
    rtvi_embed_model: str = Field(default="cosmos-embed1-448p", description="Embedding model name")
    rtvi_embed_chunk_duration: int = Field(default=5, description="Chunk duration in seconds for embedding")
    rtvi_cv_base_url: str = Field(default="", description="Base URL for RTVI CV service")
    rtvi_vlm_base_url: str = Field(
        default="",
        description=(
            "Base URL for RTVI-VLM service. When set, the RTSP stream add endpoint "
            "registers streams with RTVI-VLM (LVS path) instead of the RTVI-CV/embed search path."
        ),
    )
    elasticsearch_url: str = Field(default="", description="Elasticsearch endpoint URL")
    rtvi_embed_es_index: str = Field(default="", description="Elasticsearch index for embeddings")

    vlm_mode: str = Field(default="", description="VLM mode (remote/local/local_shared)")
    internal_ip: str = Field(default="", description="Internal IP address of the host")
    external_ip: str = Field(default="", description="External IP address for public-facing URLs")


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
