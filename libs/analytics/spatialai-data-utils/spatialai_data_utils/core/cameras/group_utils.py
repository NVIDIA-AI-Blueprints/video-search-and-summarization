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
Group Utilities Module

This module provides utility functions for camera group operations,
including adding group metadata to sensors.
"""

import re
import sys
import math
import logging
import random
import statistics
import itertools
import concurrent.futures
from typing import Any, Optional
from pathlib import Path
from typing import Dict, List, Tuple

from spatialai_data_utils.core.cameras.utils import (
    load_map_data,
    save_calibration_data,
)
from spatialai_data_utils.loaders.calibration import load_calib_json
from spatialai_data_utils.core.cameras.origin import (
    calculate_group_origin,
    calculate_group_origin_hybrid,
    calculate_and_update_group_origins,
)
from spatialai_data_utils.core.cameras.polygon import calculate_scene_bounds_from_calibration

logger = logging.getLogger(__name__)


def add_group_info_to_sensors(
    calibration_data,
    group_list,
    dilation_distance=1.0,
    check_sensor_id_format=False,
    use_frustum=False,
    height_range=(1.0, 3.0),
    image_size=(1920, 1080),
    max_distance=30.0,
):
    """
    Create new sensor entries with group information for each camera in the group list.

    This function processes a list of camera groups and creates duplicate sensor entries
    if a camera appears in multiple groups. Each sensor entry is enriched with group
    information including group name, origin, and dimensions.

    :param calibration_data: Dictionary containing sensor calibration data.
    :type calibration_data: dict
    :param group_list: List of camera groups, where each group is a list of sensor indices.
    :type group_list: List[List[int]]
    :param dilation_distance: Distance for dilating the group bounding box (default: 1.0 meters).
    :type dilation_distance: float
    :param check_sensor_id_format: Whether to enforce and reformat sensor IDs (default: False).
    :type check_sensor_id_format: bool
    :param use_frustum: If True, use frustum-based FOV calculation (default: False).
    :type use_frustum: bool
    :param height_range: Tuple of (min_height, max_height) for ground plane intersection (default: (1.0, 3.0)).
    :type height_range: tuple
    :param image_size: Tuple of (width, height) for image dimensions (default: (1920, 1080)).
    :type image_size: tuple
    :param max_distance: Maximum distance in meters from camera center to constrain frustum (default: 30.0m).
    :type max_distance: float
    :return: List of sensor dictionaries with group information added.
    :rtype: List[dict]
    """
    original_sensors = calibration_data["sensors"]
    new_sensors = []

    for group_idx, group_sensor_ids in enumerate(group_list):
        if use_frustum:
            # Use hybrid approach with frustum-based FOV calculation
            origin, dimensions = calculate_group_origin_hybrid(
                original_sensors,
                group_sensor_ids,
                height_range=height_range,
                image_size=image_size,
                dilation_distance=dilation_distance,
                use_frustum=True,
                max_distance=max_distance,
            )
        else:
            # Use attribute-based FOV calculation (original behavior)
            origin, dimensions = calculate_group_origin(
                original_sensors,
                group_sensor_ids,
                dilation_distance=dilation_distance,
            )

        for sensor_id in group_sensor_ids:
            new_sensor = original_sensors[sensor_id].copy()
            if check_sensor_id_format:
                # Find the corresponding sensor from the original data
                pattern = re.compile(r"Camera(?:_(\d+))?$")
                match = pattern.match(new_sensor["id"])
                if not match:
                    raise ValueError(f"Invalid sensor ID format: {new_sensor['id']}")

                index = int(match.group(1)) if match.group(1) else 0

                if index == 0:
                    new_sensor["id"] = "Camera"
                elif index < 100:
                    new_sensor["id"] = f"Camera_{index:02d}"
                else:
                    new_sensor["id"] = f"Camera_{index}"

            new_sensor["group"] = {
                "name": f"bev-sensor-{group_idx + 1}",
                "alias": f"area-{group_idx + 1}",
                "type": "bev",
                "origin": origin,
                "dimensions": dimensions,
            }
            new_sensors.append(new_sensor)

    return new_sensors


def parse_moves(values: List[str]) -> List[Tuple[str, str]]:
    """
    Parse CLI move strings of the form camera_id:group_name into tuples.
    """
    moves: List[Tuple[str, str]] = []
    for val in values:
        if ":" not in val:
            raise ValueError(
                f"Invalid --move format '{val}', expected camera_id:group_name"
            )
        cam, grp = val.split(":", 1)
        cam = cam.strip()
        grp = grp.strip()
        if not cam or not grp:
            raise ValueError(
                f"Invalid --move entry '{val}', camera_id or group_name empty"
            )
        moves.append((cam, grp))
    return moves


def apply_group_reassignments(
    calibration: Dict,
    moves: List[Tuple[str, str]],
    strict: bool = False,
) -> Tuple[int, List[str]]:
    """
    Reassign cameras to existing groups using templates from current sensors.

    :param calibration: Calibration data containing sensors.
    :param moves: List of (camera_id, target_group_name) tuples.
    :param strict: If True, raise on missing camera/group; otherwise warn and skip.
    :return: (updated_count, list_of_warnings)
    """
    sensors: List[Dict] = calibration.get("sensors", [])
    id_to_sensor = {s.get("id"): s for s in sensors if "id" in s}

    templates: Dict[str, Dict] = {}
    for sensor in sensors:
        grp = sensor.get("group")
        name = grp.get("name") if grp else None
        if name:
            templates.setdefault(name, grp)

    warnings: List[str] = []
    updated = 0
    for cam_id, target_group in moves:
        sensor = id_to_sensor.get(cam_id)
        if sensor is None:
            msg = f"Camera '{cam_id}' not found; skipped."
            if strict:
                raise KeyError(msg)
            warnings.append(msg)
            continue

        template = templates.get(target_group)
        if template is None:
            msg = f"Target group '{target_group}' not found; skipped camera '{cam_id}'."
            if strict:
                raise KeyError(msg)
            warnings.append(msg)
            continue

        sensor["group"] = template
        updated += 1
        logger.info("Reassigned %s -> group '%s'", cam_id, target_group)

    return updated, warnings


def reassign_camera_groups_from_calibration(
    input_calibration: str,
    moves: str,
    output: Optional[str] = None,
    overwrite: bool = False,
    strict: bool = False,
    map_file: Optional[Path] = None,
    prefer_existing_fov: bool = False,
    dilation: float = 1.0,
    height_range: tuple = (1.0, 3.0),
    image_size: tuple = (1920, 1080),
    max_camera_distance: float = 30.0,
    output_suffix: str = "reassigned",
    label_camera_ids: bool = True,
    visualize: bool = True,
) -> Tuple[Path, List[str]]:
    """
    Reassign cameras to existing groups, recompute origins, and optionally visualize.
    """

    # Validate output arguments
    try:
        if overwrite and output is not None:
            raise ValueError("overwrite and output arguments are mutually exclusive")
    except Exception:
        logger.exception("Invalid argument combination for output handling.")
        sys.exit(1)

    input_path = Path(input_calibration)
    if overwrite:
        if input_path.is_dir():
            output_path = input_path / "calibration.json"
        else:
            output_path = input_path
    elif output is None:
        if input_path.is_dir():
            output_path = input_path / f"calibration_{output_suffix}.json"
        else:
            output_path = input_path.parent / f"calibration_{output_suffix}.json"
    else:
        output_path = Path(output)
        if input_path.is_dir() and output_path.suffix == "":
            output_path = output_path / f"calibration_{output_suffix}.json"

    # Load calibration data
    logger.info("=" * 80)
    logger.info("Camera Reassignment Pipeline")
    logger.info("=" * 80)
    logger.info(f"Loading calibration data from: {input_calibration}")

    try:
        calibration = load_calib_json(
            input_path, load_original=True, validate=True,
        )
    except Exception:
        logger.exception("Error loading calibration file: %s", input_calibration)
        sys.exit(1)

    # Parse moves
    moves = parse_moves(moves)
    logger.info("Loaded %d move(s): %s", len(moves), moves)

    # Apply reassignment moves
    updated_count, warnings = apply_group_reassignments(
        calibration, moves, strict=strict
    )
    logger.info("✓ %d cameras reassigned", updated_count)
    if warnings:
        for w in warnings:
            logger.warning(f"⚠️  {w}")

    # Resolve map path (default to Top.png next to calibration)
    map_file, map_image, map_width, map_height = load_map_data(map_file)

    # Derive scene bounds (best-effort; continue without bounds on failure)
    if map_file and map_width is not None and map_height is not None:
        scene_bounds = calculate_scene_bounds_from_calibration(
            calibration, map_width=map_width, map_height=map_height
        )
    else:
        logger.warning("No map file available; continuing without scene bounds.")
        scene_bounds = calculate_scene_bounds_from_calibration(calibration)

    # Recompute origins/dimensions
    logger.info("Recomputing group origins/dimensions after reassignment...")
    calibration = calculate_and_update_group_origins(
        calibration,
        dilation_distance=dilation,
        height_range=height_range,
        image_size=image_size,
        use_frustum=not prefer_existing_fov,
        scene_bounds=scene_bounds,
        max_distance=max_camera_distance,
    )
    save_calibration_data(calibration, str(output_path))
    logger.info("✓ Group origins/dimensions recomputed and saved to %s", output_path)

    # Visualization (optional)
    if visualize:
        try:
            from spatialai_data_utils.visualization.camera_groups import (
                plot_sensor_groups,
            )

            output_map = output_path.with_name(f"{output_path.stem}_map.png")
            actual_map_file = (
                str(map_file)
                if map_file is not None and map_image is not None
                else None
            )
            if actual_map_file is None:
                logger.info(
                    "No map file available, using black background for visualization"
                )
            logger.info(
                "Generating visualization: %s (map=%s)",
                output_map,
                actual_map_file or "None/black",
            )
            plot_sensor_groups(
                calibration["sensors"],
                actual_map_file,
                str(output_map),
                label_ids=label_camera_ids,
            )
            logger.info("✓ Visualization generated")
        except Exception as exc:
            logger.error("Failed to generate visualization: %s", exc)

    logger.info("✓ Completed reassignment pipeline")
    return output_path, warnings


def _compute_score(
    manager: Any,
    assignments: List[int],
    max_camera_per_group: int,
    target_n_clusters: int,
) -> Dict[str, float]:
    """
    Evaluate clustering quality with a simple weighted objective.

    Heavily penalizes unassigned cameras and capacity overflow, then prefers
    solutions with compact clusters.
    """
    unassigned_count = sum(1 for a in assignments if a is None)
    cluster_sizes = [len(members) for members in manager.clusters.values()]
    overflow_penalty = sum(
        max(0, size - max_camera_per_group) for size in cluster_sizes
    )
    cluster_count_penalty = abs(len(manager.clusters) - target_n_clusters)

    # If a configuration already violates hard constraints, skip expensive scatter calc.
    if unassigned_count > 0 or overflow_penalty > 0:
        score = (
            unassigned_count * 1_000_000
            + overflow_penalty * 10_000
            + cluster_count_penalty * 100
        )
        return {
            "score": score,
            "unassigned_count": unassigned_count,
            "overflow": overflow_penalty,
            "scatter_mean": float("inf"),
            "cluster_count_penalty": cluster_count_penalty,
        }

    scatters = []
    for members in manager.clusters.values():
        if members:
            scatters.append(manager.evaluate_point_scatter(members))
    scatter_mean = statistics.mean(scatters) if scatters else float("inf")

    # Weight unassigned and overflow much higher than compactness
    score = (
        unassigned_count * 1_000_000
        + overflow_penalty * 10_000
        + cluster_count_penalty * 100
        + scatter_mean
    )

    return {
        "score": score,
        "unassigned_count": unassigned_count,
        "overflow": overflow_penalty,
        "scatter_mean": scatter_mean,
        "cluster_count_penalty": cluster_count_penalty,
    }


def _default_grid(values: List[float], name: str) -> List[float]:
    if values:
        return values
    if name == "overlap":
        # Ratios in [0, 1]
        return [0.0, 0.05, 0.1, 0.2, 0.3, 0.4, 0.5]
    if name == "distance":
        return [4.0, 6.0, 8.0, 10.0, 12.0, 15.0, 20.0, 30.0]
    return values


def _linspace(start: float, stop: float, num: int) -> List[float]:
    if num <= 1:
        return [start]
    step = (stop - start) / (num - 1)
    return [start + step * i for i in range(num)]


def _grid_from_stats_or_default(
    user_values: List[float],
    name: str,
    stats: Dict[str, float],
) -> Tuple[List[float], Tuple[float, float]]:
    """
    Build a grid based on observed global stats when the user does not provide one.
    Returns (grid, (vmin, vmax)).
    """
    if user_values:
        return user_values, (None, None)

    if name == "overlap":
        vmin, vmax = stats.get("overlap_min"), stats.get("overlap_max")
        clamp_min, clamp_max = 0.0, 1.0
    else:
        vmin, vmax = stats.get("distance_min"), stats.get("distance_max")
        clamp_min, clamp_max = 0.0, float("inf")

    if (
        vmin is None
        or vmax is None
        or not math.isfinite(vmin)
        or not math.isfinite(vmax)
    ):
        return _default_grid([], name), (None, None)

    if vmax < vmin:  # defensive
        vmin, vmax = vmax, vmin

    size = 5
    raw_grid = _linspace(vmin, vmax, size)
    grid = []
    for val in raw_grid:
        val = max(clamp_min, min(clamp_max, val))
        grid.append(val)

    # Deduplicate while preserving order
    dedup = []
    for g in grid:
        if all(abs(g - d) > 1e-6 for d in dedup):
            dedup.append(g)

    return dedup, (vmin, vmax)


def _build_fine_grid(
    best_value: float, vmin: float, vmax: float, name: str
) -> List[float]:
    if vmin is None or vmax is None or not math.isfinite(best_value):
        return []
    span = max(vmax - vmin, 0.0)
    if span <= 0.0:
        return [best_value]

    # Finer step for refinement; aim for ~5-7 samples around best
    step = span * 0.02
    if name == "overlap":
        step = max(step, 0.01)
        clamp_min, clamp_max = 0.0, 1.0
    else:
        step = max(step, 0.3)
        clamp_min, clamp_max = 0.0, float("inf")

    # If best is on a boundary, bias deltas inward to still generate enough points
    eps = step * 0.5
    if best_value <= vmin + eps:
        deltas = (0, 1, 2, 3, 4, 5, 6)
    elif best_value >= vmax - eps:
        deltas = (-6, -5, -4, -3, -2, -1, 0)
    else:
        deltas = (-3, -2, -1, 0, 1, 2, 3)
    candidates = [best_value + step * delta for delta in deltas]
    refined = []
    for c in candidates:
        c = max(clamp_min, min(clamp_max, c))
        if c < vmin or c > vmax:
            continue
        if all(abs(c - r) > 1e-6 for r in refined):
            refined.append(c)

    return sorted(refined)


def _build_start_indices(
    grid: List[int], num_sensors: int, seed: Optional[int] = None
) -> List[int]:
    if grid:
        return [idx for idx in grid if 0 <= idx < num_sensors]

    # Default pool size
    k = min(10, max(1, num_sensors))
    if k == num_sensors:
        return list(range(num_sensors))

    if seed is not None:
        rng = random.Random(seed)
        return sorted(rng.sample(range(num_sensors), k))

    # Deterministic evenly spaced picks
    step = (num_sensors - 1) / (k - 1) if k > 1 else 1
    picks = []
    for i in range(k):
        idx = round(i * step)
        if 0 <= idx < num_sensors and idx not in picks:
            picks.append(idx)
    return picks


def _run_single_config(
    sensors: List[dict],
    n_clusters: int,
    max_camera_per_group: int,
    start_idx: int,
    overlap_threshold: float,
    distance_threshold: float,
    cfg: Dict[str, float],
) -> Dict[str, float]:
    """
    Run clustering once and return combined params+metrics (picklable for multiprocessing).
    """
    # Local import to avoid circular dependency at module import time
    from spatialai_data_utils.core.cameras.clustering import CameraClusterManager

    manager = CameraClusterManager(sensors)
    assignments = manager.cluster_cameras(
        n_clusters=n_clusters,
        start_camera_index=start_idx,
        use_frustum=cfg["use_frustum"],
        scene_bounds=None,
        max_camera_distance=cfg["max_camera_distance"],
        height_range=cfg["height_range"],
        image_size=cfg["image_size"],
        mode=cfg["mode"],
        overlap_threshold=overlap_threshold,
        distance_threshold=distance_threshold,
        max_cluster_size=max_camera_per_group,
        max_cascade_depth=cfg["max_cascade_depth"],
        enable_unassigned_processing=True,
        global_stats=cfg.get("global_stats"),
        warn_thresholds=cfg.get("global_stats") is None,
    )

    metrics = _compute_score(
        manager=manager,
        assignments=assignments,
        max_camera_per_group=max_camera_per_group,
        target_n_clusters=n_clusters,
    )

    params = {
        "start_camera_index": start_idx,
        "overlap_threshold": overlap_threshold,
        "distance_threshold": distance_threshold,
        "n_clusters": n_clusters,
    }
    return {**params, **metrics}


def _run_grid(
    sensors: List[dict],
    n_clusters: int,
    ov_grid: List[float],
    dist_grid: List[float],
    start_idx_grid: List[int],
    label: str,
    results: List[Dict[str, float]],
    seen: set,
    args: Any,
    run_cfg: Dict[str, Any],
    workers: int,
):
    """
    Execute a grid of clustering runs and append successful results to `results`.
    Logs progress for visibility.
    """
    logger.info(
        "Searching (%s): overlaps=%s | distances=%s | start_indices=%s | total=%d",
        label,
        [round(v, 3) for v in ov_grid],
        [round(v, 3) for v in dist_grid],
        start_idx_grid,
        len(ov_grid) * len(dist_grid) * len(start_idx_grid),
    )
    tasks = []
    for overlap, distance, start_idx in itertools.product(
        ov_grid, dist_grid, start_idx_grid
    ):
        key = (overlap, distance, start_idx)
        if key in seen:
            continue
        seen.add(key)
        tasks.append((overlap, distance, start_idx))

    total = len(tasks)
    if total == 0:
        return
    actual_workers = min(workers, total)
    progress_every = 1 if total <= 20 else max(1, total // 10)

    if actual_workers > 1:
        logger.info("Using %d workers for %s stage", actual_workers, label)
        with concurrent.futures.ProcessPoolExecutor(max_workers=actual_workers) as ex:
            future_to_key = {
                ex.submit(
                    _run_single_config,
                    sensors,
                    n_clusters,
                    args.max_camera_per_group,
                    start_idx,
                    overlap,
                    distance,
                    run_cfg,
                ): key
                for (overlap, distance, start_idx) in tasks
            }
            completed = 0
            for future in concurrent.futures.as_completed(future_to_key):
                overlap, distance, start_idx = future_to_key[future]
                try:
                    result = future.result()
                    results.append(result)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Skipping config (start=%s, overlap=%.3f, distance=%.2f): %s",
                        start_idx,
                        overlap,
                        distance,
                        exc,
                    )
                completed += 1
                if completed % progress_every == 0 or completed == total:
                    logger.info(
                        "[%s] progress: %d/%d (%.1f%%)",
                        label,
                        completed,
                        total,
                        100.0 * completed / total,
                    )
    else:
        completed = 0
        for overlap, distance, start_idx in tasks:
            try:
                result = _run_single_config(
                    sensors=sensors,
                    n_clusters=n_clusters,
                    max_camera_per_group=args.max_camera_per_group,
                    start_idx=start_idx,
                    overlap_threshold=overlap,
                    distance_threshold=distance,
                    cfg=run_cfg,
                )
                results.append(result)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Skipping config (start=%s, overlap=%.3f, distance=%.2f): %s",
                    start_idx,
                    overlap,
                    distance,
                    exc,
                )
            completed += 1
            if completed % progress_every == 0 or completed == total:
                logger.info(
                    "[%s] progress: %d/%d (%.1f%%)",
                    label,
                    completed,
                    total,
                    100.0 * completed / total,
                )


def _configure_logging(args, verbose: bool = False):
    log_level = logging.INFO if verbose else logging.WARNING
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    # Quiet noisy submodules when requested
    logging.getLogger("spatialai_data_utils.core.cameras.clustering").setLevel(
        log_level
    )
    logger.setLevel(log_level)

    # Ensure progress/result logs are visible even when quiet
    if not verbose:
        progress_handler = logging.StreamHandler()
        progress_handler.setLevel(logging.INFO)
        progress_handler.setFormatter(
            logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )
        logger.addHandler(progress_handler)
        # Allow INFO from this module to pass to the handler
        logger.setLevel(logging.INFO)
        # Prevent double-logging through root handler
        logger.propagate = False
