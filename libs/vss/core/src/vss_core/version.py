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

Consumed by ``vss_agents.api.version`` (``GET /api/v1/version``) and by
``vss configure check``. Standard library only and safe to run as a file, so a
deploy script on a host with VSS installed can ask for the version the same
way::

    python3 libs/vss/core/src/vss_core/version.py

One version, one source: what a deployment reports is the version of the
``nvidia-vss-core`` library the running process imported
(:func:`library_version`). Nothing outranks it and nothing stands in for it —
not an environment variable, not a ``git describe`` at runtime. A version that
can be edited at deploy time is a version nobody can trust, and a version
derived from whatever checkout the process happens to sit in is not the
version of the code that was installed.

Where that installed version comes from is git, and only git:

* ``hatch-vcs`` (every ``pyproject.toml`` in this repo) runs
  ``git describe --tags --long --match 'v[0-9]*'`` at build time and emits
  PEP 440: ``3.3.0rc0`` on the tag, ``3.3.0rc0.post1.dev20+g73f724482`` twenty
  commits later. A checkout install (``uv sync``) carries exactly that.
* An image has no ``.git``, so ``build-dev-images.yml`` computes the release
  line with the same ``git describe`` (``--abbrev=0``) and stamps the packages
  ``<release line>+tree.<source tree sha>`` through ``SETUPTOOLS_SCM_PRETEND_VERSION``.
  The release segment changes only when a ``v*`` tag is pushed and the tree
  segment names the source the image was built from, so the stamp survives
  the workflow re-tagging an identical tree onto a later commit — a
  commit-derived stamp would go stale the moment that reuse fired.

Either way the release line is the nearest ``v*`` tag reached: ``3.2.1`` until
``v3.3.0rc0`` is pushed, ``3.3.0-rc0`` until ``v3.3.0`` is. Nightly and other
tags are invisible to the derivation. SemVer precedence ignores build
metadata and the compatibility checker treats a prerelease of X.Y.Z as X.Y.Z,
so ``3.3.0-rc0+tree.<sha>`` satisfies a range like ``>=3.3.0,<4.0.0``.
"""

from __future__ import annotations

import importlib.metadata
import re
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

#: The distribution whose installed version a deployment reports.
LIBRARY_DISTRIBUTION = "nvidia-vss-core"

# The PEP 440 shapes hatch-vcs' `no-guess-dev` scheme emits off a `v*` tag, and
# the build stamp: <release>[<pre>][.postN][.devN][+<local>].
#   3.2.1                              on a release tag
#   3.3.0rc0                           on a pre-release tag (v3.3.0rc0)
#   3.3.0rc0.post1.dev20+g73f724482    twenty commits past it (+ `.dirty`)
#   3.3.0rc0+tree.4d9b2c1              an image stamp
# The pre-release letter is one of PEP 440's normalised a/b/rc; hatch-vcs never
# emits the alpha/beta/c/pre/preview spellings, so they are not accepted.
_PEP440_PATTERN = re.compile(
    r"^(?P<release>\d+\.\d+\.\d+)"
    r"(?P<pre>(?:a|b|rc)\d+)?"
    r"(?:\.post\d+)?"
    r"(?:\.dev(?P<dev>\d+))?"
    r"(?:\+(?P<local>[0-9A-Za-z.]+))?$"
)


def pep440_to_semver(version: str) -> str | None:
    """Translate a PEP 440 package version into strict SemVer, or ``None``.

    ``hatch-vcs`` versions this repo's packages as PEP 440, which SemVer 2.0.0
    does not accept: ``3.3.0rc0.post1.dev20+g73f724482`` runs the pre-release
    into the patch number and has dot-separated ``.post``/``.dev`` segments
    where SemVer wants one ``-`` pre-release. The pre-release and the commit
    distance become the pre-release identifiers ``rc0.dev.20`` and the local
    segment becomes build metadata, giving ``3.3.0-rc0.dev.20+g73f724482``.
    Precedence comes out in the right order without a comparator:
    ``3.3.0-rc0.dev.20`` < ``3.3.0-rc0`` < ``3.3.0``.

    A version already in strict SemVer is returned unchanged.
    """
    if SEMVER_PATTERN.fullmatch(version):
        return version

    match = _PEP440_PATTERN.fullmatch(version.strip())
    if match is None:
        return None
    return _compose(
        match.group("release"),
        match.group("pre"),
        match.group("dev"),
        # `+g<sha>.dirty` -> `g<sha>.dirty`: both are valid SemVer build metadata.
        match.group("local"),
    )


def _compose(release: str, pre: str | None, distance: str | None, build: str | None) -> str | None:
    """Assemble MAJOR.MINOR.PATCH, the pre-release identifiers, and metadata."""
    identifiers: list[str] = []
    if pre:
        identifiers.append(pre)
    if distance is not None and distance != "0":
        identifiers.extend(("dev", distance))
    candidate = release
    if identifiers:
        candidate += "-" + ".".join(identifiers)
    if build:
        candidate += f"+{build}"
    return candidate if SEMVER_PATTERN.fullmatch(candidate) else None


def library_version() -> str | None:
    """Return the installed ``nvidia-vss-core`` version as SemVer, or ``None``.

    The version of the VSS library the running process imported, which is what
    the whole deployment reports: in an image the build stamped it from the
    release tag and the source tree, in a checkout install ``hatch-vcs``
    derived it from the tags.

    ``None`` when the package is not installed (a source tree on
    ``PYTHONPATH``) or when its metadata cannot be normalised to SemVer.
    """
    try:
        installed = importlib.metadata.version(LIBRARY_DISTRIBUTION)
    except importlib.metadata.PackageNotFoundError:
        return None
    return pep440_to_semver(installed)


def resolve_deployment_version() -> str | None:
    """Return the version this deployment should report, or ``None``.

    The installed library's version, and nothing else — see the module
    docstring for why there is no override and no fallback.
    """
    return library_version()


def main() -> int:
    """Print the resolved version; exit 1 when there is none to print."""
    resolved = resolve_deployment_version()
    if resolved is None:
        print(
            f"error: no VSS version to report: {LIBRARY_DISTRIBUTION} is not installed with a "
            "version that normalises to Semantic Versioning 2.0.0. A checkout is versioned by "
            "`uv sync` (hatch-vcs, from the nearest v* tag); an image by the "
            "VSS_PACKAGE_VERSION build argument.",
            file=sys.stderr,
        )
        return 1
    print(resolved)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
