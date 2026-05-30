#!/bin/bash
set -euo pipefail

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

# Batch Camera Grouping Script (Optimized)
# 
# This script iterates through all scene folders under a data directory,
# displays all scenes with their parameters, lets user select which
# scenes to process, and then processes them all together.
#
# Usage: bash tools/camera_grouping/batch_create_camera_groups.sh [DATA_DIR]
#
# Arguments:
#   DATA_DIR  - Path to directory containing scene folders (default: data/mtmc)
#
# Examples:
#   bash tools/camera_grouping/batch_create_camera_groups.sh
#   bash tools/camera_grouping/batch_create_camera_groups.sh /path/to/my/scenes
#   bash tools/camera_grouping/batch_create_camera_groups.sh data/custom_dataset
#

# Use set -uo pipefail for better error handling
# Note: We intentionally do NOT use 'set -e' because we want to capture and handle
# command failures in process_scenes() rather than having the script exit immediately.
# The -u flag ensures undefined variables cause errors, and pipefail ensures pipe
# command failures are properly propagated.
set -uo pipefail

# Colors for better readability
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color
BOLD='\033[1m'
DIM='\033[2m'

# Default parameters
DEFAULT_AUTO_MODE="yes"
DEFAULT_N_GROUPS=1
DEFAULT_MAX_SENSORS_PER_GROUP=18
DEFAULT_CAMERAS_PER_GROUP="8"
DEFAULT_OUTPUT_SUFFIX="grouped"
DEFAULT_START_CAMERA_INDEX=0
DEFAULT_MIN_OVERLAP_THRESHOLD=0.2
DEFAULT_MAX_DISTANCE_THRESHOLD="inf"
DEFAULT_MAX_CAMERA_DISTANCE=30.0
DEFAULT_HEIGHT_RANGE="1.0 3.0"
DEFAULT_IMAGE_SIZE="1920 1080"
DEFAULT_DILATION=8.0
DEFAULT_VISUALIZE="yes"
DEFAULT_VIS_COMBINED="no"

# Working directory (script directory)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Parse command line arguments
if [[ $# -ge 1 ]]; then
    # Use provided data directory
    if [[ "$1" == /* ]]; then
        # Absolute path
        DATA_DIR="$1"
    else
        # Relative path - make it relative to current working directory
        DATA_DIR="$(pwd)/$1"
    fi
else
    # Default data directory
    DATA_DIR="$PROJECT_ROOT/data/mtmc"
fi

# Arrays to store scene data
declare -a SCENE_PATHS
declare -a SCENE_NAMES
declare -a SCENE_CAMERAS
declare -a SCENE_MAP_FILES
declare -a SCENE_EXISTING
declare -a SCENE_SELECTED

# Function to print a separator line
print_separator() {
    echo -e "${BLUE}════════════════════════════════════════════════════════════════════════════════${NC}"
}

# Function to print a thin separator
print_thin_separator() {
    echo -e "${DIM}────────────────────────────────────────────────────────────────────────────────${NC}"
}

# Function to read input with default value
# Uses printf -v for safe variable assignment (no command injection risk)
read_with_default() {
    local prompt=$1
    local default=$2
    local varname=$3
    
    echo -ne "${GREEN}$prompt${NC} [${YELLOW}$default${NC}]: "
    read -r input
    if [[ -z "$input" ]]; then
        printf -v "$varname" '%s' "$default"
    else
        printf -v "$varname" '%s' "$input"
    fi
}

# Function to read yes/no with default
# Uses printf -v for safe variable assignment (no command injection risk)
read_yes_no() {
    local prompt=$1
    local default=$2
    local varname=$3
    
    local default_display
    if [[ "$default" == "yes" ]]; then
        default_display="Y/n"
    else
        default_display="y/N"
    fi
    
    echo -ne "${GREEN}$prompt${NC} [$default_display]: "
    read -r input
    input=$(echo "$input" | tr '[:upper:]' '[:lower:]')
    
    if [[ -z "$input" ]]; then
        printf -v "$varname" '%s' "$default"
    elif [[ "$input" == "y" || "$input" == "yes" ]]; then
        printf -v "$varname" '%s' "yes"
    else
        printf -v "$varname" '%s' "no"
    fi
}

# Function to count cameras in calibration file
count_cameras() {
    local calib_file=$1
    if command -v python3 &> /dev/null; then
        python3 -c "
import json
try:
    with open('$calib_file', 'r') as f:
        data = json.load(f)
    cameras = data.get('sensors', data.get('cameras', []))
    print(len(cameras))
except:
    print('?')
" 2>/dev/null
    else
        echo "?"
    fi
}

# Function to resolve map file path
resolve_map_file() {
    local scene_path=$1
    local scene_name=$(basename "$scene_path")
    local parent_dir=$(dirname "$scene_path")
    
    # First check if Top.png exists in the current scene folder
    if [[ -f "$scene_path/Top.png" ]]; then
        echo "$scene_path/Top.png"
        return
    fi
    
    # If scene name contains __, try to find Top.png in the base scene folder
    if [[ "$scene_name" == *"__"* ]]; then
        local base_scene="${scene_name%%__*}"
        local base_scene_path="$parent_dir/$base_scene"
        
        if [[ -f "$base_scene_path/Top.png" ]]; then
            echo "$base_scene_path/Top.png"
            return
        fi
    fi
    
    # No map file found
    echo ""
}

# Function to collect all scene data
collect_scenes() {
    echo -e "${CYAN}Scanning for scene directories with calibration.json...${NC}"
    
    local idx=0
    shopt -s nullglob
    for calib_file in "$DATA_DIR"/*/calibration.json; do
        local scene_dir=$(dirname "$calib_file")
        local scene_name=$(basename "$scene_dir")
        
        # Skip directories that are clearly not scenes
        if [[ "$scene_name" != "Calibration_old" && \
              "$scene_name" != "Calibration_check" && \
              "$scene_name" != "test" && \
              "$scene_name" != "data" && \
              ! "$scene_name" =~ ^anno_ && \
              ! "$scene_name" =~ ^split_ && \
              ! "$scene_name" =~ ^OUTPUT_ ]]; then
            
            SCENE_PATHS[$idx]="$scene_dir"
            SCENE_NAMES[$idx]="$scene_name"
            SCENE_CAMERAS[$idx]=$(count_cameras "$calib_file")
            SCENE_MAP_FILES[$idx]=$(resolve_map_file "$scene_dir")
            
            # Check for existing grouped file
            if [[ -f "$scene_dir/calibration_${DEFAULT_OUTPUT_SUFFIX}.json" ]]; then
                SCENE_EXISTING[$idx]="yes"
            else
                SCENE_EXISTING[$idx]="no"
            fi
            
            # Default to selected
            SCENE_SELECTED[$idx]="yes"
            
            idx=$((idx + 1))
        fi
    done
    shopt -u nullglob
    
    # Sort scenes by name (using indices)
    # Simple bubble sort for bash compatibility
    local n=${#SCENE_NAMES[@]}
    for ((i=0; i<n-1; i++)); do
        for ((j=0; j<n-i-1; j++)); do
            if [[ "${SCENE_NAMES[$j]}" > "${SCENE_NAMES[$((j+1))]}" ]]; then
                # Swap all arrays
                local tmp="${SCENE_PATHS[$j]}"
                SCENE_PATHS[$j]="${SCENE_PATHS[$((j+1))]}"
                SCENE_PATHS[$((j+1))]="$tmp"
                
                tmp="${SCENE_NAMES[$j]}"
                SCENE_NAMES[$j]="${SCENE_NAMES[$((j+1))]}"
                SCENE_NAMES[$((j+1))]="$tmp"
                
                tmp="${SCENE_CAMERAS[$j]}"
                SCENE_CAMERAS[$j]="${SCENE_CAMERAS[$((j+1))]}"
                SCENE_CAMERAS[$((j+1))]="$tmp"
                
                tmp="${SCENE_MAP_FILES[$j]}"
                SCENE_MAP_FILES[$j]="${SCENE_MAP_FILES[$((j+1))]}"
                SCENE_MAP_FILES[$((j+1))]="$tmp"
                
                tmp="${SCENE_EXISTING[$j]}"
                SCENE_EXISTING[$j]="${SCENE_EXISTING[$((j+1))]}"
                SCENE_EXISTING[$((j+1))]="$tmp"
                
                tmp="${SCENE_SELECTED[$j]}"
                SCENE_SELECTED[$j]="${SCENE_SELECTED[$((j+1))]}"
                SCENE_SELECTED[$((j+1))]="$tmp"
            fi
        done
    done
}

# Function to display all scenes in a table
display_all_scenes() {
    local total=${#SCENE_NAMES[@]}
    
    echo ""
    print_separator
    echo -e "${BOLD}${CYAN}Found $total Scene(s):${NC}"
    print_separator
    echo ""
    
    # Table header
    echo -e "${BOLD}#     Scene Name                                  Cameras  Map  Existing${NC}"
    print_thin_separator
    
    for ((i=0; i<total; i++)); do
        local num=$((i+1))
        local name="${SCENE_NAMES[$i]}"
        local cameras="${SCENE_CAMERAS[$i]}"
        local map_display
        local existing_display
        
        if [[ -n "${SCENE_MAP_FILES[$i]}" ]]; then
            map_display="${GREEN}✓${NC}"
        else
            map_display="${YELLOW}✗${NC}"
        fi
        
        if [[ "${SCENE_EXISTING[$i]}" == "yes" ]]; then
            existing_display="${YELLOW}exists${NC}"
        else
            existing_display="${GREEN}new${NC}   "
        fi
        
        # Truncate name if too long
        if [[ ${#name} -gt 43 ]]; then
            name="${name:0:40}..."
        fi
        
        # Build the line with printf for alignment, then echo -e for colors
        local line=$(printf "%-5s %-45s %5s" "$num" "$name" "$cameras")
        echo -e "${line}  ${map_display}    ${existing_display}"
    done
    
    echo ""
}

# Function to set global parameters
set_global_parameters() {
    echo -e "${BOLD}Global Parameters:${NC}"
    echo -e "${CYAN}(These will be applied to all selected scenes)${NC}"
    echo ""
    
    read_yes_no "  Use auto mode?" "$DEFAULT_AUTO_MODE" "AUTO_MODE"
    read_with_default "  Number of groups per size" "$DEFAULT_N_GROUPS" "N_GROUPS"
    
    if [[ "$AUTO_MODE" == "yes" ]]; then
        read_with_default "  Max sensors per group" "$DEFAULT_MAX_SENSORS_PER_GROUP" "MAX_SENSORS_PER_GROUP"
        CAMERAS_PER_GROUP=""
        echo -e "  ${CYAN}Cameras per group:${NC} ${GREEN}[1, 2, ..., min(n_cameras, $MAX_SENSORS_PER_GROUP)] (auto)${NC}"
    else
        read_with_default "  Cameras per group (space-separated)" "$DEFAULT_CAMERAS_PER_GROUP" "CAMERAS_PER_GROUP"
        MAX_SENSORS_PER_GROUP=""
    fi
    
    read_with_default "  Output suffix" "$DEFAULT_OUTPUT_SUFFIX" "OUTPUT_SUFFIX"
    read_with_default "  Start camera index" "$DEFAULT_START_CAMERA_INDEX" "START_CAMERA_INDEX"
    read_with_default "  Min overlap threshold (0-1)" "$DEFAULT_MIN_OVERLAP_THRESHOLD" "MIN_OVERLAP_THRESHOLD"
    read_with_default "  Max distance threshold (meters or 'inf')" "$DEFAULT_MAX_DISTANCE_THRESHOLD" "MAX_DISTANCE_THRESHOLD"
    read_with_default "  Max camera distance for frustum (meters)" "$DEFAULT_MAX_CAMERA_DISTANCE" "MAX_CAMERA_DISTANCE"
    read_with_default "  Height range (min max)" "$DEFAULT_HEIGHT_RANGE" "HEIGHT_RANGE"
    read_with_default "  Image size (width height)" "$DEFAULT_IMAGE_SIZE" "IMAGE_SIZE"
    read_with_default "  Dilation (meters)" "$DEFAULT_DILATION" "DILATION"
    read_yes_no "  Generate visualization?" "$DEFAULT_VISUALIZE" "VISUALIZE"
    read_yes_no "  Use combined visualization?" "$DEFAULT_VIS_COMBINED" "VIS_COMBINED"
    
    echo ""
}

# Function to select scenes
select_scenes() {
    local total=${#SCENE_NAMES[@]}
    
    echo -e "${BOLD}Select Scenes to Process:${NC}"
    echo -e "${CYAN}Options: [a]ll, [n]one, [i]nvert, or enter scene numbers (e.g., 1 3 5-8)${NC}"
    echo ""
    
    # Show current selection
    local selected_count=0
    for ((i=0; i<total; i++)); do
        if [[ "${SCENE_SELECTED[$i]}" == "yes" ]]; then
            selected_count=$((selected_count + 1))
        fi
    done
    echo -e "${CYAN}Currently selected: ${BOLD}$selected_count/$total${NC}"
    echo ""
    
    echo -ne "${GREEN}Selection${NC} [all]: "
    read -r selection
    
    if [[ -z "$selection" || "$selection" == "a" || "$selection" == "all" ]]; then
        # Select all
        for ((i=0; i<total; i++)); do
            SCENE_SELECTED[$i]="yes"
        done
        echo -e "${GREEN}Selected all $total scenes${NC}"
    elif [[ "$selection" == "n" || "$selection" == "none" ]]; then
        # Deselect all
        for ((i=0; i<total; i++)); do
            SCENE_SELECTED[$i]="no"
        done
        echo -e "${YELLOW}Deselected all scenes${NC}"
    elif [[ "$selection" == "i" || "$selection" == "invert" ]]; then
        # Invert selection
        for ((i=0; i<total; i++)); do
            if [[ "${SCENE_SELECTED[$i]}" == "yes" ]]; then
                SCENE_SELECTED[$i]="no"
            else
                SCENE_SELECTED[$i]="yes"
            fi
        done
        echo -e "${CYAN}Inverted selection${NC}"
    else
        # Parse specific numbers (e.g., "1 3 5-8")
        # First deselect all
        for ((i=0; i<total; i++)); do
            SCENE_SELECTED[$i]="no"
        done
        
        # Parse selection
        for part in $selection; do
            if [[ "$part" =~ ^([0-9]+)-([0-9]+)$ ]]; then
                # Range (e.g., 5-8)
                local start="${BASH_REMATCH[1]}"
                local end="${BASH_REMATCH[2]}"
                for ((n=start; n<=end; n++)); do
                    local idx=$((n-1))
                    if [[ $idx -ge 0 && $idx -lt $total ]]; then
                        SCENE_SELECTED[$idx]="yes"
                    fi
                done
            elif [[ "$part" =~ ^[0-9]+$ ]]; then
                # Single number
                local idx=$((part-1))
                if [[ $idx -ge 0 && $idx -lt $total ]]; then
                    SCENE_SELECTED[$idx]="yes"
                fi
            fi
        done
        
        # Count selected
        selected_count=0
        for ((i=0; i<total; i++)); do
            if [[ "${SCENE_SELECTED[$i]}" == "yes" ]]; then
                selected_count=$((selected_count + 1))
            fi
        done
        echo -e "${GREEN}Selected $selected_count scene(s)${NC}"
    fi
    
    echo ""
}

# Global variable to store selected count
SELECTED_COUNT=0

# Function to display selected scenes with their commands
display_selected_scenes() {
    local total=${#SCENE_NAMES[@]}
    SELECTED_COUNT=0
    
    echo ""
    print_separator
    echo -e "${BOLD}${CYAN}Scenes to Process:${NC}"
    print_separator
    echo ""
    
    for ((i=0; i<total; i++)); do
        if [[ "${SCENE_SELECTED[$i]}" == "yes" ]]; then
            SELECTED_COUNT=$((SELECTED_COUNT + 1))
            local name="${SCENE_NAMES[$i]}"
            local cameras="${SCENE_CAMERAS[$i]}"
            local map_info
            
            if [[ -n "${SCENE_MAP_FILES[$i]}" ]]; then
                map_info="+map"
                map_color="${GREEN}"
            else
                map_info="no-map"
                map_color="${YELLOW}"
            fi
            
            # Calculate actual max for auto mode
            local groups_info
            if [[ "$AUTO_MODE" == "yes" ]]; then
                if [[ "$cameras" =~ ^[0-9]+$ ]]; then
                    local actual_max=$((cameras < MAX_SENSORS_PER_GROUP ? cameras : MAX_SENSORS_PER_GROUP))
                    groups_info="auto[1..$actual_max]"
                else
                    groups_info="auto[1..?]"
                fi
            else
                groups_info="$CAMERAS_PER_GROUP"
            fi
            
            # Build line and echo with colors
            local line=$(printf "%4d. %-35s %3s cams | %-14s |" "$SELECTED_COUNT" "$name" "$cameras" "$groups_info")
            echo -e "  ${GREEN}${line}${NC} ${map_color}${map_info}${NC}"
        fi
    done
    
    if [[ $SELECTED_COUNT -eq 0 ]]; then
        echo -e "  ${YELLOW}No scenes selected${NC}"
    fi
    
    echo ""
    echo -e "${BOLD}Total: $SELECTED_COUNT scene(s) to process${NC}"
    echo ""
}

# Function to build command for a scene
build_scene_command() {
    local scene_path=$1
    local map_file=$2
    
    local cmd="python $SCRIPT_DIR/create_camera_groups.py"
    cmd+=" \"$scene_path\""
    
    if [[ "$AUTO_MODE" == "yes" ]]; then
        cmd+=" --auto"
        cmd+=" --max_sensors_per_group $MAX_SENSORS_PER_GROUP"
    else
        cmd+=" --cameras_per_group $CAMERAS_PER_GROUP"
    fi
    
    cmd+=" --n_groups $N_GROUPS"
    cmd+=" --output_suffix $OUTPUT_SUFFIX"
    cmd+=" --start_camera_index $START_CAMERA_INDEX"
    cmd+=" --min_overlap_threshold $MIN_OVERLAP_THRESHOLD"
    
    if [[ "$MAX_DISTANCE_THRESHOLD" != "inf" ]]; then
        cmd+=" --max_distance_threshold $MAX_DISTANCE_THRESHOLD"
    fi
    
    cmd+=" --max_camera_distance $MAX_CAMERA_DISTANCE"
    cmd+=" --height_range $HEIGHT_RANGE"
    cmd+=" --image_size $IMAGE_SIZE"
    cmd+=" --dilation $DILATION"
    
    if [[ "$VISUALIZE" == "yes" ]]; then
        cmd+=" --visualize"
    fi
    
    if [[ "$VIS_COMBINED" == "yes" ]]; then
        cmd+=" --vis_combined"
    fi
    
    if [[ -n "$map_file" ]]; then
        cmd+=" --map_file \"$map_file\""
    fi
    
    echo "$cmd"
}

# Function to process all selected scenes
process_scenes() {
    local total=${#SCENE_NAMES[@]}
    local processed=0
    local failed=0
    local scene_num=0
    local -a failed_scenes=()
    
    echo ""
    print_separator
    echo -e "${BOLD}${CYAN}Processing Scenes...${NC}"
    print_separator
    echo ""
    
    for ((i=0; i<total; i++)); do
        if [[ "${SCENE_SELECTED[$i]}" == "yes" ]]; then
            scene_num=$((scene_num + 1))
            local scene_path="${SCENE_PATHS[$i]}"
            local scene_name="${SCENE_NAMES[$i]}"
            local map_file="${SCENE_MAP_FILES[$i]}"
            
            echo -e "${BOLD}${CYAN}[$scene_num] Processing: ${YELLOW}$scene_name${NC}"
            
            # Build command
            local cmd=$(build_scene_command "$scene_path" "$map_file")
            
            # Show command (abbreviated)
            echo -e "${DIM}$cmd${NC}"
            
            # Execute with guarded execution to capture exit code without exiting script
            # This pattern allows us to capture the exit status even with pipefail set
            local exit_code=0
            if ! eval "$cmd"; then
                exit_code=$?
                # If exit_code is still 0 after a failed command (shouldn't happen),
                # set it to 1 to indicate failure
                if [[ $exit_code -eq 0 ]]; then
                    exit_code=1
                fi
            fi
            
            if [[ $exit_code -eq 0 ]]; then
                echo -e "${GREEN}✓ Success${NC}"
                processed=$((processed + 1))
            else
                echo -e "${RED}✗ Failed (exit code: $exit_code)${NC}"
                failed=$((failed + 1))
                failed_scenes+=("$scene_name")
            fi
            
            print_thin_separator
        fi
    done
    
    # Summary
    echo ""
    print_separator
    echo -e "${BOLD}${CYAN}Processing Complete!${NC}"
    print_separator
    echo -e "  ${GREEN}Successful: $processed${NC}"
    echo -e "  ${RED}Failed: $failed${NC}"
    echo -e "  ${CYAN}Total: $((processed + failed))${NC}"
    
    # List failed scenes if any
    if [[ $failed -gt 0 ]]; then
        echo ""
        echo -e "  ${RED}${BOLD}Failed Scenes:${NC}"
        for scene in "${failed_scenes[@]}"; do
            echo -e "    ${RED}• $scene${NC}"
        done
    fi
    
    print_separator
    echo ""
}

# Main function
main() {
    echo ""
    echo -e "${BOLD}${CYAN}╔═════════════════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BOLD}${CYAN}║         Batch Camera Grouping Script for MTMC Datasets (Optimized)          ║${NC}"
    echo -e "${BOLD}${CYAN}╚═════════════════════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "${CYAN}Data Directory:${NC} ${YELLOW}$DATA_DIR${NC}"
    echo ""
    
    # Check if data directory exists
    if [[ ! -d "$DATA_DIR" ]]; then
        echo -e "${RED}Error: Data directory not found: $DATA_DIR${NC}"
        exit 1
    fi
    
    # Step 1: Collect all scenes
    collect_scenes
    
    local total=${#SCENE_NAMES[@]}
    if [[ $total -eq 0 ]]; then
        echo -e "${YELLOW}No scene directories with calibration.json found.${NC}"
        exit 0
    fi
    
    # Step 2: Display all scenes
    display_all_scenes
    
    # Step 3: Set global parameters
    set_global_parameters
    
    # Step 4: Select scenes
    select_scenes
    
    # Step 5: Display selected scenes with parameters
    display_selected_scenes
    
    if [[ $SELECTED_COUNT -eq 0 ]]; then
        echo -e "${YELLOW}No scenes selected. Exiting.${NC}"
        exit 0
    fi
    
    # Step 6: Confirm and process
    read_yes_no "Start processing all selected scenes?" "yes" "CONFIRM"
    
    if [[ "$CONFIRM" != "yes" ]]; then
        echo -e "${YELLOW}Cancelled.${NC}"
        exit 0
    fi
    
    # Step 7: Process all scenes
    process_scenes
}

# Run main function
main
