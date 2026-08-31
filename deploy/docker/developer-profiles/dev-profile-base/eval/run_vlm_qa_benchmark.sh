#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../../../" && pwd)"
export VSS_REPO_ROOT="${VSS_REPO_ROOT:-${REPO_ROOT}}"

exec python3 "${SCRIPT_DIR}/benchmark_vlm_qa.py" --repo-root "${VSS_REPO_ROOT}" "$@"
