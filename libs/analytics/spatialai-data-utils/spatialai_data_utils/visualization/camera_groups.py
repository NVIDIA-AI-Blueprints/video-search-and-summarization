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

"""Camera group and FOV visualization utilities.

This module is the canonical home for camera-group BEV visualization APIs.
The old ``spatialai_data_utils.core.cameras.visualization`` module remains as
a compatibility shim for existing imports.
"""

from __future__ import annotations

import logging
import os

import cv2
import numpy as np
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon
from shapely.ops import unary_union
from shapely.validation import make_valid

from spatialai_data_utils.core.cameras.polygon import (
    find_field_of_view_polygon,
    parse_polygon,
)
from spatialai_data_utils.core.cameras.utils import load_map_data
from spatialai_data_utils.utils.string_utils import natural_sort_key

logger = logging.getLogger(__name__)


# Highly distinguishable color palette using perceptually distinct colors.
CLUSTER_COLORS = [
    "#E6194B",
    "#3CB44B",
    "#FFE119",
    "#4363D8",
    "#F58231",
    "#911EB4",
    "#42D4F4",
    "#F032E6",
    "#BFEF45",
    "#FABED4",
    "#469990",
    "#DCBEFF",
    "#9A6324",
    "#FFFAC8",
    "#800000",
    "#AAFFC3",
    "#808000",
    "#FFD8B1",
    "#000075",
    "#A9A9A9",
]


def get_cluster_color(index: int, as_tuple: bool = False):
    """Get a distinguishable color for a cluster by index."""

    color_hex = CLUSTER_COLORS[index % len(CLUSTER_COLORS)]
    if as_tuple:
        r = int(color_hex[1:3], 16) / 255.0
        g = int(color_hex[3:5], 16) / 255.0
        b = int(color_hex[5:7], 16) / 255.0
        return (r, g, b)
    return color_hex


def transform_polygon(polygon, translation, scale, map_height=1080, resize_factor=1.0):
    """Transform polygon from world coordinates to image/pixel coordinates."""

    if isinstance(polygon, Polygon):
        return Polygon(
            [
                (
                    resize_factor * scale * (x + translation["x"]),
                    resize_factor * (map_height - 1 - scale * (y + translation["y"])),
                )
                for x, y in polygon.exterior.coords
            ]
        )
    if isinstance(polygon, MultiPolygon):
        return MultiPolygon(
            [
                Polygon(
                    [
                        (
                            resize_factor * scale * (x + translation["x"]),
                            resize_factor
                            * (map_height - 1 - scale * (y + translation["y"])),
                        )
                        for x, y in poly.exterior.coords
                    ]
                )
                for poly in polygon.geoms
            ]
        )
    if isinstance(polygon, GeometryCollection):
        transformed = [
            transform_polygon(geom, translation, scale, map_height, resize_factor)
            for geom in polygon.geoms
            if isinstance(geom, (Polygon, MultiPolygon, GeometryCollection))
        ]
        flattened = []
        for geom in transformed:
            if isinstance(geom, Polygon):
                flattened.append(geom)
            elif isinstance(geom, MultiPolygon):
                flattened.extend(list(geom.geoms))
        return MultiPolygon(flattened)
    return polygon


def draw_polygon(img, transformed_polygon, color):
    """Draw a polygon on an image using OpenCV."""

    if isinstance(transformed_polygon, Polygon):
        pts = np.array(transformed_polygon.exterior.coords, dtype=np.int32)
        cv2.polylines(img, [pts], isClosed=True, color=color, thickness=2)
        cv2.fillPoly(img, [pts], color=color)
    elif isinstance(transformed_polygon, MultiPolygon):
        for poly in transformed_polygon.geoms:
            pts = np.array(poly.exterior.coords, dtype=np.int32)
            cv2.polylines(img, [pts], isClosed=True, color=color, thickness=2)
            cv2.fillPoly(img, [pts], color=color)


def _draw_label(ax, x, y, text, color="#000000"):
    ax.text(
        x,
        y,
        text,
        fontsize=8,
        color=color,
        ha="center",
        va="center",
        bbox=dict(facecolor="white", edgecolor="none", alpha=0.7, pad=1.5),
    )


def _extract_polygons(geometry):
    """Extract all valid Polygon objects from any geometry type."""

    polygons = []
    if geometry is None or geometry.is_empty:
        return polygons
    if isinstance(geometry, Polygon):
        if geometry.is_valid:
            polygons.append(geometry)
        else:
            polygons.extend(_extract_polygons(make_valid(geometry)))
    elif isinstance(geometry, MultiPolygon):
        for poly in geometry.geoms:
            polygons.extend(_extract_polygons(poly))
    elif isinstance(geometry, GeometryCollection):
        for geom in geometry.geoms:
            polygons.extend(_extract_polygons(geom))
    return polygons


def _plot_polygon_on_ax(ax, geometry, color, alpha=0.3, linewidth=1.5):
    """Plot a Shapely geometry on a matplotlib axis."""

    polygons = _extract_polygons(geometry)
    if not polygons:
        return None

    for poly in polygons:
        try:
            x, y = poly.exterior.xy
            ax.fill(
                x,
                y,
                facecolor=color,
                edgecolor=color,
                alpha=alpha,
                linewidth=linewidth,
            )
        except (AttributeError, TypeError, ValueError) as exc:
            logger.warning("Failed to plot polygon: %s", exc)
            continue

    try:
        centroid = (
            polygons[0].centroid
            if len(polygons) == 1
            else unary_union(polygons).centroid
        )
        return centroid.x, centroid.y
    except (AttributeError, TypeError, ValueError):
        return None


def plot_sensor_groups(
    sensors,
    map_file,
    output_map_file,
    label_ids: bool = False,
    separate_images: bool = True,
    scene_name: str | None = None,
    algorithm_name: str | None = None,
):
    """Visualize camera groups by plotting their FOV polygons on the map image."""

    import matplotlib.pyplot as plt

    _, map_image_loaded, map_width, map_height = load_map_data(map_file)
    if map_image_loaded is None:
        plot_sensor_groups_black_background(
            sensors,
            output_map_file,
            label_ids=label_ids,
            separate_images=separate_images,
            scene_name=scene_name,
            algorithm_name=algorithm_name,
        )
        return

    groups = np.unique(np.array([sensor["group"]["name"] for sensor in sensors]))
    logger.info("Generating visualization for %d groups", len(groups))

    sorted_groups = sorted(groups, key=natural_sort_key)
    color_map = {
        group_name: get_cluster_color(idx, as_tuple=True)
        for idx, group_name in enumerate(sorted_groups)
    }

    if separate_images:
        for group_id in groups:
            map_image = map_image_loaded.copy()
            fig, ax = plt.subplots(
                figsize=(map_width / 100, map_height / 100), dpi=100, facecolor="black"
            )
            ax.set_facecolor("black")
            ax.imshow(map_image)

            grouping_sensor_list = []
            group_union_polygon_vis = None
            for sensor in sensors:
                group_value = sensor["group"]["name"]
                if group_value != group_id:
                    continue
                grouping_sensor_list.append(sensor["id"])

                polygon_str = find_field_of_view_polygon(sensor["attributes"])
                polygon = parse_polygon(polygon_str)
                if polygon is None:
                    continue

                if isinstance(polygon, MultiPolygon):
                    polygon = MultiPolygon([poly.buffer(1) for poly in polygon.geoms])
                else:
                    polygon = polygon.buffer(1)

                translation = sensor["translationToGlobalCoordinates"]
                scale = sensor["scaleFactor"]
                transformed_polygon = transform_polygon(polygon, translation, scale)

                if group_union_polygon_vis is None:
                    group_union_polygon_vis = transformed_polygon
                else:
                    group_union_polygon_vis = unary_union(
                        [group_union_polygon_vis, transformed_polygon]
                    )

                centroid_result = _plot_polygon_on_ax(
                    ax, transformed_polygon, color_map[group_value]
                )
                if label_ids and centroid_result:
                    cx, cy = centroid_result
                    _draw_label(ax, cx, cy, sensor.get("id", ""), color="#000000")

            if group_union_polygon_vis:
                centroid_vis = group_union_polygon_vis.centroid
                ax.plot(
                    centroid_vis.x,
                    centroid_vis.y,
                    marker="o",
                    color="green",
                    markersize=10,
                )

            title_parts = []
            if scene_name:
                title_parts.append(scene_name)
            if algorithm_name:
                title_parts.append(algorithm_name)
            title_parts.append(f"{group_id} ({len(grouping_sensor_list)} cameras)")
            ax.set_title(" - ".join(title_parts), color="white", fontsize=12, pad=10)

            base_name, _ = os.path.splitext(output_map_file)
            output_map_grouping_file = f"{base_name}_{group_id}.png"
            logger.info(
                "Saved %s (%d cameras) -> %s",
                group_id,
                len(grouping_sensor_list),
                output_map_grouping_file,
            )
            plt.axis("off")
            plt.savefig(
                output_map_grouping_file,
                bbox_inches="tight",
                pad_inches=0.1,
                facecolor="black",
            )
            plt.close()
        return

    map_image = map_image_loaded.copy()
    fig, ax = plt.subplots(
        figsize=(map_width / 100, map_height / 100), dpi=100, facecolor="black"
    )
    ax.set_facecolor("black")
    ax.imshow(map_image)

    processed_sensors = set()
    for group_id in sorted_groups:
        group_union_polygon_vis = None
        for sensor in sensors:
            group_value = sensor["group"]["name"]
            sensor_id = sensor["id"]
            if group_value != group_id or sensor_id in processed_sensors:
                continue
            processed_sensors.add(sensor_id)

            polygon_str = find_field_of_view_polygon(sensor["attributes"])
            polygon = parse_polygon(polygon_str)
            if polygon is None:
                continue

            if isinstance(polygon, MultiPolygon):
                polygon = MultiPolygon([poly.buffer(1) for poly in polygon.geoms])
            else:
                polygon = polygon.buffer(1)

            translation = sensor["translationToGlobalCoordinates"]
            scale = sensor["scaleFactor"]
            transformed_polygon = transform_polygon(polygon, translation, scale)

            if group_union_polygon_vis is None:
                group_union_polygon_vis = transformed_polygon
            else:
                group_union_polygon_vis = unary_union(
                    [group_union_polygon_vis, transformed_polygon]
                )

            centroid_result = _plot_polygon_on_ax(
                ax, transformed_polygon, color_map[group_value]
            )
            if label_ids and centroid_result:
                cx, cy = centroid_result
                _draw_label(ax, cx, cy, sensor_id, color="#000000")

        if group_union_polygon_vis:
            centroid_vis = group_union_polygon_vis.centroid
            ax.plot(
                centroid_vis.x,
                centroid_vis.y,
                marker="o",
                color=color_map[group_id],
                markeredgecolor="white",
                markersize=10,
                markeredgewidth=2,
            )

    title_parts = []
    if scene_name:
        title_parts.append(scene_name)
    if algorithm_name:
        title_parts.append(algorithm_name)
    title_parts.append(f"{len(sorted_groups)} clusters")
    ax.set_title(" - ".join(title_parts), color="white", fontsize=12, pad=10)
    plt.axis("off")
    plt.savefig(output_map_file, bbox_inches="tight", pad_inches=0.1, facecolor="black")
    plt.close()
    logger.info("Saved combined visualization to: %s", output_map_file)


def plot_sensor_groups_black_background(
    sensors,
    output_map_file,
    image_size=(1920, 1080),
    padding=10,
    label_ids: bool = False,
    separate_images: bool = True,
    scene_name: str | None = None,
    algorithm_name: str | None = None,
):
    """Visualize camera groups by plotting FOV polygons on a black background."""

    import matplotlib.pyplot as plt

    groups = np.unique(np.array([sensor["group"]["name"] for sensor in sensors]))
    logger.info("Generating visualization for %d groups (black background)", len(groups))

    all_polygons = []
    group_data = {group: [] for group in groups}
    for sensor in sensors:
        group_name = sensor["group"]["name"]
        if "attributes" not in sensor:
            continue
        polygon_str = find_field_of_view_polygon(sensor["attributes"])
        polygon = parse_polygon(polygon_str)
        if polygon is not None:
            all_polygons.append(polygon)
            group_data[group_name].append((sensor["id"], polygon))

    if not all_polygons:
        logger.warning("No valid polygons found for visualization")
        return

    min_x, min_y = float("inf"), float("inf")
    max_x, max_y = float("-inf"), float("-inf")
    for polygon in all_polygons:
        bounds = polygon.bounds
        min_x = min(min_x, bounds[0])
        min_y = min(min_y, bounds[1])
        max_x = max(max_x, bounds[2])
        max_y = max(max_y, bounds[3])

    min_x -= padding
    min_y -= padding
    max_x += padding
    max_y += padding
    logger.info(
        "Scene bounds: (%.1f, %.1f) to (%.1f, %.1f)",
        min_x,
        min_y,
        max_x,
        max_y,
    )
    logger.info("Scene size: %.1f x %.1f meters", max_x - min_x, max_y - min_y)

    color_map = {
        group_name: get_cluster_color(idx)
        for idx, group_name in enumerate(sorted(groups, key=natural_sort_key))
    }
    base_name, _ = os.path.splitext(output_map_file)
    fig_width = image_size[0] / 100
    fig_height = image_size[1] / 100

    if separate_images:
        for group_name in sorted(groups, key=natural_sort_key):
            color = color_map[group_name]
            group_polygons = group_data[group_name]
            fig, ax = plt.subplots(
                figsize=(fig_width, fig_height), dpi=100, facecolor="black"
            )
            ax.set_facecolor("black")
            group_union_polygon = None
            sensor_ids = []

            for sensor_id, polygon in group_polygons:
                sensor_ids.append(sensor_id)
                buffered_polygon = polygon.buffer(0.5)
                if group_union_polygon is None:
                    group_union_polygon = buffered_polygon
                else:
                    group_union_polygon = unary_union(
                        [group_union_polygon, buffered_polygon]
                    )

                centroid_result = _plot_polygon_on_ax(ax, buffered_polygon, color)
                if label_ids and centroid_result:
                    cx, cy = centroid_result
                    _draw_label(ax, cx, cy, sensor_id, color="#ffffff")

            if group_union_polygon:
                centroid = group_union_polygon.centroid
                ax.plot(
                    centroid.x,
                    centroid.y,
                    marker="o",
                    color="white",
                    markeredgecolor=color,
                    markersize=10,
                    markeredgewidth=2,
                )

            ax.set_xlim(min_x, max_x)
            ax.set_ylim(min_y, max_y)
            ax.set_aspect("equal")
            ax.axis("off")

            title_parts = []
            if scene_name:
                title_parts.append(scene_name)
            if algorithm_name:
                title_parts.append(algorithm_name)
            title_parts.append(f"{group_name} ({len(sensor_ids)} cameras)")
            ax.set_title(" - ".join(title_parts), color="white", fontsize=10, pad=10)

            output_group_file = f"{base_name}_{group_name}.png"
            plt.savefig(
                output_group_file,
                facecolor="black",
                edgecolor="none",
                bbox_inches="tight",
                pad_inches=0.2,
                dpi=150,
            )
            plt.close()
            logger.info("Saved %s (%d cameras) -> %s", group_name, len(sensor_ids), output_group_file)
        return

    fig, ax = plt.subplots(figsize=(fig_width, fig_height), dpi=100, facecolor="black")
    ax.set_facecolor("black")
    processed_sensors = set()
    for group_name in sorted(groups, key=natural_sort_key):
        color = color_map[group_name]
        group_union_polygon = None
        for sensor_id, polygon in group_data[group_name]:
            if sensor_id in processed_sensors:
                continue
            processed_sensors.add(sensor_id)
            buffered_polygon = polygon.buffer(0.5)
            if group_union_polygon is None:
                group_union_polygon = buffered_polygon
            else:
                group_union_polygon = unary_union([group_union_polygon, buffered_polygon])

            centroid_result = _plot_polygon_on_ax(ax, buffered_polygon, color)
            if label_ids and centroid_result:
                cx, cy = centroid_result
                _draw_label(ax, cx, cy, sensor_id, color="#ffffff")

        if group_union_polygon:
            centroid = group_union_polygon.centroid
            ax.plot(
                centroid.x,
                centroid.y,
                marker="o",
                color=color,
                markeredgecolor="white",
                markersize=10,
                markeredgewidth=2,
            )

    ax.set_xlim(min_x, max_x)
    ax.set_ylim(min_y, max_y)
    ax.set_aspect("equal")
    ax.axis("off")

    title_parts = []
    if scene_name:
        title_parts.append(scene_name)
    if algorithm_name:
        title_parts.append(algorithm_name)
    title_parts.append(f"{len(groups)} clusters")
    ax.set_title(" - ".join(title_parts), color="white", fontsize=10, pad=10)
    plt.savefig(
        output_map_file,
        facecolor="black",
        edgecolor="none",
        bbox_inches="tight",
        pad_inches=0.2,
        dpi=150,
    )
    plt.close()
    logger.info("Saved combined visualization to: %s", output_map_file)


__all__ = [
    "CLUSTER_COLORS",
    "draw_polygon",
    "get_cluster_color",
    "plot_sensor_groups",
    "plot_sensor_groups_black_background",
    "transform_polygon",
]
