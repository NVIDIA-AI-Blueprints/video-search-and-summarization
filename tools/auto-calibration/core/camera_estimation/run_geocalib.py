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

import os
import torch 
import yaml
import argparse
import logging
import cv2
from core.utils.video_capture import open_video_capture
import sys
import numpy as np
import json
from pathlib import Path

# Add submodules to Python path
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(current_dir))
submodules_path = os.path.join(project_root, 'submodules', 'GeoCalib')
sys.path.append(submodules_path)
rectification_path = os.path.join(project_root, 'core', 'rectification')
sys.path.append(rectification_path)

from geocalib import GeoCalib
from geocalib.utils import print_calibration, rad2deg
from run_auto_rectification import extract_background 

def get_local_model_path(model_name):
    """Get the path to a local GeoCalib model.
    
    Args:
        model_name (str): Name of the model ('pinhole' or 'distorted')
        
    Returns:
        str: Path to the local model file
        
    Raises:
        FileNotFoundError: If the model file doesn't exist
    """
    model_path = os.path.join(project_root, 'models', 'geocalib', f'geocalib-{model_name}.tar')
    if not os.path.exists(model_path):
        raise FileNotFoundError(f"Model file not found: {model_path}")
    return model_path

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("-c", "--config", type=str, 
                        default=None)
    parser.add_argument("-i", "--img_path", type=str, 
                        default=None)
    parser.add_argument("-v", "--video_path", type=str, default=None)
    parser.add_argument("-o", "--out_path", type=str, default="output")
    parser.add_argument("--rectification_config", type=str, default=None,
                        help="Path to rectification config YAML to read camera model from (optional)")
    return parser.parse_args()

def determine_camera_model(args):
    """Determine which camera/distortion model to use.
    
    Args:
        args: Command line arguments
    
    Returns:
        str: Camera model name (pinhole, simple_radial, radial, or simple_divisional)
    """
    
    # Read from rectification config YAML
    try:
        with open(args.rectification_config, 'r') as f:
            rect_config = yaml.safe_load(f)
        
        if 'distortion' in rect_config and 'model' in rect_config['distortion']:
            camera_model = rect_config['distortion']['model']
            valid_models = ["pinhole", "simple_radial", "radial", "simple_divisional"]

            if camera_model not in valid_models:
                logging.warning(f"Invalid camera model '{camera_model}' in rectification config. "
                                f"Using default 'pinhole'. Valid models: {valid_models}")
                return "pinhole"
            else:
                logging.info(f"Using camera model from {args.rectification_config}: {camera_model}")
                return camera_model

    except Exception as e:
        logging.warning(f"Could not read camera model from rectification config: {e}")
        return None

def remove_outliers_iqr(values, multiplier=1.5):
    """Remove outliers using the Interquartile Range (IQR) method.
    
    Args:
        values (list): List of numeric values
        multiplier (float): IQR multiplier for outlier detection (default 1.5)
        
    Returns:
        tuple: (cleaned_values, outlier_info)
    """
    if len(values) < 4:  # Need at least 4 values for IQR
        return values, {'outliers_removed': 0, 'method': 'insufficient_data'}
    
    values_array = np.array(values)
    q25 = np.percentile(values_array, 25)
    q75 = np.percentile(values_array, 75)
    iqr = q75 - q25
    
    lower_bound = q25 - multiplier * iqr
    upper_bound = q75 + multiplier * iqr
    
    # Filter out outliers
    mask = (values_array >= lower_bound) & (values_array <= upper_bound)
    cleaned_values = values_array[mask].tolist()
    
    outlier_info = {
        'original_count': len(values),
        'cleaned_count': len(cleaned_values),
        'outliers_removed': len(values) - len(cleaned_values),
        'method': f'IQR (multiplier={multiplier})',
        'bounds': [lower_bound, upper_bound],
        'q25': q25,
        'q75': q75,
        'iqr': iqr
    }
    
    return cleaned_values, outlier_info

def save_config_params(result, out_path, config, iteration_stats=None):
    """Save the calibration results in the format of AutoMagicCalib.
    
    Args:
        result: GeoCalib result dictionary
        out_path: Output file path
        config: Configuration dictionary
        iteration_stats: Optional dictionary with iteration statistics for adaptive range setting
                        Expected keys: 'focal_lengths', 'cv', 'representative_focal'
    
    # parameter space: [minimum, maximum]
    focal_length: [2, 30] 
    rotation_x: [-45, 0] # degree 
    rotation_z: [-10, 10] # degree 
    translation_y: [0.01, 100] # meter  
    human_radius: [0.30, 0.35] # meter
    iterations: 5000  

    # vanishing point parameters 
    focal_length_init: 933.33 # initial focal length for vanishing point detection in pixel
    weight_iou: 0.5
    """

    focal_length_search_range = config["focal_length_search_range"]
    weight_iou = config["weight_iou"]
    iterations = config["iterations"]
    config_params = {'focal_length': [],
                     'rotation_x': [],
                     'rotation_z': [],
                     'translation_y': [0.01, 100],
                     'human_radius': [0.3, 0.35],
                     'iterations': iterations,
                     'focal_length_init': 0,
                     'weight_iou': weight_iou}

    camera, gravity = result["camera"], result["gravity"]
    vfov = rad2deg(camera.vfov)
    roll, pitch = rad2deg(gravity.rp).unbind(-1)

    logging.info("\nEstimated parameters (Pred):")
    logging.info(f"Roll:  {roll.item():.1f}° (± {rad2deg(result['roll_uncertainty']).item():.1f})°")
    logging.info(f"Pitch: {pitch.item():.1f}° (± {rad2deg(result['pitch_uncertainty']).item():.1f})°")
    logging.info(f"vFoV:  {vfov.item():.1f}° (± {rad2deg(result['vfov_uncertainty']).item():.1f})°")
    logging.info(f"Focal: {camera.f[0, 1].item():.1f} px (± {result['focal_uncertainty'].item():.1f} px)")


    # Log and save distortion parameters if available (distorted model)
    has_distortion = hasattr(camera, 'k1')
    if has_distortion:
        # GeoCalib's k1 in its internal representation (normalized coords with f = w/2)
        k1_geocalib = camera.k1[0].item()
        logging.info(f"Dist (GeoCalib): k1 = {k1_geocalib:.8e}")
        
        # Convert to AMC/OpenCV convention (f=1, pixel-centered coords)
        # AMC uses: radial = 1 + k1 * r_pixel²
        # GeoCalib uses: radial = 1 + k1 * (r_pixel/f)²
        # Therefore: k1_AMC = k1_GeoCalib / f²
        f = camera.f[0, 1].item()  # Focal length used by GeoCalib
        k1_amc = k1_geocalib / (f ** 2)
        logging.info(f"Dist (AMC):      k1 = {k1_amc:.8e}  (converted for f=1 convention)")
        
        # Get model name for proper distortion model specification
        model_name = camera.name() if hasattr(camera, 'name') else "unknown"
        
        # Save distortion to separate YAML file for use by Auto Rectification
        # IMPORTANT: Save BOTH versions for clarity
        distortion_data = {
            "model": model_name,  # simple_radial, radial, or simple_divisional
            "k1": float(k1_amc),  # AMC/OpenCV convention (f=1)
            "k1_geocalib": float(k1_geocalib),  # GeoCalib internal (f=w/2)
            "focal_length": float(f),  # Focal length used for conversion
            "source": "geocalib",
        }
        
        # Add k2 if radial model
        if model_name == "radial" and hasattr(camera, 'k2'):
            k2_geocalib = camera.k2[0].item()
            k2_amc = k2_geocalib / (f ** 4)  # k2 scales with r⁴, so divide by f⁴
            distortion_data["k2"] = float(k2_amc)
            distortion_data["k2_geocalib"] = float(k2_geocalib)
            logging.info(f"       (GeoCalib): k2 = {k2_geocalib:.8e}")
            logging.info(f"       (AMC):      k2 = {k2_amc:.8e}  (converted for f=1 convention)")
        
        # Extract directory from out_path (which is a file path)
        out_dir = os.path.dirname(out_path)
        distortion_file = os.path.join(out_dir, "geocalib_distortion.yaml")
        with open(distortion_file, "w") as f:
            yaml.dump(distortion_data, f, sort_keys=False)
        logging.info(f"Saved GeoCalib distortion parameters to {distortion_file}")
        logging.info(f"  Model: {model_name}")

    # focal length 
    f = camera.f[0, 1].item()
    
    # Calculate intelligent focal length search range based on iteration statistics
    if iteration_stats is not None and 'focal_lengths' in iteration_stats:
        focal_lengths = iteration_stats['focal_lengths']
        cv = iteration_stats.get('cv', 0)
        
        # Remove outliers before calculating search range statistics
        cleaned_focal_lengths, outlier_info = remove_outliers_iqr(focal_lengths)
        
        # Calculate statistics from outlier-cleaned data
        focal_std = np.std(cleaned_focal_lengths)
        focal_mean = np.mean(cleaned_focal_lengths)
        focal_range = max(cleaned_focal_lengths) - min(cleaned_focal_lengths)
        n_samples = len(cleaned_focal_lengths)
        
        # Calculate standard error of the mean for confidence intervals
        sem = focal_std / np.sqrt(n_samples)
        
        # Get search range strategy from config
        adaptive_config = config.get('adaptive_geocalib', {})
        search_strategy = adaptive_config.get('search_range_strategy', 'confidence_interval')

        # Calculate search range based on selected strategy
        if search_strategy == "aggressive":
            # Aggressive tightening: Use very tight bounds for excellent convergence
            if cv < 0.3:  # Excellent convergence
                # Use 90% confidence interval (1.645 SEM) + minimal buffer
                confidence_interval = 1.645 * sem
                f_uncertainty = max(confidence_interval + 0.5, 2.0)  # min 2px
                strategy = "90% CI + 0.5px buffer (aggressive, excellent)"
            elif cv < 0.5:  # Very good convergence
                # Use 95% confidence interval (1.96 SEM) + small buffer
                confidence_interval = 1.96 * sem
                f_uncertainty = max(confidence_interval + 1.0, 3.0)  # min 3px
                strategy = "95% CI + 1px buffer (aggressive, very good)"
            elif cv < 0.8:  # Good convergence
                # Use 1 standard deviation + small buffer
                f_uncertainty = max(focal_std + 1.5, 4.0)
                strategy = "1 std dev + 1.5px buffer (aggressive, good)"
            elif cv < 1.2:  # Moderate convergence
                # Use 1.2x standard deviation + small buffer
                f_uncertainty = max(1.2 * focal_std + 2.0, 5.0)
                strategy = "1.2x std dev + 2px buffer (aggressive, moderate)"
            else:  # Poor convergence
                # Use 1.5x standard deviation + buffer
                f_uncertainty = max(1.5 * focal_std + 2.5, 6.0)
                strategy = "1.5x std dev + 2.5px buffer (aggressive, poor)"

        elif search_strategy == "conservative":
            # Conservative: Closer to original method, safer bounds
            if cv < 0.3:  # Excellent convergence
                # Use 2x standard deviation + moderate buffer
                f_uncertainty = max(2.0 * focal_std + 1.5, 5.0)
                strategy = "2x std dev + 1.5px buffer (conservative, excellent)"
            elif cv < 0.5:  # Very good convergence
                # Use 2.5x standard deviation + buffer
                f_uncertainty = max(2.5 * focal_std + 2.0, 6.0)
                strategy = "2.5x std dev + 2px buffer (conservative, very good)"
            elif cv < 0.8:  # Good convergence
                # Use 3x standard deviation + buffer
                f_uncertainty = max(3.0 * focal_std + 2.0, 7.0)
                strategy = "3x std dev + 2px buffer (conservative, good)"
            elif cv < 1.2:  # Moderate convergence
                # Use range-based approach with large buffer
                range_buffer = min(focal_range * 0.25, 8.0)  # 25% of range, max 8px
                f_uncertainty = max(focal_std + range_buffer, 8.0)
                strategy = f"1 std dev + {range_buffer:.1f}px range buffer (conservative, moderate)"
            else:  # Poor convergence
                # Use original-style approach
                f_uncertainty = max(focal_range * 0.6, 10.0)
                strategy = "60% of observed range (conservative, poor)"

        else:  # Default: "confidence_interval" strategy
            # Principled search range calculation based on statistical confidence intervals
            if cv < 0.3:  # Excellent convergence (very tight)
                # Use 99% confidence interval (2.58 SEM) + small buffer
                confidence_interval = 2.58 * sem
                f_uncertainty = max(confidence_interval + 1.0, 3.0)  # min 3px for robustness
                strategy = "99% confidence interval + 1px buffer (excellent convergence)"
            elif cv < 0.5:  # Very good convergence
                # Use 95% confidence interval (1.96 SEM) + buffer
                confidence_interval = 1.96 * sem
                f_uncertainty = max(confidence_interval + 2.0, 4.0)  # min 4px for robustness
                strategy = "95% confidence interval + 2px buffer (very good convergence)"
            elif cv < 0.8:  # Good convergence
                # Use 90% confidence interval (1.645 SEM) + larger buffer
                confidence_interval = 1.645 * sem
                f_uncertainty = max(confidence_interval + 3.0, 5.0)  # min 5px for robustness
                strategy = "90% confidence interval + 3px buffer (good convergence)"
            elif cv < 1.2:  # Moderate convergence
                # Use 1 standard deviation + adaptive buffer based on range
                range_buffer = min(focal_range * 0.15, 6.0)  # 15% of range, max 6px
                f_uncertainty = max(focal_std + range_buffer, 6.0)
                strategy = f"1 std dev + {range_buffer:.1f}px range buffer (moderate convergence)"
            else:  # Poor convergence
                # Conservative: 1.5x std dev + larger buffer
                f_uncertainty = max(1.5 * focal_std + 4.0, 8.0)
                strategy = "1.5x std dev + 4px buffer (poor convergence)"

        # Calculate improvement metrics
        improvement_pct = max(0, (focal_length_search_range - f_uncertainty) / focal_length_search_range * 100)

        logging.info(f"  Adaptive focal length search range ({search_strategy} mode):")
        logging.info(f"   Convergence CV: {cv:.2f}%")
        logging.info(f"   Sample size: {n_samples} (outliers removed: {outlier_info['outliers_removed']})")
        logging.info(f"   Standard deviation: {focal_std:.2f} px")
        logging.info(f"   Standard error of mean: {sem:.2f} px")
        logging.info(f"   Observed range: {focal_range:.1f} px")
        logging.info(f"   Strategy: {strategy}")
        logging.info(f"   Search range: ±{f_uncertainty:.1f} px (vs ±{focal_length_search_range} px fixed)")
        logging.info(f"   Improvement: {improvement_pct:.1f}% tighter bounds")

        # Additional statistical insights
        if cv < 0.5:
            logging.info(f"    Convergence quality: Excellent - very tight statistical bounds possible")
        elif cv < 1.0:
            logging.info(f"    Convergence quality: Good - moderate tightening appropriate")
        else:
            logging.info(f"    Convergence quality: Needs attention - conservative bounds recommended")

        # Store outlier detection info for summary
        outlier_detection_info = outlier_info

    else:
        # Use original fixed uncertainty for single-run mode
        f_uncertainty = focal_length_search_range
        strategy = "fixed range (single-run mode)"
        outlier_detection_info = None
        improvement_pct = 0
        logging.info(f"📊 Using fixed focal length search range: ±{f_uncertainty} px")
    
    f_min = (f - f_uncertainty) 
    f_max = (f + f_uncertainty) 
    
    # pitch
    rot_x = pitch.item()
    pitch_uncertainty = rad2deg(result['pitch_uncertainty'].item())
    rot_x_min = rot_x - pitch_uncertainty
    rot_x_max = rot_x + pitch_uncertainty
    # roll
    rot_z = roll.item()
    roll_uncertainty = rad2deg(result['roll_uncertainty'].item())
    # GeoCalib's camera z axis is forward
    # AMC's camera z axis is backward
    rot_z_min = -rot_z - roll_uncertainty
    rot_z_max = -rot_z + roll_uncertainty

    config_params["focal_length"] = [float(f_min), float(f_max)]  # Convert to Python float
    config_params["rotation_x"] = [float(rot_x_min), float(rot_x_max)]  # Convert to Python float
    config_params["rotation_z"] = [float(rot_z_min), float(rot_z_max)]  # Convert to Python float
    config_params["focal_length_init"] = float(f)  # Convert to Python float

    with open(out_path, "w") as f:
        yaml.dump(config_params, f, default_flow_style=False, sort_keys=False)
    logging.info(f"Saved config params to {out_path}")
    
    # Save adaptive search range info to summary if available
    if iteration_stats is not None:
        adaptive_info = {
            'adaptive_search_range_used': float(f_uncertainty),
            'original_fixed_range': focal_length_search_range,
            'search_range_strategy': search_strategy,
            'improvement_strategy': strategy,
            'improvement_percentage': float(improvement_pct),
            'iteration_cv': float(cv),
            'observed_focal_std': float(focal_std),
            'observed_focal_range': float(focal_range),
            'standard_error_mean': float(sem),
            'sample_size': int(n_samples)
        }
        
        # Add outlier detection info if available
        if outlier_detection_info is not None:
            adaptive_info['outlier_detection'] = outlier_detection_info
            
        iteration_stats['search_range_info'] = adaptive_info

def calculate_focal_length_cv(focal_lengths):
    """Calculate coefficient of variation for focal length values.
    
    Args:
        focal_lengths (list): List of focal length values
        
    Returns:
        float: Coefficient of variation as percentage
    """
    if len(focal_lengths) < 2:
        return float('inf')  # Not enough data
    
    focal_array = np.array(focal_lengths)
    mean_focal = np.mean(focal_array)
    std_focal = np.std(focal_array)
    cv = (std_focal / mean_focal) * 100
    return cv

def get_representative_focal_length(focal_lengths, use_median=True):
    """Get representative focal length value.
    
    Args:
        focal_lengths (list): List of focal length values
        use_median (bool): If True, use median; otherwise use mean
        
    Returns:
        float: Representative focal length value
    """
    focal_array = np.array(focal_lengths)
    if use_median:
        return np.median(focal_array)
    else:
        return np.mean(focal_array)

def run_adaptive_geocalib(img_path, out_path, camera_model, weights_name, config, args=None):
    """Run GeoCalib with adaptive iteration approach.
    
    Args:
        img_path (str): Path to input image
        out_path (str): Output directory
        weights_name (str): GeoCalib model name
        config (dict): Configuration parameters
        
    Returns:
        dict: Final calibration result with representative values
    """
    logging.info("Starting adaptive GeoCalib approach...")
    
    # Get adaptive configuration
    adaptive_config = config.get('adaptive_geocalib', {})
    min_iterations = adaptive_config.get('min_iterations', 20)
    max_iterations = adaptive_config.get('max_iterations', 50)
    iteration_increment = adaptive_config.get('iteration_increment', 5)
    cv_threshold = adaptive_config.get('cv_threshold', 0.8)
    early_stop_cv = adaptive_config.get('early_stop_cv', 0.5)
    use_median = adaptive_config.get('use_median', True)
    save_temp_files = adaptive_config.get('save_temp_files', True)
    
    logging.info(f"Adaptive parameters: min={min_iterations}, max={max_iterations}, "
                f"increment={iteration_increment}, cv_threshold={cv_threshold}%")
    
    device = "cuda" if torch.cuda.is_available() else "cpu"
    
    # Use local model file
    model_path = get_local_model_path(weights_name)
    logging.info(f"Using local model: {model_path}")
    model = GeoCalib(weights=model_path).to(device)

    # Load image once
    image = model.load_image(img_path).to(device)

    # Create temporary directory for iteration results
    temp_dir = Path(out_path) / "geocalib_adaptive"
    if save_temp_files:
        temp_dir.mkdir(parents=True, exist_ok=True)
    
    # Store all iteration results
    all_results = []
    focal_lengths = []
    
    # First, run minimum iterations
    logging.info(f"\n--- Running initial batch: {min_iterations} iterations ---")
    for i in range(min_iterations):
        logging.info(f"Running calibration iteration {i+1}...")
        result = model.calibrate(image, camera_model=camera_model)
        
        # Extract focal length
        focal_length = result["camera"].f[0, 1].item()
        logging.info(f"  → Focal length: {focal_length:.1f} px")
        focal_lengths.append(focal_length)
        all_results.append(result)
        
        # Save temporary result if enabled
        if save_temp_files:
            temp_result_file = temp_dir / f"iteration_{i+1:03d}.json"
            roll, pitch = rad2deg(result["gravity"].rp).unbind(-1)
            temp_data = {
                'iteration': i + 1,
                'focal_length': focal_length,
                'roll': roll.item(),
                'pitch': pitch.item(),
                'vfov': rad2deg(result["camera"].vfov).item(),
                'roll_uncertainty': rad2deg(result['roll_uncertainty']).item(),
                'pitch_uncertainty': rad2deg(result['pitch_uncertainty']).item(),
                'vfov_uncertainty': rad2deg(result['vfov_uncertainty']).item(),
                'focal_uncertainty': result['focal_uncertainty'].item()
            }
            with open(temp_result_file, 'w') as f:
                json.dump(temp_data, f, indent=2)
    
    # Check convergence and add more iterations if needed
    while len(focal_lengths) < max_iterations:
        # Calculate current CV
        cv = calculate_focal_length_cv(focal_lengths)
        logging.info(f"Current CV after {len(focal_lengths)} iterations: {cv:.2f}%")
        
        # Check stopping conditions
        if cv < early_stop_cv:
            logging.info(f"✅ Early stop: Excellent CV ({cv:.2f}%) achieved at {len(focal_lengths)} iterations")
            break
        elif cv < cv_threshold:
            logging.info(f"✅ Convergence achieved: CV ({cv:.2f}%) below threshold at {len(focal_lengths)} iterations")
            break
        elif len(focal_lengths) + iteration_increment > max_iterations:
            # Run remaining iterations up to max
            remaining = max_iterations - len(focal_lengths)
            logging.info(f"⚠️  Running final {remaining} iterations to reach maximum ({max_iterations})")
            
            for i in range(remaining):
                current_iter = len(focal_lengths) + 1
                logging.info(f"Running calibration iteration {current_iter}...")
                result = model.calibrate(image, camera_model=camera_model)
                
                focal_length = result["camera"].f[0, 1].item()
                logging.info(f"  → Focal length: {focal_length:.1f} px")
                focal_lengths.append(focal_length)
                all_results.append(result)
                
                if save_temp_files:
                    temp_result_file = temp_dir / f"iteration_{current_iter:03d}.json"
                    roll, pitch = rad2deg(result["gravity"].rp).unbind(-1)
                    temp_data = {
                        'iteration': current_iter,
                        'focal_length': focal_length,
                        'roll': roll.item(),
                        'pitch': pitch.item(),
                        'vfov': rad2deg(result["camera"].vfov).item(),
                        'roll_uncertainty': rad2deg(result['roll_uncertainty']).item(),
                        'pitch_uncertainty': rad2deg(result['pitch_uncertainty']).item(),
                        'vfov_uncertainty': rad2deg(result['vfov_uncertainty']).item(),
                        'focal_uncertainty': result['focal_uncertainty'].item()
                    }
                    with open(temp_result_file, 'w') as f:
                        json.dump(temp_data, f, indent=2)
            break
        else:
            # Add more iterations
            logging.info(f"➕ CV ({cv:.2f}%) above threshold, adding {iteration_increment} more iterations...")
            
            for i in range(iteration_increment):
                current_iter = len(focal_lengths) + 1
                logging.info(f"Running calibration iteration {current_iter}...")
                result = model.calibrate(image, camera_model=camera_model)
                
                focal_length = result["camera"].f[0, 1].item()
                logging.info(f"  → Focal length: {focal_length:.1f} px")
                focal_lengths.append(focal_length)
                all_results.append(result)
                
                if save_temp_files:
                    temp_result_file = temp_dir / f"iteration_{current_iter:03d}.json"
                    roll, pitch = rad2deg(result["gravity"].rp).unbind(-1)
                    temp_data = {
                        'iteration': current_iter,
                        'focal_length': focal_length,
                        'roll': roll.item(),
                        'pitch': pitch.item(),
                        'vfov': rad2deg(result["camera"].vfov).item(),
                        'roll_uncertainty': rad2deg(result['roll_uncertainty']).item(),
                        'pitch_uncertainty': rad2deg(result['pitch_uncertainty']).item(),
                        'vfov_uncertainty': rad2deg(result['vfov_uncertainty']).item(),
                        'focal_uncertainty': result['focal_uncertainty'].item()
                    }
                    with open(temp_result_file, 'w') as f:
                        json.dump(temp_data, f, indent=2)
    
    # Final CV calculation
    cv = calculate_focal_length_cv(focal_lengths)
    
    # Calculate representative values
    representative_focal = get_representative_focal_length(focal_lengths, use_median)
    
    logging.info(f"\n📊 ADAPTIVE GEOCALIB SUMMARY:")
    logging.info(f"Total iterations: {len(focal_lengths)}")
    logging.info(f"Final CV: {cv:.2f}%")
    logging.info(f"Focal length range: {min(focal_lengths):.1f} - {max(focal_lengths):.1f} px")
    logging.info(f"Representative focal ({'median' if use_median else 'mean'}): {representative_focal:.1f} px")
    
    # Use the result closest to representative focal length
    focal_diffs = [abs(result["camera"].f[0, 1].item() - representative_focal) for result in all_results]
    best_result_idx = np.argmin(focal_diffs)
    best_result = all_results[best_result_idx]
    
    logging.info(f"Selected result from iteration {best_result_idx + 1} (closest to representative value)")
    
    # Save summary
    if save_temp_files:
        summary_file = temp_dir / "adaptive_summary.json"
        summary_data = {
            'total_iterations': int(len(focal_lengths)),
            'final_cv': float(cv),
            'representative_focal_length': float(representative_focal),
            'use_median': use_median,
            'focal_length_range': [float(min(focal_lengths)), float(max(focal_lengths))],
            'all_focal_lengths': [float(f) for f in focal_lengths],
            'selected_iteration': int(best_result_idx + 1),
            'config_used': adaptive_config
        }
        with open(summary_file, 'w') as f:
            json.dump(summary_data, f, indent=2)
        
        logging.info(f"Temporary files saved to: {temp_dir}")
    
    # Prepare iteration statistics for save_config_params
    iteration_stats = {
        'focal_lengths': focal_lengths,
        'cv': cv,
        'representative_focal': representative_focal,
        'total_iterations': len(focal_lengths),
        'selected_iteration': best_result_idx + 1,
        'use_median': use_median
    }
    
    return best_result, iteration_stats

def save_geocalib_undistorted_image(result, img_path, out_path, model_name):
    """Save undistorted image using GeoCalib's camera model.
    
    Args:
        result: GeoCalib calibration result
        img_path: Path to input image
        out_path: Output directory
        model_name: Model type (pinhole or distorted)
    """
    # Only save undistorted image if using distorted model with k1
    if model_name != "distorted" or not hasattr(result['camera'], 'k1'):
        return
    
    try:
        device = result['camera'].device
        camera = result['camera']
        
        logging.info("Saving GeoCalib undistorted image...")
        
        # Load image as tensor [C, H, W] in range [0, 1]
        img_cv = cv2.imread(img_path)
        if img_cv is None:
            logging.warning(f"Could not load image for undistortion: {img_path}")
            return
        
        # Convert BGR to RGB and to tensor
        img_rgb = cv2.cvtColor(img_cv, cv2.COLOR_BGR2RGB)
        img_tensor = torch.from_numpy(img_rgb).permute(2, 0, 1).float() / 255.0
        img_tensor = img_tensor.unsqueeze(0).to(device)  # Add batch dimension
        
        # Undistort using GeoCalib's camera model
        img_undistorted = camera.undistort_image(img_tensor)
        
        # Convert back to numpy and BGR
        img_undistorted = img_undistorted.squeeze(0).permute(1, 2, 0).cpu().numpy()
        img_undistorted = (img_undistorted * 255.0).clip(0, 255).astype(np.uint8)
        img_undistorted_bgr = cv2.cvtColor(img_undistorted, cv2.COLOR_RGB2BGR)
        
        # Save
        output_path = f'{out_path}/geocalib_undistorted.jpg'
        cv2.imwrite(output_path, img_undistorted_bgr)
        logging.info(f"Saved GeoCalib undistorted image to {output_path}")
        
    except Exception as e:
        logging.warning(f"Failed to save GeoCalib undistorted image: {e}")

def main(img_path, out_path, config=None, args=None):
    logging.basicConfig(level=logging.INFO)

    iteration_stats = None  # Will be set for adaptive mode
    
    # Determine camera model (distortion model) to use
    camera_model = determine_camera_model(args)
    logging.info(f"Using camera/distortion model: {camera_model}")

    # Map camera_model to weight file name
    # GeoCalib has 2 weight files: 'pinhole' and 'distorted'
    # All distortion models (simple_radial, radial, simple_divisional) use 'distorted' weights
    weights_name = "pinhole" if camera_model == "pinhole" else "distorted"

    # Check if adaptive approach is enabled
    adaptive_config = config.get('adaptive_geocalib', {}) if config else {}
    if adaptive_config.get('enabled', False):
        # Use adaptive approach
        result, iteration_stats = run_adaptive_geocalib(img_path, out_path, camera_model, weights_name, config, args)
    else:
        # Use original single-run approach
        device = "cuda" if torch.cuda.is_available() else "cpu"

        # Use local model file instead of downloading
        model_path = get_local_model_path(weights_name)
        logging.info(f"Using local model: {model_path}")
        model = GeoCalib(weights=model_path).to(device)

        # load image as tensor in range [0, 1] with shape [C, H, W]
        image = model.load_image(img_path).to(device)
        result = model.calibrate(image, camera_model=camera_model)
        
        # Log focal length for single-run mode
        focal_length = result["camera"].f[0, 1].item()
        logging.info(f"Single-run focal length: {focal_length:.1f} px")

    # print_calibration(result)
    
    if not os.path.exists(out_path):
        os.makedirs(out_path, exist_ok=True)
    save_config_params(result, f"{out_path}/config_sv_amc.yaml", config, iteration_stats)
    
    # Save GeoCalib undistorted image (only for distorted model)
    save_geocalib_undistorted_image(result, img_path, out_path, weights_name)
    
    # Update summary with search range info if adaptive mode was used
    if iteration_stats is not None and 'search_range_info' in iteration_stats:
        adaptive_config = config.get('adaptive_geocalib', {})
        if adaptive_config.get('save_temp_files', True):
            temp_dir = Path(out_path) / "geocalib_adaptive"
            summary_file = temp_dir / "adaptive_summary.json"
            if summary_file.exists():
                # Update the existing summary with search range info
                with open(summary_file, 'r') as f:
                    summary_data = json.load(f)
                summary_data['search_range_info'] = iteration_stats['search_range_info']
                with open(summary_file, 'w') as f:
                    json.dump(summary_data, f, indent=2)
                logging.info(f"Updated summary with adaptive search range info")

def validate_inputs(img_path: str = None, video_path: str = None, 
                    out_path: str = None, config_path: str = None) -> None:
    """Validate input parameters and file existence.

    Args:
        img_path: path to the image file
        video_path: path to the video file
        out_path: path to the output directory
    """
    if img_path is None and video_path is None:
        raise ValueError("Either -i (img_path) or -v (video_path) must be provided")
    
    if img_path is not None:
        if not os.path.exists(img_path):
            raise FileNotFoundError(f"Image file not found: {img_path}")
        if not os.access(img_path, os.R_OK):
            raise PermissionError(f"Cannot read image file: {img_path}")
    
    if video_path is not None:
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Video file not found: {video_path}")
        if not os.access(video_path, os.R_OK):
            raise PermissionError(f"Cannot read video file: {video_path}")
    
    if out_path is not None:
        if os.path.exists(out_path) and not os.access(out_path, os.W_OK):
            raise PermissionError(f"Cannot write to output directory: {out_path}")

    if config_path is not None:
        if not os.path.exists(config_path):
            raise FileNotFoundError(f"Config file not found: {config_path}")
        if not os.access(config_path, os.R_OK):
            raise PermissionError(f"Cannot read config file: {config_path}")


if __name__ == "__main__":
    args = parse_args()
    validate_inputs(args.img_path, args.video_path, args.out_path)

    with open(args.config, "r") as f:
        config = yaml.safe_load(f)


    if args.video_path is not None:
        cap = open_video_capture(args.video_path)
        if not cap.isOpened():
            logging.error(f"Failed to open video file: {args.video_path}")
            exit(1)
        # extract background
        config_bg_extraction = {'preprocess': {'num_frames': 500, 
                                 'frame_step': 30, 
                                 'resize_width': 960, 
                                 'resize_height': 540}}
        scale = 1.0
        bg_img = extract_background(args.video_path, config_bg_extraction, scale)
        #ret, frame = cap.read()
        #if not ret:
        #    logging.error(f"Failed to read frame from video file: {args.video_path}")
        #    exit(1)
        cv2.imwrite(f'{args.out_path}/geocalib_input.jpg', bg_img)
        cap.release()

        main(f'{args.out_path}/geocalib_input.jpg', args.out_path, 
             config, args)
    else:
        main(args.img_path, args.out_path, config, args)
