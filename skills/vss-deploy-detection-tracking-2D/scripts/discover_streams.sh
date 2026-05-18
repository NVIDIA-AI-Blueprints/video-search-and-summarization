#!/usr/bin/env bash

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

# discover_streams.sh - Deterministic stream enumeration for rtvicv-deploy.
#
# Scans $RESOURCES for any directory that contains .mp4/.mkv files (NO
# hardcoded NGC subdirectory names like 'nv-warehouse-4cams' or 'smc-app').
# Emits RESOLVE_OK / RESOLVE_AMBIGUOUS on stderr so the calling skill can
# drive an AskQuestion when multiple video directories exist. Once a
# directory is chosen, lists .mp4 files in stable (sorted) order and cycles
# them to produce exactly N (id, url) pairs (cycled entries get a `_<i>`
# suffix to avoid REST-add duplicate-id errors).
#
# Cycling rule (matches automation repo)
# --------------------------------------
#   orig_count = number of videos found
#   for i in 1..BATCH_SIZE:
#       idx = (i - 1) % orig_count
#       if i <= orig_count:   id = <stem>
#       else:                 id = <stem>_<i>         # unique-suffix to avoid collision
#       url = file://<dir>/<stem>.mp4
#
# This lets a batch-size-8 deploy with 4 videos produce 8 unique stream ids
# without the REST API rejecting duplicates.
#
# Usage
# -----
#   discover_streams.sh <usecase> <batch_size>
#       [--videos-dir <dir>]        # skip the scan, use this dir verbatim
#       [--format env|json|lines]   # output format (default: env)
#       [--warn-cycle]              # print a WARN line when cycling kicks in
#                                   # (purely informational; cycling is always
#                                   # allowed, including for warehouse-3d)
#
# Output formats
# --------------
#   env   (default)   Bash-evalable KEY='v1;v2;...' — source directly:
#                       eval "$(discover_streams.sh warehouse-2d 4)"
#                       # -> STREAM_IDS, STREAM_URLS, STREAM_COUNT, STREAM_DIR
#   lines             One "id<TAB>url" per line (scriptable via read)
#   json              JSON array of {"id":..., "url":...} objects
#
# Video directory discovery is layout-agnostic — the script looks at the
# actual contents of $RESOURCES, not at any expected directory name. If the
# user pulled an NGC resource with a renamed / restructured video folder, it
# still works. The <usecase> argument is used only to provide hints (e.g. the
# warehouse-3d calibration hint); it does NOT constrain the video-dir pick.
#
# Exit codes:  0 success,  1 usage error,  2 no videos found,  3 multiple video dirs (caller must re-invoke with --videos-dir)

set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

USECASE="${1:-}"
BATCH="${2:-}"
[[ -n "$USECASE" && -n "$BATCH" ]] || { sed -n '18,40p' "$0"; exit 1; }
shift 2
is_valid_usecase "$USECASE" || die "Invalid use case: $USECASE (valid: ${USECASES[*]})"
[[ "$BATCH" =~ ^[1-9][0-9]*$ ]] || die "batch_size must be a positive integer (got: $BATCH)"

VIDEOS_DIR=""
FORMAT="env"
WARN_CYCLE=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --videos-dir) VIDEOS_DIR="$2"; shift 2 ;;
        --format)     FORMAT="$2";     shift 2 ;;
        --warn-cycle) WARN_CYCLE=1;    shift   ;;
        -h|--help)    sed -n '18,40p' "$0"; exit 0 ;;
        *)            die "Unknown argument: $1" ;;
    esac
done
case "$FORMAT" in env|json|lines) ;; *) die "Invalid --format: $FORMAT (env|json|lines)" ;; esac

# ── Discover videos dir if not provided ─────────────────────────
# Layout-agnostic: enumerate ALL directories under $RESOURCES that contain
# at least one .mp4 or .mkv file. No hardcoded leaf names — this survives
# NGC resources being renamed or restructured between versions.
#
# Dispatch on the candidate count:
#   0  → die (exit 2) — no videos at all
#   1  → use it (RESOLVE_OK)
#   >1 → emit RESOLVE_AMBIGUOUS with a numbered list on stderr and exit 3.
#        The caller (rtvicv-deploy skill) must drive an AskQuestion, then
#        re-invoke with --videos-dir <chosen>.
if [[ -z "$VIDEOS_DIR" ]]; then
    require_dir "$RESOURCES"
    mapfile -t VIDEO_CANDS < <(
        find "$RESOURCES" -type d -print0 2>/dev/null | \
        while IFS= read -r -d '' d; do
            if compgen -G "$d/*.mp4" > /dev/null 2>&1 || \
               compgen -G "$d/*.mkv" > /dev/null 2>&1; then
                echo "$d"
            fi
        done | sort
    )
    case ${#VIDEO_CANDS[@]} in
        0)
            die "No directories containing .mp4/.mkv files under $RESOURCES — did you mount the NGC resources? (Tried a layout-agnostic scan; specify --videos-dir <path> if your videos live elsewhere.)"
            ;;
        1)
            VIDEOS_DIR="${VIDEO_CANDS[0]}"
            ;;
        *)
            {
                echo "RESOLVE_AMBIGUOUS: videos_dir count=${#VIDEO_CANDS[@]}"
                for i in "${!VIDEO_CANDS[@]}"; do
                    n=$(compgen -G "${VIDEO_CANDS[$i]}/*.mp4" 2>/dev/null | wc -l)
                    m=$(compgen -G "${VIDEO_CANDS[$i]}/*.mkv" 2>/dev/null | wc -l)
                    printf '  [%d] %s  (%d .mp4 / %d .mkv)\n' "$i" "${VIDEO_CANDS[$i]}" "$n" "$m"
                done
                echo "Re-invoke with --videos-dir <absolute-path> after user confirms."
            } >&2
            exit 3
            ;;
    esac
fi

require_dir "$VIDEOS_DIR"
echo "RESOLVE_OK: videos-dir=$VIDEOS_DIR" >&2

# ── Enumerate .mp4 files in stable sorted order ─────────────────
shopt -s nullglob
mapfile -t MP4S < <(printf '%s\n' "$VIDEOS_DIR"/*.mp4 | sort)
shopt -u nullglob
orig_count=${#MP4S[@]}
(( orig_count > 0 )) || { echo "ERROR: no .mp4 files under $VIDEOS_DIR" >&2; exit 2; }

# ── Build id/url arrays of length BATCH (with cycle-suffix) ─────
IDS=()
URLS=()
for (( i=1; i<=BATCH; i++ )); do
    idx=$(( (i - 1) % orig_count ))
    path="${MP4S[$idx]}"
    stem=$(basename "$path" .mp4)
    if (( i <= orig_count )); then
        id="$stem"
    else
        id="${stem}_${i}"
    fi
    IDS+=( "$id" )
    URLS+=( "file://$path" )
done

# ── Warn if cycling occurred ────────────────────────────────────
# Cycling is permitted for every use case (including warehouse-3d, where
# the agent has already confirmed the user's intent via Step 2's
# "Warehouse-3d batch > calibrated cameras" AskQuestion before reaching
# this script).
if (( BATCH > orig_count )) && (( WARN_CYCLE == 1 )); then
    echo "WARN: BATCH=$BATCH > videos=$orig_count — cycled ids get '_<i>' suffix starting at stream $((orig_count+1))" >&2
fi

# ── Emit in requested format ────────────────────────────────────
case "$FORMAT" in
    env)
        # Semicolon-separated, bash-evalable. Consumers do:
        #   eval "$(discover_streams.sh warehouse-2d 4)"
        # All four lines use %q so filenames with quotes, spaces, or
        # shell metacharacters can't break out of the consumer's eval.
        printf 'STREAM_DIR=%q\n' "$VIDEOS_DIR"
        printf 'STREAM_COUNT=%d\n' "$BATCH"
        printf 'STREAM_IDS=%q\n'  "$(IFS=';'; echo "${IDS[*]}")"
        printf 'STREAM_URLS=%q\n' "$(IFS=';'; echo "${URLS[*]}")"
        ;;
    lines)
        for (( i=0; i<BATCH; i++ )); do
            printf '%s\t%s\n' "${IDS[$i]}" "${URLS[$i]}"
        done
        ;;
    json)
        printf '['
        for (( i=0; i<BATCH; i++ )); do
            (( i > 0 )) && printf ','
            printf '{"id":"%s","url":"%s"}' "${IDS[$i]}" "${URLS[$i]}"
        done
        printf ']\n'
        ;;
esac
