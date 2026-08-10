#!/usr/bin/env bash

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

# Install the media packages VIOS needs at runtime, before launch_vst. The base
# image ships without them (and deletes the files of the ones that arrive as
# transitive dependencies), so this script restores them on first start.
#
# Keep this idempotent: a retained container does not need an APT transaction on
# restart. The marker file plus a spot check of the installed libraries is what
# makes the restart path free.
set -e  # Exit on any error

# Ensure non-interactive mode for apt operations
export DEBIAN_FRONTEND=noninteractive

FORCE_INSTALL="${VST_FORCE_ADDITIONAL_PACKAGES_INSTALL:-false}"

# Randomized timeout avoids a thundering herd when several replicas (5 nvstreamers
# plus stream-processing) cold-start against the same mirror at once.
APT_UPDATE_TIMEOUT="${VST_APT_UPDATE_TIMEOUT:-$((200 + RANDOM % 101))}"
MAX_RETRIES="${VST_APT_MAX_RETRIES:-3}"

# Runtime libraries only; development packages are intentionally excluded.
#
# The four top-level entries are what VIOS actually needs. Everything below them
# arrives as a transitive dependency, but the base image deletes the files of the
# ones it already has installed, so they must be listed explicitly for
# --reinstall to restore them.
RUNTIME_PACKAGES=(
  gstreamer1.0-plugins-good gstreamer1.0-plugins-bad
  gstreamer1.0-plugins-ugly gstreamer1.0-libav
  libopencv-core406t64 libv4lconvert0t64
  # JPEG and PNG are pruned from the base image and restored here; the packaged
  # application links them for snapshot and image encoding.
  libjpeg8 libjpeg-turbo8 libpng16-16t64
  libvo-aacenc0 libfaad2 libswresample4 libavutil58 libswscale7 libpostproc57
  libavcodec60 libavformat60 libavfilter9 libde265-0 libx265-199 libx264-164
  libmpeg2encpp-2.1-0t64 libmpeg2-4 libmpg123-0t64 libbs2b0 libreadline8t64
  libcdio19t64 libdca0 libdvdnav4 libmjpegutils-2.1-0t64 liba52-0.7.4
  libdvdread8t64 libsbc1 libzvbi0t64 libmp3lame0 libsidplay1v5 liblrdf0
  libneon27t64 libflac12t64 libxvidcore4 libvpx9 libopenh264-7
  # libavcodec/libavformat link these directly. The base image deletes their
  # files (they arrive as plugins-base/good dependencies), so without an
  # explicit --reinstall the libav dlopen in LibavWrapper fails and file upload
  # cannot read duration/codec. libgstlibav.so needs the same set.
  libogg0 libvorbis0a libvorbisenc2 libopus0 libspeex1 libtheora0 libtwolame0
  libwebp7 libsharpyuv0
)

# The marker is keyed on the package list itself, so editing RUNTIME_PACKAGES
# automatically invalidates markers written by an older revision of this script.
# Without this, a container that completed an install with a previous list would
# skip forever and never pick up newly added packages.
PACKAGE_SET_ID="$(printf '%s\n' "${RUNTIME_PACKAGES[@]}" | md5sum | cut -c1-10)"
MARKER_FILE="${VST_ADDITIONAL_PACKAGES_MARKER:-/var/lib/vios/additional-packages-installed-${PACKAGE_SET_ID}}"

is_dpkg_broken() {
  dpkg --audit 2>/dev/null | grep -q .
}

# Sentinels for the package groups this script installs: plugins-bad, libav and
# libv4lconvert (whose files the base image deletes).
#
# Existence alone is not enough. The base image deletes libraries that libav and
# several plugins link against, which leaves the plugin file on disk but
# unloadable -- that is exactly how avdec_* silently disappeared while every
# sentinel still "existed". Check that each one actually resolves.
runtime_present() {
  local required match
  for required in \
    /usr/lib/*-linux-gnu/libv4lconvert.so.0 \
    /usr/lib/*-linux-gnu/gstreamer-1.0/libgstvideoparsersbad.so \
    /usr/lib/*-linux-gnu/gstreamer-1.0/libgstmpegtsmux.so \
    /usr/lib/*-linux-gnu/gstreamer-1.0/libgstlibav.so; do
    compgen -G "${required}" >/dev/null || return 1
    for match in ${required}; do
      if ldd "${match}" 2>/dev/null | grep -q "not found"; then
        return 1
      fi
    done
  done
}

if [[ "${FORCE_INSTALL}" != "true" && -f "${MARKER_FILE}" ]] && runtime_present; then
  echo "Additional packages already installed; skipping APT."
  exit 0
fi

if is_dpkg_broken; then
  echo "Repairing incomplete dpkg state..."
  dpkg --configure -a
fi

# Keep public Ubuntu repositories as the default. aarch64 packages are served
# from Ubuntu Ports; no NVIDIA-internal mirror is required or assumed.
if [[ "$(uname -m)" == *"aarch64"* ]] && ! grep -qr "ports.ubuntu.com" /etc/apt/sources.list.d 2>/dev/null; then
  echo "Detected aarch64, configuring HTTPS for ports.ubuntu.com..."
  cat >/etc/apt/sources.list.d/ubuntu.sources <<'EOF'
Types: deb
URIs: https://ports.ubuntu.com/ubuntu-ports/
Suites: noble noble-updates
Components: main universe
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg

Types: deb
URIs: https://ports.ubuntu.com/ubuntu-ports/
Suites: noble-security
Components: main universe
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg
EOF
fi

# Handle network-level timeouts gracefully without killing dpkg.
APT_OPTS=(
  -o Acquire::http::Timeout=30
  -o Acquire::https::Timeout=30
  -o Acquire::Retries=5
  -o DPkg::Lock::Timeout=60
  -o Acquire::ForceIPv4=true
  -o Acquire::http::Pipeline-Depth=0
  -o Dpkg::Options::=--force-confdef
  -o Dpkg::Options::=--force-confold
)

install_packages() {
  apt-get install --reinstall -y --no-install-recommends "${APT_OPTS[@]}" "${RUNTIME_PACKAGES[@]}"
}

refresh_apt_metadata() {
  local attempt
  for attempt in $(seq 1 "${MAX_RETRIES}"); do
    echo "Running apt-get update (attempt ${attempt}/${MAX_RETRIES}, timeout: ${APT_UPDATE_TIMEOUT}s)..."
    if timeout "${APT_UPDATE_TIMEOUT}" apt-get update "${APT_OPTS[@]}"; then
      return 0
    fi
    echo "apt-get update attempt ${attempt}/${MAX_RETRIES} failed."
    if [[ ${attempt} -lt ${MAX_RETRIES} ]]; then
      # Clear partially fetched or corrupted indexes so the retry starts clean.
      echo "Cleaning apt lists..."
      rm -rf /var/lib/apt/lists/*
      echo "Retrying in $((2 * attempt))s..."
      sleep $((2 * attempt))
    fi
  done
  return 1
}

# Reuse the base image's APT metadata first. Refresh only if installation shows
# it is stale or a package is unavailable, keeping the normal path offline-fast.
echo "Installing VIOS runtime media packages..."
if ! install_packages; then
  echo "Initial install failed; refreshing APT metadata."
  if ! refresh_apt_metadata; then
    echo "ERROR: Unable to refresh APT metadata after ${MAX_RETRIES} attempts."
    exit 1
  fi
  if is_dpkg_broken; then
    echo "Repairing incomplete dpkg state..."
    dpkg --configure -a
  fi
  install_packages
fi

# OSRB: strip Intel MediaSDK / QuickSync (QSV) codec libs re-pulled as part of the
# gstreamer1.0-plugins-bad install above. VIOS uses NVIDIA NVENC/NVDEC exclusively.
echo "Removing unused Intel MediaSDK / QSV (patent watchlist) libraries..."
for libdir in /usr/lib/*-linux-gnu; do
  rm -f "${libdir}"/mfx/libmfx_*_hw64.so* \
        "${libdir}"/libmfx.so* "${libdir}"/libmfxhw64.so* "${libdir}"/libmfx-tracer.so* \
        "${libdir}"/gstreamer-1.0/libgstmsdk.so* \
        "${libdir}"/gstreamer-1.0/libgstqsv.so*
  rm -rf "${libdir}"/mfx
done

# Force GStreamer to rebuild its plugin registry so the newly installed plugins
# are picked up.
echo "Cleaning up GStreamer cache..."
rm -rf ~/.cache/gstreamer-1.0/

install -d "$(dirname "${MARKER_FILE}")"
date --iso-8601=seconds >"${MARKER_FILE}"
echo "Installation completed successfully!"
