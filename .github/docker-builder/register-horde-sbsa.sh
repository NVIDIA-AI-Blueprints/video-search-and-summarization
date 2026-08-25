#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Register a *new* SBSA builder (horde-sbsa-NN). Does not modify
# horde-docker-0[1-4]. Cohort label only — no canary/active labels — so the
# listener can be online without receiving production or canary jobs.
#
# Required env:
#   RUNNER_TOKEN  short-lived GitHub Actions registration token
#   RUNNER_NAME   e.g. horde-sbsa-01
# Optional:
#   RUNNER_DIR    default /srv/github-actions/$RUNNER_NAME
set -euo pipefail

repo="NVIDIA-AI-Blueprints/video-search-and-summarization"
name="${RUNNER_NAME:?set RUNNER_NAME to horde-sbsa-NN}"
token="${RUNNER_TOKEN:?set RUNNER_TOKEN from the repo registration-token API}"
dir="${RUNNER_DIR:-/srv/github-actions/${name}}"

[[ "$name" =~ ^horde-sbsa-[0-9]{2}$ ]] || {
  echo "RUNNER_NAME must match horde-sbsa-NN" >&2
  exit 2
}

cd "$dir"
./config.sh --unattended \
  --url "https://github.com/${repo}" \
  --token "$token" \
  --name "$name" \
  --labels "vss-sbsa-builder" \
  --work _work \
  --replace
