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

# Recreate one or more already-deployed services with an extra env file layered
# on top, leaving the rest of the stack alone. Used to swap the gateway between
# the deployment's real public origin and the synthetic one in brevsim.env
# without redeploying anything else.
#
#   usage: recreate.sh <extra-env-file|-> <service...>
#
# `-` means "no extra env file", which is how a service is put back:
#
#   ./recreate.sh brevsim.env vss-haproxy-ingress   # apply the synthetic origin
#   ./recreate.sh - vss-haproxy-ingress             # restore the real one
set -euo pipefail
# deploy/docker, two levels up from scripts/gateway-harness/.
D="${DOCKER_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
P=$D/developer-profiles/dev-profile-${PROFILE:-alerts}
EXTRA="$1"; shift
ARGS=(docker compose -f "$D/compose.yml"
  --env-file "$D/containers.env"
  --env-file "$P/.env"
  --env-file "$P/overrides.env"
  --env-file "$P/generated.env")
[[ "$EXTRA" != "-" ]] && ARGS+=(--env-file "$(readlink -f "$EXTRA")")
cd "$D"
"${ARGS[@]}" up -d --no-deps --force-recreate "$@"
