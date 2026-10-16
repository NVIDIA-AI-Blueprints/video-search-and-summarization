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

# Poll Redis until PONG. Host/port come from /wdm-env/wdm.env (written from config.yml).
set -e
if [ -f /wdm-env/wdm.env ]; then
  set -a
  # shellcheck disable=SC1090
  . /wdm-env/wdm.env
  set +a
fi
if [ -z "${WDM_WL_REDIS_SERVER:-}" ]; then
  echo "WDM_WL_REDIS_SERVER missing: add to config.yml under the first enable:true workload, or ensure wdm-env-from-config ran." >&2
  exit 1
fi
port="${WDM_WL_REDIS_PORT:-6379}"
max="${REDIS_WAIT_TIMEOUT_SECONDS:-120}"
i=0
while [ "$i" -lt "$max" ]; do
  if redis-cli -h "$WDM_WL_REDIS_SERVER" -p "$port" ping 2>/dev/null | grep -q PONG; then
    echo "redis is up at ${WDM_WL_REDIS_SERVER}:${port}"
    exit 0
  fi
  echo "waiting for redis at ${WDM_WL_REDIS_SERVER}:${port}..."
  sleep 1
  i=$((i + 1))
done
echo "timeout waiting for redis at ${WDM_WL_REDIS_SERVER}:${port}" >&2
exit 1
