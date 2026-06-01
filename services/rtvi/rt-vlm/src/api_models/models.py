# SPDX-FileCopyrightText: Copyright (c) 2024-2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
"""Models API Models."""

from typing import Literal

from pydantic import Field

from .common import ANY_CHAR_PATTERN, DESCRIPTION_PATTERN, CommonBaseModel


class ModelInfo(CommonBaseModel):
    """Describes an model offering that can be used with the API."""

    id: str = Field(
        description="The model identifier, which can be referenced in the API endpoints.",
        pattern=ANY_CHAR_PATTERN,
        max_length=2560,
    )
    created: int = Field(
        description="The Unix timestamp (in seconds) when the model was created.",
        examples=[1686935002],
        ge=0,
        le=4000000000,
        json_schema_extra={"format": "int64"},
    )
    object: Literal["model"] = Field(description="Type of object")
    owned_by: str = Field(
        description="The organization that owns the model.",
        examples=["NVIDIA"],
        max_length=10000,
        pattern=DESCRIPTION_PATTERN,
    )
    api_type: str = Field(
        description="API used to access model.",
        examples=["internal"],
        max_length=32,
        pattern=r"^[A-Za-z]*$",
    )


class ListModelsResponse(CommonBaseModel):
    """Lists and describes the various models available."""

    object: Literal["list"] = Field(description="Type of response object")
    data: list[ModelInfo] = Field(max_length=5)
    audio_support: bool = Field(description="Whether the server supports audio transcription.")
