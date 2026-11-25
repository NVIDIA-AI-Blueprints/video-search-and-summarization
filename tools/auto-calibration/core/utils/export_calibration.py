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
Standalone calibration export utility.

Converts AMC core output into calibration.json format without requiring
the vss-auto-calibration server. Reads directly from the project directory
structure produced by AMC core.

Expected project layout:
    <project_dir>/
        alignment_data/
            annotations.json              (optional, for ROIs/tripwires)
            layout.png                    (optional, for translationToGlobalCoordinates)
        multi_view_results/
            BA_output/
                results_ba_scaled_world/
                    camInfo_00.yaml       (projection matrices)
                    camInfo_01.yaml
                    transform_world_to_map.json  (scale factor)

Usage:
    python -m core.utils.export_calibration /path/to/project_dir
    python -m core.utils.export_calibration /path/to/project_dir --result-type vggt
    python -m core.utils.export_calibration /path/to/project_dir -o /path/to/calibration.json
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from core.utils.video_capture import open_video_capture
import yaml

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Matrix math
# ---------------------------------------------------------------------------

def decompose_projection_matrix(P: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Decompose 3x4 projection matrix P into K, R, t  (P = K[R|t]).

    Uses RQ decomposition so K is upper-triangular and R is orthogonal.

    Returns:
        (K, R, t) – intrinsic (3x3), rotation (3x3), translation (3x1).
    """
    M = P[:, :3]
    p4 = P[:, 3]

    try:
        from scipy.linalg import rq
        K, R = rq(M)
    except ImportError:
        P_rev = np.fliplr(np.flipud(M))
        Q, R_temp = np.linalg.qr(P_rev.T)
        R = np.flipud(np.fliplr(Q.T))
        K = np.flipud(np.fliplr(R_temp.T))

    if abs(K[2, 2]) < 1e-10:
        raise ValueError(f"Degenerate projection matrix: K[2,2]={K[2, 2]}")

    K = K / K[2, 2]

    T = np.diag(np.sign(np.diag(K)))
    if np.linalg.det(T) < 0:
        T[1, 1] *= -1
    K = K @ T
    R = T @ R

    if abs(np.linalg.det(R)) < 0.5:
        raise ValueError(f"Degenerate rotation: det(R)={np.linalg.det(R)}")
    if np.linalg.det(R) < 0:
        R = -R
        K = -K

    if abs(np.linalg.det(K)) < 1e-10:
        raise ValueError(f"Singular intrinsic matrix: det(K)={np.linalg.det(K)}")

    t = np.linalg.inv(K) @ p4
    return K, R, t


def compute_homography(K: np.ndarray, R: np.ndarray, t: np.ndarray) -> np.ndarray:
    """Ground-plane homography: H = K [r1 r2 t]  (z=0)."""
    Rt_ground = np.column_stack([R[:, 0], R[:, 1], t])
    return K @ Rt_ground


def project_image_to_world(
    image_points: List[Dict],
    P: np.ndarray,
    z: float = 0.0,
) -> List[Dict]:
    """Project image-space {x, y} points onto the ground plane (z=``z``)."""
    K, R, t = decompose_projection_matrix(P)
    K_inv = np.linalg.inv(K)
    R_T = R.T
    cam_center = -R_T @ t

    world_points = []
    for pt in image_points:
        ray_cam = K_inv @ np.array([pt["x"], pt["y"], 1.0])
        ray_world = R_T @ ray_cam
        if abs(ray_world[2]) > 1e-6:
            lam = (z - cam_center[2]) / ray_world[2]
            w = cam_center + lam * ray_world
            world_points.append({"x": float(w[0]), "y": float(w[1])})
        else:
            world_points.append({"x": 0.0, "y": 0.0})
    return world_points


# ---------------------------------------------------------------------------
# Annotation conversion (image → world)
# ---------------------------------------------------------------------------

def _convert_roi(roi: Dict, P: np.ndarray, index: int) -> Dict:
    image_coords = roi.get("points", roi.get("roiCoordinates", []))
    return {
        "id": f"roi-id-{index}",
        "roiCoordinates": project_image_to_world(image_coords, P),
        "restrictedObjectTypes": roi.get("restrictedObjectTypes", []),
        "confinedObjectTypes": roi.get("confinedObjectTypes", []),
    }


def _extract_line_points(obj: Dict) -> List[Dict]:
    if "points" in obj:
        return obj["points"]
    if "start" in obj and "end" in obj:
        return [obj["start"], obj["end"]]
    return []


def _convert_tripwires(
    lines: List[Dict],
    directions: List[Dict],
    P: np.ndarray,
) -> List[Dict]:
    tripwires = []
    for idx, wire in enumerate(lines):
        pts = _extract_line_points(wire)
        if len(pts) < 2:
            continue
        w = project_image_to_world(pts, P)
        obj: Dict = {
            "id": f"tripwire-id-{idx + 1}",
            "wire": {"p1": w[0], "p2": w[1]},
        }
        if idx < len(directions):
            dpts = _extract_line_points(directions[idx])
            if len(dpts) >= 2:
                d = project_image_to_world(dpts, P)
                obj["direction"] = {"p1": d[0], "p2": d[1]}
        tripwires.append(obj)
    return tripwires


# ---------------------------------------------------------------------------
# File I/O helpers
# ---------------------------------------------------------------------------

def _load_projection_matrix(yaml_path: Path) -> np.ndarray:
    with open(yaml_path, "r") as f:
        data = yaml.safe_load(f)
    return np.array(data["projectionMatrix_3x4"])


def _load_annotations(path: Path) -> Dict:
    if not path.exists():
        return {}
    with open(path, "r") as f:
        return json.load(f)


def _load_transform(path: Path) -> Optional[Dict]:
    if not path.exists():
        return None
    with open(path, "r") as f:
        return json.load(f)


def _get_video_metadata(video_path: Path) -> Dict[str, str]:
    """Return fps / frameWidth / frameHeight for a video, with safe fallback."""
    defaults = {"fps": "30", "frameWidth": "1920", "frameHeight": "1080"}
    if not video_path.exists():
        return defaults
    try:
        import cv2
        cap = open_video_capture(str(video_path))
        meta = {
            "fps": str(int(cap.get(cv2.CAP_PROP_FPS))),
            "frameWidth": str(int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))),
            "frameHeight": str(int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))),
        }
        cap.release()
        return meta
    except Exception:
        return defaults


def _get_layout_image_height(project_path: Path) -> Optional[int]:
    """Try multiple known locations for the layout image."""
    candidates = [
        project_path / "alignment_data" / "layout.png",
        project_path / "manual_adjustment" / "layout.png",
    ]
    for p in candidates:
        if not p.exists():
            continue
        try:
            import cv2
            img = cv2.imread(str(p))
            if img is not None:
                return img.shape[0]
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
# Core export
# ---------------------------------------------------------------------------

def _build_sensor(
    cam_id: str,
    display_name: str,
    P: np.ndarray,
    scale_factor: float,
    annotations_data: Dict,
    video_metadata: Dict[str, str],
    layout_height: Optional[int],
) -> Dict:
    K, R, t = decompose_projection_matrix(P)
    extrinsic = np.hstack([R, t.reshape(3, 1)])
    H_inv = np.linalg.inv(compute_homography(K, R, t))

    video_key = f"{cam_id}.mp4"
    cam_ann = annotations_data.get(video_key, {})
    rois = [_convert_roi(r, P, i + 1) for i, r in enumerate(cam_ann.get("rois", []))]
    tripwires = _convert_tripwires(
        cam_ann.get("tripwireLines", []),
        cam_ann.get("tripwireDirections", []),
        P,
    )

    y_translation = 0.0
    if layout_height is not None and scale_factor > 0:
        y_translation = layout_height / scale_factor

    return {
        "type": "camera",
        "id": display_name,
        "scaleFactor": scale_factor,
        "origin": {"lat": 0.0, "lng": 0.0},
        "geoLocation": {"lat": 0.0, "lng": 0.0},
        "coordinates": {"x": 0.0, "y": 0.0},
        "translationToGlobalCoordinates": {"x": 0.0, "y": y_translation},
        "cameraMatrix": P.tolist(),
        "intrinsicMatrix": K.tolist(),
        "extrinsicMatrix": extrinsic.tolist(),
        "homography": H_inv.tolist(),
        "attributes": [
            {"name": "fps", "value": video_metadata["fps"]},
            {"name": "depth", "value": ""},
            {"name": "fieldOfView", "value": ""},
            {"name": "direction", "value": ""},
            {"name": "source", "value": "vst"},
            {"name": "frameWidth", "value": video_metadata["frameWidth"]},
            {"name": "frameHeight", "value": video_metadata["frameHeight"]},
        ],
        "place": [],
        "imageCoordinates": [],
        "globalCoordinates": [],
        "tripwires": tripwires,
        "rois": rois,
    }


def export_calibration(
    project_dir: str | Path,
    result_type: str = "amc",
    output_path: Optional[str | Path] = None,
    camera_name_map: Optional[Dict[str, str]] = None,
) -> Path:
    """
    Convert AMC core output into calibration.json.

    Args:
        project_dir: Root project directory containing ``multi_view_results/``.
        result_type: ``"amc"`` (default) or ``"vggt"``.
        output_path: Where to write the JSON.  Defaults to
            ``<project_dir>/calibration.json``.
        camera_name_map: Optional mapping from internal camera ids
            (``cam_00``) to display names.  When ``None``, display names
            default to ``<cam_id>.mp4`` (matching server behaviour).

    Returns:
        Path to the written calibration JSON file.

    Raises:
        FileNotFoundError: If the expected results directory is missing.
        RuntimeError: If no cameras could be exported successfully.
    """
    project_dir = Path(project_dir)

    if result_type == "vggt":
        results_dir = (
            project_dir / "vggt_results" / "multi_view_results"
            / "BA_output" / "results_ba_scaled_world"
        )
    else:
        results_dir = (
            project_dir / "multi_view_results"
            / "BA_output" / "results_ba_scaled_world"
        )

    if not results_dir.exists():
        raise FileNotFoundError(f"Results directory not found: {results_dir}")

    # Scale factor from the world-to-map transform produced by BA
    transform_data = _load_transform(results_dir / "transform_world_to_map.json")
    scale_factor = transform_data["scale"] if transform_data else -1.0

    annotations = _load_annotations(
        project_dir / "alignment_data" / "annotations.json"
    )

    layout_height = _get_layout_image_height(project_dir)

    caminfo_files = sorted(results_dir.glob("camInfo_*.yaml"))
    if not caminfo_files:
        raise FileNotFoundError(f"No camInfo_*.yaml files in {results_dir}")

    camera_name_map = camera_name_map or {}
    sensors: List[Dict] = []
    errors: List[str] = []

    for cf in caminfo_files:
        cam_num = cf.stem.split("_")[1]
        cam_id = f"cam_{cam_num}"
        try:
            P = _load_projection_matrix(cf)
            video_meta = _get_video_metadata(project_dir / f"{cam_id}.mp4")
            display_name = camera_name_map.get(cam_id, f"{cam_id}.mp4")
            sensor = _build_sensor(
                cam_id, display_name, P, scale_factor,
                annotations, video_meta, layout_height,
            )
            sensors.append(sensor)
        except Exception as e:
            errors.append(f"{cam_id}: {e}")
            logger.warning("Failed to export %s: %s", cam_id, e)

    if not sensors:
        raise RuntimeError(
            "No cameras exported successfully.\n" + "\n".join(errors)
        )

    if errors:
        logger.warning(
            "%d camera(s) failed: %s", len(errors), "; ".join(errors)
        )

    calibration = {
        "version": "1.0",
        "osmURL": "",
        "calibrationType": "cartesian",
        "sensors": sensors,
    }

    if output_path is None:
        output_path = project_dir / "calibration.json"
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w") as f:
        json.dump(calibration, f, indent=2)

    logger.info(
        "Exported calibration for %d cameras → %s", len(sensors), output_path
    )
    return output_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export AMC core output to calibration.json",
    )
    parser.add_argument(
        "project_dir",
        type=Path,
        help="Root project directory (contains multi_view_results/ folder)",
    )
    parser.add_argument(
        "--result-type",
        choices=["amc", "vggt"],
        default="amc",
        help="Which result set to export (default: amc)",
    )
    parser.add_argument(
        "-o", "--output",
        type=Path,
        default=None,
        help="Output JSON path (default: <project_dir>/calibration.json)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    try:
        out = export_calibration(
            args.project_dir,
            result_type=args.result_type,
            output_path=args.output,
        )
        print(f"Calibration exported to {out}")
    except Exception as e:
        logger.error("%s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
