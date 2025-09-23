######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2024-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.
######################################################################################################

import os
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from via_logger import logger


def get_timestamp_str(ts):
    """Get RFC3339 string timestamp"""
    return (
        datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        + f".{(int(ts * 1000) % 1000):03d}Z"
    )


class ChunkInfo(BaseModel):
    """Represents a video chunk"""

    streamId: str = Field(default="")
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
    asset_dir: str = Field(default="")

    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        if name in ["streamId", "chunkIdx"] and self.streamId and self.chunkIdx is not None:
            logger.debug(f"Setting asset_dir to minio://{self.streamId}/chunk_{self.chunkIdx}")
            if os.getenv("SAVE_CHUNK_FRAMES_MINIO", "").lower() in ["true", "1"]:
                super().__setattr__("asset_dir", f"minio://{self.streamId}/chunk_{self.chunkIdx}")

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
