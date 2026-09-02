# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, List, Tuple

import numpy as np


@dataclass(frozen=True)
class ModelInfoEntry:
    class_id: int
    height_raw: str
    radius_raw: str


def _parse_float_token(token: str, field_name: str) -> None:
    try:
        float(token)
    except ValueError as exc:
        raise ValueError(f"Invalid {field_name} value '{token}'. Must be numeric.") from exc


def _parse_model_args(model_args: List[List[str]]) -> List[ModelInfoEntry]:
    entries: List[ModelInfoEntry] = []
    for triple in model_args:
        class_id_raw, height_raw, radius_raw = triple
        try:
            class_id = int(class_id_raw)
        except ValueError as exc:
            raise ValueError(
                f"Invalid classID value '{class_id_raw}'. classID must be an integer."
            ) from exc

        _parse_float_token(height_raw, "height")
        _parse_float_token(radius_raw, "radius")
        entries.append(
            ModelInfoEntry(class_id=class_id, height_raw=height_raw, radius_raw=radius_raw)
        )
    return entries


def _format_number(value: Any) -> str:
    if isinstance(value, bool):
        raise ValueError("Boolean value found where numeric projection matrix entry was expected.")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    raise ValueError(f"Unsupported projection matrix entry type: {type(value).__name__}")


def _as_matrix(
    value: Any, shape: Tuple[int, int], sensor_id: str, field: str
) -> np.ndarray:
    """Validate a nested-list matrix from calibration.json and return it as float64.

    Every projection input goes through here, so no calibration field reaches the
    projection maths unchecked.
    """
    rows, cols = shape
    if (
        not isinstance(value, list)
        or len(value) != rows
        or not all(isinstance(row, list) and len(row) == cols for row in value)
    ):
        raise ValueError(
            f"Sensor '{sensor_id}' has invalid {field} shape; expected {rows}x{cols}."
        )
    for row in value:
        for entry in row:
            if isinstance(entry, bool) or not isinstance(entry, (int, float)):
                raise ValueError(
                    f"Sensor '{sensor_id}' has non-numeric {field} entry {entry!r}."
                )
    matrix = np.array(value, dtype=float)
    if not np.all(np.isfinite(matrix)):
        raise ValueError(f"Sensor '{sensor_id}' has a non-finite {field} entry.")
    return matrix


# Plausibility bounds on the decomposed intrinsics. Deliberately wide: they are
# here to catch a matrix that is not a camera at all -- wrong units, transposed
# inputs, a fit that collapsed -- not to judge calibration quality. Deployment
# calibrations sit at fx = fy = 900-1700 px, skew under 4 px, principal point near
# (960, 540) and cond(M) = 2.4e3, so each bound below has orders of margin.
_FOCAL_PX_RANGE = (1.0, 1.0e5)
_MAX_FOCAL_ASPECT = 10.0
_MAX_SKEW_FRACTION = 0.1
_MAX_PRINCIPAL_POINT_PX = 1.0e5
_MAX_CONDITION_NUMBER = 1.0e10
_MAX_RECONSTRUCTION_RESIDUAL = 1.0e-6

# Row and column reversal, used to turn numpy's lower Cholesky factor into the
# upper triangular one an RQ factorisation needs.
_REVERSE = np.array([[0.0, 0.0, 1.0], [0.0, 1.0, 0.0], [1.0, 0.0, 0.0]])


def _decompose_projection(
    P: np.ndarray,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """P -> (K, R, C): intrinsics scaled to K[2,2]=1, rotation, camera centre.

    P = K [R | -R C], so its left 3x3 block M is K R, and separating the two is an
    RQ factorisation: M M^T = K K^T, whose upper triangular Cholesky factor is K.

    The overall sign of P is free, since P and -P project identically, and fitted
    cameraMatrix blocks in real calibrations carry either. Normalising it here is
    what leaves K with a positive diagonal and R a proper rotation.
    """
    if np.linalg.det(P[:, :3]) < 0:
        P = -P
    M = P[:, :3]
    K = _REVERSE @ np.linalg.cholesky(_REVERSE @ (M @ M.T) @ _REVERSE) @ _REVERSE
    # R and C need the unscaled K: dividing it by K[2,2] would scale R off the
    # orthonormal manifold.
    return K / K[2, 2], np.linalg.solve(K, M), -np.linalg.solve(M, P[:, 3])


def _not_a_camera(sensor_id: str, reason: str) -> ValueError:
    return ValueError(
        f"Sensor '{sensor_id}' projection matrix is not a valid camera: {reason}."
    )


def _validate_projection(P: np.ndarray, sensor_id: str) -> None:
    """Reject a projection matrix that does not decompose into a plausible camera.

    A matrix that is malformed rather than merely inaccurate corrupts 3D
    localisation silently, and surfaces far downstream: pub/sub generation
    decomposes these same matrices to derive camera positions and FOV overlap.
    Decomposing here is the cheapest check that what is about to be written is a
    camera at all.
    """
    condition = np.linalg.cond(P[:, :3])
    if not np.isfinite(condition) or condition > _MAX_CONDITION_NUMBER:
        raise _not_a_camera(
            sensor_id, f"left 3x3 block is singular or ill-conditioned (cond {condition:.3g})"
        )

    try:
        K, R, centre = _decompose_projection(P)
    except np.linalg.LinAlgError as exc:
        raise _not_a_camera(sensor_id, f"decomposition failed ({exc})") from exc

    # Rebuilding P is what establishes that the parts mean anything. The overall
    # scale is free, so it is fitted rather than assumed.
    rebuilt = K @ np.hstack([R, -R @ centre.reshape(3, 1)])
    scale = float(np.sum(P * rebuilt) / np.sum(rebuilt * rebuilt))
    residual = float(np.abs(P - scale * rebuilt).max() / np.abs(P).max())
    if residual > _MAX_RECONSTRUCTION_RESIDUAL:
        raise _not_a_camera(
            sensor_id, f"K[R|t] does not reproduce it (relative residual {residual:.3g})"
        )
    if not np.allclose(R @ R.T, np.eye(3), atol=1e-6):
        raise _not_a_camera(sensor_id, "rotation block is not orthonormal")
    if not np.all(np.isfinite(centre)):
        raise _not_a_camera(sensor_id, "camera centre is not finite")

    fx, fy, skew = K[0, 0], K[1, 1], K[0, 1]
    low, high = _FOCAL_PX_RANGE
    if not (low <= fx <= high and low <= fy <= high):
        raise _not_a_camera(
            sensor_id, f"implausible focal length in pixels (fx {fx:.4g}, fy {fy:.4g})"
        )
    if not 1.0 / _MAX_FOCAL_ASPECT <= fy / fx <= _MAX_FOCAL_ASPECT:
        raise _not_a_camera(
            sensor_id, f"implausible focal aspect ratio (fx {fx:.4g}, fy {fy:.4g})"
        )
    if abs(skew) > _MAX_SKEW_FRACTION * fx:
        raise _not_a_camera(
            sensor_id, f"implausible axis skew ({skew:.4g} against fx {fx:.4g})"
        )
    if max(abs(K[0, 2]), abs(K[1, 2])) > _MAX_PRINCIPAL_POINT_PX:
        raise _not_a_camera(
            sensor_id, f"implausible principal point ({K[0, 2]:.4g}, {K[1, 2]:.4g})"
        )


def _render_cam_info_yaml(flattened_projection: Iterable[str], model_entries: List[ModelInfoEntry]) -> str:
    lines = ["projectionMatrix_3x4_w2p:"]
    for value in flattened_projection:
        lines.append(f"- {value}")

    lines.append("")
    lines.append("modelInfo:")
    for entry in model_entries:
        lines.append(f"  - classID: {entry.class_id}")
        lines.append(f"    height: {entry.height_raw}")
        lines.append(f"    radius: {entry.radius_raw}")

    lines.append("")
    return "\n".join(lines)


def generate_cam_info_files(
    calibration_json: Path, output_dir: Path, model_entries: List[ModelInfoEntry]
) -> int:
    with calibration_json.open("r", encoding="utf-8") as fh:
        calibration = json.load(fh)

    sensors = calibration.get("sensors")
    if not isinstance(sensors, list):
        raise ValueError("calibration.json does not contain a valid 'sensors' list.")

    output_dir.mkdir(parents=True, exist_ok=True)
    generated_count = 0
    for sensor in sensors:
        if not isinstance(sensor, dict):
            continue
        if sensor.get("type") != "camera":
            continue

        sensor_id = sensor.get("id")
        camera_matrix = sensor.get("cameraMatrix")
        K, Rt = sensor.get("intrinsicMatrix"), sensor.get("extrinsicMatrix")
        if not isinstance(sensor_id, str) or not sensor_id:
            raise ValueError("Encountered camera sensor with missing/invalid 'id'.")
        # The id becomes a filename below, so it must be a single path component.
        # Rejecting separators and traversal here keeps every write inside
        # output_dir and fails before any work is done. ".." needs its own case:
        # Path("..").name is "..", so the name comparison alone lets it through.
        if sensor_id in {".", ".."} or Path(sensor_id).name != sensor_id:
            raise ValueError(
                f"Sensor id {sensor_id!r} is not a valid filename component."
            )
        if camera_matrix is None and (K is None or Rt is None):
            raise ValueError(f"Sensor '{sensor_id}' is missing 'cameraMatrix'.")

        # Prefer K @ Rt: a precomputed cameraMatrix can be a degenerate fit, exact on
        # its own correspondences yet badly wrong elsewhere.
        if K is not None and Rt is not None:
            K = _as_matrix(K, (3, 3), sensor_id, "intrinsicMatrix")
            Rt = _as_matrix(Rt, (3, 4), sensor_id, "extrinsicMatrix")
            P = K @ Rt
        else:
            P = _as_matrix(camera_matrix, (3, 4), sensor_id, "cameraMatrix")
        _validate_projection(P, sensor_id)

        # tolist() because numpy 2.x reprs a scalar as "np.float64(1.0)".
        flattened_projection = [_format_number(v) for v in P.ravel().tolist()]
        rendered_yaml = _render_cam_info_yaml(flattened_projection, model_entries)

        out_path = output_dir / f"{sensor_id}.yml"
        out_path.write_text(rendered_yaml, encoding="utf-8")
        generated_count += 1

    if generated_count == 0:
        raise ValueError("No camera sensors found in calibration.json.")

    return generated_count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate camInfo YAML files from calibration.json and repeated "
            "(classID height radius) model triples."
        )
    )
    parser.add_argument(
        "--calibration-json",
        type=Path,
        required=True,
        help="Path to input calibration.json.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory where camera YAML files will be written.",
    )
    parser.add_argument(
        "--class",
        dest="class_args",
        action="append",
        nargs=3,
        metavar=("CLASS_ID", "HEIGHT", "RADIUS"),
        required=True,
        help=(
            "Class tuple: classID height radius. "
            "Repeat this argument for each object class."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    model_entries = _parse_model_args(args.class_args)
    generated_count = generate_cam_info_files(
        calibration_json=args.calibration_json,
        output_dir=args.output_dir,
        model_entries=model_entries,
    )
    print(f"Generated {generated_count} camInfo files in: {args.output_dir}")


if __name__ == "__main__":
    main()
