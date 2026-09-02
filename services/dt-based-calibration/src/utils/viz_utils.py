#!/usr/bin/env python3

# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
#
# SPDX-License-Identifier: Apache-2.0


"""
Visualization utilities for CARLA Autocalibration

Contains 3D visualization functions for point clouds and camera frustums.
"""

import os
import json
import numpy as np
import cv2
import trimesh
import tempfile
from pathlib import Path


def load_calibration_data(json_path):
    """Load MDX calibration file"""
    with open(json_path, "r") as f:
        return json.load(f)


def create_camera_frustum_mesh(
    intrinsic_matrix, extrinsic_matrix, scale=5.0, color=[255, 0, 0]
):
    """
    Create camera frustum as trimesh object for GLB export

    Returns:
        trimesh mesh: Camera frustum wireframe
    """
    K = np.array(intrinsic_matrix)
    RT = np.array(extrinsic_matrix)
    RT_inv = np.linalg.inv(RT)
    camera_center = RT_inv[:3, 3].copy()
    camera_center[1] = -camera_center[1]  # Fix CARLA Y axis

    # Frustum corners
    w, h = K[0, 2] * 2, K[1, 2] * 2
    corners_2d = np.array([[0, 0], [w, 0], [w, h], [0, h]])

    corners_3d_cam = []
    for u, v in corners_2d:
        x = (u - K[0, 2]) / K[0, 0]
        y = (v - K[1, 2]) / K[1, 1]
        direction = np.array([scale, x * scale, -y * scale, 1.0])
        corners_3d_cam.append(direction)

    corners_3d_world = []
    for corner in corners_3d_cam:
        world_point = RT_inv @ corner
        world_point[1] = -world_point[1]
        corners_3d_world.append(world_point[:3])

    corners_3d_world = np.array(corners_3d_world)

    # Create line segments as cylinders
    vertices = np.vstack([camera_center.reshape(1, 3), corners_3d_world])

    edges = [
        [0, 1],
        [0, 2],
        [0, 3],
        [0, 4],  # center to corners
        [1, 2],
        [2, 3],
        [3, 4],
        [4, 1],  # rectangle
    ]

    meshes = []
    cylinder_radius = scale * 0.02

    for edge in edges:
        start = vertices[edge[0]]
        end = vertices[edge[1]]
        direction = end - start
        height = np.linalg.norm(direction)

        if height < 0.001:
            continue

        cylinder = trimesh.creation.cylinder(
            radius=cylinder_radius, height=height, sections=8
        )

        z_axis = np.array([0, 0, 1])
        edge_direction = direction / height

        if not np.allclose(edge_direction, z_axis):
            rotation_axis = np.cross(z_axis, edge_direction)
            if np.linalg.norm(rotation_axis) > 0.001:
                rotation_axis = rotation_axis / np.linalg.norm(rotation_axis)
                angle = np.arccos(np.clip(np.dot(z_axis, edge_direction), -1, 1))
                rotation_matrix = trimesh.transformations.rotation_matrix(
                    angle, rotation_axis
                )
                cylinder.apply_transform(rotation_matrix)

        midpoint = (start + end) / 2
        cylinder.apply_translation(midpoint)
        cylinder.visual.face_colors = color + [255]
        meshes.append(cylinder)

    if meshes:
        return trimesh.util.concatenate(meshes)
    return None


def depth_to_point_cloud_mesh(
    depth_image,
    rgb_image,
    intrinsic_matrix,
    extrinsic_matrix,
    max_depth=200,
    downsample=2,
):
    """Convert depth + RGB to colored point cloud"""
    K = np.array(intrinsic_matrix)
    RT = np.array(extrinsic_matrix)
    RT_inv = np.linalg.inv(RT)

    h, w = depth_image.shape
    fx, fy = K[0, 0], K[1, 1]
    cx, cy = K[0, 2], K[1, 2]

    points_3d = []
    colors = []

    for v in range(0, h, downsample):
        for u in range(0, w, downsample):
            depth = depth_image[v, u]

            if depth <= 0.1 or depth > max_depth:
                continue

            x_cam = (u - cx) * depth / fx
            y_cam = (v - cy) * depth / fy
            z_cam = depth

            point_cam_ue4 = np.array([z_cam, x_cam, -y_cam, 1.0])
            point_world = RT_inv @ point_cam_ue4
            point_world[1] = -point_world[1]
            points_3d.append(point_world[:3])

            color = rgb_image[v, u, :3]
            colors.append([color[2], color[1], color[0], 255])  # BGR to RGBA

    if len(points_3d) == 0:
        return None

    points_3d = np.array(points_3d)
    colors = np.array(colors, dtype=np.uint8)

    cloud = trimesh.PointCloud(vertices=points_3d, colors=colors)
    return cloud


def compute_scene_bounds(scene):
    """Compute bounding box of scene to auto-position camera"""
    all_vertices = []

    for geom_name in scene.geometry:
        geom = scene.geometry[geom_name]
        if hasattr(geom, "vertices"):
            all_vertices.append(geom.vertices)

    if not all_vertices:
        return np.array([0, 0, 0]), 100

    all_vertices = np.vstack(all_vertices)
    min_bound = all_vertices.min(axis=0)
    max_bound = all_vertices.max(axis=0)
    center = (min_bound + max_bound) / 2
    size = np.linalg.norm(max_bound - min_bound)

    return center, size


def create_3d_scene(
    calibration_path,
    output_dir,
    max_depth=150,
    downsample=4,
    camera_scale=8.0,
    show_cameras=True,
    show_coord_frame=True,
    selected_cameras=None,
):
    """
    Create 3D scene with point clouds and camera frustums

    Args:
        selected_cameras: List of camera IDs to visualize, or None for all

    Returns:
        (glb_path, log_msg, camera_position)
    """
    import traceback

    try:
        if calibration_path is None:
            return None, "❌ Calibration path is None", None
        if isinstance(calibration_path, str) and not os.path.exists(calibration_path):
            return None, f"❌ File not found: {calibration_path}", None

        # Try to load _full.json first (has matrices), fallback to .mdx.json
        calibration_path_full = calibration_path.replace(".mdx.json", "_full.json")
        if os.path.exists(calibration_path_full):
            calib = load_calibration_data(calibration_path_full)
        else:
            calib = load_calibration_data(calibration_path)

        camera_colors = [
            [255, 0, 0],
            [0, 255, 0],
            [0, 0, 255],
            [255, 255, 0],
            [255, 0, 255],
            [0, 255, 255],
        ]

        scene = trimesh.Scene()
        total_points = 0

        for idx, sensor in enumerate(calib["sensors"]):
            cam_id = sensor["id"]

            if selected_cameras is not None and len(selected_cameras) > 0:
                if cam_id not in selected_cameras:
                    continue

            depth_file = Path(output_dir) / f"{cam_id}_depth.npy"
            rgb_file = Path(output_dir) / f"{cam_id}_rgb.png"

            if not rgb_file.exists():
                rgb_file = Path(output_dir) / f"{cam_id}_visualization.png"

            if not depth_file.exists() or not rgb_file.exists():
                continue

            depth = np.load(depth_file)
            rgb = cv2.imread(str(rgb_file))

            if not isinstance(depth, np.ndarray) or not isinstance(rgb, np.ndarray):
                continue
            if depth.size == 0 or rgb.size == 0:
                continue

            K = sensor["intrinsicMatrix"]
            RT = sensor["extrinsicMatrix"]

            cloud = depth_to_point_cloud_mesh(depth, rgb, K, RT, max_depth, downsample)
            if cloud is not None:
                scene.add_geometry(cloud, node_name=f"pointcloud_{cam_id}")
                total_points += len(cloud.vertices)

            if show_cameras:
                color = camera_colors[idx % len(camera_colors)]
                frustum = create_camera_frustum_mesh(
                    K, RT, scale=camera_scale, color=color
                )
                if frustum is not None:
                    scene.add_geometry(frustum, node_name=f"camera_{cam_id}")

        if show_coord_frame:
            coord_frame = trimesh.creation.axis(origin_size=2.0, axis_length=10.0)
            scene.add_geometry(coord_frame, node_name="coordinate_frame")

        # Auto camera position: behind, up, and right
        center, size = compute_scene_bounds(scene)
        camera_distance = size * 1.2
        camera_pos = center + np.array(
            [
                camera_distance * 0.5,  # Right
                -camera_distance * 1.2,  # Behind
                camera_distance * 0.8,  # Up
            ]
        )

        with tempfile.NamedTemporaryFile(suffix=".glb", delete=False) as tmp:
            glb_path = tmp.name

        scene.export(glb_path)

        log_msg = (
            f"✅ Reconstruction complete!\n"
            f"📊 Cameras: {len(calib['sensors'])}\n"
            f"📍 Points: {total_points:,}\n"
            f"⚙️ Downsample: {downsample}x"
        )

        camera_position = (
            float(camera_pos[0]),
            float(camera_pos[1]),
            float(camera_pos[2]),
        )
        return glb_path, log_msg, camera_position

    except Exception as e:
        error_detail = traceback.format_exc()
        return None, f"❌ Error: {str(e)}\n\nTraceback:\n{error_detail}", None
