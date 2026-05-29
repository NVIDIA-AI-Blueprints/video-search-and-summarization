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

"""Dual-view plotting utilities for camera placement visualization.

The static renderer pairs a 3D frustum view with a BEV coverage panel. The BEV
panel defaults to frustum-derived FOV polygons, clips them to known scene
bounds when available, and can draw a map background with a subtle mask for
readability. The CLI may also show the same matplotlib figure interactively so
the 3D panel can be rotated with a display-capable backend.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np
import matplotlib.patheffects as path_effects
from shapely.errors import ShapelyError
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon, box

from spatialai_data_utils.core.cameras.polygon import (
    calculate_overlap_ratio,
    calculate_scene_bounds_from_calibration,
    extract_sensor_fov_polygons,
    find_field_of_view_polygon,
    parse_polygon,
)
from spatialai_data_utils.core.cameras.utils import load_map_data
from spatialai_data_utils.utils.string_utils import natural_sort_key
from spatialai_data_utils.visualization.camera_groups import (
    get_cluster_color,
    transform_polygon,
)
from spatialai_data_utils.visualization.camera_placement.calibration_parser import (
    CameraPose,
)


@dataclass(frozen=True)
class CameraPlacementStyle:
    """Rendering controls for 3D camera frustums."""

    frustum_depth: float = 3.0
    fallback_half_width_ratio: float = 0.35
    fallback_half_height_ratio: float = 0.22
    face_alpha: float = 0.12
    edge_alpha: float = 0.75
    edge_width: float = 1.2
    camera_marker_size: int = 28
    label_fontsize: int = 9
    draw_labels: bool = True
    draw_local_axes: bool = False
    draw_ground_plane: bool = True
    ground_plane_alpha: float = 0.04
    zoom_to_camera_content: bool = True
    axis_padding_ratio: float = 0.12
    min_axis_padding: float = 0.5


@dataclass(frozen=True)
class CameraPlacementBevStyle:
    """Rendering controls for the BEV coverage panel."""

    source_mode: str = "frustum"  # "auto", "attributes", "frustum"
    height_range: tuple[float, float] = (1.0, 3.0)
    max_camera_distance: float = 20.0
    polygon_alpha: float = 0.3
    polygon_linewidth: float = 1.0
    polygon_boundary_alpha: float = 0.8
    polygon_boundary_linewidth: float = 2.0
    draw_camera_centers: bool = True
    draw_heading_arrows: bool = True
    map_alpha: float = 1.0
    map_mask_alpha: float = 0.35
    map_mask_color: tuple[float, float, float] = (0.0, 0.0, 0.0)
    label_fontsize: int = 8
    label_offset_px: float = 5.0
    center_marker_size: int = 44
    center_marker_edge_width: float = 1.2
    heading_arrow_width: float = 1.2
    heading_arrow_scale: float = 0.55
    axis_padding_ratio: float = 0.08
    min_axis_padding: float = 1.0
    zoom_to_content: bool = True


def _estimate_half_plane_size(
    pose: CameraPose,
    depth: float,
    style: CameraPlacementStyle,
) -> tuple[float, float]:
    fx = float(pose.intrinsic_matrix[0, 0])
    fy = float(pose.intrinsic_matrix[1, 1])

    if pose.image_size is not None and fx > 1e-8 and fy > 1e-8:
        width_px, height_px = pose.image_size
        return (
            depth * (0.5 * width_px / fx),
            depth * (0.5 * height_px / fy),
        )

    return (
        depth * style.fallback_half_width_ratio,
        depth * style.fallback_half_height_ratio,
    )


def _build_world_frustum_vertices(
    pose: CameraPose,
    style: CameraPlacementStyle,
) -> np.ndarray:
    depth = float(style.frustum_depth)
    half_w, half_h = _estimate_half_plane_size(pose, depth, style)

    local_vertices = np.array(
        [
            [0.0, 0.0, 0.0],
            [-half_w, half_h, depth],
            [half_w, half_h, depth],
            [half_w, -half_h, depth],
            [-half_w, -half_h, depth],
        ],
        dtype=np.float64,
    )
    rotated = (pose.rotation_c2w @ local_vertices.T).T
    return rotated + pose.position_xyz[None, :]


def _set_equal_axes(
    ax,
    all_points: np.ndarray,
    *,
    padding_ratio: float,
    min_padding: float,
) -> None:
    mins = all_points.min(axis=0)
    maxs = all_points.max(axis=0)
    center = (mins + maxs) / 2.0
    max_span = float(np.max(maxs - mins))

    if max_span < 1e-3:
        max_span = 1.0
    padding = max(min_padding, padding_ratio * max_span)
    half_span = 0.5 * max_span + padding

    ax.set_xlim(center[0] - half_span, center[0] + half_span)
    ax.set_ylim(center[1] - half_span, center[1] + half_span)
    ax.set_zlim(center[2] - half_span, center[2] + half_span)
    ax.set_box_aspect((1.0, 1.0, 1.0))


def _auto_3d_view(camera_poses: Sequence[CameraPose]) -> tuple[float, float]:
    """Choose a 3D view angle that spreads cameras and shows their headings."""

    positions = np.asarray([pose.position_xyz for pose in camera_poses], dtype=np.float64)
    if len(positions) <= 1:
        return 30.0, -60.0

    centered = positions[:, :2] - positions[:, :2].mean(axis=0, keepdims=True)
    forward_xy = np.asarray(
        [
            (pose.rotation_c2w @ np.array([0.0, 0.0, 1.0], dtype=np.float64))[:2]
            for pose in camera_poses
        ],
        dtype=np.float64,
    )
    norms = np.linalg.norm(forward_xy, axis=1, keepdims=True)
    valid = norms[:, 0] > 1e-6
    if np.any(valid):
        forward_xy[valid] = forward_xy[valid] / norms[valid]

    best_score = -np.inf
    best_azim = -60.0
    for azim in np.arange(-180.0, 180.0, 10.0):
        theta = np.deg2rad(azim)
        screen_x = np.array([-np.sin(theta), np.cos(theta)], dtype=np.float64)
        view_dir = np.array([np.cos(theta), np.sin(theta)], dtype=np.float64)

        spread = np.ptp(centered @ screen_x)
        depth_spread = np.ptp(centered @ view_dir)
        heading_side_visibility = (
            float(np.mean(np.abs(forward_xy[valid] @ screen_x))) if np.any(valid) else 0.0
        )
        heading_front_visibility = (
            float(np.mean(np.abs(forward_xy[valid] @ view_dir))) if np.any(valid) else 0.0
        )
        # Favor angles that avoid collapsing the camera layout while still showing
        # enough side/front heading variation to make frustum orientation readable.
        score = spread + 0.35 * depth_spread + 1.5 * heading_side_visibility + 0.5 * heading_front_visibility
        if score > best_score:
            best_score = score
            best_azim = float(azim)

    z_span = float(np.ptp(positions[:, 2]))
    xy_span = max(float(np.ptp(positions[:, 0])), float(np.ptp(positions[:, 1])), 1e-6)
    elev = 28.0 if z_span / xy_span < 0.35 else 34.0
    return elev, best_azim


def _camera_positions_bounds(
    camera_poses: Sequence[CameraPose],
    margin_ratio: float = 0.1,
    min_margin: float = 1.0,
) -> tuple[float, float, float, float]:
    positions = np.asarray([pose.position_xyz[:2] for pose in camera_poses], dtype=np.float64)
    mins = positions.min(axis=0)
    maxs = positions.max(axis=0)
    margin = max(min_margin, margin_ratio * float(np.max(maxs - mins)))
    return (
        mins[0] - margin,
        mins[1] - margin,
        maxs[0] + margin,
        maxs[1] + margin,
    )


def _resolve_scene_bounds(
    camera_poses: Sequence[CameraPose],
    calibration_data: dict | None,
    map_width: int | None,
    map_height: int | None,
) -> tuple[float, float, float, float]:
    # Only trust calibration-derived scene bounds when a map size is present;
    # without map dimensions, the upstream fallback can add very large margins.
    if (
        calibration_data
        and calibration_data.get("sensors")
        and map_width is not None
        and map_height is not None
    ):
        bounds = calculate_scene_bounds_from_calibration(
            calibration_data,
            map_width=map_width,
            map_height=map_height,
        )
        if bounds is not None:
            return bounds
    return _camera_positions_bounds(camera_poses)


def _compute_world_content_bounds(
    camera_poses: Sequence[CameraPose],
    polygon_by_id: Mapping[str, object],
    *,
    padding_ratio: float,
    min_padding: float,
) -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []
    for pose in camera_poses:
        xs.append(float(pose.position_xyz[0]))
        ys.append(float(pose.position_xyz[1]))
        geometry = polygon_by_id.get(pose.sensor_id)
        if geometry is None:
            continue
        for poly in _iter_polygons(geometry):
            min_x, min_y, max_x, max_y = poly.bounds
            xs.extend([float(min_x), float(max_x)])
            ys.extend([float(min_y), float(max_y)])

    if not xs or not ys:
        return _camera_positions_bounds(camera_poses)

    min_x = min(xs)
    max_x = max(xs)
    min_y = min(ys)
    max_y = max(ys)
    span = max(max_x - min_x, max_y - min_y)
    padding = max(min_padding, padding_ratio * float(span))
    return (
        min_x - padding,
        min_y - padding,
        max_x + padding,
        max_y + padding,
    )


def _iter_polygons(geometry):
    if geometry is None:
        return
    if isinstance(geometry, Polygon):
        if not geometry.is_empty:
            yield geometry
        return
    if isinstance(geometry, MultiPolygon):
        for part in geometry.geoms:
            if not part.is_empty:
                yield part
        return
    if isinstance(geometry, GeometryCollection):
        for part in geometry.geoms:
            yield from _iter_polygons(part)


def _draw_polygon_geometry(
    ax,
    geometry,
    color,
    alpha: float,
    linewidth: float,
    boundary_alpha: float | None = None,
    boundary_linewidth: float | None = None,
) -> None:
    from matplotlib.patches import Polygon as MplPolygon

    for poly in _iter_polygons(geometry):
        patch = MplPolygon(
            np.asarray(poly.exterior.coords, dtype=np.float64),
            closed=True,
            facecolor=(*color, alpha),
            edgecolor=(
                *color,
                boundary_alpha if boundary_alpha is not None else alpha,
            ),
            linewidth=boundary_linewidth if boundary_linewidth is not None else linewidth,
            joinstyle="round",
        )
        ax.add_patch(patch)


def _extract_polygons_from_attributes(sensors: Sequence[dict]) -> list:
    polygons: list = []
    for sensor in sensors:
        polygon = None
        attributes = sensor.get("attributes") or []
        try:
            poly_str = find_field_of_view_polygon(attributes)
            polygon = parse_polygon(poly_str)
        except (ValueError, KeyError, TypeError):
            polygon = None
        polygons.append(polygon)
    return polygons


def _clip_geometry_to_scene_bounds(
    geometry,
    scene_bounds: tuple[float, float, float, float] | None,
):
    """Clip a polygon geometry to scene bounds when available."""

    if geometry is None or scene_bounds is None:
        return geometry

    min_x, min_y, max_x, max_y = scene_bounds
    try:
        clipped = geometry.intersection(box(min_x, min_y, max_x, max_y))
    except (AttributeError, TypeError, ValueError, ShapelyError):
        return geometry

    if getattr(clipped, "is_empty", False):
        return None
    return clipped


def _extract_bev_polygons(
    camera_poses: Sequence[CameraPose],
    sensors_by_id: Mapping[str, dict] | None,
    style: CameraPlacementStyle,
    bev_style: CameraPlacementBevStyle,
    scene_bounds: tuple[float, float, float, float] | None,
) -> tuple[dict[str, object], float]:
    pose_ids = [pose.sensor_id for pose in camera_poses]
    sensor_list = [
        sensors_by_id[sid] for sid in pose_ids
        if sensors_by_id is not None and sid in sensors_by_id
    ]

    polygon_by_id: dict[str, object] = {}
    polygons: list[object] = []

    if sensor_list:
        if bev_style.source_mode == "attributes":
            polygons = _extract_polygons_from_attributes(sensor_list)
        elif bev_style.source_mode == "frustum":
            polygons, _ = extract_sensor_fov_polygons(
                sensor_list,
                prefer_existing_fov=False,
                height_range=bev_style.height_range,
                scene_bounds=scene_bounds,
                max_distance=bev_style.max_camera_distance,
            )
        else:
            polygons, _ = extract_sensor_fov_polygons(
                sensor_list,
                prefer_existing_fov=True,
                height_range=bev_style.height_range,
                scene_bounds=scene_bounds,
                max_distance=bev_style.max_camera_distance,
            )
        polygons = [
            _clip_geometry_to_scene_bounds(poly, scene_bounds) for poly in polygons
        ]
        polygon_by_id.update(
            {
                sensor["id"]: poly
                for sensor, poly in zip(sensor_list, polygons, strict=True)
            }
        )

    # Always guarantee a polygon fallback per camera for readability.
    for pose in camera_poses:
        existing = polygon_by_id.get(pose.sensor_id)
        if existing is not None and not getattr(existing, "is_empty", False):
            continue
        frustum_vertices = _build_world_frustum_vertices(pose, style)
        fallback_polygon = Polygon(frustum_vertices[1:, :2])
        polygon_by_id[pose.sensor_id] = _clip_geometry_to_scene_bounds(
            fallback_polygon,
            scene_bounds,
        )

    overlap_ratio = calculate_overlap_ratio(list(polygon_by_id.values()))
    return polygon_by_id, overlap_ratio


def _resolve_fov_clip_bounds(
    camera_poses: Sequence[CameraPose],
    sensors_by_id: Mapping[str, dict] | None,
    calibration_data: dict | None,
    map_width: int | None,
    map_height: int | None,
) -> tuple[float, float, float, float] | None:
    """Resolve scene bounds used to crop BEV FOV polygons.

    Preference order:
    1) Existing group dimensions from BEV origin calculation outputs.
    2) Existing fieldOfViewPolygon attribute bounds.
    3) Map-derived world bounds from translation+scale metadata.
    """

    if calibration_data is None or not calibration_data.get("sensors"):
        return None

    selected_sensor_ids = {pose.sensor_id for pose in camera_poses}
    candidate_sensors = [
        sensor
        for sensor_id, sensor in (sensors_by_id or {}).items()
        if sensor_id in selected_sensor_ids
    ]
    if not candidate_sensors:
        candidate_sensors = [
            sensor
            for sensor in calibration_data["sensors"]
            if sensor.get("id") in selected_sensor_ids
        ]
    if not candidate_sensors:
        candidate_sensors = calibration_data["sensors"]

    # Prefer group dimensions if they already exist (e.g., output from
    # calculate_origin/camera-grouping workflows).
    group_bounds: list[tuple[float, float, float, float]] = []
    seen_groups: set[str] = set()
    for sensor in candidate_sensors:
        group = sensor.get("group")
        if not isinstance(group, dict):
            continue
        group_name = str(group.get("name", sensor.get("id", "")))
        if group_name in seen_groups:
            continue
        seen_groups.add(group_name)

        dimensions = group.get("dimensions")
        if not isinstance(dimensions, (list, tuple)) or len(dimensions) != 4:
            continue
        try:
            x0, y0, x1, y1 = (float(v) for v in dimensions)
        except (TypeError, ValueError):
            continue
        group_bounds.append((min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))

    if group_bounds:
        min_x = min(b[0] for b in group_bounds)
        min_y = min(b[1] for b in group_bounds)
        max_x = max(b[2] for b in group_bounds)
        max_y = max(b[3] for b in group_bounds)
        return (min_x, min_y, max_x, max_y)

    # Fallback: reuse existing FOV polygons as scene boundary hints, consistent
    # with the BEV-origin visualization pipeline's bounds derivation.
    attribute_polygons = _extract_polygons_from_attributes(candidate_sensors)
    valid_polygons = [
        poly
        for poly in attribute_polygons
        if poly is not None and not getattr(poly, "is_empty", False)
    ]
    if valid_polygons:
        min_x = min(poly.bounds[0] for poly in valid_polygons)
        min_y = min(poly.bounds[1] for poly in valid_polygons)
        max_x = max(poly.bounds[2] for poly in valid_polygons)
        max_y = max(poly.bounds[3] for poly in valid_polygons)
        return (min_x, min_y, max_x, max_y)

    if map_width is None or map_height is None:
        return None

    first_sensor = candidate_sensors[0]
    if (
        "translationToGlobalCoordinates" not in first_sensor
        or "scaleFactor" not in first_sensor
    ):
        return None

    return calculate_scene_bounds_from_calibration(
        calibration_data,
        map_width=map_width,
        map_height=map_height,
    )


def _sensor_world_to_map_params(calibration_data: dict | None):
    if not calibration_data:
        return None, None
    for sensor in calibration_data.get("sensors", []):
        translation = sensor.get("translationToGlobalCoordinates")
        scale = sensor.get("scaleFactor")
        if isinstance(translation, dict) and scale is not None:
            return translation, float(scale)
    return None, None


def _world_to_map_xy(
    x: float,
    y: float,
    translation: dict,
    scale: float,
    map_height: int,
) -> tuple[float, float]:
    return (
        scale * (x + float(translation["x"])),
        map_height - 1 - scale * (y + float(translation["y"])),
    )


def _draw_3d_panel(
    ax,
    camera_poses: Sequence[CameraPose],
    color_lookup: Mapping[str, tuple[float, float, float]],
    style: CameraPlacementStyle,
    scene_bounds: tuple[float, float, float, float] | None,
    *,
    title: str,
    elev: float,
    azim: float,
) -> None:
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    all_points: list[np.ndarray] = []

    if style.draw_ground_plane and scene_bounds is not None:
        min_x, min_y, max_x, max_y = scene_bounds
        xs = np.array([[min_x, max_x], [min_x, max_x]], dtype=np.float64)
        ys = np.array([[min_y, min_y], [max_y, max_y]], dtype=np.float64)
        zs = np.zeros_like(xs)
        ax.plot_surface(
            xs,
            ys,
            zs,
            color="gray",
            alpha=style.ground_plane_alpha,
            linewidth=0.0,
            zorder=0,
        )
        ground_points = np.array(
            [
                [min_x, min_y, 0.0],
                [max_x, min_y, 0.0],
                [max_x, max_y, 0.0],
                [min_x, max_y, 0.0],
            ],
            dtype=np.float64,
        )
        # For readability on large scenes, keep 3D axis zoom tied to camera/frustum
        # content by default. Full-scene bounds can make cameras visually tiny.
        if not style.zoom_to_camera_content:
            all_points.append(ground_points)

    for pose in camera_poses:
        color = color_lookup[pose.sensor_id]
        vertices = _build_world_frustum_vertices(pose, style)
        all_points.append(vertices)
        p = pose.position_xyz
        ax.scatter(
            p[0],
            p[1],
            p[2],
            color=color,
            s=style.camera_marker_size,
            edgecolors="white",
            linewidths=1.0,
            depthshade=False,
            zorder=10,
        )

        side_faces = [
            [vertices[0], vertices[1], vertices[2]],
            [vertices[0], vertices[2], vertices[3]],
            [vertices[0], vertices[3], vertices[4]],
            [vertices[0], vertices[4], vertices[1]],
        ]
        near_plane = [vertices[1], vertices[2], vertices[3], vertices[4]]

        ax.add_collection3d(
            Poly3DCollection(
                side_faces,
                facecolors=color,
                edgecolors=color,
                linewidths=style.edge_width,
                alpha=style.face_alpha,
            )
        )
        ax.add_collection3d(
            Poly3DCollection(
                [near_plane],
                facecolors=color,
                edgecolors=color,
                linewidths=style.edge_width,
                alpha=style.face_alpha * 1.2,
            )
        )

        edges = [(1, 2), (2, 3), (3, 4), (4, 1), (0, 1), (0, 2), (0, 3), (0, 4)]
        for a, b in edges:
            seg = np.vstack([vertices[a], vertices[b]])
            ax.plot(
                seg[:, 0],
                seg[:, 1],
                seg[:, 2],
                color=color,
                alpha=style.edge_alpha,
                linewidth=style.edge_width,
            )

        if style.draw_labels:
            label = ax.text(
                p[0],
                p[1],
                p[2],
                pose.sensor_id,
                color="white",
                fontsize=style.label_fontsize,
                weight="bold",
            )
            label.set_path_effects(
                [path_effects.withStroke(linewidth=2.5, foreground="black")]
            )

        if style.draw_local_axes:
            axis_len = style.frustum_depth * 0.5
            basis = pose.rotation_c2w
            axis_colors = ("#d62728", "#2ca02c", "#1f77b4")
            for axis_idx, axis_color in enumerate(axis_colors):
                direction = basis[:, axis_idx] * axis_len
                ax.quiver(
                    pose.position_xyz[0],
                    pose.position_xyz[1],
                    pose.position_xyz[2],
                    direction[0],
                    direction[1],
                    direction[2],
                    color=axis_color,
                    linewidth=1.4,
                    alpha=0.8,
                )

    all_points_np = np.vstack(all_points)
    _set_equal_axes(
        ax,
        all_points_np,
        padding_ratio=style.axis_padding_ratio,
        min_padding=style.min_axis_padding,
    )
    ax.set_xlabel("X", labelpad=4)
    ax.set_ylabel("Y", labelpad=4)
    ax.set_zlabel("Z", labelpad=4)
    ax.set_title(title)
    ax.view_init(elev=elev, azim=azim)
    ax.grid(True, alpha=0.18)


def _draw_bev_panel(
    ax,
    camera_poses: Sequence[CameraPose],
    color_lookup: Mapping[str, tuple[float, float, float]],
    style: CameraPlacementStyle,
    bev_style: CameraPlacementBevStyle,
    sensors_by_id: Mapping[str, dict] | None,
    calibration_data: dict | None,
    map_file: str | Path | None,
    scene_bounds: tuple[float, float, float, float],
    fov_clip_bounds: tuple[float, float, float, float] | None,
    *,
    title: str,
) -> float:
    _map_path, map_image, map_width, map_height = load_map_data(map_file)
    translation, scale = _sensor_world_to_map_params(calibration_data)
    use_map = (
        map_image is not None
        and map_width is not None
        and map_height is not None
        and translation is not None
        and scale is not None
    )

    polygon_by_id, overlap_ratio = _extract_bev_polygons(
        camera_poses=camera_poses,
        sensors_by_id=sensors_by_id,
        style=style,
        bev_style=bev_style,
        scene_bounds=fov_clip_bounds,
    )
    content_bounds_world = _compute_world_content_bounds(
        camera_poses,
        polygon_by_id,
        padding_ratio=bev_style.axis_padding_ratio,
        min_padding=bev_style.min_axis_padding,
    )

    if use_map:
        ax.imshow(map_image, alpha=bev_style.map_alpha)
        if bev_style.map_mask_alpha > 0:
            mask = np.zeros((int(map_height), int(map_width), 3), dtype=np.float32)
            mask[:, :] = np.asarray(bev_style.map_mask_color, dtype=np.float32)
            ax.imshow(mask, alpha=float(min(max(bev_style.map_mask_alpha, 0.0), 1.0)))
        map_pixel_bounds = (0.0, 0.0, float(map_width - 1), float(map_height - 1))
    else:
        map_pixel_bounds = None

    for pose in camera_poses:
        color = color_lookup[pose.sensor_id]
        polygon = polygon_by_id.get(pose.sensor_id)
        if polygon is None or getattr(polygon, "is_empty", False):
            continue

        if use_map:
            polygon_to_draw = transform_polygon(
                polygon,
                translation=translation,
                scale=scale,
                map_height=map_height,
            )
            polygon_to_draw = _clip_geometry_to_scene_bounds(
                polygon_to_draw,
                map_pixel_bounds,
            )
        else:
            polygon_to_draw = polygon

        _draw_polygon_geometry(
            ax,
            polygon_to_draw,
            color=color,
            alpha=bev_style.polygon_alpha,
            linewidth=bev_style.polygon_linewidth,
            boundary_alpha=bev_style.polygon_boundary_alpha,
            boundary_linewidth=bev_style.polygon_boundary_linewidth,
        )

        if use_map:
            cx, cy = _world_to_map_xy(
                float(pose.position_xyz[0]),
                float(pose.position_xyz[1]),
                translation=translation,
                scale=scale,
                map_height=map_height,
            )
            cx = float(np.clip(cx, 0.0, float(map_width - 1)))
            cy = float(np.clip(cy, 0.0, float(map_height - 1)))
        else:
            cx, cy = float(pose.position_xyz[0]), float(pose.position_xyz[1])

        if bev_style.draw_camera_centers:
            ax.scatter(
                cx,
                cy,
                color="white",
                s=bev_style.center_marker_size * 1.45,
                edgecolors="black",
                linewidths=bev_style.center_marker_edge_width,
                zorder=6,
            )
            ax.scatter(
                cx,
                cy,
                color=color,
                s=bev_style.center_marker_size,
                edgecolors="white",
                linewidths=bev_style.center_marker_edge_width,
                zorder=7,
            )

        if bev_style.draw_heading_arrows:
            forward_world = pose.rotation_c2w @ np.array([0.0, 0.0, 1.0], dtype=np.float64)
            end_world = (
                pose.position_xyz
                + forward_world * style.frustum_depth * bev_style.heading_arrow_scale
            )
            if use_map:
                ex, ey = _world_to_map_xy(
                    float(end_world[0]),
                    float(end_world[1]),
                    translation=translation,
                    scale=scale,
                    map_height=map_height,
                )
                ex = float(np.clip(ex, 0.0, float(map_width - 1)))
                ey = float(np.clip(ey, 0.0, float(map_height - 1)))
            else:
                ex, ey = float(end_world[0]), float(end_world[1])
            ax.annotate(
                "",
                xy=(ex, ey),
                xytext=(cx, cy),
                arrowprops={
                    "arrowstyle": "->",
                    "color": color,
                    "linewidth": bev_style.heading_arrow_width,
                    "shrinkA": 4,
                    "shrinkB": 2,
                },
                zorder=8,
            )

        if style.draw_labels:
            label = ax.text(
                cx + bev_style.label_offset_px,
                cy - bev_style.label_offset_px,
                pose.sensor_id,
                fontsize=bev_style.label_fontsize,
                color="white",
                weight="bold",
                ha="left",
                va="bottom",
                zorder=9,
            )
            label.set_path_effects(
                [path_effects.withStroke(linewidth=3.0, foreground="black")]
            )

    if use_map:
        if bev_style.zoom_to_content:
            min_x, min_y, max_x, max_y = content_bounds_world
            x0 = scale * (min_x + float(translation["x"]))
            x1 = scale * (max_x + float(translation["x"]))
            y0 = map_height - 1 - scale * (min_y + float(translation["y"]))
            y1 = map_height - 1 - scale * (max_y + float(translation["y"]))

            x_min = max(0.0, min(x0, x1))
            x_max = min(float(map_width), max(x0, x1))
            y_min = max(0.0, min(y0, y1))
            y_max = min(float(map_height), max(y0, y1))

            if x_max - x_min > 10 and y_max - y_min > 10:
                ax.set_xlim(x_min, x_max)
                ax.set_ylim(y_max, y_min)
            else:
                ax.set_xlim(0, map_width)
                ax.set_ylim(map_height, 0)
        else:
            ax.set_xlim(0, map_width)
            ax.set_ylim(map_height, 0)
        ax.set_xlabel("")
        ax.set_ylabel("")
    else:
        min_x, min_y, max_x, max_y = content_bounds_world
        ax.set_xlim(min_x, max_x)
        ax.set_ylim(min_y, max_y)
        ax.set_xlabel("")
        ax.set_ylabel("")
        ax.grid(True, alpha=0.12)

    ax.set_aspect("equal", adjustable="box")
    ax.tick_params(axis="both", which="both", labelbottom=False, labelleft=False)
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_title(f"{title} (overlap={overlap_ratio:.2f})")
    return overlap_ratio


def _default_color_lookup(
    camera_poses: Sequence[CameraPose],
) -> dict[str, tuple[float, float, float]]:
    ordered = sorted((pose.sensor_id for pose in camera_poses), key=natural_sort_key)
    return {
        sensor_id: get_cluster_color(idx, as_tuple=True)
        for idx, sensor_id in enumerate(ordered)
    }


def _safe_name(name: str) -> str:
    return "".join(ch if (ch.isalnum() or ch in ("-", "_", ".")) else "_" for ch in name)


def render_camera_placement(
    camera_poses: Sequence[CameraPose],
    output_path: str | Path | None = None,
    *,
    sensors_by_id: Mapping[str, dict] | None = None,
    calibration_data: dict | None = None,
    map_file: str | Path | None = None,
    show: bool = False,
    title: str = "Camera 3D Placement",
    elev: float | None = None,
    azim: float | None = None,
    figure_size: tuple[float, float] = (15.0, 7.5),
    dpi: int = 180,
    style: CameraPlacementStyle | None = None,
    bev_style: CameraPlacementBevStyle | None = None,
    color_lookup: Mapping[str, tuple[float, float, float]] | None = None,
    footer_text: str | None = None,
    close: bool = False,
):
    """Render camera placement as a dual-view figure (3D + BEV)."""

    if not camera_poses:
        raise ValueError("camera_poses is empty; nothing to draw.")

    style = style or CameraPlacementStyle()
    bev_style = bev_style or CameraPlacementBevStyle()
    if bev_style.source_mode not in {"auto", "attributes", "frustum"}:
        raise ValueError(
            f"Unsupported bev source_mode={bev_style.source_mode!r}; "
            "choose from: auto, attributes, frustum."
        )

    import matplotlib.pyplot as plt

    color_lookup = dict(color_lookup or _default_color_lookup(camera_poses))
    if elev is None or azim is None:
        auto_elev, auto_azim = _auto_3d_view(camera_poses)
        elev = auto_elev if elev is None else elev
        azim = auto_azim if azim is None else azim

    _map_path, _map_image, map_width, map_height = load_map_data(map_file)
    scene_bounds = _resolve_scene_bounds(
        camera_poses=camera_poses,
        calibration_data=calibration_data,
        map_width=map_width,
        map_height=map_height,
    )
    fov_clip_bounds = _resolve_fov_clip_bounds(
        camera_poses=camera_poses,
        sensors_by_id=sensors_by_id,
        calibration_data=calibration_data,
        map_width=map_width,
        map_height=map_height,
    )

    fig = None
    try:
        fig = plt.figure(figsize=figure_size)
        grid = fig.add_gridspec(1, 2, width_ratios=(1.35, 1.0))
        ax_3d = fig.add_subplot(grid[0, 0], projection="3d")
        ax_bev = fig.add_subplot(grid[0, 1])

        _draw_3d_panel(
            ax=ax_3d,
            camera_poses=camera_poses,
            color_lookup=color_lookup,
            style=style,
            scene_bounds=scene_bounds,
            title=f"{title} - 3D",
            elev=elev,
            azim=azim,
        )
        overlap_ratio = _draw_bev_panel(
            ax=ax_bev,
            camera_poses=camera_poses,
            color_lookup=color_lookup,
            style=style,
            bev_style=bev_style,
            sensors_by_id=sensors_by_id,
            calibration_data=calibration_data,
            map_file=map_file,
            scene_bounds=scene_bounds,
            fov_clip_bounds=fov_clip_bounds,
            title=f"{title} - BEV",
        )

        fig.suptitle(
            f"{title} | cameras={len(camera_poses)} | overlap={overlap_ratio:.2f}"
        )
        if footer_text:
            fig.text(
                0.5,
                0.01,
                footer_text,
                ha="center",
                va="bottom",
                fontsize=8,
                color="#444444",
            )
            fig.tight_layout(rect=(0.0, 0.035, 1.0, 0.96))
        else:
            fig.tight_layout()

        if output_path is not None:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            fig.savefig(output_path, dpi=dpi, bbox_inches="tight")

        if show:
            plt.show()

        return fig, (ax_3d, ax_bev)
    finally:
        if close and not show and fig is not None:
            plt.close(fig)


def render_camera_placement_sequence(
    camera_poses: Sequence[CameraPose],
    output_dir: str | Path,
    *,
    sensors_by_id: Mapping[str, dict] | None = None,
    cams_by_group: Mapping[str, Sequence[str]] | None = None,
    calibration_data: dict | None = None,
    map_file: str | Path | None = None,
    group_names: Sequence[str] | None = None,
    include_all_cameras: bool = True,
    title: str = "Camera 3D Placement",
    elev: float | None = None,
    azim: float | None = None,
    figure_size: tuple[float, float] = (15.0, 7.5),
    dpi: int = 180,
    style: CameraPlacementStyle | None = None,
    bev_style: CameraPlacementBevStyle | None = None,
    footer_text: str | None = None,
) -> dict[str, Path]:
    """Render a sequence of dual-view camera placement PNGs."""

    if not camera_poses:
        raise ValueError("camera_poses is empty; nothing to render.")

    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)

    pose_by_id = {pose.sensor_id: pose for pose in camera_poses}
    selected_sensor_ids = sorted(pose_by_id.keys(), key=natural_sort_key)
    shared_color_lookup = _default_color_lookup(camera_poses)
    outputs: dict[str, Path] = {}

    def _subset(sensor_ids: Sequence[str]) -> list[CameraPose]:
        return [
            pose_by_id[sensor_id]
            for sensor_id in sensor_ids
            if sensor_id in pose_by_id
        ]

    if include_all_cameras:
        all_output = output_root / "all_cameras.png"
        render_camera_placement(
            _subset(selected_sensor_ids),
            output_path=all_output,
            sensors_by_id=sensors_by_id,
            calibration_data=calibration_data,
            map_file=map_file,
            show=False,
            title=f"{title} - all_cameras",
            elev=elev,
            azim=azim,
            figure_size=figure_size,
            dpi=dpi,
            style=style,
            bev_style=bev_style,
            color_lookup=shared_color_lookup,
            footer_text=footer_text,
            close=True,
        )
        outputs["all_cameras"] = all_output

    requested_groups = set(group_names) if group_names is not None else None
    groups_dir = output_root / "groups"
    groups_written = 0
    for group_name in sorted((cams_by_group or {}).keys(), key=natural_sort_key):
        if requested_groups is not None and group_name not in requested_groups:
            continue
        members = [
            sensor_id for sensor_id in cams_by_group[group_name] if sensor_id in pose_by_id
        ]
        if not members:
            continue
        groups_dir.mkdir(parents=True, exist_ok=True)
        group_output = groups_dir / f"{_safe_name(group_name)}.png"
        render_camera_placement(
            _subset(members),
            output_path=group_output,
            sensors_by_id=sensors_by_id,
            calibration_data=calibration_data,
            map_file=map_file,
            show=False,
            title=f"{title} - {group_name}",
            elev=elev,
            azim=azim,
            figure_size=figure_size,
            dpi=dpi,
            style=style,
            bev_style=bev_style,
            color_lookup=shared_color_lookup,
            footer_text=footer_text,
            close=True,
        )
        outputs[group_name] = group_output
        groups_written += 1

    # Flat calibrations or filtered-out groups: still emit one useful frame.
    if not include_all_cameras and groups_written == 0:
        fallback_output = output_root / "all_cameras.png"
        render_camera_placement(
            _subset(selected_sensor_ids),
            output_path=fallback_output,
            sensors_by_id=sensors_by_id,
            calibration_data=calibration_data,
            map_file=map_file,
            show=False,
            title=f"{title} - all_cameras",
            elev=elev,
            azim=azim,
            figure_size=figure_size,
            dpi=dpi,
            style=style,
            bev_style=bev_style,
            color_lookup=shared_color_lookup,
            footer_text=footer_text,
            close=True,
        )
        outputs["all_cameras"] = fallback_output

    return outputs
