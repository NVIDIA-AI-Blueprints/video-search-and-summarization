#!/usr/bin/env bash
######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
######################################################################################################
#
# apply_vss_sysctl.sh — Apply VSS kernel socket buffer tuning via /etc/sysctl.d/99-vss.conf
#
# Writes persistent sysctl settings for RTSP/streaming performance:
#   vm.overcommit_memory, net.core.rmem_max, net.core.wmem_max,
#   net.ipv4.tcp_rmem, net.ipv4.tcp_wmem.
# Optionally disables IPv6 when DISABLE_IPV6=true (system-wide, persistent).
#
# Must be run with sudo (e.g. sudo bash perf/apply_vss_sysctl.sh).
#
# Optional environment variable:
#   DISABLE_IPV6 — Set to 'true' to add net.ipv6.conf.* disable entries (default: false)
######################################################################################################

set -euo pipefail
# ---------------------------------------------------------------------------
#
# Kernel socket buffer tuning (applied once; persistent across reboots via sysctl.d)
# ---------------------------------------------------------------------------
# Large RTSP streams and concurrent VLM responses require larger socket buffers.
# rmem/wmem_max = 5 MiB  — raise the kernel ceiling for SO_RCVBUF/SO_SNDBUF
# tcp_rmem/tcp_wmem      — set min/default/max for TCP read/write buffers
# vm.overcommit_memory=1 — required by Redis; prevents background save failures
#                          and the "WARNING Memory overcommit must be enabled!" crash
# IPv6 disabling is opt-in via DISABLE_IPV6=true — see usage for caveats.
# ---------------------------------------------------------------------------

DISABLE_IPV6="${DISABLE_IPV6:-false}"

echo "[apply_vss_sysctl] Applying VSS kernel socket buffer settings via /etc/sysctl.d/99-vss.conf ..."

# Ensure the sysctl.d directory exists (non-fatal — sysctl tuning is optional)
mkdir -p /etc/sysctl.d 2>/dev/null || true

# Build sysctl content; IPv6 disabling is opt-in only.
_sysctl_content=""
if [[ "${DISABLE_IPV6}" == "true" ]]; then
    echo "[apply_vss_sysctl] WARNING: DISABLE_IPV6=true: writing net.ipv6.conf.* disable entries." >&2
    echo "[apply_vss_sysctl] WARNING: This disables IPv6 system-wide and persists across reboots — other services may be affected." >&2
    _sysctl_content="net.ipv6.conf.all.disable_ipv6 = 1
net.ipv6.conf.default.disable_ipv6 = 1
net.ipv6.conf.lo.disable_ipv6 = 1
"
fi
_sysctl_content+="vm.overcommit_memory = 1
net.core.rmem_max = 5242880
net.core.wmem_max = 5242880
net.ipv4.tcp_rmem = 4096 87380 16777216
net.ipv4.tcp_wmem = 4096 65536 16777216"

# Write VSS kernel settings to 99-vss.conf (persistent across reboots)
if ! printf '%s\n' "${_sysctl_content}" | tee /etc/sysctl.d/99-vss.conf > /dev/null; then
    echo "[apply_vss_sysctl] WARNING: Could not write /etc/sysctl.d/99-vss.conf — socket buffer tuning skipped." >&2
    exit 1
fi

# Reload sysctl to apply the new settings immediately
if sysctl --system >/dev/null 2>&1; then
    echo "[apply_vss_sysctl] Kernel socket buffers tuned."
else
    echo "[apply_vss_sysctl] WARNING: 'sysctl --system' failed — settings will apply after next reboot." >&2
    exit 1
fi
