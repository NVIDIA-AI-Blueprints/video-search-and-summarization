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

# install_pylon.sh — activate the Basler pylon SDK at container startup (run from entrypoint before launch_vst).
#
# Pylon is proprietary and large (~1.3 GB); kept out of the image to avoid redistribution concerns and bloat.
# Opt-in: no-op unless INSTALL_PYLON=1, safe to ship in every image.
# Source resolution order (first match wins):
#   1. PYLON_ROOT already populated (bind-mounted extracted SDK)
#   2. PYLON_SDK_ARCHIVE=<tarball>  (bind-mounted .tar.gz)
#   3. PYLON_SDK_URL=<url>          (internal mirror, fetched with curl/wget)
#
# Env vars:
#   INSTALL_PYLON       1 to enable (default: 0)
#   PYLON_ROOT          install/lookup dir (default: /opt/pylon)
#   PYLON_SDK_ARCHIVE   path to a bind-mounted pylon tarball
#   PYLON_SDK_URL       internal mirror URL

set -euo pipefail

log() { echo "[install_pylon] $*"; }

if [[ "${INSTALL_PYLON:-0}" != "1" ]]; then
  log "INSTALL_PYLON != 1; skipping (basler discovery stays disabled)"
  exit 0
fi

PYLON_ROOT="${PYLON_ROOT:-/opt/pylon}"

# Handles both the official setup wrapper and the inner SDK tarball.
extract_tarball() {
  local src="$1" tmp inner
  tmp="$(mktemp -d)"
  tar -xzf "${src}" -C "${tmp}"
  inner="$(find "${tmp}" -maxdepth 1 -name 'pylon-*_linux-*.tar.gz' | head -n1)"
  mkdir -p "${PYLON_ROOT}"
  if [[ -n "${inner}" ]]; then
    log "unwrapping setup archive (${inner##*/})"
    tar -xzf "${inner}" -C "${PYLON_ROOT}" --strip-components=1
  else
    tar -xzf "${src}" -C "${PYLON_ROOT}" --strip-components=1
  fi
  rm -rf "${tmp}"
}

if [[ -e "${PYLON_ROOT}/lib/libpylonbase.so" ]]; then
  log "pylon already present at ${PYLON_ROOT}"
elif [[ -n "${PYLON_SDK_ARCHIVE:-}" && -f "${PYLON_SDK_ARCHIVE}" ]]; then
  log "extracting ${PYLON_SDK_ARCHIVE} -> ${PYLON_ROOT}"
  extract_tarball "${PYLON_SDK_ARCHIVE}"
elif [[ -n "${PYLON_SDK_URL:-}" ]]; then
  log "downloading pylon from ${PYLON_SDK_URL}"
  tmpf="$(mktemp --suffix=.tar.gz)"
  if command -v curl >/dev/null 2>&1; then
    curl -fSL --retry 3 --connect-timeout 30 -o "${tmpf}" "${PYLON_SDK_URL}"
  elif command -v wget >/dev/null 2>&1; then
    wget -q -O "${tmpf}" "${PYLON_SDK_URL}"
  else
    log "ERROR: neither curl nor wget available to fetch PYLON_SDK_URL"
    exit 1
  fi
  extract_tarball "${tmpf}"
  rm -f "${tmpf}"
else
  log "ERROR: INSTALL_PYLON=1 but no pylon source found."
  log "       Provide one of: a populated ${PYLON_ROOT} (bind mount),"
  log "       PYLON_SDK_ARCHIVE=<tarball path>, or PYLON_SDK_URL=<mirror url>."
  exit 1
fi

if [[ ! -e "${PYLON_ROOT}/lib/libpylonbase.so" ]]; then
  log "ERROR: libpylonbase.so not found under ${PYLON_ROOT}/lib after setup"
  exit 1
fi

# Preload libpylonbase so GenICam typeinfo resolves at dlopen time (typeinfo cannot bind lazily).
# Both ldconfig and ld.so.preload require root; without root, export the vars on the service instead.
if [[ "$(id -u)" == "0" ]]; then
  echo "${PYLON_ROOT}/lib" > /etc/ld.so.conf.d/pylon.conf
  ldconfig
  preload="${PYLON_ROOT}/lib/libpylonbase.so"
  if ! grep -qxF "${preload}" /etc/ld.so.preload 2>/dev/null; then
    echo "${preload}" >> /etc/ld.so.preload
  fi
  log "registered libs (ldconfig) and preload (ld.so.preload)"
else
  log "not root: set LD_LIBRARY_PATH=${PYLON_ROOT}/lib and"
  log "          LD_PRELOAD=${PYLON_ROOT}/lib/libpylonbase.so on the service"
fi

log "pylon $("${PYLON_ROOT}/bin/pylon-config" --version 2>/dev/null || echo "(version unknown)") activated at ${PYLON_ROOT}"
