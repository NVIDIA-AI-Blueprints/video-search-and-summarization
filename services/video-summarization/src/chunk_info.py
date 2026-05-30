# SPDX-FileCopyrightText: Copyright (c) 2024-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from pydantic import BaseModel, Field


@dataclass
class RequestSource:
    """Lightweight identity carrier for summarization requests.

    Replaces Asset/AssetManager for RTVI-only mode where no local
    file management is needed.
    """

    url: str | None = None
    camera_id: str = "default"
    source_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    @classmethod
    def for_file(cls, url: str, camera_id: str = "default") -> "RequestSource":
        """Create a RequestSource for a file-based request with a synthetic ID."""
        return cls(url=url, camera_id=camera_id)


def get_timestamp_str(ts):
    """Get RFC3339 string timestamp"""
    return (
        datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        + f".{(int(ts * 1000) % 1000):03d}Z"
    )


class ChunkInfo(BaseModel):
    """Represents a video chunk"""

    sourceId: str = Field(default="")
    chunkIdx: int = Field(default=0)
    file: str = Field(default="")
    pts_offset_ns: int = Field(default=0)
    start_pts: int = Field(default=0)
    end_pts: int = Field(default=-1)
    start_ntp: str = Field(default="")
    end_ntp: str = Field(default="")
    start_ntp_float: float = Field(default=0.0)
    end_ntp_float: float = Field(default=0.0)
    is_first: bool = Field(default=False)
    is_last: bool = Field(default=False)
    cv_metadata_json_file: str = Field(default="")
    osd_output_video_file: str = Field(default="")
    cached_frames_cv_meta: list = Field(default=[])

    def __repr__(self) -> str:
        if self.file.startswith("rtsp://"):
            return (
                f"Chunk {self.chunkIdx}: start={self.start_pts / 1000000000.0}"
                f" end={self.end_pts / 1000000000.0} start_ntp={self.start_ntp}"
                f" end_ntp={self.end_ntp} file={self.file}"
            )
        return (
            f"Chunk {self.chunkIdx}: start={self.start_pts / 1000000000.0}"
            f" end={self.end_pts / 1000000000.0} file={self.file}"
        )

    def __str__(self) -> str:
        return self.__repr__()

    def get_timestamp(self, frame_pts) -> str:
        timestamp_str = ""
        if self.file.startswith("rtsp://"):
            timestamp_float = self.start_ntp_float + frame_pts - self.start_pts / 1000000000.0
            timestamp_str = get_timestamp_str(timestamp_float)
        else:
            timestamp_str = str(frame_pts)
        return timestamp_str
