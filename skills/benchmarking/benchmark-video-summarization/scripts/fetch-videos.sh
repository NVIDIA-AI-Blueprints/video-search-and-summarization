#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# fetch-videos.sh — download the NGC VSS warehouse sample videos for the LVS
# benchmark and place a curated set into the media-server videos directory.
#
# Usage:
#   ./scripts/fetch-videos.sh [version]        # version defaults to 3.2.0
#   FORCE=1 ./scripts/fetch-videos.sh          # re-fetch even if videos already exist
#
# Environment:
#   VSS_BENCHMARK_DATA_DIR  root data dir (default: ~/vss-benchmark-data).
#                           Videos are placed in <data dir>/videos, which is
#                           what media-server.yaml serves on :8888/videos/.
#   NGC_CLI_API_KEY / NGC_API_KEY   used to authenticate the ngc CLI.
#
# This script does NOT install the ngc CLI (a versioned, EULA-gated binary) — it
# checks for it and prints install instructions if missing.

set -euo pipefail

VERSION="${1:-3.2.0}"
RESOURCE="nvidia/vss-warehouse/vss-warehouse-app-data"
DATA_DIR="${VSS_BENCHMARK_DATA_DIR:-${HOME}/vss-benchmark-data}"
VIDEOS_DIR="${DATA_DIR}/videos"
STAGING="${DATA_DIR}/ngc-warehouse"

# Curated mapping: friendly served-name  ->  path inside the package (under
# vss-warehouse-app-data/videos/). One representative single-camera clip per
# duration; enough for single_file (latency) and file_burst (throughput).
NAMES=( "warehouse_4min.mp4"  "warehouse_5min.mp4"  "warehouse_10min.mp4" )
SRCS=(  "warehouse-loading-dock-3cams-synthetic/Camera_01.mp4" \
        "warehouse-4cams-20mx20m-synthetic/Camera_01.mp4" \
        "nv-warehouse-4cams/Camera_01.mp4" )

# --- 1. ngc CLI present? ----------------------------------------------------
if ! command -v ngc >/dev/null 2>&1; then
    echo "ERROR: 'ngc' CLI not found on PATH." >&2
    echo "  Install it (Linux x86_64/arm64) from:" >&2
    echo "    https://org.ngc.nvidia.com/setup/installers/cli" >&2
    echo "  Then authenticate:  export NGC_CLI_API_KEY=<your-key>   (or run: ngc config set)" >&2
    exit 1
fi

# --- 2. authenticated? ------------------------------------------------------
export NGC_CLI_API_KEY="${NGC_CLI_API_KEY:-${NGC_API_KEY:-}}"
if [[ -z "${NGC_CLI_API_KEY}" ]] && ! ngc config current >/dev/null 2>&1; then
    echo "ERROR: ngc CLI is not authenticated." >&2
    echo "  Set NGC_CLI_API_KEY (or NGC_API_KEY), or run: ngc config set" >&2
    exit 1
fi

mkdir -p "${VIDEOS_DIR}" "${STAGING}"

# --- 3. idempotency ---------------------------------------------------------
all_present=true
for n in "${NAMES[@]}"; do
    [[ -f "${VIDEOS_DIR}/${n}" ]] || all_present=false
done
if [[ "${all_present}" == true && "${FORCE:-}" != "1" ]]; then
    echo "All warehouse videos already present in ${VIDEOS_DIR} (set FORCE=1 to re-fetch)."
else
    # --- 4. download --------------------------------------------------------
    echo "Downloading ${RESOURCE}:${VERSION} (~2.2 GB) to ${STAGING} ..."
    ngc registry resource download-version "${RESOURCE}:${VERSION}" --dest "${STAGING}" >/dev/null
    DL_DIR="${STAGING}/vss-warehouse-app-data_v${VERSION}"
    TARBALL="$(find "${DL_DIR}" -maxdepth 1 -name '*.tar.gz' 2>/dev/null | head -1)"
    [[ -n "${TARBALL}" ]] || { echo "ERROR: package tarball not found under ${DL_DIR}" >&2; exit 1; }

    # --- 5. extract curated members + copy with friendly names --------------
    i=0
    for n in "${NAMES[@]}"; do
        src="vss-warehouse-app-data/videos/${SRCS[$i]}"
        if tar -tzf "${TARBALL}" "${src}" >/dev/null 2>&1; then
            tar -xzf "${TARBALL}" -C "${STAGING}" "${src}"
            cp -f "${STAGING}/${src}" "${VIDEOS_DIR}/${n}"
            echo "  + ${n}  (<- ${SRCS[$i]})"
        else
            echo "  ! skip ${n}: '${src}' not in package (layout may differ in v${VERSION})"
        fi
        i=$((i + 1))
    done
fi

# --- 6. report + ready-to-paste URLs ----------------------------------------
HOST_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo ""
echo "Videos available in ${VIDEOS_DIR}:"
ls -1 "${VIDEOS_DIR}"/*.mp4 2>/dev/null | sed 's#^#  #' || echo "  (none)"
echo ""
echo "Start the media server (from the skill dir):"
echo "  VSS_BENCHMARK_DATA_DIR=${DATA_DIR} docker compose -f scripts/media-server.yaml up -d"
echo ""
echo "Use these URLs in config.yaml (HOST IP, not localhost — LVS downloads the"
echo "video from inside its container, so localhost would point at the container):"
for n in "${NAMES[@]}"; do
    [[ -f "${VIDEOS_DIR}/${n}" ]] && echo "  http://${HOST_IP:-<HOST_IP>}:8888/videos/${n}"
done
