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
ROI and Tripwire Verification Utility
Generates Bird's Eye View visualization of ROIs and tripwires
"""

import json
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.patches import Polygon, FancyArrowPatch
import numpy as np
from PIL import Image
from pathlib import Path
from typing import Optional
import io

from .annotation_storage import resolve_camera_annotation_block

# Camera color list for visualization (indexed by position)
CAMERA_COLORS_LIST = [
    '#FF6B6B',  # Red
    '#4ECDC4',  # Teal
    '#45B7D1',  # Blue
    '#FFA07A',  # Light Salmon
    '#95E1D3',  # Mint
    '#F38181',  # Pink
    '#AA96DA',  # Purple
    '#FCBAD3',  # Light Pink
    '#FFFFD2',  # Light Yellow
    '#A8D8EA',  # Sky Blue
    '#FFAAA6',  # Coral
    '#C5E99B',  # Light Green
]

def get_camera_color(index: int) -> str:
    """Get color for camera by index, cycling through colors if needed"""
    return CAMERA_COLORS_LIST[index % len(CAMERA_COLORS_LIST)]


# Root-level (layout / global) ROIs and tripwires in export JSON — distinct from per-camera palette
GLOBAL_ANNOTATION_COLOR = "#7D3C98"
GLOBAL_ANNOTATION_LABEL = "Global"


def transform_world_coords(x, y, world_transform):
    """
    World (meters) -> layout pixels. Matches transform_world_to_map.json (Umeyama):
    layout_pixel = scale * (R @ world_pt) + t, with t already in pixels (do not scale t).
    """
    scale = world_transform.get('scale', 1.0)
    rotation = world_transform.get('rotation', [[1, 0, 0], [0, 1, 0], [0, 0, 1]])
    translation = np.array(world_transform.get('translation', [0, 0, 0]))
    rotation_matrix = np.array(rotation)
    coord = np.array([x, y, 0.0])
    rotated = rotation_matrix @ coord
    pixel_x = scale * rotated[0] + translation[0]
    pixel_y = scale * rotated[1] + translation[1]
    return (float(pixel_x), float(pixel_y))

def plot_roi(ax, roi, camera_id, color, alpha=0.3, world_transform=None):
    """Plot ROI polygon on the axes"""
    coords = roi['roiCoordinates']
    # Flip negative y-coordinates to positive for plotting
    points = [(pt['x'], pt['y']) for pt in coords]
    
    # Apply world transform if provided
    if world_transform:
        points = [transform_world_coords(pt[0], pt[1], world_transform) for pt in points]
    
    if len(points) > 0:
        # Create polygon
        poly = Polygon(points, closed=True, 
                      edgecolor=color, facecolor=color, 
                      alpha=alpha, linewidth=2)
        ax.add_patch(poly)
        
        # Add ROI ID text at centroid
        centroid_x = np.mean([pt[0] for pt in points])
        centroid_y = np.mean([pt[1] for pt in points])
        roi_id = roi.get('id', 'ROI')
        ax.text(centroid_x, centroid_y, f"{camera_id}\n{roi_id}", 
               ha='center', va='center', fontsize=8, 
               color='black', weight='bold',
               bbox=dict(boxstyle='round,pad=0.3', facecolor='white', alpha=0.8))

def plot_tripwire(ax, tripwire, camera_id, color, world_transform=None):
    """Plot a complete tripwire: the line and its direction arrow.

    Incomplete tripwires are rejected earlier in the exporter. This helper still
    names missing fields so a leftover KeyError cannot become a generic 500.
    """
    wire = tripwire.get('wire') or {}
    direction = tripwire.get('direction') or {}
    wire_start = wire.get('p1')
    wire_end = wire.get('p2')
    dir_start = direction.get('p1')
    dir_end = direction.get('p2')
    tripwire_id = tripwire.get('id', 'unknown')
    if not isinstance(wire_start, dict) or not isinstance(wire_end, dict):
        raise ValueError(
            f"Tripwire '{tripwire_id}' for camera '{camera_id}' has no wire endpoints"
        )
    if not isinstance(dir_start, dict) or not isinstance(dir_end, dict):
        raise ValueError(
            f"Tripwire '{tripwire_id}' for camera '{camera_id}' is missing direction data"
        )
    
    # Flip negative y-coordinates to positive for plotting
    wire_p1 = (wire_start['x'], wire_start['y'])
    wire_p2 = (wire_end['x'], wire_end['y'])
    dir_p1 = (dir_start['x'], dir_start['y'])
    dir_p2 = (dir_end['x'], dir_end['y'])
    
    # Apply world transform if provided
    if world_transform:
        wire_p1 = transform_world_coords(wire_p1[0], wire_p1[1], world_transform)
        wire_p2 = transform_world_coords(wire_p2[0], wire_p2[1], world_transform)
        dir_p1 = transform_world_coords(dir_p1[0], dir_p1[1], world_transform)
        dir_p2 = transform_world_coords(dir_p2[0], dir_p2[1], world_transform)
    
    # Plot the wire line
    wire_x = [wire_p1[0], wire_p2[0]]
    wire_y = [wire_p1[1], wire_p2[1]]
    ax.plot(wire_x, wire_y, color=color, linewidth=3, 
           marker='o', markersize=6)
    
    # Calculate midpoint for direction arrow
    mid_x = (wire_p1[0] + wire_p2[0]) / 2
    mid_y = (wire_p1[1] + wire_p2[1]) / 2
    
    # Plot small markers for direction
    ax.plot([dir_p1[0]], [dir_p1[1]], 'o', color=color, markersize=4, alpha=0.5)
    ax.plot([dir_p2[0]], [dir_p2[1]], 's', color=color, markersize=4, alpha=0.5)
    
    # Add arrow to show direction
    arrow = FancyArrowPatch((dir_p1[0], dir_p1[1]), (dir_p2[0], dir_p2[1]),
                           arrowstyle='->', mutation_scale=15, 
                           color=color, linewidth=1.5, alpha=0.7)
    ax.add_patch(arrow)
    
    # Add tripwire ID text
    tripwire_id = tripwire.get('id', 'TW')
    ax.text(mid_x, mid_y, f"{camera_id}\n{tripwire_id}", 
           ha='center', va='bottom', fontsize=7, 
           color='white', weight='bold',
           bbox=dict(boxstyle='round,pad=0.2', facecolor=color, alpha=0.9))

def load_world_transform(project_path: Path, result_type: Optional[str] = None) -> dict:
    """
    Load transform_world_to_map.json (multi path first, then single).
    Single-view files include "expected" (ppm, diag(1,-1,-1), t=0): use it so world
    frame matches homography unproject in the exporter; multi-view uses top-level scale/R/t.
    When result_type is ``vggt``, the VGGT results tree is tried first so BEV matches
    ``*_projected_vggt.json``; otherwise VGGT path is a fallback after AMC paths.
    """
    project_path = Path(project_path).resolve()
    multi = (
        project_path / "output" / "multi_view_results" / "BA_output" / "results_ba_scaled_world" / "transform_world_to_map.json"
    )
    single = project_path / "output" / "single_view_results" / "cam_00" / "transform_world_to_map.json"
    vggt = (
        project_path
        / "output"
        / "vggt_results"
        / "multi_view_results"
        / "BA_output"
        / "results_ba_scaled_world"
        / "transform_world_to_map.json"
    )
    if result_type == "vggt":
        candidates = [vggt, multi, single]
    else:
        candidates = [multi, single, vggt]
    for transform_file in candidates:
        if not transform_file.exists():
            continue
        try:
            with open(transform_file, 'r') as f:
                data = json.load(f)
            return {
                'scale': data.get('scale', 1.0),
                'rotation': data.get('R', [[1, 0, 0], [0, 1, 0], [0, 0, 1]]),
                'translation': data.get('t', [0, 0, 0]),
            }
        except Exception as e:
            print(f"Error loading world transform from {transform_file}: {e}")
    return None


def generate_bev_verification(
    export_json_path: Path,
    layout_image_path: Path = None,
    project_path: Path = None,
    result_type: Optional[str] = None,
) -> tuple:
    """
    Generate Bird's Eye View verification plot
    
    Args:
        export_json_path: Path to exported calibration JSON
        layout_image_path: Optional path to layout image
        project_path: Optional path to project directory (to load transform from source file)
    
    Returns:
        tuple: (figure with layout, figure without layout) or (None, None) if failed
    """
    try:
        # Load calibration data
        with open(export_json_path, 'r') as f:
            calib_data = json.load(f)
        
        # Load world transform from source file if project_path provided
        world_transform_from_file = None
        if project_path:
            world_transform_from_file = load_world_transform(project_path, result_type=result_type)
        
        # Create figure with layout if available
        fig_with_layout = None
        if layout_image_path and layout_image_path.exists():
            fig_with_layout = plt.figure(figsize=(16, 12))
            ax1 = fig_with_layout.add_subplot(111)
            
            # Load and display layout
            layout_img = Image.open(layout_image_path)
            img_array = np.array(layout_img)
            ax1.imshow(img_array, alpha=0.4, aspect='auto')
            ax1.set_title('ROI and Tripwire Verification on Layout', fontsize=16, weight='bold')
            
            # Plot ROIs and tripwires with transformation applied
            # Use world_transform_from_file (loaded directly from transform_world_to_map.json)
            _plot_annotations(ax1, calib_data, world_transform=world_transform_from_file)
            
            ax1.set_xlabel('X Coordinate (pixels)', fontsize=12)
            ax1.set_ylabel('Y Coordinate (pixels)', fontsize=12)
            ax1.grid(True, alpha=0.3, linestyle='--')
            ax1.set_aspect('equal', adjustable='box')
            
            # Add legend
            camera_sensors = [s for s in calib_data['sensors'] if s['type'] == 'camera']
            legend_elements = [
                patches.Patch(facecolor=get_camera_color(i), 
                            edgecolor=get_camera_color(i), 
                            label=sensor['id'])
                for i, sensor in enumerate(camera_sensors)
            ]
            if calib_data.get('rois') or calib_data.get('tripwires'):
                legend_elements.append(
                    patches.Patch(
                        facecolor=GLOBAL_ANNOTATION_COLOR,
                        edgecolor=GLOBAL_ANNOTATION_COLOR,
                        label=f'{GLOBAL_ANNOTATION_LABEL} (layout)',
                    )
                )
            ax1.legend(handles=legend_elements, loc='upper right', fontsize=10)
            
            # Add info about transformation - use world_transform_from_file
            if world_transform_from_file:
                scale = world_transform_from_file.get('scale', 1.0)
                scale_text = f'{scale:.2f}'
                source_text = 'from transform_world_to_map.json'
            else:
                # Fallback to scaleFactor from first sensor
                first_sensor = next((s for s in calib_data.get('sensors', []) if s.get('type') == 'camera'), None)
                scale = first_sensor.get('scaleFactor', -1.0) if first_sensor else -1.0
                scale_text = f'{scale:.2f}' if scale != -1.0 else 'N/A'
                source_text = 'from sensor scaleFactor'
            
            ax1.text(0.02, 0.02, f'World coords → pixels (scale: {scale_text}, {source_text})',
                   transform=ax1.transAxes, fontsize=9, verticalalignment='bottom',
                   bbox=dict(boxstyle='round', facecolor='lightyellow', alpha=0.7))
            
            fig_with_layout.tight_layout()
        
        # Create figure without layout (coordinates only)
        fig_coords_only = plt.figure(figsize=(16, 12))
        ax2 = fig_coords_only.add_subplot(111)
        ax2.set_title('ROI and Tripwire - World Coordinates', fontsize=16, weight='bold')
        
        # Plot ROIs and tripwires without transformation (raw world coordinates)
        _plot_annotations(ax2, calib_data, world_transform=None)
        
        ax2.set_xlabel('World X Coordinate (m)', fontsize=12)
        ax2.set_ylabel('World Y Coordinate (m)', fontsize=12)
        ax2.grid(True, alpha=0.3, linestyle='--')
        ax2.set_aspect('equal', adjustable='box')
        
        # Add legend
        camera_sensors = [s for s in calib_data['sensors'] if s['type'] == 'camera']
        legend_elements = [
            patches.Patch(facecolor=get_camera_color(i), 
                        edgecolor=get_camera_color(i), 
                        label=sensor['id'])
            for i, sensor in enumerate(camera_sensors)
        ]
        if calib_data.get('rois') or calib_data.get('tripwires'):
            legend_elements.append(
                patches.Patch(
                    facecolor=GLOBAL_ANNOTATION_COLOR,
                    edgecolor=GLOBAL_ANNOTATION_COLOR,
                    label=f'{GLOBAL_ANNOTATION_LABEL} (layout)',
                )
            )
        ax2.legend(handles=legend_elements, loc='upper right', fontsize=10)
        
        fig_coords_only.tight_layout()
        
        return fig_with_layout, fig_coords_only
    
    except Exception as e:
        print(f"Error generating BEV verification: {e}")
        return None, None

def _plot_annotations(ax, calib_data, world_transform=None):
    """
    Helper to plot all annotations on an axis
    
    Args:
        ax: matplotlib axis
        calib_data: calibration export data
        world_transform: dict with 'scale', 'rotation', 'translation' keys (or None for no transform)
    """
    camera_index = 0
    for sensor in calib_data['sensors']:
        if sensor['type'] != 'camera':
            continue
        
        camera_id = sensor['id']
        color = get_camera_color(camera_index)
        camera_index += 1
        
        # Plot ROIs
        for roi in sensor.get('rois', []):
            plot_roi(ax, roi, camera_id, color, world_transform=world_transform)
        
        # Plot tripwires
        for tripwire in sensor.get('tripwires', []):
            plot_tripwire(ax, tripwire, camera_id, color, world_transform=world_transform)

    # Root-level ROIs / tripwires (same world frame as cartesian export; map with same transform)
    for roi in calib_data.get('rois') or []:
        if not isinstance(roi, dict):
            continue
        plot_roi(
            ax,
            roi,
            GLOBAL_ANNOTATION_LABEL,
            GLOBAL_ANNOTATION_COLOR,
            world_transform=world_transform,
        )
    for tripwire in calib_data.get('tripwires') or []:
        if not isinstance(tripwire, dict):
            continue
        plot_tripwire(
            ax,
            tripwire,
            GLOBAL_ANNOTATION_LABEL,
            GLOBAL_ANNOTATION_COLOR,
            world_transform=world_transform,
        )

def fig_to_pil_image(fig):
    """Convert matplotlib figure to PIL Image"""
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=150, bbox_inches='tight')
    buf.seek(0)
    img = Image.open(buf)
    return img

def generate_camera_overlays(export_json_path: Path, project_path: Path):
    """
    Generate camera overlay images with ROIs and tripwires drawn
    Uses annotations.json for pixel coordinates
    
    Args:
        export_json_path: Path to exported calibration JSON
        project_path: Path to project directory
    
    Returns:
        dict: Camera ID -> PIL Image mapping with overlays drawn
    """
    overlay_images = {}
    
    try:
        # Load calibration data for camera info
        with open(export_json_path, 'r') as f:
            calib_data = json.load(f)
        
        # Load annotations.json for pixel coordinates
        annotations_path = project_path / "annotations.json"
        annotations = {}
        if annotations_path.exists():
            with open(annotations_path, 'r') as f:
                annotations = json.load(f)
        
        # Single view results directory
        sv_results_dir = project_path / "output" / "single_view_results"
        
        if not sv_results_dir.exists():
            return overlay_images
        
        # Process each camera
        camera_index = 0
        for sensor in calib_data['sensors']:
            if sensor['type'] != 'camera':
                continue
            
            camera_id = sensor['id']
            # Use cam_XX format for directory lookup
            cam_dir_name = f"cam_{camera_index:02d}"
            cam_dir = sv_results_dir / cam_dir_name
            rectified_img_path = cam_dir / "rectified.jpg"
            
            if not rectified_img_path.exists():
                camera_index += 1
                continue
            
            # Load the rectified image
            img = Image.open(rectified_img_path)
            
            # Create matplotlib figure to draw on
            fig, ax = plt.subplots(1, 1, figsize=(19.2, 10.8), dpi=100)
            ax.imshow(img)
            ax.axis('off')
            
            # Get camera color by index
            color = get_camera_color(camera_index)
            
            # Pixel annotations: top-level key is internal camera id (no video extension)
            video_name = f"{cam_dir_name}.mp4"
            cam_annotations = resolve_camera_annotation_block(annotations, video_name)
            
            # Draw ROIs from annotations.json (these are in pixel coordinates!)
            for roi in cam_annotations.get('rois', []):
                coords = roi['roiCoordinates']
                if len(coords) > 0:
                    points = [(pt['x'], pt['y']) for pt in coords]
                    poly = Polygon(points, closed=True,
                                  edgecolor=color, facecolor=color,
                                  alpha=0.3, linewidth=3)
                    ax.add_patch(poly)
                    
                    # Add ROI label with ID
                    centroid_x = np.mean([pt[0] for pt in points])
                    centroid_y = np.mean([pt[1] for pt in points])
                    roi_id = roi.get('id', 'ROI')
                    ax.text(centroid_x, centroid_y, roi_id,
                           ha='center', va='center', fontsize=10,
                           color='white', weight='bold',
                           bbox=dict(boxstyle='round,pad=0.4', facecolor=color, alpha=0.9))
            
            # Draw tripwires from annotations.json
            for tripwire in cam_annotations.get('tripwires', []):
                wire = tripwire['wire']
                direction = tripwire.get('direction', {})
                
                # Draw wire line
                wire_x = [wire['p1']['x'], wire['p2']['x']]
                wire_y = [wire['p1']['y'], wire['p2']['y']]
                ax.plot(wire_x, wire_y, color=color, linewidth=4,
                       marker='o', markersize=8, label='Tripwire')
                
                # Draw direction arrow if available
                if direction:
                    dir_p1 = direction.get('p1', {})
                    dir_p2 = direction.get('p2', {})
                    if dir_p1 and dir_p2:
                        arrow = FancyArrowPatch(
                            (dir_p1['x'], dir_p1['y']),
                            (dir_p2['x'], dir_p2['y']),
                            arrowstyle='->', mutation_scale=20,
                            color=color, linewidth=2, alpha=0.8
                        )
                        ax.add_patch(arrow)
                
                # Add tripwire label with ID
                mid_x = (wire['p1']['x'] + wire['p2']['x']) / 2
                mid_y = (wire['p1']['y'] + wire['p2']['y']) / 2
                tw_id = tripwire.get('id', 'TW')
                ax.text(mid_x, mid_y, tw_id,
                       ha='center', va='bottom', fontsize=10,
                       color='white', weight='bold',
                       bbox=dict(boxstyle='round,pad=0.4', facecolor=color, alpha=0.9))
            
            # Add camera info text
            roi_count = len(cam_annotations.get('rois', []))
            tw_count = len(cam_annotations.get('tripwires', []))
            
            info_text = f"{camera_id}: {roi_count} ROI(s), {tw_count} Tripwire(s)"
            ax.text(0.02, 0.98, info_text, transform=ax.transAxes,
                   fontsize=14, weight='bold', color='white',
                   verticalalignment='top',
                   bbox=dict(boxstyle='round', facecolor=color, alpha=0.8))
            
            # Convert figure to PIL image
            buf = io.BytesIO()
            fig.savefig(buf, format='png', dpi=100, bbox_inches='tight', pad_inches=0)
            buf.seek(0)
            overlay_img = Image.open(buf)
            
            overlay_images[camera_id] = overlay_img
            
            plt.close(fig)
            camera_index += 1
        
        return overlay_images
    
    except Exception as e:
        print(f"Error generating camera overlays: {e}")
        import traceback
        traceback.print_exc()
        return {}

def get_camera_rectified_images(project_path: Path):
    """
    Get rectified camera images from single view results
    
    Args:
        project_path: Path to project directory
    
    Returns:
        dict: Camera ID -> rectified image path mapping
    """
    rectified_images = {}
    
    # Check single view results
    sv_results_dir = project_path / "output" / "single_view_results"
    
    if not sv_results_dir.exists():
        return rectified_images
    
    # Look for rectified images in each camera directory
    for cam_dir in sorted(sv_results_dir.iterdir()):
        if cam_dir.is_dir() and cam_dir.name.startswith('cam_'):
            camera_id = cam_dir.name
            
            # Look for rectified image
            rectified_img = cam_dir / "rectified.jpg"
            
            if rectified_img.exists():
                rectified_images[camera_id] = rectified_img
    
    return rectified_images

