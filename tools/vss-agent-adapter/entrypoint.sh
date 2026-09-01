#!/bin/sh
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Point the `vss` CLI at this deployment, then serve.
#
# POST /v1/search shells out to that CLI, which refuses to run until a
# deployment is recorded ("no deployment configured"). Doing it here rather
# than baking it into the image keeps the origin a deployment concern, and
# re-running on every start means a moved ingress is picked up rather than
# remembered wrongly.
#
# Best-effort on purpose: chat and the skills endpoints do not depend on the
# CLI, so a VSS that is still coming up must not stop the adapter from serving.
# /v1/search reports the failure itself if it is called too early.
set -e

if [ -n "${VSS_SEARCH_BASE_URL}" ]; then
  echo "[adapter] configuring vss CLI against ${VSS_SEARCH_BASE_URL}"
  if ! uv run --project "${VSS_REPO_ROOT:-/repo}/services/agent" --no-dev --extra cli \
      vss configure --base-url "${VSS_SEARCH_BASE_URL}"; then
    echo "[adapter] vss configure failed; /v1/search will report it until this succeeds" >&2
  fi
fi

exec python3 /app/adapter.py
