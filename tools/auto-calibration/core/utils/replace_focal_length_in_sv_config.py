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
Replace focal length values in config_sv_amc.yaml with ground truth focal length.

This script preserves all GeoCalib intelligence (rotation estimates, uncertainties) 
while replacing only the focal length values with user-provided ground truth.
"""

import argparse
import yaml
import os
import sys
import shutil
from pathlib import Path

def validate_focal_length(focal_length):
    """Validate focal length is in reasonable range"""
    if focal_length <= 0:
        raise ValueError(f"Focal length must be positive, got: {focal_length}")
    
    if focal_length < 100 or focal_length > 5000:
        print(f"Warning: Focal length {focal_length} is outside typical range (100-5000 pixels)")
        response = input("Continue anyway? (y/n): ")
        if response.lower() != 'y':
            sys.exit(1)

def replace_focal_length_in_config(config_file, ground_truth_focal_length, search_range=2.0):
    """
    Replace focal length values in config_sv_amc.yaml with ground truth values
    
    Args:
        config_file (str): Path to config_sv_amc.yaml file
        ground_truth_focal_length (float): Ground truth focal length in pixels
        search_range (float): Search range around ground truth (default: ±2px)
    
    Returns:
        dict: Modified configuration
    """
    
    # Validate inputs
    if not os.path.exists(config_file):
        raise FileNotFoundError(f"Config file not found: {config_file}")
    
    validate_focal_length(ground_truth_focal_length)
    
    # Load existing configuration
    with open(config_file, 'r') as f:
        config_data = yaml.safe_load(f)
    
    # Store original values for logging
    original_fl_range = config_data.get('focal_length', [0, 0])
    original_fl_init = config_data.get('focal_length_init', 0)
    
    print(f"Original focal length range: {original_fl_range}")
    print(f"Original focal length init: {original_fl_init}")
    
    # Calculate new focal length range
    fl_min = float(ground_truth_focal_length - search_range)
    fl_max = float(ground_truth_focal_length + search_range)
    
    # Replace focal length values (preserve all other GeoCalib values)
    config_data['focal_length'] = [fl_min, fl_max]
    config_data['focal_length_init'] = float(ground_truth_focal_length)
    
    # Create backup of original file
    backup_file = config_file.replace('.yaml', '_geocalib_backup.yaml')
    if not os.path.exists(backup_file):
        shutil.copy2(config_file, backup_file)
        print(f"Created backup: {backup_file}")
    
    # Write modified configuration
    with open(config_file, 'w') as f:
        f.write("# Configuration modified with ground truth focal length\n")
        f.write(f"# Original focal length range: {original_fl_range}\n")
        f.write(f"# Original focal length init: {original_fl_init}\n")
        f.write(f"# Updated to ground truth: {ground_truth_focal_length} ± {search_range} pixels\n")
        f.write("# All other GeoCalib values (rotations, uncertainties) preserved\n\n")
        yaml.dump(config_data, f, sort_keys=False)
    
    print(f"✅ Replaced focal length values:")
    print(f"   Range: {original_fl_range} → [{fl_min}, {fl_max}]")
    print(f"   Init:  {original_fl_init} → {ground_truth_focal_length}")
    print(f"   Search range: ±{search_range} pixels")
    
    return config_data

def main():
    parser = argparse.ArgumentParser(
        description="Replace focal length in config_sv_amc.yaml with ground truth values",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s -c output/config_sv_amc.yaml -f 1200.0
  %(prog)s -c output/config_sv_amc.yaml -f 1200.0 -r 1.5
        """
    )
    
    parser.add_argument('-c', '--config', required=True,
                       help='Path to config_sv_amc.yaml file')
    parser.add_argument('-f', '--focal-length', type=float, required=True,
                       help='Ground truth focal length in pixels')
    parser.add_argument('-r', '--search-range', type=float, default=2.0,
                       help='Search range around ground truth in pixels (default: 2.0)')
    parser.add_argument('--no-backup', action='store_true',
                       help='Skip creating backup file')
    
    args = parser.parse_args()
    
    try:
        config_data = replace_focal_length_in_config(
            args.config, 
            args.focal_length, 
            args.search_range
        )
        
        print("\n🎉 Focal length replacement completed successfully!")
        print("The SV calibration will now use ground truth focal length with GeoCalib rotations.")
        return 0
        
    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        return 1

if __name__ == '__main__':
    sys.exit(main())

