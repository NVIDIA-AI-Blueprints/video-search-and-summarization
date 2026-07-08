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
"""Repo-wide Dockerfile hygiene checks.

Guards against two bug classes that each broke every CI image build for days:

* base images pulled from internal-only registries (``nvcr.io/nvidian``) that
  public/CI builders cannot authenticate against — every pull 403s;
* version-pinned Debian security-pool URLs — Debian removes superseded ``.deb``
  files from the pool, so the pinned URL starts returning 404 on the next
  point release of the package.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]

# Directories that never contain shipped Dockerfiles.
_SKIP_PARTS = {".git", "node_modules", ".venv", "__pycache__"}

INTERNAL_REGISTRY_PATTERN = re.compile(r"nvcr\.io/nvidian(?:/|\s|$)")
PINNED_SECURITY_POOL_PATTERN = re.compile(
    r"security\.debian\.org/\S*/pool/\S+\.deb"
)


def _repo_dockerfiles() -> list[Path]:
    files = [
        path
        for path in REPO_ROOT.rglob("Dockerfile*")
        if path.is_file()
        and not path.name.endswith(".dockerignore")
        and not _SKIP_PARTS.intersection(path.relative_to(REPO_ROOT).parts)
    ]
    assert files, f"no Dockerfiles found under {REPO_ROOT} — wrong repo root?"
    return files


def _matching_lines(pattern: re.Pattern[str]) -> list[str]:
    hits = []
    for dockerfile in _repo_dockerfiles():
        for lineno, line in enumerate(
            dockerfile.read_text(errors="replace").splitlines(), start=1
        ):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if pattern.search(stripped):
                rel = dockerfile.relative_to(REPO_ROOT)
                hits.append(f"{rel}:{lineno}: {stripped}")
    return hits


class TestDockerfileHygiene:
    def test_no_internal_only_registry_images(self):
        """nvcr.io/nvidian is inaccessible to public and CI builders (403)."""
        hits = _matching_lines(INTERNAL_REGISTRY_PATTERN)
        assert not hits, (
            "Dockerfiles reference the internal-only nvcr.io/nvidian registry; "
            "use the public nvcr.io/nvidia org instead:\n" + "\n".join(hits)
        )

    def test_no_version_pinned_debian_security_pool_urls(self):
        """Pinned security-pool .deb URLs 404 once Debian supersedes the version."""
        hits = _matching_lines(PINNED_SECURITY_POOL_PATTERN)
        assert not hits, (
            "Dockerfiles download version-pinned .debs from the Debian security "
            "pool; these URLs rot when the package is superseded. Install from "
            "the security suite via apt (e.g. `apt-get download <pkg>`) "
            "instead:\n" + "\n".join(hits)
        )
