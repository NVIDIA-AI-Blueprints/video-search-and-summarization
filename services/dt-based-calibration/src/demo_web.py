#!/usr/bin/env python3

# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
#
# SPDX-License-Identifier: Apache-2.0


"""
End-to-End CARLA Autocalibration Web Demo

Complete pipeline from camera specs to 3D visualization.

Usage:
    python demo_web.py
    
Then open browser at http://localhost:7860
"""

import os
import json
import gradio as gr
from pathlib import Path

# Import utilities
from utils.viz_utils import create_3d_scene
from utils.carla_utils import (
    check_carla_server,
    run_autocalibration,
    create_download_zip,
)

# Get CARLA configuration from environment variables
DEFAULT_CARLA_HOST = os.getenv("CARLA_HOST", "127.0.0.1")
DEFAULT_CARLA_PORT = int(os.getenv("CARLA_PORT", "2000"))


# -------------------------------------------------------------------------
# Event Handlers
# -------------------------------------------------------------------------


def on_test_connection(host, port):
    """Test CARLA server connection"""
    is_running, msg = check_carla_server(host, int(port), timeout=60)
    return msg


def on_calibrate(spec_file, out_dir, host, port, map_name):
    """Run calibration and update all outputs"""
    if spec_file is None:
        return {
            calibration_log: "⚠️ Please upload camera_specs.yml",
            current_output_dir: None,
            viz_gallery: [],
            json_files: [],
            viz_files: [],
            depth_files: [],
            json_preview: None,
            camera_selector: gr.Radio(choices=[], value=None),
        }

    success, log_msg, output_files = run_autocalibration(
        spec_file, out_dir, host, int(port), map_name
    )

    if not success:
        return {
            calibration_log: log_msg,
            current_output_dir: None,
            viz_gallery: [],
            json_files: [],
            viz_files: [],
            depth_files: [],
            json_preview: None,
            camera_selector: gr.Radio(choices=[], value=None),
        }

    # Extract output directory
    if output_files["calibration"]:
        actual_output_dir = str(Path(output_files["calibration"][0]).parent)
    else:
        actual_output_dir = out_dir

    # Load combined calibration
    combined_json_path = os.path.join(
        actual_output_dir, "combined_calibration.mdx.json"
    )
    json_data = None
    camera_ids = []
    if os.path.exists(combined_json_path):
        with open(combined_json_path, "r") as f:
            json_data = json.load(f)
            camera_ids = [sensor["id"] for sensor in json_data.get("sensors", [])]

    return {
        calibration_log: log_msg,
        current_output_dir: actual_output_dir,
        viz_gallery: output_files["visualization"],
        json_files: output_files["calibration"],
        viz_files: output_files["visualization"],
        depth_files: output_files["depth"],
        json_preview: json_data,
        camera_selector: gr.Radio(
            choices=camera_ids, value=camera_ids[0] if camera_ids else None
        ),
    }


def on_visualize_3d(out_dir, selected_cam, max_d, show_cam, show_axes):
    """Generate 3D visualization"""
    if out_dir is None or not os.path.isdir(out_dir):
        return None, "⚠️ Please run calibration first", None

    # Use _full.json which contains matrices for 3D visualization
    calib_path = os.path.join(out_dir, "combined_calibration_full.json")
    if not os.path.exists(calib_path):
        return None, "⚠️ Calibration file not found", None

    selected_cameras = [selected_cam] if selected_cam else None

    glb_path, log_msg, camera_pos = create_3d_scene(
        calib_path,
        out_dir,
        float(max_d),
        2,  # downsample=2
        8.0,  # camera_scale=8.0
        bool(show_cam),
        bool(show_axes),
        selected_cameras=selected_cameras,
    )

    # Load preview image
    preview_img = None
    if selected_cam:
        viz_path = os.path.join(out_dir, f"{selected_cam}_visualization.png")
        if os.path.exists(viz_path):
            preview_img = viz_path

    return glb_path, log_msg, preview_img


def on_download_all(out_dir):
    """Create zip file with all outputs"""
    return create_download_zip(out_dir)


# -------------------------------------------------------------------------
# Gradio UI
# -------------------------------------------------------------------------

theme = gr.themes.Soft(primary_hue="cyan", secondary_hue="blue")

with gr.Blocks(title="CARLA Autocalibration E2E Demo") as demo:

    gr.HTML(
        """
    <div style="text-align: center; padding: 20px;">
        <h1>🚗 Digital Twin-Based Camera Autocalibration Tool</h1>
    </div>
    """
    )

    current_output_dir = gr.State(value=None)

    with gr.Tabs():
        # TAB 1: Calibration
        with gr.Tab("1️⃣ Calibration"):
            with gr.Row():
                with gr.Column(scale=1):
                    gr.Markdown("### 📤 Upload")
                    camera_spec_upload = gr.File(
                        label="Camera Specs (YAML)",
                        file_types=[".yml", ".yaml"],
                        file_count="single",
                    )

                    gr.Markdown("### ⚙️ CARLA Configuration")
                    carla_host = gr.Textbox(
                        label="CARLA Host", value=DEFAULT_CARLA_HOST
                    )
                    carla_port = gr.Number(
                        label="CARLA Port", value=DEFAULT_CARLA_PORT, precision=0
                    )
                    carla_map = gr.Textbox(
                        label="CARLA Map (optional)",
                        value="",
                        placeholder="e.g., Town10HD_Opt (leave empty for current)",
                    )
                    output_dir_input = gr.Textbox(
                        label="Output Directory (optional)",
                        value="",
                        placeholder="Auto: ./output/YYYYMMDD_HHMMSS",
                    )

                    gr.Markdown("### 🔌 Connection")
                    server_status = gr.Markdown(
                        "Click 'Test Connection' to check CARLA"
                    )
                    test_connection_btn = gr.Button("🔍 Test Connection", size="sm")

                    gr.Markdown("### 🚀 Run")
                    calibrate_btn = gr.Button(
                        "🎯 Run Calibration", variant="primary", size="lg"
                    )

                with gr.Column(scale=1):
                    gr.Markdown("### 📋 Status")
                    calibration_log = gr.Markdown(
                        "👈 Upload camera_specs.yml and click **Run Calibration**"
                    )

                    gr.Markdown("### 📸 Preview: Visualization Images")
                    viz_gallery = gr.Gallery(
                        label="Calibration Visualizations", columns=2, height=400
                    )

        # TAB 2: Results
        with gr.Tab("2️⃣ Results & Downloads"):
            gr.Markdown("### 📦 Download Files")

            with gr.Row():
                with gr.Column():
                    gr.Markdown("**Calibration JSON**")
                    json_files = gr.Files(label="Calibration Files", interactive=False)

                with gr.Column():
                    gr.Markdown("**Visualization Images**")
                    viz_files = gr.Files(label="Visualization PNG", interactive=False)

            gr.Markdown("**Depth Maps**")
            depth_files = gr.Files(label="Depth NPY Files", interactive=False)

            gr.Markdown("### 💾 Download All")
            download_all_btn = gr.Button(
                "📦 Download All Files", variant="secondary", size="lg"
            )
            download_status = gr.Markdown("")

            gr.Markdown("### 📄 Calibration JSON Preview")
            json_preview = gr.JSON(label="Combined Calibration")

        # TAB 3: 3D Visualization
        with gr.Tab("3️⃣ 3D Visualization"):
            with gr.Row():
                with gr.Column(scale=1):
                    gr.Markdown("### 🎥 Camera Selection")
                    camera_selector = gr.Radio(
                        label="Select Camera", choices=[], value=None, interactive=True
                    )

                    gr.Markdown("### ⚙️ Settings")
                    max_depth_3d = gr.Slider(
                        10, 500, value=150, step=10, label="Max Depth (m)"
                    )

                    with gr.Row():
                        show_cameras_3d = gr.Checkbox(label="Show Cameras", value=True)
                        show_axes_3d = gr.Checkbox(label="Show Axes", value=True)

                    visualize_3d_btn = gr.Button(
                        "🎨 Generate 3D View", variant="primary"
                    )

                with gr.Column(scale=2):
                    gr.Markdown("### 🌐 Interactive 3D Viewer")
                    viewer_log = gr.Markdown("👈 Complete calibration first")

                    model_viewer_3d = gr.Model3D(
                        label="Point Cloud + Camera Poses",
                        height=500,
                        camera_position=(0, 0, 100),
                        zoom_speed=1.0,
                        pan_speed=1.0,
                    )

                    gr.Markdown("### 📸 Camera View Preview")
                    preview_image = gr.Image(label="Selected Camera View", height=250)

    # Event connections
    test_connection_btn.click(
        fn=on_test_connection, inputs=[carla_host, carla_port], outputs=server_status
    )

    calibrate_btn.click(
        fn=on_calibrate,
        inputs=[
            camera_spec_upload,
            output_dir_input,
            carla_host,
            carla_port,
            carla_map,
        ],
        outputs={
            calibration_log,
            current_output_dir,
            viz_gallery,
            json_files,
            viz_files,
            depth_files,
            json_preview,
            camera_selector,
        },
    )

    download_all_btn.click(
        fn=on_download_all,
        inputs=[current_output_dir],
        outputs=[json_files, download_status],
    )

    visualize_3d_btn.click(
        fn=on_visualize_3d,
        inputs=[
            current_output_dir,
            camera_selector,
            max_depth_3d,
            show_cameras_3d,
            show_axes_3d,
        ],
        outputs=[model_viewer_3d, viewer_log, preview_image],
    )

    # Real-time updates
    for component in [camera_selector, max_depth_3d, show_cameras_3d, show_axes_3d]:
        component.change(
            fn=on_visualize_3d,
            inputs=[
                current_output_dir,
                camera_selector,
                max_depth_3d,
                show_cameras_3d,
                show_axes_3d,
            ],
            outputs=[model_viewer_3d, viewer_log, preview_image],
        )


if __name__ == "__main__":
    server_name = os.getenv("GRADIO_SERVER_NAME", "0.0.0.0")
    server_port = int(os.getenv("GRADIO_DEMO_SERVER_PORT", "7860"))

    print(f"Starting CARLA Autocalibration Demo")
    print(
        f"   Web UI: http://{server_name if server_name != '0.0.0.0' else 'localhost'}:{server_port}"
    )
    print(f"   CARLA Server: {DEFAULT_CARLA_HOST}:{DEFAULT_CARLA_PORT}")

    demo.launch(
        server_name=server_name,
        server_port=server_port,
        share=False,
        show_error=True,
        theme=theme,
    )
