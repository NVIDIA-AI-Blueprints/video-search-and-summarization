#!/bin/sh
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

set -e
CONFIG="${WDM_WORKLOADS_CONFIG:-/config.yml}"
if [ ! -f "$CONFIG" ] || [ ! -r "$CONFIG" ]; then
  echo "wdm-router-entrypoint: missing or unreadable workload config at: $CONFIG" >&2
  echo "  Mount config.yml into the container and point WDM_WORKLOADS_CONFIG at it (default /config.yml)." >&2
  echo "  Example: -e WDM_WORKLOADS_CONFIG=/config.yml -v \"\$PWD/config.yml:/config.yml:ro\"" >&2
  exit 1
fi
GEN_OUT="${ENVOY_GENERATED_CONFIG:-/tmp/envoy-wdm-generated.yaml}"
python3 /opt/wdm-runtime/envoy/generate_envoy_config_xds_mw.py \
    --config "$CONFIG" \
    --out "$GEN_OUT"
/sdr-mw &
N=$(nproc)
MAX=$((N - 1))
[ "$MAX" -gt 63 ] && MAX=63
echo "$(date '+%Y-%m-%d %H:%M:%S') INFO [envoy] Pinning envoy to CPUs 0-$MAX (host exposes $N CPU(s)) to avoid tcmalloc percpu crash"
# Match SDRC Python layout: timestamp, LEVEL, [envoy], category, message.
# Envoy's %l is lowercase only; pipe through sed to emit INFO/DEBUG/… like SDRC.
# Filter with: grep '\[envoy\]'  (see lib/logging/wdm_logging.py / README).
ENVOY_LOG_FORMAT="${ENVOY_LOG_FORMAT:-%Y-%m-%d %T.%e %l [envoy] [%n] %v}"
ENVOY_FIFO="${ENVOY_LOG_FIFO:-/tmp/envoy-sdrc.log.fifo}"
rm -f "$ENVOY_FIFO"
mkfifo "$ENVOY_FIFO"

# Line-buffered: uppercase severity token after the timestamp.
sed -u -E 's/^([0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]+) (trace|debug|info|warning|error|critical) /\1 \U\2 /' \
  < "$ENVOY_FIFO" &
SED_PID=$!

/usr/bin/taskset -c 0-"$MAX" /usr/local/bin/envoy \
  -c "$GEN_OUT" \
  --concurrency 16 \
  --base-id 1 \
  --log-format "$ENVOY_LOG_FORMAT" \
  > "$ENVOY_FIFO" 2>&1 &
ENVOY_PID=$!

term_handler() {
  kill -TERM "$ENVOY_PID" 2>/dev/null || true
  wait "$ENVOY_PID" 2>/dev/null || true
  kill "$SED_PID" 2>/dev/null || true
  rm -f "$ENVOY_FIFO"
  exit 0
}
trap term_handler TERM INT

STATUS=0
wait "$ENVOY_PID" || STATUS=$?
kill "$SED_PID" 2>/dev/null || true
rm -f "$ENVOY_FIFO"
exit "$STATUS"
