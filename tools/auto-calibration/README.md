# AutoMagicCalib

AutoMagicCalib (AMC) is an automated calibration tool that estimates both intrinsic and extrinsic camera parameters for single-camera and multi-camera systems. It provides camera projection matrices and lens distortion coefficients essential for accurate 3D reconstruction and multi-view applications.

AMC eliminates the need for traditional calibration patterns (like checkerboards) by using tracked moving objects in the scene as natural features for calibration. It leverages DeepStream's object detection and tracking capabilities to identify and follow objects (particularly people) across frames, then analyzes these trajectories across camera views to automatically derive camera parameters from regular operational footage. This approach enables calibration without interrupting normal operations, allows retroactive calibration using archived footage, and performs calibration in the actual deployment environment.


## Features
- Estimate camera lens distortion parameter (k1)
- Estimate 3x4 camera projection matrix (focal length, rotation, translation)
- Ground truth focal length override: Use known focal lengths while preserving GeoCalib rotation intelligence
- Output calibration results in YAML format
- Visualization tools:
  - Score metrics graphs of parameter estimation
  - Rectified video generation with estimated lens parameters
  - Visual overlay video generation (SV3DT) with estimated camera projection matrix
- Complete end-to-end pipeline for multi-camera calibration
- Bundle adjustment for improved accuracy
- Evaluation against ground truth data
- Support a geometry-based calibration method and a model-based calibration method (VGGT)

## Table of Contents
- [Features](#features)
- [Quick Start](#quick-start)
  - [System Requirements](#system-requirements)
  - [NGC Setup](#ngc-setup)
  - [Scripts Setup](#scripts-setup)
  - [Sample Data Setup](#sample-data-setup)
- [Launch End-to-End Pipeline](#launch-end-to-end-pipeline)
  - [Use Case 1: Calibration with Ground Truth Data, with Quantitative/Qualitative Evaluation](#use-case-1-calibration-with-ground-truth-data-with-quantitativequalitative-evaluation)
  - [Use Case 2: Calibration without Ground Truth Data, with Qualitative Evaluation](#use-case-2-calibration-without-ground-truth-data-with-qualitative-evaluation)
  - [Processing Time](#processing-time)
  - [Command Line Arguments](#command-line-arguments)
- [Output Files](#output-files)
  - [Output Structure Example](#output-structure-example)
  - [Single-View (SV) Calibration Results](#single-view-sv-calibration-results)
  - [Multi-View (MV) Calibration Results](#multi-view-mv-calibration-results)
  - [Evaluation Results](#evaluation-results-evaluation---if-ground-truth-provided)
  - [Trajectory-Only Visualization Results](#trajectory-only-visualization-results)
  - [End-to-End (E2E) Pipeline Summary](#end-to-end-e2e-pipeline-summary)
- [Format Conversion for Downstream Applications: MV3DT Use Case](#format-conversion-for-downstream-applications-mv3dt-use-case)
  - [Why Format Conversion is Needed](#why-format-conversion-is-needed)
  - [MV3DT Format Conversion](#mv3dt-format-conversion)
  - [Step-by-step Guide to Run MV3DT with Converted Calibration Files](#step-by-step-guide-to-run-mv3dt-with-converted-calibration-files)
  - [Adapting for Other Applications](#adapting-for-other-applications)
- [Manual Alignment](#manual-alignment)
- [Assumptions](#assumptions)
  - [Input Video Naming Convention](#input-video-naming-convention)
  - [Input Video Resolution](#input-video-resolution)
  - [Time-synced Input Videos](#time-synced-input-videos)
  - [Lens Distortion](#lens-distortion)
  - [Ground Truth Directory Structure](#ground-truth-directory-structure)
  - [Layout and Alignment Files](#layout-and-alignment-files)
  - [Camera Configuration](#camera-configuration)
  - [File Structure Requirements](#file-structure-requirements)
- [Development and Advanced Usage](#development-and-advanced-usage)
  - [Development Setup](#development-setup)
  - [Scripts](#scripts)
    - [1. Complete End-to-End Pipeline](#1-complete-end-to-end-pipeline)
    - [2. Single-View Calibration](#2-single-view-calibration)
    - [3. Layout Alignment](#3-layout-alignment)
    - [4. Multi-View Calibration](#4-multi-view-calibration)
    - [5. Evaluation](#5-evaluation)
    - [6. PeopleNet Detection](#6-peoplenet-detection)
    - [7. Individual Python Scripts](#7-individual-python-scripts)
  - [(Optional) Ground Truth Focal Length Override](#optional-ground-truth-focal-length-override)
  - [Configuration Files](#configuration-files)
- [Development Configuration Details](#development-configuration-details)
  - [Internal Configuration (conf.py)](#internal-configuration-confpy)
  - [Development Output Structure](#development-output-structure)
- [AI Agent Skills & Guides](#ai-agent-skills--guides)
- [License](#license)

<br><br>
# Quick Start

### System Requirements
- x86_64 system
- OS Ubuntu 24.04
- NVIDIA GPU with hardware encoder (NVENC)
- NVIDIA driver 580
- Docker (setup to run without sudo privilege)
- NVIDIA container toolkit (see [NVIDIA DeepStream Docker Prerequisites](https://docs.nvidia.com/metropolis/deepstream/dev-guide/text/DS_docker_containers.html#prerequisites))

### NGC Setup
This step is needed to pull AutoMagicCalib and DeepStream docker images.
1. Visit NGC sign in page, enter your email address and click Next, or Create an Account
2. Choose your Organization/Team
3. Generate an API key following the instructions
4. Log in to the NGC docker registry:
```bash
docker login nvcr.io
Username: "$oauthtoken"
Password: "YOUR_NGC_API_KEY"
```

### Scripts Setup
Clone the repo to your local directory

```bash
# clone the repo
cd auto-magic-calib   # or auto-magic-calib-dev (if dev repo)
git submodule update --init --recursive
git lfs pull
```

> [!WARNING]
> NOTICE — VGGT is an optional third-party model governed by Meta’s VGGT License and Acceptable Use Policy, separately from this repository’s Apache-2.0 license. The VGGT-1B-Commercial checkpoint permits commercial use subject to restrictions, including a prohibition on military applications.
>
> Before downloading or using VGGT, review and accept the applicable terms and confirm that your intended use is permitted. Redistribution of VGGT code, weights or derivatives remains subject to Meta’s agreement; these materials are not relicensed under Apache-2.0.
>
> Model and license terms:
> https://huggingface.co/facebook/VGGT-1B-Commercial
>
> VGGT is not required to use the AMC calibration method.

Download a VGGT commercial model from HuggingFace (https://huggingface.co/facebook/VGGT-1B-Commercial). 
You need to sign up HuggingFace for accessing the model.   
Once you have the model, copy the downloaded model(vggt_1B_commercial.pt) to `auto-magic-calib/models/vggt` 

Then run the setup script to prepare required resources. Docker images will be pulled automatically.

```bash
cd scripts
bash setup.sh 
```
If the setup is successful, the sample directory structure should look like this:
```
~/auto-magic-calib
├── configs
│   ├── config_AutoMagicCalib
│   │   ├── camera_matrix_config.yaml
│   │   ├── eval_config.yaml
│   │   ├── mv_amc_config.yaml
│   │   ├── preprocess_config.yaml
│   │   ├── rectification_config.yaml
│   │   └── sv_amc_config.yaml
│   └── config_DeepStream
│       ├── config_3d.yaml
│       ├── config_deepstream_2d_resnet.txt
│       ├── config_deepstream_2d_transformer.txt
│       ├── config_deepstream_3d_resnet.txt
│       ├── config_deepstream_3d_transformer.txt
│       ├── config_pgie_resnet34.txt
│       ├── config_pgie_transformer.txt
│       ├── config_tracker_resnet34_3d.yaml
│       ├── config_tracker_resnet34.yaml
│       └── config_tracker_transformer.yaml
├── models
│   ├── custom_parser
│   │   ├── libnvds_infercustomparser_tao.so
│   │   ├── Makefile
│   │   └── nvdsinfer_custombboxparser_tao.cpp
│   ├── vggt 
│   │   └── vggt_1B_commercial.pt 
│   ├── labels_peoplenet_resnet34.txt
│   ├── labels_peoplenet_transformer.txt
│   ├── peoplenet_transformer_v2.onnx
│   ├── resnet34_peoplenet_int8.etlt
│   ├── resnet34_peoplenet_int8.txt
│   └── resnet50_market1501_aicity156.onnx
├── ngc_download
│   └── peoplenet_deployable_quantized.zip
├── assets
│   └── sdg_08_2_sample_data_091025.zip
└── scripts
    ├── convert_det2kitti.sh
    ├── kittiDetect2mot_4_viz.sh
    ├── launch_AutoMagicCalib.sh
    ├── launch_ConvertToMv3dt.sh
    ├── launch_EndToEndCalib.sh
    ├── launch_Evaluation.sh
    ├── launch_MultiViewCalib.sh
    ├── launch_VggtCalib.sh
    ├── launch_Visualization.sh
    ├── run_2d.sh
    ├── run_3d.sh
    └── setup.sh
```

Also verify docker images are available:
```bash
docker images 
# Should show:
# nvcr.io/nvidia/auto-magic-calib:1.1                                    1.1
# nvcr.io/nvidia/deepstream                                              9.1-triton-multiarch
```

### Sample Data Setup
Unzip the compressed sample data file under `auto-magic-calib/assets`. The sample folder includes 4 different types of data to help you run end-to-end calibration and evaluation.
1. Input video files
2. Ground truth data
3. BirdEyeView map image
4. Pre-calibrated alignment data for BirdEyeView map

```
~/auto-magic-calib/assets/sdg_08_2_sample_data_091025
├── cam_00.mp4                  # Input video files
├── cam_01.mp4                  
├── cam_02.mp4                  
├── cam_03.mp4                  
├── manual_adjustment/
│   ├── alignment_data_orig.json     # Pre-calibrated alignment (cam0, cam1, BEV map correspondences)
│   └── layout.png                   # BirdEyeView map image required for visualization
├── _World_Cameras_Camera_00/        # Ground truth data (camera info, extrinsics, object trajectories)
│   ├── camera_params/
│   └── object_detection/
├── _World_Cameras_Camera_01/
│   ├── camera_params/
│   └── object_detection/
├── _World_Cameras_Camera_02/
│   ├── camera_params/
│   └── object_detection/
└── _World_Cameras_Camera_03/
    ├── camera_params/
    └── object_detection/
```

Now you're ready to start the calibration process.

In case you want to try your own dataset, please verify requirements (files, directories, formats, lens distortion) explained in [Assumptions](#assumptions) section.

<br><br>
#  Launch End-to-End Pipeline

This script runs a complete calibration pipeline that includes single-view calibration, multi-view calibration, and **optional** evaluation.
The pipeline supports two main use case scenarios:

### **Use Case 1: Calibration with Ground Truth Data, with Quantitative/Qualitative Evaluation**
Use when you have ground truth data and layout map for comprehensive evaluation.

> **Step 0: Prepare Linear Media, Then (Optional) Layout Alignment**
> 
> Run `launch_Rectification.sh` first for distorted videos. For videos that are already linear, use the UI's **Videos Are Already Linear** action. Both paths stage `rectified.mp4` and `rectified.jpg`. If you want visualization on a BEV map, run layout alignment after that staging and before calibration; it writes `alignment_data.json` directly in linear image coordinates.
> 
> To try your own [Manual Alignment](#manual-alignment):
> ```bash
> # Remove existing alignment_data.json to trigger the interactive GUI
> # Run layout alignment (interactive GUI will pop up)
> 
> cd auto-magic-calib/scripts
> bash launch_LayoutAlignment.sh -v ~/auto-magic-calib/assets/sdg_08_2_sample_data_091025 -o ~/auto-magic-calib/assets/sdg_08_2_sample_data_091025/output -l ~/auto-magic-calib/assets/sdg_08_2_sample_data_091025/manual_adjustment/layout.png
> ```
>
> **Step 1: End-to-End Calibration**
> ```bash
> bash launch_EndToEndCalib.sh -v <video_dir> -o <output_base_dir> -g <groundtruth_dir> -l <layout_image_path> -f <focal_lengths>
> ```
>
> **Sample Command:**
> ```bash
> cd auto-magic-calib/scripts
> bash launch_EndToEndCalib.sh -v ~/auto-magic-calib/assets/sdg_08_2_sample_data_091025 -o ~/auto-magic-calib/assets/sdg_08_2_sample_data_091025/output -g ~/auto-magic-calib/assets/sdg_08_2_sample_data_091025 -l ~/auto-magic-calib/assets/sdg_08_2_sample_data_091025/manual_adjustment/layout.png -f "1269.01, 1099.50, 1099.50, 1099.50"
> ```
>
> **Pipeline Steps:**
> 1. **Single-View Calibration** → Individual camera calibration with lens rectification
> 2. **Multi-View Calibration** → Bundle adjustment and global optimization across cameras
> 3. **Layout Post-Processing** → Generates transforms and scaled projection matrices (if layout alignment was done in Step 0)
> 4. **Evaluation and Visualization** → Accuracy metrics against ground truth. Trajectories overlay image onto BirdEyeView map.
> 
> **Key Output Files:**
> - Final refined camera parameters (most important): `multi_view_results/BA_output/results_ba/refined/camInfo_XX.yaml` - Complete intrinsic and extrinsic parameters after bundle adjustment
> - Single-view intrinsic parameters: `single_view_results/cam_XX/camInfo_hyper_XX.yaml` (original format)
> - Single-view intrinsic parameters (OpenCV): `single_view_results/cam_XX/camInfo_hyper_XX_opencv.yaml` (principal points at center)
> - Alignment data: `output/alignment_data/alignment_data.json` (rectified coordinates)
> - Evaluation metrics and visualizations (`multi_view_results/evaluation/`)
> - ... (for complete output details, see the [Output Files](#output-files) section)
>
> **Why Manual Alignment?**
>
> Even when ground truth data is provided, manual alignment is still required because the ground truth coordinate system and the layout map coordinate system are different. The layout image is a 2D representation, and the transform calculated during manual alignment establishes a custom mapping between the estimated camera coordinate system (with cam_00 as reference) and the 2D layout map. This transform is essential for visualization and also some downstream applications (such as MV3DT) that need to relate camera views to the layout.
> Layout alignment is performed on raw (unrectified) background images extracted from the input videos. During calibration, the alignment coordinates are automatically rectified to match the lens-corrected images. This workflow ensures:
> - Users can perform alignment without waiting for calibration to complete
> - Alignment coordinates are properly transformed when lens distortion correction is applied
> - The transform from camera coordinates to the 2D layout map is accurate for visualization and downstream applications (such as MV3DT)
>
> You can verify calibration output in [Output Files](#output-files).
>
> Or, proceed to [Format Conversion for Downstream Applications: MV3DT Use Case](#format-conversion-for-downstream-applications-mv3dt-use-case) to see how AMC calibration can be used by downstream applications.

### **Processing Time** ### 
Total processing time varies based on hardware specifications, video resolution, file count, and scene complexity. For the provided sample data on an RTX6000 system, expect approximately:
- **Single-view calibration**: ~10 minutes per camera (40 minutes total for 4 cameras)
- **Multi-view calibration**: ~10 minutes for bundle adjustment and optimization

### **Use Case 2: Calibration without Ground Truth Data, with Qualitative Evaluation**
Use when you have layout map for visualization but no ground truth data.

> **Step 0: Layout Alignment (Required for Visualization)**
> 
> Run layout alignment **before** calibration to enable trajectory visualization on the BEV map.
> ```bash
> cd auto-magic-calib/scripts
> bash launch_LayoutAlignment.sh -v ~/auto-magic-calib/assets/sdg_08_2_sample_data_091025 -o ~/auto-magic-calib/assets/sdg_08_2_sample_data_091025/output -l ~/auto-magic-calib/assets/sdg_08_2_sample_data_091025/manual_adjustment/layout.png
> ```
>
> **Step 1: End-to-End Calibration**
> ```bash
> bash launch_EndToEndCalib.sh -v <video_dir> -o <output_base_dir> -l <layout_image_path> -f <focal_lengths>
> ```
> **Sample Command:**
> ```bash
> cd auto-magic-calib/scripts
> bash launch_EndToEndCalib.sh -v ~/auto-magic-calib/assets/sdg_08_2_sample_data_091025 -o ~/auto-magic-calib/assets/sdg_08_2_sample_data_091025/output -f "1269.01, 1099.50, 1099.50, 1099.50"
> ```
> 
> **Pipeline Steps:**
> 1. **Single-View Calibration** → Individual camera calibration with lens rectification
> 2. **Multi-View Calibration** → Bundle adjustment and global optimization across cameras
> 3. **Layout Post-Processing** → Generates transforms and scaled projection matrices
> 
> **Key Output Files:**
> - Calibration results (`camInfo_*.yaml` files)
> - 3D trajectory data (`trajDump_Stream_0_3d.txt`)
> - Bundle adjustment results (`BA_output/results_ba/refined/`)
> - Alignment data: `output/alignment_data/alignment_data.json`
> - ... (for complete output details, see the [Output Files](#output-files) section)
> 
> **Step 2 (Optional): Trajectory Visualization**
>
> If you want to visualize trajectories on the BEV map after calibration:
> 1. Set `anchor: "layout"` in `configs/config_AutoMagicCalib/eval_config.yaml`
> 2. Run `launch_Visualization.sh`:
> 
> ```bash
> bash launch_Visualization.sh -i ~/auto-magic-calib/assets/sdg_08_2_sample_data_091025/output/single_view_results -o ~/auto-magic-calib/assets/sdg_08_2_sample_data_091025/output -l ~/auto-magic-calib/assets/sdg_08_2_sample_data_091025/manual_adjustment/layout.png
> ```
> 
> Then the pair-wise trajectory overlay images are generated:
> ```
> ~/auto-magic-calib/assets/sdg_08_2_sample_data_091025/output/multi_view_results/manual_adjustment/
> ├── overlay_img_00.png
> ├── overlay_img_01.png
> └── overlay_img_02.png
> ```
>
> You can verify calibration output (including visualization examples) in [Output Files](#output-files).
>
> Or, proceed to [Format Conversion for Downstream Applications: MV3DT Use Case](#format-conversion-for-downstream-applications-mv3dt-use-case) to see how AMC calibration can be used by downstream applications.

### **Command Line Arguments**

> **Required:**
> - `-v <video_dir>`: Directory containing input video files (*.mp4)
> - `-o <output_base_dir>`: Base directory for all outputs
> 
> **Optional:**
> - `-g <groundtruth_dir>`: Ground truth directory for evaluation
> - `-l <layout_image_path>`: Path to layout image file for manual alignment
> - `-d <detector_type>`: Detector type: 'resnet' or 'transformer' (default: 'resnet')
> - `-f <focal_lengths>`: Ground truth focal lengths comma-separated (overrides GeoCalib estimates)
> - `--no-plot`: Disable plotting for bundle adjustment

<br><br>
# Output Files

### **Output Structure Example**
Here we show the output directory structure and some key output files after running the End-to-End script successfully on propvided data.

```
output_base_dir/
├── backgrounds/                         # Background images for layout alignment (created by launch_LayoutAlignment.sh)
│   ├── cam_00/background.jpg
│   └── cam_01/background.jpg
├── alignment_data/                      # Layout alignment data
│   ├── alignment_data_orig.json         # Original coordinates (before lens rectification)
│   └── alignment_data.json              # Rectified coordinates (created during calibration)
├── single_view_results/
│   ├── cam_00/                          # Results for first camera
│   │   ├── camInfo_hyper_00.yaml        # Camera parameters
│   │   ├── camInfo_hyper_00_opencv.yaml # Camera parameters with principal points at center
│   │   └── trajDump_Stream_0_3d.txt     # 3D trajectory data (required for Multi-View)
│   └── ...                              # Additional cameras: 01, 02, ...
└── multi_view_results/                  # Multi-view calibration results
    ├── cam0_cam1/                       # Pairwise calibration results
    ├── ...                              # Additional camera pairs: 1_2, 2_3, ...
    ├── BA_output/                       # Bundle adjustment results
    │   ├── results_ba/                  # Main bundle adjustment outputs
    │   │   ├── initial/                 # Initial camera parameters
    │   │   └── refined/                 # Refined camera parameters
    │   │       ├── camInfo_00.yaml      # Final camera parameters (K, R, T, projection) in {cam0} coordinate
    │   │       └── ...                  # Additional cameras: 01, 02, ...
    │   ├── results_ba_scaled/           # Scale-adjusted calibration results
    │   ├── results_ba_scaled_world/     # Calibration results in global (map) coordinate frame
    │   │   ├── transform_cam0_to_map.json   # Transform from camera 0 to layout map
    │   │   └── overlay_img_XX.png           # Trajectory visualization overlays
    │   └── results_iterative_ba/        # Iterative refinement outputs
    ├── evaluation/                      # Evaluation results (if ground truth provided)
    │   ├── 0_1/                         # Camera pair evaluation
    │   ├── ...                          # Additional camera pairs: 1_2, 2_3, ...
    │   ├── est_camera_params_in_world/  # Coordinate-transformed camera parameters to align with the ground truth
    │   ├── statistics.txt               # L2 dist and error statistics
    │   ├── transform_cam0_to_map.json   # Transform from {cam0} to provided {map}
    │   ├── transform_world_to_map.json  # Transform from {world} to provided {map}
    │   └── <png files>                  # Statistics shown in graphs 
    └── manual_adjustment/               # Layout alignment post-processing outputs
```

### Single-View (SV) Calibration Results
> Each camera produces results in its own directory (`cam_XX/`) with the following key outputs:
> 
> ### Camera Parameter Files
> - `camInfo_hyper_XX.yaml`: Camera projection matrix (3x4) with intrinsic and extrinsic parameters
> - `camInfo_hyper_XX_opencv.yaml`: Camera parameters with principal points located at the image center (OpenCV format)
> - `distortion.yaml`: Lens distortion parameters (k1 coefficient)
> - `geocalib_distortion.yaml`: Distortion parameters estimated by GeoCalib (if distorted model used)
> - `rectified.mp4`: Video with lens distortion corrected
> - `background.jpg`: Extracted background image used for calibration
> - `geocalib_undistorted.jpg`: Undistorted background image using GeoCalib parameters (if distorted model used)
> - `config_sv_amc.yaml`: Single-view configuration used for this camera
> 
> ### 3D Tracking Results
> - `trajDump_Stream_0_3d.txt`: 3D trajectory data for tracked humans (required for Multi-View calibration)

### Multi-View (MV) Calibration Results
> ### Pairwise Calibration Results (`camX_camY/`)
> Each camera pair produces:
> - `calib_results.json`: Essential matrix estimation and relative pose parameters
> - `3d_point_alignment.png`: 3D alignment diagnostics
> - `3d_points_in_sv3dt.png`: 3D points visualization in single-view coordinate system
> 
> ### Bundle Adjustment Results (`BA_output/results_ba/`)
> - `initial/`: Initial camera parameters before optimization
>   - `camInfo_XX.yaml`: Initial camera parameters for each camera
> - `refined/`: **Refined camera parameters after optimization (primary output)**
>   - `camInfo_XX.yaml`: Optimized intrinsic and extrinsic parameters for each camera. Reference coordinate frame is `{cam_00}`.
>
> *Note: The values in `refined/camInfo_XX.yaml` is up-to-scale.*
>
> ### Scaled Bundle Adjustment Results (`BA_output/results_ba_scaled/`)
> - `camInfo_XX.yaml`: Bundle Adjustment Results converted into metric scale using the user-provided info
>
> ### Example Camera Parameter File Format
> The refined camera parameter files from provided sample data (`BA_output/results_ba/refined/camInfo_00.yaml`) contain:
> 
> ```yaml
> K:                         # 3x3 intrinsic matrix
> - - 1208.3875566209306
>   - 0.0
>   - 960.0
> - - 0.0
>   - 1208.3875566209306
>   - 540.0
> - - 0.0
>   - 0.0
>   - 1.0
> R:                        # 3x3 rotation matrix
> - - 1.0
>   - 0.0
>   - 0.0
> - - 0.0
>   - 1.0
>   - 0.0
> - - 0.0
>   - 0.0
>   - 1.0
> projectionMatrix_3x4:     # 3x4 projection matrix (K * [R|t])
> - - 1208.3875566209306
>   - 0.0
>   - 960.0
>   - 0.0
> - - 0.0
>   - 1208.3875566209306
>   - 540.0
>   - 0.0
> - - 0.0
>   - 0.0
>   - 1.0
>   - 0.0
> t:                        # 3x1 translation vector
> - - 0.0
> - - 0.0
> - - 0.0
> ```
> 
> **Note:** The actual numbers may look different from the optimization step is not deterministic.

### Evaluation Results (`evaluation/` - if ground truth provided)
> When ground truth data is available, AMC provides comprehensive evaluation capabilities to validate calibration quality:
> 
> ### Transformed Camera Parameters (`est_camera_params_in_world/`)
> 
> During evaluation, the system generates coordinate-transformed camera parameters to align with the ground truth coordinate system:
> - **`camInfo_XX.yaml`**: Camera parameters transformed to match ground truth coordinate frame
> - These files contain the same camera intrinsics but with extrinsics adjusted for proper comparison with ground truth data
> - Used internally for accurate evaluation metrics calculation
> 
> ### Understanding Evaluation Metrics
> 
> **Quantitative Metrics (`evaluation/statistics.txt`):**
> - **L2 Distance**: Measures 3D reconstruction accuracy in real-world units (meters). Lower values indicate better calibration accuracy.
> - **Reprojection Errors**: Measure how well 3D points project back to 2D images, reported in pixels. Lower pixel errors indicate more precise calibration.
> 
> **Example Statistics Output:**
> ```
> Average L2 distance: 0.34 (meters)
> Standard deviation of L2 distance: 0.09 (meters)
> Max L2 distance: 0.57 (meters)
> Min L2 distance: 0.22 (meters)
> Average reprojection error 0: 1.43 (pixels)
> Standard deviation of reprojection error 0: 0.92 (pixels)
> Max reprojection error 0: 7.68 (pixels)
> Min reprojection error 0: 0.00 (pixels)
> Average reprojection error 1: 1.95 (pixels)
> Standard deviation of reprojection error 1: 2.10 (pixels)
> Max reprojection error 1: 15.08 (pixels)
> Min reprojection error 1: 0.00 (pixels)
> ```
> 
> **Visual Analysis:**
> - **Box Plots**: Compare performance across different camera pairs, showing median accuracy and consistency. Consistent, low values across pairs indicate good overall calibration.
> - **Error Distribution Histograms**: Reveal the distribution of errors - good calibrations show narrow peaks near zero with few outliers.
> 
> **Example Evaluation Plots:**
> 
> <div align="center">
> <img src="resources/images/all_results_l2_dist.png" alt="L2 Distance Distribution" width="60%">
> </div>
> 
> <div align="center">
> <img src="resources/images/combined_histograms.png" alt="Error Distribution Histograms" width="60%">
> </div>
> 
> *Top: L2 distance distribution showing 3D reconstruction accuracy across camera pairs. Bottom: Combined error histograms showing reprojection error distributions - narrow peaks near zero indicate good calibration quality.*
> 
> #### Per-Camera-Pair Evaluation (`X_Y/` directories)
> Each camera pair (e.g., `0_1/`, `1_2/`, `2_3/`) contains:
> - **`statistics.txt`**: Statistical summary of calibration accuracy for the camera pair
> - **`l2_dist.csv`**: L2 distance measurements between estimated and ground truth 3D points
> - **`reproj_error0.csv`**: Reprojection errors for the first camera in the pair
> - **`reproj_error1.csv`**: Reprojection errors for the second camera in the pair
> - **`3d_points.png`**: 3D point cloud visualization comparing estimated vs ground truth
> - **`combined_histograms.png`**: Error distribution histograms for the camera pair
> - **`res_imgs/`**: Directory containing detailed result visualization images
> 
> #### Aggregate Evaluation Results (cross-camera statistics)
> - **`all_results.csv`**: Combined calibration accuracy results across all camera pairs
> - **`statistics.txt`**: Overall statistical summary across all camera pair evaluations
> - **`all_results_l2_dist.png`**: Aggregated L2 distance distribution plot across all pairs
> - **`all_results_reproj_error0.png`**: Combined reprojection error plot for all first cameras
> - **`all_results_reproj_error1.png`**: Combined reprojection error plot for all second cameras
> - **`combined_histograms.png`**: Overall error distribution across all camera pairs
> - **`results_layout_3d_points.png`**: Layout visualization with all 3D points overlaid
> 
> #### Trajectory Visualization With Ground Truth
> 
> <div align="center">
> <img src="resources/images/results_layout_3d_points.png" alt="Trajectory Overlay Visualization" width="45%">
> </div>
> 
> *Bird's-eye view trajectory overlay showing ground truth (white dots) vs estimated positions (colored dots) from different camera pairs on the actual floor layout.*
> 
> The bird's-eye view trajectory overlay (`results_layout_3d_points.png`) provides immediate visual validation:
> - **White dots**: Ground truth positions where people actually walked
> - **Colored dots**: Estimated positions from different camera pairs (each color represents a different pair)
> - **Accuracy indicator**: The closer the colored dots are to the white reference points, the better your calibration accuracy
> 
> This visualization gives you an immediate visual sense of how well your camera network is performing in real-world scenarios.
> 
> **Interpreting Results:**
> This comprehensive evaluation suite helps you validate calibration quality and identify potential issues before deploying your system. Look for:
> - Low L2 distances (< 0.5 meters typically indicates good accuracy)
> - Low reprojection errors (< 5 pixels typically indicates good precision)  
> - Tight clustering in trajectory overlays
> - Consistent performance across all camera pairs

### Trajectory-Only Visualization Results
> When using `launch_Visualization.sh` for use case 2, the following visualization files are generated in the `manual_adjustment/` directory:
> 
> ### Trajectory Overlay Images
> - **`overlay_img_00.png`**: Trajectory reconstructed from the first camera pair
> - **`overlay_img_01.png`**: Trajectory reconstructed from the first and second camera pairs 
> - **`overlay_img_02.png`**: Trajectory reconstructed from the first, second, and third camera pairs
> - **`alignment_data.json`**: Manual alignment transformation data
> - **`layout.png`**: Original layout image used for visualization
> 
> <div align="center">
> <img src="resources/images/overlay_img_00.png" alt="Trajectory Overlay Pair 1" width="30%">
> <img src="resources/images/overlay_img_01.png" alt="Trajectory Overlay Pair 1, 2" width="30%">
> <img src="resources/images/overlay_img_02.png" alt="Trajectory Overlay Pair 1, 2, 3" width="30%">
> </div>
> 
> *Example trajectory overlay images showing detected human trajectories projected onto the bird's eye view layout map for visual sanity check.*
> 
> These visualizations help you:
> - Verify that calibration results make sense spatially
> - Understand coverage areas of each camera
> - Validate trajectory detection and projection accuracy
> - Assess overall system performance without ground truth data

### Additional Multi-View Files
- `manual_adjustment/`: Manual layout alignment data (if manual alignment performed)
- `tracklet_to_global.json`: Cross-camera tracklet ID correspondences
- `sorted_tracklet_matches.json`: Organized tracklet matching results for all camera pairs

## End-to-End (E2E) Pipeline Summary
The complete pipeline produces a hierarchical output structure:
1. **Single-View Stage**: Individual camera calibration and 3D tracking
2. **Multi-View Stage**: Pairwise calibration, bundle adjustment, and global optimization
3. **Evaluation Stage**: Accuracy assessment against ground truth (optional)

Key files for downstream applications:
- **Final refined camera parameters (most important)**: `multi_view_results/BA_output/results_ba/refined/camInfo_XX.yaml` - Complete intrinsic and extrinsic parameters after bundle adjustment
- **Single-view intrinsic parameters**: `single_view_results/cam_XX/camInfo_hyper_XX.yaml` (original format)
- **Single-view intrinsic parameters (OpenCV)**: `single_view_results/cam_XX/camInfo_hyper_XX_opencv.yaml` (principal points at center)


<br><br>
# Format Conversion for Downstream Applications: MV3DT Use Case

AutoMagicCalib results can be converted to formats compatible with various downstream applications. This section demonstrates format conversion using MV3DT (Multi-View 3D Tracking) as an example.

Depending on the downstream application requirements, the calibration output format may need modification. Here we present the specific conversion requirements for [MV3DT](https://github.com/NVIDIA-AI-IOT/deepstream_reference_apps/tree/master/deepstream-tracker-3d-multi-view).

MV3DT is a distributed, real-time multi-view multi-target 3D tracking framework built for large-scale, calibrated camera networks. With AMC, you can effortlessly tryout MV3DT on your own datasets. This section bridges the format differences, making the calibration files compatible with MV3DT

## Why Format Conversion is Needed
AMC produces comprehensive calibration results (see [Output Files](#output-files) section for details), but different applications expect different formats:

**AMC Output Format:**
- **Camera parameters**: 3x3 intrinsic matrix (K), 3x3 rotation matrix (R), 3x1 translation vector (t), and 3x4 projection matrix stored separately in YAML files
- **Coordinate system**: 3D world coordinates with full transformation matrices
- **File naming**: `camInfo_XX.yaml` format in the refined results directory

**MV3DT Requirements:**
- **Camera parameters**: Single vector containing projection matrix in row-major order format for ObjectModelProjector
- **Object modeling**: Cylinder parameters (height, radius) for human tracking - AMC doesn't include these by default
- **Layout map**: Expects `map.png` filename (AMC uses `layout.png`)  
- **Coordinate transform**: 2D-to-2D pixel transformation (AMC outputs 3D transformation matrices)
- **Transform file**: `transform_world_to_map.json` must be provided (generated by AMC [Use Case 1, evaluation step](#use-case-1-calibration-with-ground-truth-data-with-quantitativequalitative-evaluation))

## MV3DT Format Conversion
The `scripts/launch_ConvertToMv3dt.sh` script converts AutoMagicCalib outputs to a format compatible with MV3DT (Multi-View 3D Tracking).

The following commands assume that you have followed the [Use Case 1: Calibration with Ground Truth Data, with Quantitative/Qualitative Evaluation](#use-case-1-calibration-with-ground-truth-data-with-quantitativequalitative-evaluation) section to run the end-to-end pipeline and generate the output files.

When ground truth data is provided during AMC calibration, the conversion routine automatically utilizes the resulting output files to achieve enhanced MV3DT accuracy.

**Sample Command:**
```bash
# Usage:
#   bash launch_ConvertToMv3dt.sh -i /path/to/AMC/output -o /path/to/output -g  # Include -g flag when ground truth data was used during AMC calibration
#   bash launch_ConvertToMv3dt.sh -i /path/to/AMC/output -o /path/to/output     # Omit -g flag when ground truth data was not used during AMC calibration

bash launch_ConvertToMv3dt.sh -i ~/auto-magic-calib/assets/sdg_08_2_sample_data_091025/output -o ~/auto-magic-calib/assets/sdg_08_2_sample_data_091025/mv3dt_output -g  # For Use Case 1 (with ground truth data)

# OR

bash launch_ConvertToMv3dt.sh -i ~/auto-magic-calib/assets/sdg_08_2_sample_data_091025/output -o ~/auto-magic-calib/assets/sdg_08_2_sample_data_091025/mv3dt_output # For Use Case 2 (without ground truth data)

# expected CLI output:
#   [done] Wrote camInfo/, map.png, transforms.yml to /auto-magic-calib/output
```

### Output Structure
The conversion generates MV3DT-compatible files:

```
output_directory/
├── camInfo/
│   ├── camInfo_00.yml          # Camera projection matrices in MV3DT format
│   ├── camInfo_01.yml
│   ├── camInfo_02.yml
│   └── camInfo_03.yml
├── map.png                     # Copy of layout.png
└── transforms.yml              # 2D coordinate transformation parameters
```

The output files from the conversion script is ready to be used in MV3DT. Please refer to the [MV3DT custom dataset guide](https://github.com/NVIDIA-AI-IOT/deepstream_reference_apps/tree/master/deepstream-tracker-3d-multi-view#running-mv3dt-on-custom-datasets) for custom dataset requirements.

*Note: Different downstream applications may require different conversion scripts depending on their specific input format requirements.*

## Step-by-step Guide to Run MV3DT with Converted Calibration Files
1. Follow the [MV3DT Prerequisites section](https://github.com/NVIDIA-AI-IOT/deepstream_reference_apps/tree/master/deepstream-tracker-3d-multi-view#prerequisites).
2. Create a new dataset directory named `amc_dataset`, and copy the video files and calibration files to the directory. If you're using' your own video data which includes lens distiortion, please read [Lens Distortion](#lens-distortion) first.
  ```bash
  cd /path/to/deepstream_reference_apps/deepstream-tracker-3d-multi-view
  mkdir -p datasets/amc_dataset/videos
  cp ~/auto-magic-calib/assets/sdg_08_2_sample_data_091025/*.mp4 datasets/amc_dataset/videos
  cp -r ~/auto-magic-calib/assets/sdg_08_2_sample_data_091025/mv3dt_output/* datasets/amc_dataset/
  ```
3. Prepare a MV3DT launch script for the new dataset
  ```bash
  # Copy from a sample launch script
  cp scripts/test_12cam_ds.sh scripts/test_amc_ds.sh
  # Only update the dataset and experiment paths
  sed -i -e 's/mtmc_12cam/amc_dataset/g' -e 's|deepstream/12cam|deepstream/amc|g' scripts/test_amc_ds.sh
  # Optional, show raw tracking results from all cams in BEV, instead of the aggregated results
  sed -i -e '/--average-multi-cam/d' scripts/test_amc_ds.sh
  chmod +x scripts/test_amc_ds.sh
  ```
4. Run the MV3DT launch script
  ```bash
  ./scripts/test_amc_ds.sh
  ```

**Example: MV3DT Running with AMC Calibration Results**
<div align="center">
<img src="resources/images/MV3DT_ds_viz.png" alt="MV3DT Real-time Tracking with AMC Calibration" width="70%">
</div>

*MV3DT framework running in real-time using camera calibration parameters generated by AutoMagicCalib. The system performs multi-view 3D tracking across multiple camera feeds, demonstrating successful integration of AMC calibration results with downstream tracking applications.*

## Adapting for Other Applications
For different downstream applications, you may need to create similar conversion scripts that:

1. Read the refined camera parameters from `multi_view_results/BA_output/results_ba/refined/`
2. Transform coordinate systems if needed (using transform files from evaluation)
3. Reformat the data according to the target application's requirements
4. Generate any additional metadata or configuration files needed

The MV3DT conversion scripts serve as a template for creating converters for other applications.

<br><br>
# Manual Alignment 

Manual alignment establishes a transform from the camera coordinate frame to a BEV (Bird's Eye View) map, enabling trajectory visualization on the layout image.

## When to Run Layout Alignment

Layout alignment should be run **before** calibration using the `launch_LayoutAlignment.sh` script. This workflow:
- Extracts background images from raw input videos
- Presents an interactive GUI for selecting corresponding points
- Saves alignment coordinates that are automatically rectified during calibration

## Running Layout Alignment

```bash
cd auto-magic-calib/scripts
bash launch_LayoutAlignment.sh -v <video_dir> -o <output_dir> -l <layout_image_path>

# Example:
bash launch_LayoutAlignment.sh -v ~/auto-magic-calib/assets/sdg_08_2_sample_data_091025 -o ~/auto-magic-calib/assets/sdg_08_2_sample_data_091025/output -l ~/auto-magic-calib/assets/sdg_08_2_sample_data_091025/manual_adjustment/layout.png
```

**Arguments:**
- `-v <video_dir>`: Directory containing input video files
- `-o <output_dir>`: Output directory (alignment data will be saved to `<output_dir>/alignment_data/`)
- `-l <layout_image_path>`: Path to the BEV layout map image

## Output Files

Layout alignment creates:
```
<output_dir>/
├── backgrounds/                    # Extracted background images
│   ├── cam_00/background.jpg
│   └── cam_01/background.jpg
└── alignment_data/
    └── alignment_data_orig.json    # Original coordinates (before rectification)
```

After calibration runs, the alignment coordinates are automatically rectified:
```
<output_dir>/alignment_data/
├── alignment_data_orig.json        # Original coordinates
└── alignment_data.json             # Rectified coordinates (used for visualization)
```

## Alignment Data Format

**alignment_data.json**: Contains 4 corresponding points across camera 0, camera 1, and the BEV map:
```json
[
  [
    [u0_cam0, v0_cam0],
    [u0_cam1, v0_cam1],
    [u0_bev,  v0_bev]
  ],
  [
    [u1_cam0, v1_cam0],
    [u1_cam1, v1_cam1],
    [u1_bev,  v1_bev]
  ],
  [
    [u2_cam0, v2_cam0],
    [u2_cam1, v2_cam1],
    [u2_bev,  v2_bev]
  ],
  [
    [u3_cam0, v3_cam0],
    [u3_cam1, v3_cam1],
    [u3_bev,  v3_bev]
  ]
]
```

## Using the Interactive GUI

Once the UI launches, you should see a window similar to this screenshot:

<div align="center">
<img src="resources/images/manual_alignment_UI.png" alt="Manual Alignment UI" width="85%">
</div>

In the window, the image on the left is camera 0's view, the middle is camera 1's view, and the right is the BEV map.

**Steps:**
1. Click one point in camera 0's view
2. Click the corresponding point in camera 1's view
3. Click the corresponding point in the BEV map
4. Repeat steps 1-3 for 4 corresponding points (12 clicks total)
5. Press 'q' key to save and exit

**Tips for accurate alignment:**
- Choose points that are clearly visible on the ground plane in all three views
- Select points that form a large polygon area for better accuracy
- Points are numbered and color-coded to help track correspondence

<div align="center">
<img src="resources/images/manual_alignment_data.png" alt="Manual Alignment Data" width="85%">
</div>

*Example showing 4 corresponding points selected across camera 0, camera 1, and the BEV map.*

## Workflow Summary

1. **Prepare linear media**: Rectify distorted videos, or explicitly stage already-linear uploads.
2. **Before calibration**: Run `launch_LayoutAlignment.sh` on the staged linear images → creates `alignment_data.json`.
3. **Calibration**: `launch_EndToEndCalib.sh` consumes `rectified.mp4` / `rectified.jpg` without rectification or coordinate remapping.
4. **After calibration**: `launch_Visualization.sh` uses `alignment_data.json` for trajectory overlay.


<br><br>
# Assumptions

AutoMagicCalib makes several assumptions about input data structure and naming conventions. Please ensure your data follows these requirements:

### Input Video Naming Convention:
Video files must follow the naming pattern `cam_XX.mp4` where XX matches the camera IDs in your configuration.
The camera numbers (XX) must correspond to the `cam_dir` entries in your configuration file (`mv_amc_config.yaml`).

```
input_video_directory/
├── cam_00.mp4
├── cam_01.mp4
├── cam_02.mp4
└── cam_03.mp4
```
 
## Input Video Contents:
There must be objects moving around the scene, because AMC relies on tracking results.
Cameras must be specified in order and have overlapping areas: `cam_00` overlaps with `cam_01`, and `cam_01` overlaps with `cam_02`, ...

### Input Video Resolution:
Video files' resolution should be 1920x1080.

### Time-synced Input Videos:
Input video files from all cameras must be synchronized

### Lens Distortion:
Lens rectification is a standalone stage upstream of calibration. In the UI, choose **Start Rectification** for distorted uploads or **Videos Are Already Linear** for pinhole/linear uploads. The E2E calibration command itself always requires staged linear media.
**rectification_config.yaml**: Configuration for lens rectification
```yaml
distortion:
  # Distortion model to use (must match GeoCalib model)
  # Options: 'simple_radial' or 'radial' depending on the distortion severity 
  model: "simple_radial"

param_space:
  k1_min: -0.00001  # minimum value of k1 search range
  k1_max: 0.00001   # maximum value of k1 search range
  k1_step: 100      # number of values for grid search
```
With this feature enabled, camera parameters (intrinsic and extrinsic) are being calculated on the rectified videos. Thus, all the downstream applications MUST use the rectified videos as their input in order to be consistent with their input camera parameters. Specifically, copy as below

```bash
single_view_results/cam_00/rectified.mp4 --> datasets/amc_dataset/videos/cam_00.mp4
single_view_results/cam_01/rectified.mp4 --> datasets/amc_dataset/videos/cam_01.mp4
single_view_results/cam_02/rectified.mp4 --> datasets/amc_dataset/videos/cam_02.mp4
```
...

See Step 2 in [MV3DT setup guide](#step-by-step-guide-to-run-mv3dt-with-converted-calibration-files).

### Ground Truth Directory Structure:
When providing ground truth data for evaluation, the directory structure must follow this specific naming convention:
```
groundtruth_directory/
├── _World_Cameras_Camera_00/
│   ├── camera_params/
│   └── object_detection/
├── _World_Cameras_Camera_01/
│   ├── camera_params/
│   └── object_detection/
├── _World_Cameras_Camera_02/
│   └── ... (similar structure)
└── _World_Cameras_Camera_03/
    └── ... (similar structure)
```

### Layout and Alignment Files:
For visualization and evaluation features:
- **Layout image**: A BEV (Bird's Eye View) map image (e.g., `layout.png`)
- **Alignment data**: `alignment_data.json`, created by `launch_LayoutAlignment.sh` in `<output_dir>/alignment_data/` directly in linear image coordinates.

### Camera Configuration:
The `mv_amc_config.yaml` file must contain:

- **Camera directories**: Listed in `cam_dir` array (e.g., `['cam_00', 'cam_01', 'cam_02', 'cam_03']`)
- **Camera pairing**: System automatically generates pairs from directory ordering: `[[0,1], [1,2], [2,3]]`
- **Camera numbers**: Extracted from directory names (e.g., 'cam_02' → camera 2)

### File Structure Requirements:
- Video files and ground truth directories should use consistent camera numbering
- All camera IDs must be zero-padded (e.g., `00`, `01`, `02` not `0`, `1`, `2`)
- Directory and file names are case-sensitive
- Ground truth camera parameter files follow specific internal naming conventions


<br><br>
#  Model-based Calibration Pipeline

AutoMagiCalib tool also supports a model-based calibration method.
In order to run the model-based calibration, the `launch_EndToEndCalib.sh` should be executed first for lens distortion correction. 
Once `launch_EndToEndCalib.sh` script finishes, you can run the model-based calibration using `launch_VggtCalib.sh` script.

The pipeline supports two main use case scenarios:

### **Use Case 1: Calibration with Ground Truth Data, with Quantitative/Qualitative Evaluation**
Use when you have ground truth data and layout map for comprehensive evaluation.

> **Command:**
> ```bash
> bash launch_VggtCalib.sh -a <amc_output_dir> -g <groundtruth_dir> -l <layout_image_path> 
> ```
> 
> The provided data includes pre-calibrated alignment transform (`alignment_data.json`), so manual alignment GUI won't pop up.
> To try out your own [Manual Alignment](#manual-alignment), remove the json file before running the `launch_VggtCalib.sh` command below.
> ```bash
> rm ~/auto-magic-calib/assets/sdg_08_2_sample_data_091025/manual_adjustment/alignment_data.json
> ```
>
> **Sample Command:**
> ```bash
> cd auto-magic-calib/scripts
> bash launch_VggtCalib.sh -a ~/auto-magic-calib/assets/sdg_08_2_sample_data_091025/output -g ~/auto-magic-calib/assets/sdg_08_2_sample_data_091025 -l ~/auto-magic-calib/assets/sdg_08_2_sample_data_091025/manual_adjustment/layout.png 
> ```
>
> **Pipeline Steps:**
> 1. **Camera Calibration** → camera calibration to get intrinsic and extrinsic parameters
> 2. **Manual Alignment** → Layout alignment. Interactive GUI pops up only if `alignment_data.json` doesn’t exist under the specified -l <layout_image_path>.
> 3. **Evaluation and Visualization** → Accuracy metrics against ground truth. Trajectories overlay image onto BirdEyeView map.
> 
> **Key Output Files:**
> - Final refined camera parameters (most important): `vggt_results/multi_view_results/BA_output/results_ba/refined/camInfo_XX.yaml` - Complete intrinsic and extrinsic parameters
> - Evaluation metrics and visualizations (`vggt_results/multi_view_results/evaluation/`)
> - ... (for complete output details, see the [VGGT Output Files](#vggt-output-files) section)
>
> You can verify calibration output in [VGGT Output Files](#vggt-output-files).
>
> Or, proceed to [Format Conversion for Downstream Applications: MV3DT Use Case](#format-conversion-for-downstream-applications-mv3dt-use-case) to see how AMC calibration can be used by downstream applications.

### **Use Case 2: Calibration without Ground Truth Data, with Qualitative Evaluation**
Use when you have layout map for visualization.

> **Command:**
> ```bash
> bash launch_VggtCalib.sh -a <amc_output_base_dir> 
> ```
> **Sample Command:**
> ```bash
> cd auto-magic-calib/scripts
> bash launch_VggtCalib.sh -a ~/auto-magic-calib/assets/sdg_08_2_sample_data_091025/output 
> ```
> 
> **Pipeline Steps:**
> 1. **Camera Calibration** → camera calibration to get intrinsic and extrinc parameters
> 
> **Key Output Files:**
> - Calibration results (`camInfo_*.yaml` files)
> - ... (for complete output details, see the [Vggt Output Files](#vggt-output-files) section)
> 
> **Trajectory-Only Visualization (Optional):**
>
> User may still want trajectory visualization for sanity check even without ground truth. Then the user will need to provide a map, and an alginment transform to the map. The following launch script encapsulates both manual alignment and visualization. It'll pop up the interactive GUI to the user.
> 1. Set `anchor: "layout"` in `configs/config_AutoMagicCalib/eval_config.yaml`
> 2. Run `launch_Visualization.sh` as follows
> 
> ```bash
> bash launch_Visualization.sh -i ~/auto-magic-calib/assets/sdg_08_2_sample_data_091025/output/single_view_results -o ~/auto-magic-calib/assets/sdg_08_2_sample_data_091025/output/vggt_results -l ~/auto-magic-calib/assets/sdg_08_2_sample_data_091025/manual_adjustment/layout.png
> ```
> 
> Then the pair-wise trajectory overlay images are generated in same folder as `layout.png`.
> ```
> ~/auto-magic-calib/assets/sdg_08_2_sample_data_091025/output/vggt_results/multi_view_results/manual_adjustment/
> ├── alignment_data.json
> ├── layout.png
> ├── overlay_img_00.png
> ├── overlay_img_01.png
> └── overlay_img_02.png
> ```
>
> You can verify calibration output (including visualization examples) in [VGGT Output Files](#vggt-output-files).
>
> Or, proceed to [Format Conversion for Downstream Applications: MV3DT Use Case](#format-conversion-for-downstream-applications-mv3dt-use-case) to see how AMC calibration can be used by downstream applications.

### **Command Line Arguments**

> **Required:**
> - `-a <amc_output_base_dir>`: Base directory of `launch_EndToEnd.sh` for all outputs
> 
> **Optional:**
> - `-g <groundtruth_dir>`: Ground truth directory for evaluation
> - `-l <layout_image_path>`: Path to layout image file for manual alignment



<br><br>
## Vggt Output Files

### **Output Structure Example**
Here we show the output directory structure and some key output files after running the End-to-End script and the VGGT script successfully on propvided data.

```
output_base_dir/
├── single_view_results/                     # single-view calibration results
└── multi_view_results/                      # Multi-view calibration results
└── vggt_results/                            # VGGT calibration results
    └── multi_view_results/                  
        ├── cam0_cam1/                       # Pairwise calibration results
        ├── ...                              # Additional camera pairs: 1_2, 2_3, ...
        ├── BA_output/                       
        │   ├── results_ba/                  
        │   │   └── refined/                 # Refined camera parameters
        │   │   │   ├── camInfo_00.yaml      # Final camera parameters (K, R, T, projection) in {cam0} coordinate
        │   │   │   └── ...                  # Additional cameras: 01, 02, ...
        │   ├── results_ba_scaled/           # Scale-adjusted calibration results
        │   └── results_ba_scaled_world/     # camera parameters in the world coordinate system 
        ├── evaluation/                      # Evaluation results (if ground truth provided)
        │   ├── 0_1/                         # Camera pair evaluation
        │   ├── ...                          # Additional camera pairs: 1_2, 2_3, ...
        │   ├── est_camera_params_in_world/  # Coordinate-transformed camera parameters to align with the ground truth
        │   ├── statistics.txt               # L2 dist and error statistics
        │   ├── transform_cam0_to_map.json   # Transform from {cam0} to provided {map}
        │   ├── transform_world_to_map.json  # Transform from {world} to provided {map}
        │   └── <png files>                  # Statistics shown in graphs 
        └── manual_adjustment/               # Manual layout alignment data.
```
`single_view_results` and `multi_view_results` are the same as before. 
Once VGGT script completes, `vggt_results` folder is created. 
The `vggt_results` folder has `multi_view_results` folder that has the same structure as the End-to-End script's output 

## Auto Frame Selection
The quality of VGGT calibration depends heavily on the specific image frames chosen from each input video. With manual frame selection, users would manually pick one frame per camera, run the calibration, assess the results, and repeat the process with different frames if necessary. This trial-and-error approach can be inefficient and time-consuming. To address this, we offer an automatic frame selection method, which intelligently identifies and selects the most suitable frames for calibration, streamlining the workflow and improving consistency in results.

### Configuration
To use automatic frame selection with the VGGT calibration script, you can configure the following parameters in your YAML configuration file (typically `mv_amc_config.yaml` or similar):

```yaml
vggt:
  auto_select: true           # Set to true to enable auto frame selection
  sample_interval_sec: 1      # Interval (in seconds) between candidate frames
  max_num_frames: 10000          # Maximum number of candidate frames to consider. If max_num_frames is 0, all frames are sampled.
  temp_dir: temp # Temporary folder name for storing sampled frames
  model_path: models/vggt/vggt_1B_commercial.pt  # Path to the VGGT model checkpoint
```

- If `auto_select: true`, the calibration will scan through frames from each video, sample at `sample_interval_sec` intervals, and automatically pick the set of frames that provide the most confident calibration result.
- `max_num_frames` controls how many frames will be sampled in total per camera; the selection will pick from these.
- If you want to force the calibration to use a specific time, set `auto_select: false` and provide a numeric value for `timestamp_sec`.
- `temp_dir` specifies where temporary images (frames) will be stored during calibration, so you can inspect which frames were selected.

**Typical Usage:**  
No need for users to select/inspect frames manually—just set `auto_select: true` and tune `sample_interval_sec`/`max_num_frames` as needed for your videos. The output folder under `auto_selection/best_frame` will show which frames were picked for each camera.

**Example output of auto-selection:**
```
output_base_dir/vggt_results/multi_view_results/
├── auto_selection
│   ├── best_frame                                # Image frames shows the best results
│   │   ├── cam_00_001260.jpg                     # Camera 0's image frame
│   │   └── ...                                   # Additional image frames
│   ├── num_confident_points.png                  # Plot of the number of confident points across frame index
│   └── top_k_results                             # Top k results
│       ├── rank_01                               # Rank 1 result
│       │   ├── BA_output                         # Contains camInfo_XX.yaml, overlay images
│       │   ├── frames                            # Used image frames
│       │   └── map_with_cameras_3d_points.jpg    # BEV image with point cloud
│       ├── ...                                   # Additional rank outputs  
│       └── top_k_ranking.txt                     # Top k results (rank, frame index, num_confident_points)
└── BA_output                                     # Duplication of rand 1 folder. This is for backward compatibility
    ├── results_ba
    │   └── refined
    │       ├── camInfo_00.yaml
    │       └── ...
    ├── results_ba_scaled
    │   ├── camInfo_00.yaml
    │   └── ... 
    └── results_ba_scaled_world
        ├── camInfo_00.yaml
        ├── ... 
        ├── overlay_img_00.png
        ├── ... 
        ├── transform_cam0_to_map.json
        └── transform_world_to_map.json
```

The best frames will be used for calibration, and you can inspect the ranking and statistics in the `top_k_results` and `num_confident_points.png`.

See above for the full directory structure and further explanation.

<br><br>
# Development and Advanced Usage

## Development Setup
1. Clone the AMC source code repo.
2. Set up a Python virtual environment to isolate dependencies. Make sure to activate the virtual environment (`source ~/venv/amc/bin/activate`) each time you work on the project as developer. 
> ```bash
> # Navigate to the project directory
> cd ~/auto-magic-calib-dev
> 
> # Create a virtual environment named 'amc'
> python3 -m venv ~/venv/amc
> 
> # Activate the virtual environment
> source ~/venv/amc/bin/activate
> 
> # Install required packages
> pip install -r requirements.txt
> ```
3. Run the setup script from `auto-magic-calib-dev/scripts` (similarly as in [Scripts Setup](#scripts-setup))
4. An additional argument (`-m local`) is required when running `launch_xx` scripts

## Scripts
## 1. Complete End-to-End Pipeline
*Note: General information is given in [Launch End-to-End Pipeline](#launch-end-to-end-pipeline)*

> **Command:**
> ```bash
> bash launch_EndToEndCalib.sh -v <video_dir> -o <output_base_dir> -g <groundtruth_dir> -l <layout_image_path> -f <focal_lengths> -m local
> ```

### **Pipeline Details**
> **Single-View Calibration:**
> - Runs AutoMagicCalib for each video file found
> - Estimates camera intrinsics, lens distortion, and initial pose using GeoCalib
> - Optional: Replaces GeoCalib focal length estimates with ground truth values (if `-f` provided)
> - Outputs: Individual camera parameter files (`camInfo_hyper_XX.yaml`, `camInfo_hyper_XX_opencv.yaml`) and rectified video
> 
> **3D Tracking:**
> - Runs SV3DT pipeline for each camera using calibrated parameters
> - Performs human detection and 3D trajectory estimation
> - Outputs: `trajDump_Stream_0_3d.txt` (3D trajectory data needed for Multi-View calibration)
> 
> **Multi-View Calibration:**
> - Performs pairwise calibration between overlapping camera views
> - Runs bundle adjustment for global optimization of all camera parameters
> - Outputs: Refined camera extrinsic parameters (`BA_output/results_ba/refined/camInfo_XX.yaml`)
> 
> **Manual Alignment (Use case 1 only):**
> - Interactive GUI-based alignment of camera views to layout map
> - Creates `alignment_data.json` for evaluation coordinate transformation
> - Skipped if `alignment_data.json` already exists
> 
> **Evaluation (Use case 1 only):**
> - Compares calibration results against ground truth data
> - Outputs: Accuracy metrics and visualizations in `evaluation/` directory


## 2. Single-View Calibration

For single camera calibration:

```bash
# Usage
bash launch_AutoMagicCalib.sh -i <input_video_file> -o <output_folder> [-d <detector_type>] [-m <mode>] [-f <focal_length>]

# Example: use 'transformer' model instead of default 'resnet' 
bash launch_AutoMagicCalib.sh -i /path/to/video.mp4 -o /path/to/output/ -m local -d transformer 

# Example: use ground truth focal length
bash launch_AutoMagicCalib.sh -i /path/to/video.mp4 -o /path/to/output/ -m local -f 1200.0
```

**Arguments:**
- `-i <input_video_file>`: Input video file path
- `-o <output_folder>`: Output directory
- `-d <detector_type>`: Detector type: 'resnet' or 'transformer' (default: 'resnet')
- `-m <mode>`: Execution mode: 'local' or 'docker' (default: 'docker')
- `-f <focal_length>`: Ground truth focal length in pixels (overrides GeoCalib estimate)

> **Note:** The camera distortion model (e.g., simple_radial, radial, simple_divisional) is configured in `configs/config_AutoMagicCalib/rectification_config.yaml`.

### Expected Output

Single-view calibration produces results in the specified output directory with the same structure as `single_view_results/` from the [End-to-End Pipeline](#1-complete-end-to-end-pipeline). Key outputs include:

- **Camera parameters**: `camInfo_hyper_XX.yaml` (main format) and `camInfo_hyper_XX_opencv.yaml` (OpenCV format)
- **Lens distortion**: `distortion.yaml` and `rectified.mp4` 
- **3D tracking data**: `trajDump_Stream_0_3d.txt` (required for multi-view calibration)
- **Background image**: `background.jpg`

For complete output details, see the [Output Files](#output-files) section.

## 3. Layout Alignment

For manual alignment of camera views to a BEV layout map. This should be run **before** calibration if you want trajectory visualization.

### Usage

```bash
# Usage
bash launch_LayoutAlignment.sh -v <video_dir> -o <output_dir> -l <layout_image_path> [-m <mode>]

# Example with local mode
bash launch_LayoutAlignment.sh -v /path/to/videos -o /path/to/output -l /path/to/layout.png -m local
```

**Required Arguments:**
- `-v <video_dir>`: Directory containing input video files (*.mp4)
- `-o <output_dir>`: Output directory for alignment data
- `-l <layout_image_path>`: Path to the BEV layout map image

**Optional Arguments:**
- `-m <mode>`: Execution mode: 'local' or 'docker' (default: 'docker')

### What It Does

1. **Background Extraction**: Extracts median background images from raw input videos
2. **Interactive GUI**: Displays camera 0, camera 1, and layout map side-by-side for point selection
3. **Saves Alignment Data**: Creates `alignment_data_orig.json` with coordinates in original image space

### Expected Output

```
<output_dir>/
├── backgrounds/
│   ├── cam_00/background.jpg    # Background from camera 0
│   └── cam_01/background.jpg    # Background from camera 1
└── alignment_data/
    └── alignment_data_orig.json  # Alignment coordinates (original image space)
```

During subsequent calibration, `alignment_data_orig.json` is automatically rectified to create `alignment_data.json` with coordinates in the lens-corrected image space.

For detailed instructions on using the interactive GUI, see [Manual Alignment](#manual-alignment).

## 4. Multi-View Calibration

For multi-view calibration when you already have single-view results (and optionally layout alignment):

### Usage

```bash
# Usage
bash launch_MultiViewCalib.sh -i <input_base_dir> -o <output_base_dir> [-m <mode>] [--no-plot]

# Example with local mode
bash launch_MultiViewCalib.sh -i /path/to/single_view_results -o /path/to/output -m local
```

**Required Arguments:**
- `-i <input_base_dir>`: Base directory containing single view calibration outputs
- `-o <output_base_dir>`: Base directory where multi-view calibration results will be saved

**Optional Arguments:**
- `-m <mode>`: Execution mode: 'local' or 'docker' (default: 'docker')
- `--no-plot`: Disable plotting for bundle adjustment

### Expected Output

Multi-view calibration produces results in the specified output directory with the same structure as `multi_view_results/` from the [End-to-End Pipeline](#1-complete-end-to-end-pipeline). Key outputs include:

- **Pairwise calibration**: `camX_camY/` directories with relative pose estimation results
- **Bundle adjustment**: `BA_output/results_ba/refined/camInfo_XX.yaml` - **final optimized camera parameters**
- **Tracklet matching**: `tracklet_to_global.json` and `sorted_tracklet_matches.json`
- **Visualization**: 3D alignment diagnostics and point cloud visualizations

The most important outputs are the refined camera parameters in `BA_output/results_ba/refined/` which contain the final calibration results after global optimization.

For complete output details, see the [Output Files](#output-files) section.

## 5. Evaluation

For evaluation against ground truth data:

```bash
# Usage
bash launch_Evaluation.sh -i <input_base_dir> -o <output_base_dir> -g <gt_dir> -l <layout_image_path> [-m <mode>] [--no-plot]

# Example
bash launch_Evaluation.sh -i /path/to/single_view_results -o /path/to/output -g /path/to/groundtruth -l /path/to/layout.png -m local
```

**Required Arguments:**
- `-i <input_base_dir>`: Base directory containing single view calibration outputs
- `-o <output_base_dir>`: Base directory containing multi-view calibration results
- `-g <gt_dir>`: Directory containing ground truth data
- `-l <layout_image_path>`: Path to layout image file


## 6. PeopleNet Detection

### 2D Detection
```bash
# Edit conf_2d.txt file first, then:
bash run_2d.sh <output_folder>
# For multiple videos: bash run_2d_batch.sh
```

### 3D Detection
```bash
# Edit conf_3d.txt file first, then:
bash run_3d.sh <output_folder>
# For multiple videos: bash run_3d_batch.sh
```


## 7. Individual Python Scripts

For developers who need fine-grained control over individual pipeline components. All Python scripts require explicit directory arguments instead of relying on absolute paths in config files. This makes the system more portable and secure across different environments.

### Lens Distortion Rectification
```bash
python core/rectification/run_auto_rectification.py -i /path/to/video -c /path/to/rectification_config.yaml -o /path/to/output
```

### Bounding Box Sampling
```bash
python core/camera_matrix_estimation/box_sampling.py -c /path/to/preprocess_config.yaml -i ../data/bbox_test/Pegatron_real/Det-camera_6.log -b /path/to/background.jpg -o /path/to/output
```
**Arguments:**
- `-i`: Input bounding box file

### Camera Parameter Estimation
```bash
python core/camera_matrix_estimation/camera_proj_matrix_estimation.py -c /path/to/camera_matrix_config.yaml -b /output/camera06/Det_bbox_sampling_v2.txt -i /output/camera06/first_frame.jpg -o /output/camera06
```
**Arguments:**
- `-b`: Path to bounding box file
- `-i`: Input image for vanishing point detection 

### Multi-View Calibration: Pairwise Calibration
```bash
python core/camera_estimation/multi_view_calib.py -c /path/to/mv_amc_config.yaml -i /path/to/input_base_dir -o /path/to/output_base_dir
```

### Multi-View Calibration: Visualize Pairwise Results
```bash
python core/camera_estimation/visualize_2_view_results.py -c /path/to/mv_amc_config.yaml -o /path/to/output_base_dir
```

### Multi-View Calibration: Bundle Adjustment
```bash
python core/camera_estimation/bundle_adjustment.py -c /path/to/mv_amc_config.yaml -i /path/to/input_base_dir -o /path/to/output_base_dir
```

### Multi-View Calibration: Manual Layout Alignment
```bash
python core/camera_estimation/layout_alignment.py -c /path/to/mv_amc_config.yaml -o /path/to/output_base_dir -l /path/to/layout.png
```

### Multi-View Calibration: Evaluation
```bash
python core/evaluation/evaluate_multi_cam.py -c /path/to/mv_amc_config.yaml -e /path/to/eval_config.yaml -i /path/to/input_base_dir -o /path/to/output_base_dir -g /path/to/groundtruth_dir -l /path/to/layout.png
```

### Evaluation: Calculate Reprojection Error
```bash
python core/evaluation/evaluate.py --gt-cam-param-path /path/to/groundtruth/camparams --est-cam-param-path /path/to/estimated/camparams --output-dir /path/to/output_base_dir --results-file /path/to/evaluation/output
```

### Evaluation: Cylinder Visualization
```bash
python core/evaluation/visualize_cylinder.py -i /path/to/output_base_dir
```


## (Optional) Ground Truth Focal Length Override

AutoMagicCalib supports using ground truth focal lengths from external sources (lens manufacturers, other calibration tools) while preserving all GeoCalib intelligence for rotation estimation. This is an advanced feature that can improve accuracy when you have known focal length values.

### How It Works

1. **GeoCalib runs normally** - Estimates rotation, uncertainty ranges, and other parameters
2. **Focal length replacement** - Only focal length values are surgically replaced with ground truth
3. **SV calibration proceeds** - Uses ground truth focal length with GeoCalib rotation estimates

### Benefits

- **Improved accuracy**: Use known-accurate focal lengths from lens specifications
- **Preserved intelligence**: Keep all valuable GeoCalib rotation and uncertainty estimates
- **Minimal disruption**: Backward compatible - existing workflows unchanged
- **Geometric consistency**: SV optimization still refines poses around ground truth focal length

### Single Camera Calibration Example
```bash
# Use ground truth focal length for single camera
bash launch_AutoMagicCalib.sh -i /path/to/input/video -o /path/to/output -f 1200.0
```

### Multi Camera End-to-End pipeline Example
```bash
# Use different focal lengths for 4 cameras (in camera directory order)
bash launch_EndToEndCalib.sh -v /path/to/input/videos -o /path/to/output -f "1269.01, 1099.50, 1099.50, 1099.50"
```

## Configuration Files

### Main Configuration Files

**rectification_config.yaml**: Configuration for lens distortion rectification
```yaml
general:
  skip: True # skip rectification if True

distortion:
  # Distortion model to use (must match GeoCalib model)
  # Options: simple_radial, radial, simple_divisional
  model: "simple_radial"
  
  # Auto-generate search range from GeoCalib estimate
  # Search range will be: geocalib_value * (1 ± search_range_percentage/100)
  search_range_percentage: 20  # Search ±20% around GeoCalib estimate

preprocess:
  num_frames: 500     # number of frames to get background image 
  frame_step: 30      # number of frames to skip 
  resize_width: 960   # width of resized image
  resize_height: 540  # height of resized image

param_space:
  k1_min: -0.000001   # minimum value of k1
  k1_max: -0.0000001  # maximum value of k1
  k1_step: 100        # number of values for grid search

output:
  crop: False
```

**camera_matrix_config.yaml**: Configuration for camera projection matrix estimation
```yaml
# parameter space: [minimum, maximum]
focal_length: [2, 30]
rotation_x: [-45, 0]
rotation_z: [-15, 15]
translation_y: [100, 1000]
human_radius: [30, 35]

# parameters 
focal_length_init: 933.33  # initial focal length for vanishing point detection in pixel
weight_iou: 0.5
iterations: 10000  
```

**preprocess_config.yaml**: Configuration for bounding box sampling
```yaml
image:
  width: 1920   # image width
  height: 1080  # image height
region_split:
  width: 21     # width of grid
  height: 11    # height of grid
box_sampling:
  confidence: 0.9             # confidence threshold for bounding box
  aspect_ratio_residual: 0.25 # aspect ratio's residual threshold
  width_residual: 50          # width's residual threshold
  height_residual: 50         # height's residual threshold
```

<br><br>
# Development Configuration Details

### Internal Configuration (conf.py)
This file contains parameters for curve fitting found through experiments. These parameters should only be modified by internal developers:

```python
# Filters  
AR_FILTER_LOW_BOUND = (-np.inf, -np.inf, -np.inf, -1.0e-6, -1.0e-6, -1.0e-6)
AR_FILTER_HIGH_BOUND = (np.inf, np.inf, np.inf, 1.0e-6, 1.0e-6, 1.0e-6) 
WIDTH_FILTER_LOW_BOUND = (-np.inf, -np.inf, -np.inf, -1.0e-5, -5.0e-5, -np.inf) 
WIDTH_FILTER_HIGH_BOUND = (np.inf, np.inf, np.inf, 1.0e-5, 5.0e-5, np.inf) 
HEIGHT_FILTER_LOW_BOUND = (-np.inf, -np.inf, -np.inf, -5.0e-5, -5.0e-4, -np.inf) 
HEIGHT_FILTER_HIGH_BOUND = (np.inf, np.inf, np.inf, 0.0, 1.0e-4, np.inf) 
```

### Development Output Structure
```
output/
└── video_file_name/
    ├── distortion.yaml
    ├── input_video_rectified.mp4
    ├── det-bbox.txt              # detection output file 
    ├── sampled-bbox.txt          # sampled detection bboxes
    ├── Det_bbox_sampling_v1.txt  # sampled bboxes (old method)
    ├── Det_bbox_sampling_v2.txt  # sampled bboxes (new method)
    └── camera_projection_matrix
```

## AI Agent Skills & Guides

This repository includes structured documentation and skills for AI coding assistants (Claude Code, Cursor, GitHub Copilot, etc.) to work autonomously with the codebase. These files are also useful for human developers as quick reference guides.

### Documentation Files

**`AGENT.md`** (Root directory)
- Comprehensive guide for all AI coding assistants (generic, not tool-specific)
- Contains: Architecture overview, autonomous workflows, testing patterns, decision trees, quick reference
- **Start here** if using an AI assistant or need a comprehensive overview of the system
- Works with: Claude Code, Cursor, GitHub Copilot, and other AI coding tools

**`CLAUDE.md`** (Root directory)
- Minimal pointer file that redirects to `AGENT.md`
- Exists so Claude Code can discover agent instructions
- Points to `.claude/` directory for executable skills

**`.claude/` Directory**
- Contains structured skills and guides for autonomous agent workflows
- Also useful for developers as step-by-step command references

### `.claude/` Directory Structure

**`.claude/README.md`**
- Skill catalog and usage guide
- How to use skills (for both agents and developers)
- Naming conventions and maintenance guidelines

**`.claude/skills/setup/`** - Infrastructure setup skills
- `launch-release-containers.md` ⭐ **Primary Method** - Launch full stack from release images: microservice + web UI (use by default when asked to "launch auto calibration")
- `launch-dev-container.md` - Launch microservice dev container with live code bind-mounts (use only when explicitly asked for dev setup)

**`.claude/skills/calibration/`** - Calibration workflow skills (CLI/legacy)
- `run-e2e-pipeline.md` - CLI-based end-to-end pipeline (deprecated, use only if explicitly requested)
- `run-single-view.md` - CLI-based single-view only (deprecated)
- `run-multi-view.md` - CLI-based multi-view only (deprecated)

**`.claude/skills/testing/`** - Testing workflow skills
- `run-integration-tests.md` - API/E2E integration tests against microservice
- `run-ui-test.md` ⭐ **NEW** - Playwright-based UI E2E tests covering the full 6-step workflow
- `run-unit-tests.md` - Pure Python unit tests with coverage reporting
- `test-single-dataset.md` - Run calibration on specific test dataset and validate metrics

**`.claude/guides/`** - Reference guides
- `decision-trees.md` - 6 decision trees for method selection (rectification, detector choice, camera pairing, etc.)
- `troubleshooting.md` - Top 10 common issues with symptoms, fixes, and emergency recovery procedures
- `ui-workflow-guide.md` ⭐ **NEW** - Complete 6-step UI workflow reference with detailed component descriptions, selectors, and agent example code

### Skill File Format

Each skill file follows a consistent structure:
- **Purpose**: What problem it solves
- **Prerequisites**: Environment/service requirements
- **Invocation**: Exact commands to run (copy-paste ready)
- **Execution Flow**: What happens during execution
- **Success Criteria**: How to verify it worked
- **Key Output Files**: Important artifacts produced
- **Troubleshooting**: Common issues with solutions
- **Related Skills**: Cross-references to other relevant skills

### Using These Files

**For AI Coding Assistants:**
1. Read `AGENT.md` for architecture overview
2. Explore `.claude/skills/` for executable capabilities
3. Follow skill instructions to execute workflows autonomously
4. Check `.claude/guides/` for decision logic and troubleshooting

<br><br>
# License

## Repository Licenses
This repository contains materials released under different licenses:
- The scripts and code are licensed under the Apache License 2.0.
- The assets are licensed under the Creative Commons Attribution 4.0 International (CC-BY-4.0) license.

## Proprietary Container Notices (AutoMagicCalib)
The scripts in this repository interact with and pull the proprietary AutoMagicCalib Container. The use of this container, and any software, data, or intellectual property contained within it, is governed by a separate set of licenses and third-party notices.

The applicable End User License Agreement (EULA), 3rd-party notice, and reference information for the container can be found in [AutoMagicCalib page in NGC Catalog](https://catalog.ngc.nvidia.com/orgs/nvidia/containers/auto-magic-calib?version=1.0).
