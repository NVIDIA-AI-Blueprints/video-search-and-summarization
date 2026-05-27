#!/bin/bash
#
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

# Generic compile_commands.json Generator for C++ Projects
# Supports multi-platform builds (local + Docker)
# Works with any Makefile-based C++ project

set -euo pipefail

# Configuration (can be overridden via environment variables)
PROJECT_NAME="${PROJECT_NAME:-$(basename "$(pwd)")}"
DOCKER_IMAGE_X86="${DOCKER_IMAGE_X86:-""}"
DOCKER_IMAGE_CROSS="${DOCKER_IMAGE_CROSS:-""}"
BUILD_VARIANTS="${BUILD_VARIANTS:-x86}"
SOURCE_DIRS="${SOURCE_DIRS:-src}"
DOCKERFILE_PATH="${DOCKERFILE_PATH:-""}"
MAKE_PARALLEL_JOBS="${MAKE_PARALLEL_JOBS:-$(nproc 2>/dev/null || echo 4)}"

# Output directories for better organization
SONAR_OUTPUT_DIR="${SONAR_OUTPUT_DIR:-sonar-databases}"
UNIFIED_DIR="${UNIFIED_DIR:-$SONAR_OUTPUT_DIR/unified}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
print_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
print_error() { echo -e "${RED}[ERROR]${NC} $1"; }
print_config() { echo -e "${BLUE}[CONFIG]${NC} $1"; }

# Function to show configuration
show_configuration() {
    print_info "📋 Current Configuration:"
    echo "   Project Name: $PROJECT_NAME"
    echo "   Source Dirs: $SOURCE_DIRS" 
    echo "   Output Dir: $SONAR_OUTPUT_DIR"
    echo "   Unified Dir: $UNIFIED_DIR"
    echo "   Parallel Jobs: $MAKE_PARALLEL_JOBS"
    echo "   Docker Image (x86): ${DOCKER_IMAGE_X86:-Not set}"
    echo "   Docker Image (Cross): ${DOCKER_IMAGE_CROSS:-Not set}"
    echo "   Dockerfile Path: ${DOCKERFILE_PATH:-Not set}"
}

# Function to detect project structure
detect_project_structure() {
    print_info "🔍 Detecting project structure..."
    
    # Check if we have a Makefile
    if [[ ! -f "Makefile" ]]; then
        print_error "No Makefile found in current directory"
        return 1
    fi
    
    # Find source directories
    local detected_src_dirs=()
    for possible_dir in src source lib sources; do
        if [[ -d "$possible_dir" ]]; then
            detected_src_dirs+=("$possible_dir")
        fi
    done
    
    if [[ ${#detected_src_dirs[@]} -eq 0 ]]; then
        print_warn "No standard source directories found (src, source, lib, sources)"
        print_warn "Please set SOURCE_DIRS environment variable"
        return 1
    fi
    
    # Use detected directories if SOURCE_DIRS wasn't explicitly set
    if [[ "$SOURCE_DIRS" = "src" ]]; then
        SOURCE_DIRS="${detected_src_dirs[*]}"
    fi
    
    print_config "Project: $PROJECT_NAME"
    print_config "Source directories: $SOURCE_DIRS"
    
    # Count source files
    local total_cpp=0
    for src_dir in $SOURCE_DIRS; do
        if [[ -d "$src_dir" ]]; then
            local count=$(find "$src_dir" -name "*.cpp" -o -name "*.cc" -o -name "*.cxx" | wc -l)
            total_cpp=$((total_cpp + count))
        fi
    done
    
    print_config "Total C++ files: $total_cpp"
    
    # Try to detect build variants from Makefile
    if grep -q "cc=" Makefile 2>/dev/null; then
        print_config "Detected multi-platform Makefile (supports cc= parameter)"
        local cc_values=$(grep -o "cc=[0-9]" Makefile | sort -u | tr '\n' ' ')
        print_config "Found cc values: $cc_values"
    else
        print_config "Standard Makefile detected"
    fi
    
    return 0
}

# Function to auto-detect dependencies from Dockerfile
detect_dependencies_from_dockerfile() {
    if [[ -n "$DOCKERFILE_PATH" ]] && [[ -f "$DOCKERFILE_PATH" ]]; then
        print_info "📦 Extracting dependencies from Dockerfile: $DOCKERFILE_PATH"
        
        # Extract apt-get install commands from Dockerfile
        local deps=$(grep -A 50 "apt-get install" "$DOCKERFILE_PATH" | \
                    grep -E "^\s*[a-zA-Z0-9][a-zA-Z0-9.-]*\s*\\\\?\s*$" | \
                    sed 's/\s*\\\s*$//' | \
                    tr '\n' ' ' | \
                    sed 's/[[:space:]]\+/ /g' | \
                    sed 's/--no-install-recommends//g')
        
        if [[ -n "$deps" ]]; then
            print_config "Dockerfile dependencies: $deps"
            echo "bear pkg-config jq $deps"
            return 0
        fi
    fi
    
    # Default minimal dependencies
    echo "build-essential bear pkg-config jq libboost-all-dev"
}

# Function to install minimal host dependencies (Docker + basic tools)
install_host_dependencies() {
    print_info "🔧 Installing minimal host dependencies for container builds..."
    
    # Only install what we need on the host
    local host_deps="docker.io jq"
    
    print_config "Installing host tools: $host_deps"
    
    # Update package lists
    sudo apt-get update
    
    # Install minimal host dependencies
    if sudo DEBIAN_FRONTEND=noninteractive apt-get install -y $host_deps; then
        print_info "✅ Host dependencies installed successfully"
        
        # Check Docker access
        if groups | grep -q docker; then
            print_info "✅ User already in docker group"
        else
            print_warn "⚠️ Adding user to docker group (logout/login required)"
            sudo usermod -a -G docker "$USER"
            print_warn "Please logout and login again, or run: newgrp docker"
        fi
    else
        print_warn "⚠️ Some host dependencies may have failed to install"
        return 1
    fi
    
    # Verify Docker is running
    if sudo systemctl is-active docker >/dev/null 2>&1; then
        print_info "✅ Docker service is running"
    else
        print_info "Starting Docker service..."
        sudo systemctl start docker
    fi
}

# Function to verify build tools (container-focused)
verify_build_tools() {
    print_info "🔍 Verifying build tools (container-based approach)..."
    
    local missing_tools=()
    
    # Check essential host tools
    for tool in docker jq; do
        if ! command -v "$tool" >/dev/null 2>&1; then
            missing_tools+=("$tool")
        else
            print_config "✅ $tool available on host"
        fi
    done
    
    # Check Docker access
    if command -v docker >/dev/null 2>&1; then
        if docker ps >/dev/null 2>&1; then
            print_config "✅ Docker access verified"
        else
            print_warn "⚠️ Docker found but access denied"
            print_warn "You may need to: sudo usermod -a -G docker $USER && newgrp docker"
        fi
    fi
    
    # Check if Docker images are accessible (if specified)
    if [[ -n "$DOCKER_IMAGE_X86" ]]; then
        print_config "x86 Docker image: $DOCKER_IMAGE_X86"
    fi
    
    if [[ -n "$DOCKER_IMAGE_CROSS" ]]; then
        print_config "Cross-compile image: $DOCKER_IMAGE_CROSS"
    fi
    
    if [[ ${#missing_tools[@]} -gt 0 ]]; then
        print_error "❌ Missing required host tools: ${missing_tools[*]}"
        print_error "Please install them manually or run --deps-only"
        return 1
    fi
    
    print_info "✅ Container build environment ready"
    return 0
}

# Function to backup existing files (organized structure)
backup_existing() {
    local backup_suffix="backup.$(date +%Y%m%d_%H%M%S)"
    local backup_dir="$SONAR_OUTPUT_DIR/backups"
    
    # Create backup directory
    mkdir -p "$backup_dir"
    
    # Backup organized structure
    if [[ -d "$SONAR_OUTPUT_DIR" ]]; then
        for variant_dir in "$SONAR_OUTPUT_DIR"/*; do
            if [[ -d "$variant_dir" ]] && [[ "$(basename "$variant_dir")" != "backups" ]]; then
                local variant_name=$(basename "$variant_dir")
                if [[ -f "$variant_dir/compile_commands.json" ]]; then
                    cp "$variant_dir/compile_commands.json" "$backup_dir/compile_commands_${variant_name}.json.$backup_suffix"
                    print_config "📁 Backed up $variant_name variant to $backup_dir/"
                fi
            fi
        done
    fi
    
    # Backup legacy files
    for file in compile_commands_*.json sonar-output-*/compile_commands_*.json; do
        if [[ -f "$file" ]] && [[ ! "$file" =~ "$backup_dir" ]]; then
            local backup_name="$(basename "$file").$backup_suffix"
            cp "$file" "$backup_dir/$backup_name"
            print_config "📁 Backed up legacy file $(basename "$file")"
        fi
    done
}

# Function to generate compile_commands.json for local build
generate_local_variant() {
    local variant="$1"
    local make_args="$2"
    local output_file="$3"
    
    print_info "🔨 Generating $variant compilation database locally..."
    
    # Create output directory
    local output_dir=$(dirname "$output_file")
    mkdir -p "$output_dir"
    
    # Create log file
    local build_log="$output_dir/build_$(date +%Y%m%d_%H%M%S).log"
    
    # Clean and build
    make clean || true
    
    print_info "Running: bear --output $output_file -- make $make_args"
    bear --output "$output_file" -- make $make_args 2>&1 | tee "$build_log"
    
    print_config "📝 Build log: $build_log"
    
    if [[ -f "$output_file" ]]; then
        local entries=$(jq length "$output_file" 2>/dev/null || echo "unknown")
        print_info "✅ Generated $output_file ($entries entries)"
        return 0
    else
        print_error "❌ Failed to generate $output_file"
        return 1
    fi
}

# Function to generate compile_commands.json using Docker (mirrors Makefile approach)
generate_docker_variant() {
    local variant="$1"
    local cc_value="$2"
    local output_file="$3"
    local docker_image="$4"
    local docker_env_cmd="$5"
    
    print_info "🐳 Generating $variant compilation database using Docker (Makefile approach)..."
    
    if [[ -z "$docker_image" ]]; then
        print_error "Docker image not specified for $variant variant"
        return 1
    fi
    
    if ! command -v docker >/dev/null 2>&1; then
        print_error "Docker not available for $variant build"
        return 1
    fi
    
    # Create output directory
    local output_dir=$(dirname "$output_file")
    mkdir -p "$output_dir"
    
    # Create log file
    local build_log="$output_dir/docker_build_$(date +%Y%m%d_%H%M%S).log"
    
    print_config "Using Docker image: $docker_image"
    print_config "Docker environment: $docker_env_cmd"
    print_config "Build variant: cc=$cc_value"
    
    # Get absolute paths
    local project_root="$(pwd)"
    local container_output="/root/$output_file"
    
    # Run Docker build using native compilation (bypass Makefile's docker calls)
    print_info "Executing container build (native compilation)..."
    
    # Determine architecture and environment for native build
    local arch_env=""
    case "$cc_value" in
        "0")
            arch_env="export arch=x86_64"
            ;;
        "1") 
            arch_env="export arch=aarch64 && export CROSS_COMPILE=aarch64-linux-gnu- && $docker_env_cmd"
            ;;
        "2")
            arch_env="export arch=aarch64 && export CROSS_COMPILE=aarch64-linux-gnu- && $docker_env_cmd"
            ;;
    esac
    
    docker run --rm -e HOST_PROJECT_ROOT="$project_root" -v "$project_root:/root" "$docker_image" \
        bash -c "
            echo '=== Container Build Start ==='
            
            # Install bear and jq inside container
            apt-get update >/dev/null 2>&1
            apt-get install -y bear jq >/dev/null 2>&1
            
            # Navigate to project root
            cd /root
            
            # Set architecture and environment
            $arch_env
            
            # Clean build first
            echo 'Cleaning build...'
            \$(which make) clean >/dev/null 2>&1 || true
            
            # Force native build by bypassing cc= docker logic
            # Use the native compilation part of Makefile directly
            echo 'Generating compilation database with native build...'
            
            # We need to call the native build path, not the docker wrapper
            # Set cc= to empty to force native path, but set arch appropriately
            unset cc
            bear --output $container_output.tmp -- \$(which make) -j12
            
            # Fix paths from container (/root) to host paths (HOST_PROJECT_ROOT passed from host)
            if [ -f '$container_output.tmp' ]; then
                echo 'Fixing container paths to host paths...'
                
                # Replace /root with actual host project path from -e HOST_PROJECT_ROOT
                jq --arg host_root \"\$HOST_PROJECT_ROOT\" '
                    map(
                        .file |= sub(\"^/root\"; \$host_root) |
                        .directory |= sub(\"^/root\"; \$host_root)
                    )
                ' '$container_output.tmp' > '$container_output'
                
                rm -f '$container_output.tmp'
                echo \"Paths fixed: /root -> \$HOST_PROJECT_ROOT\"
            else
                echo 'No temporary compilation database found'
                exit 1
            fi
            
            # Verify and report results
            if [ -f '$output_file' ]; then
                entries=\$(jq length '$output_file' 2>/dev/null || echo 'unknown')
                files=\$(jq -r '.[].file' '$output_file' 2>/dev/null | wc -l)
                echo \"SUCCESS: Generated $output_file\"
                echo \"  Entries: \$entries\"
                echo \"  Files: \$files\"
                echo \"  Size: \$(du -h '$output_file' | cut -f1)\"
                
                # Verify paths and show samples
                sample_path=\$(jq -r '.[0].file' '$output_file' 2>/dev/null)
                echo \"  Sample path: \$sample_path\"
                
                if [[ \"\$sample_path\" == \"/root\"* ]]; then
                    echo \"  ⚠️ WARNING: Paths still contain /root - SonarQube won't work\"
                else
                    echo \"  ✅ Paths correctly fixed for SonarQube\"
                fi
                
                # Show sample files
                echo \"  Sample files:\"
                jq -r '.[].file' '$output_file' 2>/dev/null | head -5 | sed 's/^/    /'
                
                # Show directory distribution with fixed paths
                echo \"  Directories covered:\"
                jq -r '.[].file' '$output_file' 2>/dev/null | sed \"s|\$HOST_PROJECT_ROOT/||\" | cut -d'/' -f1-2 | sort | uniq -c | sort -nr | head -5 | sed 's/^/    /'
            else
                echo \"FAILED: Could not generate $output_file\"
                echo \"Checking for any compilation files...\"
                find /root -name 'compile_commands*.json' -o -name '*.o' | head -10
                exit 1
            fi
            
            echo '=== Container Build Complete ==='
        " 2>&1 | tee "$build_log"
    
    local docker_exit_code=${PIPESTATUS[0]}
    
    print_config "📝 Build log: $build_log"
    
    if [[ "$docker_exit_code" -eq 0 ]] && [[ -f "$output_file" ]]; then
        local entries=$(jq length "$output_file" 2>/dev/null || echo "unknown")
        local files=$(jq -r '.[].file' "$output_file" 2>/dev/null | wc -l)
        print_info "✅ Generated $output_file ($entries entries, $files unique files)"
        return 0
    else
        print_error "❌ Docker build failed for $variant (exit code: $docker_exit_code)"
        print_error "Check build log: $build_log"
        return 1
    fi
}

# Function to auto-detect Docker images from Makefile
detect_docker_images() {
    if [[ -f "Makefile" ]]; then
        # Extract Docker images from Makefile more precisely
        local x86_image=$(grep -A5 "cc.*0" Makefile | grep "docker run" | head -1 | sed 's/.*docker run[^:]*://' | awk '{print $1}')
        local cross_image=$(grep -A5 "cc.*1" Makefile | grep "docker run" | head -1 | sed 's/.*docker run[^:]*://' | awk '{print $1}')
        
        if [[ -n "$x86_image" ]] && [[ -z "$DOCKER_IMAGE_X86" ]]; then
            export DOCKER_IMAGE_X86="$x86_image"
            print_config "Auto-detected x86 image: $DOCKER_IMAGE_X86"
        fi
        
        if [[ -n "$cross_image" ]] && [[ -z "$DOCKER_IMAGE_CROSS" ]]; then
            export DOCKER_IMAGE_CROSS="$cross_image"
            print_config "Auto-detected cross-compile image: $DOCKER_IMAGE_CROSS"
        fi
    fi
}

# Function to test Docker connectivity
test_docker_images() {
    print_info "🧪 Testing Docker image accessibility..."
    
    if [[ -n "$DOCKER_IMAGE_X86" ]]; then
        print_config "Testing x86 image: $DOCKER_IMAGE_X86"
        if docker run --rm "$DOCKER_IMAGE_X86" echo "x86 image accessible" 2>/dev/null; then
            print_info "✅ x86 Docker image accessible"
        else
            print_warn "⚠️ x86 Docker image not accessible (may need NVIDIA container registry login)"
        fi
    fi
    
    if [[ -n "$DOCKER_IMAGE_CROSS" ]]; then
        print_config "Testing cross-compile image: $DOCKER_IMAGE_CROSS"  
        if docker run --rm "$DOCKER_IMAGE_CROSS" echo "cross-compile image accessible" 2>/dev/null; then
            print_info "✅ Cross-compile Docker image accessible"
        else
            print_warn "⚠️ Cross-compile Docker image not accessible"
        fi
    fi
}

# Function to generate for all requested variants
generate_variants() {
    local variants=("$@")
    local successful=()
    local failed=()
    
    # Auto-detect Docker images if not set
    detect_docker_images
    
    for variant in "${variants[@]}"; do
        case "$variant" in
            "x86"|"local"|"cc0")
                # Always use Docker for x86 (mirrors Makefile approach)
                local docker_img="$DOCKER_IMAGE_X86"
                if [[ -z "$docker_img" ]]; then
                    # Auto-detect from Makefile if not set
                    docker_img=$(grep -A3 "cc.*0" Makefile | grep "docker run" | sed 's/.*docker run[^:]*://' | cut -d' ' -f1 | head -1)
                fi
                
                if [[ -n "$docker_img" ]]; then
                    print_info "Using Docker for x86 build: $docker_img"
                    local output_path="$SONAR_OUTPUT_DIR/x86/compile_commands.json"
                    if generate_docker_variant "x86" "0" "$output_path" "$docker_img" ""; then
                        successful+=("x86")
                    else
                        failed+=("x86")
                    fi
                else
                    print_error "No Docker image found for x86 variant"
                    failed+=("x86")
                fi
                ;;
                
            "cc1"|"thor")
                # Always use Docker for Thor (mirrors Makefile)
                local docker_img="$DOCKER_IMAGE_CROSS"
                if [[ -z "$docker_img" ]]; then
                    # Auto-detect from Makefile
                    docker_img=$(grep -A3 "cc.*1" Makefile | grep "docker run" | sed 's/.*docker run[^:]*://' | cut -d' ' -f1 | head -1)
                fi
                
                if [[ -n "$docker_img" ]]; then
                    print_info "Using Docker for Thor build: $docker_img"
                    local output_path="$SONAR_OUTPUT_DIR/thor/compile_commands.json"
                    if generate_docker_variant "thor" "1" "$output_path" "$docker_img" "unset SBSA_PLATFORM"; then
                        successful+=("thor")
                    else
                        failed+=("thor")
                    fi
                else
                    print_error "No Docker image found for Thor variant"
                    print_error "Set DOCKER_IMAGE_CROSS or ensure Makefile has cc=1 Docker command"
                    failed+=("thor")
                fi
                ;;
                
            "cc2"|"sbsa")
                # Always use Docker for SBSA (mirrors Makefile)
                local docker_img="$DOCKER_IMAGE_CROSS"
                if [[ -z "$docker_img" ]]; then
                    # Auto-detect from Makefile
                    docker_img=$(grep -A3 "cc.*2" Makefile | grep "docker run" | sed 's/.*docker run[^:]*://' | cut -d' ' -f1 | head -1)
                fi
                
                if [[ -n "$docker_img" ]]; then
                    print_info "Using Docker for SBSA build: $docker_img"
                    local output_path="$SONAR_OUTPUT_DIR/sbsa/compile_commands.json"
                    if generate_docker_variant "sbsa" "2" "$output_path" "$docker_img" "export SBSA_PLATFORM=1"; then
                        successful+=("sbsa")
                    else
                        failed+=("sbsa")
                    fi
                else
                    print_error "No Docker image found for SBSA variant"  
                    print_error "Set DOCKER_IMAGE_CROSS or ensure Makefile has cc=2 Docker command"
                    failed+=("sbsa")
                fi
                ;;
                
            *)
                print_error "Unknown variant: $variant"
                print_error "Supported: x86, cc0, cc1/thor, cc2/sbsa"
                failed+=("$variant")
                ;;
        esac
    done
    
    # Report results
    print_info "📊 Generation Results:"
    if [[ ${#successful[@]} -gt 0 ]]; then
        echo "   ✅ Successful: ${successful[*]}"
    fi
    if [[ ${#failed[@]} -gt 0 ]]; then
        echo "   ❌ Failed: ${failed[*]}"
    fi
    
    # Analyze results for each successful variant
    local total_cpp=0
    for src_dir in $SOURCE_DIRS; do
        if [[ -d "$src_dir" ]]; then
            local count=$(find "$src_dir" -name "*.cpp" -o -name "*.cc" -o -name "*.cxx" | wc -l)
            total_cpp=$((total_cpp + count))
        fi
    done
    
    for variant in "${successful[@]}"; do
        local variant_files=($(ls compile_commands_${variant}.json ${variant}/compile_commands_${variant}.json sonar-output-${variant}/compile_commands_${variant}.json 2>/dev/null || true))
        
        for variant_file in "${variant_files[@]}"; do
            if [[ -f "$variant_file" ]]; then
                local captured_files=$(jq -r '.[].file' "$variant_file" 2>/dev/null | wc -l)
                local total_entries=$(jq length "$variant_file" 2>/dev/null || echo "unknown")
                local coverage=$((total_cpp > 0 ? captured_files * 100 / total_cpp : 0))
                
                print_info "📊 $variant variant results:"
                echo "   📝 Files captured: $captured_files/$total_cpp (${coverage}% coverage)"
                echo "   📋 JSON entries: $total_entries"
                echo "   📁 File: $variant_file"
                
                # Show directory distribution for this variant
                if [[ "$captured_files" -gt 0 ]]; then
                    echo "   📂 Top directories:"
                    jq -r '.[].file' "$variant_file" 2>/dev/null | \
                        sed "s|$(pwd)/||" | \
                        cut -d'/' -f1-2 | \
                        sort | uniq -c | sort -nr | head -5 | sed 's/^/      /'
                fi
                break
            fi
        done
    done
    
    return $((${#failed[@]} > 0 ? 1 : 0))
}

# Map variant name (x86, cc0, thor, cc1, sbsa, cc2) to output dir name
_variant_to_dir() {
    case "$1" in
        x86|cc0) echo "x86" ;;
        thor|cc1) echo "thor" ;;
        sbsa|cc2) echo "sbsa" ;;
        *) echo "$1" ;;
    esac
}

# Function to create unified compile_commands.json
# Usage: create_unified_compile_commands [variant1 variant2 ...]
#   With args: only these variants are merged (e.g. "x86" → x86-only unified DB, no aarch64).
#   No args: merge all existing variant DBs (previous behavior).
create_unified_compile_commands() {
    local temp_file="compile_commands_temp.json"
    echo "[]" > "$temp_file"
    
    local found_any=false
    local total_unified=0
    local source_files=()
    
    if [[ $# -ge 1 ]]; then
        # Only from requested variants (e.g. x86 only → avoid aarch64-linux-gnu-g++ in unified DB)
        print_info "🔗 Creating unified compile_commands.json from requested variants: $*..."
        for v in "$@"; do
            local dir_name=$(_variant_to_dir "$v")
            local variant_file="$SONAR_OUTPUT_DIR/$dir_name/compile_commands.json"
            if [[ -f "$variant_file" ]]; then
                source_files+=("$variant_file")
            else
                print_warn "Variant $v ($dir_name) not found at $variant_file, skipping"
            fi
        done
    else
        # Previous behavior: all existing variant DBs
        print_info "🔗 Creating unified compile_commands.json from all variants..."
        local potential_files=(
            "$SONAR_OUTPUT_DIR/x86/compile_commands.json"
            "$SONAR_OUTPUT_DIR/thor/compile_commands.json"
            "$SONAR_OUTPUT_DIR/sbsa/compile_commands.json"
            "compile_commands_x86.json"
            "compile_commands_cc0.json"
            "sonar-output-thor/compile_commands_thor.json"
            "sonar-output-sbsa/compile_commands_sbsa.json"
        )
        for variant_file in "${potential_files[@]}"; do
            if [[ -f "$variant_file" ]]; then
                source_files+=("$variant_file")
            fi
        done
        if [[ -d "$SONAR_OUTPUT_DIR" ]]; then
            for variant_dir in "$SONAR_OUTPUT_DIR"/*; do
                if [[ -d "$variant_dir" ]] && [[ -f "$variant_dir/compile_commands.json" ]]; then
                    local file_path="$variant_dir/compile_commands.json"
                    if [[ ! " ${source_files[*]} " =~ " $file_path " ]]; then
                        source_files+=("$file_path")
                    fi
                fi
            done
        fi
    fi
    
    # Remove duplicates
    local unique_files=($(printf '%s\n' "${source_files[@]}" | sort -u))
    
    print_config "Variant databases to merge: ${unique_files[*]}"
    
    # Combine all files
    for variant_file in "${unique_files[@]}"; do
        local variant_name=$(basename "$variant_file" | sed 's/compile_commands_//' | sed 's/.json//')
        local variant_entries=$(jq length "$variant_file" 2>/dev/null || echo "0")
        
        print_config "Including $variant_name ($variant_entries entries)"
        
        if [[ "$found_any" = false ]]; then
            cp "$variant_file" "$temp_file"
            found_any=true
            total_unified=$variant_entries
        else
            # Merge JSON arrays with deduplication by file path
            if command -v jq >/dev/null 2>&1; then
                jq -s 'add | unique_by(.file)' "$temp_file" "$variant_file" > "${temp_file}.new"
                mv "${temp_file}.new" "$temp_file"
                total_unified=$(jq length "$temp_file" 2>/dev/null || echo "unknown")
            fi
        fi
    done
    
    if [[ "$found_any" = true ]]; then
        # Create unified directory and move file there
        mkdir -p "$UNIFIED_DIR"
        mv "$temp_file" "$UNIFIED_DIR/compile_commands.json"
        
        # Fix container paths (/root) to host paths in unified database (during compilation flow)
        local project_root="$(pwd)"
        print_config "Fixing container paths to host paths in unified database..."
        jq --arg host_root "$project_root" '
            map(
                .file |= sub("^/root"; $host_root) |
                .directory |= sub("^/root"; $host_root)
            )
        ' "$UNIFIED_DIR/compile_commands.json" > "${UNIFIED_DIR}/compile_commands.json.tmp"
        mv "${UNIFIED_DIR}/compile_commands.json.tmp" "$UNIFIED_DIR/compile_commands.json"
        
        print_info "✅ Created unified compilation database ($total_unified unique entries)"
        print_config "📁 Unified database: $UNIFIED_DIR/compile_commands.json"
        
        # Show final distribution
        print_info "📂 Unified database directory coverage:"
        jq -r '.[].file' "$UNIFIED_DIR/compile_commands.json" 2>/dev/null | \
            sed "s|$(pwd)/||" | \
            cut -d'/' -f1-2 | \
            sort | uniq -c | sort -nr | head -10 | sed 's/^/   /'
    else
        print_error "❌ No variant compilation databases found to unify"
        rm -f "$temp_file"
        return 1
    fi
}

# Function to show current status with organized structure
show_status() {
    print_info "📊 Current Status for $PROJECT_NAME:"
    
    local found_any=false
    
    # Check unified database
    if [[ -f "$UNIFIED_DIR/compile_commands.json" ]]; then
        local entries=$(jq length "$UNIFIED_DIR/compile_commands.json" 2>/dev/null || echo "unknown")
        local file_size=$(du -h "$UNIFIED_DIR/compile_commands.json" | cut -f1)
        print_info "✅ Unified database: $entries entries ($file_size)"
        print_config "📁 $UNIFIED_DIR/compile_commands.json"
        found_any=true
    fi
    
    # Check organized variant directories
    if [[ -d "$SONAR_OUTPUT_DIR" ]]; then
        print_info "📂 Organized compilation databases:"
        for variant_dir in "$SONAR_OUTPUT_DIR"/*; do
            if [[ -d "$variant_dir" ]] && [[ -f "$variant_dir/compile_commands.json" ]]; then
                local variant_name=$(basename "$variant_dir")
                local entries=$(jq length "$variant_dir/compile_commands.json" 2>/dev/null || echo "unknown")
                local file_size=$(du -h "$variant_dir/compile_commands.json" | cut -f1)
                print_info "   ✅ $variant_name: $entries entries ($file_size)"
                found_any=true
            fi
        done
    fi
    
    # Check legacy locations for backward compatibility
    print_info "📂 Legacy files (if any):"
    local legacy_found=false
    for variant_file in compile_commands_*.json sonar-output-*/compile_commands_*.json; do
        if [[ -f "$variant_file" ]]; then
            local entries=$(jq length "$variant_file" 2>/dev/null || echo "unknown")
            print_config "   📄 $variant_file ($entries entries)"
            legacy_found=true
        fi
    done
    
    if [[ "$legacy_found" = false ]]; then
        print_config "   (None - all organized in $SONAR_OUTPUT_DIR/)"
    fi
    
    if [[ "$found_any" = false ]]; then
        print_warn "❌ No compilation databases found"
        echo "   Run: $0 --all --unified"
    else
        # Validate paths in unified database if it exists
        if [[ -f "$UNIFIED_DIR/compile_commands.json" ]]; then
            local sample_path=$(jq -r '.[0].file' "$UNIFIED_DIR/compile_commands.json" 2>/dev/null)
            if [[ "$sample_path" == "/root"* ]]; then
                print_warn "⚠️ ISSUE: Paths in unified database use container paths (/root)"
                print_warn "   SonarQube won't work with these paths"
                print_warn "   Run: $0 --fix-paths to correct them"
            else
                print_info "✅ Paths in unified database are correct for host"
            fi
        fi
    fi
    
    # Show total source files for comparison
    local total_cpp=0
    for src_dir in $SOURCE_DIRS; do
        if [[ -d "$src_dir" ]]; then
            local count=$(find "$src_dir" -name "*.cpp" -o -name "*.cc" -o -name "*.cxx" | wc -l)
            total_cpp=$((total_cpp + count))
        fi
    done
    print_config "📊 Total project C++ files: $total_cpp"
    
    # Check workspace configuration if it exists
    local workspace_configs=(".vscode/settings.json" "../.vscode/settings.json")
    for config in "${workspace_configs[@]}"; do
        if [[ -f "$config" ]]; then
            print_info "⚙️ Workspace config: $config"
            local compile_path=$(jq -r '.["sonarlint.pathToCompileCommands"] // "not-set"' "$config" 2>/dev/null)
            if [[ "$compile_path" != "not-set" ]] && [[ "$compile_path" != "null" ]]; then
                print_config "   SonarLint path: $compile_path"
            fi
            break
        fi
    done
}

# Function to show manual steps
show_manual_steps() {
    cat << EOF

📝 Manual Steps for Any C++ Project:

===== Environment Variables (Optional) =====
export PROJECT_NAME="my_project"
export SOURCE_DIRS="src lib"  # Space-separated source directories
export DOCKER_IMAGE_CROSS="your-cross-compiler-image"
export DOCKERFILE_PATH="path/to/Dockerfile"  # For auto dependency detection
export MAKE_PARALLEL_JOBS="8"

===== Manual Commands (Container-Based) =====

1. Install Host Dependencies:
   sudo apt-get update
   sudo apt-get install -y docker.io jq
   sudo usermod -a -G docker \$USER  # Add user to docker group
   newgrp docker  # Or logout/login

2. Generate for x86 (cc=0) using Docker:
   mkdir -p sonar-databases/x86
   docker run --rm -v \$(pwd):/root YOUR_X86_DOCKER_IMAGE \\
       bash -c "apt-get update && apt-get install -y bear jq && \\
                cd /root && make clean && \\
                bear --output sonar-databases/x86/compile_commands.json -- make -j12"

3. Generate for Thor (cc=1) using Docker:
   mkdir -p sonar-databases/thor
   docker run --rm -v \$(pwd):/root YOUR_CROSS_DOCKER_IMAGE \\
       bash -c "apt-get update && apt-get install -y bear jq && \\
                cd /root && make clean && unset SBSA_PLATFORM && \\
                bear --output sonar-databases/thor/compile_commands.json -- make -j12"

4. Generate for SBSA (cc=2) using Docker:
   mkdir -p sonar-databases/sbsa
   docker run --rm -v \$(pwd):/root YOUR_CROSS_DOCKER_IMAGE \\
       bash -c "apt-get update && apt-get install -y bear jq && \\
                cd /root && make clean && export SBSA_PLATFORM=1 && \\
                bear --output sonar-databases/sbsa/compile_commands.json -- make -j12"

5. Create Organized Unified Database:
   mkdir -p sonar-databases/unified
   jq -s 'add | unique_by(.file)' \\
       sonar-databases/*/compile_commands.json > sonar-databases/unified/compile_commands.json

6. Verify results:
   jq length sonar-databases/unified/compile_commands.json
   echo "Files: \$(jq -r '.[].file' sonar-databases/unified/compile_commands.json | wc -l)"
   echo "Expected: \$(find src/ -name '*.cpp' | wc -l) cpp files"

EOF
}

# Parse command line arguments
VARIANTS=()
UNIFIED=false
DEPS_ONLY=false
GENERATE_ONLY=false
FORCE=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --help|-h)
            echo "Generic Container-Based compile_commands.json Generator for C++ Projects"
            echo ""
            echo "USAGE:"
            echo "  $0 [OPTIONS] [VARIANTS]"
            echo ""
            echo "This script uses Docker containers (like your Makefile) to avoid host dependency issues."
            echo ""
            echo "OPTIONS:"
            echo "  --all, -a             Generate for all variants (x86, cc1, cc2)"
            echo "  --unified, -u         Create unified compile_commands.json from requested variants only"
            echo "  --deps-only           Only install minimal host dependencies (Docker + jq)"
            echo "  --generate-only       Only generate (assume Docker available)"
            echo "  --force, -f           Skip confirmation prompts (container-only mode)"
            echo "  --fix-paths           Fix existing databases with wrong container paths"
            echo "  --config              Show current configuration"
            echo "  --status              Show current status"
            echo "  --manual              Show manual container commands"
            echo "  --help, -h            Show this help"
            echo ""
            echo "VARIANTS:"
            echo "  x86, cc0              x86 build using container (cc=0)"
            echo "  cc1, thor             Thor build using container (cc=1)"
            echo "  cc2, sbsa             SBSA build using container (cc=2)"
            echo ""
            echo "ENVIRONMENT VARIABLES:"
            echo "  PROJECT_NAME          Project name (default: current directory name)"
            echo "  SOURCE_DIRS           Source directories (default: 'src')"
            echo "  SONAR_OUTPUT_DIR      Output directory for organized databases (default: 'sonar-databases')"
            echo "  UNIFIED_DIR           Unified database directory (default: 'sonar-databases/unified')"
            echo "  DOCKER_IMAGE_X86      Docker image for x86 builds"
            echo "  DOCKER_IMAGE_CROSS    Docker image for cross-compilation (cc=1,2)"
            echo "  DOCKERFILE_PATH       Path to Dockerfile for dependency detection"
            echo "  MAKE_PARALLEL_JOBS    Container make jobs (default: 12)"
            echo ""
            echo "EXAMPLES:"
            echo "  $0 --all --unified              # All platforms + unified"
            echo "  $0 x86 --unified                 # x86 only + unified (avoids aarch64 compiler in DB)"
            echo "  $0 x86 --force                  # x86 only, no confirmation"
            echo "  $0 cc1 cc2 --force              # Cross-compile variants only, automated"
            echo "  $0 --config                     # Show current setup"
            echo "  $0 --status                     # Check existing databases"
            echo ""
            echo "ORGANIZED OUTPUT STRUCTURE:"
            echo "  sonar-databases/"
            echo "  ├── x86/compile_commands.json       # x86 platform compilation database"
            echo "  ├── thor/compile_commands.json      # Thor platform compilation database"  
            echo "  ├── sbsa/compile_commands.json      # SBSA platform compilation database"
            echo "  ├── unified/compile_commands.json   # Combined database (for SonarQube)"
            echo "  └── backups/                        # Automatic backups"
            echo ""
            echo "CONTAINER SETUP:"
            echo "  DOCKER_IMAGE_X86=my-x86-image:tag DOCKER_IMAGE_CROSS=my-cross-image:tag $0 --all"
            echo ""
            show_manual_steps
            exit 0
            ;;
        --all|-a)
            VARIANTS=("x86" "cc1" "cc2")
            shift
            ;;
        --unified|-u)
            UNIFIED=true
            shift
            ;;
        --deps-only)
            DEPS_ONLY=true
            shift
            ;;
        --generate-only)
            GENERATE_ONLY=true
            shift
            ;;
        --force|-f)
            FORCE=true
            shift
            ;;
        --config)
            detect_project_structure
            show_configuration
            exit 0
            ;;
        --status)
            show_status
            exit 0
            ;;
        --manual)
            show_manual_steps
            exit 0
            ;;
        --fix-paths)
            # Fix existing databases with wrong paths
            print_info "🔧 Fixing paths in existing compilation databases..."
            
            project_root="$(pwd)"
            fixed_any=false
            
            # Fix organized databases
            if [[ -d "$SONAR_OUTPUT_DIR" ]]; then
                for variant_dir in "$SONAR_OUTPUT_DIR"/*; do
                    if [[ -d "$variant_dir" ]] && [[ "$(basename "$variant_dir")" != "backups" ]]; then
                        variant_file="$variant_dir/compile_commands.json"
                        if [[ -f "$variant_file" ]]; then
                            variant_name=$(basename "$variant_dir")
                            print_info "Fixing $variant_name database..."
                            jq --arg host_root "$project_root" '
                                map(
                                    .file |= sub("^/root"; $host_root) |
                                    .directory |= sub("^/root"; $host_root)
                                )
                            ' "$variant_file" > "${variant_file}.tmp"
                            
                            mv "${variant_file}.tmp" "$variant_file"
                            print_info "✅ Fixed $variant_name database"
                            fixed_any=true
                        fi
                    fi
                done
            fi
            
            if [[ "$fixed_any" = true ]]; then
                print_info "🎉 Path fixing complete! Restart Cursor to use fixed databases."
            else
                print_warn "No databases found to fix"
            fi
            
            exit 0
            ;;
        x86|local|cc0|cc1|thor|cc2|sbsa)
            VARIANTS+=("$1")
            shift
            ;;
        *)
            print_error "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Handle specific modes
if [[ "$DEPS_ONLY" = true ]]; then
    detect_project_structure
    install_host_dependencies
    verify_build_tools
    exit 0
fi

if [[ "$GENERATE_ONLY" = true ]]; then
    # For container builds, we only need Docker and jq on host
    if ! command -v docker >/dev/null 2>&1; then
        print_error "Docker not available - required for container builds"
        exit 1
    fi
    
    if ! command -v jq >/dev/null 2>&1; then
        print_error "jq not available - please install: sudo apt-get install jq"
        exit 1
    fi
    
    # Use provided variants or default to x86
    if [[ ${#VARIANTS[@]} -eq 0 ]]; then
        VARIANTS=("x86")
    fi
    
    detect_project_structure
    backup_existing
    generate_variants "${VARIANTS[@]}"
    
    if [[ "$UNIFIED" = true ]]; then
        create_unified_compile_commands "${VARIANTS[@]}"
    fi
    
    exit 0
fi

# Main execution
main() {
    # Set default variants if none specified
    if [[ ${#VARIANTS[@]} -eq 0 ]]; then
        VARIANTS=("x86")  # Default to x86 only
    fi
    
    print_info "===== Generic C++ Compilation Database Generator ====="
    
    # Detect project structure
    detect_project_structure || exit 1
    
    # Auto-detect Docker images from Makefile if not set
    detect_docker_images
    
    # Show configuration
    show_configuration
    
    # Test Docker images if available
    if [[ -n "$DOCKER_IMAGE_X86" ]] || [[ -n "$DOCKER_IMAGE_CROSS" ]]; then
        test_docker_images
    fi
    
    print_info "Variants to generate: ${VARIANTS[*]}"
    print_info "Unified database: $([[ "$UNIFIED" = true ]] && echo "Yes" || echo "No")"
    print_info "Build method: Container-based (mirrors Makefile)"
    echo ""
    
    # Skip confirmation if --force flag is used
    if [[ "$FORCE" = false ]]; then
        read -p "Continue with container-based generation? (y/N): " confirm
        if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
            print_info "Setup cancelled. Use --manual to see manual steps."
            exit 0
        fi
    else
        print_info "🚀 Force mode enabled - proceeding with container-based generation..."
    fi
    
    # Verify minimal tools (Docker + jq)
    print_info "🔧 Verifying container build requirements..."
    
    if ! command -v docker >/dev/null 2>&1; then
        print_error "Docker not found. Installing Docker..."
        install_host_dependencies
    fi
    
    if ! command -v jq >/dev/null 2>&1; then
        print_info "Installing jq..."
        sudo apt-get update && sudo apt-get install -y jq
    fi
    
    verify_build_tools || exit 1
    
    # Backup existing files
    backup_existing
    
    # Generate compilation databases for specified variants
    print_info "🏗️ Generating compilation databases..."
    generate_variants "${VARIANTS[@]}"
    
    # Create unified database if requested (only from variants we just built, e.g. x86-only)
    if [[ "$UNIFIED" = true ]]; then
        create_unified_compile_commands "${VARIANTS[@]}"
    fi
    
    # Final status
    echo ""
    show_status
    echo ""
    print_info "🎉 Generation Complete!"
    
    # Show what was created in organized structure
    echo "📁 Generated files (organized structure):"
    
    # Check organized variant files
    for variant in "${VARIANTS[@]}"; do
        local variant_path=""
        case "$variant" in
            "x86"|"cc0") variant_path="$SONAR_OUTPUT_DIR/x86/compile_commands.json" ;;
            "thor"|"cc1") variant_path="$SONAR_OUTPUT_DIR/thor/compile_commands.json" ;;
            "sbsa"|"cc2") variant_path="$SONAR_OUTPUT_DIR/sbsa/compile_commands.json" ;;
        esac
        
        if [[ -n "$variant_path" ]] && [[ -f "$variant_path" ]]; then
            local entries=$(jq length "$variant_path" 2>/dev/null || echo "unknown")
            local size=$(du -h "$variant_path" | cut -f1)
            echo "   ✅ $variant_path ($entries entries, $size)"
        fi
    done
    
    # Check unified files
    if [[ "$UNIFIED" = true ]]; then
        if [[ -f "$UNIFIED_DIR/compile_commands.json" ]]; then
            local entries=$(jq length "$UNIFIED_DIR/compile_commands.json" 2>/dev/null || echo "unknown")
            local size=$(du -h "$UNIFIED_DIR/compile_commands.json" | cut -f1)
            echo "   ✅ $UNIFIED_DIR/compile_commands.json (unified database: $entries entries, $size)"
        fi
    fi
    
    # Show folder structure
    if [[ -d "$SONAR_OUTPUT_DIR" ]]; then
        echo ""
        print_info "📂 Folder structure:"
        tree "$SONAR_OUTPUT_DIR" 2>/dev/null || {
            echo "   $SONAR_OUTPUT_DIR/"
            for subdir in "$SONAR_OUTPUT_DIR"/*; do
                if [[ -d "$subdir" ]]; then
                    echo "   ├── $(basename "$subdir")/"
                    if [[ -f "$subdir/compile_commands.json" ]]; then
                        echo "   │   └── compile_commands.json"
                    fi
                fi
            done
        }
    fi
    
    echo ""
    print_info "💡 Usage Tips:"
    echo "   • For SonarQube: Use the unified database in $UNIFIED_DIR/"
    echo "   • For variant-specific analysis: Use individual variant files"
    echo "   • To regenerate: Run this script again with same options"
    echo "   • To switch variants: Use individual files in your IDE settings"
}

# Run main function if not handled by argument parsing
main