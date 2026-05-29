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

"""
3D Bounding Box Visualization Tool

Standalone CLI for projecting and drawing 3D bounding boxes on camera
images.  All rendering logic lives in
``spatialai_data_utils.visualization.render``; this script only handles
argument parsing and invokes the library.

Supported input formats (pass exactly one):

    --nvschema_path  : NVSchema JSON-lines model results.
                       Requires --calib_path and --data_path.
    --gt_json_aicity_path   : ``ground_truth.json`` file.
                       Requires --data_path; --calib_path is optional
                       (auto-detected from the scene directory if omitted).
    --data_pkl       : sparse4d-style pkl bundling calibration,
                       per-frame image paths, and GT annotations all
                       in one file.  --calib_path and --data_path must
                       NOT be combined with this.

Recentering note:
  When a model is trained with recentered coordinates (group origin at
  0, 0), predictions live in a shifted coordinate frame.  Pass
  ``--recentering`` with ``--calib_path`` to shift the extrinsics so
  the group origin maps to (0, 0).  Not needed with ``--data_pkl`` —
  the pkl's extrinsics already have recentering baked in.

SECURITY WARNING — ``--data_pkl``:
  The ``--data_pkl`` mode loads the file via :func:`pickle.load`, which
  can execute arbitrary code on malicious input (CWE-502).  **Only pass
  ``.pkl`` files from trusted sources.**  Verify the SHA-256 / provenance
  of any pkl you didn't generate yourself before using this flag.  A
  future release will migrate this format to a safer container
  (JSON / HDF5 / protobuf).

Example usage:

  # NVSchema model results with a calibration JSON
  python tools/visualization/draw_3dbbox_batch.py \\
      --nvschema_path results/scene_001.json \\
      --calib_path   data/mtmc/Scene/calibration_clustered.json \\
      --data_path    data/mtmc/Scene \\
      --output_dir   output/viz \\
      --recentering --h5_file

  # Ground-truth JSON (calibration auto-detected from the scene dir)
  python tools/visualization/draw_3dbbox_batch.py \\
      --gt_json_aicity_path data/mtmc/Scene/ground_truth.json \\
      --data_path    data/mtmc/Scene \\
      --output_dir   output/viz

  # sparse4d-style pkl (calib + images + GT all bundled)
  python tools/visualization/draw_3dbbox_batch.py \\
      --data_pkl   data/mtmc/anno_pkls/.../scene_infos_test.pkl \\
      --output_dir output/viz

  # Single camera
  python tools/visualization/draw_3dbbox_batch.py \\
      --nvschema_path results/scene_001.json \\
      --calib_path    calibration.json \\
      --data_path     data/mtmc/Scene \\
      --output_dir    output/viz \\
      --sensor_ids Camera_01
"""

import argparse

from spatialai_data_utils.visualization.render import visualize_3dbbox


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the 3D bounding box visualization tool."""
    parser = argparse.ArgumentParser(
        description="Visualize 3D bounding boxes on camera images",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # --- Exactly one of these three source arguments must be provided. ---
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--nvschema_path", type=str, default=None,
        help="Path to an NVSchema JSON-lines model results file",
    )
    source.add_argument(
        "--gt_json_aicity_path", type=str, default=None,
        help="Path to a scene ground_truth.json file",
    )
    source.add_argument(
        "--data_pkl", type=str, default=None,
        help="Path to a sparse4d-style data pkl (calib + image paths + GT "
             "in one file). SECURITY: loaded via pickle.load — only pass "
             "files from trusted sources (CWE-502).",
    )

    parser.add_argument(
        "--output_dir", type=str, required=True,
        help="Directory where annotated images are written",
    )
    parser.add_argument(
        "--data_path", type=str, default=None,
        help="Scene root containing per-camera image folders (required for "
             "nvschema/gt_json_aicity modes; ignored with --data_pkl)",
    )
    parser.add_argument(
        "--calib_path", type=str, default=None,
        help="Path to calibration JSON (required with --nvschema_path; "
             "optional with --gt_json_aicity_path; forbidden with --data_pkl)",
    )
    parser.add_argument(
        "--sensor_ids", type=str, nargs="+", default=None,
        help="Camera names to render (default: all cameras in the calibration)",
    )
    parser.add_argument(
        "--conf_thresh", type=float, default=0.1,
        help="Confidence threshold for filtering detections (default: 0.1)",
    )
    parser.add_argument(
        "--n_frames", type=int, default=-1,
        help="Max frames to render (-1 = all, default: -1)",
    )
    parser.add_argument(
        "--h5_file", action="store_true",
        help="Load images from .h5 files instead of loose JPGs/PNGs",
    )
    parser.add_argument(
        "--recentering", action="store_true",
        help="Apply group-origin recentering to the calibration.  "
             "Pass this in --nvschema_path mode when the model was "
             "trained with recentered targets.  DO NOT pass this in "
             "--gt_json_aicity_path mode: GT data is in world frame, so "
             "recentering the calibration shifts the projection "
             "into a frame the boxes don't live in.  --data_pkl "
             "mode already has recentering baked into the pkl's "
             "extrinsics, so do not pass this there either.",
    )
    parser.add_argument(
        "--calib_mode", type=str, default="aic25", choices=["aic24", "aic25"],
        help="Calibration format (default: aic25; used for gt_json_aicity mode)",
    )
    parser.add_argument(
        "--object_class_tag", type=str, default="warehouse",
        help="Object-class config name (built-in, e.g. 'warehouse', "
             "'default', 'scout') or path to a .py config file.  "
             "Drives both (1) class filtering — boxes whose 'type' "
             "isn't recognised by the config are dropped — and "
             "(2) display-name remap for the kept boxes' labels.  "
             "Pass 'none' to disable both (every box drawn with its "
             "raw 'type' string).  Default: 'warehouse'.",
    )
    parser.add_argument(
        "--color_by", type=str, default="track_id",
        choices=("track_id", "class"),
        help="Auto-coloring mode for the wireframes: 'track_id' "
             "(default) gives each object ``id`` its own colour "
             "cycled through COLOR_MAP — matches the per-track "
             "convention used by gt_json_aicity / pkl workflows.  'class' "
             "walks the palette in FIFO order per frame (first "
             "class seen → slot 0, next → slot 1, …), giving "
             "maximally-separated colours when a scene has many "
             "instances but few classes.  (The focused NVSchema-"
             "only CLI ``draw_3dbbox.py`` defaults to ``class``.)",
    )
    return parser.parse_args()


def main() -> None:
    """Parse CLI arguments and run the 3D bounding box visualization pipeline."""
    args = parse_args()

    object_class_tag = (
        args.object_class_tag
        if args.object_class_tag and args.object_class_tag.lower() != "none"
        else None
    )

    visualize_3dbbox(
        output_dir=args.output_dir,
        nvschema_path=args.nvschema_path,
        gt_json_aicity_path=args.gt_json_aicity_path,
        data_pkl=args.data_pkl,
        data_path=args.data_path,
        calib_path=args.calib_path,
        sensor_ids=args.sensor_ids,
        conf_thresh=args.conf_thresh,
        n_frames=args.n_frames,
        h5_file=args.h5_file,
        recentering=args.recentering,
        calib_mode=args.calib_mode,
        object_class_tag=object_class_tag,
        color_by=args.color_by,
    )


if __name__ == "__main__":
    main()
