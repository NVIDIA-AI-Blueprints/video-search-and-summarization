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

ARCH="x86_64"
PACKAGE=0
CONTAINER=0
TAG=""
PUSH=0
CLEAN=0
DEBUG=0
TESTS=0
MINIO=0
VSTAPP=0
STREAMPROCESSINGAPP=0
INGRESS=0
NVSTREAMER_INGRESS=0
NVSTREAMER=0
VSTMONOLITH=0
MCP=0
NO_CACHE=0
BASE_IMAGE=0
BASE_TAG=""
MODULES=()  # Array to hold the modules
# Registry and org for built images. Defaults to a bare local namespace so
# local builds work out of the box (e.g. vios/vst:latest, nvstreamer:latest);
# no registry is hardcoded in the public tree. Override to push elsewhere:
#   export IMAGE_REGISTRY=my-registry.example.com/vios
IMAGE_REGISTRY="${IMAGE_REGISTRY:-vios}"
NVSTREAMER_IMAGE="${NVSTREAMER_IMAGE:-nvstreamer}"

# Define valid module names
declare -A VALID_MODULES=(
    ["sensor"]=1
    ["rtspserver"]=1
    ["recorder"]=1
    ["livestream"]=1
    ["replaystream"]=1
    ["streambridge"]=1
    ["storage"]=1
    ["streamprocessing"]=1
)

# Function to display help
show_help() {
    echo "Usage: ./build.sh [options]"
    echo
    echo "Options:"
    echo "  arch=<arch>        Specify the architecture (amd64/x86_64 or arm64/aarch64). Default is x86_64/amd64."
    echo "  module=<modules>   Comma-separated list of modules to build (e.g., sensor,rtspserver,streamprocessing)."
    echo "  package            Build and package the modules."
    echo "  container          Build, package, and create Docker containers (uses base image strategy for faster builds)."
    echo "  tag=<name>         Docker image tag for application containers (used with container option)."
    echo "  base-tag=<name>    Docker tag for base image (default: latest)."
    echo "  push=<0|1>         Push Docker images to the registry (used with container option)."
    echo "  vst-app            Build k8s based vst-app for all modules and scaling-app"
    echo "  streamprocessing-app Build k8s based streamprocessing-app (sensor, streamprocessing, postgres, ingress)"
    echo "  ingress            Build ingress container needed for scaling-app"
    echo "  nvstreamer-ingress Build nvstreamer ingress container for scaling-app"
    echo "  mcp                Build MCP (Model Context Protocol) gateway container"
    echo "  clean              clean the earlier builds, similar to 'make clean'"
    echo "  debug              debug build"
    echo "  tests              build and run unit tests (optionally with module=<module>)"
    echo "  minio              build vst-app package minio"
    echo "  nvstreamer         Build nvstreamer"
    echo "  vst-monolith       Build vst-monolith"
    echo "  no-cache           Build Docker images without using cache"
    echo "  base-container     Build only the base image with system packages (for optimization)"
    echo "  help               Show this help message."
    echo
    echo "Examples:"
    echo "  ./build.sh (Same as => ./build.sh arch=x86_64 OR ./build.sh arch=amd64)"
    echo "  ./build.sh arch=arm64" OR " ./build.sh arch=aarch64"
    echo ""
    echo "  ./build.sh module=sensor"
    echo "  ./build.sh arch=arm64 module=recorder"
    echo ""
    echo "  ./build.sh package module=sensor,rtspserver,recorder,livestream,replaystream,storage,streambridge,streamprocessing"
    echo "  ./build.sh container module=sensor,rtspserver,recorder,livestream,replaystream,storage,streambridge,streamprocessing"
    echo "  ./build.sh container module=sensor,rtspserver,recorder,livestream,replaystream,storage,streambridge,streamprocessing push=1"
    echo "  ./build.sh container ingress push=1"
    echo "  ./build.sh container nvstreamer-ingress push=1"
    echo "  ./build.sh container mcp push=1"
    echo "  ./build.sh container module=streamprocessing push=1"
    echo ""
    echo "  ./build.sh vst-app"
    echo "  ./build.sh vst-app module=sensor,rtspserver,recorder"
    echo "  ./build.sh streamprocessing-app"
    echo ""
    echo "  ./build.sh clean"
    echo "  ./build.sh clean module=sensor"
    echo ""
    echo "Unit Tests (always builds ALL modules - storage + recorder):"
    echo "  ./build.sh tests                           # Build all tests (49 total)"
    echo "  ./build.sh arch=arm64 tests                # Cross-compile tests"
    echo ""
    echo "After building, run tests:"
    echo "  ./vst_test                                 # All 49 tests"
    echo "  ./vst_test --gtest_list_tests              # List tests"
    echo "  ./vst_test --gtest_filter=*Upload*         # Storage tests"
    echo "  ./vst_test --gtest_filter=StreamRecorderTest.* # Recorder tests"
    echo ""
    echo "Documentation: test/gtests/README_FIRST.md"
    echo "  ./build.sh nvstreamer clean"
    echo "  ./build.sh vst-monolith clean"
    echo "  ./build.sh container nvstreamer push=1"
    echo "  ./build.sh container vst-monolith push=1"
    echo "  ./build.sh container vst-monolith no-cache"
    echo "  ./build.sh container mcp push=1"
    echo ""
    echo "Base Image Strategy (default for faster builds):"
    echo "  ./build.sh base-container base-tag=<base-tag> push=1   # Build and push base image with specific tag"
    echo "  ./build.sh container module=sensor,rtspserver,recorder,livestream,replaystream,storage,streambridge,streamprocessing base-tag=<base-tag> tag=<tag> push=1  # App with specific base and app tags"
    echo ""
}

# Function to validate modules
validate_modules() {
    local invalid_modules=()

    for module in "${MODULES[@]}"; do
        if [[ ! ${VALID_MODULES[$module]} ]]; then
            invalid_modules+=("$module")
        fi
    done

    if [[ ${#invalid_modules[@]} -ne 0 ]]; then
        echo "Error: Invalid module(s) specified: ${invalid_modules[*]}"
        echo "Valid modules are: ${!VALID_MODULES[*]}"
        exit 1
    fi
}

# --- Build timing helpers (elapsed time and summary banners) ---
elapsed_seconds_since() {
    local start_ts=$1
    local now_ts
    now_ts=$(date +%s)
    echo $((now_ts - start_ts))
}

format_duration_hms() {
    local total=$1
    local mins=$((total / 60))
    local secs=$((total % 60))
    echo "${mins}m ${secs}s (${total} seconds)"
}

print_per_image_build_timing_line() {
    local start_ts=$1
    local did_push
    if [[ $# -ge 2 ]]; then
        did_push=$2
    else
        did_push=$PUSH
    fi
    if [[ "$did_push" -eq 1 ]]; then
        echo "Total time (build + push): $(format_duration_hms "$(elapsed_seconds_since "$start_ts")") total"
    else
        echo "Build time: $(format_duration_hms "$(elapsed_seconds_since "$start_ts")") total"
    fi
}

print_container_build_summary_footer() {
    local start_ts=$1
    local module_count=${2:-}
    local elapsed
    elapsed=$(elapsed_seconds_since "$start_ts")
    echo ""
    echo "========================================================"
    if [[ -n "$module_count" ]] && [[ "$module_count" -ge 1 ]] 2>/dev/null; then
        echo "All Container Builds Complete!"
        echo "========================================================"
        echo "Build Summary:"
        echo "   Modules built: $module_count"
    else
        echo "Container Build Complete!"
        echo "========================================================"
        echo "Build Summary:"
    fi
    if [[ $PUSH -eq 1 ]]; then
        echo "   Total time (build + push): $(format_duration_hms "$elapsed")"
    else
        echo "   Total time: $(format_duration_hms "$elapsed")"
    fi
    if [[ -n "$module_count" ]] && [[ "$module_count" -ge 1 ]] 2>/dev/null; then
        echo "   Average per module: $((elapsed / module_count)) seconds"
    fi
    echo ""
}

# Parse command line arguments
while [[ "$#" -gt 0 ]]; do
    case $1 in
        arch=*)
            ARCH="${1#*=}"
            if [[ "$ARCH" = "arm64" ]]; then ARCH="aarch64"; fi
            if [[ "$ARCH" = "amd64" ]]; then ARCH="x86_64"; fi
            ;;
        module=*)
            IFS=',' read -r -a MODULES <<< "${1#*=}"
            validate_modules  # Add validation check right after parsing
            ;;
        package) PACKAGE=1;;
        container) CONTAINER=1;;
        tag=*) TAG="${1#*=}";;
        base-tag=*) BASE_TAG="${1#*=}";;
        push=*) PUSH="${1#*=}";;
        vst-app) VSTAPP=1;;
        streamprocessing-app) STREAMPROCESSINGAPP=1;;
        nvstreamer-app) NVSTREAMERAPP=1;;
        nvstreamer) NVSTREAMER=1;;
        ingress) INGRESS=1;;
        nvstreamer-ingress) NVSTREAMER_INGRESS=1;;
        mcp) MCP=1;;
        clean) CLEAN=1;;
        debug) DEBUG=1;;
        tests) TESTS=1;;
        minio=*) MINIO="${1#*=}";;
        vst-monolith) VSTMONOLITH=1;;
        no-cache) NO_CACHE=1;;
        base-container) BASE_IMAGE=1;;
        help) show_help; exit 0;;
        *) echo "Unknown parameter passed: $1"; show_help; exit 1;;
    esac
    shift
done

# Print all variables
echo "ARCH=$ARCH"
echo "PACKAGE=$PACKAGE"
echo "CONTAINER=$CONTAINER"
echo "TAG=$TAG"
echo "BASE_TAG=$BASE_TAG"
echo "PUSH=$PUSH"
echo "CLEAN=$CLEAN"
echo "DEBUG=$DEBUG"
echo "TESTS=$TESTS"
echo "MINIO=$MINIO"
echo "VST-APP=$VSTAPP"
echo "STREAMPROCESSING-APP=$STREAMPROCESSINGAPP"
echo "NVSTREAMER-APP=$NVSTREAMERAPP"
echo "NVSTREAMER-INGRESS=$NVSTREAMER_INGRESS"
echo "NVSTREAMER=$NVSTREAMER"
echo "MCP=$MCP"
echo "VSTMONOLITH=$VSTMONOLITH"
echo "NO_CACHE=$NO_CACHE"
echo "BASE_IMAGE=$BASE_IMAGE"
echo "MODULES=${MODULES[@]}"
echo "IMAGE_REGISTRY=$IMAGE_REGISTRY"

# Default tags for each module
declare -A DEFAULT_TAGS=(
    [sensor]="latest"
    [storage]="latest"
    [recorder]="latest"
    [rtspserver]="latest"
    [livestream]="latest"
    [replaystream]="latest"
    [streambridge]="latest"
    [streamprocessing]="latest"
    [ingress]="latest"
    [nvstreamer-ingress]="latest"
    [mcp]="latest"
    [nvstreamer]="latest"
    [vst]="latest"
    [vst-base]="2.1.0-runtime-26.04.1"
)

# Function to build base image for faster container builds
build_base_image() {
    local push=$1

    echo "=============================================="
    echo "Building VST Base Image (Optimization Strategy)"
    echo "=============================================="
    echo "This builds a base image containing all system packages"
    echo ""

    # Determine the base image name and tag
    if [[ -n "$BASE_TAG" ]]; then
        BASE_IMAGE_NAME="$IMAGE_REGISTRY/vst-base:${BASE_TAG}"
    else
        BASE_IMAGE_NAME="$IMAGE_REGISTRY/vst-base:latest"
    fi

    echo "Building base image: $BASE_IMAGE_NAME"
    echo "Architecture: $ARCH"
    echo ""

    cd "cicd_files/$ARCH" || { echo "[ERROR] Cannot find cicd_files/$ARCH directory"; exit 1; }

    if [[ ! -f "Dockerfile.base" ]]; then
        echo "[ERROR] Dockerfile.base not found in cicd_files/$ARCH/"
        echo "Make sure you have the split Dockerfile strategy implemented."
        cd - || exit 1
        exit 1
    fi

    CACHE_FLAG=""
    if [[ $NO_CACHE -eq 1 ]]; then
        CACHE_FLAG="--no-cache"
        echo "Building without Docker cache..."
    fi

    echo "Starting base image build..."

    BASE_BUILD_START_TIME=$(date +%s)

    if [[ "$ARCH" == "aarch64" ]] || [[ "$ARCH" == "arm64" ]] || [[ "$ARCH" == "sbsa" ]]; then
        docker build $CACHE_FLAG --platform linux/arm64 --network=host -t "$BASE_IMAGE_NAME" -f Dockerfile.base .
    else
        docker build $CACHE_FLAG --network=host -t "$BASE_IMAGE_NAME" -f Dockerfile.base .
    fi

    if [[ $? -ne 0 ]]; then
        echo "[ERROR] Base image build failed: $BASE_IMAGE_NAME"
        cd - || exit 1
        exit 1
    fi

    echo ""
    echo "Base image build succeeded: $BASE_IMAGE_NAME"

    if [[ $push -eq 1 ]]; then
        echo "Pushing base image to registry..."
        docker push "$BASE_IMAGE_NAME"

        if [[ $? -ne 0 ]]; then
            echo "[ERROR] Base image push failed: $BASE_IMAGE_NAME"
            cd - || exit 1
            exit 1
        fi

        echo "Base image push succeeded: $BASE_IMAGE_NAME"
    fi

    echo ""
    echo "=============================================="
    echo "Base Image Build Complete!"
    echo "=============================================="
    echo "Base image: $BASE_IMAGE_NAME"
    print_per_image_build_timing_line "$BASE_BUILD_START_TIME" "$push"
    echo ""

    cd - || exit 1
}

# Function to build the vios-ui and stage its dist output into the webroot dir
# (used when building the nvstreamer container; webroot is packaged into the image).
build_vios_ui_webroot() {
    local build_root
    build_root=$(pwd)
    local ui_dir="$build_root/ui/vios-ui"
    local webroot_dir="$build_root/webroot"

    echo "Building vios-ui in $ui_dir ..."
    cd "$ui_dir" || { echo "[ERROR] Cannot find vios-ui directory: $ui_dir"; exit 1; }
    npm run install:link || { echo "[ERROR] npm run install:link failed"; exit 1; }
    npm run build || { echo "[ERROR] npm run build failed"; exit 1; }

    if [[ ! -d "dist" ]]; then
        echo "[ERROR] vios-ui dist directory not found after build"
        exit 1
    fi

    echo "Staging vios-ui dist into $webroot_dir ..."
    # Remove only the VST UI static files; leave other webroot files intact.
    rm -rf "$webroot_dir/assets" "$webroot_dir/favicon" "$webroot_dir/index.html"
    cp -rf dist/. "$webroot_dir/" || { echo "[ERROR] Failed to copy vios-ui dist to $webroot_dir"; exit 1; }
    cd "$build_root" || exit 1
}

# Function to build a module
build_module() {
    local module=$1
    local cc_value=$2
    local package=$3
    local clean=$4

    # Check if module is empty
    if [[ -z "$module" ]]; then
        echo "No module specified. Exiting function."
        return 1
    fi

    if [[ $CLEAN -eq 1 ]]; then
        MODULE=$module make cc=$cc_value clean
        exit 0
    fi

    if [[ $clean -eq 1 ]]; then
        echo "Cleaning module: $module"
        MODULE=$module make cc=$cc_value clean
        return 0
    fi

    if [[ $DEBUG -eq 1 ]]; then
        echo "Building module: $module with cc_value=$cc_value and debug mode"
        MODULE=$module make cc=$cc_value debug $package
    else
        echo "Building module: $module with cc_value=$cc_value and package=$package"
        MODULE=$module make cc=$cc_value $package
    fi

    # Check if the build was successful
    if [[ $? -ne 0 ]]; then
        echo "Build failed for module: $module. Exiting function."
        return 1
    fi
}

# Function to build and package all modules
build_all() {
    local cc_value=$1
    local package=$2
    local container=$3

    if [[ $CLEAN -eq 1 ]]; then
        make cc=$cc_value clean

        rm -rf deployment/scaling/ucf/vst-app/vst-app-*
        rm -rf deployment/scaling/ucf/vst-streamprocessing-app/vst-streamprocessing-app-*
        rm -rf deployment/scaling/ucf/ingress/ucf/output
        rm -rf deployment/scaling/ucf/redis/redis-app*
        rm -rf deployment/scaling/ucf/recorder/output
        rm -rf deployment/scaling/ucf/rtsp-server/output
        rm -rf deployment/scaling/ucf/sensor/output
        rm -rf deployment/scaling/ucf/storage/output
        rm -rf deployment/scaling/ucf/postgres/output
        rm -rf deployment/scaling/ucf/minio/output
        rm -rf deployment/scaling/ucf/livestream/output
        rm -rf deployment/scaling/ucf/replaystream/output
        rm -rf deployment/scaling/ucf/nvstreamer-app/nvstreamer/nvstreamer-app-*
        rm -rf deployment/scaling/ucf/nvstreamer-app/ingress/ucf/output
        rm -rf deployment/ucf/nv-streamer/output
        rm -rf deployment/scaling/ucf/sdr/vst-rtspserver-sdr/output/
        rm -rf deployment/scaling/ucf/sdr/vst-recorder-sdr/output/
        rm -rf deployment/scaling/ucf/sdr/vst-livestream-sdr/output/
        rm -rf deployment/scaling/ucf/sdr/vst-replaystream-sdr/output/
        rm -rf deployment/scaling/ucf/sdr/vst-streamprocessing/output/
        rm -rf deployment/scaling/ucf/streamprocessing/output/
        echo "Cleaning done ..!"
        exit 0
    fi

    echo "Building all with cc_value=$cc_value, package=$package, container=$container"

    if [[ $package -eq 1 ]]; then
        echo "Packaging all modules"
        if [[ $DEBUG -eq 1 ]]; then
            make cc=$cc_value debug package
        else
            make cc=$cc_value package
        fi
    else
        echo "Building all modules"
        if [[ $DEBUG -eq 1 ]]; then
            make cc=$cc_value debug
        else
            make cc=$cc_value
        fi
    fi

    # Check if the build or packaging was successful
    if [[ $? -ne 0 ]]; then
        echo -e "[ERROR] Build or packaging failed. Exiting function."
        return 1  # Return 1 to indicate an error or issue
    fi

    if [[ $container -eq 1 ]]; then
        cd "out/$ARCH" || exit 1
        # Use the specified TAG if available, otherwise use the default tag
        if [[ $NVSTREAMER -eq 1 ]]; then
            if [[ -n "$TAG" ]]; then
                TAG="${TAG}"
            else
                TAG=${DEFAULT_TAGS["nvstreamer"]:-"latest"}
                TAG="${TAG}"
            fi
            IMAGE_NAME=$NVSTREAMER_IMAGE:$TAG
        elif [[ $VSTMONOLITH -eq 1 ]]; then
            if [[ -n "$TAG" ]]; then
                TAG="${TAG}"
            else
                TAG=${DEFAULT_TAGS["vst"]:-"latest"}
                TAG="${TAG}"
            fi
            IMAGE_NAME=$IMAGE_REGISTRY/vst:$TAG
        else
            if [[ -n "$TAG" ]]; then
                TAG="${TAG}"
            else
                TAG="latest"
            fi
            IMAGE_NAME=$IMAGE_REGISTRY/vst:$TAG
        fi

        echo "Building Docker image: $IMAGE_NAME"

        BUILD_START_TIME=$(date +%s)

        # Add --no-cache flag if NO_CACHE is set
        CACHE_FLAG=""
        if [[ $NO_CACHE -eq 1 ]]; then
            CACHE_FLAG="--no-cache"
            echo "Building without Docker cache..."
        fi

        echo "Using optimized base image strategy for faster builds..."

        if [[ -n "$BASE_TAG" ]]; then
            BASE_IMAGE_TAG="$BASE_TAG"
        else
            BASE_IMAGE_TAG=${DEFAULT_TAGS["vst-base"]:-"latest"}
        fi
        BASE_IMAGE_NAME="$IMAGE_REGISTRY/vst-base:$BASE_IMAGE_TAG"

        if [[ ! -f "../../cicd_files/$ARCH/Dockerfile.app" ]]; then
            echo "[ERROR] Dockerfile.app not found in cicd_files/$ARCH/"
            cd - || exit 1
            return 1
        fi

        if [[ "$ARCH" == "aarch64" ]] || [[ "$ARCH" == "arm64" ]] || [[ "$ARCH" == "sbsa" ]]; then
            docker build $CACHE_FLAG --platform linux/arm64 --network=host -t $IMAGE_NAME --build-arg BASE_IMAGE="$BASE_IMAGE_NAME" --build-arg PKG_LOCATION="." -f "../../cicd_files/$ARCH/Dockerfile.app" .
        else
            docker build $CACHE_FLAG --network=host -t $IMAGE_NAME --build-arg BASE_IMAGE="$BASE_IMAGE_NAME" --build-arg PKG_LOCATION="." -f "../../cicd_files/$ARCH/Dockerfile.app" .
        fi

        # Check if Docker build was successful
        if [[ $? -ne 0 ]]; then
            echo -e "[ERROR] Docker build failed for image: $IMAGE_NAME"
            cd - || exit 1
            return 1
        fi
        echo "Docker build succeeded for image: $IMAGE_NAME"
        print_per_image_build_timing_line "$BUILD_START_TIME"

        if [[ $PUSH -eq 1 ]]; then
            echo "Pushing Docker image: $IMAGE_NAME"
            docker push $IMAGE_NAME
            # Check if Docker push was successful
            if [[ $? -ne 0 ]]; then
                echo -e "[ERROR] Docker push failed for image: $IMAGE_NAME"
                cd - || exit 1
                return 1
            fi
            echo "Docker push succeeded for image: $IMAGE_NAME"
        fi
        cd - || exit 1
    fi
}

# Function to build a nvstreasmer scaling helm chart
build_nvstreamer_app() {
    local package=$1

    # Build the ingress service
    echo "Building nvstreamer ingress service ..."
    cd deployment/scaling/ucf/nvstreamer-app/ingress/ucf|| exit 1
    rm -rf output
    ucf_ms_builder_cli service build -d .
    cd - || exit 1

    # Build the ingress service
    echo "Building nvstreamer service ..."
    cd deployment/ucf/nv-streamer/ || exit 1
    cp -rf manifest.yaml manifest.yaml_org
    cp -rf manifest.yaml_instance manifest.yaml
    rm -rf output
    ucf_ms_builder_cli service build -d .
    mv manifest.yaml_org manifest.yaml
    cd - || exit 1

    # Build the app module
    echo "Building nvstreamer app module ..."
    cd deployment/scaling/ucf/nvstreamer-app/nvstreamer || exit 1
    rm -rf nvstreamer-app-*
    ucf_app_builder_cli app build nvstreamer-app.yaml
    streamer_app_name=$(ls -d nvstreamer-app-* 2>/dev/null)
    if [[ -d "$streamer_app_name" ]]; then
        input_file="$streamer_app_name/charts/nvstreamer-instance/values.yaml"
        temp_file="$streamer_app_name/charts/nvstreamer-instance/temp_values.yaml"
        new_volume_claim_templates="
    volumeClaimTemplates:
    - metadata:
        name: data-storage
      spec:
        accessModes: [\"ReadWriteOnce\"]
        storageClassName: mdx-local-path
        resources:
          requests:
            storage: 300Gi
        "

        # Use awk to replace volumeClaimTemplates: []
        awk -v new_vct="$new_volume_claim_templates" '
        /volumeClaimTemplates: \[\]/ {
            print new_vct
            next
        }
        { print }
        ' "$input_file" > "$temp_file" && mv "$temp_file" "$input_file"

        tar czf "${streamer_app_name}.tgz" $streamer_app_name || { echo "[ERROR] Tar creation failed"; }
        rm -rf $streamer_app_name
    fi
    cd - || exit 1
}

# Function to build a vst helm chart
build_vst_app() {
    local package=$1
    echo "Executing build for all modules in deployment/scaling/ucf/"
    echo "Building all with cc_value=$cc_value, package=$package, container=$container"

    # Bult nvstreamer-app as a part of vst package
    build_nvstreamer_app

    # Build the ingress service
    echo "Building vst ingress service ..."
    cd deployment/scaling/ucf/ingress/ucf || exit 1
    rm -rf output
    ucf_ms_builder_cli service build -d .
    cd - || exit 1

    # Building the individual vst module chart
    for dir in deployment/scaling/ucf/*; do
        if [[ -d "$dir" ]] && [[ "$(basename "$dir")" != "vst-app" ]] && [[ "$(basename "$dir")" != "nvstreamer-app" ]] && [[ "$(basename "$dir")" != "ingress" ]] && [[ "$(basename "$dir")" != "sdr" ]] && [[ "$(basename "$dir")" != "redis" ]] && [[ "$(basename "$dir")" != "minio" ]]; then
            echo "Building module in $dir ..."
            cd "$dir" || exit 1
            rm -rf output
            ucf_ms_builder_cli service build -d .
            cd - || exit 1
        fi
    done

    # Build the SDR modules
    for dir in deployment/scaling/ucf/sdr/*; do
        echo "Building sdr module in $dir ..."
        cd "$dir" || exit 1
        rm -rf output
        ucf_ms_builder_cli service build -d .
        cd - || exit 1
    done

    if [[ $MINIO -eq 1 ]]; then
        echo "Building minio module ..."
        cd deployment/scaling/ucf/minio/ || exit 1
        rm -rf output
        ucf_ms_builder_cli service build -d .
        cd - || exit 1
    fi

    # Build the app module
    echo "Building app module ..."
    cd deployment/scaling/ucf/vst-app/ || exit 1
    if [[ $MINIO -eq 1 ]]; then
        mv scaling-app.yaml scaling-app.yaml_org
        mv scaling-app-build-params.yaml scaling-app-build-params.yaml_org
        cp -rf minio/scaling-app.yaml scaling-app.yaml
        cp -rf minio/scaling-app-build-params.yaml scaling-app-build-params.yaml
    fi
    rm -rf vst-app-*
    ucf_app_builder_cli app build scaling-app.yaml scaling-app-build-params.yaml
    if [[ $MINIO -eq 1 ]]; then
        mv scaling-app.yaml_org scaling-app.yaml
        mv scaling-app-build-params.yaml_org scaling-app-build-params.yaml
    fi
    vst_app_name=$(ls -d vst-app-* 2>/dev/null)
    if [[ -d "$vst_app_name" ]]; then
        tar czf "${vst_app_name}.tgz" $vst_app_name || { echo "[ERROR] Tar creation failed"; }
        rm -rf $vst_app_name
    fi
    cd - || exit 1

    if [[ $package -eq 1 ]]; then
        echo "Packaging vst-app helm charts"

        rm -rf vst-app-package*
        mkdir -p vst-app-package
        mkdir -p vst-app-package/k8s-deployment
        cp -rf deployment/scaling/docker-compose vst-app-package/
        cp -rf deployment/scaling/ucf/vst-app/override_values.yaml vst-app-package/k8s-deployment/vst-app-values.yml
        cp -rf deployment/scaling/ucf/nvstreamer-app/nvstreamer_upload.py vst-app-package/k8s-deployment/
        cp -rf deployment/scaling/ucf/nvstreamer-app/nvstreamer/override_values.yaml vst-app-package/k8s-deployment/nvstreamer-app-values.yml
        cp -rf deployment/scaling/ucf/redis/redis_app.yaml vst-app-package/k8s-deployment/mdx-redis.yml
        cp -rf deployment/scaling/ucf/redis/mdx-local-path-provisioner.yaml vst-app-package/k8s-deployment/mdx-local-path-provisioner.yml

        mkdir -p vst-app-package/k8s-deployment/charts
        cp -rf deployment/scaling/ucf/vst-app/vst-app-* vst-app-package/k8s-deployment/charts/
        cp -rf deployment/scaling/ucf/nvstreamer-app/nvstreamer/nvstreamer-app-* vst-app-package/k8s-deployment/charts/

        # Create tar of vst scaling package
        tar czf vst-app-package.tgz vst-app-package/ || { echo "[ERROR] Tar creation failed"; }
    fi
}

# Function to build streamprocessing-app helm chart
build_streamprocessing_app() {
    local package=$1
    local build_root
    build_root=$(pwd)
    echo "Executing build for streamprocessing-app in deployment/scaling/ucf/"
    echo "Building: streamprocessing, sdr/vst-streamprocessing, sensor, postgres, ingress (nginx-streamprocessing)"

    # Bult nvstreamer-app as a part of vst package
    build_nvstreamer_app

    # Build ingress with nginx-streamprocessing.conf (copy to nginx.conf, build, restore)
    echo "Building vst ingress service with nginx-streamprocessing config ..."
    cd "$build_root/deployment/scaling/ucf/ingress/ucf/configs" || exit 1
    cp -f nginx.conf nginx.conf_org
    cp -f nginx-streamprocessing.conf nginx.conf
    cd "$build_root/deployment/scaling/ucf/ingress/ucf" || exit 1
    rm -rf output
    ucf_ms_builder_cli service build -d .
    cd configs || exit 1
    mv -f nginx.conf_org nginx.conf
    cd "$build_root" || exit 1

    # Build postgres module
    echo "Building postgres module ..."
    cd "$build_root/deployment/scaling/ucf/postgres" || exit 1
    rm -rf output
    ucf_ms_builder_cli service build -d .
    cd "$build_root" || exit 1

    # Build sdr/vst-streamprocessing
    echo "Building sdr/vst-streamprocessing module ..."
    cd "$build_root/deployment/scaling/ucf/sdr/vst-streamprocessing" || exit 1
    rm -rf output
    ucf_ms_builder_cli service build -d .
    cd "$build_root" || exit 1

    # Build sensor module
    echo "Building sensor module ..."
    cd "$build_root/deployment/scaling/ucf/sensor" || exit 1
    rm -rf output
    ucf_ms_builder_cli service build -d .
    cd "$build_root" || exit 1

    # Build streamprocessing module
    echo "Building streamprocessing module ..."
    cd "$build_root/deployment/scaling/ucf/streamprocessing" || exit 1
    rm -rf output
    ucf_ms_builder_cli service build -d .
    cd "$build_root" || exit 1

    # Build the streamprocessing-app module
    echo "Building streamprocessing-app module ..."
    cd "$build_root/deployment/scaling/ucf/vst-streamprocessing-app/" || exit 1
    rm -rf vst-streamprocessing-app-*
    ucf_app_builder_cli app build vst-streamprocessing-app.yaml
    streamprocessing_app_name=$(ls -d vst-streamprocessing-app-* 2>/dev/null)
    if [ -d "$streamprocessing_app_name" ]; then
        tar czf "${streamprocessing_app_name}.tgz" "$streamprocessing_app_name" || { echo "[ERROR] Tar creation failed"; }
        rm -rf $streamprocessing_app_name
    fi
    cd "$build_root" || exit 1

    if [ $package -eq 1 ]; then
        echo "Packaging streamprocessing-app helm charts"

        rm -rf vst-streamprocessing-app-package*
        mkdir -p vst-streamprocessing-app-package
        mkdir -p vst-streamprocessing-app-package/k8s-deployment
        cp -rf deployment/stream-processing/docker-compose vst-streamprocessing-app-package/
        cp -rf deployment/scaling/ucf/vst-streamprocessing-app/override_values.yaml vst-streamprocessing-app-package/k8s-deployment/streamprocessing-app-values.yml
        cp -rf deployment/scaling/ucf/redis/redis_app.yaml vst-streamprocessing-app-package/k8s-deployment/mdx-redis.yml
        cp -rf deployment/scaling/ucf/redis/mdx-local-path-provisioner.yaml vst-streamprocessing-app-package/k8s-deployment/mdx-local-path-provisioner.yml
        cp -rf deployment/scaling/ucf/nvstreamer-app/nvstreamer_upload.py vst-streamprocessing-app-package/k8s-deployment/
        cp -rf deployment/scaling/ucf/nvstreamer-app/nvstreamer/override_values.yaml vst-streamprocessing-app-package/k8s-deployment/nvstreamer-app-values.yml


        mkdir -p vst-streamprocessing-app-package/k8s-deployment/charts
        cp -rf deployment/scaling/ucf/vst-streamprocessing-app/vst-streamprocessing-app-* vst-streamprocessing-app-package/k8s-deployment/charts/
        cp -rf deployment/scaling/ucf/nvstreamer-app/nvstreamer/nvstreamer-app-* vst-streamprocessing-app-package/k8s-deployment/charts/

        # Create tar of streamprocessing-app scaling package
        tar czf vst-streamprocessing-app-package.tgz vst-streamprocessing-app-package/ || { echo "[ERROR] Tar creation failed"; }
    fi
}

if [[ ${#MODULES[@]} -eq 0 ]]; then
    # No modules specified
    if [[ $TESTS -eq 1 ]]; then
        echo "Building unit tests (all modules)"
        if [[ "$ARCH" == "aarch64" ]] || [[ "$ARCH" == "arm64" ]]; then
            make cc=1 tests
        else
            make cc=0 tests
        fi

        if [[ $? -eq 0 ]]; then
            echo "Unit tests build successful"
            echo "Run tests with: ./vst_test"
        else
            echo "Unit tests build failed"
            exit 1
        fi
        exit 0
    elif [[ $BASE_IMAGE -eq 1 ]]; then
        echo "Building base image only"
        build_base_image $PUSH
        exit 0
    elif [[ $PACKAGE -eq 0 ]] && [[ $CONTAINER -eq 0 ]] && [[ $VSTAPP -eq 0 ]] && [[ $NVSTREAMER -eq 0 ]] && [[ $VSTMONOLITH -eq 0 ]]; then
        echo "No modules specified, default build"
        if [[ "$ARCH" == "aarch64" ]] || [[ "$ARCH" == "arm64" ]]; then
            build_all 1 0 0
        else
            build_all 0 0 0
        fi
    elif [[ $PACKAGE -eq 1 ]]; then
        echo "Packaging all modules"

        if [[ $VSTAPP -eq 1 ]]; then
            echo "Building helm chart package for vst-app"
            build_vst_app 1
        elif [ $STREAMPROCESSINGAPP -eq 1 ]; then
            echo "Building helm chart package for streamprocessing-app"
            build_streamprocessing_app 1
        else
            if [[ "$ARCH" == "aarch64" ]] || [[ "$ARCH" == "arm64" ]]; then
                build_all 1 1 0
            else
                build_all 0 1 0
            fi
        fi
    elif [[ $CONTAINER -eq 1 ]]; then

        if [[ $NVSTREAMER_INGRESS -eq 1 ]]; then
            echo "Build nvstreamer ingress container"
            if [[ -n "$TAG" ]]; then
                imagename="$IMAGE_REGISTRY/nvstreamer-ingress:${TAG}"
            else
                TAG=${DEFAULT_TAGS[nvstreamer-ingress]:-"latest"}
                imagename="$IMAGE_REGISTRY/nvstreamer-ingress:${TAG}"
            fi
            cd deployment/scaling/ucf/nvstreamer-app/ingress/ || exit 1
            echo "Building Docker image: $imagename"
            docker buildx build --platform linux/amd64,linux/arm64 -t $imagename --push .
            cd - || exit 1
            exit 0
        fi

        if [[ $INGRESS -eq 1 ]]; then
            echo "Build ingress container"
            if [[ -n "$TAG" ]]; then
                imagename="$IMAGE_REGISTRY/vst-ingress:${TAG}"
            else
                TAG=${DEFAULT_TAGS[ingress]:-"latest"}
                imagename="$IMAGE_REGISTRY/vst-ingress:${TAG}"
            fi

            # Build the vios-ui and stage its dist output into the ingress vst-ui dir
            INGRESS_BUILD_ROOT=$(pwd)
            UI_DIR="$INGRESS_BUILD_ROOT/ui/vios-ui"
            VST_UI_DIR="$INGRESS_BUILD_ROOT/deployment/scaling/ingress/vst-ui"

            echo "Building vios-ui in $UI_DIR ..."
            cd "$UI_DIR" || { echo "[ERROR] Cannot find vios-ui directory: $UI_DIR"; exit 1; }
            npm run install:link || { echo "[ERROR] npm run install:link failed"; exit 1; }
            npm run build || { echo "[ERROR] npm run build failed"; exit 1; }

            if [[ ! -d "dist" ]]; then
                echo "[ERROR] vios-ui dist directory not found after build"
                exit 1
            fi

            echo "Staging vios-ui dist into $VST_UI_DIR ..."
            find "$VST_UI_DIR" -mindepth 1 -not -name '.gitkeep' -delete
            cp -rf dist/. "$VST_UI_DIR/" || { echo "[ERROR] Failed to copy vios-ui dist to $VST_UI_DIR"; exit 1; }
            cd "$INGRESS_BUILD_ROOT" || exit 1

            cd deployment/scaling/ingress/ || exit 1
            echo "Building Docker image: $imagename"
            if [[ $PUSH -eq 1 ]]; then
                docker buildx build --platform linux/amd64,linux/arm64 -t $imagename --push .
            else
                docker buildx build -t $imagename --load .
            fi
            cd - || exit 1
            exit 0
        fi

        if [[ $MCP -eq 1 ]]; then
            echo "Build MCP container"
            if [[ -n "$TAG" ]]; then
                imagename="$IMAGE_REGISTRY/vst-mcp:${TAG}"
            else
                TAG=${DEFAULT_TAGS[mcp]:-"latest"}
                imagename="$IMAGE_REGISTRY/vst-mcp:${TAG}"
            fi
            cd mcp/ || exit 1
            echo "Building Docker image: $imagename"
            docker buildx build --platform linux/amd64,linux/arm64 -t $imagename --push .
            cd - || exit 1
            exit 0
        fi

        # For the nvstreamer container, build the vios-ui and stage its dist
        # into webroot before packaging so it is baked into the image.
        if [[ $NVSTREAMER -eq 1 ]]; then
            build_vios_ui_webroot
        fi

        echo "Building and containerizing all modules"
        if [[ "$ARCH" == "aarch64" ]] || [[ "$ARCH" == "arm64" ]]; then
            build_all 1 1 1
        else
            build_all 0 1 1
        fi
    elif [[ $NVSTREAMER -eq 1 ]]; then
        echo "Building nvstreamer"
        if [[ "$ARCH" == "aarch64" ]] || [[ "$ARCH" == "arm64" ]]; then
            build_all 1 0 0
        else
            build_all 0 0 0
        fi
    elif [[ $VSTMONOLITH -eq 1 ]]; then
        echo "Building vst-monolith"
        if [[ "$ARCH" == "aarch64" ]] || [[ "$ARCH" == "arm64" ]]; then
            build_all 1 0 0
        else
            build_all 0 0 0
        fi
    elif [ $VSTAPP -eq 1 ]; then
        echo "Building helm chart for vst-app"
        build_vst_app 0
    elif [ $STREAMPROCESSINGAPP -eq 1 ]; then
        echo "Building helm chart for streamprocessing-app"
        build_streamprocessing_app 0
    fi

    # Check if build_all function completed successfully
    if [[ $? -ne 0 ]]; then
        echo -e "[ERROR] Build or packaging failed. Exiting script."
        exit 1
    fi
else
    # Build tests - always builds ALL modules (storage + recorder + dependencies)
    if [[ $TESTS -eq 1 ]]; then
        echo ""
        echo "======================================================="
        echo "Building unit tests"
        echo "======================================================="
        echo ""
        echo "Test modules: storage (29 tests) + recorder (20 tests)"
        echo "Total: 49 comprehensive unit tests"
        echo ""
        
        # Call main Makefile's tests target
        if [[ "$ARCH" == "aarch64" ]] || [[ "$ARCH" == "arm64" ]]; then
            make cc=1 tests
        else
            make cc=0 tests
        fi
        
        if [[ $? -eq 0 ]]; then
            echo ""
            echo "======================================================="
            echo "✅ Unit tests build successful"
            echo "======================================================="
            echo ""
            echo "Test binary: ./vst_test"
            echo ""
            echo "Run tests:"
            echo "  ./vst_test                                    # All 49 tests"
            echo "  ./vst_test --gtest_list_tests                 # List tests"
            echo "  ./vst_test --gtest_filter=*Upload*            # Storage tests"
            echo "  ./vst_test --gtest_filter=StreamRecorderTest.* # Recorder tests"
            echo ""
        else
            echo ""
            echo "======================================================="
            echo "❌ Unit tests build failed"
            echo "======================================================="
            echo ""
            exit 1
        fi
        exit 0
    elif [[ $PACKAGE -eq 0 ]] && [[ $CONTAINER -eq 0 ]]; then
        if [[ ${#MODULES[@]} -gt 1 ]]; then
            echo "[ERROR] Multiple modules are supported only in case of package/container, Exiting script ..."
            exit 1
        fi
        echo "Building specified modules"
        for module in "${MODULES[@]}"; do
            if [[ "$ARCH" == "aarch64" ]] || [[ "$ARCH" == "arm64" ]]; then
                build_module "$module" 1
            else
                build_module "$module" 0
            fi

            if [[ $? -ne 0 ]]; then
                echo -e "[ERROR] build failed for module: $module. Exiting script."
                exit 1
            fi
        done
    elif [[ $PACKAGE -eq 1 ]]; then
        echo "Packaging specified modules"
        for module in "${MODULES[@]}"; do
            if [[ ${#MODULES[@]} -gt 1 ]]; then
                # Clean previous module before building new
                if [[ "$ARCH" == "aarch64" ]] || [[ "$ARCH" == "arm64" ]]; then
                    build_module "$module" 1 package 1
                else
                    build_module "$module" 0 package 1
                fi
            fi
            if [[ "$ARCH" == "aarch64" ]] || [[ "$ARCH" == "arm64" ]]; then
                build_module "$module" 1 package
            else
                build_module "$module" 0 package
            fi

            if [[ $? -ne 0 ]]; then
                echo -e "[ERROR] Packaging failed for module: $module. Exiting script."
                exit 1
            fi
        done
    elif [[ $CONTAINER -eq 1 ]]; then
        echo "Building and containerizing specified modules"
        declare -a cont_array
        OVERALL_START_TIME=$(date +%s)
        MODULE_COUNT=0
        for module in "${MODULES[@]}"; do
            # Use the specified TAG if available, otherwise use the default tag
            if [[ -n "$TAG" ]]; then
                imagename="$IMAGE_REGISTRY/vst-${module}:${TAG}"
            else
                TAG=${DEFAULT_TAGS[$module]:-"latest"}
                imagename="$IMAGE_REGISTRY/vst-${module}:${TAG}"
            fi
            echo "Setting image name for module $module: $imagename"

            # Clean previous module before building new
            if [[ ${#MODULES[@]} -gt 1 ]]; then
                if [[ "$ARCH" == "aarch64" ]] || [[ "$ARCH" == "arm64" ]]; then
                    build_module "$module" 1 package 1
                else
                    build_module "$module" 0 package 1
                fi
            fi

            # Build the module
            if [[ "$ARCH" == "aarch64" ]] || [[ "$ARCH" == "arm64" ]]; then
                build_module "$module" 1 package
            else
                build_module "$module" 0 package
            fi

            if [[ $? -ne 0 ]]; then
                echo -e "[ERROR] Build or packaging failed for module: $module. Exiting script."
                exit 1
            fi

            cont_array+=("$imagename")

            # Change to the output directory
            cd "out/$ARCH" || exit 1

            echo "Building Docker image: $imagename"

            MODULE_BUILD_START_TIME=$(date +%s)

            # Add --no-cache flag if NO_CACHE is set
            CACHE_FLAG=""
            if [[ $NO_CACHE -eq 1 ]]; then
                CACHE_FLAG="--no-cache"
                echo "Building without Docker cache..."
            fi

            echo "Using optimized base image strategy for faster builds..."

            if [[ -n "$BASE_TAG" ]]; then
                BASE_IMAGE_TAG="$BASE_TAG"
            else
                BASE_IMAGE_TAG=${DEFAULT_TAGS["vst-base"]:-"latest"}
            fi
            BASE_IMAGE_NAME="$IMAGE_REGISTRY/vst-base:$BASE_IMAGE_TAG"

            if [[ ! -f "../../cicd_files/$ARCH/Dockerfile.app" ]]; then
                echo "[ERROR] Dockerfile.app not found in cicd_files/$ARCH/"
                exit 1
            fi

            if [[ "$ARCH" == "aarch64" ]] || [[ "$ARCH" == "arm64" ]] || [[ "$ARCH" == "sbsa" ]]; then
                docker build $CACHE_FLAG --platform linux/arm64 --network=host -t "$imagename" --build-arg BASE_IMAGE="$BASE_IMAGE_NAME" --build-arg PKG_LOCATION="." -f "../../cicd_files/$ARCH/Dockerfile.app" .
            else
                docker build $CACHE_FLAG --network=host -t "$imagename" --build-arg BASE_IMAGE="$BASE_IMAGE_NAME" --build-arg PKG_LOCATION="." -f "../../cicd_files/$ARCH/Dockerfile.app" .
            fi

            # Check if Docker build was successful
            if [[ $? -ne 0 ]]; then
                echo -e "[ERROR] Docker build failed for image: $imagename"
                exit 1
            fi
            echo -e "Docker build succeeded for image: $imagename"
            print_per_image_build_timing_line "$MODULE_BUILD_START_TIME"

            if [[ $PUSH -eq 1 ]]; then
                echo "Pushing Docker image: $imagename"
                docker push "$imagename"

                # Check if Docker push was successful
                if [[ $? -ne 0 ]]; then
                    echo -e "[ERROR] Docker push failed for image: $imagename"
                    exit 1
                fi
                echo -e "Docker push succeeded for image: $imagename"
            fi

            MODULE_COUNT=$((MODULE_COUNT + 1))

            # Change back to the previous directory
            cd - || exit 1
        done

        print_container_build_summary_footer "$OVERALL_START_TIME" "$MODULE_COUNT"
    fi
fi