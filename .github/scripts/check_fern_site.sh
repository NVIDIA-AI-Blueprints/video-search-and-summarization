#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Validate the Fern site in the current directory (a build_fern_site.py output
# tree). Every docs workflow runs this before it publishes, so a preview or a
# production publish never ships a tree that skipped the checks PR CI runs.
#
# Usage: [FERN_TOKEN=...] check_fern_site.sh
#   FERN_TOKEN enables fern check's redirects check.

set -euo pipefail

echo "::group::fern check"
fern check
echo "::endgroup::"

echo "::group::fern docs md check"
fern docs md check
echo "::endgroup::"

echo "::group::fern docs broken-links --strict"
fern docs broken-links --strict
echo "::endgroup::"
