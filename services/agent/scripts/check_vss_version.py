#!/usr/bin/env python3
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

"""Check ``GET /api/v1/version`` on a VSS deployment, optionally against a range.

Standard library only, so it runs against any deployment without installing
VSS. Copy it to the machine that needs it and run it on a bare ``python3``.

    python3 check_vss_version.py http://localhost:8000
    python3 check_vss_version.py http://localhost:8000 --skill path/to/SKILL.md
    python3 check_vss_version.py http://localhost:8000 --require '>=3.2.0,<4.0.0'

Exit codes (0 is the only success):

    0  compatible     the deployment reported a version, and it satisfies the
                      required range when one was given
    1  indeterminate  the deployed version could not be determined, or the
                      required range could not be read: unreachable, HTTP 404
                      (deployment predates the endpoint), HTTP 503, non-JSON,
                      wrong shape, non-SemVer version, unreadable SKILL.md,
                      absent or malformed range
    2  usage          bad command line (argparse)
    3  incompatible   the deployed version is valid but outside the required
                      range

Range grammar — comma-separated comparators, all of which must hold:

    >=3.3.0          >3.3.0          <4.0.0          <=3.4.1         ==3.3.0
    >=3.2.0,<4.0.0

Each bound is a bare ``MAJOR.MINOR.PATCH``. Comparison uses SemVer precedence
on those three numbers only: prerelease and build metadata are ignored, so a
prerelease of X.Y.Z counts as X.Y.Z. That is deliberate — Helm defaults the
deployment to ``3.3.0-65576357eb80`` and Compose to a bare ``3.3.0``, and the
same skill must behave identically on both.
"""

import argparse
import json
from pathlib import Path
import re
import sys
from urllib.error import HTTPError
from urllib.error import URLError
from urllib.request import urlopen

# Source of truth: vss_agents.api.version.SEMVER_PATTERN (the official Semantic
# Versioning 2.0.0 grammar). Duplicated rather than imported so this script
# needs nothing but Python and can be copied to any machine; the agent's
# test_version.py asserts the two expressions are identical so they cannot
# drift. The three capture groups are MAJOR, MINOR and PATCH.
SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-(?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*))*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)

# A range bound is a bare release: no prerelease, no build metadata. Bounds are
# authored by hand in skill metadata, and allowing `>=3.3.0-rc.1` would imply
# this script honours prerelease precedence, which it deliberately does not.
BOUND_PATTERN = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")

# Longest first, so `>=` is not read as `>` followed by a junk version.
COMPARATORS = (">=", "<=", "==", ">", "<")

# Skill metadata field naming the deployment range a skill supports. Read out of
# the SKILL.md front matter rather than passed in, so the value a run enforces
# cannot drift from the value the skill publishes.
REQUIREMENT_FIELD = "requires-vss"
SKILL_VERSION_FIELD = "version"

EXIT_OK = 0
EXIT_INDETERMINATE = 1
EXIT_USAGE = 2
EXIT_INCOMPATIBLE = 3

Requirement = list[tuple[str, tuple[int, int, int]]]


class IndeterminateError(RuntimeError):
    """The deployed version, or the range to check it against, is unknowable."""


class IncompatibleError(RuntimeError):
    """The deployed version is known, and outside the required range."""


def precedence(version: str) -> tuple[int, int, int]:
    """Return the MAJOR, MINOR, PATCH triple a SemVer string sorts on here.

    Prerelease and build metadata are dropped: see the module docstring for why
    ``3.3.0-65576357eb80`` must satisfy exactly what ``3.3.0`` satisfies.
    """
    match = SEMVER_PATTERN.fullmatch(version)
    if match is None:
        raise ValueError(f"{version!r} is not valid Semantic Versioning 2.0.0")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def parse_requirement(spec: str) -> Requirement:
    """Parse a comma-separated comparator range, or raise ``ValueError``."""
    clauses = [clause.strip() for clause in spec.split(",")]
    if not any(clauses):
        raise ValueError("the required range is empty")

    parsed: Requirement = []
    for clause in clauses:
        if not clause:
            raise ValueError(f"{spec!r} has an empty comparator (check for a stray comma)")
        for comparator in COMPARATORS:
            if clause.startswith(comparator):
                bound = clause[len(comparator) :].strip()
                break
        else:
            raise ValueError(
                f"{clause!r} does not start with a comparator; "
                f"expected one of {', '.join(COMPARATORS)} (for example '>={clause}')"
            )
        if BOUND_PATTERN.fullmatch(bound) is None:
            raise ValueError(f"{bound!r} in {clause!r} is not a bare MAJOR.MINOR.PATCH version")
        parsed.append((comparator, precedence(bound)))
    return parsed


def satisfies(version: str, requirement: Requirement) -> bool:
    """Return whether ``version`` satisfies every comparator in ``requirement``."""
    actual = precedence(version)
    for comparator, bound in requirement:
        if comparator == ">=" and not actual >= bound:
            return False
        if comparator == ">" and not actual > bound:
            return False
        if comparator == "<=" and not actual <= bound:
            return False
        if comparator == "<" and not actual < bound:
            return False
        if comparator == "==" and actual != bound:
            return False
    return True


def read_skill_field(skill_md: Path, field: str) -> str | None:
    """Return a scalar field from a SKILL.md YAML front matter block.

    A deliberately minimal reader: PyYAML is not importable on a bare
    ``python3``. It matches the field at any indentation, because ``version``
    and ``requires-vss`` live under ``metadata:`` in these skills but the spec
    permits them at the top level. Raises ``IndeterminateError`` when the file or its
    front matter cannot be read at all; returns ``None`` when the field is
    simply absent, which callers report rather than treating as a pass.
    """
    try:
        lines = skill_md.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise IndeterminateError(f"cannot read {skill_md}: {error}.") from error

    if not lines or lines[0].strip() != "---":
        raise IndeterminateError(f"{skill_md} does not start with a '---' YAML front matter block.")
    try:
        end = next(i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration:
        raise IndeterminateError(f"{skill_md} has an unterminated YAML front matter block.") from None

    prefix = f"{field}:"
    for line in lines[1:end]:
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if stripped.startswith(prefix):
            return stripped[len(prefix) :].strip().strip("\"'") or None
    return None


def requirement_from_skill(skill_md: Path) -> tuple[Requirement, str, str]:
    """Return the range, its raw text, and the skill's own version, from a SKILL.md."""
    raw = read_skill_field(skill_md, REQUIREMENT_FIELD)
    if raw is None:
        raise IndeterminateError(
            f"{skill_md} declares no '{REQUIREMENT_FIELD}' in its front matter, so which VSS "
            "deployments this skill supports cannot be determined. Add it under 'metadata:', "
            f'for example: {REQUIREMENT_FIELD}: ">=3.2.0,<4.0.0".'
        )
    try:
        requirement = parse_requirement(raw)
    except ValueError as error:
        raise IndeterminateError(
            f"{skill_md} declares {REQUIREMENT_FIELD}: {raw!r}, which cannot be parsed: {error}."
        ) from error
    return requirement, raw, read_skill_field(skill_md, SKILL_VERSION_FIELD) or "unknown"


def check(base_url: str, timeout: float) -> str:
    """Return the deployed version, or raise ``IndeterminateError`` explaining why not."""
    url = f"{base_url.rstrip('/')}/api/v1/version"
    try:
        with urlopen(url, timeout=timeout) as response:
            payload = json.load(response)
    except HTTPError as error:
        if error.code == 404:
            raise IndeterminateError(
                f"{url} returned 404: this deployment predates the version endpoint and cannot "
                "report a version, so its compatibility cannot be determined. Upgrade the "
                "deployment, or check the agent's own origin if the deployment ingress does not "
                "route /api to the agent."
            ) from error
        if error.code == 503:
            raise IndeterminateError(
                f"{url} returned 503: the deployment has no usable VSS_DEPLOYMENT_VERSION "
                "(or legacy VSS_AGENT_VERSION), so its version cannot be determined."
            ) from error
        raise IndeterminateError(f"{url} returned HTTP {error.code} {error.reason}.") from error
    except (URLError, TimeoutError) as error:
        raise IndeterminateError(f"{url} is not reachable: {error}.") from error
    except json.JSONDecodeError as error:
        raise IndeterminateError(f"{url} did not return JSON: {error}.") from error

    if not isinstance(payload, dict) or payload.get("service") != "vss" or not isinstance(payload.get("version"), str):
        raise IndeterminateError(
            f'{url} returned {json.dumps(payload)}, expected {{"service": "vss", "version": "<semver>"}}.'
        )

    version = str(payload["version"])
    if not SEMVER_PATTERN.fullmatch(version):
        raise IndeterminateError(f"{url} reported version {version!r}, which is not valid Semantic Versioning 2.0.0.")
    return version


def check_compatible(base_url: str, timeout: float, requirement: Requirement, raw: str, skill_version: str) -> str:
    """Return the deployed version when it satisfies ``requirement``.

    Raises ``IndeterminateError`` when the deployed version is unknowable, and
    ``IncompatibleError`` — with the message an operator needs to act on — when it is
    known and out of range.
    """
    version = check(base_url, timeout)
    if not satisfies(version, requirement):
        raise IncompatibleError(
            f"deployed VSS {version} is outside the range {raw} required by this skill "
            f"(skill version {skill_version}). Do not benchmark this deployment: the results "
            "would not be comparable. Either deploy a VSS release inside that range, or use a "
            "revision of the skill whose declared range covers the deployment. A prerelease of "
            "X.Y.Z counts as X.Y.Z, so the suffix is not what excluded it."
        )
    return version


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("base_url", help="Deployment origin, e.g. http://localhost:8000")
    parser.add_argument("--timeout", type=float, default=10.0, help="Request timeout in seconds (default: 10)")
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--skill",
        type=Path,
        help=f"Path to a SKILL.md; the range is read from its '{REQUIREMENT_FIELD}' front matter field",
    )
    source.add_argument(
        "--require",
        help="Range to check against, e.g. '>=3.2.0,<4.0.0'. Prefer --skill, which cannot drift from the metadata",
    )
    args = parser.parse_args()

    try:
        if args.skill is not None:
            requirement, raw, skill_version = requirement_from_skill(args.skill)
        elif args.require is not None:
            try:
                requirement, raw, skill_version = parse_requirement(args.require), args.require, "unknown"
            except ValueError as error:
                raise IndeterminateError(f"--require {args.require!r} cannot be parsed: {error}.") from error
        else:
            print(check(args.base_url, args.timeout))
            return EXIT_OK
        print(check_compatible(args.base_url, args.timeout, requirement, raw, skill_version))
    except IncompatibleError as error:
        print(f"error: incompatible: {error}", file=sys.stderr)
        return EXIT_INCOMPATIBLE
    except IndeterminateError as error:
        print(f"error: cannot determine compatibility: {error}", file=sys.stderr)
        return EXIT_INDETERMINATE
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
