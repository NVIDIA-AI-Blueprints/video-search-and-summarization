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
"""Files API Models."""

from enum import Enum
from typing import Annotated, Literal, Optional
from uuid import UUID

from pydantic import AfterValidator, Field

from .common import (
    ANY_CHAR_PATTERN,
    FILE_NAME_PATTERN,
    TIMESTAMP_PATTERN,
    CommonBaseModel,
    timestamp_validator,
)


class MediaType(str, Enum):
    """Media type of the uploaded file."""

    VIDEO = "video"
    IMAGE = "image"


class Purpose(str, Enum):
    """Purpose for the file."""

    VISION = "vision"


class FileInfo(CommonBaseModel):
    """Information about an uploaded file."""

    id: UUID = Field(
        description="The file identifier, which can be referenced in the API endpoints."
    )
    bytes: int = Field(
        description="The size of the file, in bytes.",
        json_schema_extra={"format": "int64"},
        examples=[2000000],
        ge=0,
        le=100_000_000_000,
    )
    filename: str = Field(
        description="Filename along with path to be used.",
        max_length=256,
        examples=["myfile.mp4"],
        pattern=FILE_NAME_PATTERN,
    )
    creation_time: Annotated[
        Optional[str | None],
        AfterValidator(lambda v, info: timestamp_validator(v, info) if v else None),
    ] = Field(
        default=None,
        description=(
            "Creation time of the file in ISO8601 format. "
            "If provided, this offsets the frame times in the response. "
            "If not provided, the frame times will be relative to the start of the file."
        ),
        min_length=24,
        max_length=24,
        examples=["2024-06-09T18:32:11.123Z"],
        pattern=TIMESTAMP_PATTERN,
    )
    purpose: Purpose = Field(
        description=("The intended purpose of the uploaded file. This must be set to vision"),
        examples=["vision"],
    )
    sensor_name: str = Field(
        default="",
        description="User-defined sensor name for the file.",
        max_length=256,
        examples=["camera-001"],
        pattern=ANY_CHAR_PATTERN,
    )


class AddFileInfoResponse(FileInfo):
    """Response schema for the add file request."""

    media_type: MediaType = Field(description="Media type (image / video).")


class DeleteFileResponse(CommonBaseModel):
    """Response schema for delete file request."""

    id: UUID = Field(
        description="The file identifier, which can be referenced in the API endpoints."
    )
    object: Literal["file"] = Field(description="Type of response object.")
    deleted: bool = Field(description="Indicates if the file was deleted")


class ListFilesResponse(CommonBaseModel):
    """Response schema for the list files API."""

    data: list[AddFileInfoResponse] = Field(max_length=1000000)
    object: Literal["list"] = Field(description="Type of response object")
