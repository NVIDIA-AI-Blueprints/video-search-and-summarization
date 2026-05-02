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

set -e  # Exit on any error

# Ensure non-interactive mode for apt operations
export DEBIAN_FRONTEND=noninteractive

# Generate random timeout to avoid thundering herd problem
APT_UPDATE_TIMEOUT=$((200 + RANDOM % 101))    # 200-300 seconds for apt-get update
MAX_RETRIES=3

# Function to check if dpkg is in a broken state
is_dpkg_broken() {
  # Check for packages in bad state (Half-installed, Unpacked, Reinst-required)
  if dpkg -l 2>/dev/null | grep -qE "^[HUR]"; then
    return 0  # dpkg is broken
  fi

  # Check dpkg audit for issues
  if dpkg --audit 2>&1 | grep -q .; then
    return 0  # dpkg has issues
  fi

  return 1  # dpkg is healthy
}

# Function to fix dpkg state
fix_dpkg() {
  echo "Fixing dpkg state..."

  # SAFETY: Check if apt/dpkg is already running before touching locks
  if pgrep -x apt-get >/dev/null 2>&1 || pgrep -x dpkg >/dev/null 2>&1 || pgrep -x apt >/dev/null 2>&1; then
    echo "Package manager is currently running, cannot safely fix dpkg state"
    echo "Waiting for package manager to complete..."
    return 1
  fi

  # Remove stale lock files (safe now that we checked for running processes)
  echo "Removing stale lock files..."
  rm -f /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock /var/cache/apt/archives/lock 2>/dev/null || true

  # Try to configure any pending packages
  dpkg --configure -a 2>/dev/null || true

  # Find and remove packages in bad state
  BAD_PKGS=$(dpkg -l 2>/dev/null | grep -E "^[HUR]" | awk '{print $2}' || true)
  if [ -n "$BAD_PKGS" ]; then
    echo "Removing packages in bad state: $BAD_PKGS"
    for pkg in $BAD_PKGS; do
      dpkg --remove --force-remove-reinstreq "$pkg" 2>/dev/null || true
    done
  fi

  # Verify fix worked
  if is_dpkg_broken; then
    echo "WARNING: dpkg may still have issues after fix attempt"
    return 1
  fi

  echo "dpkg state fixed successfully"
  return 0
}

echo "Checking and fixing dpkg state..."
if ! fix_dpkg; then
  echo "WARNING: Could not fix dpkg state (package manager may be running)"
  echo "Proceeding with caution - DPkg::Lock::Timeout will handle lock contention"
fi

# APT acquire options for robustness and performance
# These handle network-level timeouts gracefully without killing dpkg
APT_OPTS="-o Acquire::http::Timeout=30 \
-o Acquire::https::Timeout=30 \
-o Acquire::Retries=5 \
-o DPkg::Lock::Timeout=60 \
-o Acquire::ForceIPv4=true \
-o Acquire::http::Pipeline-Depth=0 \
-o Acquire::http::No-Cache=true \
-o Dpkg::Options::=--force-confdef \
-o Dpkg::Options::=--force-confold"

echo "Starting package installation for Ubuntu 24.04..."

# Optimize APT sources to only include necessary suites and components (HTTPS)
echo "Configuring APT sources for optimal performance..."

# Detect architecture - only modify sources for aarch64
ARCH=$(uname -m)
if [[ "$ARCH" == *"aarch64"* ]]; then
    # Skip modification if already configured with HTTPS ports.ubuntu.com
    if [ -f /etc/apt/sources.list.d/ubuntu.sources ] && grep -q "https://ports.ubuntu.com" /etc/apt/sources.list.d/ubuntu.sources; then
        echo "APT sources already configured with HTTPS ports.ubuntu.com, skipping modification..."
    else
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
fi

# Run apt-get update with timeout and retry logic
echo "Running apt-get update (timeout: ${APT_UPDATE_TIMEOUT}s)..."
for attempt in $(seq 1 $MAX_RETRIES); do
  if timeout ${APT_UPDATE_TIMEOUT}s apt-get update ${APT_OPTS}; then
    echo "apt-get update completed successfully"
    break
  else
    if [ $attempt -lt $MAX_RETRIES ]; then
      echo "apt-get update attempt $attempt/$MAX_RETRIES failed"

      # SAFE lock cleanup: only if locks exist AND no process running
      if [ -f /var/lib/dpkg/lock-frontend ] || [ -f /var/lib/dpkg/lock ]; then
        if ! pgrep -x apt-get >/dev/null 2>&1 && ! pgrep -x dpkg >/dev/null 2>&1 && ! pgrep -x apt >/dev/null 2>&1; then
          echo "Found stale lock files, clearing..."
          rm -f /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock /var/cache/apt/archives/lock 2>/dev/null || true
        else
          echo "Lock files present but package manager running in another process"
        fi
      fi

      # Clean corrupted apt lists for fresh retry
      echo "Cleaning apt lists..."
      rm -rf /var/lib/apt/lists/*

      echo "Retrying in $((5 * attempt))s..."
      sleep $((2 * attempt))
    else
      echo "ERROR: apt-get update failed after $MAX_RETRIES attempts"
      exit 1
    fi
  fi
done

# Install gstreamer1.0-libav with retry logic
echo "Installing gstreamer1.0-libav..."
for attempt in $(seq 1 $MAX_RETRIES); do
  if apt-get install -y ${APT_OPTS} gstreamer1.0-libav; then
    echo "gstreamer1.0-libav installed successfully"
    break
  else
    if [ $attempt -lt $MAX_RETRIES ]; then
      echo "Attempt $attempt/$MAX_RETRIES failed"

      # Check if dpkg is broken and fix if needed
      if is_dpkg_broken; then
        echo "Detected dpkg corruption, attempting fix..."
        fix_dpkg || echo "WARNING: dpkg fix may not have completed successfully"
      fi

      echo "Retrying in $((2 * attempt))s..."
      sleep $((2 * attempt))
    else
      echo "ERROR: Failed to install gstreamer1.0-libav after $MAX_RETRIES attempts"
      exit 1
    fi
  fi
done

# Reinstall GStreamer plugins and multimedia libraries (batch 1) with retry logic
echo "Reinstalling GStreamer plugins and core multimedia libraries..."
for attempt in $(seq 1 $MAX_RETRIES); do
  if apt-get install --reinstall -y ${APT_OPTS} \
      gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly \
      libvo-aacenc0 libfaad2 libswresample-dev libswresample4 libavutil-dev libavutil58 \
      libavcodec-dev libavcodec60 libavformat-dev libavformat60 libavfilter-dev libavfilter9 \
      libde265-dev libde265-0 libx265-199 libx264-164 libmpeg2encpp-2.1-0 libmpeg2-4 \
      libmpg123-0 libbs2b0 libreadline8 libcdio19 libdca0 libdvdnav4 libmjpegutils-2.1-0 \
      liba52-0.7.4 libdvdread8 libsbc1 libzvbi0 libmp3lame0 libsidplay1v5 liblrdf0 libneon27; then
    echo "GStreamer plugins installed successfully"
    break
  else
    if [ $attempt -lt $MAX_RETRIES ]; then
      echo "Attempt $attempt/$MAX_RETRIES failed"

      # Check if dpkg is broken and fix if needed
      if is_dpkg_broken; then
        echo "Detected dpkg corruption, attempting fix..."
        fix_dpkg || echo "WARNING: dpkg fix may not have completed successfully"
      fi

      echo "Retrying in $((2 * attempt))s..."
      sleep $((2 * attempt))
    else
      echo "ERROR: Failed to reinstall GStreamer plugins after $MAX_RETRIES attempts"
      exit 1
    fi
  fi
done

# Reinstall additional codec libraries (batch 2) with retry logic
echo "Reinstalling additional codec libraries..."
for attempt in $(seq 1 $MAX_RETRIES); do
  if apt-get install --reinstall -y ${APT_OPTS} \
      libflac12 libxvidcore4; then
    echo "Codec libraries installed successfully"
    break
  else
    if [ $attempt -lt $MAX_RETRIES ]; then
      echo "Attempt $attempt/$MAX_RETRIES failed"

      # Check if dpkg is broken and fix if needed
      if is_dpkg_broken; then
        echo "Detected dpkg corruption, attempting fix..."
        fix_dpkg || echo "WARNING: dpkg fix may not have completed successfully"
      fi

      echo "Retrying in $((2 * attempt))s..."
      sleep $((2 * attempt))
    else
      echo "ERROR: Failed to reinstall codec libraries after $MAX_RETRIES attempts"
      exit 1
    fi
  fi
done

# Reinstall libvpx and h264 libraries (batch 3) with retry logic
echo "Reinstalling libvpx and h264 libraries..."
for attempt in $(seq 1 $MAX_RETRIES); do
  if apt-get install --reinstall -y ${APT_OPTS} \
      libvpx9 libopenh264-7; then
    echo "libvpx/h264 libraries installed successfully"
    break
  else
    if [ $attempt -lt $MAX_RETRIES ]; then
      echo "Attempt $attempt/$MAX_RETRIES failed"

      # Check if dpkg is broken and fix if needed
      if is_dpkg_broken; then
        echo "Detected dpkg corruption, attempting fix..."
        fix_dpkg || echo "WARNING: dpkg fix may not have completed successfully"
      fi

      echo "Retrying in $((2 * attempt))s..."
      sleep $((2 * attempt))
    else
      echo "ERROR: Failed to reinstall libvpx/h264 libraries after $MAX_RETRIES attempts"
      exit 1
    fi
  fi
done

# Clean up GStreamer cache
echo "Cleaning up GStreamer cache..."
rm -rf ~/.cache/gstreamer-1.0/

echo "Installation completed successfully!"