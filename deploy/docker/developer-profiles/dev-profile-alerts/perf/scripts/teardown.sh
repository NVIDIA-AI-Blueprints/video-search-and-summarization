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

# Stage 5: collect logs, stop the stack, clean up. Always runs, even after a
# failed stage, so it must not fail on a partially deployed stack.
set -uo pipefail
# shellcheck source=_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

_logs="${PERF_WORK_DIR}/logs"
mkdir -p "${_logs}"
log "teardown: collecting container logs to ${_logs}"
for _c in $(docker ps -a --filter "label=com.docker.compose.project=${COMPOSE_PROJECT}" --format '{{.Names}}'); do
  docker logs --timestamps "${_c}" > "${_logs}/${_c}.log" 2>&1 || true
done

log "teardown: docker compose down"
if [[ -f "${PERF_WORK_DIR}/perf-runtime.env" ]] && [[ -n "${PERF_CONFIG_ID:-}" ]]; then
  perf_compose down --volumes --remove-orphans || true
else
  docker compose -p "${COMPOSE_PROJECT}" down --volumes --remove-orphans || true
fi

# Staged videos are hard links/copies; drop them so the next entry starts clean.
rm -rf "${PERF_WORK_DIR}/videos" "${PERF_WORK_DIR}/deepstream-configs"
if [[ "${PERF_KEEP_GENERATED_ENV:-false}" != "true" ]]; then
  rm -f "${PROFILE_DIR}/generated.env"
fi
log "teardown done"
exit 0
