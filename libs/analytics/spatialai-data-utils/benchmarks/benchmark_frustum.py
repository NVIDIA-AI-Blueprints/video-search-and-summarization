#!/usr/bin/env python3

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
Benchmark script for frustum calculation algorithm.

Tests the performance of calculate_camera_frustum_polygon with real calibration data.
"""

import json
import sys
import time
import numpy as np
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from spatialai_data_utils.core.cameras.polygon import calculate_camera_frustum_polygon
from spatialai_data_utils.core.cameras.utils import extract_camera_matrices


def load_real_calibration(calibration_file):
    """Load real calibration data from file."""
    with open(calibration_file, 'r') as f:
        calibration_data = json.load(f)
    
    # Extract camera matrices from all sensors
    cameras = []
    for sensor in calibration_data['sensors']:
        intrinsic, extrinsic = extract_camera_matrices(sensor)
        if intrinsic is not None and extrinsic is not None:
            cameras.append({
                'id': sensor['id'],
                'intrinsic': intrinsic,
                'extrinsic': extrinsic
            })
    
    return cameras


def benchmark_single_camera(cameras):
    """Benchmark single camera frustum calculation."""
    if not cameras:
        print("ERROR: No cameras loaded")
        return 0.0
    
    camera = cameras[0]
    intrinsic = camera['intrinsic']
    extrinsic = camera['extrinsic']
    
    print("=" * 80)
    print("BENCHMARK: Single Camera Frustum Calculation")
    print(f"Camera: {camera['id']}")
    print("=" * 80)
    
    # Warm-up run
    polygon = calculate_camera_frustum_polygon(
        intrinsic, extrinsic,
        height_range=(1.0, 3.0),
        max_distance=30.0
    )
    
    if polygon is None:
        print(f"WARNING: Camera {camera['id']} failed to generate polygon, trying another...")
        if len(cameras) > 1:
            return benchmark_single_camera(cameras[1:])
        else:
            print("ERROR: No cameras could generate valid polygons")
            return 0.0
    
    print(f"✓ Successfully generated polygon with {len(polygon.exterior.coords)} vertices")
    
    # Benchmark runs
    num_runs = 100
    times = []
    
    for i in range(num_runs):
        start = time.perf_counter()
        polygon = calculate_camera_frustum_polygon(
            intrinsic, extrinsic,
            height_range=(1.0, 3.0),
            max_distance=30.0
        )
        end = time.perf_counter()
        times.append((end - start) * 1000)  # Convert to milliseconds
    
    times = np.array(times)
    
    print(f"\nRuns: {num_runs}")
    print(f"Mean:   {times.mean():.2f} ms")
    print(f"Median: {np.median(times):.2f} ms")
    print(f"Min:    {times.min():.2f} ms")
    print(f"Max:    {times.max():.2f} ms")
    print(f"Std:    {times.std():.2f} ms")
    
    return times.mean()


def benchmark_multiple_cameras(cameras, num_cameras_list=[1, 5, 10, 20]):
    """Benchmark frustum calculation for multiple cameras."""
    if not cameras:
        print("ERROR: No cameras loaded")
        return
    
    # Use first camera for repeated calculations
    camera = cameras[0]
    intrinsic = camera['intrinsic']
    extrinsic = camera['extrinsic']
    
    print("\n" + "=" * 80)
    print("BENCHMARK: Multiple Cameras (Repeated Calculation)")
    print(f"Using camera: {camera['id']}")
    print("=" * 80)
    
    print(f"\n{'Cameras':<10} {'Total Time':<15} {'Time/Camera':<15} {'Est. FPS':<10}")
    print("-" * 60)
    
    for num_cameras in num_cameras_list:
        if num_cameras > len(cameras) * 5:
            # Skip if we're going way beyond available cameras
            continue
            
        start = time.perf_counter()
        
        for i in range(num_cameras):
            _ = calculate_camera_frustum_polygon(
                intrinsic, extrinsic,
                height_range=(1.0, 3.0),
                max_distance=30.0
            )
        
        end = time.perf_counter()
        total_time = (end - start) * 1000  # ms
        time_per_camera = total_time / num_cameras
        
        # Estimate FPS if we need to calculate once per frame
        fps = 1000.0 / total_time if total_time > 0 else float('inf')
        
        print(f"{num_cameras:<10} {total_time:<15.2f} {time_per_camera:<15.2f} {fps:<10.1f}")


def benchmark_with_scene_bounds(cameras):
    """Benchmark with scene bounds (clipping)."""
    if not cameras:
        print("ERROR: No cameras loaded")
        return
    
    camera = cameras[0]
    intrinsic = camera['intrinsic']
    extrinsic = camera['extrinsic']
    scene_bounds = (-50, -50, 50, 50)
    
    print("\n" + "=" * 80)
    print("BENCHMARK: With Scene Bounds Clipping")
    print(f"Camera: {camera['id']}")
    print("=" * 80)
    
    num_runs = 100
    
    # Without scene bounds
    times_no_clip = []
    for _ in range(num_runs):
        start = time.perf_counter()
        _ = calculate_camera_frustum_polygon(
            intrinsic, extrinsic,
            height_range=(1.0, 3.0),
            max_distance=30.0,
            scene_bounds=None
        )
        end = time.perf_counter()
        times_no_clip.append((end - start) * 1000)
    
    # With scene bounds
    times_with_clip = []
    for _ in range(num_runs):
        start = time.perf_counter()
        _ = calculate_camera_frustum_polygon(
            intrinsic, extrinsic,
            height_range=(1.0, 3.0),
            max_distance=30.0,
            scene_bounds=scene_bounds
        )
        end = time.perf_counter()
        times_with_clip.append((end - start) * 1000)
    
    times_no_clip = np.array(times_no_clip)
    times_with_clip = np.array(times_with_clip)
    
    print(f"\nWithout clipping: {times_no_clip.mean():.2f} ms (±{times_no_clip.std():.2f})")
    print(f"With clipping:    {times_with_clip.mean():.2f} ms (±{times_with_clip.std():.2f})")
    print(f"Overhead:         {times_with_clip.mean() - times_no_clip.mean():.2f} ms ({((times_with_clip.mean() / times_no_clip.mean() - 1) * 100):.1f}%)")


if __name__ == "__main__":
    print("\n🚀 Frustum Calculation Performance Benchmark\n")
    
    # Load real calibration data
    calib_file = Path(__file__).parent.parent / "data" / "mtmc" / "scene_001" / "calibration.json"
    
    if not calib_file.exists():
        print(f"ERROR: Calibration file not found: {calib_file}")
        sys.exit(1)
    
    print(f"Loading calibration from: {calib_file}")
    cameras = load_real_calibration(calib_file)
    print(f"✓ Loaded {len(cameras)} cameras with valid matrices\n")
    
    if not cameras:
        print("ERROR: No valid cameras found in calibration file")
        sys.exit(1)
    
    # Run benchmarks
    avg_time = benchmark_single_camera(cameras)
    
    if avg_time > 0:
        benchmark_multiple_cameras(cameras)
        benchmark_with_scene_bounds(cameras)
        
        # Summary
        print("\n" + "=" * 80)
        print("SUMMARY")
        print("=" * 80)
        print(f"Average time per camera: {avg_time:.2f} ms")
        print(f"Cameras processed per second: {1000.0 / avg_time:.1f}")
        print(f"\nFor a scene with {len(cameras)} cameras:")
        print(f"  - Total time: {avg_time * len(cameras):.2f} ms")
        print(f"  - Can recalculate at: {1000.0 / (avg_time * len(cameras)):.1f} FPS")
        print("\nFor a scene with 10 cameras:")
        print(f"  - Total time: {avg_time * 10:.2f} ms")
        print(f"  - Can recalculate at: {1000.0 / (avg_time * 10):.1f} FPS")
        print("\n" + "=" * 80)

