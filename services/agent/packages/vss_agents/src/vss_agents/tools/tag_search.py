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
"""NAT adapter for source-scoped BM25 search over VLM tag documents."""

from collections.abc import AsyncGenerator

from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.function import FunctionBaseConfig
from pydantic import Field

from vss_core.search_core.clients import ElasticClient
from vss_core.search_core.models import TagSearchInput
from vss_core.search_core.models import TagSearchOutput
from vss_core.search_core.primitives import TagSearch
from vss_core.vios import VSTClient


class TagSearchConfig(FunctionBaseConfig, name="tag_search"):
    """Configuration for VLM tag keyword retrieval."""

    es_endpoint: str = Field(..., description="Elasticsearch endpoint containing VLM tag documents")
    tag_index: str = Field(
        default="default_*",
        description="RT-VLM caption index family; '*' is replaced with each selected source ID",
    )
    vst_internal_url: str = Field(..., description="Internal VST endpoint used to resolve source identities")
    vst_external_url: str = Field(..., description="External VST endpoint used for screenshot URLs")
    default_max_results: int = Field(default=100, ge=1, le=1000)


@register_function(config_type=TagSearchConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def tag_search(config: TagSearchConfig, _builder: Builder) -> AsyncGenerator[FunctionInfo]:
    """Build the Agent adapter around the reusable ``vss_core`` primitive."""
    es = ElasticClient.from_endpoint(config.es_endpoint)
    vst = VSTClient(
        internal_url=config.vst_internal_url,
        external_url=config.vst_external_url,
    )
    primitive = TagSearch(
        es=es,
        vst=vst,
        tag_index=config.tag_index,
        default_max_results=config.default_max_results,
    )

    async def _tag_search(search_input: TagSearchInput) -> TagSearchOutput:
        return await primitive.run(search_input)

    try:
        yield FunctionInfo.create(
            single_fn=_tag_search,
            description="Search VLM tags and descriptions with optional source scoping using BM25",
            input_schema=TagSearchInput,
            single_output_schema=TagSearchOutput,
        )
    finally:
        await es.aclose()
        await vst.aclose()
