#!/usr/bin/env python3

# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
#
# SPDX-License-Identifier: Apache-2.0


"""
CARLA utilities for autocalibration web demo

Contains server connection checks, calibration execution, and camera preview.
"""

import os
import sys
import time
import socket
import subprocess
import zipfile
import carla
import rasterio
from PIL import Image
import re
from rasterio.transform import from_origin
import numpy as np
from pathlib import Path
from datetime import datetime
from pyproj import CRS, Transformer
import xml.etree.ElementTree as ET


PGM_PATH = os.getenv("PGM_PATH", "/opt/geoids/egm96-15.pgm")


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

    lon_wrapped = lon if lon >= 0 else lon + 360
    with rasterio.open(pgm_path) as src:

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


def check_carla_server(host, port, timeout=60):
    """
    Check if CARLA server is running and accessible

    Returns:
        (is_running, message)
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, int(port)))
        sock.close()

        if result == 0:
            return True, f"✅ CARLA server is running at {host}:{port}"
        else:
            return (
                False,
                f"❌ Cannot connect to CARLA server at {host}:{port}\n\nPlease make sure CARLA is running:\n./CarlaUE4.sh",
            )
    except socket.gaierror:
        return False, f"❌ Invalid hostname: {host}"
    except Exception as e:
        return False, f"❌ Connection error: {str(e)}"


def get_available_maps(host, port, timeout=60):
    """
    Get list of available maps from CARLA server

    Returns:
        (success, maps_list_or_error_message)
    """
    try:
        # Check connection first
        is_running, msg = check_carla_server(host, port, timeout)
        if not is_running:
            return False, ["Error: CARLA not connected"]

        # Connect to CARLA
        client = carla.Client(host, int(port))
        client.set_timeout(timeout)

        # Get available maps
        maps = client.get_available_maps()

        # Extract map names (remove path prefix)
        map_names = []
        for map_path in maps:
            # Extract just the map name (e.g., "Town10HD_Opt" from "/Game/Carla/Maps/Town10HD_Opt")
            map_name = os.path.basename(map_path)
            map_names.append(map_name)

        # Sort alphabetically
        map_names.sort()

        print(f"[DEBUG] Found {len(map_names)} available maps")
        return True, map_names

    except Exception as e:
        print(f"Error getting maps: {e}")
        return False, [f"Error: {str(e)}"]


def run_autocalibration(
    camera_spec_file, output_dir, carla_host, carla_port, carla_map
):
    """
    Run autocalibration.py with given parameters

    Returns:
        (success, log_message, output_files)
    """
    import traceback
    import sys
    from pathlib import Path

    # Get the absolute path to the directory containing THIS file (src/utils)
    current_dir = str(Path(__file__).parent.resolve())

    # Inject it into the search path so sibling imports work
    if current_dir not in sys.path:
        sys.path.insert(0, current_dir)

    try:
        import autocalibration

        mp = autocalibration.main_processor
    except ImportError:
        # Fallback for PyInstaller's flattened namespace
        from utils import autocalibration

        mp = autocalibration.main_processor

    try:
        if camera_spec_file is None:
            return False, "❌ Please upload camera_specs.yml file", []

        # Check CARLA server
        is_running, server_msg = check_carla_server(carla_host, carla_port, timeout=60)
        if not is_running:
            return False, server_msg, []

        # Handle file upload
        if hasattr(camera_spec_file, "name"):
            spec_path = camera_spec_file.name
        elif isinstance(camera_spec_file, dict) and "name" in camera_spec_file:
            spec_path = camera_spec_file["name"]
        elif isinstance(camera_spec_file, str):
            spec_path = camera_spec_file
        else:
            return False, f"❌ Invalid file format: {type(camera_spec_file)}", []

        # Create output directory
        if not output_dir or len(output_dir.strip()) == 0:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_dir = f"./output/{timestamp}"

        os.makedirs(output_dir, exist_ok=True)

        # Build command with absolute paths
        utils_dir = os.path.dirname(os.path.abspath(__file__))
        project_dir = os.path.dirname(utils_dir)

        # Convert output_dir to absolute path
        if not os.path.isabs(output_dir):
            output_dir = os.path.abspath(os.path.join(project_dir, output_dir))
        print(f"[DEBUG] Working directory: {project_dir}")
        mp(output_dir, carla_host, carla_port, carla_map, spec_path)

        # Find output files
        output_files = {"calibration": [], "visualization": [], "depth": [], "rgb": []}

        for f in os.listdir(output_dir):
            full_path = os.path.join(output_dir, f)
            if (
                f.endswith("_calibration.mdx.json")
                or f == "combined_calibration.mdx.json"
            ):
                output_files["calibration"].append(full_path)
            elif f.endswith("_visualization.png"):
                output_files["visualization"].append(full_path)
                rgb_candidate = os.path.join(
                    output_dir, f.replace("_visualization.png", "_rgb.png")
                )
                if not os.path.exists(rgb_candidate):
                    output_files["rgb"].append(full_path)
            elif f.endswith("_depth.npy"):
                output_files["depth"].append(full_path)
            elif f.endswith("_rgb.png"):
                output_files["rgb"].append(full_path)

        success_msg = f"✅ Calibration complete!\n\n"
        success_msg += f"📁 Output directory: {output_dir}\n"
        success_msg += f"📊 Calibration files: {len(output_files['calibration'])}\n"
        success_msg += (
            f"🖼️ Visualization images: {len(output_files['visualization'])}\n"
        )
        success_msg += f"📍 Depth maps: {len(output_files['depth'])}\n"

        return True, success_msg, output_files

    except subprocess.TimeoutExpired:
        return False, "❌ Calibration timeout (>5 minutes)", []
    except Exception as e:
        error_detail = traceback.format_exc()
        return False, f"❌ Error: {str(e)}\n\n{error_detail}", []


def create_download_zip(out_dir):
    """Create zip file with all outputs"""
    if out_dir is None or not os.path.isdir(out_dir):
        return None, "⚠️ No data to download. Run calibration first."

    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        zip_name = f"calibration_results_{timestamp}.zip"
        zip_path = os.path.join(out_dir, zip_name)

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(out_dir):
                for file in files:
                    if file == zip_name:
                        continue
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, out_dir)
                    zipf.write(file_path, arcname)

        file_count = len([f for f in os.listdir(out_dir) if f != zip_name])
        return zip_path, f"✅ Created {zip_name} with {file_count} files"
    except Exception as e:
        return None, f"❌ Error creating zip: {str(e)}"


def extract_geo_reference(world):
    """
    Extract the geo reference from the world


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


def batch_preview_frames(cameras_specs_list, carla_host, carla_port, carla_map=None):
    """

    Spawns all cameras at once, captures all frames, then destroys all.

    Args:
        cameras_specs_list: List of camera spec dicts
        carla_host: CARLA server host
        carla_port: CARLA server port
        carla_map: Optional map to load

    Returns:
        List of (rgb_image, camera_spec) tuples
    """
    cameras = []
    try:
        import time

        start_time = time.time()

        # Connect to CARLA once
        client = carla.Client(carla_host, int(carla_port))
        client.set_timeout(60.0)

        # Load map if specified (once) - skip if "(Current Map)"
        if carla_map and len(carla_map.strip()) > 0 and carla_map != "(Current Map)":
            print(f"[DEBUG] Loading map: {carla_map}")
            client.load_world(carla_map)
            time.sleep(2)

        world = client.get_world()
        geo_reference = extract_geo_reference(world)

        # Set synchronous mode once
        settings = world.get_settings()
        original_sync = settings.synchronous_mode
        original_delta = settings.fixed_delta_seconds

        settings.synchronous_mode = True
        settings.fixed_delta_seconds = 0.05
        world.apply_settings(settings)

        print(f"[PERF] Setup: {time.time() - start_time:.2f}s")
        spawn_start = time.time()

        # Spawn ALL cameras at once
        bp_library = world.get_blueprint_library()
        camera_data_list = []

        for cam_spec in cameras_specs_list:
            camera_bp = bp_library.find("sensor.camera.rgb")
            camera_bp.set_attribute("image_size_x", str(cam_spec["resolution"][0]))
            camera_bp.set_attribute("image_size_y", str(cam_spec["resolution"][1]))
            camera_bp.set_attribute("fov", str(cam_spec["fov"]))

            # Handle geo position
            carla_x, carla_y, carla_z = (
                cam_spec["position"][0],
                cam_spec["position"][1],
                cam_spec["position"][2],
            )
            if cam_spec.get("geo_position", False):
                carla_x, carla_y, carla_z = latlon_to_world(
                    carla_x, carla_y, carla_z, geo_reference, PGM_PATH
                )

            loc = carla.Location(x=carla_x, y=carla_y, z=carla_z)
            rot = carla.Rotation(
                pitch=cam_spec["orientation"][0],
                yaw=cam_spec["orientation"][1],
                roll=cam_spec["orientation"][2],
            )
            transform = carla.Transform(loc, rot)

            camera = world.spawn_actor(camera_bp, transform)
            cameras.append(camera)

            # Prepare data storage for this camera
            image_data = {"frame": None, "done": False, "spec": cam_spec}
            camera_data_list.append(image_data)

            # Setup callback
            def make_callback(data_dict):
                def callback(image):
                    try:
                        array = np.frombuffer(image.raw_data, dtype=np.uint8)
                        array = array.reshape((image.height, image.width, 4))
                        rgb_image = array[:, :, :3][:, :, ::-1].copy()
                        data_dict["frame"] = rgb_image
                        data_dict["done"] = True
                    except Exception as e:
                        print(f"Callback error: {e}")
                        data_dict["done"] = True

                return callback

            camera.listen(make_callback(image_data))

        print(
            f"[PERF] Spawned {len(cameras)} cameras: {time.time() - spawn_start:.2f}s"
        )
        capture_start = time.time()

        # Tick world until all cameras have frames (max 10 ticks)
        for tick_count in range(10):
            world.tick()

            # Check if all cameras are done
            if all(data["done"] for data in camera_data_list):
                print(f"[PERF] All frames captured after {tick_count + 1} ticks")
                break

            time.sleep(0.05)

        print(f"[PERF] Capture: {time.time() - capture_start:.2f}s")
        cleanup_start = time.time()

        # Cleanup all cameras
        for camera in cameras:
            try:
                camera.stop()
                camera.destroy()
            except:
                pass
        cameras = []

        # Restore settings
        settings.synchronous_mode = original_sync
        settings.fixed_delta_seconds = original_delta
        world.apply_settings(settings)

        print(f"[PERF] Cleanup: {time.time() - cleanup_start:.2f}s")
        print(
            f"[PERF] TOTAL: {time.time() - start_time:.2f}s for {len(cameras_specs_list)} cameras"
        )

        # Return successful frames
        results = []
        for data in camera_data_list:
            if data["frame"] is not None:
                results.append((data["frame"], data["spec"]))

        return results

    except Exception as e:
        import traceback

        print(f"Batch preview error: {str(e)}\n{traceback.format_exc()}")

        # Cleanup on error
        for camera in cameras:
            try:
                camera.stop()
                camera.destroy()
            except:
                pass

        return []


def quick_preview_frame(cam_specs, carla_host, carla_port, carla_map=None):
    """
    Quick camera preview - spawn camera and capture 1 frame

    Does NOT run full calibration (much faster!)

    Args:
        cam_specs: Dict with camera parameters
        carla_host: CARLA server host
        carla_port: CARLA server port
        carla_map: Optional map to load

    Returns:
        (rgb_image, status_message)
    """
    camera = None
    try:
        # Connect to CARLA
        client = carla.Client(carla_host, int(carla_port))
        client.set_timeout(60.0)

        # Load map if specified - skip if "(Current Map)"
        if carla_map and len(carla_map.strip()) > 0 and carla_map != "(Current Map)":
            print(f"[DEBUG] Loading map: {carla_map}")
            client.load_world(carla_map)
            time.sleep(2)  # Wait for map to load

        world = client.get_world()

        geo_reference = extract_geo_reference(world)
        carla_x, carla_y, carla_z = (
            cam_specs["position"][0],
            cam_specs["position"][1],
            cam_specs["position"][2],
        )
        if cam_specs["geo_position"]:
            carla_x, carla_y, carla_z = latlon_to_world(
                carla_x, carla_y, carla_z, geo_reference, PGM_PATH
            )

        # Set synchronous mode for reliable frame capture
        settings = world.get_settings()
        original_sync = settings.synchronous_mode
        original_delta = settings.fixed_delta_seconds

        settings.synchronous_mode = True
        settings.fixed_delta_seconds = 0.05  # 20 FPS
        world.apply_settings(settings)

        # Spawn RGB camera
        bp_library = world.get_blueprint_library()
        camera_bp = bp_library.find("sensor.camera.rgb")
        camera_bp.set_attribute("image_size_x", str(cam_specs["resolution"][0]))
        camera_bp.set_attribute("image_size_y", str(cam_specs["resolution"][1]))
        camera_bp.set_attribute("fov", str(cam_specs["fov"]))

        # Set camera transform
        loc = carla.Location(x=carla_x, y=carla_y, z=carla_z)
        rot = carla.Rotation(
            pitch=cam_specs["orientation"][0],
            yaw=cam_specs["orientation"][1],
            roll=cam_specs["orientation"][2],
        )
        transform = carla.Transform(loc, rot)

        # Spawn camera
        camera = world.spawn_actor(camera_bp, transform)

        # Capture frame with proper synchronization
        image_data = {"frame": None, "done": False}

        def image_callback(image):
            try:
                # Copy data immediately to avoid memory issues
                array = np.frombuffer(image.raw_data, dtype=np.uint8)
                array = array.reshape((image.height, image.width, 4))
                # Convert BGRA to RGB and make a copy
                rgb_image = array[:, :, :3][:, :, ::-1].copy()
                image_data["frame"] = rgb_image
                image_data["done"] = True
            except Exception as e:
                print(f"Callback error: {e}")
                image_data["done"] = True

        camera.listen(image_callback)

        # Tick world to generate frames
        for _ in range(5):  # Tick 5 times to ensure camera is ready
            world.tick()
            if image_data["done"]:
                break
            time.sleep(0.1)

        # Cleanup camera
        if camera is not None:
            camera.stop()
            camera.destroy()
            camera = None

        # Restore original settings
        settings.synchronous_mode = original_sync
        settings.fixed_delta_seconds = original_delta
        world.apply_settings(settings)

        if image_data["frame"] is not None:
            msg = f"✅ Preview captured!\n"
            msg += f"Position: {cam_specs['position']}\n"
            msg += f"Orientation: {cam_specs['orientation']}"
            return image_data["frame"], msg
        else:
            return None, "❌ Timeout waiting for frame"

    except Exception as e:
        import traceback

        error_detail = traceback.format_exc()

        # Ensure cleanup on error
        if camera is not None:
            try:
                camera.stop()
                camera.destroy()
            except:
                pass

        return None, f"❌ Error: {str(e)}\n\n{error_detail}"
