# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Pixel helpers for live-picture bbox overlay assertions (ffmpeg RGB only)."""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Dict, Tuple

logger = logging.getLogger(__name__)


def jpeg_to_rgb(jpeg_path: Path) -> Tuple[bytes, int, int]:
    """Decode a JPEG to raw rgb24 via ffmpeg; return (rgb, width, height)."""
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x",
            str(jpeg_path),
        ],
        capture_output=True, text=True, check=True, timeout=15,
    )
    w_s, h_s = probe.stdout.strip().split("x")
    width, height = int(w_s), int(h_s)
    rgb = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-i", str(jpeg_path),
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
        ],
        capture_output=True, check=True, timeout=30,
    ).stdout
    expected = width * height * 3
    if len(rgb) < expected:
        raise RuntimeError(f"short RGB frame: got {len(rgb)}, expected {expected}")
    return rgb[:expected], width, height


def scale_box(
    box: Tuple[int, int, int, int],
    src_w: int,
    src_h: int,
    dst_w: int,
    dst_h: int,
) -> Tuple[int, int, int, int]:
    """Map a pixel box from source metadata coords into JPEG pixel coords."""
    sx = dst_w / float(src_w)
    sy = dst_h / float(src_h)
    l, t, r, b = box
    return (
        int(round(l * sx)),
        int(round(t * sy)),
        int(round(r * sx)),
        int(round(b * sy)),
    )


def count_border_matching(
    rgb: bytes,
    width: int,
    height: int,
    box: Tuple[int, int, int, int],
    target: Tuple[int, int, int],
    tol: int = 55,
    margin: int = 8,
) -> Tuple[int, int]:
    """Count target-colored pixels on the box border annulus vs an off-box patch."""
    el, et, er, eb = box
    tr, tg, tb = target

    def match(o: int) -> bool:
        return (
            abs(rgb[o] - tr) <= tol
            and abs(rgb[o + 1] - tg) <= tol
            and abs(rgb[o + 2] - tb) <= tol
        )

    border = 0
    for y in range(max(0, et - margin), min(height, eb + margin)):
        base = y * width * 3
        for x in range(max(0, el - margin), min(width, er + margin)):
            on_v = abs(x - el) <= margin or abs(x - er) <= margin
            on_h = abs(y - et) <= margin or abs(y - eb) <= margin
            if (on_v or on_h) and match(base + x * 3):
                border += 1

    fw = max(1, er - el)
    fx = max(0, el - 3 * margin - fw)
    fy = et
    floor = 0
    for y in range(fy, min(height, fy + max(1, eb - et))):
        base = y * width * 3
        for x in range(fx, min(width, fx + fw)):
            if match(base + x * 3):
                floor += 1
    return border, floor


def assert_live_box_border(
    snapshot_rgb: bytes,
    width: int,
    height: int,
    box: Tuple[int, int, int, int],
    target_rgb: Tuple[int, int, int],
    min_border: int = 200,
    tol: int = 55,
) -> Dict:
    """Assert a colored box outline is drawn at ``box`` in a live overlay JPEG."""
    border, floor = count_border_matching(
        snapshot_rgb, width, height, box, target_rgb, tol=tol,
    )
    logger.info(
        "live box border matching px=%d, off-box floor px=%d (target=%s box=%s)",
        border, floor, target_rgb, box,
    )
    assert border >= min_border, (
        f"no box outline at expected border {box}: matching px {border} < {min_border}"
    )
    assert border > floor * 5 + min_border // 2, (
        f"box border not distinct from floor: border={border} floor={floor}"
    )
    return {"border": border, "floor": floor}
