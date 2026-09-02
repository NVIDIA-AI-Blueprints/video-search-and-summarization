#!/usr/bin/env python3

# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
#
# SPDX-License-Identifier: Apache-2.0


import os
import cv2
import json
import yaml
import time
import carla
import rasterio
from PIL import Image
import re
from rasterio.transform import from_origin
import logging
import argparse
import traceback
import ipaddress
import numpy as np
from pathlib import Path
import matplotlib.pyplot as plt
from pyproj import CRS, Transformer
import xml.etree.ElementTree as ET

"""

Digital Twin-Based Camera Autocalibration Tool using CARLA Simulator
====================================================================

Description:
------------
This script performs automatic camera calibration in a digital twin environment 
using the CARLA simulator. It generates a calibration file and visual 
outputs for each camera based on their position and orientation in the scene.

The tool identifies road points visible to each camera using CARLA's semantic 
segmentation sensor, converts them into geo-coordinates (latitude/longitude), and 
leverages existing calibration scripts to compute camera matrices.


Inputs:
-------
- Camera specification file (YAML): Contains camera positions, orientations, FOV, and resolution.
- Map file (CARLA/OpenDRIVE)
- Carla map name (Town01/Town03)
- Output (output)
- CARLA host and port (default: localhost:2000)

Outputs:
--------
- Calibration file(s) (.json): One per camera or combined, format TBD.
- Contour mask (.png): For each camera, shows selected calibration points

Usage Example:
--------------
    python autocalibration.py \
        --map main.xodr \
        --camera-spec camera_specs.yaml \
        --output-dir ./output/ \
        --carla-host localhost \
        --carla-port 2000 \
        --carla-map=Town10HD

Requirements:
-------------
- Python 3.7+
- CARLA simulator (running)
- carla
- PyYAML
- OpenCV
- NumPy
- matplotlib

"""

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] {%(filename)s:%(lineno)d} {%(processName)s:%(process)d} - %(levelname)s - %(message)s",  # noqa: E501
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

PGM_PATH = os.getenv("PGM_PATH", "/opt/geoids/egm96-15.pgm")
WLD_PATH = os.getenv("WLD_PATH", "/opt/geoids/egm96-15.wld")


def load_camera_specs(yaml_path, geo_reference):
    with open(yaml_path, "r") as f:
        specs = yaml.safe_load(f)
    cameras_obj = {"cameras": []}
    for spec in specs["cameras"]:
        new_spec = spec
        if spec.get("geo_position", False) == True:
            logger.info(f"Using Geo Location!")
            carla_x, carla_y, carla_z = latlon_to_world(
                spec["position"][0],
                spec["position"][1],
                spec["position"][2],
                geo_reference,
                PGM_PATH,
            )
            new_spec["position"][0] = carla_x
            new_spec["position"][1] = carla_y
            new_spec["position"][2] = carla_z
        cameras_obj["cameras"].append(new_spec)
    return cameras_obj


def print_camera_specs(camera):
    """display camera spect"""
    logger.info(f"Camera ID: {camera['id']}")
    logger.info(f"  Position: {camera['position']}")
    logger.info(f"  Orientation: {camera['orientation']}")
    logger.info(f"  FOV: {camera['fov']} degrees")
    logger.info(f"  Resolution: {camera['resolution']}")
    logger.info("##########################################")


def validate_ip_addr(ip):
    """Validate IPv4, IPv6 Addresses, or hostname
    Args:
        ip (str): The ip address or hostname to validate.

    Returns:
        str: The validated ip address or hostname
    """
    # Try to parse as IP address
    try:
        ipaddress.ip_address(ip)
        return ip
    except ValueError:
        # Not an IP, check if it's a valid hostname
        # Allow alphanumeric, dots, hyphens (valid DNS hostname chars)
        import re

        hostname_pattern = r"^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*$"
        if re.match(hostname_pattern, ip) or ip == "localhost":
            return ip
        raise Exception(f"Invalid IP or hostname: {ip}")


def parse_path_file(path):
    """
    Parse the path and make sure it exists

    Parameters:
    path (str): full path on disk

    Returns:
    str: path

    """
    if not os.path.exists(path) or not os.path.isfile(path):
        raise Exception("Invalid file: " + path)
    return path


def parse_path_dir(path):
    """
    Parse the path and make sure it exists

    Parameters:
    path (str): full path on disk

    Returns:
    str: path

    """
    if not os.path.exists(path) or not os.path.isdir(path):
        raise Exception("Invalid output folder: " + path)
    return path


def connect_to_carla(host="localhost", port=2000):
    client = carla.Client(host, port)
    client.set_timeout(60.0)
    return client


def load_custom_xodr_map(client, xodr_path):
    with open(xodr_path, "r") as f:
        xodr_content = f.read()
    world = client.generate_opendrive_world(xodr_content)
    time.sleep(2)  # allow some time to load
    return world


def spawn_single_camera(world, blueprints, camera_spec):
    """
    Spawn a single camera with all its sensors (RGB, Semantic Segmentation, Depth).
    This ensures each camera is processed independently for maximum accuracy.
    
    Args:
        world: CARLA world object
        blueprints: CARLA blueprint library
        camera_spec: Single camera specification dictionary
    
    Returns:
        dict: Dictionary containing the spawned camera sensors and spec
    """
    print_camera_specs(camera_spec)
    cam_id = camera_spec["id"]
    loc = carla.Location(*camera_spec["position"])
    rot = carla.Rotation(*camera_spec["orientation"])
    tf = carla.Transform(loc, rot)
    w, h = camera_spec["resolution"]
    fov = str(camera_spec["fov"])

    # Spawn semantic segmentation camera
    seg_bp = blueprints.find("sensor.camera.semantic_segmentation")
    seg_bp.set_attribute("image_size_x", str(w))
    seg_bp.set_attribute("image_size_y", str(h))
    seg_bp.set_attribute("fov", fov)
    seg_cam = world.spawn_actor(seg_bp, tf)

    # Spawn depth camera
    depth_bp = blueprints.find("sensor.camera.depth")
    depth_bp.set_attribute("image_size_x", str(w))
    depth_bp.set_attribute("image_size_y", str(h))
    depth_bp.set_attribute("fov", fov)
    depth_cam = world.spawn_actor(depth_bp, tf)

    # Spawn RGB camera
    rgb_bp = blueprints.find("sensor.camera.rgb")
    rgb_bp.set_attribute("image_size_x", str(w))
    rgb_bp.set_attribute("image_size_y", str(h))
    rgb_bp.set_attribute("fov", fov)
    rgb_cam = world.spawn_actor(rgb_bp, tf)

    logger.info(f"Spawned camera {cam_id} with 3 sensors (RGB, Seg, Depth)")
    
    return {
        cam_id: {
            "rgb_camera": rgb_cam, 
            "seg_camera": seg_cam, 
            "depth_camera": depth_cam, 
            "spec": camera_spec
        }
    }


def spawn_cameras(world, blueprints, camera_specs):
    """
    Legacy function - spawns all cameras at once.
    Note: For better accuracy, use spawn_single_camera() in a sequential loop.
    """
    all_cams = {}
    for spec in camera_specs:
        single_cam = spawn_single_camera(world, blueprints, spec)
        all_cams.update(single_cam)
    return all_cams


def get_rgb_image(carla_image):
    """
    Converts a CARLA RGB image (BGRA format) to a proper RGB image.

    Parameters:
        carla_image (carla.Image): CARLA sensor.camera.rgb image

    Returns:
        rgb_image (np.ndarray): RGB image (H, W, 3) dtype=uint8
    """
    array = np.frombuffer(carla_image.raw_data, dtype=np.uint8)
    array = array.reshape((carla_image.height, carla_image.width, 4))  # BGRA
    rgb_image = array[:, :, :3][:, :, ::-1]  # BGR → RGB
    return rgb_image


def destroy_cameras(cameras):
    """
    Destroy all camera sensors to free up resources.
    
    Args:
        cameras: Dictionary of camera objects to destroy
    """
    for cam_id, cams in cameras.items():
        try:
            cams["rgb_camera"].destroy()
            cams["seg_camera"].destroy()
            cams["depth_camera"].destroy()
            logger.info(f"Destroyed camera: {cam_id}")
        except Exception as e:
            logger.warning(f"Error destroying camera {cam_id}: {e}")


def collect_images(cameras, world=None, stabilization_ticks=3):
    """
    Collect images from camera sensors with optional world ticks for stabilization.
    
    Args:
        cameras: Dictionary of camera objects
        world: CARLA world object (optional, for stabilization)
        stabilization_ticks: Number of world ticks to wait for sensor stabilization
    
    Returns:
        dict: Dictionary containing captured images and metadata for each camera
    """
    # Allow sensors to stabilize if world is provided
    if world is not None:
        logger.info(f"Stabilizing sensors with {stabilization_ticks} world ticks...")
        for _ in range(stabilization_ticks):
            world.tick()
            time.sleep(0.1)
    
    results = {}
    for cam_id, cams in cameras.items():
        logger.info(f"Collecting images from camera: {cam_id}")
        image_data = {"seg": None, "depth": None, "rgb": None}
        done = {"seg": False, "depth": False, "rgb": False}

        def seg_callback(image):
            # rgb_image_latest = get_rgb_image(image)
            # cv2.imwrite(f"rgb_camera_{cam_id}.png", rgb_image_latest)
            # extract_contours_from_seg(image,cams["spec"]["label"])
            arr = (
                np.frombuffer(image.raw_data, dtype=np.uint8)
                .copy()
                .reshape((image.height, image.width, 4))
            )
            image_data["seg"] = arr[:, :, 2]  # Blue channel
            done["seg"] = True

        def depth_callback(image):
            # arr = np.frombuffer(image.raw_data, dtype=np.uint8).copy().reshape((image.height, image.width, 4))
            # norm = (arr[:, :, 0].astype(np.uint32) +
            #         arr[:, :, 1].astype(np.uint32) * 256 +
            #         arr[:, :, 2].astype(np.uint32) * 256 * 256) / (256**3 - 1)
            # depth = 1000 * norm
            depth = decode_carla_depth(image)
            image_data["depth"] = depth
            done["depth"] = True

        def rgb_callback(image):
            array = np.frombuffer(image.raw_data, dtype=np.uint8)
            array = np.reshape(array, (image.height, image.width, 4))
            image_data["rgb"] = array
            done["rgb"] = True

        # Start listening
        cams["seg_camera"].listen(seg_callback)
        cams["depth_camera"].listen(depth_callback)
        cams["rgb_camera"].listen(rgb_callback)

        # Wait for all sensors to capture
        timeout = time.time() + 10
        while not all(done.values()) and time.time() < timeout:
            time.sleep(0.1)

        # Stop listening
        cams["seg_camera"].stop()
        cams["depth_camera"].stop()
        cams["rgb_camera"].stop()

        if not all(done.values()):
            logger.warning(f"Timeout waiting for images from camera {cam_id}. Status: {done}")

        results[cam_id] = {
            "seg_image": image_data["seg"],
            "rgb_image": image_data["rgb"],
            "depth": image_data["depth"],
            "fov": cams["spec"]["fov"],
            "label": cams["spec"]["label"],
            "fps": cams["spec"]["fps"],
            "resolution": cams["spec"]["resolution"],
            "position": cams["spec"]["position"],
            "transform": cams["seg_camera"].get_transform(),
        }
        logger.info(f"Successfully collected images from camera: {cam_id}")
    
    return results


def get_camera_intrinsics(fov_deg, width, height):
    """Get camera intrisics

    Parameters:
        fov (int): field of view in degree
        image_w (int): image width
        image_h (int): image height

    Returns:
        np.array
    """
    fov_rad = np.deg2rad(fov_deg)
    f = width / (2 * np.tan(fov_rad / 2))
    cx, cy = width / 2, height / 2
    return np.array([[f, 0, cx], [0, f, cy], [0, 0, 1]])


def visualize_drivable_surface(seg, label):
    """
    Creates a color image where drivable surface (class 7) is shown in yellow.
    All other pixels are black.

    Parameters:
        seg (np.ndarray): 2D array of class labels (uint8), shape (H, W)
        label (int): label id of the object of interest

    Returns:
        color_mask (np.ndarray): 3D uint8 image (H, W, 3)
    """
    # Create empty black image
    color_mask = np.zeros((seg.shape[0], seg.shape[1], 3), dtype=np.uint8)

    # Mask for road class (7)
    road_mask = seg == label

    # Set Black color for road pixels [B, G, R] = [255, 255, 255]
    color_mask[road_mask] = [255, 255, 255]  # OpenCV uses BGR

    return color_mask


def normalize_path(path_str: str) -> Path:
    """Normalize path"""
    return Path(path_str).expanduser().resolve()


def extract_2d_3d(seg, depth, intrinsics, label, max_pts=200):
    h, w = seg.shape
    fx, fy = intrinsics[0, 0], intrinsics[1, 1]
    cx, cy = intrinsics[0, 2], intrinsics[1, 2]
    logger.info("Seg shape: %s", None if seg is None else seg.shape)
    logger.info("Seg dtype: %s", None if seg is None else seg.dtype)
    road_mask = seg == label

    indices = np.argwhere(road_mask)

    if len(indices) == 0:
        raise ValueError("No road pixels found.")

    sampled = indices[
        np.random.choice(len(indices), min(max_pts, len(indices)), replace=False)
    ]
    image_points, object_points = [], []

    for v, u in sampled:
        z = depth[v, u]
        if z <= 0.1:
            continue
        x = (u - cx) * z / fx
        y = (v - cy) * z / fy
        image_points.append([u, v])
        object_points.append([x, y, z])

    return np.array(object_points, dtype=np.float32), np.array(
        image_points, dtype=np.float32
    )


def pixels_to_geo_coords(geo_ref, img_obj, visualization_file_path, sample_step=25):
    """
    Convert contour-based pixels with a given class label to geo-coordinates.

    For each pixel on the contour, sample every 25th point and compute GPS coordinates.
    Also draws the selected contour and points to an output image.
    """

    label = img_obj["label"]
    # Step 1: Create binary mask from segmentation
    binary_mask = np.uint8(img_obj["seg_image"] == label) * 255

    # Step 2: Extract contours from mask
    contours, _ = cv2.findContours(
        binary_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE
    )

    # exits if no pixel for label
    if not contours:
        raise ValueError(f"No contours found for label {label}.")

    # Step 3: Collect all contour points
    all_points = []
    for contour in contours:
        for pt in contour:
            x, y = pt[0]
            all_points.append((x, y))  # (u, v)

    # Step 4: Sample every sample_step'th point
    sampled_pixels = all_points[::sample_step]
    if not sampled_pixels:
        raise ValueError("No contour points available after sampling.")

    # Step 5: Draw original contour and sampled points for visualization
    vis_img = cv2.cvtColor(img_obj["rgb_image"], cv2.COLOR_BGRA2BGR)
    cv2.drawContours(
        vis_img, contours, -1, (0, 255, 0), 1
    )  # draw full contours in green
    for (u, v) in sampled_pixels:
        cv2.circle(
            vis_img, (u, v), radius=3, color=(0, 0, 255), thickness=-1
        )  # sampled points in red

    # os.makedirs(os.path.dirname(output_path), exist_ok=True)
    cv2.imwrite(visualization_file_path, vis_img)
    logger.info("Contour visualization saved: {}".format(visualization_file_path))

    # Step 6: Camera to world transformation
    geo_coords = []
    K = build_intrinsic(
        img_obj["resolution"][0], img_obj["resolution"][1], img_obj["fov"]
    )
    for u, v in sampled_pixels:
        depth_point = float(img_obj["depth"][v, u])
        world_coord = pixel_to_world(u, v, depth_point, K, img_obj["transform"])
        # pix_coor = world_to_pixel(world_coord, K, img_obj["transform"])
        # validations = round(pix_coor[0]) == u and round(pix_coor[1]) == v
        # print("validation: {}".format(validations))
        if depth_point <= 0.1:
            continue

        # loc = carla.Location(x=world_coord[0], y=world_coord[1], z=world_coord[2])

        geo = world_to_latlon(world_coord[0], world_coord[1], geo_ref)
        logger.info(f"pixel: ({u}, {v})")
        logger.info(f"geo: {geo}\n")
        geo_coords.append({"pixel": (int(u), int(v)), "lat": geo[0], "lon": geo[1]})

    return geo_coords


def compute_camera_matrix(obj_pts, img_pts, intrinsics):
    """Generate Calibration Matrix using cv2.solvePnP"""

    dist = np.zeros((4, 1))
    success, rvec, tvec = cv2.solvePnP(obj_pts, img_pts, intrinsics, dist)
    if not success:
        raise RuntimeError("solvePnP failed.")
    return rvec, tvec


def write_combined_mdx(config_arr, output_dir):
    # Create MDX version (without matrices for mdxui compatibility)
    sensors_mdx = []
    for sensor in config_arr:
        sensor_mdx = sensor.copy()
        sensor_mdx.pop("intrinsicMatrix", None)
        sensor_mdx.pop("extrinsicMatrix", None)
        sensors_mdx.append(sensor_mdx)

    data = {
        "version": "1.0",
        "osmURL": "",
        "calibrationType": "geo",
        "sensors": config_arr
        # "corridors": [
        #     {
        #         "directions": [
        #             "E",
        #             "W"
        #         ],
        #         "length": 0,
        #         "name": "xxxx",
        #         "sensors": [
        #             "xxxx"
        #         ]
        #     }
        # ]
    }
    path = os.path.join(output_dir, f"combined_calibration.mdx.json")
    with open(path, "w") as f:
        json.dump(data, f, indent=4)
    logger.info(f"📁 Saved: {path}")

    # Create full version with matrices for 3D visualization
    data_full = {
        "version": "1.0",
        "osmURL": "",
        "calibrationType": "geo",
        "sensors": config_arr,  # Full sensors with matrices
    }
    path_full = os.path.join(output_dir, f"combined_calibration_full.json")
    with open(path_full, "w") as f:
        json.dump(data_full, f, indent=4)
    logger.info(f"📁 Saved (with matrices): {path_full}")


def write_mdx(
    cam_id,
    city,
    intrinsics,
    extrinsic,
    depth_img,
    imgs,
    cam_geo_coordinate,
    geo_points,
    output_dir,
):
    """Write MDX Calibration File"""
    # R, _ = cv2.Rodrigues(rvec)
    sensor = {
        "type": "camera",
        "id": cam_id,
        # geoLocation of the intersection
        "origin": {"lng": 0, "lat": 0},
        # geoLocation of the camera. convert posision ins camera spec to geo
        "geoLocation": {
            "lng": round(cam_geo_coordinate[1], 6),
            "lat": round(cam_geo_coordinate[0], 6),
        },
        # get place(town) form spec file or arg
        "place": [
            {"name": "city", "value": city},
            {"name": "intersection", "value": "Walsh Avenue"},
        ],
        "imageCoordinates": [
            {"x": px[0], "y": px[1]} for item in geo_points for px in [item["pixel"]]
        ],
        "globalCoordinates": [
            {"x": round(item["lon"], 6), "y": round(item["lat"], 6)}
            for item in geo_points
        ],
        "scaleFactor": 1,
        "tripwires": [],
        "rois": [
            {
                "id": "roi-id-1",
                "roiCoordinates": [
                    {"x": round(item["lon"], 6), "y": round(item["lat"], 6)}
                    for item in geo_points
                ],
            }
        ],
        "intrinsicMatrix": intrinsics.round(6).tolist(),
        "extrinsicMatrix": extrinsic.round(6).tolist(),
        "attributes": [
            {
                # get PFS from spec file
                "name": "fps",
                "value": str(imgs["fps"]),
            },
            {"name": "depth", "value": str(depth_to_meters(depth_img))},
            {
                # get FOV from spec file
                "name": "fieldOfView",
                "value": str(imgs["fov"]),
            },
            # {
            #     "name": "direction",
            #     "value": "330.0349098"
            # },
            {"name": "source", "value": "vst"},
            {
                # get FPS Width from spec file resolution
                "name": "frameWidth",
                "value": str(imgs["resolution"][0]),
            },
            {
                # get FPS Height from spec file resolution
                "name": "frameHeight",
                "value": str(imgs["resolution"][1]),
            },
        ],
        "coordinates": {
            "x": imgs["position"][0],
            "y": imgs["position"][1],
            "z": imgs["position"][2],
        },
    }
    data = {
        "version": "1.0",
        "osmURL": "",
        "calibrationType": "geo",
        "sensors": [sensor],
    }
    path = os.path.join(output_dir, f"{cam_id}_calibration.mdx.json")
    with open(path, "w") as f:
        json.dump(data, f, indent=4)
    logger.info(f"📁 Saved: {path}")
    return sensor


def depth_to_meters(depth_img):
    """
    Get the center pixel depth value from a CARLA depth map.


    Parameters
    ----------
    depth : np.ndarray of shape (H, W)
        2D array containing depth values in meters.
        - H = image height (rows, pixels)
        - W = image width (columns, pixels)

    Returns
    -------
    float
        Depth at the center pixel (meters).
    """

    # could get diff type of depth.
    # Minimum: min_value = depth_img.min()
    # maximux: max_value = depth_img.max()
    # mean_value = depth_img.mean()
    h, w = depth_img.shape

    return float(depth_img[h // 2, w // 2])


def carla_to_opendrive(location):
    """Convert CARLA Location → OpenDRIVE (x,y,z)."""
    return (location.x, -location.y, location.z)


def opendrive_to_gps(x_od, y_od, to_latlon):
    """Convert OpenDRIVE (x,y) → GPS (lat, lon)."""
    lon, lat = to_latlon.transform(x_od, y_od)
    gps = {"longitude": lon, "latitude": lat}
    return gps


def carla_to_gps(location, to_latlon):
    """Convert CARLA Location → GPS (lat, lon)."""
    x_od, y_od, _ = carla_to_opendrive(location)
    return opendrive_to_gps(x_od, y_od, to_latlon)


def extract_geo_reference(world):
    """
    Extract the geo reference from the word


    Parameters
    ----------
    world : Carla world

    Returns
    -------
    string
        Geo reference.
    """

    xodr_content = world.get_map().to_opendrive()
    root = ET.fromstring(xodr_content)

    geo_ref = None
    header = root.find("header")
    if header is not None:
        geo = header.find("geoReference")
        if geo is not None and geo.text:
            geo_ref = geo.text.strip()

    return geo_ref


def build_intrinsic(width, height, fov):
    focal = width / (2.0 * np.tan(fov * np.pi / 360.0))
    return np.array(
        [[focal, 0.0, width / 2.0], [0.0, focal, height / 2.0], [0.0, 0.0, 1.0]]
    )


def decode_carla_depth(depth_image):
    """
    Decode a CARLA depth image into a 2D NumPy array of depth values in meters.

    Args:
        depth_image (carla.Image): Depth image from a CARLA camera sensor.
            Each pixel encodes depth information in the BGR channels.

    Returns:
        np.ndarray: 2D array (H x W) of depth values in meters.
    """
    array = np.frombuffer(depth_image.raw_data, dtype=np.uint8)
    array = array.reshape((depth_image.height, depth_image.width, 4))
    B = array[:, :, 0].astype(np.float32)
    G = array[:, :, 1].astype(np.float32)
    R = array[:, :, 2].astype(np.float32)
    normalized = (R + G * 256.0 + B * 256.0 * 256.0) / (256.0**3 - 1.0)
    return normalized * 1000.0


def get_extrinsic_matrix(transform):

    # retrieve location
    location = transform.location

    # retrieve rotation
    rotation = transform.rotation

    # Get Rotation matrix (roll, pitch, yaw in degrees)
    pitch = np.deg2rad(rotation.pitch)
    yaw = np.deg2rad(rotation.yaw)
    roll = np.deg2rad(rotation.roll)

    # rotate yaw
    R_yaw = np.array(
        [[np.cos(yaw), -np.sin(yaw), 0], [np.sin(yaw), np.cos(yaw), 0], [0, 0, 1]]
    )
    # rotate pitch
    R_pitch = np.array(
        [
            [np.cos(pitch), 0, np.sin(pitch)],
            [0, 1, 0],
            [-np.sin(pitch), 0, np.cos(pitch)],
        ]
    )
    # rotate roll
    R_roll = np.array(
        [[1, 0, 0], [0, np.cos(roll), -np.sin(roll)], [0, np.sin(roll), np.cos(roll)]]
    )

    # Final rotation matrix
    R = R_yaw @ R_pitch @ R_roll

    # Translation vector
    T = np.array([[location.x], [location.y], [location.z]])

    # Compose extrinsic matrix [R | T]
    RT = np.hstack((R, T))

    # Convert to 4x4 homogeneous matrix
    extrinsic = np.vstack((RT, [0, 0, 0, 1]))

    # Invert to get world-to-camera (since CARLA gives camera-to-world)
    extrinsic_inv = np.linalg.inv(extrinsic)
    return extrinsic_inv


def pixel_to_world(u, v, depth_m, K, camera_transform):
    """Converts a 2D pixel coordinate with depth back to a 3D world coordinate.

    Args:
        u (float): The horizontal (column) coordinate of the pixel.
        v (float): The vertical (row) coordinate of the pixel.
        depth_m (float): The depth of the point in meters, as measured along the camera's forward axis.
        K (numpy.ndarray): The 3x3 camera intrinsic matrix
        camera_transform (object): An object representing the camera's pose (position and orientation) in the world.

    Returns:
        tuple: A tuple of floats `(X, Y, Z)` representing the calculated
        coordinates of the point in the 3D world.
    """

    fx, fy = K[0, 0], K[1, 1]
    cx, cy = K[0, 2], K[1, 2]
    x_std = (u - cx) * depth_m / fx
    y_std = (v - cy) * depth_m / fy
    z_std = float(depth_m)
    point_cam_ue4 = np.array([z_std, x_std, -y_std, 1.0], dtype=np.float64)
    w2c = np.array(camera_transform.get_inverse_matrix(), dtype=np.float64)
    c2w = np.linalg.inv(w2c)
    world_point_h = c2w.dot(point_cam_ue4)
    return tuple((world_point_h[:3] / world_point_h[3]).tolist())


def world_to_pixel(world_xyz, K, camera_transform):
    """Projects a 3D world point to 2D pixel coordinates.

    Args:
        world_xyz (tuple): A tuple of floats (x, y, z)
        K (numpy.ndarray): The 3x3 camera intrinsic matrix
        camera_transform (object): An object representing the camera's pose (position and orientation)

    Returns:
        tuple: A tuple containing (u, v, depth).
            - u (float): The horizontal pixel coordinate on the image.
            - v (float): The vertical pixel coordinate on the image.
    """

    xw, yw, zw = world_xyz
    w2c = np.array(camera_transform.get_inverse_matrix(), dtype=np.float64)
    p_world = np.array([xw, yw, zw, 1.0], dtype=np.float64)
    pc = w2c.dot(p_world)
    x_cam_std, y_cam_std, z_cam_std = pc[1], -pc[2], pc[0]
    if z_cam_std <= 0:
        return None, None
    proj = K.dot([x_cam_std, y_cam_std, z_cam_std])
    u, v = proj[0] / proj[2], proj[1] / proj[2]
    return float(u), float(v)


def world_to_latlon(carla_x: float, carla_y: float, geo_reference: str):
    """
    Converts CARLA world coordinates (x, y) to geographic coordinates (lat, lon)
    based on a given geo-reference string.

    Parameters:
        carla_x (float): CARLA x (East) in meters
        carla_y (float): CARLA y (North) in meters
        geo_reference (str): PROJ string defining the map projection

    Returns:
        (lat, lon): Tuple of latitude and longitude in degrees
    """

    # Create CRS from proj string
    crs = CRS.from_proj4(geo_reference)

    # Check for false easting/northing (UTM-style projections)
    proj_dict = crs.to_dict()
    origin_easting = float(proj_dict.get("x_0", 0))
    origin_northing = float(proj_dict.get("y_0", 0))

    # Convert CARLA coordinates to projected coordinates (easting/northing)
    proj_x = origin_easting + carla_x
    # negate the carla_y to comply with openderive
    proj_y = origin_northing - carla_y

    # Create transformer from local projection to WGS84 (EPSG:4326)
    transformer = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)

    # Transform to lat/lon
    lon, lat = transformer.transform(proj_x, proj_y)

    return lat, lon


def latlon_to_world(
    lat: float, lon: float, alt: float, geo_reference: str, pgm_path: str
):
    """
    Converts geographic coordinates (lat, lon) to CARLA world coordinates (x, y, z)
    based on a given geo-reference string and EGM96 geoid file.

    Parameters:
        lat (float): Latitude in degrees
        lon (float): Longitude in degrees
        alt: orthometric altitude (meters above mean sea level)
        geo_reference (str): PROJ string defining the map projection
        pgm_path: path to egm96-15.pgm

    Returns:
        (carla_x, carla_y, carla_z): Tuple of CARLA world coordinates in meters
    """

    # --- Load offset and scale from PGM file metadata using PIL ---
    with Image.open(pgm_path) as img:
        info = img.info.get("comment", "")
        scale_match = re.search(r"Scale:\s*([-\d.]+)", info, re.IGNORECASE)
        offset_match = re.search(r"Offset:\s*([-\d.]+)", info, re.IGNORECASE)

        if scale_match and offset_match:
            scale = float(scale_match.group(1))
            offset = float(offset_match.group(1))
        else:
            # Fallback to standard EGM96-15' values if not found in comments
            # These are typical values for GeographicLib files as noted in docs
            scale = 0.003
            offset = -108.0

    logger.info(f"Geoid scale: {scale}, offset: {offset}")

    lon_wrapped = lon if lon >= 0 else lon + 360
    logger.info(f"Initial lat:{lat} lon:{lon}")
    with rasterio.open(pgm_path) as src:
        logger.info(f"Raster width:{src.width}, height:{src.height}")
        logger.info(f"Raster bounds: {src.bounds}")

        # Get row/col index safely
        row, col = src.index(lon_wrapped, lat)
        # Clamp to valid indices
        row = min(max(row, 0), src.height - 1)
        col = min(max(col, 0), src.width - 1)

        # Read the raw pixel value (it's 16-bit, so rasterio handles this)
        geoid_data = src.read(1)
        pixel_value = float(geoid_data[row, col])

        # Calculate the actual geoid height N using the correct scale and offset
        geoid_correction = offset + scale * pixel_value
        logger.info(f"Geoid correction (N) at point: {geoid_correction:.2f} m")

    # Create CRS from proj string (remains the same)
    crs = CRS.from_proj4(geo_reference)
    proj_dict = crs.to_dict()
    origin_easting = float(proj_dict.get("x_0", 0))
    origin_northing = float(proj_dict.get("y_0", 0))

    transformer = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    proj_x, proj_y = transformer.transform(lon, lat)

    carla_x = proj_x - origin_easting
    carla_y = -(proj_y - origin_northing)
    # Disable GeoID for now
    # carla_z = alt + geoid_correction
    carla_z = alt

    return carla_x, carla_y, carla_z


def main_processor(
    output_dir: str, carla_host: str, carla_port: str, carla_map: str, camera_spec: str
):

    output_dir = normalize_path(output_dir)
    os.makedirs(output_dir, exist_ok=True)
    combined_config = []

    client = connect_to_carla(carla_host, carla_port)

    # loading this can take time. If the the default map is already carla_map, we should ommit --carla-map
    if carla_map:
        client.load_world(carla_map)
    world = client.get_world()
    current_map = world.get_map()
    logger.info(f"Using current map {current_map}")
    blueprints = world.get_blueprint_library()

    # parse geo reference
    geo_ref = extract_geo_reference(world)
    specs = load_camera_specs(camera_spec, geo_ref)
    
    # Process each camera sequentially for maximum accuracy
    logger.info("=" * 60)
    logger.info("SEQUENTIAL CAMERA PROCESSING MODE")
    logger.info(f"Total cameras to process: {len(specs['cameras'])}")
    logger.info("=" * 60)
    
    for camera_idx, camera_spec_item in enumerate(specs["cameras"], 1):
        cam_id = camera_spec_item["id"]
        logger.info("")
        logger.info("=" * 60)
        logger.info(f"Processing Camera {camera_idx}/{len(specs['cameras'])}: {cam_id}")
        logger.info("=" * 60)
        
        # Step 1: SPAWN camera
        logger.info(f"[{cam_id}] Step 1/5: Spawning camera sensors...")
        cameras = spawn_single_camera(world, blueprints, camera_spec_item)
        
        # Step 2: STABILIZE - Wait for sensors to initialize
        logger.info(f"[{cam_id}] Step 2/5: Stabilizing sensors...")
        world.tick()
        time.sleep(1.0)  # Allow sensors to fully initialize
        
        # Step 3: CAPTURE images
        logger.info(f"[{cam_id}] Step 3/5: Capturing images...")
        images = collect_images(cameras, world=world, stabilization_ticks=3)
        
        # Step 4: PROCESS calibration
        logger.info(f"[{cam_id}] Step 4/5: Processing calibration...")
        imgs = images[cam_id]
        intrinsics = get_camera_intrinsics(imgs["fov"], imgs["resolution"][0], imgs["resolution"][1])
        seg_img = imgs["seg_image"]
        depth_img = imgs["depth"]
        
        # Validate captured data
        if seg_img is None or depth_img is None or imgs["rgb_image"] is None:
            logger.error(f"[{cam_id}] Missing image data! Seg: {seg_img is not None}, Depth: {depth_img is not None}, RGB: {imgs['rgb_image'] is not None}")
            # Clean up this camera before continuing
            destroy_cameras(cameras)
            continue
        
        # print unique label for debug purposes
        unique_labels = np.unique(seg_img)
        logger.info(f"[{cam_id}] Available labels in segmentation: {unique_labels}")
        
        if imgs['label'] not in unique_labels:
            logger.warning(f"[{cam_id}] Label {imgs['label']} not found in frame. Skipping camera!")
            # Clean up this camera before continuing
            destroy_cameras(cameras)
            continue
        
        try:
            visualization_file_path = os.path.join(output_dir, f"{cam_id}_visualization.png")
            
            # Save depth for reference
            depth_file = os.path.join(output_dir, f"{cam_id}_depth.npy")
            np.save(depth_file, depth_img)
            logger.info(f"[{cam_id}] Saved depth: {depth_file}")
            
            # Get geo points for sampled drivable pixels
            geo_points = pixels_to_geo_coords(geo_ref, imgs, visualization_file_path, sample_step=50)
            
            # cam location
            cam_geo_coordinate = world_to_latlon(imgs["position"][0], imgs["position"][1], geo_ref)
            
            # get sensor transform and extrinsic
            sensor_transform = imgs["transform"]
            extrinsic = get_extrinsic_matrix(sensor_transform)
            
            # Write MDX calibration file
            single_config = write_mdx(cam_id, current_map.name.split("/")[-1], intrinsics, extrinsic, depth_img, imgs, cam_geo_coordinate, geo_points, output_dir)
            combined_config.append(single_config)
            
            logger.info(f"[{cam_id}] ✓ Calibration completed successfully")
            
        except Exception as e:
            logger.error(f"[{cam_id}] Failed to process calibration: {e}")
            logger.error(traceback.format_exc())
        
        # Step 5: DESTROY camera immediately to free resources
        logger.info(f"[{cam_id}] Step 5/5: Destroying camera sensors...")
        destroy_cameras(cameras)
        
        # Wait before processing next camera
        world.tick()
        time.sleep(0.5)
        
        logger.info(f"[{cam_id}] Camera processing complete")
        logger.info("=" * 60)

    # Write combined calibration file
    logger.info("")
    logger.info("=" * 60)
    logger.info("FINALIZING")
    logger.info("=" * 60)
    write_combined_mdx(combined_config, output_dir)
    logger.info(f"✓ Successfully processed {len(combined_config)}/{len(specs['cameras'])} cameras")
    logger.info("=" * 60)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--camera-spec",
        type=parse_path_file,
        required=True,
        help="Path to camera spec YAML",
    )
    parser.add_argument(
        "--output-dir",
        type=parse_path_dir,
        required=True,
        help="Output directory for calibration",
    )
    parser.add_argument(
        "--carla-host",
        type=validate_ip_addr,
        default="localhost",
        help="Carla server IP",
    )
    parser.add_argument(
        "--carla-port", type=int, default=2000, help="Carla server port"
    )
    parser.add_argument(
        "--carla-map",
        help="Map to use/ loading this can take time. If the the default map is already carla_map, we should omit --carla-map",
    )
    args = parser.parse_args()
    
    # Delegate to main_processor with parsed arguments
    main_processor(
        output_dir=args.output_dir,
        carla_host=args.carla_host,
        carla_port=args.carla_port,
        carla_map=args.carla_map,
        camera_spec=args.camera_spec
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.error("Failed to execute script: %s", repr(e))
        logger.error(traceback.format_exc())
