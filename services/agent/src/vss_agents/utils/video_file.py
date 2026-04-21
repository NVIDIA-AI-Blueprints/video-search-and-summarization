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
import logging
import os

import cv2

from vss_agents.data_models.vss import MediaInfoOffset

logger = logging.getLogger(__name__)


def get_video_duration(file_path: str) -> float:
    # Check if file exists
    if not os.path.exists(file_path):
        logger.error(f"Video file does not exist: {file_path}")
        return 0.0

    video_capture = cv2.VideoCapture(file_path)

    # Check if video was opened successfully
    if not video_capture.isOpened():
        logger.error(f"Could not open video file: {file_path}")
        video_capture.release()
        return 0.0

    # Get frame count and FPS
    frame_count = video_capture.get(cv2.CAP_PROP_FRAME_COUNT)
    fps = video_capture.get(cv2.CAP_PROP_FPS)

    video_capture.release()

    # Check for valid FPS to avoid division by zero
    if fps <= 0:
        logger.error(f"Invalid FPS ({fps}) for video file: {file_path}")
        return 0.0

    # Check for valid frame count
    if frame_count <= 0:
        logger.error(f"Invalid frame count ({frame_count}) for video file: {file_path}")
        return 0.0

    video_duration = frame_count / fps
    logger.info(f"Video duration for {file_path}: {video_duration} seconds")
    return video_duration


def pad_media_info(media_info: MediaInfoOffset, video_duration: float, min_chunk_duration: int = 2) -> MediaInfoOffset:
    """Pad the media info to the minimum chunk duration"""
    left_padding = min_chunk_duration // 2

    if media_info.start_offset > left_padding:
        media_info.start_offset -= left_padding
    else:
        left_padding = media_info.start_offset
        media_info.start_offset = 0
    right_padding = min_chunk_duration - left_padding
    media_info.end_offset += right_padding
    if media_info.end_offset > video_duration:
        media_info.end_offset = int(video_duration)
    return media_info
