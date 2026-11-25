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

"""Draw camera centers as dots on a BEV floor-plan map.

Reads a ``camera_centers.json`` file (produced by
``extract_camera_centers.py``) and the dataset's calibration JSON for the
world->pixel transform, then renders a centers-only overlay onto
``map.png``.

Pixel mapping (matches ``label_map_cameras.sensor_to_pixel`` in
``auto-magic-calib-dev/core/camera_chaining/label_map_cameras.py``)::

    px =     (x_world + tx) * sf
    py = H - (y_world + ty) * sf       # Y-flip: bottom-left -> top-left origin

where ``tx, ty = sensor.translationToGlobalCoordinates`` and
``sf = sensor.scaleFactor`` (constant across all sensors in a calibration).

Two rendering "ways" are exposed via ``--mode``:

  * ``dots``   — colored dots only (default; matches the original
                 ``map_labeled_centers.png`` style).
  * ``labeled`` — same dots plus a small camera-id label per marker.

Usage::

    draw_camera_centers_overlay.py <dataset_dir> [--centers FILENAME]
                                   [--calib FILENAME] [--map FILENAME]
                                   [--out FILENAME] [--mode {dots,labeled}]
                                   [--radius PIXELS] [--color B,G,R]

Examples::

    # Default: read camera_centers.json + calibration*.json + map.png
    # from the dataset folder and write map_labeled_centers.png there.
    python draw_camera_centers_overlay.py Warehouse_Synthetic_030624

    # Add per-dot labels.
    python draw_camera_centers_overlay.py Foxconn_Synthetic_101825 --mode labeled

    # Custom calibration and a different output filename.
    python draw_camera_centers_overlay.py Warehouse_Synthetic_072925 \
        --calib calibration_12groups.json --out centers_12g.png
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Literal

import cv2
import numpy as np

DEFAULT_CALIB_NAMES: tuple[str, ...] = (
    "calibration.json",
    "calibration_grouped.json",
)
DEFAULT_CENTERS_NAME: str = "camera_centers.json"
DEFAULT_MAP_NAME: str = "map.png"
DEFAULT_OUT_NAME: str = "map_labeled_centers.png"

DEFAULT_DOT_RADIUS: int = 6
DEFAULT_DOT_COLOR_BGR: tuple[int, int, int] = (0, 0, 255)
DEFAULT_OUTLINE_COLOR_BGR: tuple[int, int, int] = (0, 0, 0)
DEFAULT_OUTLINE_THICK: int = 2

ModeLiteral = Literal["dots", "labeled"]
MODES: tuple[str, ...] = ("dots", "labeled")


def find_calibration_file(dataset_dir: Path, override: str | None) -> Path:
    """Locate the calibration JSON inside ``dataset_dir`` (or use override)."""
    if override is not None:
        p = (dataset_dir / override).resolve()
        if not p.exists():
            raise FileNotFoundError(f"calibration not found: {p}")
        return p
    for name in DEFAULT_CALIB_NAMES:
        p = dataset_dir / name
        if p.exists():
            return p.resolve()
    raise FileNotFoundError(
        f"no calibration JSON in {dataset_dir} "
        f"(looked for {', '.join(DEFAULT_CALIB_NAMES)}); pass --calib FILENAME"
    )


def parse_color(text: str) -> tuple[int, int, int]:
    """Parse a ``B,G,R`` triple of 0-255 ints."""
    parts = [p.strip() for p in text.split(",")]
    if len(parts) != 3:
        raise argparse.ArgumentTypeError(
            f"expected 3 comma-separated ints (B,G,R); got {text!r}"
        )
    try:
        b, g, r = (int(p) for p in parts)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"invalid color {text!r}: {e}") from e
    for v in (b, g, r):
        if not 0 <= v <= 255:
            raise argparse.ArgumentTypeError(
                f"color components must be in [0, 255]; got {text!r}"
            )
    return (b, g, r)


def short_label(camera_id: str) -> str:
    """Return a compact label suitable for on-map display."""
    return camera_id.split("_")[-1] if "_" in camera_id else camera_id


def draw_overlay(
    img: np.ndarray,
    centers: dict[str, dict[str, float]],
    tx: float,
    ty: float,
    sf: float,
    *,
    mode: ModeLiteral = "dots",
    dot_radius: int = DEFAULT_DOT_RADIUS,
    dot_color: tuple[int, int, int] = DEFAULT_DOT_COLOR_BGR,
    outline_color: tuple[int, int, int] = DEFAULT_OUTLINE_COLOR_BGR,
    outline_thick: int = DEFAULT_OUTLINE_THICK,
) -> tuple[int, int]:
    """Draw centers onto ``img`` in place. Returns ``(drawn, off_map)`` counts."""
    H, W = img.shape[:2]
    drawn, off_map = 0, 0
    for cam_id, c in centers.items():
        px = (c["x"] + tx) * sf
        py = H - (c["y"] + ty) * sf
        ix, iy = int(round(px)), int(round(py))
        if not (0 <= ix < W and 0 <= iy < H):
            off_map += 1
            continue
        cv2.circle(img, (ix, iy), dot_radius + 1, outline_color,
                   outline_thick, cv2.LINE_AA)
        cv2.circle(img, (ix, iy), dot_radius, dot_color, -1, cv2.LINE_AA)
        if mode == "labeled":
            label = short_label(cam_id)
            font = cv2.FONT_HERSHEY_SIMPLEX
            font_scale = 0.32
            thickness = 1
            cv2.putText(img, label, (ix + dot_radius + 2, iy - dot_radius - 2),
                        font, font_scale, outline_color, thickness + 1, cv2.LINE_AA)
            cv2.putText(img, label, (ix + dot_radius + 2, iy - dot_radius - 2),
                        font, font_scale, dot_color, thickness, cv2.LINE_AA)
        drawn += 1
    return drawn, off_map


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "dataset_dir",
        type=Path,
        help="Dataset directory containing camera_centers.json, the "
             "calibration JSON, and map.png.",
    )
    parser.add_argument(
        "--centers",
        type=str,
        default=DEFAULT_CENTERS_NAME,
        help=f"Centers JSON filename inside dataset_dir "
             f"(default: {DEFAULT_CENTERS_NAME}).",
    )
    parser.add_argument(
        "--calib",
        type=str,
        default=None,
        help=f"Calibration JSON filename inside dataset_dir "
             f"(default: auto-detect {', '.join(DEFAULT_CALIB_NAMES)}).",
    )
    parser.add_argument(
        "--map",
        type=str,
        default=DEFAULT_MAP_NAME,
        help=f"BEV map filename inside dataset_dir (default: {DEFAULT_MAP_NAME}).",
    )
    parser.add_argument(
        "--out",
        type=str,
        default=DEFAULT_OUT_NAME,
        help=f"Output overlay filename inside dataset_dir "
             f"(default: {DEFAULT_OUT_NAME}).",
    )
    parser.add_argument(
        "--mode",
        choices=MODES,
        default="dots",
        help="Render mode. dots: colored circles only (default). "
             "labeled: dots + short camera-id labels.",
    )
    parser.add_argument(
        "--radius",
        type=int,
        default=DEFAULT_DOT_RADIUS,
        help=f"Dot radius in pixels (default: {DEFAULT_DOT_RADIUS}).",
    )
    parser.add_argument(
        "--color",
        type=parse_color,
        default=DEFAULT_DOT_COLOR_BGR,
        help="Dot color as B,G,R (default: 0,0,255 = red).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    dataset_dir: Path = args.dataset_dir.resolve()
    if not dataset_dir.is_dir():
        print(f"ERROR: not a directory: {dataset_dir}", file=sys.stderr)
        return 2

    centers_path = (dataset_dir / args.centers).resolve()
    map_path = (dataset_dir / args.map).resolve()
    out_path = (dataset_dir / args.out).resolve()
    calib_path = find_calibration_file(dataset_dir, args.calib)

    if not centers_path.exists():
        print(
            f"ERROR: centers file not found: {centers_path}\n"
            f"       Run extract_camera_centers.py first.",
            file=sys.stderr,
        )
        return 1
    if not map_path.exists():
        print(f"ERROR: map not found: {map_path}", file=sys.stderr)
        return 1

    print(f"Dataset:      {dataset_dir}")
    print(f"Centers:      {centers_path}")
    print(f"Calibration:  {calib_path}")
    print(f"Map:          {map_path}")
    print(f"Mode:         {args.mode}")

    centers: dict[str, dict[str, float]] = json.loads(centers_path.read_text())
    if not centers:
        print("ERROR: centers file is empty", file=sys.stderr)
        return 1

    calib = json.loads(calib_path.read_text())
    any_cam = next(
        (s for s in calib.get("sensors", []) if s.get("type") == "camera"),
        None,
    )
    if any_cam is None:
        print(f"ERROR: no camera sensors in {calib_path}", file=sys.stderr)
        return 1
    tx = float(any_cam["translationToGlobalCoordinates"]["x"])
    ty = float(any_cam["translationToGlobalCoordinates"]["y"])
    sf = float(any_cam["scaleFactor"])

    img = cv2.imread(str(map_path))
    if img is None:
        print(f"ERROR: failed to read map: {map_path}", file=sys.stderr)
        return 1
    H, W = img.shape[:2]
    print(f"Transform:    tx={tx}, ty={ty}, sf={sf}, map={W}x{H}")

    drawn, off_map = draw_overlay(
        img,
        centers,
        tx=tx,
        ty=ty,
        sf=sf,
        mode=args.mode,
        dot_radius=args.radius,
        dot_color=args.color,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(out_path), img):
        print(f"ERROR: cv2.imwrite failed for {out_path}", file=sys.stderr)
        return 1
    print(
        f"Wrote:        {out_path}  ({drawn}/{len(centers)} centers drawn"
        + (f", {off_map} off-map)" if off_map else ")")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
