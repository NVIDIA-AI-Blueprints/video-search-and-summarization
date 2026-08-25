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

# The base image may preload libraries that are restored by this installer.
# Do not pass unavailable preload paths to apt/dpkg helper processes.
unset LD_PRELOAD

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
#
# VST_APT_MIRROR overrides the archive for deployments that are far from the
# public mirrors or behind a restricted egress. Download time is dominated by
# which mirror answers, not by bandwidth: the same 24.6MB fetch measures ~10s
# against a close mirror and has been observed to take minutes against a slow
# or stalled one. Point this at an internal mirror to make startup predictable.
#
#   VST_APT_MIRROR=http://mirror.example.com/ubuntu           (x86_64)
#   VST_APT_MIRROR=http://mirror.example.com/ubuntu-ports     (aarch64)
if [[ "$(uname -m)" == *"aarch64"* ]]; then
  APT_MIRROR="${VST_APT_MIRROR:-https://ports.ubuntu.com/ubuntu-ports/}"
else
  APT_MIRROR="${VST_APT_MIRROR:-}"
fi

if [[ -n "${APT_MIRROR}" ]]; then
  # Trailing slash keeps the generated URIs well formed whichever form is passed.
  [[ "${APT_MIRROR}" == */ ]] || APT_MIRROR="${APT_MIRROR}/"
fi

if [[ -n "${VST_APT_MIRROR:-}" ]]; then
  echo "Using APT mirror: ${APT_MIRROR}"
  cat >/etc/apt/sources.list.d/ubuntu.sources <<EOF
Types: deb
URIs: ${APT_MIRROR}
Suites: noble noble-updates noble-security
Components: main universe
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg
EOF
  # A mirror supersedes whatever the base image shipped; leaving the original
  # list in place would send half the requests back to the slow archive.
  rm -f /etc/apt/sources.list
elif [[ "$(uname -m)" == *"aarch64"* ]] && ! grep -qr "ports.ubuntu.com" /etc/apt/sources.list.d 2>/dev/null; then
  echo "Detected aarch64, configuring HTTPS for ports.ubuntu.com..."
  cat >/etc/apt/sources.list.d/ubuntu.sources <<EOF
Types: deb
URIs: ${APT_MIRROR}
Suites: noble noble-updates
Components: main universe
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg

Types: deb
URIs: ${APT_MIRROR}
Suites: noble-security
Components: main universe
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg
EOF
fi

# Handle network-level timeouts gracefully without killing dpkg.
#
# HTTP pipelining is left at the APT default. Forcing Pipeline-Depth=0 serialises
# every request and measured ~40% slower on this package set (13s vs 8s for the
# same 52 packages / 24.6MB). Set VST_APT_PIPELINE_DEPTH=0 to restore the
# serialised behaviour if a proxy or mirror mishandles pipelined requests.
#
# ForceIPv4 stays on: a host with broken IPv6 otherwise waits for the v6 attempt
# to time out on every connection.
APT_OPTS=(
  -o Acquire::http::Timeout=30
  -o Acquire::https::Timeout=30
  -o Acquire::Retries=5
  -o DPkg::Lock::Timeout=60
  -o Acquire::ForceIPv4=true
  -o Dpkg::Options::=--force-confdef
  -o Dpkg::Options::=--force-confold
)
if [[ -n "${VST_APT_PIPELINE_DEPTH:-}" ]]; then
  APT_OPTS+=(-o "Acquire::http::Pipeline-Depth=${VST_APT_PIPELINE_DEPTH}")
fi

# ---------------------------------------------------------------------------
# Shared package cache
#
# Every VIOS service on a host installs the same package set, and each cold
# start otherwise re-downloads ~138MB. An init container populates a shared
# volume once (VST_APT_CACHE_POPULATE=true); services then install from it with
# no network.
#
# Consumers mount the cache READ-ONLY at VST_APT_CACHE_DIR. Two options make
# that work and both matter:
#
#   Dir::Cache::archives  points apt at the shared copy instead of its own.
#   Debug::NoLocking      skips the archives lock. apt takes that lock even with
#                         nothing to download, and a shared volume means a shared
#                         lock file -- measured: two containers starting together,
#                         the loser fails outright with
#                         "Could not get lock ... held by process 0".
#
# Skipping the lock is safe HERE ONLY because consumers never write to that
# directory: the mount is read-only and the init container has finished. If the
# mount is ever made writable, or a service can start before the init completes,
# restore locking -- two writers with NoLocking race unprotected.
APT_CACHE_DIR="${VST_APT_CACHE_DIR:-/opt/apt-cache}"
APT_CACHE_POPULATE="${VST_APT_CACHE_POPULATE:-false}"

# Snapshot the options WITHOUT the cache overrides, so a cache-mode install that
# fails (stale, partial, or -- being read-only -- unable to fetch a package it
# lacks) can fall back to a normal networked install instead of blocking startup.
BASE_APT_OPTS=("${APT_OPTS[@]}")
CACHE_MODE=false
if [[ "${APT_CACHE_POPULATE}" != "true" ]] && compgen -G "${APT_CACHE_DIR}/*.deb" >/dev/null 2>&1; then
  echo "Using shared package cache at ${APT_CACHE_DIR} ($(ls "${APT_CACHE_DIR}"/*.deb | wc -l) packages)."
  APT_OPTS+=(-o "Dir::Cache::archives=${APT_CACHE_DIR}" -o Debug::NoLocking=true)
  CACHE_MODE=true
fi


install_packages() {
  apt-get install --reinstall -y --no-install-recommends "${APT_OPTS[@]}" "${RUNTIME_PACKAGES[@]}"
}

# APT already retries individual downloads (Acquire::Retries), but a connection
# that drops mid-transaction fails the whole install. Retry the install itself so
# a brief outage does not fail container startup.
#
# Deliberately not wrapped in `timeout`: killing apt-get install midway can leave
# dpkg half-configured, which is worse than a slow start. Repair between attempts
# instead -- a failed install is exactly what leaves packages unconfigured, and
# without this the retry fails the same way.
install_packages_with_retries() {
  local attempt
  for attempt in $(seq 1 "${MAX_RETRIES}"); do
    if install_packages; then
      return 0
    fi
    echo "apt-get install attempt ${attempt}/${MAX_RETRIES} failed."
    if [[ ${attempt} -lt ${MAX_RETRIES} ]]; then
      if is_dpkg_broken; then
        echo "Repairing incomplete dpkg state before retry..."
        dpkg --configure -a || true
      fi
      echo "Retrying in $((2 * attempt))s..."
      sleep $((2 * attempt))
    fi
  done
  return 1
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

# ---------------------------------------------------------------------------
# Init-container mode: populate the shared cache and exit.
#
# The only writer, so it removes docker-clean -- the base image ships that file
# and it deletes every .deb after each apt operation, which would leave the cache
# volume empty and the whole scheme silently doing nothing.
#
# flock serialises populators: in the standalone deployment nvstreamer and
# stream-processing are separate compose projects, so depends_on cannot order
# them and both run an init against the same volume. The second blocks here, then
# finds the cache warm and returns in seconds.
if [[ "${APT_CACHE_POPULATE}" == "true" ]]; then
  echo "Populating shared package cache in ${APT_CACHE_DIR}..."
  install -d "${APT_CACHE_DIR}/partial"
  rm -f /etc/apt/apt.conf.d/docker-clean
  exec 9>"${APT_CACHE_DIR}/.populate.lock"
  if ! flock -w "${VST_APT_CACHE_LOCK_WAIT:-600}" 9; then
    echo "ERROR: timed out waiting for another cache populator."
    exit 1
  fi
  if ! refresh_apt_metadata; then
    echo "ERROR: Unable to refresh APT metadata."
    exit 1
  fi
  # -d downloads without installing: this container only fills the cache.
  if ! apt-get install --reinstall -d -y --no-install-recommends \
         "${APT_OPTS[@]}" -o "Dir::Cache::archives=${APT_CACHE_DIR}" \
         "${RUNTIME_PACKAGES[@]}"; then
    echo "ERROR: Unable to populate the package cache."
    exit 1
  fi
  echo "Cache ready: $(ls "${APT_CACHE_DIR}"/*.deb 2>/dev/null | wc -l) packages, $(du -sh "${APT_CACHE_DIR}" | cut -f1)."
  exit 0
fi

# A mirror override invalidates the metadata the base image ships, which is
# indexed against the default archive. Measured: with a mirror configured and the
# shipped lists in place, apt resolves no download URI at all, so the fast path
# below would fail and only then refresh. Refresh up front instead of paying for
# a doomed attempt first.
if [[ -n "${VST_APT_MIRROR:-}" ]]; then
  echo "APT mirror configured; refreshing metadata before install."
  if ! refresh_apt_metadata; then
    echo "ERROR: Unable to refresh APT metadata from ${APT_MIRROR}."
    exit 1
  fi
fi

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
  if ! install_packages_with_retries; then
    if [[ "${CACHE_MODE}" == "true" ]]; then
      # The shared cache is stale, incomplete, or (being read-only) cannot be
      # written to for a package it lacks. Never let that block startup: drop
      # the cache overrides and install normally over the network -- exactly
      # what a deployment without the cache does. The cache is an optimization,
      # not a hard dependency.
      echo "Cache-mode install failed; falling back to a normal networked install (bypassing the shared cache)."
      APT_OPTS=("${BASE_APT_OPTS[@]}")
      CACHE_MODE=false
      if ! refresh_apt_metadata; then
        echo "ERROR: Unable to refresh APT metadata for the cache-fallback install."
        exit 1
      fi
      if ! install_packages_with_retries; then
        echo "ERROR: Unable to install runtime media packages after the cache fallback."
        exit 1
      fi
    else
      echo "ERROR: Unable to install runtime media packages after ${MAX_RETRIES} attempts."
      exit 1
    fi
  fi
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
