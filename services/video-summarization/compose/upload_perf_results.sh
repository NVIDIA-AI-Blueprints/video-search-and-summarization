#!/usr/bin/env bash
######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.
######################################################################################################
#
# upload_perf_results.sh — Push existing perf result JSON(s) to the MinIO-backed
# perf dashboard, optionally stamping metadata.run_info.triggered_by.
#
# Companion to run_benchmark.sh: generate the JSON with run_benchmark.sh -O,
# inspect it locally, then publish it with this script when satisfied.
#
# Prerequisites:
#   - perf/benchmark/vss-perf-env venv exists (run any benchmark once to bootstrap).
#
# MinIO endpoint / bucket / credentials are baked into vss_perf_common.py
# (bucket perf-results) — no env vars needed.
#
# Run with -h for usage.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BENCHMARK_DIR="$SCRIPT_DIR/../perf/benchmark"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${GREEN}[✓]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
error() { echo -e "${RED}[✗]${NC} $*"; }
step()  { echo -e "${CYAN}[➜]${NC} $*"; }

usage() {
    cat <<EOF
Usage: $(basename "$0") <path> [options]

Upload perf result JSON file(s) to MinIO. <path> is a single lvs_*.json
file or a directory containing lvs_*.json files.

Options:
  -T TRIGGER     Stamp metadata.run_info.triggered_by before upload.
                 One of: ci_pipeline | manual | scheduled | sqa | perf-lab.
                 Default: keep whatever is in the file.
  -s SERVICE     Service name passed to the uploader (default: LVS).
  -h             Show this help.

Examples:
  # Upload everything in the report dir, stamped as 'sqa'
  $(basename "$0") ../perf/benchmark/vss-perf-report/ -T sqa

  # Upload a single file as 'perf-lab'
  $(basename "$0") ../perf/benchmark/vss-perf-report/lvs_8xH100-9k_20260318_104530.json -T perf-lab
EOF
    exit 0
}

# Positional path is required before optional flags
if [ "$#" -lt 1 ]; then
    error "Path argument is required"
    usage
fi
if [ "$1" = "-h" ] || [ "$1" = "--help" ]; then
    usage
fi

UPLOAD_PATH="$1"
shift

TRIGGERED_BY=""
SERVICE="LVS"

while getopts "T:s:h" opt; do
    case $opt in
        T) TRIGGERED_BY="$OPTARG" ;;
        s) SERVICE="$OPTARG" ;;
        h) usage ;;
        *) usage ;;
    esac
done

# Resolve to list of JSON files
UPLOAD_FILES=()
if [ -d "$UPLOAD_PATH" ]; then
    while IFS= read -r -d '' f; do UPLOAD_FILES+=("$f"); done \
        < <(find "$UPLOAD_PATH" -maxdepth 1 -type f -name 'lvs_*.json' -print0)
    if [ ${#UPLOAD_FILES[@]} -eq 0 ]; then
        error "No lvs_*.json files found in $UPLOAD_PATH"
        exit 1
    fi
elif [ -f "$UPLOAD_PATH" ]; then
    UPLOAD_FILES=("$UPLOAD_PATH")
else
    error "Upload path not found: $UPLOAD_PATH"
    exit 1
fi

cd "$BENCHMARK_DIR"
if [ ! -f vss-perf-env/bin/activate ]; then
    error "Perf venv not found at $BENCHMARK_DIR/vss-perf-env"
    echo "  Bootstrap it by running any benchmark first, e.g."
    echo "  ./run_benchmark.sh -f <compose-file> -s quick_test -d"
    exit 1
fi
set +u
source vss-perf-env/bin/activate
set -u

step "Uploading ${#UPLOAD_FILES[@]} file(s) to MinIO (service=$SERVICE)"
[ -n "$TRIGGERED_BY" ] && step "Stamping triggered_by=$TRIGGERED_BY"

UPLOAD_CMD=(python vss_perf_common.py "${UPLOAD_FILES[@]}" --service "$SERVICE")
[ -n "$TRIGGERED_BY" ] && UPLOAD_CMD+=(--triggered-by "$TRIGGERED_BY")

"${UPLOAD_CMD[@]}"
upload_exit=$?

if [ $upload_exit -eq 0 ]; then
    info "Upload complete"
else
    error "Upload failed (exit code: $upload_exit)"
fi
exit $upload_exit
