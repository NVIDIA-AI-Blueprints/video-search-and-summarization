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

"""Render dual-view camera placement from a calibration JSON file.

Outputs a static PNG sequence by default: all cameras plus per-group images
when calibration groups exist. The 3D panel uses scene-aware auto view angles
unless ``--elev`` / ``--azim`` are provided; ``--interactive_3d`` can open the
matplotlib window for manual rotation without adding extra dependencies.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _resolve_map_file(calib_path: Path, map_file_arg: str | None) -> Path | None:
    """Resolve BEV background map path from CLI args or scene defaults."""

    if map_file_arg:
        map_path = Path(map_file_arg)
        if not map_path.exists():
            logger.warning(
                "Requested --map_file does not exist (%s); BEV background disabled.",
                map_path,
            )
            return None
        return map_path

    candidate = calib_path.parent / "map.png"
    if candidate.exists():
        return candidate
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Draw dual-view camera placement from calibration.json "
            "(3D frustums + BEV coverage)."
        )
    )
    parser.add_argument(
        "--calib_path",
        type=str,
        required=True,
        help="Path to calibration JSON file (usually calibration.json).",
    )
    parser.add_argument(
        "--output_path",
        type=str,
        default="camera_placement",
        help=(
            "Output path. In default sequence mode this is a directory "
            "(writes all_cameras.png + groups/*.png). In --single_output mode "
            "this is a single image path."
        ),
    )
    parser.add_argument(
        "--single_output",
        action="store_true",
        help="Write only one dual-view image instead of sequence PNGs.",
    )
    parser.add_argument(
        "--sensor_ids",
        nargs="+",
        default=None,
        help="Optional camera ids to render (space separated).",
    )
    parser.add_argument(
        "--group_names",
        nargs="+",
        default=None,
        help="Optional calibration group names to render in sequence mode.",
    )
    parser.add_argument(
        "--frustum_depth",
        type=float,
        default=3.0,
        help="Frustum depth in world units (default: 3.0).",
    )
    parser.add_argument(
        "--draw_local_axes",
        action="store_true",
        help="Draw each camera's local XYZ axes.",
    )
    parser.add_argument(
        "--hide_labels",
        action="store_true",
        help="Hide camera id labels.",
    )
    parser.add_argument(
        "--map_file",
        type=str,
        default=None,
        help="Optional map image path for BEV panel background.",
    )
    parser.add_argument(
        "--map_mask_alpha",
        type=float,
        default=0.35,
        help=(
            "Dark-mask opacity applied on top of BEV map background "
            "(0 disables mask, 1 fully dark). Default: 0.35."
        ),
    )
    parser.add_argument(
        "--bev_source",
        choices=("auto", "attributes", "frustum"),
        default="frustum",
        help=(
            "BEV polygon source: frustum (generated only, default), "
            "attributes (WKT only), or auto (attributes then frustum fallback)."
        ),
    )
    parser.add_argument(
        "--height_range",
        nargs=2,
        type=float,
        default=(1.0, 3.0),
        metavar=("MIN_Z", "MAX_Z"),
        help="Height range for frustum-to-ground intersection in BEV fallback.",
    )
    parser.add_argument(
        "--max_camera_distance",
        type=float,
        default=20.0,
        help="Max frustum distance for BEV polygon fallback.",
    )
    parser.add_argument(
        "--recentering",
        action="store_true",
        help="Apply group-origin recentering while loading calibration.",
    )
    parser.add_argument(
        "--title",
        type=str,
        default="Camera 3D Placement",
        help="Plot title.",
    )
    parser.add_argument(
        "--elev",
        type=float,
        default=None,
        help="3D view elevation angle in degrees. Defaults to scene-aware auto view.",
    )
    parser.add_argument(
        "--azim",
        type=float,
        default=None,
        help="3D view azimuth angle in degrees. Defaults to scene-aware auto view.",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=180,
        help="Output image DPI.",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="Show interactive window in addition to writing the image.",
    )
    parser.add_argument(
        "--interactive_3d",
        action="store_true",
        help=(
            "Enable matplotlib interactive mode so the 3D panel can be "
            "rotated/zoomed in a GUI window. Requires a display-capable backend."
        ),
    )
    return parser.parse_args()


def _configure_matplotlib_backend(interactive: bool) -> None:
    """Select matplotlib backend before importing plotting modules."""

    import matplotlib

    if not interactive:
        matplotlib.use("Agg")
        return

    current_backend = matplotlib.get_backend().lower()
    if not current_backend.endswith("agg"):
        return

    for backend in ("QtAgg", "TkAgg", "GTK3Agg", "WXAgg"):
        try:
            matplotlib.use(backend, force=True)
            logger.info("Using matplotlib backend: %s", backend)
            return
        except Exception:
            continue

    logger.warning(
        "Could not switch from matplotlib backend %r to a GUI backend. "
        "The interactive window may not open; verify DISPLAY/Wayland and GUI "
        "backend support in this conda environment.",
        matplotlib.get_backend(),
    )


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    _configure_matplotlib_backend(interactive=args.show or args.interactive_3d)

    from spatialai_data_utils.visualization.camera_placement import (
        CameraPlacementBevStyle,
        CameraPlacementStyle,
        load_camera_placement_context,
        render_camera_placement,
        render_camera_placement_sequence,
    )

    calib_path = Path(args.calib_path)
    output_path = Path(args.output_path)
    map_file = _resolve_map_file(calib_path, args.map_file)

    logger.info("=== Camera Placement Dual-View Visualization ===")
    logger.info("  Calib  : %s", calib_path)
    logger.info("  Output : %s", output_path)
    logger.info("  Mode   : %s", "single_output" if args.single_output else "sequence")
    if args.sensor_ids:
        logger.info("  Sensors: %s", args.sensor_ids)
    if args.group_names:
        logger.info("  Groups : %s", args.group_names)
    if map_file is not None:
        source = "auto-detected map.png" if args.map_file is None else "cli"
        logger.info("  Map    : %s (%s)", map_file, source)
    logger.info("======================================")

    context = load_camera_placement_context(
        calibration_json_path=calib_path,
        sensor_ids=args.sensor_ids,
        recentering=args.recentering,
    )
    if not context.camera_poses:
        raise ValueError(
            "No cameras were loaded. Check calibration and --sensor_ids filters."
        )

    style = CameraPlacementStyle(
        frustum_depth=args.frustum_depth,
        draw_labels=not args.hide_labels,
        draw_local_axes=args.draw_local_axes,
    )
    bev_style = CameraPlacementBevStyle(
        source_mode=args.bev_source,
        height_range=(float(args.height_range[0]), float(args.height_range[1])),
        max_camera_distance=args.max_camera_distance,
        map_mask_alpha=float(min(max(args.map_mask_alpha, 0.0), 1.0)),
    )
    footer_text = f"Calibration: {calib_path}"

    if args.single_output:
        single_output = output_path
        if single_output.suffix == "":
            single_output = single_output.with_suffix(".png")
        render_camera_placement(
            context.camera_poses,
            output_path=single_output,
            sensors_by_id=context.sensors_by_id,
            calibration_data=context.calibration_data,
            map_file=map_file,
            show=args.show or args.interactive_3d,
            title=args.title,
            elev=args.elev,
            azim=args.azim,
            dpi=args.dpi,
            style=style,
            bev_style=bev_style,
            footer_text=footer_text,
            close=not (args.show or args.interactive_3d),
        )
        logger.info("Saved camera placement image: %s", single_output)
        return

    outputs = render_camera_placement_sequence(
        context.camera_poses,
        output_dir=output_path,
        sensors_by_id=context.sensors_by_id,
        cams_by_group=context.cams_by_group,
        calibration_data=context.calibration_data,
        map_file=map_file,
        group_names=args.group_names,
        include_all_cameras=True,
        title=args.title,
        elev=args.elev,
        azim=args.azim,
        dpi=args.dpi,
        style=style,
        bev_style=bev_style,
        footer_text=footer_text,
    )
    if args.show or args.interactive_3d:
        import matplotlib.pyplot as plt

        # Close saved sequence figures so the GUI shows one clean interactive view.
        plt.close("all")
        logger.info("Opening interactive matplotlib view for 3D inspection...")
        render_camera_placement(
            context.camera_poses,
            output_path=None,
            sensors_by_id=context.sensors_by_id,
            calibration_data=context.calibration_data,
            map_file=map_file,
            show=True,
            title=args.title,
            elev=args.elev,
            azim=args.azim,
            dpi=args.dpi,
            style=style,
            bev_style=bev_style,
            footer_text=footer_text,
        )
    logger.info("Saved %d camera placement image(s):", len(outputs))
    for key, path in outputs.items():
        logger.info("  %s -> %s", key, path)


if __name__ == "__main__":
    main()
