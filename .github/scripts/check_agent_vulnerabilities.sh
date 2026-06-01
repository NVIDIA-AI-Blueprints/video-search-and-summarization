#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Scan the vss-agent runtime Python dependency set for known vulnerabilities.

set -euo pipefail

repo_root=$(git rev-parse --show-toplevel 2>/dev/null || printf '%s\n' "${GITHUB_WORKSPACE:-$PWD}")
output_dir="${1:-$repo_root/vulnerability-reports/vss-agent}"
case "$output_dir" in
  /*) ;;
  *) output_dir="$repo_root/$output_dir" ;;
esac
scan_dir=$(mktemp -d)
trap 'rm -rf "$scan_dir"' EXIT

if ! command -v grype >/dev/null 2>&1; then
  echo "ERROR: grype is required for vulnerability scanning." >&2
  exit 127
fi

mkdir -p "$output_dir"
cd "$repo_root/services/agent"

uv export \
  --quiet \
  --frozen \
  --no-default-groups \
  --no-emit-project \
  --no-hashes \
  --format requirements-txt \
  --output-file "$scan_dir/requirements.txt"

grype "dir:$scan_dir" -o json --file "$output_dir/grype-report.json"
grype "dir:$scan_dir" -o table --file "$output_dir/grype-report.txt"

python3 "$repo_root/.github/scripts/check_grype_vulnerabilities.py" \
  "$output_dir/grype-report.json" \
  --service "vss-agent"
