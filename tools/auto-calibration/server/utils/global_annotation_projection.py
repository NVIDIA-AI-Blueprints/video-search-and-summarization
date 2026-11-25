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
Project global layout ROIs/tripwires into a camera's image space for API responses (UI canvas).
Uses the same path as CalibrationExporter cartesian export (project_global_*_to_image_space),
including clipping to the video frame from metadata (CAP_PROP-style width/height).
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .calibration_exporter import (
    CalibrationExporter,
    GLOBAL_ROIS_TRIPWIRES_KEY,
    frame_dimensions_from_video_metadata,
    has_file_extension,
    strip_video_filename_extension,
)

# Global layout → camera overlay (ROIs + tripwires). Purple (#7D3C98); ROI fill looks translucent
GLOBAL_OVERLAY_COLOR = "#9B59B266"


def _result_type_for_projection(
    project_row: Dict[str, Any],
    result_type: Optional[str] = None,
) -> str:
    # Explicit UI/API selection (AMC vs VGGT) wins when provided.
    if result_type in ("amc", "vggt"):
        return result_type
    # VGGT completion also sets project_state=COMPLETED; use amc_state so VGGT-only
    # (or AMC failed + VGGT ok) still projects from vggt_results.
    if project_row.get("amc_state") == "COMPLETED":
        return "amc"
    if project_row.get("vggt_state") == "COMPLETED":
        return "vggt"
    return "amc"


def _caminfo_path_for_video(exporter: CalibrationExporter, video_stem: str) -> Optional[Path]:
    if exporter.is_single_camera:
        p = exporter.results_path / "camInfo_world.yaml"
        return p if p.exists() else None
    parts = video_stem.split("_", 1)
    if len(parts) == 2 and parts[1].isdigit():
        p = exporter.results_path / f"camInfo_{parts[1]}.yaml"
        return p if p.exists() else None
    return None


def compute_global_layout_camera_overlay(
    project_dir: Path,
    project_row: Dict[str, Any],
    video_name: str,
    result_type: Optional[str] = None,
) -> Tuple[List[Dict], List[Dict], List[Dict]]:
    """
    Returns (extra_rois, extra_tripwire_lines, extra_tripwire_directions) in UI annotation shape
    (points / start-end / color). Empty if calibration or globals are unavailable.

    result_type: optional 'amc' | 'vggt' to select which calibration matrices to use.
    When omitted, prefers AMC if completed, else VGGT.
    """
    if not has_file_extension(video_name):
        return [], [], []
    video_stem = strip_video_filename_extension(video_name)
    vc = int(project_row.get("video_files_count") or 0)
    resolved_type = _result_type_for_projection(project_row, result_type)
    exporter = CalibrationExporter(
        project_dir,
        result_type=resolved_type,
        camera_name_map=project_row.get("camera_name_map") or {},
        is_single_camera=(vc == 1),
    )
    if not exporter.results_path.exists():
        return [], [], []
    wt = exporter.load_transform_data()
    if not wt:
        return [], [], []
    ann = exporter.load_annotations()
    global_ann = ann.get(GLOBAL_ROIS_TRIPWIRES_KEY) or {}
    if not (global_ann.get("rois") or global_ann.get("tripwireLines")):
        return [], [], []
    yaml_path = _caminfo_path_for_video(exporter, video_stem)
    if not yaml_path:
        return [], [], []
    try:
        P = exporter.load_projection_matrix(yaml_path)
    except Exception:
        return [], [], []
    try:
        root_rois = exporter.build_global_root_rois(global_ann, wt)
        root_tw = exporter.pair_global_tripwires_layout_to_world(
            global_ann.get("tripwireLines", []),
            global_ann.get("tripwireDirections", []),
            wt,
        )
    except Exception:
        return [], [], []

    video_path = project_dir / video_name
    fw, fh = frame_dimensions_from_video_metadata(exporter.get_video_metadata(video_path))
    proj_rois = exporter.project_global_rois_to_image_space(root_rois, P, fw, fh)
    proj_tw = exporter.project_global_tripwires_to_image_space(root_tw, P, fw, fh)

    rois_out: List[Dict] = []
    for roi in proj_rois:
        coords = roi.get("roiCoordinates") or []
        if len(coords) < 3:
            continue
        rois_out.append(
            {
                "id": f"global-proj-{roi.get('id', 'roi')}",
                "camera_id": video_stem,
                "points": [{"x": float(p["x"]), "y": float(p["y"])} for p in coords],
                "color": GLOBAL_OVERLAY_COLOR,
            }
        )

    lines_out: List[Dict] = []
    dirs_out: List[Dict] = []
    for tw in proj_tw:
        w = tw.get("wire") or {}
        tid = tw.get("id", "tw")
        p1, p2 = w.get("p1"), w.get("p2")
        if not (p1 and p2):
            continue
        lines_out.append(
            {
                "id": f"global-proj-{tid}-line",
                "camera_id": video_stem,
                "start": {"x": float(p1["x"]), "y": float(p1["y"])},
                "end": {"x": float(p2["x"]), "y": float(p2["y"])},
                "color": GLOBAL_OVERLAY_COLOR,
            }
        )
        d = tw.get("direction") or {}
        d1, d2 = d.get("p1"), d.get("p2")
        if d1 and d2:
            dirs_out.append(
                {
                    "id": f"global-proj-{tid}-dir",
                    "camera_id": video_stem,
                    "start": {"x": float(d1["x"]), "y": float(d1["y"])},
                    "end": {"x": float(d2["x"]), "y": float(d2["y"])},
                    "color": GLOBAL_OVERLAY_COLOR,
                    "direction": "forward",
                }
            )

    return rois_out, lines_out, dirs_out


def merge_global_projected_overlay(
    base: Dict[str, Any],
    extra_rois: List[Dict],
    extra_lines: List[Dict],
    extra_dirs: List[Dict],
) -> Dict[str, Any]:
    return {
        "rois": list(base.get("rois") or []) + list(extra_rois),
        "tripwireLines": list(base.get("tripwireLines") or []) + list(extra_lines),
        "tripwireDirections": list(base.get("tripwireDirections") or []) + list(extra_dirs),
    }
