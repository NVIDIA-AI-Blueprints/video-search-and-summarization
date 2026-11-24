#!/usr/bin/env python3
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

"""Apply manual distortion parameters and produce full linear-media artifacts per camera."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import cv2
import yaml

# Allow imports from this package directory.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import distortion_models  # noqa: E402
from run_auto_rectification import (  # noqa: E402
    extract_background,
    read_config,
    rectify_image,
    rectify_video,
)

# Prefer the MS-safe NVIDIA GStreamer path (same as auto rectification).
try:
    from core.utils.video_capture import open_video_capture  # noqa: E402
except ImportError:  # pragma: no cover - local/dev fallback
    open_video_capture = None


def _write_distortion_yaml(camera_dir: Path, params: dict) -> None:
    model, k1, k2 = distortion_models.normalize_lens_param(params)
    data = distortion_models.params_dict(model, k1, k2)
    camera_dir.mkdir(parents=True, exist_ok=True)
    with open(camera_dir / "distortion.yaml", "w") as f:
        yaml.dump(data, f, sort_keys=False)


def _open_capture(path: Path):
    if open_video_capture is not None:
        return open_video_capture(str(path))
    return cv2.VideoCapture(str(path))


def commit_camera(
    input_video: Path,
    camera_dir: Path,
    params: dict,
    config: dict,
) -> None:
    camera_dir.mkdir(parents=True, exist_ok=True)
    _write_distortion_yaml(camera_dir, params)
    model, k1, k2 = distortion_models.normalize_lens_param(params)
    lens_param = distortion_models.params_dict(model, k1, k2)

    cap = _open_capture(input_video)
    try:
        if not cap.isOpened():
            raise RuntimeError(f"Could not open video: {input_video}")
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        ok_first, first_frame = cap.read()
    finally:
        cap.release()
    if not ok_first or first_frame is None:
        raise RuntimeError(f"Could not read first frame from {input_video}")
    if width <= 0 or height <= 0:
        height, width = first_frame.shape[:2]

    resize_width = config["preprocess"]["resize_width"]
    resize_height = config["preprocess"]["resize_height"]
    resize_scale = 1.0
    if width > resize_width and height > resize_height:
        resize_scale = float(resize_width) / float(width)

    try:
        bg = extract_background(str(input_video), config, resize_scale)
    except Exception:
        # Short clips / codecs that do not yield enough sampled frames:
        # fall back to the first decoded frame as background.
        bg = first_frame
        if resize_scale != 1.0:
            bg = cv2.resize(bg, None, fx=resize_scale, fy=resize_scale)
    cv2.imwrite(str(camera_dir / "background.jpg"), bg)

    rectify_video(str(input_video), lens_param, config, str(camera_dir))

    # Prefer a native-resolution preview from the original first frame.
    rectified_preview = rectify_image(first_frame, lens_param, config)
    cv2.imwrite(str(camera_dir / "rectified.jpg"), rectified_preview)


def load_params_from_auto_estimates(output_root: Path) -> dict:
    """Load cam_XX -> params from GeoCalib estimate YAMLs under output_root."""
    estimates = sorted(
        output_root.glob("cam_*/distortion_estimation/geocalib_distortion.yaml")
    )
    if not estimates:
        raise FileNotFoundError(
            f"No GeoCalib estimates under {output_root}/cam_*/distortion_estimation/"
        )
    params = {}
    for estimate in estimates:
        cam_id = estimate.parents[1].name
        with open(estimate) as f:
            params[cam_id] = yaml.safe_load(f) or {}
    return params


def main() -> None:
    parser = argparse.ArgumentParser(description="Manual rectification commit (all cameras).")
    parser.add_argument("-v", "--video-dir", required=True, help="Directory with cam_XX.mp4 uploads")
    parser.add_argument(
        "-o",
        "--output-root",
        required=True,
        help="single_view_results root (parent of cam_XX folders)",
    )
    parser.add_argument(
        "-c",
        "--config",
        required=True,
        help="rectification_config.yaml path",
    )
    parser.add_argument(
        "-p",
        "--params-json",
        help='JSON map cam_XX -> {model,k1,k2?} (optional with --from-auto-estimates)',
    )
    parser.add_argument(
        "--from-auto-estimates",
        action="store_true",
        help="Use cam_*/distortion_estimation/geocalib_distortion.yaml under -o",
    )
    args = parser.parse_args()

    if not args.params_json and not args.from_auto_estimates:
        parser.error("Provide -p/--params-json or --from-auto-estimates")

    video_dir = Path(args.video_dir).resolve()
    output_root = Path(args.output_root).resolve()
    config = read_config(str(Path(args.config).resolve()))

    if args.params_json:
        with open(Path(args.params_json).resolve()) as f:
            all_params = json.load(f)
    else:
        all_params = load_params_from_auto_estimates(output_root)

    for cam_id, params in sorted(all_params.items()):
        video_path = video_dir / f"{cam_id}.mp4"
        if not video_path.is_file():
            raise FileNotFoundError(f"Missing source video: {video_path}")
        commit_camera(video_path, output_root / cam_id, params, config)
    print("Manual rectification commit completed.")


if __name__ == "__main__":
    main()
