
# Dataset Processing Pipeline

This repository provides tools to verify the correctness of all generated data and to convert ground truth data into a format compatible with the MTMC model.

## Objectives

This repository aims to:
1. Conduct sanity checks for all generated data.
2. Convert data to save space and reduce redundant ground truth data.

---

## Dataset Information

- PhysicalAI-SmartSpaces datasets are available on [Hugging Face](https://huggingface.co/datasets/nvidia/PhysicalAI-SmartSpaces).

---

## Installation

- Python version: 3.10

1. Install Anaconda3 by following this [tutorial](https://www.anaconda.com/docs/getting-started/anaconda/install#macos-linux-installation).
2. Create a new environment:
    ```bash
    conda create -n postpro python=3.10
    ```
3. Activate the `postpro` environment:
    ```bash
    conda activate postpro
    ```
4. Install required dependencies:
    ```bash
    pip install -r requirements.txt
    ```
5. Install `ffmpeg` on Linux:
    ```bash
    sudo apt update
    sudo apt install ffmpeg
    ffmpeg -version
    ```

---

## Get Started

### 1. Semantic Labeling
Label all objects to enable the generation of Ground Truth (GT) data for specific targets before SDG. If the asset scene being used is SimReady, the existing semantic labels can be directly utilized for SDG. If not, follow the steps below to add semantic labels for specific targets.

Steps (example: labeling boxes):
- Identify candidate prims by keyword search
  - Open and review `semantic_labeling/box_check.py`.
  - Update the `patterns` dictionary (and optional `excluded_pattern`) to match your targets (e.g., keys containing "box").
  - Set the output list path in `output_file` (e.g., `path/to/box_check_result.txt`).
  - Run the script in Isaac Sim Script Editor so it can traverse the live USD stage.
  - Verify the `.txt` result contains the intended prim paths.

- Apply labels when the selection is correct
  - In `box_check.py`, uncomment the two `enable_semantics(prim, category, "class")` calls near where categories are appended.
  - Re-run the script in the Script Editor to write semantics onto the selected prims.
  - Save your USD stage once labeling looks correct.

- Iterate if needed
  - If results are too broad or missing items, refine `patterns` and/or `excluded_pattern`, re-run, and review the `.txt` again.
  - After confirming labels are applied, comment out the `enable_semantics(...)` calls to avoid reapplying semantics on subsequent scans.

- Remove labels (optional)
  - To clear all labels and start over, use `semantic_labeling/remove_label.py` in the Script Editor; then repeat the steps above.

**Notes**:
- Above codes need to excuted in **IsaacSim Editor Script**.
- Always back up or work on a copy of your USD stage before bulk labeling.
- Use consistent semantic types and values; the example uses type `class` and category names as data.
- Keep category names stable across scenes to simplify downstream filtering.

### 2. Raw Data Sanity Checks

Expected dataset layout (per scene):
```
/path/to/dataset/
  _World_Cameras_Camera/       # or _World_Cameras_Camera_01, _World_Cameras_Camera_02, ...
    rgb/                       # RGB frames (rgb_00000.jpg ...)
    object_detection/          # 2D/3D annotation JSONs (object_detection_00000.json ...)
    distance_to_image_plane/   # Depth .npy (distance_to_image_plane_00000.npy ...)
```

- **RGB image sanity check**
    ```bash
    python data_sanity_check/dataset_sanity_check_rgb.py --base_dir /path/to/dataset --total_frames 9000
    ```
    - Outputs: `sanity_rgb_check_log.txt` under `--base_dir` (if `--output_log` omitted)
    - Notes:
      - Expects folders named `_World_Cameras_Camera*` with an `rgb/` subfolder.
      - Checks missing/unreadable frames only; does not validate content quality.

- **NPY depth map sanity check**
    ```bash
    python data_sanity_check/dataset_sanity_check_npy.py --base_dir /path/to/dataset --total_frames 9000
    ```
    - Outputs: `sanity_depth_map_check_log.txt` under `--base_dir` (if `--output_log` omitted)
    - Notes:
      - Expects `distance_to_image_plane/` with files like `distance_to_image_plane_00000.npy`.
      - Verifies presence and readability via NumPy; does not validate physical ranges.

- **Ground truth sanity check**
    ```bash
    python data_sanity_check/dataset_sanity_check_json.py --base_dir /path/to/dataset --total_frames 9000
    ```
    - Outputs: `sanity_object_detection_check_log.txt` under `--base_dir` (if `--output_log` omitted)
    - Notes:
      - Expects `object_detection/object_detection_00000.json` style files.
      - Flags missing/unreadable/empty JSONs and overflow values (>1e10 or inf) in bbox fields.

- **Calibration file sanity check**
    See NVIDIA Spatial AI documentation for calibration generation: [Simulation and Synthetic Data Generation](https://developer.nvidia.com/docs/oiss/spatial-ai/Simulation-and-Synthetic-Data-Generation.html).
    ```bash
    python data_sanity_check/dataset_sanity_check_calibration.py --base_dir /path/to/dataset --calibration /path/to/calibration/file --output_dir /path/to/output
    ```
    - Outputs (in `--output_dir`):
      - Per-camera images: `_intr_extr.png`, `_homo.png`, `_pair_points.png`, `_cam_params.png`, `_homo_from_pp.png`, `_vis.png`
    - Notes:
      - Ensure calibration JSON contains `sensors[].intrinsicMatrix`, `extrinsicMatrix`, homography or paired points when required.
      - All cameras should share consistent naming (`_World_Cameras_Camera*`).

- **Bounding box sanity check**
    ```bash
    python data_sanity_check/dataset_sanity_check_bbox_direction.py \
      --root_dir /path/to/dataset \
      --calibration /path/to/calibration/file \
      --save_path /path/to/save/exceptional/bounding/box/data \
      --frame_idx_list 0 100 200 \
      --prim_key /World/Object_A /World/Object_B \
      --xform_json_path /path/to/output_xform.json
    ```
    - `--prim_key` (optional): Only process the listed prim paths.
    - `--xform_json_path` (optional): Path to the parent Xform rotation JSON generated by `export_xform_semantics.py`.
    Outputs saved to `--root_dir`:
    - `bbox_3d_nan.json`: objects/frames with NaNs
    - `bbox_3d_direction_rotation.json`: objects with non-trivial pitch/roll
    - `bbox_3d_corners_comparison.json`: keys with corner inconsistency
    - Per-frame images under each camera folder: `bbox_3d_direction_*.jpg`
    - Notes:
      - `--save_path` directory should exist and be writable for corner comparison snapshots.
      - If some prims need manual rotation correction, generate `--xform_json_path` via `export_xform_semantics.py`.
---

### 3. Data Conversion

- **RGB to video conversion**
    ```bash
    bash data_conversion/convert_images_to_videos_no_bframes.sh /path/to/dataset
    ```
    - Outputs: MP4 video per camera folder placed alongside the images (no B-frames).
    - Notes:
      - Requires `ffmpeg`; verify with `ffmpeg -version`.
      - Script enforces no B-frames to ease frame-to-time mapping.

- **NPY to PNG depth map conversion**
    ```bash
    python data_conversion/convert_npy_to_png_depthmap.py /path/to/dataset
    ```
    - Outputs: `distance_to_image_plane_png/` per camera with `.png` depth images.
    - Notes:
      - Depth is scaled by 1000 and saved as `uint16`.
      - Ensure enough disk space; PNGs are larger than raw `.npy` in some cases.

- **Ground truth conversion**
    ```bash
    python data_conversion/convert_ground_truth.py /path/to/dataset --calibration /path/to/calibration/file --output /path/to/ output
    ```
    Note: Use **--xform_info** when some objects do not inherit the parent Xform rotation (i.e., need explicit rotation from the parent Xform). Generate this file from Isaac Sim Script Editor using the helper script:
    ```bash
    # In Isaac Sim Script Editor
    # Set USER_OUTPUT_PATH in utils/export_xform_semantics.py
    ```
    This produces a JSON (e.g., `output_xform.json`) that you pass to convert_ground_truth via `--xform_info /path/to/output_xform.json`.

    Outputs saved to `--output`:
    - `ground_truth.json`: consolidated per-frame objects (type/id/name, 3D location/scale/rotation, 2D bboxes)
    - `bounding_boxes.json`: per-frame canonical 3D corners (`_3d_bounding_box_list`)
    - `rotation_keys_with_rot.json`: objects that exhibit pitch/roll
    - `corners_comparison_dict.json`: keys with corner inconsistency
    - Notes:
      - All camera subfolders must contain the same number of frames; the script will error otherwise.
      - Supports optional renaming format and prim-specific rotations; see comments in `convert_ground_truth.py` (e.g., `BBOX_FILTER_CONFIG`).

 - **HDF5 Conversion**
     For training/inference the pipeline reads from HDF5. Convert each camera’s RGB and depth into one .h5 per camera:
     ```bash
     bash data_conversion/convert_depth_rgb_to_h5_restarable.sh /path/to/dataset
     ```
     Output: one HDF5 per Camera folder with groups `rgb/` and `distance_to_image_plane_png/`, preserving original filenames.
    - Notes:
      - RGB stored as `uint8`, depth stored as `uint16`, gzip-compressed.
      - One `.h5` per camera folder; useful when storage quotas limit file counts.

---

### 4. Post-Conversion Sanity Checks

- **PNG depth map sanity check**
    ```bash
    python data_sanity_check/dataset_sanity_check_png.py --base_dir /path/to/dataset --total_frames 9000
    ```
    - Outputs: `sanity_png_depth_map_check_log.txt` under `--base_dir` (if `--output_log` omitted)
    - Notes:
      - Verifies file existence and readability; not physical validity.

- **Video B-frame sanity check**
    ```bash
    bash data_sanity_check/dataset_sanity_check_videos.sh /path/to/dataset
    ```
    - Outputs: Report printed to console for each video; ensures no B-frames.
    - Notes:
      - Re-encode with provided script if B-frames are detected.

- **Object velocity sanity check**
    ```bash
    python data_sanity_check/dataset_sanity_check_velocity.py --gt_dir /path/to/your/generated/ground_truth.json/file
    ```
    - Outputs: `character_speeds_<step>.json` alongside the input `ground_truth.json`.
    - Notes:
      - Set `--step` to control frame interval; high thresholds may filter meaningful motion.

---

## Troubleshooting

- Missing folders (e.g., `rgb`, `object_detection`):
  - Verify dataset layout matches the expected structure above.
- Mismatched frame counts across cameras:
  - Ensure all camera folders have the same number of frames; scripts will warn or raise.
- Projection looks wrong (3D boxes off-image):
  - Re-check `--calibration` path and content; validate with "Calibration file sanity check" step.
  - If specific objects are rotated incorrectly, export `--xform_info` and pass it to conversion/sanity scripts.
- Environment issues:
  - Confirm active interpreter: `which python && python -m pip --version`
  - Reinstall deps: `python -m pip install -r requirements.txt`

## Notice

This project will download and install additional third-party open source software projects. Review the license terms of these open source projects before use.
