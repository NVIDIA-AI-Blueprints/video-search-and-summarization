#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Mirrors CI Job 9 (license-check-python). Allowlist model: every runtime
# Python dep must declare an explicitly-permissive license, otherwise the
# build fails. Anything that wouldn't pass OSRB review goes red — unknown,
# proprietary, ELv2, GPL, BUSL, Commons-Clause, "Other", empty — fail-closed
# by default.
#
# Scope: only runtime deps that actually ship. Dev-only tools are excluded
# via `--no-default-groups`. We use `uv pip install` (not `uv run pip
# install`) and `uv run --no-sync` so uv does not auto-resync the dev group
# back into the venv.
#
# Override mechanism: packages whose pip metadata declares a non-allowlist
# license but which OSRB has explicitly cleared go in
# `.github/scripts/license_allowlist_overrides.txt` (one package name per
# line, `# comment` lines allowed). Every entry is a documented exception
# that the OSRB reviewer already signed off on — keep the file short.
#
# Denylist mechanism: packages whose declared license MISREPRESENTS the
# wheel's actual terms (e.g. arize-phoenix-otel declares Apache-2.0 in pip
# metadata but ships an ELv2 IP_NOTICE) go in
# `.github/scripts/license_denylist.txt`. Denylist wins over allowlist and
# overrides — anything on this list always fails. The only fix is to
# replace the dep with a permissive alternative.

set -euo pipefail

repo_root=$(git rev-parse --show-toplevel 2>/dev/null || printf '%s\n' "${GITHUB_WORKSPACE:-$PWD}")
cd "$repo_root/services/agent"

uv sync --frozen --no-default-groups --quiet
uv pip install --quiet pip-licenses

uv run --no-sync --quiet pip-licenses --format=csv \
  | python3 "$repo_root/.github/scripts/check_python_licenses.py" \
      --overrides "$repo_root/.github/scripts/license_allowlist_overrides.txt" \
      --denylist  "$repo_root/.github/scripts/license_denylist.txt"
