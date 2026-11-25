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

from typing import Any, Optional
import numpy as np
import os
import glob
import cv2
from core.utils.video_capture import open_video_capture
import sys
import subprocess
import torch
import time
import json
import yaml
import argparse
import logging
from huggingface_hub import hf_hub_download
import shutil
from tqdm import tqdm
import matplotlib.pyplot as plt

# Add submodules to Python path
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(current_dir))
submodules_path = os.path.join(project_root, 'submodules', 'vggt')
sys.path.append(submodules_path)
rectification_path = os.path.join(project_root, 'core', 'rectification')
sys.path.append(rectification_path)

from vggt.models.vggt import VGGT
from vggt.utils.load_fn import load_and_preprocess_images
from vggt.utils.pose_enc import pose_encoding_to_extri_intri
from vggt.utils.geometry import unproject_depth_map_to_point_map
from vggt.utils.pose_enc import pose_encoding_to_extri_intri

# Add the current directory to Python path to allow importing local modules
sys.path.append(current_dir)
sys.path.append(project_root)

from core.camera_estimation import config
from core.camera_estimation.layout_alignment_post_process import get_transform_from_cam0_to_layout
from core.utils.path_validation import validate_output_directory, validate_input_directory
from core.VGGT.visualization import visualize_results 

# Set up logging configuration
def setup_logging(logging_level, output_dir='.'):
    # Get the root logger
    logger = logging.getLogger()
    
    # Check if handlers are already configured to avoid duplicates
    if logger.handlers:
        return
    
    # Create a formatter
    formatter = logging.Formatter('%(levelname)s - %(message)s')
    
    # Set up file handler with DEBUG level
    # Create Logs directory if it doesn't exist
    logs_dir = os.path.join(output_dir, 'Logs')
    os.makedirs(logs_dir, exist_ok=True)
    file_handler = logging.FileHandler(os.path.join(logs_dir, f'vggt_{time.strftime("%Y%m%d_%H%M%S")}.log'))
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)  # Set DEBUG level for file
    
    # Set up console handler with INFO level
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    if logging_level == 'DEBUG':
        console_handler.setLevel(logging.DEBUG)  # Set DEBUG level for console
    else:
        console_handler.setLevel(logging.INFO)  # Set INFO level for console
    
    # Set root logger to DEBUG to capture all messages
    logger.setLevel(logging.DEBUG)
    
    # Add both handlers to the logger
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    # Suppress DEBUG messages from other packages
    logging.getLogger('cv2').setLevel(logging.WARNING)
    logging.getLogger('numpy').setLevel(logging.WARNING)
    logging.getLogger('matplotlib').setLevel(logging.WARNING)
    logging.getLogger('PIL').setLevel(logging.WARNING)
    logging.getLogger('tqdm').setLevel(logging.WARNING)
    logging.getLogger('dtaidistance').setLevel(logging.WARNING)
    logging.getLogger('scipy').setLevel(logging.WARNING)
    logging.getLogger('sklearn').setLevel(logging.WARNING)
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('requests').setLevel(logging.WARNING)

def parse_args():
    parser = argparse.ArgumentParser(description='VGGT calibration')
    parser.add_argument('-c', '--config', type=str,
                        help='Path to the config file')
    parser.add_argument('-s', '--sv_output_dir', type=str, required=False,
                        help='Single view calibration output directory')
    parser.add_argument('-i', '--input_video_dir', type=str, required=False,
                        help='Input video directory for VGGT calibration')
    parser.add_argument('-a', '--alignment_dir', type=str, required=True,
                        help='Alignment directory for VGGT calibration')
    parser.add_argument('-o', '--output_dir', type=str, required=True,
                        help='Output directory')
    parser.add_argument('-d', '--verbose', action=argparse.BooleanOptionalAction, 
                        default=False, 
                        help='Verbose mode')
    return parser.parse_args()

class Timer:
    """Simple timer class for tracking execution times."""
    
    def __init__(self):
        self.start_time = time.time()
        self.checkpoints = {}
    
    def checkpoint(self, name):
        """Record a checkpoint with the given name."""
        current_time = time.time()
        self.checkpoints[name] = current_time
        return current_time
    
    def elapsed(self, from_checkpoint=None):
        """Get elapsed time from start or from a specific checkpoint."""
        if from_checkpoint is None:
            return time.time() - self.start_time
        elif from_checkpoint in self.checkpoints:
            return time.time() - self.checkpoints[from_checkpoint]
        else:
            raise ValueError(f"Checkpoint '{from_checkpoint}' not found")
    
    def print_elapsed(self, name, from_checkpoint=None, verbose=True):
        """Print elapsed time with a descriptive message."""
        elapsed = self.elapsed(from_checkpoint)
        if verbose:
            if from_checkpoint is None:
                print(f"⏱️  {name}: {elapsed:.2f}s (total)")
            else:
                print(f"⏱️  {name}: {elapsed:.2f}s (from {from_checkpoint})")
        return elapsed

# Global device configuration
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

def create_vggt_model(downloaded_file_path, verbose=False):
    """
    Create and load a VGGT model from pretrained weights.
    
    Args:
        downloaded_file_path (str): Path to the pretrained model weights file
        verbose (bool): If True, show detailed progress. If False, only show start/end messages.
        
    Returns:
        torch.nn.Module: Loaded VGGT model ready for inference
    """

    if not os.path.exists(downloaded_file_path):
        raise FileNotFoundError(f"Model file not found: {downloaded_file_path}")

    timer = Timer()
    
    # Show start message
    if not verbose:
        print("Loading VGGT model...")
    else:
        print(f"🚀 Loading VGGT model from: {downloaded_file_path}")
        print(f"🔧 Using device: {DEVICE}")
    
    # Initialize the model architecture
    model = VGGT()
    try:
        timer.checkpoint('model_init')
        
        # Load pretrained weights
        if verbose:
            print("📦 Loading pretrained weights...")
        model.load_state_dict(torch.load(downloaded_file_path, map_location='cpu'))
        timer.checkpoint('weights_loaded')
        
        # Move model to device
        if verbose:
            print(f"🔄 Moving model to {DEVICE}...")
        model = model.to(DEVICE)
        timer.checkpoint('model_to_device')
        
        # Print timing information
        if verbose:
            timer.print_elapsed("Model initialization", 'model_init', verbose)
            timer.print_elapsed("Weight loading", 'weights_loaded', verbose)
            timer.print_elapsed("Device transfer", 'model_to_device', verbose)
            timer.print_elapsed("Total model setup", verbose=verbose)
    except Exception as e:
        raise RuntimeError(f"Failed to load model: {e}")

    # Show end message
    logging.info("✅ VGGT model loaded successfully!\n")
    return model


def run_vggt_inference(model, image_folder, verbose=False):
    """
    Run VGGT inference on a folder of images.
    
    Args:
        model: Loaded VGGT model
        image_folder (str): Path to folder containing input images
        verbose (bool): If True, show detailed progress. If False, only show start/end messages.
        
    Returns:
        dict: Predictions dictionary containing depth, poses, intrinsics, etc.
    """
    if not os.path.exists(image_folder):
        raise FileNotFoundError(f"Image folder not found: {image_folder}")
    if not os.path.isdir(image_folder):
        raise ValueError(f"Path is not a directory: {image_folder}")

    timer = Timer()
    if not verbose:
        print("running VGGT model ...")
    else:
        print(f"🔍 Running VGGT inference on images from: {image_folder}")
    
    # Load and sort image files (filter for common image extensions)
    image_extensions = {'.png', '.jpg', '.jpeg', '.bmp', '.tiff', '.tif', '.webp'}
    list_image_name = [f for f in os.listdir(image_folder) 
                       if os.path.splitext(f.lower())[1] in image_extensions]
    image_names = np.sort([os.path.join(image_folder, image_name) for image_name in list_image_name])
    
    if verbose:
        print(f"📸 Found {len(image_names)} images:")
        for i, name in enumerate(image_names):
            print(f"   {i+1}. {os.path.basename(name)}")
    else:
        for i, name in enumerate(image_names):
            print(f"   {os.path.basename(name)}")
    
    # Load and preprocess images
    if verbose:
        print("\n🔄 Loading and preprocessing images...")
    images = load_and_preprocess_images(image_names).to(DEVICE)
    timer.checkpoint('data_loaded')
    if verbose:
        timer.print_elapsed("Image loading and preprocessing", 'data_loaded')

    # Determine optimal dtype based on GPU capability
    try:
        dtype = torch.bfloat16 if torch.cuda.is_available() and torch.cuda.get_device_capability()[0] >= 8 else torch.float16
    except (RuntimeError, AttributeError):
        dtype = torch.float16  # Fallback for older GPUs or CPU-only systems
    if verbose:
        print(f"🧮 Using precision: {dtype}")
    
    # Run inference
    if verbose:
        print("\n🤖 Running VGGT inference...")
    with torch.no_grad():
        with torch.cuda.amp.autocast(dtype=dtype):
            # Predict cameras, depth maps, and point maps
            predictions = model(images)
    
    timer.checkpoint('inference_done')
    if verbose:
        timer.print_elapsed("Model inference", 'inference_done', verbose)

    # Convert pose encoding to camera matrices
    if verbose:
        print("\n📐 Converting pose encoding to camera matrices...")
    extrinsic, intrinsic = pose_encoding_to_extri_intri(predictions["pose_enc"], images.shape[-2:])
    predictions["extrinsic"] = extrinsic
    predictions["intrinsic"] = intrinsic
    timer.checkpoint('pose_converted')
    
    # Convert tensors to numpy for easier handling
    if verbose:
        print("🔄 Converting tensors to numpy arrays...")
    for key in predictions.keys():
        if isinstance(predictions[key], torch.Tensor):
            predictions[key] = predictions[key].cpu().numpy().squeeze(0)  # remove batch dimension
    timer.checkpoint('tensors_converted')
    
    # Generate 3D world points from depth maps
    if verbose:
        print("🌍 Computing 3D world points from depth maps...")
    depth_map = predictions["depth"]  # Shape: (S, H, W, 1)
    world_points = unproject_depth_map_to_point_map(
        depth_map, predictions["extrinsic"], predictions["intrinsic"]
    )
    predictions["world_points_from_depth"] = world_points
    timer.checkpoint('world_points_computed')
    
    # Clean up GPU memory
    torch.cuda.empty_cache()
    
    # Print processing timing
    if verbose:
        timer.print_elapsed("Pose conversion", 'pose_converted', verbose)
        timer.print_elapsed("Tensor conversion", 'tensors_converted', verbose)
        timer.print_elapsed("World points computation", 'world_points_computed', verbose)
        timer.print_elapsed("Total post-processing", 'inference_done', verbose)
    
    # Print final timing summary
    if verbose:
        print("\n" + "="*50)
        print("⏱️  TIMING SUMMARY")
        print("="*50)
        timer.print_elapsed("TOTAL INFERENCE TIME", verbose=verbose)
        print("="*50)
        logging.info("✅ VGGT inference completed successfully!\n")
    else:
        logging.info("✅ VGGT inference completed successfully!\n")
    
    return predictions

def run_vggt_inference_from_image_paths(model, image_paths, verbose=False):
    """
    Run VGGT inference on a list of images.
    
    Args:
        model: Loaded VGGT model
        image_paths (list): List of input image paths
        verbose (bool): If True, show detailed progress. If False, only show start/end messages.
        
    Returns:
        dict: Predictions dictionary containing depth, poses, intrinsics, etc.
    """
    timer = Timer()
    if verbose:
        print(f"🔍 Running VGGT inference on images from: {image_paths}")
    
    # Load and sort image files (filter for common image extensions)
    image_names = image_paths
    
    if verbose:
        print(f"📸 Found {len(image_names)} images:")
        for i, name in enumerate(image_names):
            print(f"   {i+1}. {os.path.basename(name)}")
    
    # Load and preprocess images
    if verbose:
        print("\n🔄 Loading and preprocessing images...")
    images = load_and_preprocess_images(image_names).to(DEVICE)
    timer.checkpoint('data_loaded')
    if verbose:
        timer.print_elapsed("Image loading and preprocessing", 'data_loaded')

    # Determine optimal dtype based on GPU capability
    try:
        dtype = torch.bfloat16 if torch.cuda.is_available() and torch.cuda.get_device_capability()[0] >= 8 else torch.float16
    except (RuntimeError, AttributeError):
        dtype = torch.float16  # Fallback for older GPUs or CPU-only systems
    if verbose:
        print(f"🧮 Using precision: {dtype}")
    
    # Run inference
    if verbose:
        print("\n🤖 Running VGGT inference...")
    with torch.no_grad():
        with torch.cuda.amp.autocast(dtype=dtype):
            # Predict cameras, depth maps, and point maps
            predictions = model(images)
    
    timer.checkpoint('inference_done')
    if verbose:
        timer.print_elapsed("Model inference", 'inference_done', verbose)

    # Convert pose encoding to camera matrices
    if verbose:
        print("\n📐 Converting pose encoding to camera matrices...")
    extrinsic, intrinsic = pose_encoding_to_extri_intri(predictions["pose_enc"], images.shape[-2:])
    predictions["extrinsic"] = extrinsic
    predictions["intrinsic"] = intrinsic
    timer.checkpoint('pose_converted')
    
    # Convert tensors to numpy for easier handling
    if verbose:
        print("🔄 Converting tensors to numpy arrays...")
    for key in predictions.keys():
        if isinstance(predictions[key], torch.Tensor):
            predictions[key] = predictions[key].cpu().numpy().squeeze(0)  # remove batch dimension
    timer.checkpoint('tensors_converted')
    
    # Generate 3D world points from depth maps
    if verbose:
        print("🌍 Computing 3D world points from depth maps...")
    depth_map = predictions["depth"]  # Shape: (S, H, W, 1)
    world_points = unproject_depth_map_to_point_map(
        depth_map, predictions["extrinsic"], predictions["intrinsic"]
    )
    predictions["world_points_from_depth"] = world_points
    timer.checkpoint('world_points_computed')
    
    # Clean up GPU memory
    torch.cuda.empty_cache()
    
    # Print processing timing
    if verbose:
        timer.print_elapsed("Pose conversion", 'pose_converted', verbose)
        timer.print_elapsed("Tensor conversion", 'tensors_converted', verbose)
        timer.print_elapsed("World points computation", 'world_points_computed', verbose)
        timer.print_elapsed("Total post-processing", 'inference_done', verbose)
    
    # Print final timing summary
    if verbose:
        print("\n" + "="*50)
        print("⏱️  TIMING SUMMARY")
        print("="*50)
        timer.print_elapsed("TOTAL INFERENCE TIME", verbose=verbose)
        print("="*50)
        print("✅ VGGT inference completed successfully!\n")
    return predictions

def _count_confident_points(predictions, confidence_score_threshold):
    """ Count the number of confident points in the predictions.
    Args:
        predictions (dict): predictions from VGGT
    Returns:
        number of confident points
    """
    depth_conf = predictions['depth_conf']
    max_conf = np.max(depth_conf)
    min_conf = np.min(depth_conf)
    # This line creates a boolean mask by marking as "confident" all points in the depth_conf array
    # whose confidence value is greater than the threshold:
    # threshold = max_conf - (max_conf - min_conf) * 0.2
    # This effectively retains the top 80% most confident points (by discarding the lowest 20%).
    confident_points = depth_conf > max_conf - (max_conf - min_conf) * confidence_score_threshold
    return np.sum(confident_points)

def run_vggt_auto_select(
    model: torch.nn.Module, 
    frame_indices: list[int], 
    image_paths_all: dict[int, list[str]], 
    temp_dir: str, 
    confidence_score_threshold: float, 
    top_k: int = 1,
    verbose: bool = False
) -> tuple[list[int], list[dict], list[int], list[int]]:
    """
    Run VGGT auto-select on a list of frame indices and image paths.
    Args:
        model: loaded VGGT model
        frame_indices: list of frame indices
        image_paths_all: dictionary of image paths by camera index
        temp_dir: temporary directory to store extracted images
        confidence_score_threshold: threshold for confidence score
        top_k: number of top frames to select
        verbose: verbose
    Returns:
        top_k_indices: top-k frame indices in the input frame_indices list
        top_k_predictions: top-k predictions (same order as top_k_indices)
        num_confident_points_all: number of confident points for each valid frame
        valid_frame_indices: valid frame indices evaluated by the model
    """
    logging.info(f'Auto-selecting frames from videos in {temp_dir}')
    cam_indices = list(image_paths_all.keys())
    # iterate over frames
    num_confident_points_all = []
    predictions_all = []
    valid_input_indices = []
    valid_frame_indices = []
    for i, frame_idx in tqdm(enumerate(frame_indices), total=len(frame_indices), desc="Auto-select VGGT frames"):
        image_paths = [image_paths_all[cam_idx][i] for cam_idx in cam_indices]
        # check if the frame index and the image files are consistent
        if not all(int(os.path.basename(image_path).split('_')[-1].split('.')[0]) == frame_idx for image_path in image_paths):
            logging.warning(f'⚠️ Frame index and image file are not consistent: {frame_idx}, {image_paths}')
            continue
        # check if the image paths are valid
        if not all(os.path.exists(image_path) for image_path in image_paths):
            logging.warning(f'⚠️ Image path is not valid: {image_paths}')
            continue
        
        # run VGGT inference
        predictions = run_vggt_inference_from_image_paths(
            model=model, image_paths=image_paths, verbose=verbose
        )
        # count the number of confident points
        num_confident_points = _count_confident_points(predictions, confidence_score_threshold)

        # store statistics
        num_confident_points_all.append(num_confident_points)
        predictions_all.append(predictions)
        valid_input_indices.append(i)
        valid_frame_indices.append(frame_idx)

    if len(num_confident_points_all) == 0:
        raise RuntimeError("No valid frames found for auto-selection.")

    # find top-k frame indices with the maximum number of confident points
    top_k = max(1, min(top_k, len(num_confident_points_all)))
    ranked_local_indices = np.argsort(num_confident_points_all)[::-1][:top_k]
    top_k_indices = [valid_input_indices[idx] for idx in ranked_local_indices]
    top_k_predictions = [predictions_all[idx] for idx in ranked_local_indices]
    top_k_frame_indices = [valid_frame_indices[idx] for idx in ranked_local_indices]

    logging.info(f'✅ Auto-selection completed successfully!')
    for rank, local_idx in enumerate(ranked_local_indices, start=1):
        logging.info(
            f'✅ Rank-{rank}: frame index {top_k_frame_indices[rank - 1]} '
            f'with {num_confident_points_all[local_idx]} confident points'
        )
    return top_k_indices, top_k_predictions, num_confident_points_all, valid_frame_indices

def post_process(predictions:dict, camera_ids:list, img_w:int, img_h:int) -> dict:
    """
    Post-process the predictions from VGGT.
    Scale the intrinsic matrix and the extrinsic matrix to the original image size.

    Args:
        predictions: predictions from VGGT
        camera_ids: camera ids
        img_w: image width
        img_h: image height
    Returns:
        camera_info: camera information
    """
    intrinsics = predictions['intrinsic'] # shape: (N, 3, 3)
    extrinsics = predictions['extrinsic'] # shape: (N, 3, 4)

    def _scale_intrinsic(intrinsic:np.array) -> np.array:
        """
        Scale the intrinsic matrix.
        Principal points in the intrinsic matrix are scaled down because the image is resized.
        The intrinsic matrix is scaled up to restore the original image size.
        """
        scale_factor = (img_w / 2.0 / intrinsic[0,2] + 
                        img_h / 2.0 / intrinsic[1,2]) / 2.0  # assuming square pixels and cx at intrinsic[0,2]
        intrinsic_scaled = intrinsic * scale_factor
        intrinsic_scaled[2,2] = 1.0  # keep this as 1.0
        return intrinsic_scaled
    
    camera_info = {}
    for i, cam_id in enumerate(camera_ids):
        K = intrinsics[i]
        K_scaled = _scale_intrinsic(K)
        R = extrinsics[i][:3, :3]
        t = extrinsics[i][:3, 3].reshape(3, 1)
        C = (-R.T @ t).reshape(3, 1)
        proj_mat = np.dot(K_scaled, np.concatenate((R, t), axis=1))
        camera_info[cam_id] = {
            'K': K_scaled,
            'R': R,
            't': t,
            'C': C,
            "projectionMatrix_3x4": proj_mat
        }
    return camera_info

def save_results(camera_info:dict, output_folder:str) -> None:
    """
    Save the calibration results to the output folder.
    Args:
        camera_info: camera information
        output_folder: output folder
    Returns:
        None
    """
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)
        logging.info(f"📁 Output folder created: {output_folder}")

    # save calibration results (camInfo_XX.yaml)
    for cam_id in camera_info.keys():
        cam_param = {'K': camera_info[cam_id]['K'].tolist(), 
                       'R': camera_info[cam_id]['R'].tolist(), 
                       't': camera_info[cam_id]['t'].tolist(), 
                       'C': camera_info[cam_id]['C'].tolist(), 
                       'projectionMatrix_3x4': camera_info[cam_id]['projectionMatrix_3x4'].tolist()}
        with open(os.path.join(output_folder, f'camInfo_{cam_id:02d}.yaml'), 'w') as f:
            yaml.dump(cam_param, f)
    logging.info(f"💾 Calibration results saved to: {output_folder}")

def prepare_capture_objects(video_paths:list) -> tuple:
    """
    Prepare capture objects for each video.

    Args:
        video_dir: video directory
    Returns:
        cap_list: list of capture objects
        num_frames: number of frames
    """
    # prepare capture objects for each video
    logging.info(f'Preparing capture objects for each video in {video_paths}')
    cap_list = []
    min_frame_count = float('inf')
    for i, video_path in enumerate(video_paths):
        cap = open_video_capture(video_path)
        if not cap.isOpened():
            raise ValueError(f'Failed to open video file: {video_path}')
        cap_list.append(cap)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        print(f"Video {i} has {frame_count} frames")
        if frame_count < min_frame_count:
            min_frame_count = frame_count
    return cap_list, min_frame_count
 
def release_capture_objects(cap_list:list) -> None:
    """
    Release capture objects.
    Args:
        cap_list: list of capture objects
    Returns:
        None
    """
    for cap in cap_list:
        if cap.isOpened():
            cap.release()
    logging.info("Capture objects are released")

def _get_video_frame_info(video_paths: list) -> tuple[int, float]:
    """
    Get minimum frame count across videos and FPS from the first video.
    Opens each video briefly to read metadata, then closes.

    Args:
        video_paths: list of video file paths
    Returns:
        (min_frame_count, fps)
    """
    min_frame_count = float('inf')
    fps = 30.0  # default fallback
    for i, video_path in enumerate(video_paths):
        cap = open_video_capture(video_path)
        if not cap.isOpened():
            raise ValueError(f'Failed to open video file: {video_path}')
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if i == 0:
            fps = cap.get(cv2.CAP_PROP_FPS)
            if fps <= 0 or fps != fps:  # NaN check
                fps = 30.0
        cap.release()
        if frame_count < min_frame_count:
            min_frame_count = frame_count
    return int(min_frame_count), float(fps)


def validate_vggt_config(
    vggt_config: dict,
    project_root: str,
    num_frames: Optional[int] = None,
    fps: Optional[float] = None,
) -> None:
    """
    Validate VGGT configuration parameters. On failure, logs error messages and exits the program.

    Args:
        vggt_config: VGGT config dict (e.g. from _get_vggt_config)
        project_root: project root for resolving model_path
        num_frames: optional max number of frames in input videos (for timestamp_sec / max_num_frames)
        fps: optional FPS from input videos (for timestamp_sec upper bound in seconds)
    """
    errors = []

    # model_path: verify the file exists
    model_path = vggt_config.get('model_path')
    if model_path is None or (isinstance(model_path, str) and not model_path.strip()):
        errors.append('vggt.model_path is missing or empty in config')
    else:
        full_path = os.path.join(project_root, model_path) if not os.path.isabs(model_path) else model_path
        if not os.path.isfile(full_path):
            errors.append(f'vggt.model_path: file does not exist: {full_path}')

    # auto_select: true or false
    auto_select = vggt_config.get('auto_select')
    if not isinstance(auto_select, bool):
        errors.append(
            f'vggt.auto_select must be true or false, got {type(auto_select).__name__}: {auto_select}'
        )

    # sample_interval_sec: 1~10
    sample_interval_sec = vggt_config.get('sample_interval_sec')
    try:
        s = int(sample_interval_sec) if sample_interval_sec is not None else None
    except (TypeError, ValueError):
        s = None
    if s is None or not (1 <= s <= 10):
        errors.append(
            f'vggt.sample_interval_sec must be an integer in [1, 10], got: {sample_interval_sec}'
        )

    # top_k: 1~5
    top_k = vggt_config.get('top_k')
    try:
        k = int(top_k) if top_k is not None else None
    except (TypeError, ValueError):
        k = None
    if k is None or not (1 <= k <= 5):
        errors.append(f'vggt.top_k must be an integer in [1, 5], got: {top_k}')

    # confidence_score_threshold: 0~1.0
    conf = vggt_config.get('confidence_score_threshold')
    try:
        c = float(conf) if conf is not None else None
    except (TypeError, ValueError):
        c = None
    if c is None or not (0 <= c <= 1.0):
        errors.append(
            f'vggt.confidence_score_threshold must be a number in [0, 1.0], got: {conf}'
        )

    # timestamp_sec: 0 ~ max timestamp in seconds (when num_frames and fps available)
    timestamp_sec = vggt_config.get('timestamp_sec')
    try:
        ts = float(timestamp_sec) if timestamp_sec is not None else None
    except (TypeError, ValueError):
        ts = None
    if ts is not None and ts < 0:
        errors.append(f'vggt.timestamp_sec must be >= 0, got: {timestamp_sec}')
    if num_frames is not None and fps is not None and fps > 0 and ts is not None:
        max_sec = (num_frames - 1) / fps
        if ts > max_sec:
            errors.append(
                f'vggt.timestamp_sec must be in [0, {max_sec:.2f}] (0 to max duration of input videos), got: {timestamp_sec}'
            )

    # max_num_frames: >= 1
    max_num_frames = vggt_config.get('max_num_frames')
    try:
        m = int(max_num_frames) if max_num_frames is not None else None
    except (TypeError, ValueError):
        m = None
    if m is not None and m < 1:
        errors.append(f'vggt.max_num_frames must be >= 1, got: {max_num_frames}')

    if errors:
        for msg in errors:
            logging.error(msg)
        logging.error('VGGT configuration validation failed. Exiting.')
        sys.exit(1)


def _get_vggt_config(app_config: dict) -> dict:
    """
    Extract and validate VGGT config from application config.

    Args:
        app_config: application config
    Returns:
        VGGT configuration
    """
    if 'vggt' not in app_config:
        raise ValueError('VGGT configuration not found in config file')
    vggt_config = app_config['vggt']
    model_path = vggt_config.get('model_path')
    if model_path is None:
        raise ValueError('Model path not found in config file')
    return {
        'model_path': model_path,
        'auto_select': vggt_config.get('auto_select', False),
        'timestamp_sec': vggt_config.get('timestamp_sec', 0),
        'sample_interval_sec': vggt_config.get('sample_interval_sec', 1),
        'max_num_frames': vggt_config.get('max_num_frames', 10000),
        'top_k': vggt_config.get('top_k', 1),
        'confidence_score_threshold': vggt_config.get('confidence_score_threshold', 0.2),
        'temp_dir': vggt_config.get('temp_dir', 'temp'),
        'camInfo_dir': vggt_config.get('camInfo_dir', 'BA_output/results_ba/refined'),
    }


def _validate_directories(alignment_dir: str, output_dir: str, temp_dir: str) -> None:
    """Validate and sanitize directory paths; create/clear temp dir if needed."""
    if alignment_dir:
        validate_input_directory(alignment_dir, 'alignment directory')
    if output_dir:
        validate_output_directory(output_dir, 'output directory')
        if os.path.exists(temp_dir):
            logging.info(f'Temp directory already exists: {temp_dir}. Removing it...')
            shutil.rmtree(temp_dir)
        validate_output_directory(temp_dir, 'temp directory')


def _run_inference_and_select(
    model,
    frame_indices,
    image_paths,
    temp_dir: str,
    auto_select: bool,
    confidence_score_threshold: float,
    top_k: int,
    verbose: bool,
) -> tuple:
    """
    Run VGGT auto-select (or single-frame) and return best predictions and metadata.

    Args:
        model: loaded VGGT model
        frame_indices: list of frame indices
        image_paths: dictionary of image paths by camera index
        temp_dir: temporary directory to store extracted images
        auto_select: whether to auto-select frames
        confidence_score_threshold: threshold for confidence score
        top_k: number of top frames to select
        verbose: verbose
    Returns:
        predictions: best prediction from VGGT
        top_k_indices: top-k frame indices in the input frame_indices list
        top_k_predictions: top-k predictions (same order as top_k_indices)
        num_confident_points_all: number of confident points for each valid frame
        valid_frame_indices: valid frame indices evaluated by the model
    """
    if auto_select:
        logging.info(f'❗ Auto-selecting frames from videos in {temp_dir}')
        top_k_indices, top_k_predictions, num_confident_points_all, valid_frame_indices = (
            run_vggt_auto_select(
                model, frame_indices, image_paths, temp_dir,
                confidence_score_threshold, top_k=top_k, verbose=verbose
            )
        )
        best_idx = top_k_indices[0]
        predictions = top_k_predictions[0]
        logging.info("✅ Auto-selection completed successfully!")
        logging.info(f'✅ Best frame index: {frame_indices[best_idx]}')
    else:
        logging.info(
            f'❗ Using a specific frame index to extract images from videos in {temp_dir} '
            f'at frame index: {frame_indices[0]}'
        )
        _, top_k_predictions, num_confident_points_all, valid_frame_indices = (
            run_vggt_auto_select(
                model, frame_indices, image_paths, temp_dir,
                confidence_score_threshold, top_k=1, verbose=verbose
            )
        )
        top_k_indices = [0]
        predictions = top_k_predictions[0]
    return predictions, top_k_indices, top_k_predictions, num_confident_points_all, valid_frame_indices


def _save_auto_select_artifacts(
    output_dir: str,
    frame_indices,
    image_paths: dict,
    top_k_indices: list,
    valid_frame_indices: list,
    num_confident_points_all: list,
    temp_dir: str,
) -> None:
    """
    Save auto-select artifacts.
    Copy best frame, top-k frames, write ranking summary, plot confidence, and remove temp dir.

    Args:
        output_dir: output directory
        frame_indices: list of frame indices
        image_paths: dictionary of image paths by camera index
        top_k_indices: top-k frame indices in the input frame_indices list
        valid_frame_indices: valid frame indices evaluated by the model
        num_confident_points_all: number of confident points for each valid frame
        temp_dir: temporary directory to store extracted images
    Returns:
        None
    """
    auto_select_dir = os.path.join(output_dir, 'auto_selection')
    best_frame_dir = os.path.join(auto_select_dir, 'best_frame')
    os.makedirs(best_frame_dir, exist_ok=True)
    best_idx = top_k_indices[0]
    for cam_idx in image_paths.keys():
        shutil.copy(
            image_paths[cam_idx][best_idx],
            os.path.join(best_frame_dir, f'cam_{cam_idx:02d}_{frame_indices[best_idx]:06d}.jpg')
        )
    logging.info(f'✅ Best frame copied to {best_frame_dir}')

    top_k_frames_dir = os.path.join(auto_select_dir, 'top_k_results')
    os.makedirs(top_k_frames_dir, exist_ok=True)
    frame_to_conf_points = dict(zip(valid_frame_indices, num_confident_points_all))
    ranking_summary_path = os.path.join(top_k_frames_dir, 'top_k_ranking.txt')
    ranking_lines = ['rank,frame_index,num_confident_points']
    for rank, ranked_idx in enumerate(top_k_indices, start=1):
        frame_idx = frame_indices[ranked_idx]
        rank_dir = os.path.join(top_k_frames_dir, f'rank_{rank:02d}', 'frames')
        os.makedirs(rank_dir, exist_ok=True)
        for cam_idx in image_paths.keys():
            shutil.copy(
                image_paths[cam_idx][ranked_idx],
                os.path.join(rank_dir, f'cam_{cam_idx:02d}_{frame_idx:06d}.jpg')
            )
        num_confident_points = frame_to_conf_points.get(frame_idx, -1)
        ranking_lines.append(f'{rank},{frame_idx},{num_confident_points}')
    with open(ranking_summary_path, 'w') as f:
        f.write('\n'.join(ranking_lines) + '\n')
    logging.info(f'✅ Top-k ranking summary saved to {ranking_summary_path}')
    logging.info(f'✅ Top-{len(top_k_indices)} frames copied to {top_k_frames_dir}')

    plt.figure(figsize=(10, 5))
    plt.plot(valid_frame_indices, num_confident_points_all)
    plt.xlabel('Frame index')
    plt.ylabel('Number of confident points')
    plt.title('Number of confident points for each valid frame')
    plt.savefig(os.path.join(auto_select_dir, 'num_confident_points.png'))
    plt.close()
    shutil.rmtree(temp_dir)
    logging.info(f'✅ Temporary directory cleaned up: {temp_dir}')


def _load_alignment_data(alignment_dir: str) -> Optional[dict]:
    """Load alignment_data.json from alignment_dir if present."""
    path = os.path.join(alignment_dir, 'alignment_data.json')
    if not os.path.exists(path):
        logging.info(f'Alignment data not found in {alignment_dir}')
        logging.info('Skipping point cloud visualization on layout image...')
        return None
    with open(path, 'r') as f:
        return json.load(f)


def post_process_and_visualize(
    predictions: dict,
    camera_ids: list,
    img_w: int,
    img_h: int,
    output_dir: str,
    camInfo_dir: str,
    alignment_data: Optional[dict],
    alignment_dir: str,
) -> dict:
    """
    Post-process VGGT predictions, save camera results, and optionally visualize points on BEV map.
    Returns camera_info dict.

    Args:
        predictions: predictions from VGGT
        camera_ids: camera ids
        img_w: image width
        img_h: image height
        output_dir: output directory
        camInfo_dir: camera information directory
        alignment_data: alignment data
        alignment_dir: alignment directory
    Returns:
        camera_info: camera information
    """
    camera_info = post_process(predictions, camera_ids, img_w, img_h)
    save_results(camera_info, os.path.join(output_dir, camInfo_dir))
    logging.info('Visualizing point cloud on layout image...')
    if alignment_data is not None:
        cid0, cid1 = camera_ids[0], camera_ids[1]
        proj_mat0 = camera_info[cid0]['projectionMatrix_3x4']
        proj_mat1 = camera_info[cid1]['projectionMatrix_3x4']
        s, R, t = get_transform_from_cam0_to_layout(alignment_data, proj_mat0, proj_mat1)
        T_c0_w = np.concatenate(
            (np.concatenate((s * R, t.reshape(-1, 1)), axis=1), np.array([[0, 0, 0, 1]])), axis=0
        )
        try:
            map_img = cv2.imread(os.path.join(alignment_dir, 'layout.png'))
            visualize_results(camera_info, predictions, T_c0_w, map_img, output_dir)
        except Exception as e:
            logging.error(f'Failed to read layout image: {e}')
            logging.error('Skipping point cloud visualization on layout image...')
    return camera_info


def prepare_image_frames(video_paths:list, auto_select:bool, timestamp_sec:int, 
                         sample_interval_sec:int, max_num_frames:int, temp_dir:str) -> tuple:
    """
    Prepare image frames from videos for VGGT inference.

    Args:
        video_paths: list of video paths
        auto_select: auto select
        timestamp_sec: timestamp in seconds
        sample_interval_sec: sample interval in seconds
        max_num_frames: maximum number of frames
        temp_dir: temporary directory for extracted images
    Returns:
        frame_indices: list of frame indices
        image_paths: dictionary of image paths by camera index
    """
    # video capture objects
    cap_list, num_frames = prepare_capture_objects(video_paths)
    fps = cap_list[0].get(cv2.CAP_PROP_FPS)
   
    if auto_select:
        # frame indices for auto-selection 
        logging.info(f'🚀 Auto-selecting frames from videos in {temp_dir}')
        frame_indices = []
        if max_num_frames == 0:
            max_num_frames = num_frames
        frame_indices = np.arange(0, num_frames, int(sample_interval_sec * fps), dtype=int)[:max_num_frames]
        logging.info(f'Auto-selected {len(frame_indices)} frames from videos')
    else: 
        # a specific frame index 
        frame_indices = [int(timestamp_sec * fps)]
        if frame_indices[0] > num_frames:
            raise ValueError(f'Frame index is out of range: {frame_indices[0]} > {num_frames}')
        logging.info(f'timestamp_sec: {timestamp_sec} sec, frame index: {frame_indices[0]}')
    
    # extract frames from each video and save them into the temporary folder
    image_paths = {}
    for cam_idx, cap in enumerate(cap_list):
        if not os.path.exists(os.path.join(temp_dir, f'cam_{cam_idx:02d}')):
            os.makedirs(os.path.join(temp_dir, f'cam_{cam_idx:02d}'))
        print(f"📷 Sampling {len(frame_indices)} frames from video {video_paths[cam_idx]}")
        image_paths[cam_idx] = []
        for frame_idx in tqdm(frame_indices):
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            success, frame = cap.read()
            if success:
                img_path = os.path.join(temp_dir, f'cam_{cam_idx:02d}/frame_{frame_idx:06d}.jpg')
                cv2.imwrite(img_path, frame)
                image_paths[cam_idx].append(img_path)
            else:
                print(f"Error reading frame {frame_idx} from video {video_paths[cam_idx]}")
                break
    # release capture objects
    release_capture_objects(cap_list)
    return frame_indices, image_paths

def main(config: dict, video_paths: list, alignment_dir: str, output_dir: str, verbose: bool = False) -> None:
    """
    Main function to run VGGT calibration.

    Args:
        config: configuration
        video_paths: list of video file paths
        alignment_dir: alignment directory
        output_dir: output directory
        verbose: verbose
    """
    logging.info('========================================================')
    logging.info("🚀 Launching model-based calibration...")
    logging.info('========================================================')

    cam_dirs = config['cam_dir']
    camera_ids = [int(cam_dir.split('_')[-1]) for cam_dir in cam_dirs]
    img_w, img_h = config['video_resolution']

    vggt_cfg = _get_vggt_config(config)
    num_frames, fps = _get_video_frame_info(video_paths)
    validate_vggt_config(vggt_cfg, project_root, num_frames, fps)

    model_path = vggt_cfg['model_path']
    auto_select = vggt_cfg['auto_select']
    timestamp_sec = vggt_cfg['timestamp_sec']
    if timestamp_sec is None:
        logging.info('Auto-select frame index based on reprojection error')
    else:
        logging.info(f'Calibrating using frames at time: {timestamp_sec} sec')

    temp_dir = os.path.join(output_dir, vggt_cfg['temp_dir'])
    _validate_directories(alignment_dir, output_dir, temp_dir)

    frame_indices, image_paths = prepare_image_frames(
        video_paths,
        auto_select,
        timestamp_sec,
        vggt_cfg['sample_interval_sec'],
        vggt_cfg['max_num_frames'],
        temp_dir,
    )
    # create VGGT model
    model = create_vggt_model(os.path.join(project_root, model_path), verbose=verbose)
    # run inference and select top-k frames
    predictions, top_k_indices, top_k_predictions, num_confident_points_all, valid_frame_indices = (
        _run_inference_and_select(
            model,
            frame_indices,
            image_paths,
            temp_dir,
            auto_select,
            vggt_cfg['confidence_score_threshold'],
            vggt_cfg['top_k'],
            verbose,
        )
    )
    # save auto-select artifacts
    if auto_select:
        _save_auto_select_artifacts(
            output_dir,
            frame_indices,
            image_paths,
            top_k_indices,
            valid_frame_indices,
            num_confident_points_all,
            temp_dir,
        )

    # save camera information under BA_output folder and visualize the best prediction on BEV map
    # this is for backward compatibility with the old code. 
    alignment_data = _load_alignment_data(alignment_dir)
    camera_info = post_process_and_visualize(
        predictions,
        camera_ids,
        img_w,
        img_h,
        output_dir,
        vggt_cfg['camInfo_dir'],
        alignment_data,
        alignment_dir,
    )
    # save camera information and visualize the top-k predictions on BEV map
    if auto_select:
        top_k_results_dir = os.path.join(output_dir, 'auto_selection', 'top_k_results')
        os.makedirs(top_k_results_dir, exist_ok=True)
        for rank, (ranked_idx, ranked_prediction) in enumerate(zip(top_k_indices, top_k_predictions), start=1):
            frame_idx = frame_indices[ranked_idx]
            rank_output_dir = os.path.join(top_k_results_dir, f'rank_{rank:02d}')
            os.makedirs(rank_output_dir, exist_ok=True)
            logging.info(f'Processing calibration outputs for rank-{rank}, frame {frame_idx}')
            post_process_and_visualize(
                ranked_prediction,
                camera_ids,
                img_w,
                img_h,
                rank_output_dir,
                vggt_cfg['camInfo_dir'],
                alignment_data,
                alignment_dir,
            )

def _get_video_paths(args, cfg: dict) -> list:
    """
    Resolve video file paths from args and config. Raises on invalid input.

    Args:
        args: arguments
        cfg: configuration
    Returns:
        video_paths: list of video file paths
    """
    cam_dirs = cfg['cam_dir']
    if args.sv_output_dir is not None:
        video_paths = []
        for cam_dir in cam_dirs:
            video_path = os.path.join(args.sv_output_dir, cam_dir, 'rectified.mp4')
            if not os.path.exists(video_path):
                raise ValueError(f'Video file not found: {video_path}')
            video_paths.append(video_path)
        if len(video_paths) != len(cam_dirs):
            raise ValueError('Number of video paths and camera directories do not match')
        return video_paths
    mp4_files = glob.glob(os.path.join(args.input_video_dir, '*.mp4'))
    mp4_files.sort()
    if len(mp4_files) == 0:
        raise ValueError('No MP4 files found in the input video directory')
    if len(mp4_files) != len(cam_dirs):
        raise ValueError('Number of MP4 files and camera directories do not match')
    return mp4_files


if __name__ == "__main__":
    args = parse_args()

    if args.input_video_dir is None and args.sv_output_dir is None:
        logging.error('Input video directory or single view calibration output directory is required')
        exit(1)

    config.load_config(args.config)
    cfg = config.get_config()
    setup_logging(cfg['logging_level'], args.output_dir)

    video_paths = _get_video_paths(args, cfg)
    print(f'Video paths: {video_paths}')

    main(cfg, video_paths, args.alignment_dir, args.output_dir, args.verbose)


