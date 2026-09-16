#!/bin/bash

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

# Pin Docker CE + plugins + containerd.io to a known-good combination, but
# ONLY when the host's Docker is outside the tested range. Some Brev
# launchables ship a newer Docker than the VSS deploy profiles are tested
# against; pin explicitly so compose/buildx incompatibilities don't surface
# mid-deployment.
#
# When the installed Docker already falls in [28.3.3, 29.5.0) the version
# downgrade is skipped: re-pinning to an exact epoch-versioned package that
# the platform's apt repo may not carry (e.g. DGX Spark / DGX-OS on arm64)
# fails with "version not found" for no benefit. The in-range packages are
# still held so the box can't drift past the tested range.
#
# Run this BEFORE anything the host is meant to keep running: a docker-ce
# downgrade restarts dockerd, which disrupts live containers.
#
# Idempotent -- safe to re-run.

set -euo pipefail

# Tested Docker Engine range -- keep in sync with the VSS launchable prereq check.
MIN_DOCKER_VERSION="28.3.3"
MAX_DOCKER_VERSION="29.5.0"

# Packages frozen with `apt-mark hold` so unattended-upgrades / later
# `apt-get install` calls can't drift the box afterwards.
HOLD_PKGS="docker-ce docker-ce-cli docker-buildx-plugin docker-compose-plugin containerd.io"

version_ge() { [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n1)" = "$2" ]; }
version_lt() { [ "$1" != "$2" ] && [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | head -n1)" = "$1" ]; }

DOCKER_VERSION="$(docker version --format '{{.Server.Version}}' 2>/dev/null || true)"
if [ -n "$DOCKER_VERSION" ] \
   && version_ge "$DOCKER_VERSION" "$MIN_DOCKER_VERSION" \
   && version_lt "$DOCKER_VERSION" "$MAX_DOCKER_VERSION"; then
  echo "Docker $DOCKER_VERSION is within the tested range [$MIN_DOCKER_VERSION, $MAX_DOCKER_VERSION); skipping the Docker version pin."
  # No downgrade needed, but still hold the in-range packages at their
  # current versions so unattended-upgrades / later apt-get calls can't drift
  # the box past the tested range.
  sudo apt-mark hold $HOLD_PKGS
  exit 0
fi

if [ -n "$DOCKER_VERSION" ]; then
  echo "Docker $DOCKER_VERSION is outside the tested range [$MIN_DOCKER_VERSION, $MAX_DOCKER_VERSION); pinning to known-good versions."
else
  echo "Could not read the installed Docker version; pinning to known-good versions."
fi

# Read distro info from /etc/os-release (always present on Ubuntu; minimal
# images don't ship `lsb_release`).
. /etc/os-release
DISTRO="${VERSION_ID}"
CODENAME="${UBUNTU_CODENAME:-${VERSION_CODENAME}}"

# Versions hard-coded to what shipped alongside docker-ce 29.4.3 on the
# Docker apt repo (verified against download.docker.com + upstream GitHub
# release timestamps). When bumping DOCKER_CE_VER, bump these four together.
DOCKER_CE_VER="5:29.4.3-1~ubuntu.${DISTRO}~${CODENAME}"
BUILDX_VER="0.33.0-1~ubuntu.${DISTRO}~${CODENAME}"
COMPOSE_VER="5.1.3-1~ubuntu.${DISTRO}~${CODENAME}"
CONTAINERD_VER="2.2.3-1~ubuntu.${DISTRO}~${CODENAME}"

# Refresh the APT cache first -- without this, the specific epoch-versioned
# package may not be in the local index and the install would fail with
# version-not-found before any pinning takes effect.
sudo apt-get update -qq

sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  --allow-downgrades \
  -o Dpkg::Options::=--force-confdef \
  -o Dpkg::Options::=--force-confold \
  docker-ce="$DOCKER_CE_VER" \
  docker-ce-cli="$DOCKER_CE_VER" \
  docker-buildx-plugin="$BUILDX_VER" \
  docker-compose-plugin="$COMPOSE_VER" \
  containerd.io="$CONTAINERD_VER"

# Hold so unattended-upgrades / later `apt-get install` calls don't drift
# the box back to newer versions.
sudo apt-mark hold $HOLD_PKGS
