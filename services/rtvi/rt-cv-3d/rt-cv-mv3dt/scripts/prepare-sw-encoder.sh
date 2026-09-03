#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#
# prepare-sw-encoder.sh — put the software (CPU) encoder into the perception
# image, for GPUs with no hardware encoder.
#
# Usage:
#   ./scripts/prepare-sw-encoder.sh             # prepare, point docker/.env at the result
#   ./scripts/prepare-sw-encoder.sh --check     # report only (exit 1 if missing)
#   ./scripts/prepare-sw-encoder.sh --probe-hw  # 0 hardware ok, 10 needs software, 2 unknown
#
# The image is slimmed: gstreamer1.0-plugins-ugly and libx264-164 are marked
# installed with their files deleted, so only --reinstall brings them back.
# DeepStream's user_additional_install.sh also reinstalls plugins-good and
# plugins-bad, which clobbers its own patched plugins; restoring just these two
# does not. It stays as a fallback.
#
# Needs network (apt) and root in the container (the image runs as uid 1000).
# Idempotent: exits early when the image already has a working encoder.
#
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$ROOT/docker/.env"

CHECK_ONLY=0
PROBE_HW=0
case "${1:-}" in
  --check)    CHECK_ONLY=1 ;;
  --probe-hw) PROBE_HW=1 ;;
esac

env_value() {
  local key="$1" v=""
  [ -f "$ENV_FILE" ] || return 1
  v="$(grep -E "^[[:space:]]*${key}=" "$ENV_FILE" | tail -1 | cut -d= -f2-)" || return 1
  v="${v%\"}"; v="${v#\"}"; v="${v%\'}"; v="${v#\'}"
  printf '%s' "$v"
}

IMAGE="$(env_value PERCEPTION_IMAGE)"; IMAGE="${IMAGE:-ghcr.io/nvidia-ai-blueprints/vss/vss-rt-cv}"
TAG="$(env_value PERCEPTION_TAG)";     TAG="${TAG:-develop-latest}"
TARGET_TAG="${TAG}-swenc"
GPU_DEVICE="${GPU_DEVICE:-$(env_value GPU_DEVICE)}"; GPU_DEVICE="${GPU_DEVICE:-0}"

# The .so can be present and still not load, so ask GStreamer for the element.
has_encoder() {
  docker run --rm --entrypoint sh "$1" \
    -c 'gst-inspect-1.0 x264enc' >/dev/null 2>&1
}

# docker commit captures the prep container's overridden entrypoint, so a
# prepared image can carry the encoder and still not start.
entrypoint_matches() {
  local want got
  want="$(docker inspect -f '{{json .Config.Entrypoint}}' "${IMAGE}:${TAG}" 2>/dev/null)"
  got="$(docker inspect -f '{{json .Config.Entrypoint}}' "$1" 2>/dev/null)"
  [ -n "$want" ] && [ "$want" = "$got" ]
}

manual_steps() {
  echo "       Prepare the image by hand instead (note -u 0: the image runs as uid 1000," >&2
  echo "       so without it apt cannot write):" >&2
  echo "         docker run -d --name swenc -u 0 --entrypoint sleep ${IMAGE}:${TAG} 3600" >&2
  echo "         docker exec -u 0 swenc sh -c 'apt-get update && apt-get install -y --reinstall gstreamer1.0-plugins-ugly libx264-164'" >&2
  echo "         docker commit --change \"ENTRYPOINT \$(docker inspect -f '{{json .Config.Entrypoint}}' ${IMAGE}:${TAG})\" \\" >&2
  echo "                       swenc ${IMAGE}:${TARGET_TAG} && docker rm -f swenc" >&2
  echo "       (the --change is required: the prep container overrides the entrypoint)" >&2
  echo "       Then set PERCEPTION_TAG=${TARGET_TAG} in docker/.env and restage." >&2
}

# The image is needed to deploy either way, so pulling here just moves it earlier.
ensure_image() {
  docker image inspect "${IMAGE}:${TAG}" >/dev/null 2>&1 && return 0
  echo "   pulling ${IMAGE}:${TAG} (needed to run at all; this takes a while)"
  docker pull "${IMAGE}:${TAG}" >/dev/null 2>&1
}

# 0 hardware encodes, 10 it cannot, 2 no answer. The element exists even where
# it cannot encode, so it is run, not inspected.
probe_hw_encoder() {
  command -v docker >/dev/null 2>&1 || return 2
  # No GPU visible means the probe cannot run, so do not pull an image for it.
  nvidia-smi -L >/dev/null 2>&1 || return 2
  ensure_image || return 2
  docker image inspect "${IMAGE}:${TAG}" >/dev/null 2>&1 || return 2
  # Jetson has no --gpus; failing to start says nothing about the encoder.
  docker run --rm --gpus "device=${GPU_DEVICE%%,*}" --entrypoint sh \
    "${IMAGE}:${TAG}" -c true >/dev/null 2>&1 || return 2
  if docker run --rm --gpus "device=${GPU_DEVICE%%,*}" --entrypoint sh "${IMAGE}:${TAG}" -c \
       'gst-launch-1.0 -q videotestsrc num-buffers=2 ! video/x-raw,width=256,height=256 ! nvvideoconvert ! nvv4l2h264enc ! fakesink' \
       >/dev/null 2>&1; then
    return 0
  fi
  return 10
}

command -v docker >/dev/null 2>&1 || {
  echo "ERROR: docker not found; cannot prepare the software encoder." >&2
  exit 1
}

if [ "$PROBE_HW" = 1 ]; then
  probe_hw_encoder
  exit $?
fi

ensure_image

if has_encoder "${IMAGE}:${TAG}"; then
  echo "   software encoder: already present in ${IMAGE}:${TAG}"
  exit 0
fi

if has_encoder "${IMAGE}:${TARGET_TAG}"; then
  if entrypoint_matches "${IMAGE}:${TARGET_TAG}"; then
    echo "   software encoder: found in ${IMAGE}:${TARGET_TAG} (prepared earlier)"
    if [ "$CHECK_ONLY" = 0 ] && [ -f "$ENV_FILE" ]; then
      sed -i -E "s|^[[:space:]]*PERCEPTION_TAG=.*|PERCEPTION_TAG=\"${TARGET_TAG}\"|" "$ENV_FILE"
      echo "   PERCEPTION_TAG -> ${TARGET_TAG}"
    fi
    exit 0
  fi
  # Has the encoder but would not start: rebuild rather than deploy it.
  echo "   ${IMAGE}:${TARGET_TAG} has the encoder but does not start like the base"
  echo "   image; rebuilding it."
fi

if [ "$CHECK_ONLY" = 1 ]; then
  echo "   software encoder: MISSING from ${IMAGE}:${TAG}"
  exit 1
fi

mkdir -p "$ROOT/generated"
C="mv3dt-swenc-prep-$$"
cleanup() { docker rm -f "$C" >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "── Preparing the software encoder in ${IMAGE}:${TAG} (apt install, a few minutes)"
if ! docker run -d --name "$C" -u 0 --entrypoint sleep "${IMAGE}:${TAG}" 3600 >/dev/null 2>&1; then
  echo "ERROR: could not start a container from ${IMAGE}:${TAG}." >&2
  manual_steps
  exit 1
fi

LOG="$ROOT/generated/sw-encoder-install.log"

# --reinstall: they are marked installed but their files were slimmed out.
docker exec -u 0 "$C" sh -c \
  'apt-get update && apt-get install -y --reinstall gstreamer1.0-plugins-ugly libx264-164 && rm -rf /root/.cache/gstreamer-1.0' \
  > "$LOG" 2>&1

if ! docker exec "$C" sh -c 'gst-inspect-1.0 x264enc' >/dev/null 2>&1; then
  echo "   minimal install did not yield x264enc; falling back to user_additional_install.sh" >&2
  docker exec -u 0 "$C" bash -c \
    'cd /opt/nvidia/deepstream/deepstream && bash user_additional_install.sh' \
    >> "$LOG" 2>&1
fi

if ! docker exec "$C" sh -c 'gst-inspect-1.0 x264enc' >/dev/null 2>&1; then
  echo "ERROR: could not make x264enc available; not committing." >&2
  echo "       Full log: generated/sw-encoder-install.log" >&2
  tail -5 "$LOG" | sed 's/^/         /' >&2
  echo "       This step needs network access to the Ubuntu archives." >&2
  manual_steps
  exit 1
fi

# This container has no GPU, so probing here blacklists every DeepStream plugin
# in the registry cache. Drop it so the deployment rebuilds it with the GPU.
docker exec -u 0 "$C" sh -c \
  'rm -rf /root/.cache/gstreamer-1.0 /home/*/.cache/gstreamer-1.0 /tmp/.cache/gstreamer-1.0 2>/dev/null; true' \
  >/dev/null 2>&1

# Restore the image's own entrypoint/cmd: commit would otherwise capture the
# prep container's sleep override.
commit_args=()
orig_ep="$(docker inspect -f '{{json .Config.Entrypoint}}' "${IMAGE}:${TAG}" 2>/dev/null)"
orig_cmd="$(docker inspect -f '{{json .Config.Cmd}}' "${IMAGE}:${TAG}" 2>/dev/null)"
[ -n "$orig_ep" ]  && [ "$orig_ep" != null ]  && commit_args+=(--change "ENTRYPOINT $orig_ep")
[ -n "$orig_cmd" ] && [ "$orig_cmd" != null ] && commit_args+=(--change "CMD $orig_cmd")

if ! docker commit "${commit_args[@]}" "$C" "${IMAGE}:${TARGET_TAG}" >/dev/null 2>&1; then
  echo "ERROR: docker commit to ${IMAGE}:${TARGET_TAG} failed." >&2
  manual_steps
  exit 1
fi

# The committed image must still start the way the original does.
new_ep="$(docker inspect -f '{{json .Config.Entrypoint}}' "${IMAGE}:${TARGET_TAG}" 2>/dev/null)"
if [ "$new_ep" != "$orig_ep" ]; then
  echo "ERROR: ${IMAGE}:${TARGET_TAG} did not keep the original entrypoint." >&2
  echo "         original: $orig_ep" >&2
  echo "         committed: $new_ep" >&2
  docker rmi "${IMAGE}:${TARGET_TAG}" >/dev/null 2>&1
  exit 1
fi

# Adding the encoder must not cost the pipeline its source bin. Needs the GPU:
# nv* plugins do not load without one.
if ! docker run --rm --gpus "device=${GPU_DEVICE%%,*}" --entrypoint sh \
       "${IMAGE}:${TARGET_TAG}" -c 'gst-inspect-1.0 nvmultiurisrcbin' >/dev/null 2>&1; then
  echo "ERROR: ${IMAGE}:${TARGET_TAG} has the encoder but lost nvmultiurisrcbin;" >&2
  echo "       the pipeline would fail to build. Not keeping this image." >&2
  docker rmi "${IMAGE}:${TARGET_TAG}" >/dev/null 2>&1
  exit 1
fi
echo "   committed ${IMAGE}:${TARGET_TAG}"

if [ -f "$ENV_FILE" ] && grep -qE '^[[:space:]]*PERCEPTION_TAG=' "$ENV_FILE"; then
  sed -i -E "s|^[[:space:]]*PERCEPTION_TAG=.*|PERCEPTION_TAG=\"${TARGET_TAG}\"|" "$ENV_FILE"
  echo "   PERCEPTION_TAG -> ${TARGET_TAG}"
else
  echo "   ⚠ docker/.env has no PERCEPTION_TAG line; set PERCEPTION_TAG=${TARGET_TAG} by hand" >&2
fi

echo "── Software encoder ready. This image is local to this host: a later image"
echo "   bump means re-running this script."
