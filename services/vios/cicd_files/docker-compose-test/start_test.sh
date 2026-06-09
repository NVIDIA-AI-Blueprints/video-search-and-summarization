#!/bin/bash

# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

set -euo pipefail # Exit on error, undefined vars, and pipe failures

# Function to display the error messages
error() {
    echo "ERROR: $1" >&2
    exit 1
}

# Function to display info messages
info() {
    echo "INFO: $1"
}

# Function to print separator
separator() {
    echo "----------------------------------------"
}

# Function to display usage
usage() {
    echo "Usage: $0 [--build-only | --test-only | --build-and-test] [--arch=<arch>] [--version=<tag>] [--<container>-version=<tag>]"
    echo "Options:"
    echo "  --build-only              Run only the build step"
    echo "  --test-only               Run only the test step (skip build)"
    echo "  --build-and-test          Run both build and test steps (default)"
    echo "  --arch=<arch>             Specify architecture (x86_64/amd64, sbsa, or arm64/aarch64, default: x86_64/amd64)"
    echo ""
    echo "Version options (if no version flags are given, all containers default to 'latest'):"
    echo "  --version=<tag>           Set image tag for all containers"
    echo "  --sensor-version=<tag>    Override sensor image tag"
    echo "  --streamprocessor-version=<tag> Override stream-processor image tag"
    echo "  --nvstreamer-version=<tag> Override nvstreamer image tag"
    echo ""
    echo "Individual --<container>-version flags take precedence over --version."
    echo "When any version flag is given, unspecified containers preserve their existing tags."
    echo ""
    echo "Examples:"
    echo "  $0 --test-only                                        # Test all containers at 'latest' (default)"
    echo "  $0 --test-only --version=2.1.0-1.0                    # Test all containers at version 2.1.0-1.0"
    echo "  $0 --build-and-test --version=latest                  # Build and test all containers at 'latest'"
    echo "  $0 --test-only --version=latest --sensor-version=2.0.0-1.0  # All at 'latest', sensor at 2.0.0-1.0"
    echo "  $0 --test-only --streamprocessor-version=2.1.0-1.0    # Override stream-processor only"
    echo "  $0 --build-only --arch=aarch64                        # Build only for aarch64 (no version changes)"
    exit 1
}

# Get the current IP address and make it globally available
CURRENT_IP=$(hostname -I | awk '{print $1}')
if [[ -z "$CURRENT_IP" ]]; then
    echo "ERROR: Failed to get system IP address" >&2
    exit 1
fi
export CURRENT_IP
info "Using system IP address: $CURRENT_IP"

# Get Docker group ID for container stats monitoring
DOCKER_GID=$(getent group docker | cut -d: -f3 2>/dev/null || echo "999")
export DOCKER_GID
info "Using Docker group ID: $DOCKER_GID"

# Function to install jq based on the OS
install_jq() {
    info "Installing jq..."

    # Detect OS
    if [[ -f /etc/os-release ]]; then
        . /etc/os-release
        OS=$ID
    elif [[ -f /etc/debian_version ]]; then
        OS="debian"
    else
        error "Unsupported operating system"
    fi

    # Install based on OS
    case $OS in
    "ubuntu" | "debian")
        if ! command -v apt-get &>/dev/null; then
            error "apt-get not found. Please install jq manually"
        fi
        sudo apt-get update && sudo apt-get install -y jq
        ;;
    *)
        error "Unsupported operating system: $OS"
        ;;
    esac

    # Verify installation
    if ! command -v jq &>/dev/null; then
        error "Failed to install jq"
    fi

    info "Successfully installed jq"
}

validate_integer() {
    local num=$1
    local name=$2
    if [[ ! $num =~ ^[0-9]+$ ]]; then
        error "$name must be a positive integer: $num"
    fi
}

# Function to validate paths
validate_path() {
    local path=$1
    local name=$2
    local dir=$(dirname "$path")

    if [[ ! -d "$dir" ]]; then
        error "Directory does not exist for $name: $dir"
    fi
}

remove_container() {
    local name=$1
    info "Attempting to remove container: $name"
    
    # Try to stop and remove the container
    docker stop "$name" 2>/dev/null || true
    sleep 1
    docker rm -f "$name" 2>/dev/null || true
    
    # If still exists, try more aggressive removal
    if docker ps -a | grep -q "$name"; then
        info "Container still exists, trying aggressive removal..."
        docker kill "$name" 2>/dev/null || true
        sleep 1
        docker rm -f --volumes "$name" 2>/dev/null || true
    fi
}

# Function for cleanup
cleanup() {
    echo "Performing cleanup..."
    
    # Stop and remove all containers from docker-compose files
    info "Stopping all Docker Compose services..."
    
    # Stop main docker-compose services
    if [[ -d "$VST_BASE_PATH" ]]; then
        cd "$VST_BASE_PATH" || error "Failed to change directory to VST_BASE_PATH"
        info "Stopping containers from main docker-compose..."
        if [[ -f docker-compose.test.yaml ]]; then
            docker compose -f docker-compose.yaml -f docker-compose.test.yaml --env-file ./compose.env down --remove-orphans --volumes || info "Failed to stop main docker-compose services"
        else
            docker compose -f docker-compose.yaml --env-file ./compose.env down --remove-orphans --volumes || info "Failed to stop main docker-compose services"
        fi
        sleep 2
    fi

    # Stop nvstreamer services
    if [[ -d "$NVSTREAMER_BASE_PATH" ]]; then
        cd "$NVSTREAMER_BASE_PATH" || error "Failed to change directory to NVSTREAMER_BASE_PATH"
        info "Stopping containers from nvstreamer docker-compose..."
        docker compose --env-file ./compose.env down --remove-orphans --volumes || info "Failed to stop nvstreamer services"
        sleep 2
    fi
    
    # Remove bdd_test container
    info "Removing bdd_test container..."
    remove_container "bdd_test"
    
    # Wait for container removal
    local max_attempts=5
    local attempt=1
    
    while [[ $attempt -le $max_attempts ]]; do
        if ! docker ps -a | grep -q "bdd_test"; then
            info "Container bdd_test successfully removed"
            return 0
        fi
        
        info "Container still exists, waiting... (attempt $attempt/$max_attempts)"
        sleep 2
        attempt=$((attempt + 1))
    done
    
    # Print diagnostic information if container still exists
    if docker ps -a | grep -q "bdd_test"; then
        info "Container removal failed. Printing diagnostic information:"
        
        # Print all running containers
        info "All running containers:"
        docker ps -a || true
        
        # Print detailed info about bdd_test container
        info "Detailed info about bdd_test container:"
        docker inspect bdd_test || true
        
        # Print container logs
        info "Container logs:"
        docker logs bdd_test 2>/dev/null || true
        
        # Print container processes
        info "Container processes:"
        docker top bdd_test 2>/dev/null || true
        
        # Print system resources
        info "System resources:"
        free -h || true
        df -h || true
        
        # Print docker info
        info "Docker system info:"
        docker info || true
        
        # Print docker system df
        info "Docker disk usage:"
        docker system df || true
        
        # Continue execution despite the error
        info "Continuing despite container removal failure..."
    fi
    
    return 0
}

# Function to update RTSP streams JSON
update_rtsp_streams_json() {
    # Create temporary file
    local temp_file
    temp_file=$(mktemp) || error "Failed to create temporary file"

    if ! cp "$RTSP_STREAMS_JSON" "${RTSP_STREAMS_JSON}.bak"; then
        rm -f "$temp_file"
        error "Failed to create backup"
    fi

    local jq_filter='.Nvstreamer = (.Nvstreamer | map(
        if .endpoint == "<IP>:<PORT>" then
            . + {"enabled": false}
        else
            .
        end
    ))'

    for ((i = 0; i < NVSTREAMER_COUNT; i++)); do
        local port=$((NVSTREAMER_PORT_BASE + i))
        jq_filter="$jq_filter | .Nvstreamer[$i] |= (. + {\"enabled\": true, \"endpoint\": \"$CURRENT_IP:$port\"})"
    done

    if ! jq "$jq_filter" "$RTSP_STREAMS_JSON" >"$temp_file"; then
        rm -f "$temp_file"
        error "Failed to update JSON file"
    fi

    if ! jq empty "$temp_file" 2>/dev/null; then
        rm -f "$temp_file"
        error "Generated invalid JSON"
    fi

    if ! mv "$temp_file" "$RTSP_STREAMS_JSON"; then
        rm -f "$temp_file"
        error "Failed to save changes"
    fi

    info "Successfully updated $RTSP_STREAMS_JSON"
    info "Backup saved as ${RTSP_STREAMS_JSON}.bak"
}

# Function to update VST config
update_vst_config() {
    # Create temporary file for VST config
    local temp_vst_file
    temp_vst_file=$(mktemp) || error "Failed to create temporary file for VST config"

    if ! cp "$VST_CONFIG_JSON" "${VST_CONFIG_JSON}.bak"; then
        rm -f "$temp_vst_file"
        error "Failed to create backup of VST config"
    fi

    local vst_jq_filter='.network.enable_grpc = true | .notifications.redis_server_env_var = "'$CURRENT_IP':6379"'

    if ! jq "$vst_jq_filter" "$VST_CONFIG_JSON" >"$temp_vst_file"; then
        rm -f "$temp_vst_file"
        error "Failed to update VST config file"
    fi

    if ! jq empty "$temp_vst_file" 2>/dev/null; then
        rm -f "$temp_vst_file"
        error "Generated invalid VST config JSON"
    fi

    if ! mv "$temp_vst_file" "$VST_CONFIG_JSON"; then
        rm -f "$temp_vst_file"
        error "Failed to save VST config changes"
    fi

    info "Successfully updated $VST_CONFIG_JSON"
    info "Backup saved as ${VST_CONFIG_JSON}.bak"
}

# Function to update nginx configuration
update_nginx_config() {
    # Swap nginx configuration files
    local nginx_dir="$VST_BASE_PATH/configs"
    if [[ ! -d "$nginx_dir" ]]; then
        error "Nginx config directory not found: $nginx_dir"
    fi

    if [[ ! -f "$nginx_dir/nginx.conf" ]] || [[ ! -f "$nginx_dir/nginx_ssl.conf" ]]; then
        error "Required nginx configuration files not found"
    fi

    # Backup original nginx.conf
    cp "$nginx_dir/nginx.conf" "$nginx_dir/nginx.conf.bak" || error "Failed to backup nginx.conf"

    # Perform the swap
    mv "$nginx_dir/nginx_ssl.conf" "$nginx_dir/nginx.conf" || error "Failed to swap nginx configuration files"

    info "Successfully swapped nginx configuration files"
    info "Backup saved as ${nginx_dir}/nginx.conf.bak"
}

# Function to update compose.env file
update_compose_env() {
    # Update compose.env file
    local compose_env_file="$VST_BASE_PATH/compose.env"
    if [[ ! -f "$compose_env_file" ]]; then
        error "compose.env file not found: $compose_env_file"
    fi

    # Create backup of compose.env
    cp "$compose_env_file" "${compose_env_file}.bak" || error "Failed to backup compose.env"

    # Create temporary file
    local temp_compose_file
    temp_compose_file=$(mktemp) || error "Failed to create temporary file for compose.env"

    # Build sed args: always update paths/IP, conditionally update image tags
    local -a sed_args=(
        -e "s|^HOST_IP=.*|HOST_IP=$CURRENT_IP|"
        -e "s|^VST_CONFIG_PATH=.*|VST_CONFIG_PATH=$VST_CONFIG_PATH|"
        -e "s|^VST_VOLUME=.*|VST_VOLUME=$VST_VOLUME|"
    )
    [[ -n "$SENSOR_VERSION" ]]           && sed_args+=(-e "s|^\(VST_SENSOR_IMAGE=.*:\).*|\1${SENSOR_VERSION}|")
    [[ -n "$STREAMPROCESSOR_VERSION" ]]  && sed_args+=(-e "s|^\(VST_STREAM_PROCESSOR_IMAGE=.*:\).*|\1${STREAMPROCESSOR_VERSION}|")

    if ! sed "${sed_args[@]}" "$compose_env_file" > "$temp_compose_file"; then
        rm -f "$temp_compose_file"
        error "Failed to update compose.env"
    fi

    if ! mv "$temp_compose_file" "$compose_env_file"; then
        rm -f "$temp_compose_file"
        error "Failed to save compose.env changes"
    fi

    info "Successfully updated compose.env with current IP: $CURRENT_IP"
    info "Backup saved as ${compose_env_file}.bak"
}

# Function to update docker-compose.yaml
update_docker_compose() {
    # Update docker-compose.yaml
    local docker_compose_file="$VST_BASE_PATH/docker-compose.yaml"
    if [[ ! -f "$docker_compose_file" ]]; then
        error "docker-compose.yaml file not found: $docker_compose_file"
    fi

    # Create backup of docker-compose.yaml
    cp "$docker_compose_file" "${docker_compose_file}.bak" || error "Failed to backup docker-compose.yaml"

    # Create temporary file
    local temp_compose_yaml
    temp_compose_yaml=$(mktemp) || error "Failed to create temporary file for docker-compose.yaml"

    if ! sed -e 's|^\([[:space:]]*\)- ${VST_VOLUME}/postgres/db:|#\1- ${VST_VOLUME}/postgres/db:|' \
            "$docker_compose_file" > "$temp_compose_yaml"; then
        rm -f "$temp_compose_yaml"
        error "Failed to update docker-compose.yaml"
    fi

    # Write test service to a separate compose override file instead of appending
    # to the main file, which can place it outside the services: block if other
    # top-level keys (volumes, networks, include, etc.) follow services.
    #
    # Test source (tests/, features/, scripts/, data/, conftest.py) is bind-mounted
    # from the host repo so test code changes do not require rebuilding the image.
    # The image only needs rebuilding when Dockerfile, docker-entrypoint.sh,
    # pyproject.toml, or poetry.lock change.
    #
    # Mount paths use ${BDD_TESTS_DIR:-<resolved-path>} so the generated YAML
    # works both when invoked via this script (env var set) and when a developer
    # runs `docker compose -f docker-compose.test.yaml up` manually (falls back
    # to the absolute path baked in by this script at generation time). The
    # @@BDD_TESTS_DIR_DEFAULT@@ placeholder is rewritten with the resolved path
    # immediately after the heredoc.
    #
    # Python import note: the image also bundles tests/ and scripts/ as installed
    # packages under .venv/site-packages/. The bind mounts win at runtime
    # because pytest prepends the rootdir (/app) to sys.path ahead of
    # site-packages -- do not move pytest's rootdir without revisiting this.
    local test_compose_file="$VST_BASE_PATH/docker-compose.test.yaml"
    cat > "$test_compose_file" << 'EOL'
services:
  test:
    image: ${BDD_TEST_IMAGE:-bdd_tests:v1.9.5_x86}
    network_mode: host
    container_name: bdd_test
    group_add:
      - "${DOCKER_GID:-999}"
    volumes:
      - ./config.json:/app/config.json
      - ./bdd_test_reports:/app/reports
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - ${BDD_TESTS_DIR:-@@BDD_TESTS_DIR_DEFAULT@@}/tests:/app/tests:ro
      - ${BDD_TESTS_DIR:-@@BDD_TESTS_DIR_DEFAULT@@}/features:/app/features:ro
      - ${BDD_TESTS_DIR:-@@BDD_TESTS_DIR_DEFAULT@@}/scripts:/app/scripts:ro
      - ${BDD_TESTS_DIR:-@@BDD_TESTS_DIR_DEFAULT@@}/data:/app/data:ro
      - ${BDD_TESTS_DIR:-@@BDD_TESTS_DIR_DEFAULT@@}/conftest.py:/app/conftest.py:ro
    depends_on:
      vst-ingress:
        condition: service_healthy
      sensor-ms:
        condition: service_healthy
    environment:
      - DASHBOARD_API_ENDPOINT=${DASHBOARD_API_ENDPOINT:-http://localhost:8000}
      - GIT_BRANCH=${GIT_BRANCH:-unknown}
      - GIT_COMMIT=${GIT_COMMIT:-}
      - MICROSERVICE_NAME=${MICROSERVICE_NAME:-VIOS}
      - DASHBOARD_METADATA=${DASHBOARD_METADATA:-{}}
      - PERF_CONFIG_ID=${PERF_CONFIG_ID:-rtx6000pro-file}
      - PERF_RELEASE=${PERF_RELEASE:-}
      - MINIO_ENDPOINT=${MINIO_ENDPOINT:-localhost:9000}
      - MINIO_ACCESS_KEY=${MINIO_ACCESS_KEY:-minioadmin}
      - MINIO_SECRET_KEY=${MINIO_SECRET_KEY:-minioadmin}
    entrypoint: []
    command:
      - /bin/bash
      - -c
      - |
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Services are healthy. Starting BDD tests now..."
        echo "--- Running unit tests (CSV + JUnit reports) ---"
        bash scripts/run_unit_tests.sh
        UNIT_EXIT=$$?
        echo "--- Running BDD tests ---"
        poetry run pytest tests/ --ignore=tests/unit_tests --ignore=tests/perf --color=yes --tb=short
        BDD_EXIT=$$?
        echo "--- Running performance tests (VSS perf JSON + MinIO upload) ---"
        poetry run pytest tests/perf/test_latency.py -v \
            --perf-iterations 4 \
            --perf-output-json /app/reports/performance/ \
            --perf-upload \
            --perf-config-id "$$PERF_CONFIG_ID" \
            --perf-release "$$PERF_RELEASE" \
            --disable-container-monitor \
            -p no:html -p no:csv \
            --override-ini="addopts=" \
            || echo "WARNING: Performance test failed (non-fatal)"
        echo "--- Publishing test results to dashboard ---"
        poetry run python -m scripts.test_result_publisher || echo "WARNING: Test result publishing failed (non-fatal)"
        if [ "$$UNIT_EXIT" -ne 0 ]; then exit "$$UNIT_EXIT"; fi
        exit "$$BDD_EXIT"
EOL

    # Bake the resolved host path into the @@BDD_TESTS_DIR_DEFAULT@@ placeholder
    # so the generated YAML stays usable even when launched without BDD_TESTS_DIR
    # exported (e.g. a developer running docker compose up manually for debug).
    if ! sed -i "s|@@BDD_TESTS_DIR_DEFAULT@@|${BDD_TESTS_DIR}|g" "$test_compose_file"; then
        error "Failed to inject BDD_TESTS_DIR default into $test_compose_file"
    fi
    info "Test service written to $test_compose_file (BDD_TESTS_DIR default: $BDD_TESTS_DIR)"

    if ! mv "$temp_compose_yaml" "$docker_compose_file"; then
        rm -f "$temp_compose_yaml"
        error "Failed to save docker-compose.yaml changes"
    fi

    info "Successfully updated docker-compose.yaml"
    info "Backup saved as ${docker_compose_file}.bak"
}

# Function to generate BDD test config.json from template
generate_bdd_config_json() {
    local config_json_source=$1
    local config_json_dest=$2
    
    # Check if source template exists
    [[ -f "$config_json_source" ]] || error "BDD config template not found: $config_json_source"
    
    # Create temporary file for BDD config
    local temp_config
    temp_config=$(mktemp) || error "Failed to create temporary file for BDD config.json"

    if ! jq --arg base_url "http://${CURRENT_IP}:30888" '.api.base_url = $base_url' "$config_json_source" > "$temp_config"; then
        rm -f "$temp_config"
        error "Failed to update BDD config.json with current IP"
    fi

    if ! jq empty "$temp_config" 2>/dev/null; then
        rm -f "$temp_config"
        error "Generated invalid BDD config JSON"
    fi

    if [[ -f "$config_json_dest" ]]; then
        cp "$config_json_dest" "${config_json_dest}.bak" || error "Failed to backup existing config.json"
    fi

    if ! mv "$temp_config" "$config_json_dest"; then
        rm -f "$temp_config"
        error "Failed to save BDD config.json"
    fi

    info "Successfully generated BDD config.json at $config_json_dest with API URL: http://${CURRENT_IP}:30888"
    if [[ -f "${config_json_dest}.bak" ]]; then
        info "Backup saved as ${config_json_dest}.bak"
    fi
}

# Function to update nvstreamer config
update_nvstreamer_config() {
    local nvstreamer_config_json="$NVSTREAMER_BASE_PATH/configs/vst_config.json"

    [[ -f "$nvstreamer_config_json" ]] || error "JSON file not found: $nvstreamer_config_json"
    jq empty "$nvstreamer_config_json" 2>/dev/null || error "Invalid JSON file: $nvstreamer_config_json"

    local temp_vst_file
    temp_vst_file=$(mktemp) || error "Failed to create temporary file for VST config"

    if ! cp "$nvstreamer_config_json" "${nvstreamer_config_json}.bak"; then
        rm -f "$temp_vst_file"
        error "Failed to create backup of VST config"
    fi

    local vst_jq_filter='.data.nv_streamer_directory_path = "/home/vst/vst_release/streamer_videos/"'

    if ! jq "$vst_jq_filter" "$nvstreamer_config_json" >"$temp_vst_file"; then
        rm -f "$temp_vst_file"
        error "Failed to update VST config file"
    fi

    if ! jq empty "$temp_vst_file" 2>/dev/null; then
        rm -f "$temp_vst_file"
        error "Generated invalid VST config JSON"
    fi

    if ! mv "$temp_vst_file" "$nvstreamer_config_json"; then
        rm -f "$temp_vst_file"
        error "Failed to save VST config changes"
    fi

    info "Successfully updated $nvstreamer_config_json"
    info "Backup saved as ${nvstreamer_config_json}.bak"
}

# Function to update nvstreamer compose.env
update_nvstreamer_compose_env() {
    local nvstreamer_compose_env="$NVSTREAMER_BASE_PATH/compose.env"
    if [[ ! -f "$nvstreamer_compose_env" ]]; then
        error "nvstreamer compose.env file not found: $nvstreamer_compose_env"
    fi

    cp "$nvstreamer_compose_env" "${nvstreamer_compose_env}.bak" || error "Failed to backup nvstreamer compose.env"

    local temp_compose_file
    temp_compose_file=$(mktemp) || error "Failed to create temporary file for nvstreamer compose.env"

    local nvstreamer_video_base="$VST_BASE_PATH/vst_volume/nvstreamer_data"
    local nvstreamer_video_src="$TOP/tools/data"
    if [[ ! -d "$nvstreamer_video_src" ]]; then
        error "Video source directory not found: $nvstreamer_video_src"
    fi
    for i in 1 2 3 4 5; do
        local instance_dir="${nvstreamer_video_base}/nvstreamer-${i}"
        mkdir -p "${instance_dir}/vst_data"
        if ! cp -n "$nvstreamer_video_src"/* "${instance_dir}/" 2>/dev/null; then
            info "WARNING: Failed to copy video files to ${instance_dir}/ -- nvstreamer-${i} may lack test videos"
        fi
    done

    if ! sed -e "s|^NVSTREAMER_VIDEO_[1-5]=.*|#&|" \
            -e "\$a\\
NVSTREAMER_VIDEO_1=${nvstreamer_video_base}/nvstreamer-1\\
NVSTREAMER_VIDEO_2=${nvstreamer_video_base}/nvstreamer-2\\
NVSTREAMER_VIDEO_3=${nvstreamer_video_base}/nvstreamer-3\\
NVSTREAMER_VIDEO_4=${nvstreamer_video_base}/nvstreamer-4\\
NVSTREAMER_VIDEO_5=${nvstreamer_video_base}/nvstreamer-5" \
            "$nvstreamer_compose_env" > "$temp_compose_file"; then
        rm -f "$temp_compose_file"
        error "Failed to update nvstreamer compose.env"
    fi

    if ! mv "$temp_compose_file" "$nvstreamer_compose_env"; then
        rm -f "$temp_compose_file"
        error "Failed to save nvstreamer compose.env changes"
    fi

    info "Successfully updated nvstreamer compose.env"
    info "Backup saved as ${nvstreamer_compose_env}.bak"
}

# Function to update nvstreamer docker-compose.yaml
update_nvstreamer_docker_compose() {
    local nvstreamer_compose_file="$NVSTREAMER_BASE_PATH/docker-compose.yaml"
    if [[ ! -f "$nvstreamer_compose_file" ]]; then
        error "nvstreamer docker-compose.yaml file not found: $nvstreamer_compose_file"
    fi

    cp "$nvstreamer_compose_file" "${nvstreamer_compose_file}.bak" || error "Failed to backup nvstreamer docker-compose.yaml"

    info "Successfully backed up nvstreamer docker-compose.yaml"
    info "Backup saved as ${nvstreamer_compose_file}.bak"
}

# Function to update nvstreamer image tag in nvstreamer compose.env
update_nvstreamer_image_tag() {
    if [[ -z "$NVSTREAMER_VERSION" ]]; then
        info "No nvstreamer version specified, preserving existing image tag"
        return 0
    fi

    local nvstreamer_compose_env="$NVSTREAMER_BASE_PATH/compose.env"
    if [[ ! -f "$nvstreamer_compose_env" ]]; then
        error "nvstreamer compose.env file not found: $nvstreamer_compose_env"
    fi

    cp "$nvstreamer_compose_env" "${nvstreamer_compose_env}.version.bak" || error "Failed to backup nvstreamer compose.env"

    local temp_file
    temp_file=$(mktemp) || error "Failed to create temporary file for nvstreamer compose.env"

    if ! sed -e "s|^\(NVSTREAMER_IMAGE=.*:\).*|\1${NVSTREAMER_VERSION}|" \
            "$nvstreamer_compose_env" > "$temp_file"; then
        rm -f "$temp_file"
        error "Failed to update nvstreamer compose.env image tag"
    fi

    if ! mv "$temp_file" "$nvstreamer_compose_env"; then
        rm -f "$temp_file"
        error "Failed to save nvstreamer compose.env image tag changes"
    fi

    info "Successfully updated nvstreamer image tag to ${NVSTREAMER_VERSION} in compose.env"
}

# Function to run build commands
run_build_commands() {
    # Run build commands
    info "Running VST module build commands for architecture: ${ARCH}"
    info "TOP directory is: ${TOP}"
    cd "${TOP}" || error "Failed to change directory to TOP: ${TOP}"
    
    # Build nvstreamer
    info "Building nvstreamer for ${ARCH}..."
    if [[ "$ARCH" = "x86_64" || "$ARCH" = "amd64" ]]; then
        ./build.sh clean || error "clean build failed"
        ./build.sh container nvstreamer || error "nvstreamer build failed"
    else
        ./build.sh arch=${ARCH} clean || error "clean build failed"
        ./build.sh arch=${ARCH} container nvstreamer || error "nvstreamer build failed"
    fi

    # Build sensor + stream-processor
    info "Building sensor and stream-processor for ${ARCH}..."
    if [[ "$ARCH" = "x86_64" || "$ARCH" = "amd64" ]]; then
        ./build.sh clean || error "clean build failed"
        ./build.sh container module=sensor || error "sensor build failed"
        ./build.sh clean || error "clean build failed"
        ./build.sh container module=streamprocessing || error "stream-processor build failed"
    else
        ./build.sh arch=${ARCH} clean || error "clean build failed"
        ./build.sh arch=${ARCH} container module=sensor || error "sensor build failed"
        ./build.sh arch=${ARCH} clean || error "clean build failed"
        ./build.sh arch=${ARCH} container module=streamprocessing || error "stream-processor build failed"
    fi
}

# Function to run docker compose
run_docker_compose() {
    # Ensure BDD test reports directory exists before starting containers
    ensure_bdd_reports_dir || error "Failed to ensure BDD reports directory exists"
    
    info "Docker version: $(docker --version 2>&1)"
    info "Docker Compose version: $(docker compose version 2>&1)"

    info "Starting docker compose..."

    # Stop and start nvstreamer containers
    info "Starting nvstreamer containers..."
    cd "$NVSTREAMER_BASE_PATH" || error "Failed to change directory to NVSTREAMER_BASE_PATH"
    docker compose --env-file ./compose.env down
    docker compose --env-file ./compose.env up -d || error "Failed to start nvstreamer containers"

    # Wait for nvstreamer containers to initialize
    info "Waiting for nvstreamer containers to initialize..."
    info "Step 1: (300s remaining)"
    sleep 100

    info "Step 2: (200s remaining)"
    sleep 100

    info "Step 3: (100s remaining)"
    sleep 100

    info "Nvstreamer container status after init wait:"
    docker ps -a --filter "name=nvstreamer" --format 'table {{.Names}}\t{{.Status}}' || true

    info "Starting main test containers..."
    cd "$VST_BASE_PATH" || error "Failed to change directory to VST_BASE_PATH"

    docker compose -f docker-compose.yaml -f docker-compose.test.yaml --env-file ./compose.env down
    
    local exit_code=0
    docker compose -f docker-compose.yaml -f docker-compose.test.yaml --env-file ./compose.env up --remove-orphans --exit-code-from test --attach test || exit_code=$?

    # Collect docker logs and mirror BDD reports BEFORE cleanup tears
    # containers down.  finalize_artifacts is idempotent (sentinel) so
    # the EXIT trap below becomes a no-op on the happy path.
    info "Collecting container logs..."
    finalize_artifacts || info "Failed to finalize artifacts but continuing..."

    cleanup || info "Failed to cleanup but continuing... "

    if [[ "$exit_code" -ne 0 ]]; then
        error "Test execution failed with exit code: $exit_code"
    fi
}

ensure_bdd_reports_dir() {
    local reports_dir="$VST_BASE_PATH/bdd_test_reports"
    
    info "Ensuring BDD test reports directory exists: $reports_dir"
    mkdir -p "$reports_dir" || error "Failed to create BDD test reports directory: $reports_dir"
    
    return 0
}

# Function to copy BDD test reports to deployment directory
copy_bdd_reports() {
    local source_reports_dir="$TOP/test/bdd_tests/reports"
    local dest_reports_dir="$VST_BASE_PATH/bdd_test_reports"
    
    info "Copying BDD test reports from $source_reports_dir to $dest_reports_dir"
    
    # Ensure destination directory exists first
    ensure_bdd_reports_dir || return $?
    
    # Check if source directory exists
    if [[ ! -d "$source_reports_dir" ]]; then
        info "Source reports directory does not exist: $source_reports_dir"
        return 0
    fi
    
    # Copy all files and subdirectories from source to destination
    if [[ -n "$(ls -A "$source_reports_dir" 2>/dev/null)" ]]; then
        cp -r "$source_reports_dir"/* "$dest_reports_dir"/ || error "Failed to copy reports from $source_reports_dir to $dest_reports_dir"
        info "Successfully copied BDD reports to $dest_reports_dir"
    else
        info "No files found in source reports directory: $source_reports_dir"
    fi
    
    return 0
}

collect_logs() {
    local containers
    containers=$(docker ps -a --format '{{.Names}}')

    if [[ -z "$containers" ]]; then
        echo "No running containers found"
        return 0
    fi

    # Dump full logs into the BDD reports artifact directory
    local artifact_log_dir="$VST_BASE_PATH/bdd_test_reports/docker_logs"
    mkdir -p "$artifact_log_dir" || info "Failed to create artifact log directory"

    # Save container status table
    docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}' \
        > "$artifact_log_dir/container_status.txt" 2>&1 || true

    local container log_file
    for container in $containers; do
        log_file="${artifact_log_dir}/${container}.log"
        if ! docker logs "$container" > "$log_file" 2>&1; then
            echo "Failed to collect logs for container: $container"
        fi
    done

    # Capture healthcheck history and image info for debugging
    {
        echo "=== Healthcheck details ==="
        for container in $containers; do
            local health_status
            health_status=$(docker inspect --format='{{.State.Health.Status}}' "$container" 2>/dev/null) || continue
            echo ""
            echo "--- $container (health: $health_status) ---"
            echo "Image: $(docker inspect --format='{{.Config.Image}}' "$container" 2>/dev/null)"
            echo "Platform: $(docker inspect --format='{{.Platform}}' "$container" 2>/dev/null)"
            echo "Env vars: $(docker inspect --format='{{range .Config.Env}}{{println .}}{{end}}' "$container" 2>/dev/null | grep -E '^(HTTP_PORT|PORT|CONTAINER_NAME)=' || echo '(none matched)')"
            echo "Healthcheck cmd: $(docker inspect --format='{{json .Config.Healthcheck.Test}}' "$container" 2>/dev/null)"
            if [[ "$health_status" != "healthy" ]]; then
                echo "Last 3 healthcheck results:"
                docker inspect --format='{{range $i, $log := .State.Health.Log}}{{if lt $i 3}}  [{{.Start}}] exit={{.ExitCode}} output={{.Output}}{{end}}{{end}}' "$container" 2>/dev/null || true
            fi
        done
    } > "$artifact_log_dir/healthcheck_details.txt" 2>&1

    if grep -q "unhealthy" "$artifact_log_dir/healthcheck_details.txt" 2>/dev/null; then
        info "Unhealthy containers detected -- see $artifact_log_dir/healthcheck_details.txt for details"
    fi

    info "Container logs saved to $artifact_log_dir"

    # Also archive to /tmp for backward compatibility with Jenkins post-build steps
    local log_base_dir="/tmp/vst_test_logs"
    local timestamp
    timestamp=$(date +'%Y%m%d_%H%M%S')
    local jenkins_log_dir="/tmp/last_run_logs"

    [[ -d "$jenkins_log_dir" ]] && rm -rf "$jenkins_log_dir"
    mkdir -p "$log_base_dir" "$jenkins_log_dir" || {
        echo "Failed to create Jenkins log directory: $jenkins_log_dir"
        return 1
    }

    local tar_file="${log_base_dir}/docker_logs_${timestamp}.tar.gz"
    if tar -czf "$tar_file" -C "$(dirname "$artifact_log_dir")" "$(basename "$artifact_log_dir")"; then
        echo "Logs archived to: $tar_file"
        mv "$tar_file" "${jenkins_log_dir}/docker_logs.tar.gz"
    else
        echo "Failed to archive logs"
    fi
}

# Idempotent collection of docker logs + BDD reports.  Invoked both
# inline (run_docker_compose, happy path) and via an EXIT trap so the
# Jenkins artifact path always has logs -- even when the script aborts
# via set -e, error(), or a signal before reaching the inline call.
ARTIFACTS_COLLECTED=0
finalize_artifacts() {
    local exit_code=$?
    if [[ "$ARTIFACTS_COLLECTED" -eq 1 ]]; then
        return "$exit_code"
    fi
    ARTIFACTS_COLLECTED=1

    echo "INFO: Finalizing test artifacts (exit_code=$exit_code)..."

    # Collect docker logs from any containers still present.  collect_logs
    # is no-op when nothing is running and tolerates missing directories.
    if [[ -n "${VST_BASE_PATH:-}" ]]; then
        collect_logs || echo "INFO: Failed to collect docker logs during finalize"
    else
        echo "INFO: VST_BASE_PATH not set yet; skipping docker log collection"
    fi

    # Mirror BDD reports to the legacy artifact path Jenkins archives from.
    if [[ -n "${TOP:-}" && -n "${VST_BASE_PATH:-}" \
          && -d "$VST_BASE_PATH/bdd_test_reports" ]]; then
        local legacy_reports_dir="$TOP/deployment/scaling/docker-compose/bdd_test_reports"
        if mkdir -p "$legacy_reports_dir" 2>/dev/null; then
            cp -r "$VST_BASE_PATH/bdd_test_reports/." "$legacy_reports_dir/" 2>/dev/null \
                || echo "INFO: Failed to mirror reports to legacy path during finalize"
            echo "INFO: Mirrored BDD reports to legacy artifact path: $legacy_reports_dir"
        fi
    fi

    return "$exit_code"
}

# Run finalize_artifacts on every exit path: normal return, error()
# (which calls `exit 1`), `set -e` abort, SIGINT (Ctrl+C), SIGTERM.
# The sentinel ensures double-execution is a no-op when the inline call
# in run_docker_compose has already collected logs on the happy path.
trap finalize_artifacts EXIT

# Main script execution
main() {
    # Default mode is build-and-test
    MODE="build-and-test"
    # Default architecture is x86_64
    ARCH="x86_64"
    # Version overrides (empty = no override; if no version flags given, defaults to 'latest')
    VERSION=""
    SENSOR_VERSION=""
    STREAMPROCESSOR_VERSION=""
    NVSTREAMER_VERSION=""
    local version_flag_given=false
    
    # Parse command line arguments
    while [[ $# -gt 0 ]]; do
        case $1 in
            --build-only)
                MODE="build-only"
                shift
                ;;
            --test-only)
                MODE="test-only"
                shift
                ;;
            --build-and-test)
                MODE="build-and-test"
                shift
                ;;
            --arch=*)
                ARCH="${1#*=}"
                shift
                ;;
            --version=*)
                VERSION="${1#*=}"
                version_flag_given=true
                shift
                ;;
            --sensor-version=*)
                SENSOR_VERSION="${1#*=}"
                version_flag_given=true
                shift
                ;;
            --streamprocessor-version=*)
                STREAMPROCESSOR_VERSION="${1#*=}"
                version_flag_given=true
                shift
                ;;
            --nvstreamer-version=*)
                NVSTREAMER_VERSION="${1#*=}"
                version_flag_given=true
                shift
                ;;
            *)
                usage
                ;;
        esac
    done

    # Validate architecture parameter
    if [[ "$ARCH" != "x86_64" && "$ARCH" != "amd64" && "$ARCH" != "sbsa" && "$ARCH" != "aarch64" && "$ARCH" != "arm64" ]]; then
        error "Invalid architecture: $ARCH. Supported architectures are: x86_64/amd64, sbsa, aarch64/arm64"
    fi

    # Validate version strings (used in sed replacements; reject special chars)
    for v in "$VERSION" "$SENSOR_VERSION" "$STREAMPROCESSOR_VERSION" "$NVSTREAMER_VERSION"; do
        if [[ -n "$v" && ! "$v" =~ ^[a-zA-Z0-9._-]+$ ]]; then
            error "Invalid version tag: '$v' -- only alphanumeric characters, dots, hyphens, and underscores are allowed"
        fi
    done
    
    info "Using architecture: $ARCH"

    # No version flags at all -> default to 'latest' for all (original behavior)
    # Any version flag given -> only specified containers change, others preserved
    if [[ "$version_flag_given" = false ]]; then
        SENSOR_VERSION="latest"
        STREAMPROCESSOR_VERSION="latest"
        NVSTREAMER_VERSION="latest"
    elif [[ -n "$VERSION" ]]; then
        [[ -z "$SENSOR_VERSION" ]]          && SENSOR_VERSION="$VERSION"
        [[ -z "$STREAMPROCESSOR_VERSION" ]] && STREAMPROCESSOR_VERSION="$VERSION"
        [[ -z "$NVSTREAMER_VERSION" ]]      && NVSTREAMER_VERSION="$VERSION"
    fi

    # Set TOP if not already set
    if [[ -z "${TOP:-}" ]]; then
        TOP="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
        export TOP
        info "TOP environment variable set to: $TOP"
    fi

    # Derive git info from the cloned repo for dashboard publishing
    export GIT_BRANCH="${GIT_BRANCH:-$(git -C "$TOP" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")}"
    export GIT_COMMIT="${GIT_COMMIT:-$(git -C "$TOP" rev-parse HEAD 2>/dev/null || echo "")}"
    info "Git branch: $GIT_BRANCH"
    info "Git commit: $GIT_COMMIT"

    # Detect GPU type and driver version via nvidia-smi for dashboard metadata
    if command -v nvidia-smi &>/dev/null; then
        local nvidia_smi_output nvidia_smi_exit
        nvidia_smi_output=$(nvidia-smi --query-gpu=name,driver_version --format=csv,noheader,nounits 2>&1) && nvidia_smi_exit=0 || nvidia_smi_exit=$?
        if [[ "$nvidia_smi_exit" -eq 0 ]]; then
            GPU_TYPE=$(echo "$nvidia_smi_output" | head -n1 | cut -d',' -f1 | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
            NVIDIA_DRIVER_VERSION=$(echo "$nvidia_smi_output" | head -n1 | cut -d',' -f2 | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
        else
            info "nvidia-smi failed with exit code $nvidia_smi_exit. Output:"
            echo "$nvidia_smi_output" >&2
        fi
    else
        info "nvidia-smi not found in PATH, skipping GPU detection"
    fi
    GPU_TYPE="${GPU_TYPE:-unknown}"
    NVIDIA_DRIVER_VERSION="${NVIDIA_DRIVER_VERSION:-unknown}"
    export GPU_TYPE
    export NVIDIA_DRIVER_VERSION
    info "GPU type: $GPU_TYPE"
    info "NVIDIA driver version: $NVIDIA_DRIVER_VERSION"

    # Map detected GPU to perf config-id (used by VSS perf dashboard)
    case "$GPU_TYPE" in
        *RTX*6000*|*RTX6000*) PERF_CONFIG_ID_PREFIX="rtx6000pro" ;;
        *H100*)               PERF_CONFIG_ID_PREFIX="h100" ;;
        *Spark*)              PERF_CONFIG_ID_PREFIX="spark" ;;
        *Thor*)               PERF_CONFIG_ID_PREFIX="thor" ;;
        *)                    PERF_CONFIG_ID_PREFIX="unknown" ;;
    esac
    export PERF_CONFIG_ID="${PERF_CONFIG_ID:-${PERF_CONFIG_ID_PREFIX}-file}"
    export PERF_RELEASE="${PERF_RELEASE:-}"
    info "Perf config-id: $PERF_CONFIG_ID"

    # Check and install jq if not present
    if ! command -v jq &>/dev/null; then
        info "jq not found. Attempting to install..."
        install_jq
    fi

    export DASHBOARD_METADATA="${DASHBOARD_METADATA:-$(jq -nc \
        --arg gpu "$GPU_TYPE" \
        --arg drv "$NVIDIA_DRIVER_VERSION" \
        --arg url "${BUILD_URL:-}" \
        --arg num "${BUILD_NUMBER:-}" \
        --arg bid "${BUILD_ID:-}" \
        '{gpu_type:$gpu, nvidia_driver_version:$drv, jenkins_job_url:$url, jenkins_build_number:$num, jenkins_build_id:$bid}')}"
    info "Jenkins build URL: ${BUILD_URL:-<not set>}"
    info "Jenkins build number: ${BUILD_NUMBER:-<not set>}"
    info "Jenkins build ID: ${BUILD_ID:-<not set>}"
    info "Dashboard metadata: $DASHBOARD_METADATA"

    # Get script directory and change to it
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    cd "$SCRIPT_DIR" || error "Failed to change to script directory"

    # Calculate absolute paths
    VST_BASE_PATH="$(cd "$SCRIPT_DIR/../../deployment/stream-processing/docker-compose" && pwd)"
    NVSTREAMER_BASE_PATH="$(cd "$SCRIPT_DIR/../../deployment/scaling/docker-compose/nvstreamer" && pwd)"
    RTSP_STREAMS_JSON="$VST_BASE_PATH/configs/rtsp_streams.json"
    VST_CONFIG_JSON="$VST_BASE_PATH/configs/vst_config.json"
    
    # Set VST_CONFIG_PATH and VST_VOLUME
    VST_CONFIG_PATH="$VST_BASE_PATH/configs"
    VST_VOLUME="$VST_BASE_PATH/vst_volume"

    # Set config.json paths for BDD tests
    BDD_CONFIG_JSON_SOURCE="$TOP/test/bdd_tests/config.json"
    BDD_CONFIG_JSON_DEST="$VST_BASE_PATH/config.json"

    # Absolute host path to BDD test sources, bind-mounted into bdd_test
    # container so test changes do not require rebuilding the image.
    BDD_TESTS_DIR="$TOP/test/bdd_tests"
    if [[ ! -d "$BDD_TESTS_DIR" ]]; then
        error "BDD tests directory not found: $BDD_TESTS_DIR"
    fi
    export BDD_TESTS_DIR
    info "Bind-mounting BDD test sources from: $BDD_TESTS_DIR"

    # Defaults formerly provided by config.env; callers can still override them.
    NVSTREAMER_COUNT="${NVSTREAMER_COUNT:-5}"
    NVSTREAMER_PORT_BASE="${NVSTREAMER_PORT_BASE:-31000}"

    # Validate required variables
    : "${NVSTREAMER_COUNT:?NVSTREAMER_COUNT is not set in config}"
    : "${NVSTREAMER_PORT_BASE:?NVSTREAMER_PORT_BASE is not set in config}"

    # Validate values
    validate_integer "$NVSTREAMER_COUNT" "NVSTREAMER_COUNT"
    validate_integer "$NVSTREAMER_PORT_BASE" "NVSTREAMER_PORT_BASE"
    validate_path "$RTSP_STREAMS_JSON" "RTSP_STREAMS_JSON"
    validate_path "$VST_CONFIG_JSON" "VST_CONFIG_JSON"

    # Check if JSON file exists and is valid
    [[ -f "$RTSP_STREAMS_JSON" ]] || error "JSON file not found: $RTSP_STREAMS_JSON"
    jq empty "$RTSP_STREAMS_JSON" 2>/dev/null || error "Invalid JSON file: $RTSP_STREAMS_JSON"

    # Update configurations
    info "Starting configuration updates..."
    update_rtsp_streams_json || error "Failed to update RTSP streams JSON"
    update_vst_config || error "Failed to update VST config"
    # update_nginx_config || error "Failed to update nginx config"
    update_compose_env || error "Failed to update compose.env"
    update_docker_compose || error "Failed to update docker-compose.yaml"
    update_nvstreamer_config || error "Failed to update nvstreamer config"
    update_nvstreamer_compose_env || error "Failed to update nvstreamer compose.env"
    update_nvstreamer_docker_compose || error "Failed to update nvstreamer docker-compose.yaml"
    update_nvstreamer_image_tag || error "Failed to update nvstreamer image tag"
    generate_bdd_config_json "$BDD_CONFIG_JSON_SOURCE" "$BDD_CONFIG_JSON_DEST" || error "Failed to generate BDD config.json"

    info "=== Modified compose.env ==="
    cat "$VST_BASE_PATH/compose.env"
    info "=== Modified nvstreamer/compose.env ==="
    cat "$NVSTREAMER_BASE_PATH/compose.env"
    separator

    # Perform initial cleanup before starting the test execution
    info "Performing initial cleanup before starting tests..."
    if ! cleanup; then
        error "Initial cleanup failed"
    fi

    # Run based on selected mode
    case "$MODE" in
        "build-only")
            info "Running build-only mode..."
            run_build_commands || error "Build process failed"
            ;;
        "test-only")
            if [[ "$ARCH" != "x86_64" && "$ARCH" != "amd64" ]]; then
                info "Test execution is only supported for x86_64 architecture. Skipping tests for $ARCH."
                info "Test execution completed (skipped for $ARCH architecture)"
                return 0
            fi
            info "Running test-only mode..."
            # Ensure BDD test reports directory exists
            ensure_bdd_reports_dir || error "Failed to ensure BDD reports directory exists"
            # Copy reports directory before starting docker compose
            copy_bdd_reports || error "Failed to copy BDD reports"
            run_docker_compose || error "Docker compose failed"
            ;;
        "build-and-test")
            info "Running build-and-test mode..."
            run_build_commands || error "Build process failed"
            if [[ "$ARCH" != "x86_64" && "$ARCH" != "amd64" ]]; then
                info "Test execution is only supported for x86_64 architecture. Skipping tests for $ARCH."
                info "Build completed successfully. Tests skipped for $ARCH architecture."
                return 0
            fi
            run_docker_compose || error "Docker compose failed"
            ;;
    esac

    separator
    cleanup
}

# Execute main function
main "$@"
