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
from pathlib import Path

import cv2
from core.utils.video_capture import open_video_capture

logger = logging.getLogger(__name__)

DEFAULT_BG_CONFIG = {
    'preprocess': {
        'num_frames': 500,
        'frame_step': 30,
        'resize_width': 960,
        'resize_height': 540,
    }
}


def extract_image_from_video(
    video_path: Path,
    output_dir: Path,
    bg_config: dict | None = None,
    output_filename: str = "runner_input.jpg",
    frame_index: int | None = None,
) -> Path:
    """Extract a representative background frame from video.

    Mirrors the preprocessing in run_geocalib.py __main__.
    Uses extract_background from core/rectification if available,
    otherwise falls back to first-frame extraction.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / output_filename
    config = bg_config or DEFAULT_BG_CONFIG

    if frame_index is not None:
        cap = open_video_capture(str(video_path))
        try:
            if frame_index > 0:
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
            ret, frame = cap.read()
        finally:
            cap.release()
        if not ret or frame is None:
            raise RuntimeError(
                f"Failed to read frame {frame_index} from video: {video_path}"
            )
        if not cv2.imwrite(str(output_path), frame):
            raise RuntimeError(f"Failed to write extracted frame: {output_path}")
        logger.info("Extracted frame %d -> %s", frame_index, output_path)
        return output_path

    try:
        from run_auto_rectification import extract_background
        scale = 1.0
        bg_img = extract_background(str(video_path), config, scale)
        cv2.imwrite(str(output_path), bg_img)
        logger.info(f"Extracted background frame -> {output_path}")
    except ImportError:
        logger.warning("extract_background not available; falling back to first frame")
        cap = open_video_capture(str(video_path))
        ret, frame = cap.read()
        cap.release()
        if not ret:
            raise RuntimeError(f"Failed to read frame from video: {video_path}")
        cv2.imwrite(str(output_path), frame)
        logger.info(f"Extracted first frame -> {output_path}")

    return output_path
