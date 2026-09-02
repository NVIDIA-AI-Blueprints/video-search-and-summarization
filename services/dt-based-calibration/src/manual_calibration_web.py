#!/usr/bin/env python3

# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
#
# SPDX-License-Identifier: Apache-2.0


"""
Multi-Camera Calibration Web UI with Hybrid Approach

Features:
- Tab 1: Quick Single Camera
- Tab 2: Multi-Camera with Auto-Orientation (with multi-select & edit)

Usage:
    python manual_calibration_web.py
    
Then open browser at http://localhost:7861
"""

import os
import sys
import yaml
import json
import tempfile
import gradio as gr
from pathlib import Path
from datetime import datetime

# Import utilities
from utils.carla_utils import (
    check_carla_server,
    run_autocalibration,
    quick_preview_frame,
    get_available_maps,
)

# Get CARLA and Gradio configuration from environment variables
DEFAULT_CARLA_HOST = os.getenv("CARLA_HOST", "127.0.0.1")
DEFAULT_CARLA_PORT = int(os.getenv("CARLA_PORT", "2000"))

# Predefined orientation presets
ORIENTATION_PRESETS = {
    "Standard (10 cameras)": [
        [0, 0, 0],
        [0, 90, 0],
        [0, 180, 0],
        [0, -90, 0],  # Level views
        [-10, 0, 0],
        [-10, 90, 0],  # 10° down
        [-20, 0, 0],
        [-20, 90, 0],  # 20° down
        [-30, 0, 0],
        [-30, 90, 0],  # 30° down
    ],
    "Level Only (4 cameras)": [[0, 0, 0], [0, 90, 0], [0, 180, 0], [0, -90, 0]],
    "Downward Focus (6 cameras)": [
        [-10, 0, 0],
        [-10, 90, 0],
        [-20, 0, 0],
        [-20, 90, 0],
        [-30, 0, 0],
        [-30, 90, 0],
    ],
    "Dense Coverage (16 cameras)": [
        [p, y, 0] for p in [0, -10, -20, -30] for y in [0, 90, 180, -90]
    ],
}

# Global state for multi-camera workflow
generated_cameras = []
selected_indices = []
current_orientations = list(ORIENTATION_PRESETS["Standard (10 cameras)"])  # Deep copy

# Load available maps on startup (before creating UI)
AVAILABLE_MAPS = ["(Current Map)"]
try:
    success, maps = get_available_maps(
        DEFAULT_CARLA_HOST, DEFAULT_CARLA_PORT, timeout=60
    )
    if success and len(maps) > 0:
        AVAILABLE_MAPS = ["(Current Map)"] + maps
        print(f"[INFO] Loaded {len(maps)} CARLA maps")
    else:
        # Fallback to common maps
        AVAILABLE_MAPS = ["(Current Map)"]
        print("[WARN] Could not connect to CARLA - using default map list")
except Exception as e:
    AVAILABLE_MAPS = ["(Current Map)", "HQ", "Town10HD_Opt"]
    print(f"[WARN] Error loading maps: {e}")


def parse_resolution(res_preset):
    """Parse resolution preset to [width, height]"""
    width, height = res_preset.split("x")
    return int(width), int(height)


def create_camera_spec_dict(
    cam_id,
    geo_position,
    pos_x,
    pos_y,
    pos_z,
    pitch,
    yaw,
    roll,
    fov,
    fps,
    res_preset,
    label,
):
    """Create camera spec dict from individual parameters"""
    res_width, res_height = parse_resolution(res_preset)
    return {
        "id": cam_id,
        "position": [float(pos_x), float(pos_y), float(pos_z)],
        "orientation": [float(pitch), float(yaw), float(roll)],
        "fov": int(fov),
        "resolution": [res_width, res_height],
        "label": int(label),
        "fps": int(fps),
        "geo_position": geo_position,
    }


def create_temp_camera_spec_from_dict(cam_spec):
    """Create temporary camera_specs.yml from camera spec dict"""
    camera_specs = {"cameras": [cam_spec]}

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        yaml.dump(camera_specs, f)
        temp_path = f.name

    return temp_path


def update_preset_selection(preset_name):
    """Update current orientations based on preset selection"""
    global current_orientations

    if preset_name == "Custom Builder":
        # Show custom builder, keep current orientations
        return (
            gr.update(visible=True),
            f"Configure custom orientations below (currently {len(current_orientations)} cameras)",
        )
    else:
        # Load preset
        current_orientations = list(
            ORIENTATION_PRESETS.get(
                preset_name, ORIENTATION_PRESETS["Standard (10 cameras)"]
            )
        )
        return (
            gr.update(visible=False),
            f"Using preset: {len(current_orientations)} cameras configured",
        )


def build_custom_orientations(
    pitch_0, pitch_10, pitch_20, pitch_30, yaw_mode, yaw_start, yaw_end, yaw_step
):
    """Build custom orientations from user selections"""
    global current_orientations

    # Collect selected pitch angles
    pitches = []
    if pitch_0:
        pitches.append(0)
    if pitch_10:
        pitches.append(-10)
    if pitch_20:
        pitches.append(-20)
    if pitch_30:
        pitches.append(-30)

    if not pitches:
        return "⚠️ Please select at least one pitch angle", "No cameras configured"

    # Determine yaw angles
    if yaw_mode == "Cardinal (4 dirs)":
        yaws = [0, 90, 180, -90]
    elif yaw_mode == "All Around (8 dirs)":
        yaws = [0, 45, 90, 135, 180, -135, -90, -45]
    else:  # Custom Range
        try:
            yaws = list(range(int(yaw_start), int(yaw_end) + 1, int(yaw_step)))
            if not yaws:
                return "⚠️ Invalid yaw range", "No cameras configured"
        except Exception as e:
            return f"⚠️ Error in yaw range: {e}", "No cameras configured"

    # Build orientations (pitch × yaw, roll=0)
    current_orientations = [[p, y, 0] for p in pitches for y in yaws]

    print(
        f"[DEBUG] Custom config: {len(pitches)} pitches × {len(yaws)} yaws = {len(current_orientations)} cameras"
    )
    print(f"[DEBUG] Pitches: {pitches}")
    print(f"[DEBUG] Yaws: {yaws}")

    count_msg = f"✅ Custom configuration ready: {len(current_orientations)} cameras"
    preset_msg = f"Custom: {len(current_orientations)} cameras configured"

    return count_msg, preset_msg


def generate_multi_orientation_previews(
    cam_id,
    geo_position,
    pos_x,
    pos_y,
    pos_z,
    fov,
    fps,
    res_preset,
    label,
    carla_host,
    carla_port,
    carla_map,
):
    """
    Generate preview frames for multiple predefined orientations
    Uses BATCH processing for faster performance!

    Returns:
        (gallery_images, camera_data_list, status_message)
    """
    global generated_cameras, current_orientations

    import time
    from utils.carla_utils import batch_preview_frames

    # Check CARLA
    is_running, server_msg = check_carla_server(carla_host, int(carla_port))
    if not is_running:
        return [], [], server_msg

    start_time = time.time()

    # Create all camera specs using current_orientations
    camera_specs_list = []
    for idx, orientation in enumerate(current_orientations):
        pitch, yaw, roll = orientation

        cam_spec = create_camera_spec_dict(
            f"{cam_id}_{idx:02d}",
            geo_position,
            pos_x,
            pos_y,
            pos_z,
            pitch,
            yaw,
            roll,
            fov,
            fps,
            res_preset,
            label,
        )
        camera_specs_list.append(cam_spec)

    print(
        f"[DEBUG] Generating {len(camera_specs_list)} camera previews in batch mode..."
    )

    # BATCH preview (much faster!)
    results = batch_preview_frames(
        camera_specs_list, carla_host, int(carla_port), carla_map
    )

    # Process results
    gallery_images = []
    camera_data = []
    generated_cameras = []  # Reset

    for idx, (frame, cam_spec) in enumerate(results):
        gallery_images.append(frame)
        camera_data.append(
            {"index": idx, "orientation": cam_spec["orientation"], "spec": cam_spec}
        )
        generated_cameras.append(cam_spec)

    elapsed = time.time() - start_time
    status = f"✅ Generated {len(gallery_images)} preview frames in {elapsed:.1f}s"
    print(f"[DEBUG] {status}")

    return gallery_images, camera_data, status


def toggle_camera_selection(selected_data: gr.SelectData):
    """Toggle camera selection on/off"""
    global selected_indices

    idx = selected_data.index

    if idx in selected_indices:
        selected_indices.remove(idx)
    else:
        selected_indices.append(idx)

    return update_selected_cameras_display()


def update_selected_cameras_display():
    """Update the dataframe showing selected cameras"""
    global generated_cameras, selected_indices

    if not selected_indices:
        return gr.update(value=[]), "No cameras selected", gr.update(visible=False)

    # Build dataframe data
    df_data = []
    for idx in sorted(selected_indices):
        if idx < len(generated_cameras):
            cam = generated_cameras[idx]
            df_data.append(
                [
                    cam["id"],
                    f"{cam['orientation'][0]}°",
                    f"{cam['orientation'][1]}°",
                    f"{cam['orientation'][2]}°",
                    cam["fov"],
                    f"{cam['resolution'][0]}x{cam['resolution'][1]}",
                ]
            )

    info = f"✅ Selected: {len(selected_indices)} cameras"

    return gr.update(value=df_data), info, gr.update(visible=True)


def calibrate_selected_cameras(carla_host, carla_port, carla_map, output_dir):
    """Run calibration for all selected cameras"""
    global generated_cameras, selected_indices

    if not selected_indices:
        return [], None, "❌ No cameras selected"

    # Check CARLA
    is_running, server_msg = check_carla_server(carla_host, int(carla_port))
    if not is_running:
        return [], None, server_msg

    # Get selected camera specs
    selected_cameras = [
        generated_cameras[idx]
        for idx in sorted(selected_indices)
        if idx < len(generated_cameras)
    ]

    if not selected_cameras:
        return [], None, "❌ No valid cameras selected"

    # Create temp YAML with all selected cameras
    camera_specs = {"cameras": selected_cameras}

    temp_yaml = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(camera_specs, f)
            temp_yaml = f.name

        # Create output directory
        if not output_dir or len(output_dir.strip()) == 0:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_dir = f"./output/{timestamp}_multi"

        # Run calibration
        success, log_msg, output_files = run_autocalibration(
            temp_yaml, output_dir, carla_host, int(carla_port), carla_map
        )

        if not success:
            return [], None, log_msg

        # Return results
        calib_files = output_files["calibration"]
        viz_imgs = output_files["visualization"]

        success_msg = f"✅ Calibration complete!\n\n"
        success_msg += f"📁 Output: {output_dir}\n"
        success_msg += f"📷 Cameras: {len(selected_cameras)}\n"
        success_msg += f"📊 Files: {len(calib_files)}\n"
        success_msg += f"🖼️ Visualizations: {len(viz_imgs)}\n"

        return calib_files, viz_imgs, success_msg

    except Exception as e:
        import traceback

        return [], None, f"❌ Error: {str(e)}\n\n{traceback.format_exc()}"
    finally:
        if temp_yaml and os.path.exists(temp_yaml):
            try:
                os.unlink(temp_yaml)
            except:
                pass


def run_single_calibration(
    cam_id,
    geo_position,
    pos_x,
    pos_y,
    pos_z,
    pitch,
    yaw,
    roll,
    fov,
    fps,
    res_preset,
    label,
    carla_host,
    carla_port,
    carla_map,
    output_dir,
):
    """Run calibration for single camera"""
    # Check CARLA
    is_running, server_msg = check_carla_server(carla_host, int(carla_port))
    if not is_running:
        return [], None, server_msg

    # Create camera spec
    cam_spec = create_camera_spec_dict(
        cam_id,
        geo_position,
        pos_x,
        pos_y,
        pos_z,
        pitch,
        yaw,
        roll,
        fov,
        fps,
        res_preset,
        label,
    )

    # Create temp YAML
    temp_yaml = create_temp_camera_spec_from_dict(cam_spec)

    try:
        # Create output directory
        if not output_dir or len(output_dir.strip()) == 0:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_dir = f"./output/{timestamp}_single"

        # Run calibration
        success, log_msg, output_files = run_autocalibration(
            temp_yaml, output_dir, carla_host, int(carla_port), carla_map
        )

        if not success:
            return [], None, log_msg

        # Return results
        calib_files = output_files["calibration"]
        viz_img = (
            output_files["visualization"][0] if output_files["visualization"] else None
        )

        success_msg = f"✅ Calibration complete!\n\n"
        success_msg += f"📁 Output: {output_dir}\n"
        success_msg += f"📊 Files: {len(calib_files)}\n"

        return calib_files, viz_img, success_msg

    except Exception as e:
        import traceback

        return [], None, f"❌ Error: {str(e)}\n\n{traceback.format_exc()}"
    finally:
        if temp_yaml and os.path.exists(temp_yaml):
            try:
                os.unlink(temp_yaml)
            except:
                pass


# -------------------------------------------------------------------------
# Gradio UI
# -------------------------------------------------------------------------

theme = gr.themes.Soft(primary_hue="blue", secondary_hue="cyan")

with gr.Blocks(title="CARLA Camera Calibration") as demo:

    gr.HTML(
        """
    <div style="text-align: center; padding: 20px;">
        <h1>📷 CARLA Camera Calibration Tool</h1>
        <p style="font-size: 16px; color: #666;">
            Single camera or multi-camera batch calibration
        </p>
    </div>
    """
    )

    # Common CARLA config (shared across tabs)
    with gr.Row():
        carla_host_input = gr.Textbox(
            label="CARLA Host", value=DEFAULT_CARLA_HOST, scale=1
        )
        carla_port_input = gr.Number(
            label="CARLA Port", value=DEFAULT_CARLA_PORT, precision=0, scale=1
        )
        carla_map_input = gr.Dropdown(
            choices=AVAILABLE_MAPS,
            value="(Current Map)",
            label="CARLA Map",
            scale=2,
            allow_custom_value=True,
            interactive=True,
        )

    with gr.Tabs():
        # ===== TAB 1: Single Camera =====
        with gr.Tab("🎯 Single Camera"):
            with gr.Row():
                with gr.Column(scale=1):
                    gr.Markdown("### 📝 Camera Parameters")

                    cam_id_single = gr.Textbox(label="Camera ID", value="cam_single")
                    geo_pos_single = gr.Checkbox(
                        label="Geo Position (lat/lon)", value=False
                    )

                    with gr.Row():
                        pos_x_single = gr.Number(label="X / Lat", value=37.37186)
                        pos_y_single = gr.Number(label="Y / Lon", value=-121.964616)
                        pos_z_single = gr.Number(label="Z / Alt (m)", value=25.0)

                    with gr.Row():
                        pitch_single = gr.Slider(
                            -90, 90, value=-20, step=5, label="Pitch"
                        )
                        yaw_single = gr.Slider(-180, 180, value=0, step=5, label="Yaw")
                        roll_single = gr.Slider(-90, 90, value=0, step=5, label="Roll")

                    fov_single = gr.Slider(30, 120, value=90, step=5, label="FOV")
                    res_single = gr.Dropdown(
                        ["640x480", "1280x720", "1920x1080", "3840x2160"],
                        value="1920x1080",
                        label="Resolution",
                    )
                    fps_single = gr.Slider(10, 60, value=30, step=10, label="FPS")
                    label_single = gr.Dropdown(
                        [
                            ("11 - Building/Wall", 11),
                            ("1 - Road", 1),
                            ("7 - Pole", 7),
                            ("6 - Road Line", 6),
                            ("8 - Sidewalk", 8),
                        ],
                        value=1,
                        label="Segmentation Label",
                    )

                    gr.Markdown("---")
                    gr.Markdown("### 🎬 Preview Control")

                    realtime_single = gr.Checkbox(
                        label="🔴 Realtime Preview",
                        value=False,
                        info="Auto-update when params change",
                    )
                    preview_single_btn = gr.Button(
                        "👁️ Show Preview", variant="secondary", size="sm"
                    )

                    gr.Markdown("---")
                    output_dir_single = gr.Textbox(
                        label="Output Directory (optional)",
                        value="",
                        placeholder="Auto-generated if empty",
                    )

                    calibrate_single_btn = gr.Button(
                        "🚀 Run Calibration", variant="primary", size="lg"
                    )

                with gr.Column(scale=1):
                    gr.Markdown("### 📸 Live Preview")
                    preview_status_single = gr.Markdown(
                        "👈 Enable realtime or click 'Show Preview'"
                    )
                    preview_image_single = gr.Image(label="Camera View", height=350)

                    gr.Markdown("### 📊 Calibration Results")
                    status_single = gr.Markdown("")
                    files_single = gr.Files(label="Calibration Files")
                    viz_single = gr.Image(label="Visualization", height=300)

        # ===== TAB 2: Multi-Camera =====
        with gr.Tab("🎭 Multi-Camera (Auto-Orientation)"):
            with gr.Row():
                # LEFT PANEL: Configuration & Selected List
                with gr.Column(scale=1):
                    gr.Markdown("### ⚙️ Base Configuration")

                    cam_id_multi = gr.Textbox(label="Camera ID Prefix", value="cam")
                    geo_pos_multi = gr.Checkbox(
                        label="Geo Position (lat/lon)", value=False
                    )

                    with gr.Row():
                        pos_x_multi = gr.Number(label="X / Lat", value=37.37186)
                        pos_y_multi = gr.Number(label="Y / Lon", value=-121.964616)
                        pos_z_multi = gr.Number(label="Z / Alt (m)", value=25.0)

                    fov_multi = gr.Slider(30, 120, value=90, step=5, label="FOV")
                    res_multi = gr.Dropdown(
                        ["640x480", "1280x720", "1920x1080", "3840x2160"],
                        value="1920x1080",
                        label="Resolution",
                    )
                    fps_multi = gr.Slider(10, 60, value=30, step=10, label="FPS")
                    label_multi = gr.Dropdown(
                        [
                            ("11 - Building/Wall", 11),
                            ("1 - Road", 1),
                            ("7 - Pole", 7),
                            ("6 - Road Line", 6),
                            ("8 - Sidewalk", 8),
                        ],
                        value=1,
                        label="Segmentation Label",
                    )

                    gr.Markdown("---")
                    gr.Markdown("### 🎯 Orientation Configuration")

                    preset_selector = gr.Dropdown(
                        choices=[
                            "Standard (10 cameras)",
                            "Level Only (4 cameras)",
                            "Downward Focus (6 cameras)",
                            "Dense Coverage (16 cameras)",
                            "Custom Builder",
                        ],
                        value="Standard (10 cameras)",
                        label="Orientation Preset",
                    )

                    preset_info = gr.Markdown("Using preset: 10 cameras configured")

                    # Custom Builder (hidden by default)
                    with gr.Group(visible=False) as custom_builder:
                        gr.Markdown("**Custom Orientation Builder**")

                        gr.Markdown("_Pitch Angles (select which to include):_")
                        with gr.Row():
                            pitch_0_check = gr.Checkbox(label="0°", value=True)
                            pitch_10_check = gr.Checkbox(label="-10°", value=True)
                            pitch_20_check = gr.Checkbox(label="-20°", value=True)
                            pitch_30_check = gr.Checkbox(label="-30°", value=False)

                        gr.Markdown("_Yaw Coverage:_")
                        yaw_mode = gr.Radio(
                            choices=[
                                "Cardinal (4 dirs)",
                                "All Around (8 dirs)",
                                "Custom Range",
                            ],
                            value="Cardinal (4 dirs)",
                            label="Yaw Pattern",
                        )

                        with gr.Row(visible=False) as yaw_custom_row:
                            yaw_start = gr.Number(label="Start", value=-90)
                            yaw_end = gr.Number(label="End", value=180)
                            yaw_step = gr.Number(label="Step", value=45)

                        custom_count = gr.Markdown("Will generate: 8 cameras")

                        apply_custom_btn = gr.Button("Apply Custom Config", size="sm")

                    generate_btn = gr.Button(
                        "🎬 Generate Previews", variant="secondary", size="lg"
                    )

                    gr.Markdown("---")
                    gr.Markdown("### ✅ Selected Cameras")

                    selected_info = gr.Markdown("No cameras selected")

                    selected_cameras_df = gr.Dataframe(
                        headers=["ID", "Pitch", "Yaw", "Roll", "FOV", "Resolution"],
                        datatype=["str", "str", "str", "str", "number", "str"],
                        row_count=(0, "dynamic"),
                        column_count=(6, "fixed"),
                        interactive=False,
                    )

                    with gr.Row():
                        clear_selection_btn = gr.Button("🗑️ Clear All", size="sm")
                        select_all_btn = gr.Button("☑️ Select All", size="sm")

                    output_dir_multi = gr.Textbox(
                        label="Output Directory (optional)",
                        value="",
                        placeholder="Auto-generated if empty",
                    )

                    calibrate_multi_btn = gr.Button(
                        "🚀 Calibrate Selected",
                        variant="primary",
                        size="lg",
                        visible=False,
                    )

                # RIGHT PANEL: Gallery & Results
                with gr.Column(scale=2):
                    gr.Markdown("### 🖼️ Camera Previews (Click to Toggle Selection)")

                    status_multi = gr.Markdown("Click 'Generate Previews' to start")

                    gallery_multi = gr.Gallery(
                        label="Generated Cameras",
                        show_label=False,
                        columns=4,
                        rows=3,
                        height=500,
                        object_fit="contain",
                    )

                    gr.Markdown("### 📊 Calibration Results")
                    result_status = gr.Markdown("")
                    result_files = gr.Files(label="Output Files")
                    result_viz = gr.Gallery(
                        label="Visualizations",
                        show_label=True,
                        columns=3,
                        height=400,
                        object_fit="contain",
                    )

    # ===== EVENT HANDLERS =====

    # ===== TAB 1: Single Camera Events =====

    # Single camera preview
    def preview_single_camera(
        cam_id,
        geo_pos,
        px,
        py,
        pz,
        pitch,
        yaw,
        roll,
        fov,
        fps,
        res,
        label,
        host,
        port,
        map_name,
        realtime,
    ):
        """Preview single camera frame"""
        if not realtime:
            return None, "⏸️ Realtime preview disabled"

        cam_spec = create_camera_spec_dict(
            cam_id, geo_pos, px, py, pz, pitch, yaw, roll, fov, fps, res, label
        )
        frame, msg = quick_preview_frame(cam_spec, host, int(port), map_name)
        return frame, msg

    single_preview_inputs = [
        cam_id_single,
        geo_pos_single,
        pos_x_single,
        pos_y_single,
        pos_z_single,
        pitch_single,
        yaw_single,
        roll_single,
        fov_single,
        fps_single,
        res_single,
        label_single,
        carla_host_input,
        carla_port_input,
        carla_map_input,
        realtime_single,
    ]

    # Manual preview button
    preview_single_btn.click(
        fn=preview_single_camera,
        inputs=single_preview_inputs,
        outputs=[preview_image_single, preview_status_single],
    )

    # Realtime preview on slider release (only for sliders, not Number inputs)
    for widget in [pitch_single, yaw_single, roll_single, fov_single]:
        widget.release(
            fn=preview_single_camera,
            inputs=single_preview_inputs,
            outputs=[preview_image_single, preview_status_single],
        )

    # For Number inputs (position), use change event instead
    for widget in [pos_x_single, pos_y_single, pos_z_single]:
        widget.change(
            fn=preview_single_camera,
            inputs=single_preview_inputs,
            outputs=[preview_image_single, preview_status_single],
        )

    # Calibration
    calibrate_single_btn.click(
        fn=run_single_calibration,
        inputs=[
            cam_id_single,
            geo_pos_single,
            pos_x_single,
            pos_y_single,
            pos_z_single,
            pitch_single,
            yaw_single,
            roll_single,
            fov_single,
            fps_single,
            res_single,
            label_single,
            carla_host_input,
            carla_port_input,
            carla_map_input,
            output_dir_single,
        ],
        outputs=[files_single, viz_single, status_single],
    )

    # ===== TAB 2: Multi-Camera Events =====

    # Preset selection
    preset_selector.change(
        fn=update_preset_selection,
        inputs=[preset_selector],
        outputs=[custom_builder, preset_info],
    )

    # Show/hide custom yaw range based on yaw mode
    def toggle_yaw_custom(mode):
        return gr.update(visible=(mode == "Custom Range"))

    yaw_mode.change(fn=toggle_yaw_custom, inputs=[yaw_mode], outputs=[yaw_custom_row])

    # Apply custom orientation config
    apply_custom_btn.click(
        fn=build_custom_orientations,
        inputs=[
            pitch_0_check,
            pitch_10_check,
            pitch_20_check,
            pitch_30_check,
            yaw_mode,
            yaw_start,
            yaw_end,
            yaw_step,
        ],
        outputs=[custom_count, preset_info],
    )

    # Multi-camera: Generate previews
    generate_btn.click(
        fn=generate_multi_orientation_previews,
        inputs=[
            cam_id_multi,
            geo_pos_multi,
            pos_x_multi,
            pos_y_multi,
            pos_z_multi,
            fov_multi,
            fps_multi,
            res_multi,
            label_multi,
            carla_host_input,
            carla_port_input,
            carla_map_input,
        ],
        outputs=[gallery_multi, gr.State(), status_multi],
    )

    # Multi-camera: Toggle selection
    gallery_multi.select(
        fn=toggle_camera_selection,
        outputs=[selected_cameras_df, selected_info, calibrate_multi_btn],
    )

    # Multi-camera: Clear all selections
    def clear_all_selections():
        global selected_indices
        selected_indices = []
        return gr.update(value=[]), "No cameras selected", gr.update(visible=False)

    clear_selection_btn.click(
        fn=clear_all_selections,
        outputs=[selected_cameras_df, selected_info, calibrate_multi_btn],
    )

    # Multi-camera: Select all
    def select_all_cameras():
        global selected_indices, generated_cameras
        selected_indices = list(range(len(generated_cameras)))
        return update_selected_cameras_display()

    select_all_btn.click(
        fn=select_all_cameras,
        outputs=[selected_cameras_df, selected_info, calibrate_multi_btn],
    )

    # Multi-camera: Calibrate selected
    calibrate_multi_btn.click(
        fn=calibrate_selected_cameras,
        inputs=[carla_host_input, carla_port_input, carla_map_input, output_dir_multi],
        outputs=[result_files, result_viz, result_status],
    )


if __name__ == "__main__":
    # Get Gradio server configuration from environment variables
    server_name = os.getenv("GRADIO_SERVER_NAME", "0.0.0.0")
    server_port = int(os.getenv("GRADIO_MANUAL_SERVER_PORT", "7861"))

    print(f"Starting CARLA Camera Calibration Tool")
    print(
        f"   Web UI: http://{server_name if server_name != '0.0.0.0' else 'localhost'}:{server_port}"
    )
    print(f"   CARLA Server: {DEFAULT_CARLA_HOST}:{DEFAULT_CARLA_PORT}")

    # Load available maps BEFORE creating UI
    print("Loading available CARLA maps...")

    demo.launch(
        server_name=server_name,
        server_port=server_port,
        share=False,
        show_error=True,
        theme=theme,
    )
