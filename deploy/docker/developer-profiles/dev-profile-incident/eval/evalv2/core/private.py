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

"""Private outputs: anything carrying ground-truth or reference text is written only where Git cannot commit it.

Ground-truth text stays local by precedent (V1 freeze, open question D7), and V2 references quote it. Every
writer of such text calls :func:`ensure_private` first. A path is private when it lies outside every Git work
tree, or when ``git check-ignore`` says the repository ignores it (``eval/eval_data/`` does). A tracked file is
never private, even under an ignored directory.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


class PrivatePathError(PermissionError):
    """A write of private text to a path Git could commit."""


def _existing_ancestor(path: Path) -> Path:
    for candidate in (path, *path.parents):
        if candidate.exists():
            return candidate if candidate.is_dir() else candidate.parent
    return Path("/")


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=30, check=False)


def is_private(path: Path | str) -> bool:
    """Whether ``path`` is outside every Git work tree or ignored by the repository it is in."""
    target = Path(path).resolve()
    cwd = _existing_ancestor(target)
    try:
        inside = _git(cwd, "rev-parse", "--is-inside-work-tree")
    except (OSError, subprocess.TimeoutExpired):
        return False  # no git to ask: never assume a path is safe
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return True  # not in any work tree, so nothing can commit it
    return _git(cwd, "check-ignore", "-q", str(target)).returncode == 0


def ensure_private(path: Path | str) -> None:
    """Raise :class:`PrivatePathError` unless :func:`is_private` holds for ``path``."""
    if not is_private(path):
        raise PrivatePathError(
            f"{path} could be committed: private ground-truth or reference text goes under the gitignored data "
            "root (eval/eval_data/ or EVALV2_DATA_ROOT) or outside the repository"
        )
