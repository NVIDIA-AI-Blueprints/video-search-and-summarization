#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Mirrors CI Job 9 (license-check-python). Scans only runtime deps that
# actually ship — dev-only tools (yamllint is GPL, others vary) are
# excluded via --no-default-groups. We use `uv pip install` (not
# `uv run pip install`) and `uv run --no-sync` so uv does not auto-resync
# the dev group back into the venv.

set -euo pipefail

cd "$(git rev-parse --show-toplevel)/services/agent"

uv sync --frozen --no-default-groups --quiet
uv pip install --quiet pip-licenses

DISALLOWED=$(uv run --no-sync --quiet pip-licenses --format=csv | grep -iE 'GPL|AGPL|SSPL|BUSL' | grep -v 'LGPL' || true)
if [ -n "$DISALLOWED" ]; then
  echo "ERROR: Found packages with disallowed licenses:"
  echo "$DISALLOWED"
  exit 1
fi
echo "OK: No disallowed licenses found."
