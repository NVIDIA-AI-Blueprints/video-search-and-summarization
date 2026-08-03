#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Ensure the NvDCF_accuracy ReID model (resnet50_market1501.etlt) is present in
# every DeepStream Tracker/ directory the runtime may resolve, and symlink a
# cached TRT engine from ENGINE_CACHE_DIR when available.
#
# Used by ds-start.sh for rtdetr-gdino profiles (alerts, smartcities). Derived
# from skills/vss-deploy-detection-tracking-2d/scripts/setup_tracker_reid.sh with
# multi-path etlt install and explicit permissions for the privilege-dropped app
# user (STORAGE_UID/STORAGE_GID).

set -euo pipefail

REID_BASENAME="resnet50_market1501.etlt"
DEST_DIR="/opt/nvidia/deepstream/deepstream/samples/models/Tracker"
DEST="$DEST_DIR/$REID_BASENAME"

USE_SYMLINK=0
SRC_OVERRIDE=""
WAIT_SEC=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --symlink)  USE_SYMLINK=1; shift ;;
        --src)      SRC_OVERRIDE="$2"; shift 2 ;;
        --wait)     WAIT_SEC="$2"; shift 2 ;;
        -h|--help)
            sed -n '1,25p' "$0"
            exit 0
            ;;
        *)          echo "Unknown arg: $1" >&2; exit 1 ;;
    esac
done

[[ "$WAIT_SEC" =~ ^[0-9]+$ ]] || { echo "ERROR: --wait must be a non-negative integer" >&2; exit 1; }

STORAGE_UID="${STORAGE_UID:-1001}"
STORAGE_GID="${STORAGE_GID:-1001}"
ENGINE_CACHE_DIR="${ENGINE_CACHE_DIR:-/opt/engines}"
mkdir -p "$ENGINE_CACHE_DIR" 2>/dev/null || true

collect_tracker_dirs() {
    TRACKER_DIRS=("$DEST_DIR")
    shopt -s nullglob
    for d in /opt/nvidia/deepstream/deepstream-[0-9]*/samples/models/Tracker; do
        [[ -d "$d" ]] || continue
        if [[ "$(readlink -f "$d" 2>/dev/null)" != "$(readlink -f "$DEST_DIR" 2>/dev/null)" ]]; then
            TRACKER_DIRS+=("$d")
        fi
    done
    shopt -u nullglob
}

resolve_etlt_source() {
    if [[ -n "$SRC_OVERRIDE" ]]; then
        echo "$SRC_OVERRIDE"
        return
    fi
    find /opt/nvidia/deepstream \
        -maxdepth 10 -type f -name "$REID_BASENAME" \
        -not -path "$DEST_DIR/*" \
        2>/dev/null | head -n 1
}

install_etlt_to_tracker_dirs() {
    local src="$1"
    local tdir dest src_real dest_real

    collect_tracker_dirs
    src_real="$(readlink -f "$src" 2>/dev/null || true)"
    for tdir in "${TRACKER_DIRS[@]}"; do
        dest="${tdir}/${REID_BASENAME}"
        install -d -m 0755 "$tdir"
        if (( USE_SYMLINK )); then
            ln -sfn -T "$src" "$dest"
            echo "TRACKER_REID: LINKED  $dest  ->  $src"
        else
            # A symlink here came from a previous --symlink run; replace it with
            # a real copy rather than writing through to the link target.
            if [[ -L "$dest" ]]; then
                rm -f "$dest"
            fi
            dest_real="$(readlink -f "$dest" 2>/dev/null || true)"
            if [[ -f "$dest" && -n "$src_real" && "$src_real" == "$dest_real" ]]; then
                # Source resolved to the already-staged file (a rerun without
                # --src can find it); install refuses to copy a file onto
                # itself, so only normalise the mode.
                chmod 0644 "$dest"
                echo "TRACKER_REID: IN_PLACE  $dest"
            else
                # Unconditional: normalises the mode and repairs a stale or
                # truncated etlt that an existence-only check would keep forever.
                install -m 0644 "$src" "$dest"
                echo "TRACKER_REID: INSTALLED  $src  ->  $dest"
            fi
        fi
        # Unconditional: the tracker writes its built ReID engine here as the
        # privilege-dropped app user, including when the etlt was already staged.
        chown "${STORAGE_UID}:${STORAGE_GID}" "$tdir"
    done
}

SRC="$(resolve_etlt_source)"
if [[ -z "$SRC" || ! -f "$SRC" ]]; then
    echo "TRACKER_REID: MISSING  could not locate $REID_BASENAME" >&2
    echo "  Pass --src <path> with an explicit source if the model lives elsewhere." >&2
    exit 2
fi

install_etlt_to_tracker_dirs "$SRC"

collect_tracker_dirs

discover_engine_candidates() {
    shopt -s nullglob
    ENGINE_CANDIDATES=()
    for tdir in "${TRACKER_DIRS[@]}"; do
        for f in "$tdir/${REID_BASENAME}"_b*_gpu*_fp*.engine; do
            [[ -e "$f" ]] && ENGINE_CANDIDATES+=("$f")
        done
    done
    if (( ${#ENGINE_CANDIDATES[@]} > 1 )); then
        mapfile -t ENGINE_CANDIDATES < <(printf '%s\n' "${ENGINE_CANDIDATES[@]}" | xargs -d '\n' ls -1t 2>/dev/null)
    fi
    shopt -u nullglob
}

discover_engine_candidates

if (( WAIT_SEC > 0 && ${#ENGINE_CANDIDATES[@]} == 0 )); then
    shopt -s nullglob
    CACHE_EXISTING=( "$ENGINE_CACHE_DIR/${REID_BASENAME}"_b*_gpu*_fp*.engine )
    shopt -u nullglob
    if (( ${#CACHE_EXISTING[@]} == 0 )); then
        echo "TRACKER_ENGINE: WAITING  poll up to ${WAIT_SEC}s for tracker to build the ReID engine..."
        WAITED=0
        while (( WAITED < WAIT_SEC )); do
            sleep 10
            WAITED=$((WAITED + 10))
            discover_engine_candidates
            if (( ${#ENGINE_CANDIDATES[@]} > 0 )); then
                echo "TRACKER_ENGINE: APPEARED  after ${WAITED}s wait — caching now"
                break
            fi
            echo "TRACKER_ENGINE: STILL_BUILDING  ${WAITED}s elapsed (typical build: 90-120s)"
        done
    fi
fi

if (( ${#ENGINE_CANDIDATES[@]} > 0 )); then
    declare -A SEEN_BASENAMES=()
    for ENGINE_AT_DEST in "${ENGINE_CANDIDATES[@]}"; do
        ENGINE_BASENAME=$(basename "$ENGINE_AT_DEST")
        [[ -n "${SEEN_BASENAMES[$ENGINE_BASENAME]:-}" ]] && continue
        SEEN_BASENAMES[$ENGINE_BASENAME]=1
        ENGINE_IN_CACHE="$ENGINE_CACHE_DIR/$ENGINE_BASENAME"

        if [[ -L "$ENGINE_AT_DEST" ]]; then
            RESOLVED=$(readlink -f "$ENGINE_AT_DEST" 2>/dev/null || true)
            if [[ "$RESOLVED" != "$ENGINE_IN_CACHE" && -f "$RESOLVED" && ! -f "$ENGINE_IN_CACHE" ]]; then
                cp -f "$RESOLVED" "$ENGINE_IN_CACHE"
                echo "TRACKER_ENGINE: CACHED  $RESOLVED  ->  $ENGINE_IN_CACHE"
            fi
        else
            if [[ ! -f "$ENGINE_IN_CACHE" ]]; then
                cp -f "$ENGINE_AT_DEST" "$ENGINE_IN_CACHE"
                echo "TRACKER_ENGINE: CACHED  $ENGINE_AT_DEST  ->  $ENGINE_IN_CACHE"
            elif [[ "$ENGINE_AT_DEST" -nt "$ENGINE_IN_CACHE" ]]; then
                cp -f "$ENGINE_AT_DEST" "$ENGINE_IN_CACHE"
                echo "TRACKER_ENGINE: REFRESHED_CACHE  $ENGINE_IN_CACHE (newer build)"
            fi
        fi

        for tdir in "${TRACKER_DIRS[@]}"; do
            target="$tdir/$ENGINE_BASENAME"
            current=$(readlink -f "$target" 2>/dev/null || true)
            if [[ "$current" == "$ENGINE_IN_CACHE" ]]; then
                continue
            fi
            install -d -m 0755 "$tdir"
            chown "${STORAGE_UID}:${STORAGE_GID}" "$tdir"
            ln -sfn -T "$ENGINE_IN_CACHE" "$target" 2>/dev/null || continue
            echo "TRACKER_ENGINE: LINKED  $target  ->  $ENGINE_IN_CACHE"
        done
    done
else
    shopt -s nullglob
    mapfile -t CACHED_ENGINES < <(
        ls -1t "$ENGINE_CACHE_DIR/${REID_BASENAME}"_b*_gpu*_fp*.engine 2>/dev/null
    )
    shopt -u nullglob
    if (( ${#CACHED_ENGINES[@]} > 0 )); then
        for ENGINE_IN_CACHE in "${CACHED_ENGINES[@]}"; do
            ENGINE_BASENAME=$(basename "$ENGINE_IN_CACHE")
            for tdir in "${TRACKER_DIRS[@]}"; do
                install -d -m 0755 "$tdir"
                chown "${STORAGE_UID}:${STORAGE_GID}" "$tdir"
                ln -sfn -T "$ENGINE_IN_CACHE" "$tdir/$ENGINE_BASENAME" 2>/dev/null || continue
                echo "TRACKER_ENGINE: LINKED  $tdir/$ENGINE_BASENAME  ->  $ENGINE_IN_CACHE  (skip rebuild)"
            done
        done
    else
        echo "TRACKER_ENGINE: NO_BUILD_YET  no engine in Tracker dirs (${TRACKER_DIRS[*]}) or $ENGINE_CACHE_DIR — will build on first launch (~90-120 s)"
    fi
fi
