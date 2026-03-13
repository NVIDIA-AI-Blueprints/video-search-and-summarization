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
from datetime import datetime
import math
from typing import Annotated
from typing import Any
from typing import Literal

from pydantic import BaseModel
from pydantic import BeforeValidator
from pydantic import Field
from pydantic import model_validator


def float_to_int(v: float | int) -> int:
    return math.ceil(v) if v is not None else None


class MediaInfoOffset(BaseModel):
    """Media information using offset for files."""

    type: Literal["offset"] = Field(
        default="offset", description="Information about a segment of media with start and end offsets."
    )
    start_offset: Annotated[
        int,
        Field(
            default=None,
            description="Segment start offset in seconds from the beginning of the media.",
            ge=0,
            le=4000000000,
            alias=["start", "start_timestamp"],
        ),
        BeforeValidator(float_to_int),
    ]
    end_offset: Annotated[
        int,
        Field(
            default=None,
            description="Segment end offset in seconds from the beginning of the media.",
            ge=0,
            le=4000000000,
            alias=["end", "end_timestamp"],
        ),
        BeforeValidator(float_to_int),
    ]

    @model_validator(mode="before")
    @classmethod
    def validate_start_and_end(cls, data: dict[str, Any]) -> dict[str, Any]:
        if data.get("start_offset") is None:
            data["start_offset"] = 0
        if data.get("end_offset") is None:
            data["end_offset"] = 4000000000
        return data

    model_config = {
        "extra": "forbid",
        "populate_by_name": True,
    }


# Validate RFC3339 timestamp string
def timestamp_validator(v: str, validation_info: Any) -> str:
    try:
        # Attempt to parse the RFC3339 timestamp
        datetime.strptime(v, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError as e:
        raise ValueError(
            f"{validation_info.field_name} be a valid RFC3339 timestamp string",
            "InvalidParameters",
        ) from e
    return v


def remove_timezone(dt: datetime | str) -> datetime:
    """Remove timezone info from datetime objects and handle ISO 8601 with or without microseconds."""

    if isinstance(dt, str):
        try:
            # Handle 'Z' for UTC and optional microseconds
            if dt.endswith("Z"):
                dt = dt[:-1] + "+00:00"
            parsed_dt = datetime.fromisoformat(dt)
        except ValueError:
            # Fallback for other potential formats or re-raise with more context if needed
            # For now, let's stick to the original error behavior if fromisoformat fails
            # This could be a place to try the original strptime if fromisoformat is too strict for other cases
            raise ValueError(f"Timestamp '{dt}' is not a recognized ISO 8601 format.") from None
    elif isinstance(dt, datetime):
        parsed_dt = dt
    else:
        # Should not happen based on type hints, but good for robustness
        raise TypeError(f"Expected datetime or string, got {type(dt)}")

    if parsed_dt.tzinfo:
        return parsed_dt.replace(tzinfo=None)
    return parsed_dt
