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
"""The version a VSS deployment reports, and the SemVer contract it must meet.

Consumed by ``vss_agents.api.version`` (``GET /api/v1/version``). Standard
library only and safe to run as a file, so a deploy script on a host without
VSS installed can still ask for the derived version::

    python3 libs/vss/core/src/vss_core/version.py

One version, not several: what a deployment reports is the version of the
``nvidia-vss-core`` library the running process imported.

Resolution order, most authoritative first:

1. ``VSS_DEPLOYMENT_VERSION`` — an operator override. Nothing sets it by
   default; it exists only so a deployment carrying a wrong stamp can be
   corrected without a rebuild. A value that is set but is not strict SemVer
   yields ``None`` rather than falling through, because a stated version that
   is wrong is worth surfacing.
2. :func:`library_version` — the installed ``nvidia-vss-core`` distribution.
   The unified source.
3. :func:`describe_version` — the checkout's ``git describe``, for a run with
   no install metadata at all (``nat serve`` from a source tree). Answers with
   the source it is actually running rather than nothing.

The build stamps the packages ``<release line>+tree.<source tree sha>``: a real
release line, with the tree that produced it as build metadata. The stamp must
remain a function of the source *tree* and never of the commit, because
``build-dev-images.yml`` re-tags an existing image across commits whose tree is
identical — a commit-derived stamp would both defeat that reuse and let a
re-tagged image report the commit it was first built from. SemVer precedence
ignores build metadata, so ``3.3.0+tree.<sha>`` still satisfies a range like
``>=3.3.0,<4.0.0``.
"""

from __future__ import annotations

import importlib.metadata
import os
from pathlib import Path
import re
import subprocess
import sys

# The official Semantic Versioning 2.0.0 grammar
# (https://semver.org/#is-there-a-suggested-regular-expression-regex-to-check-a-semver-string),
# with its capture groups made non-capturing.
#
# The source of truth for the version contract. One other place validates the
# same strings and must stay byte-identical to the expression below:
# ``services/agent/scripts/check_vss_version.py`` duplicates it, because that
# script is a standalone stdlib-only copy-and-run tool (importing VSS would
# defeat its purpose); the agent's ``test_version.py`` asserts the two are
# identical so they cannot drift. The eval preflight in the ``ci-vss-oss`` repo
# (``eval/scripts/tests/vss_version.py``) carries the same expression for the
# same reason, and hard-fails a deployment reporting a bad version.
#
# Deliberately NOT the relaxed ``VERSION_PATTERN`` used by the RTVI services
# (``services/rtvi/rt-embed/src/api_models/common.py``): that one accepts
# ``03.3.0``, ``3.3.0-.`` and ``3.3.0-01``, which the eval preflight rejects.
# Reusing it would let the endpoint serve HTTP 200 for versions CI hard-fails.
SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-(?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*))*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)

#: The operator override; set to a non-empty value it wins outright.
DEPLOYMENT_VERSION_ENV_VAR = "VSS_DEPLOYMENT_VERSION"

# `git describe --long` output: <tag>-<distance>-g<sha>, plus `-dirty` when the
# working tree has uncommitted changes. The tag match and the flags are those
# the hatch-vcs configs already use (libs/vss/core/pyproject.toml and the rest),
# so a derived version and a built package's version describe the same commit.
GIT_DESCRIBE_COMMAND = ("describe", "--dirty", "--tags", "--long", "--match", "v[0-9]*")
_DESCRIBE_PATTERN = re.compile(r"^v?(?P<release>.+?)-(?P<distance>\d+)-g(?P<sha>[0-9a-f]+)(?P<dirty>-dirty)?$")

# PEP 440 development/post-release segments hatch-vcs' `no-guess-dev` scheme
# emits off a release tag: 3.2.1.post1.dev1519+gc85c4a4e8[.dirty].
_PEP440_PATTERN = re.compile(
    r"^(?P<release>\d+\.\d+\.\d+)"
    r"(?:\.post\d+)?"
    r"(?:\.dev(?P<dev>\d+))?"
    r"(?:\+(?P<local>[0-9A-Za-z.]+))?$"
)


def _run_git(repo_root: Path, *args: str) -> str | None:
    """Return trimmed stdout of a git command, or ``None`` if it cannot be run."""
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        # No git binary, not a repository, or no reachable v* tag (a shallow
        # clone, which CI and containers routinely have).
        return None
    return completed.stdout.strip() or None


def pep440_to_semver(version: str) -> str | None:
    """Translate a PEP 440 package version into strict SemVer, or ``None``.

    ``hatch-vcs`` versions this repo's packages as PEP 440, which SemVer 2.0.0
    does not accept: ``3.2.1.post1.dev1519+gc85c4a4e8`` has dot-separated
    ``.post``/``.dev`` segments where SemVer wants a single ``-`` prerelease.
    The distance becomes the prerelease ``dev.<distance>`` and the local
    segment becomes build metadata, giving ``3.2.1-dev.1519+gc85c4a4e8``.

    A version already in strict SemVer is returned unchanged.
    """
    if SEMVER_PATTERN.fullmatch(version):
        return version

    match = _PEP440_PATTERN.fullmatch(version.strip())
    if match is None:
        return None
    return _compose(
        match.group("release"),
        match.group("dev"),
        # `+g<sha>.dirty` -> `g<sha>.dirty`: both are valid SemVer build metadata.
        match.group("local"),
    )


def _compose(release: str, distance: str | None, build: str | None) -> str | None:
    """Assemble MAJOR.MINOR.PATCH, an optional ``dev.<distance>``, and metadata."""
    candidate = release
    if distance is not None and distance != "0":
        candidate += f"-dev.{distance}"
    if build:
        candidate += f"+{build}"
    return candidate if SEMVER_PATTERN.fullmatch(candidate) else None


def describe_version(repo_root: Path | None = None) -> str | None:
    """Derive this checkout's version from its git tags, or ``None``.

    ``git describe`` against the last ``v[0-9]*`` tag, rendered as SemVer:
    exactly the tag (``3.2.1``) when sitting on it with a clean tree, otherwise
    the tag with the commit distance as a prerelease and the short SHA as build
    metadata (``3.2.1-dev.1519+gc85c4a4e8``, suffixed ``.dirty`` for
    uncommitted changes).

    Note the release is the *last tag reached*, not the next one: a develop
    commit heading for 3.3.0 derives ``3.2.1-dev.N``, because 3.3.0 is not a
    fact yet. Only reached when there is no installed ``nvidia-vss-core`` to
    ask, which states the release line rather than deriving it.
    """
    root = Path(repo_root) if repo_root is not None else Path(__file__).resolve().parent
    described = _run_git(root, *GIT_DESCRIBE_COMMAND)
    if described is None:
        return None

    match = _DESCRIBE_PATTERN.fullmatch(described)
    if match is None:
        return None
    release = pep440_to_semver(match.group("release"))
    if release is None:
        return None

    build = f"g{match.group('sha')}" + (".dirty" if match.group("dirty") else "")
    if match.group("distance") == "0" and not match.group("dirty"):
        return release
    return _compose(release, match.group("distance"), build)


def library_version() -> str | None:
    """Return the installed ``nvidia-vss-core`` version as SemVer, or ``None``.

    The version of the VSS library the running process imported, which is what
    the whole deployment reports: in an image the build stamps it with the
    release line, in a checkout install ``hatch-vcs`` derives it from the tags.

    ``None`` when the package is not installed (a source tree on
    ``PYTHONPATH``) or when its metadata cannot be normalised to SemVer.
    """
    try:
        installed = importlib.metadata.version("nvidia-vss-core")
    except importlib.metadata.PackageNotFoundError:
        return None
    return pep440_to_semver(installed)


def resolve_deployment_version(repo_root: Path | None = None) -> str | None:
    """Return the version this deployment should report, or ``None``.

    Applies the order in the module docstring. An override that is set but not
    valid SemVer yields ``None`` rather than falling through to the installed
    version: an operator correcting a stamp wrongly should see the problem, not
    the value they were trying to replace.
    """
    configured = os.getenv(DEPLOYMENT_VERSION_ENV_VAR, "").strip()
    if configured:
        return configured if SEMVER_PATTERN.fullmatch(configured) else None

    installed = library_version()
    if installed is not None:
        return installed
    return describe_version(repo_root)


def main() -> int:
    """Print the resolved version; exit 1 when there is none to print."""
    resolved = resolve_deployment_version()
    if resolved is None:
        print(
            f"error: no VSS version to report: {DEPLOYMENT_VERSION_ENV_VAR} is not set to a valid "
            "Semantic Versioning 2.0.0 value, nvidia-vss-core is not installed with a usable "
            "version, and this tree has no reachable 'v*' tag to derive one from.",
            file=sys.stderr,
        )
        return 1
    print(resolved)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
