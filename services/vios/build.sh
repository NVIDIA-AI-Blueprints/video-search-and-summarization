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
ARCH_EXPLICIT=0   # set to 1 when the user passes arch=... (drives clean targeting)
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
TOOLCHAIN=0       # ./build.sh toolchain → build the compile-toolchain image
BUILD_ALL=0       # ./build.sh all → toolchain → base → module containers → nvstreamer
MULTIARCH=0       # ./build.sh multiarch tag=X → build+push amd64+arm64, then a multiarch manifest
NO_AUTO_DEPS=0    # ./build.sh ... no-auto-deps → fail instead of auto-building missing toolchain/base
MODULES=()  # Array to hold the modules

# Toolchain image names. Must match the Makefile defaults (AARCH64_CC_IMAGE,
# X86_BUILD_IMAGE) so the make wrapper picks up the same image we build here.
# Override via env var to use a pre-pulled image from a registry:
#   export X86_BUILD_IMAGE=my-registry.example.com/vios-build:custom
X86_BUILD_IMAGE="${X86_BUILD_IMAGE:-vios-build:x86-devel-ubuntu24.04-cuda13.2.0}"
# One aarch64 cross-compile toolchain for all aarch64 targets — Orin (Jetson
# iGPU), Thor/SBSA, and DGX-Spark. Platform is detected at runtime; no separate
# Jetson/L4T toolchain is needed.
AARCH64_CC_IMAGE="${AARCH64_CC_IMAGE:-vios-build:aarch64-devel-ubuntu24.04-cuda13.2.0}"
# Registry and org for built images. Defaults to a bare local namespace so
# local builds work out of the box (e.g. vios/vst:latest, nvstreamer:latest);
# no registry is hardcoded in the public tree. Override to push elsewhere:
#   export IMAGE_REGISTRY=my-registry.example.com/vios
IMAGE_REGISTRY="${IMAGE_REGISTRY:-vios}"
NVSTREAMER_IMAGE_REGISTRY="${NVSTREAMER_IMAGE_REGISTRY:-nvstreamer}"

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
    echo "QUICK START (from a fresh clone):"
    echo "  ./build.sh container module=sensor,streamprocessing"
    echo "       ^ auto-builds the compile toolchain + base image on first run"
    echo "  ./build.sh all                  # build everything for a full deploy"
    echo
    echo "Common Options:"
    echo "  arch=<arch>        Specify the architecture (amd64/x86_64 or arm64/aarch64). Default is x86_64/amd64."
    echo "                     arch=orin (alias arch=jetson) is accepted as an alias for aarch64: one unified"
    echo "                     aarch64 build runs on Orin (Jetson iGPU), Thor/SBSA, and DGX-Spark (runtime detection)."
    echo "  module=<modules>   Comma-separated list of modules to build (e.g., sensor,rtspserver,streamprocessing)."
    echo "  package            Build and package the modules."
    echo "  container          Build, package, and create Docker containers. Auto-builds the toolchain"
    echo "                     and base image on first run; subsequent runs reuse them."
    echo "  toolchain          Build the compile-toolchain container ONLY (auto-invoked by other paths"
    echo "                     when missing; you rarely need to call this directly)."
    echo "  base-container     Build only the runtime base image (auto-invoked by 'container' when missing)."
    echo "  all                One-shot: toolchain -> base -> module containers (sensor + streamprocessing"
    echo "                     by default, or whatever module=... lists) -> nvstreamer container."
    echo "  multiarch          Build+push amd64 and arm64 images (per-arch tags) then assemble one"
    echo "                     multi-arch manifest via 'docker buildx imagetools'. Needs tag=<manifest-tag>"
    echo "                     (e.g. tag=2.1.0-26.05.4; per-arch tags are derived as -amd64-/-arm64-) and a"
    echo "                     target (module=<list> and/or nvstreamer). Set IMAGE_REGISTRY to a pushable"
    echo "                     registry first. amd64 push overlaps the arm64 build."
    echo "  multiarch all      Multi-arch equivalent of 'all': builds sensor + streamprocessing + nvstreamer"
    echo "                     for both arches (needs tag=<manifest-tag>). Ingress is separate (already"
    echo "                     multi-arch): ./build.sh container ingress push=1 tag=<tag>."
    echo "  no-auto-deps       Disable the auto-build of toolchain / base. Use when you want strict failure"
    echo "                     if those images are missing (CI; pulled-from-registry workflows)."
    echo "  tag=<name>         Docker image tag for application containers (used with container option)."
    echo "  base-tag=<name>    Docker tag for base image (default: latest)."
    echo "  push=<0|1>         Push Docker images to the registry (used with container option)."
    echo
    echo "Image-tag overrides (CLI flags; take precedence over env vars below):"
    echo "  toolchain-image=<ref>  Override the compile toolchain image (arch-aware:"
    echo "                          sets X86_BUILD_IMAGE for x86_64, AARCH64_CC_IMAGE for arm64)."
    echo "                          Same value as 'arch=arm64' takes -> AARCH64_CC_IMAGE."
    echo "  image-registry=<ref>   Override registry/org prefix for module + base images"
    echo "                          (replaces IMAGE_REGISTRY env var; default 'vios')."
    echo "  nvstreamer-image=<ref> Override the full NVStreamer image repository"
    echo "                          (replaces NVSTREAMER_IMAGE env var; default 'nvstreamer')."
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
    echo "  help               Show this help message."
    echo
    echo "Toolchain images (set via env, falling back to Makefile defaults):"
    echo "  X86_BUILD_IMAGE          (default: vios-build:x86-devel-ubuntu24.04-cuda13.2.0)"
    echo "  AARCH64_CC_IMAGE         (default: vios-build:aarch64-devel-ubuntu24.04-cuda13.2.0)  # all aarch64 incl. Orin"
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
    echo "  # Orin/Jetson build the same unified aarch64 image (alias for arch=aarch64):"
    echo "  ./build.sh arch=orin container module=sensor,streamprocessing"
    echo ""
    echo "  # Multi-arch (amd64 + arm64) build, push, and manifest in one command:"
    echo "  export IMAGE_REGISTRY=nvcr.io/rxczgrvsg8nx/vst-dev"
    echo "  ./build.sh multiarch tag=2.1.0-26.05.4 module=sensor,streamprocessing"
    echo "  ./build.sh multiarch tag=2.1.0-26.05.4 nvstreamer base-tag=2.1.0-runtime-26.05.4"
    echo "  ./build.sh arch=multiarch all tag=2.1.0-26.05.4   # sensor + streamprocessing + nvstreamer, both arches"
    echo "  ./build.sh container ingress push=1 tag=2.1.0-26.05.4   # ingress (already multi-arch), built separately"
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
            ARCH_EXPLICIT=1
            if [[ "$ARCH" = "arm64" ]]; then ARCH="aarch64"; fi
            if [[ "$ARCH" = "amd64" ]]; then ARCH="x86_64"; fi
            # Orin/Jetson build the unified aarch64 target (runtime platform
            # detection, no JETSON_PLATFORM define, no L4T rootfs). Accept them
            # as aliases for aarch64.
            if [[ "$ARCH" = "jetson" ]] || [[ "$ARCH" = "orin" ]]; then ARCH="aarch64"; fi
            # arch=multiarch is an alias for the `multiarch` subcommand (builds
            # both amd64 + arm64). ARCH itself is irrelevant then (each per-arch
            # sub-build sets its own), so reset it to the default.
            if [[ "$ARCH" = "multiarch" ]]; then MULTIARCH=1; ARCH="x86_64"; fi
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
        toolchain) TOOLCHAIN=1;;
        all) BUILD_ALL=1;;
        multiarch) MULTIARCH=1;;
        no-auto-deps) NO_AUTO_DEPS=1;;
        # CLI-flag alternatives to the X86_BUILD_IMAGE / AARCH64_CC_IMAGE /
        # IMAGE_REGISTRY / NVSTREAMER_IMAGE env vars. Applied AFTER the
        # whole arg list is parsed so `arch=arm64 toolchain-image=…` works
        # regardless of arg order. CLI > env > default.
        toolchain-image=*) TOOLCHAIN_IMAGE_OVERRIDE="${1#*=}";;
        image-registry=*) IMAGE_REGISTRY_OVERRIDE="${1#*=}";;
        nvstreamer-image=*) NVSTREAMER_IMAGE_OVERRIDE="${1#*=}";;
        help) show_help; exit 0;;
        *) echo "Unknown parameter passed: $1"; show_help; exit 1;;
    esac
    shift
done

# Apply CLI flag overrides on top of env var defaults. The overrides take
# precedence (CLI > env > built-in default). Toolchain override is arch-
# aware so a single `toolchain-image=…` flag works for both x86 and arm.
if [[ -n "${TOOLCHAIN_IMAGE_OVERRIDE:-}" ]]; then
    if [[ "$ARCH" == "aarch64" ]] || [[ "$ARCH" == "arm64" ]] || [[ "$ARCH" == "sbsa" ]]; then
        AARCH64_CC_IMAGE="$TOOLCHAIN_IMAGE_OVERRIDE"
    else
        X86_BUILD_IMAGE="$TOOLCHAIN_IMAGE_OVERRIDE"
    fi
fi
if [[ -n "${IMAGE_REGISTRY_OVERRIDE:-}" ]]; then
    IMAGE_REGISTRY="$IMAGE_REGISTRY_OVERRIDE"
fi
if [[ -n "${NVSTREAMER_IMAGE_OVERRIDE:-}" ]]; then
    NVSTREAMER_IMAGE="$NVSTREAMER_IMAGE_OVERRIDE"
fi

# Export the resolved toolchain images so the Makefile's `?=` picks them up
# (build.sh sets them as shell vars but make runs in a child process). Without
# this, an env / toolchain-image= override never reaches the containerised
# `make cc=1` step.
export AARCH64_CC_IMAGE X86_BUILD_IMAGE

# Print all variables
echo "ARCH=$ARCH"
echo "AARCH64_CC_IMAGE=$AARCH64_CC_IMAGE"
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
    [vst-base]="2.1.0-runtime-26.05.4"
)

# Function to build base image for faster container builds
build_base_image() {
    local push=$1

    echo "==================================================================================="
    echo "Building VST Runtime Base Image (one-time build, reused by later container builds)"
    echo "==================================================================================="
    echo "This bakes all system packages into a base image so subsequent container builds reuse it and run faster (optimization)."
    echo ""

    # Determine the base image name and tag
    if [[ -n "$BASE_TAG" ]]; then
        BASE_IMAGE_NAME="$IMAGE_REGISTRY/vst-base:${BASE_TAG}"
    else
        # Default to the same base tag the container build consumes (DEFAULT_TAGS),
        # so "base-container" and "container" agree without passing base-tag= to both.
        BASE_IMAGE_NAME="$IMAGE_REGISTRY/vst-base:${DEFAULT_TAGS["vst-base"]:-latest}"
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

# ============================================================================
# Toolchain / base auto-detect helpers
# ============================================================================
# These let `./build.sh container module=…` "just work" from a fresh clone.
# Previously the user had to manually run a verbose `docker build` for the
# toolchain (README section A) and `./build.sh base-container` (section B)
# before any module build would succeed. Now both are detected on demand
# and built automatically. Set `no-auto-deps` to revert to strict failure.

# Resolve the toolchain image tag for the active ARCH. The Makefile reads
# the same env vars, so build_toolchain_image and the make wrapper agree.
get_toolchain_image_name() {
    if [[ "$ARCH" == "aarch64" ]] || [[ "$ARCH" == "arm64" ]] || [[ "$ARCH" == "sbsa" ]]; then
        echo "$AARCH64_CC_IMAGE"
    else
        echo "$X86_BUILD_IMAGE"
    fi
}

# Cheap probe: returns 0 iff the named image is present in the local Docker
# image store. Used by the ensure_* functions to avoid redundant builds.
image_exists() {
    local image_ref="$1"
    docker image inspect "$image_ref" >/dev/null 2>&1
}

# Build the compile-toolchain container for the active ARCH. Standalone
# subcommand entry point (`./build.sh toolchain`) and auto-build fallback.
#
# Push semantics mirror build_base_image: the explicit subcommand path
# passes $PUSH (so `./build.sh toolchain push=1` pushes), and the
# auto-build path (ensure_toolchain_image) passes 0 (auto-built deps are
# never pushed implicitly — the user has to ask for that explicitly).
build_toolchain_image() {
    local push="${1:-0}"
    local image_name
    image_name=$(get_toolchain_image_name)

    echo "======================================================="
    echo "Building VIOS Compile Toolchain Image (one-time build)"
    echo "======================================================="
    echo "Image: $image_name"
    echo "Arch:  $ARCH"
    echo ""

    local context dockerfile_arg=""
    if [[ "$ARCH" == "aarch64" ]] || [[ "$ARCH" == "arm64" ]] || [[ "$ARCH" == "sbsa" ]]; then
        context="cicd_files/aarch64/devel"
        # arm64 devel/Dockerfile uses the default filename, no -f needed.
    else
        context="cicd_files/x86_64/devel"
        # x86_64 ships the file as Dockerfile.devel (legacy name).
        dockerfile_arg="-f $context/Dockerfile.devel"
    fi

    if [[ ! -d "$context" ]]; then
        echo "[ERROR] Toolchain build context missing: $context"
        echo "        This usually means the repo wasn't cloned with submodules,"
        echo "        OR the script is being run from outside the services/vios/ dir."
        exit 1
    fi

    local cache_flag=""
    [[ $NO_CACHE -eq 1 ]] && cache_flag="--no-cache"

    local t0
    t0=$(date +%s)

    # shellcheck disable=SC2086  # we want word-splitting on $dockerfile_arg / $cache_flag
    docker build $cache_flag --network=host -t "$image_name" $dockerfile_arg "$context"

    if [[ $? -ne 0 ]]; then
        echo "[ERROR] Toolchain image build failed: $image_name"
        exit 1
    fi

    echo ""
    echo "Toolchain image built: $image_name"
    print_per_image_build_timing_line "$t0" "$push"
    echo ""

    # Optional push. Only fires when build_toolchain_image was called with
    # push=1 (i.e. explicit `./build.sh toolchain push=1`). The auto-build
    # path (ensure_toolchain_image) always passes 0, so a `./build.sh
    # container push=1` against a fresh clone won't accidentally push the
    # auto-built toolchain to a registry.
    if [[ $push -eq 1 ]]; then
        # Warn (but don't block) on default local-only tags: pushing
        # `vios-build:x86-devel-ubuntu24.04-cuda13.2.0` would target Docker Hub, which
        # is rarely intended. The right pattern is to set X86_BUILD_IMAGE
        # / AARCH64_CC_IMAGE to a fully-qualified registry path first.
        if [[ "$image_name" != *"/"*"/"* ]]; then
            echo "[WARN] Pushing without an explicit registry prefix: '$image_name'"
            echo "       Docker will target Docker Hub. To push elsewhere, export"
            echo "       X86_BUILD_IMAGE / AARCH64_CC_IMAGE with a registry prefix:"
            echo "         X86_BUILD_IMAGE=my-registry.example.com/vios-build:x86-devel-ubuntu24.04-cuda13.2.0 \\"
            echo "         ./build.sh toolchain push=1"
        fi
        echo "Pushing toolchain image: $image_name"
        docker push "$image_name"
        if [[ $? -ne 0 ]]; then
            echo "[ERROR] Toolchain image push failed: $image_name"
            exit 1
        fi
        echo "Toolchain image pushed: $image_name"
    fi
}

# Idempotent: ensure the toolchain image is present locally. Build it if not.
# Called before any path that invokes `make` (compile / package / clean /
# tests / container — all eventually run `docker run -v $(TOP):/root $IMG`
# inside the Makefile, so $IMG must exist).
ensure_toolchain_image() {
    local image_name
    image_name=$(get_toolchain_image_name)

    # Always ask the registry for the latest published toolchain image. 'docker
    # pull' is digest-aware (no-op if the local copy matches the registry digest;
    # pulls only changed layers otherwise), so an in-place tag overwrite is picked
    # up while an unchanged tag costs just a manifest check. Fall back to a cached
    # local copy only when the registry is unreachable, and build as a last resort.
    echo "[auto-deps] Ensuring latest toolchain image: $image_name"
    if docker pull "$image_name"; then
        echo "[auto-deps] Toolchain image up to date (pulled if changed): $image_name"
        return 0
    fi
    if image_exists "$image_name"; then
        echo "[auto-deps] Pull failed (registry unreachable?); using cached local toolchain image: $image_name"
        return 0
    fi
    echo "[auto-deps] Toolchain image not available from registry and not cached locally."

    if [[ $NO_AUTO_DEPS -eq 1 ]]; then
        echo "[ERROR] Toolchain image not found locally or in registry: $image_name"
        echo "        Build it explicitly with:   ./build.sh toolchain"
        echo "        Or omit 'no-auto-deps' to let this script build it for you."
        exit 1
    fi

    echo "[auto-deps] Toolchain image unavailable — building it now ($image_name)."
    echo "            Pass 'no-auto-deps' to fail instead of auto-building."
    echo "            (auto-built deps are never pushed; use './build.sh toolchain push=1' explicitly)"
    build_toolchain_image 0   # 0 = never push from the auto-build path
}

# Idempotent: ensure the base image (vst-base) is present locally. Build it
# if not. Called before any CONTAINER build (Dockerfile.app FROM=$BASE_IMAGE).
ensure_base_image() {
    local base_tag base_image_name
    if [[ -n "$BASE_TAG" ]]; then
        base_tag="$BASE_TAG"
    else
        base_tag="${DEFAULT_TAGS["vst-base"]:-latest}"
    fi
    base_image_name="$IMAGE_REGISTRY/vst-base:$base_tag"

    # Always ask the registry for the latest published base image. 'docker pull' is
    # digest-aware: if the local copy already matches the registry digest it is a
    # no-op ("Image is up to date", no download); if the tag was overwritten in
    # place it pulls only the changed layers. So an updated base is picked up while
    # an unchanged tag costs just a manifest check. Fall back to a cached local copy
    # only when the registry is unreachable, and build only as a last resort.
    echo "[auto-deps] Ensuring latest VST Runtime base-image: $base_image_name"
    if docker pull "$base_image_name"; then
        echo "[auto-deps] Base-image up to date (pulled if changed): $base_image_name"
        return 0
    fi
    if image_exists "$base_image_name"; then
        echo "[auto-deps] Pull failed (registry unreachable?); using cached local base-image: $base_image_name"
        return 0
    fi
    echo "[auto-deps] Base-image not available from registry and not cached locally."

    if [[ $NO_AUTO_DEPS -eq 1 ]]; then
        echo "[ERROR] VST Runtime base-image not found locally or in registry: $base_image_name"
        echo "        Build+publish it once with:  ./build.sh base-container push=1"
        echo "        Or omit 'no-auto-deps' to let this script build it for you."
        exit 1
    fi

    echo "[auto-deps] Base image unavailable — building it now ($base_image_name)."
    echo "            Pass 'no-auto-deps' to fail instead of auto-building."
    build_base_image 0  # 0 = don't push during auto-build
}

# Build vios-ui with npm from the compile toolchain image, so the host does
# not need Node.js or npm and those tools stay out of public runtime images.
# Run as the invoking user to keep generated files writable outside Docker.
build_vios_ui() {
    local build_root="$1"
    local ui_dir="$build_root/ui/vios-ui"

    echo ""
    echo "=================== Building UI ==================="

    # Incremental build: reuse an existing dist/ when it is already up to date
    # with the UI sources, instead of re-running the (slow) `tsc && vite build`
    # on every nvstreamer/ingress container build. Freshness = no source file
    # (excluding node_modules/ and dist/) is newer than the build marker
    # dist/index.html. Pass `no-cache` to force a full rebuild.
    local ui_marker="$ui_dir/dist/index.html"
    if [[ $NO_CACHE -ne 1 && -f "$ui_marker" ]]; then
        local newer_src
        newer_src=$(find "$ui_dir" -type f \
            -not -path "*/node_modules/*" \
            -not -path "*/dist/*" \
            -newer "$ui_marker" -print -quit 2>/dev/null)
        if [[ -z "$newer_src" ]]; then
            echo "vios-ui dist is up to date in $ui_dir; reusing existing build (pass no-cache to force a rebuild)."
            echo "================== Building UI done (reused) ==============="
            echo ""
            return 0
        fi
        echo "vios-ui sources changed (e.g. $newer_src); rebuilding ..."
    fi

    local toolchain_image_name
    toolchain_image_name=$(get_toolchain_image_name)

    echo "Building vios-ui in $ui_dir using $toolchain_image_name ..."
    if ! docker run --rm --network=host \
        --entrypoint /bin/bash \
        --user "$(id -u):$(id -g)" \
        -e HOME=/tmp \
        -e NPM_CONFIG_CACHE=/tmp/.npm \
        -e NPM_CONFIG_PREFIX=/tmp/.npm-global \
        -v "$build_root:/workspace" \
        -w /workspace/ui/vios-ui \
        "$toolchain_image_name" \
        -c 'export PATH="$NPM_CONFIG_PREFIX/bin:$PATH"; npm run install:link && npm run build'; then
        echo "[ERROR] vios-ui build failed in toolchain image: $toolchain_image_name"
        return 1
    fi

    if [[ ! -d "$ui_dir/dist" ]]; then
        echo "[ERROR] vios-ui dist directory not found after build"
        return 1
    fi

    echo "=================== Building UI done ==================="
    echo ""
}

# Function to build the vios-ui and stage its dist output into the webroot dir
# (used when building the nvstreamer container; webroot is packaged into the image).
build_vios_ui_webroot() {
    local build_root
    build_root=$(pwd)
    local ui_dir="$build_root/ui/vios-ui"
    local webroot_dir="$build_root/webroot"

    build_vios_ui "$build_root" || exit 1

    echo "Staging vios-ui dist into $webroot_dir ..."
    # Remove only the VST UI static files; leave other webroot files intact.
    rm -rf "$webroot_dir/assets" "$webroot_dir/favicon" "$webroot_dir/index.html"
    cp -rf "$ui_dir/dist/." "$webroot_dir/" || { echo "[ERROR] Failed to copy vios-ui dist to $webroot_dir"; exit 1; }
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

    record_built_arch

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

# Record that ARCH was just built, so a later arch-less `./build.sh clean`
# knows which architecture(s) to clean. Stored as a per-arch stamp file (not a
# shell env var, which would not survive between separate build/clean runs).
record_built_arch() {
    mkdir -p "prebuilts/$ARCH" 2>/dev/null
    : > "prebuilts/$ARCH/.vios-built"
}

# Remove arch-independent generated artifacts: UCF/helm build outputs, the
# staged vios-ui assets, and the compiled release tarball. Safe to call once
# per clean regardless of architecture.
clean_generated_artifacts() {
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

    # Generated UI build output. Remove dist/ too so `clean` forces a full UI
    # rebuild on the next container build; leaving it would let the incremental
    # freshness check (marker: dist/index.html) reuse the old build.
    # node_modules/ is intentionally kept so `npm install:link` stays fast.
    rm -rf ui/vios-ui/dist

    # Staged vios-ui assets (build_vios_ui_webroot). Never committed — only the
    # .gitkeep placeholders are tracked — so these are safe to drop and are
    # regenerated on the next container build. rm -rf is a no-op if absent.
    rm -rf webroot/assets webroot/favicon webroot/index.html
    rm -rf deployment/scaling/ingress/vst-ui/assets \
           deployment/scaling/ingress/vst-ui/favicon \
           deployment/scaling/ingress/vst-ui/index.html

    # Compiled release output (out/$ARCH/vst_release.tbz2, ...). The container
    # package step may write this as root, so a user-run rm can hit "Permission
    # denied". Degrade gracefully with a hint instead of aborting the clean.
    if [[ -e out ]]; then
        rm -rf out 2>/dev/null || \
            echo "[clean] WARN: could not remove root-owned out/ — run: sudo rm -rf out"
    fi
}

# Clean the compiled artifacts for a single architecture. x86_64 cleans
# natively; aarch64 uses the cross-compile toolchain container (make cc=1).
# If the aarch64 toolchain image is absent, fall back to removing the
# generated (git-untracked) libs on the host so clean still works without
# Docker — vendored (tracked) prebuilt libs are left untouched.
clean_arch() {
    local a="$1" cc=0
    [[ "$a" == "aarch64" ]] && cc=1
    echo ""
    echo "=================== Cleaning ($a) ==================="
    if [[ $cc -eq 1 ]] && ! image_exists "$AARCH64_CC_IMAGE"; then
        echo "[clean] aarch64 toolchain image '$AARCH64_CC_IMAGE' not present;"
        echo "        removing generated (untracked) aarch64 libs on host instead."
        if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
            git ls-files --others --exclude-standard "prebuilts/$a" \
                | xargs -r rm -f
        else
            echo "[clean] WARN: not a git work tree; cannot distinguish generated"
            echo "        from vendored libs. Build the toolchain then re-run:"
            echo "          ./build.sh toolchain arch=aarch64 && ./build.sh arch=arm64 clean"
        fi
    else
        make cc=$cc clean
    fi
    rm -f "prebuilts/$a/.vios-built"
}

# Top-level clean entrypoint. With arch=... given, cleans exactly that arch;
# otherwise cleans every architecture recorded by record_built_arch (both, if
# both were built), falling back to the default arch when nothing is recorded.
do_clean() {
    local arches=()
    if [[ $ARCH_EXPLICIT -eq 1 ]]; then
        arches=("$ARCH")
    else
        local a
        for a in x86_64 aarch64; do
            [[ -f "prebuilts/$a/.vios-built" ]] && arches+=("$a")
        done
        [[ ${#arches[@]} -eq 0 ]] && arches=("$ARCH")
    fi
    echo "Clean target arch(es): ${arches[*]}"

    local a
    for a in "${arches[@]}"; do
        clean_arch "$a"
    done

    clean_generated_artifacts
    echo "Cleaning done ..!"
}

# Function to build and package all modules
build_all() {
    local cc_value=$1
    local package=$2
    local container=$3

    if [[ $CLEAN -eq 1 ]]; then
        make cc=$cc_value clean
        clean_generated_artifacts
        echo "Cleaning done ..!"
        exit 0
    fi

    record_built_arch
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
            IMAGE_NAME=$NVSTREAMER_IMAGE_REGISTRY:$TAG
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

# ============================================================================
# Top-level subcommands that don't need module dispatch
# ============================================================================

# `./build.sh toolchain` — build the compile-toolchain image and exit.
# This wraps the verbose `docker build -t vios-build:… -f cicd_files/…/devel`
# command directly, so x86_64 and aarch64 share one entry point (no per-arch
# helper script).
#
# `push=1` is honored — together with X86_BUILD_IMAGE / AARCH64_CC_IMAGE
# overrides, it lets users publish the toolchain to their own registry.
if [[ $TOOLCHAIN -eq 1 ]]; then
    build_toolchain_image "$PUSH"
    exit 0
fi

# ---------------------------------------------------------------------------
# Multi-arch build + push + manifest
# ---------------------------------------------------------------------------
# Builds amd64 and arm64 container images with per-arch tags, pushes them, then
# assembles a single multi-arch manifest tag via `docker buildx imagetools`.
# The amd64 push runs in the background while the arm64 image is being built, so
# network transfer overlaps CPU work instead of running back-to-back.
#
#   tag=2.1.0-26.05.4  ->  per-arch tags 2.1.0-amd64-26.05.4 / 2.1.0-arm64-26.05.4
#                          manifest tag  2.1.0-26.05.4
#
#   ./build.sh multiarch tag=2.1.0-26.05.4 module=sensor,streamprocessing
#   ./build.sh multiarch tag=2.1.0-26.05.4 nvstreamer base-tag=2.1.0-runtime-26.05.4
#
# Requires IMAGE_REGISTRY to point at a real registry you can push to (e.g.
# export IMAGE_REGISTRY=nvcr.io/rxczgrvsg8nx/vst-dev), and `docker login`.
build_multiarch() {
    # The multi-arch manifest tag is the standard tag= (as everywhere else).
    if [[ -z "$TAG" ]]; then
        echo "[ERROR] multiarch requires tag=<manifest-tag>, e.g. tag=2.1.0-26.05.4"
        exit 1
    fi

    # `multiarch all` (or `arch=multiarch all`) → the multi-arch equivalent of
    # `./build.sh all`: build the sensor + streamprocessing modules and the
    # NVStreamer container for both architectures (release/optimized container
    # builds, the default). Ingress is intentionally NOT included — like plain
    # `all` — because vst-ingress is nginx + static UI and already builds as a
    # single multi-arch manifest on its own:
    #   ./build.sh container ingress push=1 tag=<tag>
    if [[ $BUILD_ALL -eq 1 ]]; then
        if [[ ${#MODULES[@]} -eq 0 ]]; then
            MODULES=("sensor" "streamprocessing")
            echo "[multiarch all] defaulting modules to: ${MODULES[*]}"
        fi
        NVSTREAMER=1
    fi

    if [[ ${#MODULES[@]} -eq 0 ]] && [[ $NVSTREAMER -eq 0 ]]; then
        echo "[ERROR] multiarch needs a target: module=<list>, nvstreamer, and/or 'all'"
        exit 1
    fi

    # Preflight: assembling the manifest needs the Docker Buildx plugin
    # (docker buildx imagetools). Fail early with install guidance.
    if ! docker buildx version >/dev/null 2>&1; then
        echo "[ERROR] 'docker buildx' is required for multiarch (docker buildx imagetools create) but is not available."
        echo "        Install the Buildx plugin, then re-run:"
        echo "          Ubuntu/Debian : sudo apt-get update && sudo apt-get install -y docker-buildx-plugin"
        echo "          Manual        : https://github.com/docker/buildx#installing"
        echo "          (Docker Desktop / recent Docker Engine already bundle it — updating Docker also fixes this.)"
        echo "        Verify with:    docker buildx version"
        exit 1
    fi

    # multiarch always pushes to a registry; the bare local default namespace
    # (e.g. IMAGE_REGISTRY=vios) has no registry host and cannot be pushed.
    local reg_host="${IMAGE_REGISTRY%%/*}"
    if [[ "$reg_host" != *.* ]] && [[ "$reg_host" != *:* ]] && [[ "$reg_host" != "localhost" ]]; then
        echo "[WARN] IMAGE_REGISTRY='$IMAGE_REGISTRY' has no registry host — the push/manifest will"
        echo "       target Docker Hub or fail. Set a pushable registry and log in first, e.g.:"
        echo "         export IMAGE_REGISTRY=nvcr.io/rxczgrvsg8nx/vst-dev"
        echo "         docker login nvcr.io"
    fi

    # 2.1.0-26.05.4 -> prefix=2.1.0 suffix=26.05.4 -> 2.1.0-<arch>-26.05.4.
    local amd_tag arm_tag
    if [[ "$TAG" == *-* ]]; then
        amd_tag="${TAG%%-*}-amd64-${TAG#*-}"
        arm_tag="${TAG%%-*}-arm64-${TAG#*-}"
    else
        amd_tag="${TAG}-amd64"
        arm_tag="${TAG}-arm64"
    fi

    # Fully-qualified target repos to assemble manifests for.
    local -a repos=()
    local m
    for m in "${MODULES[@]}"; do repos+=("$IMAGE_REGISTRY/vst-${m}"); done
    [[ $NVSTREAMER -eq 1 ]] && repos+=("$NVSTREAMER_IMAGE")

    # Flags forwarded to each per-arch sub-build.
    local extra=""
    [[ -n "$BASE_TAG" ]] && extra="$extra base-tag=$BASE_TAG"
    [[ $NO_CACHE -eq 1 ]] && extra="$extra no-cache"
    local mod_csv
    mod_csv=$(IFS=, ; echo "${MODULES[*]}")

    echo "=============================================="
    echo "multiarch: $TAG"
    echo "  amd64 tag : $amd_tag"
    echo "  arm64 tag : $arm_tag"
    echo "  manifest  : $TAG"
    echo "  targets   : ${repos[*]}"
    echo "=============================================="

    # Build one architecture locally (no push). $1: arch flag ("" or "arch=arm64"), $2: tag.
    # modules and nvstreamer are built in separate invocations (build.sh builds
    # nvstreamer only when no module= is given).
    _ma_build() {
        local archflag="$1" tag="$2"
        # Compile objects live in the source tree, so switching architecture
        # (amd64 <-> arm64) without cleaning would relink stale wrong-arch objects
        # ("Relocations in generic ELF (EM: 183)" / "file in wrong format"). Clean
        # per-arch before building.
        "$0" $archflag clean >/dev/null 2>&1 || true
        if [[ ${#MODULES[@]} -gt 0 ]]; then
            # shellcheck disable=SC2086
            "$0" $archflag container tag="$tag" module="$mod_csv" $extra || return 1
        fi
        if [[ $NVSTREAMER -eq 1 ]]; then
            # NVStreamer is a distinct app; if modules were just built in this
            # arch pass, clean first so NVStreamer recompiles the shared framework
            # objects with its own CPPFLAGS instead of relinking the module-flavored
            # ones (that contamination broke the arm64 NVStreamer /sensor/list).
            if [[ ${#MODULES[@]} -gt 0 ]]; then
                "$0" $archflag clean >/dev/null 2>&1 || true
            fi
            # shellcheck disable=SC2086
            "$0" $archflag nvstreamer container tag="$tag" $extra || return 1
        fi
    }
    _ma_push() {
        local tag="$1" r
        for r in "${repos[@]}"; do docker push "$r:$tag" || return 1; done
    }

    echo "[multiarch] building amd64 ..."
    _ma_build "" "$amd_tag" || { echo "[ERROR] amd64 build failed"; exit 1; }

    echo "[multiarch] pushing amd64 (background) while building arm64 ..."
    _ma_push "$amd_tag" & local amd_push=$!

    _ma_build "arch=arm64" "$arm_tag" || {
        echo "[ERROR] arm64 build failed"; kill "$amd_push" 2>/dev/null; wait "$amd_push" 2>/dev/null; exit 1; }

    wait "$amd_push" || { echo "[ERROR] amd64 push failed"; exit 1; }
    echo "[multiarch] pushing arm64 ..."
    _ma_push "$arm_tag" || { echo "[ERROR] arm64 push failed"; exit 1; }

    echo "[multiarch] assembling manifests ..."
    local r
    for r in "${repos[@]}"; do
        docker buildx imagetools create \
            --tag "$r:$TAG" \
            "$r:$arm_tag" \
            "$r:$amd_tag" || { echo "[ERROR] imagetools create failed: $r"; exit 1; }
    done

    # Summary
    echo ""
    echo "================ multiarch complete ================"
    echo "  registry : $IMAGE_REGISTRY"
    echo "  pushed   : per-arch images (amd64 + arm64)"
    for r in "${repos[@]}"; do
        echo "     - $r:$amd_tag"
        echo "     - $r:$arm_tag"
    done
    echo "  manifest : multi-arch tag (amd64 + arm64)"
    for r in "${repos[@]}"; do
        echo "     - $r:$TAG"
    done
    echo "===================================================="
}

if [[ $MULTIARCH -eq 1 ]]; then
    build_multiarch
    exit 0
fi

# `./build.sh all` — from a fresh clone, build everything in one command:
# toolchain → base → module containers (sensor + streamprocessing by default,
# or whatever module=… listed) → nvstreamer container.
# Auto-deps are mandatory here (the whole point of `all`), so we ignore
# NO_AUTO_DEPS for this path.
if [[ $BUILD_ALL -eq 1 ]]; then
    echo "=============================================="
    echo "./build.sh all — full pipeline"
    echo "=============================================="

    # Default the module list if user didn't pass one. Sensor + streamprocessing
    # is the smallest set that produces a functional deploy.
    if [[ ${#MODULES[@]} -eq 0 ]]; then
        MODULES=("sensor" "streamprocessing")
        echo "[all] No module=… given, defaulting to: ${MODULES[*]}"
    fi

    ensure_toolchain_image
    ensure_base_image

    # Compile + containerize the requested modules.
    CONTAINER=1
    if [[ "$ARCH" == "aarch64" ]] || [[ "$ARCH" == "arm64" ]]; then
        build_all 1 1 1
    else
        build_all 0 1 1
    fi

    # Build the NVStreamer container too — `--target all` deploy needs it.
    echo ""
    echo "[all] Building NVStreamer container..."
    build_vios_ui_webroot
    NVSTREAMER=1
    if [[ "$ARCH" == "aarch64" ]] || [[ "$ARCH" == "arm64" ]]; then
        build_all 1 0 0
    else
        build_all 0 0 0
    fi

    echo ""
    echo "=============================================="
    echo "./build.sh all — complete"
    echo "=============================================="
    echo ""
    echo "Images built (locally tagged, not pushed):"
    echo "  toolchain : $(get_toolchain_image_name)"
    _base_tag="${BASE_TAG:-${DEFAULT_TAGS["vst-base"]:-latest}}"
    echo "  base      : $IMAGE_REGISTRY/vst-base:$_base_tag"
    for _m in "${MODULES[@]}"; do
        _mt="${DEFAULT_TAGS[$_m]:-latest}"
        echo "  module    : $IMAGE_REGISTRY/vst-${_m}:${TAG:-$_mt}"
    done
    echo "  nvstreamer: $NVSTREAMER_IMAGE:${DEFAULT_TAGS[nvstreamer]:-latest}"
    echo ""
    echo "To publish to your registry, set the registry env vars BEFORE running"
    echo "the build (the tag is baked into the image at build time — see the"
    echo "README's 'Pushing built images to a registry' section). Then push:"
    echo ""
    # Join MODULES with commas for the module= example. The array is
    # space-separated by default which would be the wrong arg shape.
    _mod_csv=$(IFS=, ; echo "${MODULES[*]}")
    echo "  ./build.sh toolchain push=1"
    echo "  ./build.sh base-container push=1"
    echo "  ./build.sh container module=$_mod_csv push=1"
    echo ""
    exit 0
fi

# ============================================================================
# Auto-detect missing prerequisites for everything else
# ============================================================================
# Every other path (TESTS, PACKAGE, CONTAINER, default compile, base-container
# clean) eventually runs `make` which expects the toolchain image to exist —
# the make wrapper does `docker run -v $(TOP):/root $TOOLCHAIN_IMG ...`.
# Auto-detect handles fresh clones; idempotent on warm hosts (cheap docker
# image inspect). Skipped for `help` and `clean`-only paths where compilation
# isn't actually needed.
# Clean is a delete-only operation — handle it up front so it works regardless
# of module flags, cleans whatever arch(es) were actually built (auto-detected
# from the record_built_arch stamps), and never needs the toolchain just to rm.
if [[ $CLEAN -eq 1 ]]; then
    do_clean
    exit 0
fi

if [[ $CLEAN -eq 0 ]]; then
    ensure_toolchain_image
fi
# Base image is only needed when actually building module containers.
# build_base_image / clean / package / default-compile do not need it.
if [[ $CONTAINER -eq 1 ]] && [[ $BASE_IMAGE -eq 0 ]] \
   && [[ $INGRESS -eq 0 ]] && [[ $NVSTREAMER_INGRESS -eq 0 ]] \
   && [[ $MCP -eq 0 ]]; then
    ensure_base_image
fi

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

            build_vios_ui "$INGRESS_BUILD_ROOT" || exit 1

            echo "Staging vios-ui dist into $VST_UI_DIR ..."
            find "$VST_UI_DIR" -mindepth 1 -not -name '.gitkeep' -delete
            cp -rf "$UI_DIR/dist/." "$VST_UI_DIR/" || { echo "[ERROR] Failed to copy vios-ui dist to $VST_UI_DIR"; exit 1; }

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
        # Background-push bookkeeping: overlap each image's upload (network I/O)
        # with the next module's compile + docker build (CPU). All pushes are
        # waited on after the loop, and any failure fails the script.
        declare -a PUSH_PIDS=()
        declare -a PUSH_IMAGES=()
        declare -a PUSH_LOGS=()
        # If a later module's build fails (exit 1) while an earlier module's push is
        # still running in the background, that push would keep going and leave a
        # partial image set in the registry. Trap EXIT to kill any outstanding
        # pushes and remove their temp logs. On the happy path the pushes are already
        # waited on below (and logs removed), so the trap is a no-op there.
        _cleanup_bg_pushes() {
            local _pid _lf
            for _pid in "${PUSH_PIDS[@]}"; do kill "$_pid" 2>/dev/null || true; done
            for _lf in "${PUSH_LOGS[@]}"; do rm -f "$_lf" 2>/dev/null || true; done
        }
        trap _cleanup_bg_pushes EXIT
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
                # Push in the background so the next module's compile + docker build
                # overlaps this image's upload. Output is captured per-image and
                # printed when the push is waited on after the loop.
                push_log=$(mktemp)
                echo "Pushing Docker image (background): $imagename"
                docker push "$imagename" > "$push_log" 2>&1 &
                PUSH_PIDS+=("$!")
                PUSH_IMAGES+=("$imagename")
                PUSH_LOGS+=("$push_log")
            fi

            MODULE_COUNT=$((MODULE_COUNT + 1))

            # Change back to the previous directory
            cd - || exit 1
        done

        # Wait for all background pushes; print their output and fail on any error.
        if [[ ${#PUSH_PIDS[@]} -gt 0 ]]; then
            push_failed=0
            for pi in "${!PUSH_PIDS[@]}"; do
                if wait "${PUSH_PIDS[$pi]}"; then
                    echo -e "Docker push succeeded for image: ${PUSH_IMAGES[$pi]}"
                else
                    echo -e "[ERROR] Docker push failed for image: ${PUSH_IMAGES[$pi]}"
                    push_failed=1
                fi
                cat "${PUSH_LOGS[$pi]}" 2>/dev/null || true
                rm -f "${PUSH_LOGS[$pi]}"
            done
            if [[ $push_failed -ne 0 ]]; then
                echo -e "[ERROR] One or more Docker pushes failed."
                exit 1
            fi
        fi
        # All pushes completed and waited on -- drop the safety trap.
        trap - EXIT

        print_container_build_summary_footer "$OVERALL_START_TIME" "$MODULE_COUNT"
    fi
fi
