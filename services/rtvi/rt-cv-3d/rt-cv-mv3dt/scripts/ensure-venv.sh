#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#
# ensure-venv.sh — create the shared utils/venv and install the Python deps from
# utils/requirements.txt. Source it and call `ensure_venv`:
#
#   source "$(dirname "${BASH_SOURCE[0]}")/ensure-venv.sh"
#   ensure_venv || { echo "deps unavailable" >&2; exit 1; }
#
# Idempotent via a stamp file (re-installs only when requirements.txt changes).
# Safe to source under `set -euo pipefail` (sets no shell options). After it
# returns 0, "$VENV_PY" holds the venv's python interpreter path.

ensure_venv() {
  local root venv req stamp
  root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  venv="$root/utils/venv"
  req="$root/utils/requirements.txt"
  stamp="$venv/.requirements-installed"

  if [ ! -d "$venv" ]; then
    echo "── Creating venv at $venv"
    python3 -m venv "$venv" || { echo "   ✗ failed to create venv at $venv" >&2; return 1; }
  fi

  if [ ! -f "$stamp" ] || [ "$req" -nt "$stamp" ]; then
    echo "── Installing Python deps (utils/requirements.txt) ..."
    # `python -m pip`, not the venv's pip wrapper, whose shebang hard-codes the
    # venv path and breaks if the directory is moved/renamed.
    if "$venv/bin/python" -m pip install -q --disable-pip-version-check -r "$req"; then
      touch "$stamp"
    else
      echo "   ⚠ dependency install failed (check network / pip)" >&2
      return 1
    fi
  fi
  VENV_PY="$venv/bin/python"
  return 0
}
