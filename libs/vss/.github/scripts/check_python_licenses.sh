#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(git rev-parse --show-toplevel 2>/dev/null || printf '%s\n' "${CI_PROJECT_DIR:-$PWD}")
cd "$repo_root"

uv sync --frozen --all-packages --all-extras --all-groups --quiet

# Docutils is a build-only Sphinx dependency. Its library is public-domain/BSD,
# while its aggregate package metadata also lists a GPL-licensed editor helper.
disallowed=$(
    uv run --no-sync --quiet --with pip-licenses \
        pip-licenses --format=csv \
        | grep -v '^"docutils",' \
        | grep -iE 'GPL|AGPL|SSPL|BUSL' \
        | grep -v 'LGPL' \
        || true
)

if [[ -n "$disallowed" ]]; then
    echo "ERROR: Found packages with disallowed licenses:"
    echo "$disallowed"
    exit 1
fi

echo "OK: No disallowed licenses found."
