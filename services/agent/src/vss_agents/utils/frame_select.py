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
import base64
import logging
import math
import shutil
import subprocess

import cv2

logger = logging.getLogger(__name__)


def frame_select(video_path: str, start_timestamp: float, end_timestamp: float, step_size: float) -> list[str]:
    """
    Select frames from a video using OpenCV.

    Args:
        video_path: Path to the video file
        start_timestamp: Start time in seconds
        end_timestamp: End time in seconds
        step_size: Time interval between frames in seconds

    Returns:
        List of base64 encoded JPEG frame images
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        logger.error(f"Could not open video file: {video_path}")
        raise ValueError(f"Could not open video file: {video_path}")

    try:
        # Get video properties
        fps = cap.get(cv2.CAP_PROP_FPS)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

        # Calculate frame indices
        start_frame = min(total_frames - 1, math.floor(start_timestamp * fps))
        end_frame = min(total_frames - 1, math.ceil(end_timestamp * fps))
        step_size_frame = max(1, math.floor(step_size * fps))

        frame_selection = list(range(start_frame, end_frame, step_size_frame))
        if len(frame_selection) == 0:
            logger.warning(f"No frames selected for video {video_path} from {start_timestamp} to {end_timestamp}")
            return []

        base64_frames = []
        for frame_idx in frame_selection:
            # Seek to the specific frame
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ret, frame = cap.read()

            if ret:
                # Convert frame to base64 JPEG
                _, buffer = cv2.imencode(".jpg", frame)
                base64_frames.append(base64.b64encode(buffer.tobytes()).decode("utf-8"))
            else:
                raise ValueError(f"Could not read frame {frame_idx} from {video_path}")

        return base64_frames
    except Exception as e:
        raise RuntimeError(f"Error selecting frames from video {video_path}: {e}") from None
    finally:
        cap.release()


def has_nvidia_gpu() -> bool:
    """Simple check for NVIDIA GPU"""
    return (
        shutil.which("nvidia-smi") is not None and subprocess.run(["nvidia-smi"], capture_output=True).returncode == 0
    )
