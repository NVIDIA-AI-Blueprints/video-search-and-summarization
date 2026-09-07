#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Inventory third-party dependencies between two git refs for OSRB review.

This is the declaration side of the OSRB Scan: everything the repository
*declares* it depends on, read out of lockfiles and manifests. Two sibling
modules extend it — ``osrb_sources.py`` reads the shapes that declare a
dependency without naming a package (Dockerfiles, compose files, Helm charts,
CMake, pre-commit, Actions workflows) and ``osrb_usage.py`` reports imports that
no manifest declares. ``main()`` merges all three into one CSV.

Why the coverage rules below are the point of this file
------------------------------------------------------
The failure this gate exists to prevent is silence. A dependency that no parser
understands produces no row, an empty diff reads exactly like a clean PR, and
OSRB approves a release containing a package nobody reviewed. That is how a
``pdm.lock`` on RTVI-VLM once shipped unreviewed. So the scanner is split in
two halves that must be kept honest against each other:

* ``is_dependency_file()`` recognises every shape that can carry a third-party
  dependency, whether or not we can read it;
* ``is_parsed()`` admits which of those we actually inventory.

Anything a PR ADDS that is in the first set and not the second becomes a
``UNCOVERED_SOURCE`` row and fails the job. Widening the first set without
widening the second is therefore safe (it gets loud); widening the second
without a real parser is the dangerous direction.

Runtime-only rule
-----------------
Every parser here inventories what SHIPS. Dev/test groups (``dev-dependencies``,
Pipfile ``develop``, PDM/Poetry ``dev`` groups, npm ``devDependencies``, Maven
``test`` scope, Gradle ``test*`` configurations, gemspec
``add_development_dependency``) are deliberately omitted: a linter never reaches
a release artifact, and expanding the OSRB review with them buries the packages
that do.

Diffing rule
------------
Lockfiles pin resolved versions, so they are diffed by (name, version).
Manifests carry ranges, which would re-resolve as upstream publishes and
fabricate "changes" nobody made, so they are diffed by the literal committed
spec — deterministic, driven only by the file contents. Names already covered
by a lockfile are dropped from the manifest pass so one package cannot produce
two rows.

CSV columns: the nine original columns (language, package, change, old_version,
new_version, old_license, new_license, repository_url, notes) followed by four
appended ones (source_kind, source_file, module, risk). The order and the
original names are a published surface — the private GitLab OSRB pipeline reads
this CSV out of the `license-diff` artifact by column name. Append only.

Usage:
    python osrb_scan.py --base-ref origin/develop --output license-diff.csv
"""

from __future__ import annotations

import argparse
import ast
import configparser
import csv
import importlib.util
import json
import os
import re
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from typing import Callable

PYPI_TIMEOUT = 10
PYPI_INDEX = "https://pypi.org/pypi"

PackageKey = tuple[str, str]
Inventory = dict[PackageKey, dict[str, str]]


class UnparseableManifest(Exception):
    """A recognised dependency file whose contents we refuse to guess at.

    Raised instead of returning a partial inventory, because a partial
    inventory is indistinguishable from a complete one downstream. The caller
    turns this into an ``UNCOVERED_SOURCE`` row so the gap is visible in the
    CSV rather than being absorbed as an empty result.
    """


def _log(msg: str) -> None:
    print(f"[osrb-scan] {msg}", file=sys.stderr)


def _annotate(title: str, message: str, *, level: str = "warning") -> None:
    """Emit a GitHub Actions annotation as well as a stderr line.

    Job logs are not read unless something is already suspected; annotations
    show up on the PR. A coverage gap that only ever reached stderr is a
    coverage gap nobody sees.
    """
    _log(f"{level.upper()}: {message}")
    print(f"::{level} title={title}::{message}", file=sys.stderr)


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True)


def _git_show(ref: str, path: str) -> bytes | None:
    try:
        return subprocess.check_output(
            ["git", "show", f"{ref}:{path}"], stderr=subprocess.DEVNULL
        )
    except subprocess.CalledProcessError:
        return None


_ls_tree_cache: dict[str, list[str]] = {}


def _ls_tree(ref: str) -> list[str]:
    """List every tracked path at `ref`, cached.

    main() needs the full tree at both refs many times over (one pass per
    ecosystem plus the coverage check). Without the cache that is dozens of
    `git ls-tree -r` invocations over an 8k-path tree for no new information.
    """
    if ref in _ls_tree_cache:
        return _ls_tree_cache[ref]
    try:
        out = _git("ls-tree", "-r", "--name-only", ref).splitlines()
    except subprocess.CalledProcessError:
        out = []
    _ls_tree_cache[ref] = out
    return out


def _paths_of(ref_or_paths: str | list[str]) -> list[str]:
    """Accept either a git ref or an already-listed path set.

    The coverage helpers are called from main() with refs and from tests with
    literal path lists; requiring a git repository to unit-test a pure
    filename rule is how filename rules stop being tested.
    """
    if isinstance(ref_or_paths, str):
        return _ls_tree(ref_or_paths)
    return list(ref_or_paths)


def _list_lockfiles(ref: str, filename: str) -> list[str]:
    return [
        p
        for p in _ls_tree(ref)
        if p.endswith("/" + filename) or p == filename
        if "node_modules/" not in p
    ]


def _basename(path: str) -> str:
    return path.rsplit("/", 1)[-1]


# ---------------------------------------------------------------------------
# Risk model — the single definition, imported by osrb_sources / osrb_usage
# ---------------------------------------------------------------------------

RISK_NONE = "None"
RISK_MEDIUM = "Medium"
RISK_HIGH = "High"
RISK_UNKNOWN = "Unknown"

# Strong/network copyleft, source-available, and proprietary terms. Shipping
# one of these in a binary blueprint is a legal decision, not an engineering
# one, which is why they are called out separately from the weak-copyleft set.
_HIGH_RISK_PATTERNS = [
    r"(?:^|-)agpl",
    r"(?:^|-)sspl",
    r"(?:^|-)elastic(?:-|$)",
    r"(?:^|-)busl",
    r"business-source",
    r"commons-clause",
    r"(?:^|-)rsal",
    r"confluent-community",
    r"nvidia",  # "NVIDIA Software License" and friends are proprietary
    r"proprietary",
    r"non-commercial",
    r"noncommercial",
    r"cc-by-nc",
    r"research-only",
    r"research-use",
    # Checked after LGPL below only in the sense that "(?:^|-)" cannot match
    # the "gpl" inside "lgpl" — the leading "l" is neither start-of-token nor
    # a separator. AGPL is caught by its own pattern above and here.
    r"(?:^|-)gpl",
]

# Weak / file-level copyleft: obligations attach to the modified files, not to
# the whole distribution. Reviewable, but not silently.
_MEDIUM_RISK_PATTERNS = [
    r"(?:^|-)lgpl",
    r"(?:^|-)mpl(?:-|$)",
    r"mozilla-public",
    r"(?:^|-)epl(?:-|$)",
    r"eclipse-public",
    r"(?:^|-)cddl",
    r"(?:^|-)cpl(?:-|$)",
    r"(?:^|-)osl(?:-|$)",
    r"open-software-license",
    r"(?:^|-)afl(?:-|$)",
    r"academic-free",
    r"(?:^|-)ms-pl(?:-|$)",
    r"microsoft-public",
    r"(?:^|-)freetype",
    r"(?:^|-)artistic",
]

# Permissive: attribution-only obligations, pre-cleared for the blueprint.
_NONE_RISK_PATTERNS = [
    r"(?:^|-)mit(?:-|$)",
    r"(?:^|-)bsd",
    r"(?:^|-)0bsd(?:-|$)",
    r"(?:^|-)apache",
    r"(?:^|-)isc(?:-|$)",
    r"(?:^|-)unlicense(?:-|$)",
    r"(?:^|-)cc0",
    r"(?:^|-)psf(?:-|$)",
    r"python-software-foundation",
    r"(?:^|-)zlib",
    r"(?:^|-)bsl-1",
    r"(?:^|-)boost(?:-|$)",
    r"public-domain",
]

_HIGH_RISK_RE = re.compile("|".join(_HIGH_RISK_PATTERNS))
_MEDIUM_RISK_RE = re.compile("|".join(_MEDIUM_RISK_PATTERNS))
_NONE_RISK_RE = re.compile("|".join(_NONE_RISK_PATTERNS))

# Only whitespace-delimited boolean keywords split an expression. Splitting on
# a bare "or" would tear "GPL-2.0-or-later" in half; splitting on "/" and ","
# is required because npm and PyPI both emit "MIT/Apache-2.0" style fields.
_LICENSE_SPLIT_RE = re.compile(r"\s+(?:AND|OR|WITH)\s+|[,;/()\[\]]+", re.IGNORECASE)


def _normalize_license_token(token: str) -> str:
    """Fold one operand to lowercase hyphen-separated form.

    "Apache License 2.0", "apache-2.0" and "Apache_2.0" are the same license
    written three ways; matching each spelling separately is how a High-risk
    license gets classified Unknown and slips through as noise.
    """
    return re.sub(r"[^a-z0-9]+", "-", token.strip().lower()).strip("-")


def _token_risk(token: str) -> str:
    if _HIGH_RISK_RE.search(token):
        return RISK_HIGH
    if _MEDIUM_RISK_RE.search(token):
        return RISK_MEDIUM
    if _NONE_RISK_RE.search(token):
        return RISK_NONE
    return RISK_UNKNOWN


def license_risk(expr: str) -> str:
    """Classify a license expression as None / Medium / High / Unknown.

    Composite expressions resolve to their worst operand: any High operand
    makes the whole expression High, then any Medium makes it Medium. That is
    deliberately conservative — "MIT AND GPL-2.0-or-later" is High, because a
    reviewer who sees "MIT" first and stops reading has just approved GPL.
    An unrecognised operand keeps the result Unknown rather than letting the
    recognised permissive half of the expression vouch for the whole thing.
    """
    if not expr or not expr.strip():
        return RISK_UNKNOWN
    verdicts = [
        _token_risk(normalized)
        for normalized in (
            _normalize_license_token(part) for part in _LICENSE_SPLIT_RE.split(expr)
        )
        if normalized
    ]
    if not verdicts:
        return RISK_UNKNOWN
    if RISK_HIGH in verdicts:
        return RISK_HIGH
    if RISK_MEDIUM in verdicts:
        return RISK_MEDIUM
    if RISK_UNKNOWN in verdicts:
        return RISK_UNKNOWN
    return RISK_NONE


# ---------------------------------------------------------------------------
# Module ownership — the single definition, imported by the sibling modules
# ---------------------------------------------------------------------------

ROOT_MODULE = "<root>"

# Longest first: services/rtvi/rt-cv-3d must win over services/rtvi, which must
# win over services. Getting this order wrong assigns every RTVI dependency to
# one bucket and destroys the per-component ownership OSRB reviews against.
_MODULE_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("services", "rtvi", "rt-cv-3d"),
    ("services", "rtvi"),
    ("services", "analytics"),
    ("services", "configurators"),
    ("libs", "analytics"),
    ("services",),
    ("libs",),
    ("tools",),
    ("skills",),
)

# Trees owned end-to-end by one team; the nesting under them is layout, not
# ownership, so it must not be split into per-directory modules.
_WHOLE_TREE_ROOTS = {"deploy", ".github"}


def owning_module(path: str) -> str:
    """Return the component that owns `path`, for OSRB routing.

    OSRB approval is granted per component, so this string decides who gets
    asked about a dependency. A file sitting directly inside a container
    directory belongs to that container, not to a phantom module named after
    the file — `services/rtvi/rt-cv-3d/README.md` is owned by
    `services/rtvi/rt-cv-3d`, not by `services/rtvi/rt-cv-3d/README.md`.
    """
    parts = [part for part in path.strip("/").split("/") if part and part != "."]
    if len(parts) <= 1:
        return ROOT_MODULE
    if parts[0] in _WHOLE_TREE_ROOTS:
        return parts[0]
    for prefix in _MODULE_PREFIXES:
        depth = len(prefix)
        if tuple(parts[:depth]) != prefix:
            continue
        if len(parts) > depth + 1:
            return "/".join((*prefix, parts[depth]))
        return "/".join(prefix)
    return parts[0]


# ---------------------------------------------------------------------------
# CSV shape — the published surface
# ---------------------------------------------------------------------------

# DO NOT reorder or rename. The private GitLab OSRB pipeline reads this CSV out
# of the `license-diff` artifact by column name, and that consumer is not
# visible from this repository. New columns append after `notes`.
HEADERS = [
    "language",
    "package",
    "change",
    "old_version",
    "new_version",
    "old_license",
    "new_license",
    "repository_url",
    "notes",
]

APPENDED_FIELDS = ["source_kind", "source_file", "module", "risk"]

ROW_FIELDS = HEADERS + APPENDED_FIELDS

CHANGE_ADDED = "added"
CHANGE_REMOVED = "removed"
CHANGE_UPDATED = "updated"
CHANGE_UNCOVERED_SOURCE = "UNCOVERED_SOURCE"
CHANGE_USED_UNDECLARED = "USED_UNDECLARED"

KIND_LOCKFILE = "lockfile"
KIND_MANIFEST = "manifest"
KIND_CONTAINER = "container"
KIND_COMPOSE = "compose"
KIND_CHART = "chart"
KIND_BUILD = "build"
KIND_CI = "ci"
KIND_ATTRIBUTION = "attribution"
KIND_USAGE = "usage"

SOURCE_KINDS = (
    KIND_LOCKFILE,
    KIND_MANIFEST,
    KIND_CONTAINER,
    KIND_COMPOSE,
    KIND_CHART,
    KIND_BUILD,
    KIND_CI,
    KIND_ATTRIBUTION,
    KIND_USAGE,
)


def make_row(**values: str) -> dict[str, str]:
    """Build one CSV row, defaulting every column and rejecting unknown keys.

    Three modules write into the same CSV. A typo'd column name in any of them
    would otherwise be dropped by ``csv.DictWriter`` (or, worse, raise only for
    the PRs that happen to hit that code path), so the key check happens here,
    once, at construction time.

    ``module`` and ``risk`` are derived when not passed explicitly, because
    they are pure functions of columns the caller already supplies. Leaving
    them to each caller is how half the rows end up with an empty risk column
    and the OSRB reviewer starts ignoring it.
    """
    unknown = sorted(set(values) - set(ROW_FIELDS))
    if unknown:
        raise ValueError(
            f"unknown OSRB CSV column(s): {', '.join(unknown)}; "
            f"allowed: {', '.join(ROW_FIELDS)}"
        )
    row = {field: "" for field in ROW_FIELDS}
    for key, value in values.items():
        row[key] = "" if value is None else str(value)
    if not row["module"] and row["source_file"]:
        # source_file may carry a "#L12" evidence suffix; ownership is a
        # property of the path, not of the line.
        row["module"] = owning_module(row["source_file"].split("#", 1)[0])
    if not row["risk"]:
        # The license we are moving TO decides the risk; a removal has only
        # an old license, and that is the one that mattered.
        row["risk"] = license_risk(row["new_license"] or row["old_license"])
    return row


def normalize_row(row: dict[str, str]) -> dict[str, str]:
    """Coerce a row from any module into the exact CSV shape.

    ``osrb_sources`` and ``osrb_usage`` are separate files that can be edited
    independently. An extra key from one of them must not abort the run that
    is protecting every other PR in the repo, so extras are logged and dropped
    rather than raised.
    """
    extras = sorted(set(row) - set(ROW_FIELDS))
    if extras:
        _log(
            f"WARNING: dropping unknown column(s) {', '.join(extras)} from row "
            f"{row.get('package', '')!r}"
        )
    return make_row(**{key: value for key, value in row.items() if key in ROW_FIELDS})


# ---------------------------------------------------------------------------
# Coverage classification — what carries dependencies, and what we can read
# ---------------------------------------------------------------------------

_LOCKFILE_BASENAMES = {
    "uv.lock",
    "pdm.lock",
    "poetry.lock",
    "pipfile.lock",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "cargo.lock",
    "go.sum",
    "gemfile.lock",
    "composer.lock",
}

_MANIFEST_BASENAMES = {
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "pipfile",
    "package.json",
    "go.mod",
    "cargo.toml",
    "pom.xml",
    "gemfile",
    "vcpkg.json",
    "environment.yml",
    "environment.yaml",
    "module.bazel",
    "workspace",
}

_CHART_BASENAMES = {"chart.yaml", "chart.lock"}

_PRECOMMIT_BASENAMES = {".pre-commit-config.yaml", ".pre-commit-config.yml"}

_WORKFLOW_DIR = ".github/workflows/"

# Any YAML whose name contains "compose" pins container images by tag. The
# obvious `^(?:docker-)?compose.*\.ya?ml$` is too narrow for this repo, which
# also ships `rtvi-vlm-docker-compose.yml`, `deploy_docker-compose.yml`,
# `test_docker-compose.yml` and `logstash-compose.yml` — five real compose
# files that an anchored pattern classifies as "not a dependency file" and
# therefore never mentions again.
_COMPOSE_RE = re.compile(r"^[^/]*compose[^/]*\.ya?ml$")


def _is_dockerfile(base_low: str) -> bool:
    """True for Dockerfile, Dockerfile.<variant> and <variant>.Dockerfile.

    `Dockerfile.dockerignore` matches the `Dockerfile.*` shape but declares
    nothing — treating it as a container file would put six permanent
    zero-dependency entries into the scan for no reason.
    """
    if base_low.endswith(".dockerignore"):
        return False
    return (
        base_low == "dockerfile"
        or base_low.startswith("dockerfile.")
        or base_low.endswith(".dockerfile")
    )


def _is_attribution(base_low: str) -> bool:
    """True for the third-party attribution files OSRB itself produces.

    Matched on basename only. `services/vios/include/3rdparty/aws/auth/auth.h`
    lives under a `3rdparty` directory but is vendored source, not an
    attribution record; a path-substring rule would classify hundreds of
    headers as dependency declarations.

    The repository's own `LICENSE` is excluded for the same reason — it is our
    license, not a third party's.
    """
    if base_low == "license.3rdparty":
        return True
    if base_low.startswith("license-3rd-party") and base_low.endswith(".txt"):
        return True
    if base_low.startswith("3rdparty_licenses"):
        return True
    if base_low.startswith("third_party_licenses"):
        return True
    if base_low.startswith("notice"):
        return True
    return base_low == "oss-licenses.txt"


def is_dependency_file(path: str) -> str | None:
    """Return the `source_kind` of a dependency-bearing file, else None.

    Recognition is deliberately wider than parsing. Every shape that can pull
    a third party into a release artifact is named here even when no parser
    exists for it, because a recognised-but-unparsed file fails the job loudly
    (see `is_parsed`) whereas an unrecognised one is invisible. Widening this
    function can only make the gate noisier, never quieter.

    `node_modules/` is excluded throughout: it is installed output, already
    covered by the lockfile that produced it, and large enough to bury the
    real rows.
    """
    if "node_modules/" in path:
        return None
    base_low = _basename(path).lower()

    # Path-anchored first: a workflow named compose.yml is still a workflow.
    if path.startswith(_WORKFLOW_DIR) and base_low.endswith((".yml", ".yaml")):
        return KIND_CI
    if base_low in _PRECOMMIT_BASENAMES:
        return KIND_CI
    if base_low in _CHART_BASENAMES:
        return KIND_CHART
    if _COMPOSE_RE.match(base_low):
        return KIND_COMPOSE
    if _is_dockerfile(base_low):
        return KIND_CONTAINER
    if base_low == "cmakelists.txt" or base_low.endswith(".cmake"):
        return KIND_BUILD
    if base_low in _LOCKFILE_BASENAMES:
        return KIND_LOCKFILE
    if base_low in _MANIFEST_BASENAMES:
        return KIND_MANIFEST
    if base_low.startswith("requirements") and base_low.endswith(".txt"):
        return KIND_MANIFEST
    if base_low.endswith(".gemspec"):
        return KIND_MANIFEST
    if base_low.startswith("conanfile."):
        return KIND_MANIFEST
    if base_low.endswith((".gradle", ".gradle.kts")):
        return KIND_MANIFEST
    if _is_attribution(base_low):
        return KIND_ATTRIBUTION
    return None


# Lockfiles with a parser in this file. `composer.lock` is recognised but
# absent: no PHP ships today, so a PHP lock appearing is exactly the event that
# should stop a PR and get a parser written, not be waved through.
_PARSED_LOCK_BASENAMES = {
    "uv.lock",
    "pdm.lock",
    "poetry.lock",
    "pipfile.lock",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "cargo.lock",
    "go.sum",
    "gemfile.lock",
}

# Manifests with a parser in this file. `MODULE.bazel` and `WORKSPACE` are
# recognised but absent for the same reason as composer.lock.
_PARSED_MANIFEST_BASENAMES = {
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "pipfile",
    "package.json",
    "go.mod",
    "cargo.toml",
    "pom.xml",
    "vcpkg.json",
    "environment.yml",
    "environment.yaml",
}

# Kinds inventoried by osrb_sources.py (agent B). Chart.lock rides on this:
# it only pins digests for the `dependencies:` list that Chart.yaml already
# declares and parse_helm_chart already reads, so a Chart.lock with no new
# information must not fail a PR. If Chart.yaml ever stops being parsed, this
# exemption has to go with it.
_KINDS_PARSED_BY_SOURCES = {
    KIND_CONTAINER,
    KIND_COMPOSE,
    KIND_CHART,
    KIND_BUILD,
    KIND_CI,
}


def is_parsed(path: str) -> bool:
    """True when this scanner actually inventories `path`'s dependencies.

    This is the honesty half of the coverage pair. It must describe what the
    parsers do, not what we wish they did: marking a file parsed without a
    parser behind it converts a loud failure into the silent one this whole
    module exists to prevent. Attribution files are the clearest case — they
    list third parties in prose, nothing reads them, so they stay False and a
    newly added one demands a human look.
    """
    kind = is_dependency_file(path)
    if kind is None:
        return False
    if kind in _KINDS_PARSED_BY_SOURCES:
        return True
    base_low = _basename(path).lower()
    if kind == KIND_LOCKFILE:
        return base_low in _PARSED_LOCK_BASENAMES
    if kind == KIND_MANIFEST:
        if base_low in _PARSED_MANIFEST_BASENAMES:
            return True
        if base_low.startswith("requirements") and base_low.endswith(".txt"):
            return True
        if base_low.endswith((".gemspec", ".gradle", ".gradle.kts")):
            return True
        return base_low.startswith("conanfile.")
    return False


def uncovered_dependency_files(
    base: str | list[str], head: str | list[str]
) -> list[str]:
    """Return files ADDED by this PR that carry dependencies we cannot read.

    Only additions. A dependency file that already existed on the base branch
    is a pre-existing gap: failing every PR in the repo for it would train
    everyone to ignore this check, which costs more than the gap does. New
    ones are stopped at the door.
    """
    base_paths = set(_paths_of(base))
    return sorted(
        path
        for path in set(_paths_of(head)) - base_paths
        if is_dependency_file(path) is not None and not is_parsed(path)
    )


_UNCOVERED_NOTE = (
    "dependency-bearing file added in this PR that osrb_scan.py cannot parse; "
    "its third-party dependencies were never inventoried"
)

_ATTRIBUTION_NOTE = (
    "third-party attribution file added in this PR. Advisory, not blocking: an "
    "attribution file is the OUTPUT of the licence process, not a dependency "
    "declaration, so there is nothing for a parser to resolve and nothing an "
    "OSRB approval can clear. Confirm by hand that it matches the resolved "
    "dependency set for its component"
)


def blocks_merge(row: dict[str, str]) -> bool:
    """True when an UNCOVERED_SOURCE row should fail the job.

    Attribution files are excluded. They are recognised as dependency-bearing
    (they do list third parties) but nothing parses prose, so they can never
    become "covered" — the remedy the failure message gives, extend
    `is_dependency_file`, does not apply to them. Failing on one would mean
    that adding a LICENSE.3rdparty, the thing the licence process asks
    contributors to do, blocks their PR with advice they cannot act on.

    Be clear about what this carve-out does NOT give you. It is not a claim
    that attribution changes are safe:

    * Only ADDED files reach here at all. A MODIFIED attribution file — a
      removed notice, a silently changed licence — produces no row either way,
      before this change or after it.
    * CODEOWNERS is not a backstop for most of them. It routes
      `LICENSE-3rd-party.txt` to VSS_OSRB_Approvers, which covers 6 of the 33
      attribution files in the tree; `LICENSE.3rdparty`, `3rdParty_Licenses*`
      and `NOTICE*` — the other 27 — are not routed anywhere.

    So this is not a regression (nothing parsed prose before either), but it is
    a real gap, and the honest fix is to widen CODEOWNERS rather than to fail
    the scan on a file no parser can read. Tracked in .github/osrb/MIGRATION.md.

    Everything else stays blocking. That is the point of the change: a manifest
    the scanner cannot read must stop the PR, because the alternative is the
    silent miss this module exists to prevent.
    """
    if row.get("change", "").strip().lower() != CHANGE_UNCOVERED_SOURCE.lower():
        return False
    return row.get("source_kind", "") != KIND_ATTRIBUTION


def uncovered_source_rows(
    paths: list[str], *, reason: str = _UNCOVERED_NOTE
) -> list[dict[str, str]]:
    """Turn coverage gaps into blocking CSV rows.

    The row carries the path rather than a package name because there is no
    package name — that is the whole complaint.
    """
    rows = []
    for path in paths:
        kind = is_dependency_file(path) or KIND_MANIFEST
        rows.append(
            make_row(
                package=_basename(path),
                change=CHANGE_UNCOVERED_SOURCE,
                source_kind=kind,
                source_file=path,
                # An attribution file gets its own wording, because telling the
                # author to "extend is_dependency_file" for a LICENSE.3rdparty
                # is advice that cannot be followed.
                notes=_ATTRIBUTION_NOTE if kind == KIND_ATTRIBUTION else reason,
                risk=RISK_UNKNOWN,
            )
        )
    return rows


# ---------------------------------------------------------------------------
# Python lockfile parsers
# ---------------------------------------------------------------------------


def parse_uv_lock(data: bytes) -> Inventory:
    """Return the runtime dependency closure from a ``uv.lock`` file.

    A uv lock records every resolved dependency group.  In particular, the
    root editable package's ``package.dev-dependencies`` contains linters and
    test runners, which do not ship in a release artifact and must not expand
    the OSRB review.  Start from local project packages and follow their
    regular ``dependencies`` plus every entry of their ``optional-dependencies``
    (a root project's extras, e.g. the agent stack behind ``nvidia-vss[agent]``, ship
    in release artifacts); deliberately do not follow ``dev-dependencies``.
    Third-party packages only contribute the extras that a runtime dependency
    actually requests.
    """
    doc = tomllib.loads(data.decode("utf-8"))
    packages = doc.get("package", []) or []
    packages_by_name: dict[str, list[int]] = {}
    roots: list[int] = []

    for index, pkg in enumerate(packages):
        name = (pkg.get("name") or "").lower()
        if not name:
            continue
        # uv can fork a package by platform, source, or Python version.  Keep
        # every entry and use a dependency's optional version/source metadata
        # to select the correct fork below.
        packages_by_name.setdefault(name, []).append(index)
        source = pkg.get("source") or {}
        if "editable" in source or "virtual" in source:
            roots.append(index)

    # A lockfile produced for a project always has an editable/virtual root.
    # Keep a conservative fallback for malformed or third-party lockfiles so
    # an unexpected format cannot silently omit a package from OSRB review.
    root_names = set(roots)
    if not roots:
        roots = list(range(len(packages)))

    runtime_package_indexes: set[int] = set()
    expanded_extras: dict[int, set[str]] = {}
    pending: list[tuple[int, set[str]]] = [
        (index, set((packages[index].get("optional-dependencies") or {}).keys()))
        for index in roots
    ]

    def add_dependency(dependency: dict) -> None:
        """Queue every lock entry selected by one dependency declaration."""
        dependency_name = (dependency.get("name") or "").lower()
        if not dependency_name:
            return
        dependency_version = str(dependency.get("version") or "")
        dependency_source = dependency.get("source") or {}
        dependency_extras = set(dependency.get("extra") or [])
        for dependency_index in packages_by_name.get(dependency_name, []):
            candidate = packages[dependency_index]
            candidate_source = candidate.get("source") or {}
            if dependency_version and candidate.get("version") != dependency_version:
                continue
            if dependency_source and candidate_source != dependency_source:
                continue
            pending.append((dependency_index, dependency_extras))

    while pending:
        index, requested_extras = pending.pop()
        previous_extras = expanded_extras.get(index, set())
        new_extras = requested_extras - previous_extras
        first_visit = index not in runtime_package_indexes
        if not first_visit and not new_extras:
            continue
        runtime_package_indexes.add(index)
        expanded_extras[index] = previous_extras | requested_extras
        pkg = packages[index]
        if first_visit:
            for dependency in pkg.get("dependencies", []) or []:
                add_dependency(dependency)
        optional_dependencies = pkg.get("optional-dependencies") or {}
        for extra in new_extras:
            for dependency in optional_dependencies.get(extra, []) or []:
                add_dependency(dependency)

    out: Inventory = {}
    for index in sorted(runtime_package_indexes):
        # The editable/virtual root is this repository's own project, not a
        # third-party package subject to OSRB review.
        if index in root_names:
            continue
        pkg = packages[index]
        name = (pkg.get("name") or "").lower()
        version = str(pkg.get("version") or "")
        if not name:
            continue
        source = pkg.get("source") or {}
        # Only direct sources (git/url) point at the actual upstream. The
        # `registry` source just points at PyPI's simple index, which is not a
        # useful repository URL — leave empty and let PyPI metadata fill it.
        repo = source.get("git") or source.get("url") or ""
        out[(name, version)] = {"repository_url": str(repo)}
    return out


def parse_pipfile_lock(data: bytes) -> Inventory:
    """Return {(name, version): {repository_url}} parsed from Pipfile.lock.

    Pipfile.lock is JSON with `default` (runtime) and `develop` (dev-only)
    sections; each maps a package name to `{"version": "==X.Y.Z", ...}`. Only
    `default` is inventoried — those are the packages that actually ship, which
    is what OSRB reviews (dev-only tools like linters never reach a release
    artifact). Versions are pinned as `==X.Y.Z`; strip the `==`. No license or
    repository_url is embedded in the lock, so (like uv.lock registry packages)
    those fields are left empty and filled from PyPI metadata downstream.
    """
    doc = json.loads(data.decode("utf-8"))
    out: Inventory = {}
    for name, meta in (doc.get("default") or {}).items():
        lname = (name or "").lower()
        version = str((meta or {}).get("version") or "").lstrip("=").strip()
        if not lname or not version:
            continue
        out[(lname, version)] = {"repository_url": ""}
    return out


_RUNTIME_LOCK_GROUPS = {"default", "main"}


def parse_pdm_lock(data: bytes) -> Inventory:
    """Return {(name, version): {repository_url}} parsed from pdm.lock.

    PDM records every resolved package under ``[[package]]`` with a ``groups``
    list. Only ``default`` / ``main`` groups ship in a release artifact, so
    ``dev`` and other extra groups are omitted — the same policy as
    Pipfile.lock ``default`` and uv.lock's skip of ``dev-dependencies``.
    Packages that omit ``groups`` are kept so an unexpected lock format cannot
    silently drop a runtime dependency from OSRB review.
    """
    doc = tomllib.loads(data.decode("utf-8"))
    out: Inventory = {}
    for pkg in doc.get("package", []) or []:
        groups = {str(group).lower() for group in (pkg.get("groups") or [])}
        if groups and groups.isdisjoint(_RUNTIME_LOCK_GROUPS):
            continue
        name = (pkg.get("name") or "").lower()
        version = str(pkg.get("version") or "")
        if not name or not version:
            continue
        out[(name, version)] = {"repository_url": ""}
    return out


def parse_poetry_lock(data: bytes) -> Inventory:
    """Return {(name, version): {repository_url}} parsed from poetry.lock.

    Poetry 2 records ``groups``; Poetry 1 used ``category``. Only ``main`` /
    ``default`` runtime membership is inventoried. A package present in both
    ``main`` and ``dev`` is kept; a ``dev``-only package is omitted.
    """
    doc = tomllib.loads(data.decode("utf-8"))
    out: Inventory = {}
    for pkg in doc.get("package", []) or []:
        category = str(pkg.get("category") or "").lower()
        if category and category not in _RUNTIME_LOCK_GROUPS:
            continue
        groups = {str(group).lower() for group in (pkg.get("groups") or [])}
        if groups and groups.isdisjoint(_RUNTIME_LOCK_GROUPS):
            continue
        name = (pkg.get("name") or "").lower()
        version = str(pkg.get("version") or "")
        if not name or not version:
            continue
        out[(name, version)] = {"repository_url": ""}
    return out


_REQ_NAME_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)")
_EXACT_VERSION_RE = re.compile(r"^\d+(?:\.\d+)*(?:[a-zA-Z0-9._+-]*)?$")

# A remote install is identified by a URL scheme or a VCS prefix, never by the
# first four characters of the requirement. See parse_requirements.
_VCS_PREFIXES = ("git+", "hg+", "svn+", "bzr+", "file:")


def _is_remote_requirement(line: str) -> bool:
    """True for a URL / VCS install, which has no PyPI name to resolve.

    Gating on ``line.startswith("http")`` is what this replaces, and it was
    silently dropping every package whose NAME begins with those letters:
    httpx, httpcore, httplib2, http-parser. Those are ordinary PyPI packages —
    `httpx>=0.27.0` is a direct dependency of services/alert today — and
    dropping them removed them from OSRB review entirely, in both the base and
    head inventories, so the diff stayed empty and nothing looked wrong.
    A URL install always contains "://"; a VCS install always carries an
    explicit scheme prefix. Match on those instead.
    """
    return "://" in line or line.lower().startswith(_VCS_PREFIXES)


def parse_requirements(data: bytes) -> dict[str, str]:
    """Return {canonical_name: pinned_version_or_''} from a requirements.txt.

    requirements.txt is NOT a lockfile — it lists direct deps, usually with
    version ranges and no transitive closure — so it cannot be diffed by
    resolved version the way uv.lock / Pipfile.lock are (a `>=` floor would
    re-resolve as PyPI moves, flagging upstream releases as PR changes). This
    parser extracts only what is deterministic from the committed file: the set
    of direct package NAMES, plus an exact version when (and only when) the line
    is `==`-pinned. Everything else maps to an empty version, meaning
    "unpinned — license looked up against latest at report time".

    Skips non-dependency lines: blanks, comments, option flags (`-r`, `-e`,
    `-c`, `--hash`, etc.), and VCS/URL installs (no PyPI name to resolve).
    """
    out: dict[str, str] = {}
    for raw in data.decode("utf-8", errors="replace").splitlines():
        line = raw.split(" #", 1)[0].strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        if _is_remote_requirement(line):
            continue
        # Strip environment markers and inline hashes.
        line = line.split(";", 1)[0].split(" --hash", 1)[0].strip()
        m = _REQ_NAME_RE.match(line)
        if not m:
            continue
        name = m.group(1).lower()
        rest = line[m.end():].lstrip()
        # Skip an optional extras group: name[extra1,extra2]
        if rest.startswith("["):
            rest = rest.split("]", 1)[-1].lstrip() if "]" in rest else ""
        version = ""
        if rest.startswith("=="):
            version = rest[2:].strip().rstrip(",").split(",")[0].strip()
        out[name] = version
    return out


def requirements_inventory(
    ref: str, *, sources: dict[str, str] | None = None
) -> dict[str, str]:
    """Merge every requirements*.txt at `ref` into {name: pinned_version_or_''}.

    `requirements_apt.txt` (system/apt packages, not PyPI) is excluded.

    `sources` is an optional out-parameter that records which file each name
    came from. The merge intentionally collapses the repo into one namespace
    for diffing, which throws away provenance; the CSV needs it back to fill
    the `source_file` and `module` columns, and re-walking the tree a second
    time to recover it would double the git calls.
    """
    merged: dict[str, str] = {}
    for path in _ls_tree(ref):
        base = path.rsplit("/", 1)[-1]
        if not (base == "requirements.txt" or
                (base.startswith("requirements") and base.endswith(".txt"))):
            continue
        if "node_modules/" in path or "apt" in base:
            continue
        data = _git_show(ref, path)
        if data is None:
            continue
        for name, version in parse_requirements(data).items():
            # Prefer a pinned version over unpinned; among multiple pinned
            # entries use first-seen so the same service consistently wins
            # across base and head refs (last-pinned-wins would let one
            # service's unchanged pin silently mask another's version bump).
            if name not in merged or (version and not merged[name]):
                merged[name] = version
                if sources is not None:
                    sources[name] = path
    return merged


def _direct_pin(spec: str) -> str:
    """Return an exact pin from a PEP 440 / Poetry version string, else ''."""
    spec = spec.strip().strip("'\"")
    if spec.startswith("=="):
        return spec[2:].strip().split(",", 1)[0].strip()
    if _EXACT_VERSION_RE.match(spec):
        return spec
    return ""


def parse_pyproject(data: bytes) -> dict[str, str]:
    """Return {canonical_name: pinned_version_or_''} from a pyproject.toml.

    Reads PEP 621 ``[project].dependencies`` and Poetry
    ``[tool.poetry.dependencies]``. Optional extras, PEP 735 dependency
    groups, and Poetry ``group.*.dependencies`` are omitted — those are
    typically dev/test and do not ship. Same name-level contract as
    ``parse_requirements``: only ``==`` pins (or Poetry exact versions) are
    recorded; ranges stay unpinned so PyPI drift cannot fabricate rows.
    """
    doc = tomllib.loads(data.decode("utf-8"))
    project = doc.get("project") or {}
    specs = [str(spec) for spec in (project.get("dependencies") or [])]
    out = parse_requirements("\n".join(specs).encode())

    poetry = ((doc.get("tool") or {}).get("poetry") or {}).get("dependencies") or {}
    for name, spec in poetry.items():
        if not name or name.lower() == "python":
            continue
        if isinstance(spec, dict) and (spec.get("git") or spec.get("url") or spec.get("path")):
            continue
        version_spec = spec.get("version") if isinstance(spec, dict) else spec
        version = _direct_pin(str(version_spec or ""))
        lname = name.lower()
        if lname not in out or (version and not out[lname]):
            out[lname] = version
    return out


def pyproject_inventory(
    ref: str, *, sources: dict[str, str] | None = None
) -> dict[str, str]:
    """Merge every pyproject.toml at `ref` into {name: pinned_version_or_''}.

    `sources` is the same out-parameter contract as `requirements_inventory`.
    """
    merged: dict[str, str] = {}
    for path in _list_lockfiles(ref, "pyproject.toml"):
        data = _git_show(ref, path)
        if data is None:
            continue
        try:
            parsed = parse_pyproject(data)
        except tomllib.TOMLDecodeError as exc:
            _log(f"skip {path}@{ref}: {exc}")
            continue
        for name, version in parsed.items():
            if name not in merged or (version and not merged[name]):
                merged[name] = version
                if sources is not None:
                    sources[name] = path
    return merged


def parse_pipfile(data: bytes) -> Inventory:
    """Return {(name, spec): {}} from a Pipfile's ``[packages]`` section.

    Pipfile is TOML: ``[packages]`` ships, ``[dev-packages]`` does not. Values
    are either a bare specifier (``"*"``, ``"==1.2.3"``) or a table carrying
    ``version`` / ``git`` / ``path``; local and VCS entries are skipped because
    they resolve to something other than a PyPI release.

    Parsed even though Pipfile.lock is parsed too, so that a Pipfile added
    without its lock still shows up. The name-level dedup in main() keeps the
    lock's resolved versions winning wherever both exist.
    """
    doc = tomllib.loads(data.decode("utf-8"))
    out: Inventory = {}
    for name, spec in (doc.get("packages") or {}).items():
        lname = (name or "").lower()
        if not lname:
            continue
        if isinstance(spec, dict):
            if spec.get("git") or spec.get("path") or spec.get("file"):
                continue
            version = str(spec.get("version") or "")
        else:
            version = str(spec or "")
        if version == "*":
            version = ""
        out[(lname, version)] = {"repository_url": ""}
    return out


def parse_setup_cfg(data: bytes) -> Inventory:
    """Return {(name, spec): {}} from a setup.cfg ``[options] install_requires``.

    ``[options.extras_require]`` is skipped: extras are opt-in, and in practice
    hold the dev/test groups that the rest of this file also excludes.
    """
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(data.decode("utf-8", errors="replace"))
    except configparser.Error as exc:
        raise UnparseableManifest(f"malformed setup.cfg: {exc}") from exc
    if not parser.has_option("options", "install_requires"):
        return {}
    raw = parser.get("options", "install_requires")
    return {
        (name, version): {"repository_url": ""}
        for name, version in parse_requirements(raw.encode()).items()
    }


def parse_setup_py(data: bytes) -> Inventory:
    """Return {(name, spec): {}} from a literal ``install_requires`` in setup.py.

    Parsed with ``ast``, never executed. Running an arbitrary setup.py from a
    pull request inside CI would hand any contributor code execution on a
    runner that holds a GITHUB_TOKEN — the dependency list is not worth that.

    Consequences of reading rather than running: a computed
    ``install_requires`` (a comprehension, an open() of requirements.txt, a
    function call) cannot be resolved. That case raises UnparseableManifest so
    it becomes a visible UNCOVERED_SOURCE row. A setup.py with no
    ``install_requires`` at all is not a gap — both of this repo's setup.py
    files are version shims whose dependencies live in pyproject.toml — so it
    returns an empty inventory.
    """
    try:
        tree = ast.parse(data.decode("utf-8", errors="replace"))
    except SyntaxError as exc:
        raise UnparseableManifest(f"setup.py does not parse: {exc}") from exc

    # One level of indirection is common: `INSTALL_REQUIRES = [...]` at module
    # level, then `setup(install_requires=INSTALL_REQUIRES)`.
    literals: dict[str, ast.expr] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    literals[target.id] = node.value

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name != "setup":
            continue
        for keyword in node.keywords:
            if keyword.arg != "install_requires":
                continue
            value = keyword.value
            if isinstance(value, ast.Name):
                value = literals.get(value.id, value)
            if not isinstance(value, (ast.List, ast.Tuple)):
                raise UnparseableManifest(
                    "install_requires is computed, not a literal list"
                )
            specs = []
            for element in value.elts:
                if not isinstance(element, ast.Constant) or not isinstance(
                    element.value, str
                ):
                    raise UnparseableManifest(
                        "install_requires contains a non-literal entry"
                    )
                specs.append(element.value)
            return {
                (pkg, version): {"repository_url": ""}
                for pkg, version in parse_requirements(
                    "\n".join(specs).encode()
                ).items()
            }
    return {}


def diff_requirements(
    base: dict[str, str],
    head: dict[str, str],
    covered_names: set[str],
    *,
    source: str = "requirements.txt",
    base_sources: dict[str, str] | None = None,
    head_sources: dict[str, str] | None = None,
) -> list[dict[str, str]]:
    """Diff direct-dependency NAME sets across requirements.txt / pyproject.toml.

    Reports packages added to / removed from the manifest, and `==`-pinned
    version bumps. Packages already inventoried by a lockfile (`covered_names`)
    are skipped — the lockfile diff covers them more accurately. Driven purely
    by the committed file contents, so it is deterministic: unchanged unpinned
    lines never produce phantom rows.
    """
    base_sources = base_sources or {}
    head_sources = head_sources or {}
    rows: list[dict[str, str]] = []
    for name in sorted(set(base) | set(head)):
        if name in covered_names:
            continue
        in_base, in_head = name in base, name in head
        bv, hv = base.get(name, ""), head.get(name, "")

        if not in_base and in_head:  # newly added direct dependency
            meta = pypi_metadata(name, hv)
            resolved = meta.get("version") or hv
            note = f"new {source} dependency"
            if not hv:
                note += "; unpinned (license shown for latest)"
            rows.append(make_row(
                language="python", package=name, change=CHANGE_ADDED,
                old_version="", new_version=(hv or f"latest ({resolved})"),
                old_license="", new_license=meta.get("license", ""),
                repository_url=meta.get("repository_url", ""), notes=note,
                source_kind=KIND_MANIFEST, source_file=head_sources.get(name, ""),
            ))
        elif in_base and not in_head:  # removed direct dependency
            rows.append(make_row(
                language="python", package=name, change=CHANGE_REMOVED,
                old_version=bv or "(unpinned)", new_version="",
                old_license="", new_license="",
                repository_url="", notes=f"removed from {source}",
                source_kind=KIND_MANIFEST, source_file=base_sources.get(name, ""),
            ))
        elif bv != hv and bv and hv:  # pinned == bump on both sides
            old_meta = pypi_metadata(name, bv)
            new_meta = pypi_metadata(name, hv)
            old_license = old_meta.get("license", "")
            new_license = new_meta.get("license", "")
            notes = f"{source} version pin changed"
            if old_license and new_license and old_license != new_license:
                notes += "; license changed"
            rows.append(make_row(
                language="python", package=name, change=CHANGE_UPDATED,
                old_version=bv, new_version=hv,
                old_license=old_license, new_license=new_license,
                repository_url=new_meta.get("repository_url", ""),
                notes=notes,
                source_kind=KIND_MANIFEST,
                source_file=head_sources.get(name, base_sources.get(name, "")),
            ))
    return rows


# ---------------------------------------------------------------------------
# Node parsers
# ---------------------------------------------------------------------------


def _npm_page(name: str, version: str) -> str:
    """Canonical npmjs.com page for a package.

    OSRB browses a package page to read its license; the resolved tarball URL
    in a lockfile is not something a human can review.
    """
    if version:
        return f"https://www.npmjs.com/package/{name}/v/{version}"
    return f"https://www.npmjs.com/package/{name}"


def parse_node_lock(data: bytes) -> Inventory:
    """Return {(name, version): {license, repository_url}} from package-lock.json."""
    doc = json.loads(data.decode("utf-8"))
    out: Inventory = {}
    packages = doc.get("packages") or {}
    for path, entry in packages.items():
        if not path or "node_modules/" not in path:
            continue
        name_from_path = path.rsplit("node_modules/", 1)[-1]
        name = (entry.get("name") or name_from_path or "").lower()
        version = str(entry.get("version") or "")
        if not name or not version:
            continue
        lic = entry.get("license") or ""
        if isinstance(lic, dict):
            lic = lic.get("type", "")
        elif isinstance(lic, list):
            lic = " OR ".join(
                str(x.get("type") if isinstance(x, dict) else x) for x in lic
            )
        repo_info = entry.get("repository")
        if isinstance(repo_info, dict):
            repo = str(repo_info.get("url") or "")
        elif isinstance(repo_info, str):
            repo = repo_info
        else:
            # No upstream repo declared in the lockfile. Fall back to the
            # canonical npmjs.com package page rather than the resolved tarball
            # URL, which is what OSRB will actually browse.
            repo = _npm_page(name, version)
        repo = repo.removeprefix("git+").removesuffix(".git")
        out[(name, version)] = {
            "license": str(lic),
            "repository_url": repo,
        }
    return out


def parse_node_workspace_names(data: bytes) -> set[str]:
    """Return package names npm resolves to local workspace links.

    npm represents a workspace dependency declared as ``"*"`` with a
    ``link: true`` entry in package-lock.json. The link has no version, so it
    is intentionally absent from :func:`parse_node_lock`; without retaining
    its name separately, however, the package.json pass mistakes the same
    first-party dependency for an unresolved registry package.
    """
    doc = json.loads(data.decode("utf-8"))
    out: set[str] = set()
    for path, entry in (doc.get("packages") or {}).items():
        if not path or "node_modules/" not in path or not entry.get("link"):
            continue
        name = path.rsplit("node_modules/", 1)[-1].lower()
        if name:
            out.add(name)
    return out


# Specs that resolve to something inside this repository rather than to a
# registry release. `services/vios/ui/vios-ui/package.json` depends on
# `vst-streaming-lib` as `file:../streaming-lib` today; reporting our own code
# to OSRB as a third party wastes a reviewer's time and hides the real rows.
_LOCAL_NPM_PROTOCOLS = ("file:", "link:", "portal:", "workspace:")


def parse_package_json(data: bytes) -> Inventory:
    """Return {(name, spec): {repository_url}} from a package.json manifest.

    Inventories ``dependencies``, ``optionalDependencies`` and
    ``peerDependencies`` and NOT ``devDependencies``, which is the same
    runtime-only rule every other parser in this file follows: build tooling
    and test runners never reach a release artifact. Optional and peer
    dependencies do reach it — an optional dependency that installs is
    ordinary shipped code, and a peer dependency is a third party the consumer
    is required to provide — so both are in scope.

    The value is the declared RANGE, not a resolved version, because that is
    all a manifest knows. Diffing on the literal committed range keeps the
    result deterministic; resolving it would make the diff depend on what npm
    published this morning.
    """
    doc = json.loads(data.decode("utf-8"))
    out: Inventory = {}
    for section in ("dependencies", "optionalDependencies", "peerDependencies"):
        for name, spec in (doc.get(section) or {}).items():
            lname = (name or "").lower()
            spec = str(spec or "")
            if not lname:
                continue
            if spec.startswith(_LOCAL_NPM_PROTOCOLS) or "://" in spec:
                continue
            out.setdefault((lname, spec), {"repository_url": _npm_page(lname, "")})
    return out


_YARN_ENTRY_RE = re.compile(r"^(?P<descriptor>[^\s#].*):\s*$")
_YARN_VERSION_RE = re.compile(r"""^\s+version:?\s+"?(?P<version>[^"\s]+)"?\s*$""")


def _yarn_descriptor_name(descriptor: str) -> str:
    """Extract the package name from one yarn descriptor.

    A descriptor is `<name>@<range>` where the name may itself start with `@`
    for a scope (`@babel/core@npm:^7.0.0`). Splitting on the first `@` would
    turn every scoped package into the empty string, i.e. would drop the whole
    Babel/NX/Angular half of a lockfile from OSRB review.
    """
    descriptor = descriptor.strip().strip(",").strip('"').strip("'")
    at = descriptor.rfind("@")
    if at <= 0:
        return descriptor.lower()
    return descriptor[:at].lower()


def _yarn_descriptor_spec(descriptor: str) -> str:
    descriptor = descriptor.strip().strip(",").strip('"').strip("'")
    at = descriptor.rfind("@")
    return descriptor[at + 1:] if at > 0 else ""


def parse_yarn_lock(data: bytes) -> Inventory:
    """Return {(name, version): {repository_url}} from a yarn.lock.

    Handles both on-disk formats with one line-oriented reader, because a repo
    can migrate between them without anyone telling this scanner: Yarn 1 writes
    `lodash@^4.17.21:` followed by an indented `version "4.17.21"`, Yarn 2+
    writes YAML-ish `"lodash@npm:^4.17.21":` followed by `version: 4.17.21`.
    Both are read here rather than through a YAML library, which the CI
    environment does not have.

    Workspace and link descriptors are skipped: they name directories in this
    repository, not third-party releases. Yarn does not record dependency
    groups in the lock, so a lock alone cannot exclude devDependencies — the
    package.json pass is where that distinction is available, and
    over-reporting here is the safe direction for a license review.
    """
    out: Inventory = {}
    current: list[str] = []
    for raw in data.decode("utf-8", errors="replace").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if not raw[0].isspace():
            match = _YARN_ENTRY_RE.match(raw)
            current = []
            if match:
                descriptor_line = match.group("descriptor")
                if descriptor_line.startswith("__metadata"):
                    continue
                current = [
                    part for part in descriptor_line.split(",") if part.strip()
                ]
            continue
        if not current:
            continue
        version_match = _YARN_VERSION_RE.match(raw)
        if not version_match:
            continue
        version = version_match.group("version")
        for descriptor in current:
            spec = _yarn_descriptor_spec(descriptor)
            if spec.startswith(("workspace:", "link:", "portal:", "file:")):
                continue
            name = _yarn_descriptor_name(descriptor)
            if name:
                out.setdefault((name, version), {"repository_url": _npm_page(name, version)})
        current = []
    return out


def parse_pnpm_lock(data: bytes) -> Inventory:
    """Return {(name, version): {repository_url}} from a pnpm-lock.yaml.

    Only the top-level ``packages:`` block is read. The ``importers:`` block
    repeats the same names as ``<name>: {specifier, version}`` pairs, and
    reading both would double every package and turn a one-line pnpm bump into
    a wall of duplicate rows.

    Three key spellings have shipped across pnpm 5-9 — `/lodash/4.17.21`,
    `/lodash@4.17.21` and bare `lodash@4.17.21`, each optionally followed by a
    `(peer)` suffix — so all three are accepted. Reading this with a
    line-oriented reader rather than a YAML parser is a constraint of the CI
    environment, which has no PyYAML.
    """
    out: Inventory = {}
    in_packages = False
    for raw in data.decode("utf-8", errors="replace").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if not raw[0].isspace():
            in_packages = raw.rstrip().rstrip(":") == "packages"
            continue
        if not in_packages:
            continue
        stripped = raw.strip()
        if not stripped.endswith(":"):
            continue
        indent = len(raw) - len(raw.lstrip())
        if indent > 2:  # a nested key such as `resolution:`
            continue
        key = stripped[:-1].strip().strip("'").strip('"')
        key = key.split("(", 1)[0].lstrip("/")
        if not key:
            continue
        if "@" in key[1:]:
            name, _, version = key.rpartition("@")
        elif "/" in key.lstrip("@"):
            name, _, version = key.rpartition("/")
        else:
            continue
        name = name.lower()
        if not name or not version:
            continue
        out.setdefault((name, version), {"repository_url": _npm_page(name, version)})
    return out


# ---------------------------------------------------------------------------
# Go parsers
# ---------------------------------------------------------------------------

_GO_BLOCK_RE = re.compile(r"^(require|replace|exclude|retract)\s*\($")
_GO_REQUIRE_LINE_RE = re.compile(r"^(?P<module>[^\s()]+)\s+(?P<version>v[^\s]+)")


def _go_page(module: str, version: str) -> str:
    return f"https://pkg.go.dev/{module}@{version}" if version else f"https://pkg.go.dev/{module}"


def parse_go_mod(data: bytes) -> Inventory:
    """Return {(module, version): {repository_url}} from a go.mod.

    Both `require` forms are handled: the single-line `require m v1.2.3` and
    the parenthesised block. `replace`, `exclude` and `retract` blocks are
    skipped — they rewrite or withdraw requirements rather than adding them,
    and treating a `replace` target as a dependency would invent packages the
    build never fetches.

    `// indirect` requirements are KEPT. Unlike a dev dependency, an indirect
    Go module is linked into the binary that ships, so OSRB has to see it.
    """
    out: Inventory = {}
    block: str | None = None
    for raw in data.decode("utf-8", errors="replace").splitlines():
        line = raw.split("//", 1)[0].strip()
        if not line:
            continue
        if block is not None:
            if line == ")":
                block = None
                continue
            if block != "require":
                continue
            candidate = line
        else:
            match = _GO_BLOCK_RE.match(line)
            if match:
                block = match.group(1)
                continue
            if not line.startswith("require "):
                continue
            candidate = line[len("require "):].strip()
        entry = _GO_REQUIRE_LINE_RE.match(candidate)
        if not entry:
            continue
        module = entry.group("module").strip('"')
        version = entry.group("version")
        out.setdefault((module.lower(), version), {"repository_url": _go_page(module, version)})
    return out


def parse_go_sum(data: bytes) -> Inventory:
    """Return {(module, version): {repository_url}} from a go.sum.

    go.sum lists every module in the build graph, including ones the final
    binary never links, and repeats each with a `/go.mod` suffix for the
    manifest hash. The suffix is stripped and the duplicates collapse.

    This over-reports relative to go.mod. For a license review that is the
    safe direction: a module listed and not shipped costs a reviewer one line,
    a module shipped and not listed costs a release.
    """
    out: Inventory = {}
    for raw in data.decode("utf-8", errors="replace").splitlines():
        parts = raw.split()
        if len(parts) < 3:
            continue
        module, version = parts[0], parts[1]
        version = version.removesuffix("/go.mod")
        if not module or not version.startswith("v"):
            continue
        out.setdefault((module.lower(), version), {"repository_url": _go_page(module, version)})
    return out


# ---------------------------------------------------------------------------
# Rust, Java, Ruby, C/C++, conda parsers
# ---------------------------------------------------------------------------

_CARGO_RUNTIME_TABLES = ("dependencies",)


def _cargo_crate_page(name: str, version: str) -> str:
    return f"https://crates.io/crates/{name}" if not version else f"https://crates.io/crates/{name}/{version}"


def _cargo_collect(table: dict, out: Inventory) -> None:
    for name, spec in (table or {}).items():
        lname = (name or "").lower()
        if not lname:
            continue
        if isinstance(spec, dict):
            if spec.get("path") or spec.get("git"):
                continue  # workspace-local or VCS: not a crates.io release
            version = str(spec.get("version") or "")
        else:
            version = str(spec or "")
        out.setdefault((lname, version), {"repository_url": _cargo_crate_page(lname, version)})


def parse_cargo(data: bytes) -> Inventory:
    """Return {(crate, version): {repository_url}} from Cargo.lock OR Cargo.toml.

    One function for both because the two are told apart unambiguously by the
    TOML shape: a lock has an array of tables (`[[package]]`, so `package` is a
    list), a manifest has a single `[package]` table. Splitting them into two
    functions would need the caller to know which filename it holds, and the
    caller that gets that wrong reports nothing at all.

    From a manifest, `[dependencies]` and `[target.*.dependencies]` are read;
    `[dev-dependencies]` and `[build-dependencies]` are not, matching the
    runtime-only rule — test crates and build scripts do not ship in the
    produced binary. Path and git dependencies are skipped as workspace-local.
    """
    doc = tomllib.loads(data.decode("utf-8"))
    out: Inventory = {}

    packages = doc.get("package")
    if isinstance(packages, list):  # Cargo.lock
        for pkg in packages:
            name = (pkg.get("name") or "").lower()
            version = str(pkg.get("version") or "")
            source = str(pkg.get("source") or "")
            if not name:
                continue
            if not source:
                # Cargo omits `source` for workspace members, i.e. this
                # repository's own crates. Reporting our code to OSRB as a
                # third party is noise on every Rust PR, the same reason
                # parse_uv_lock drops the editable root.
                continue
            out.setdefault((name, version), {"repository_url": source})
        return out

    for table in _CARGO_RUNTIME_TABLES:
        _cargo_collect(doc.get(table) or {}, out)
    for target in (doc.get("target") or {}).values():
        if isinstance(target, dict):
            for table in _CARGO_RUNTIME_TABLES:
                _cargo_collect(target.get(table) or {}, out)
    _cargo_collect((doc.get("workspace") or {}).get("dependencies") or {}, out)
    return out


_POM_NS_RE = re.compile(r"^\{[^}]*\}")

# Scopes whose artifacts never reach a shipped artifact. `provided` is NOT
# here: a provided artifact is on the runtime classpath, someone else just
# supplies it, and it still carries its license into the deployment.
_MAVEN_EXCLUDED_SCOPES = {"test"}


def _pom_local(tag: str) -> str:
    return _POM_NS_RE.sub("", tag)


def _pom_child_text(element: ET.Element, name: str) -> str:
    for child in element:
        if _pom_local(child.tag) == name:
            return (child.text or "").strip()
    return ""


def _pom_resolve(value: str, properties: dict[str, str]) -> str:
    """Substitute ${...} placeholders from <properties>, once, non-recursively.

    An unresolved placeholder is returned as-is rather than blanked: the
    literal `${log4j.version}` in the CSV tells a reviewer the version is
    externalised, whereas an empty cell reads like "no version pinned".
    """
    def replace(match: re.Match[str]) -> str:
        return properties.get(match.group(1), match.group(0))

    return re.sub(r"\$\{([^}]+)\}", replace, value)


def parse_pom_xml(data: bytes) -> Inventory:
    """Return {("group:artifact", version): {repository_url}} from a pom.xml.

    Maven POMs are namespaced (`http://maven.apache.org/POM/4.0.0`), and
    ElementTree reports every tag with that namespace glued on. Matching on the
    raw tag would find zero `<dependency>` elements in a perfectly ordinary POM
    and report a Java service as having no dependencies at all, so the
    namespace is stripped from every tag before comparison.

    Every `<dependencies>` block is read, including the one under
    `<dependencyManagement>` and any inside `<profiles>`: a version pinned only
    in dependencyManagement is still the version that ships. `test`-scoped
    entries are dropped per the runtime-only rule.
    """
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise UnparseableManifest(f"pom.xml does not parse: {exc}") from exc

    properties: dict[str, str] = {}
    for element in root.iter():
        if _pom_local(element.tag) != "properties":
            continue
        for child in element:
            properties[_pom_local(child.tag)] = (child.text or "").strip()
    for name in ("version", "groupId", "artifactId"):
        value = _pom_child_text(root, name)
        if value:
            properties.setdefault(f"project.{name}", value)

    out: Inventory = {}
    for block in root.iter():
        if _pom_local(block.tag) != "dependencies":
            continue
        for dependency in block:
            if _pom_local(dependency.tag) != "dependency":
                continue
            scope = _pom_child_text(dependency, "scope").lower()
            if scope in _MAVEN_EXCLUDED_SCOPES:
                continue
            group = _pom_resolve(_pom_child_text(dependency, "groupId"), properties)
            artifact = _pom_resolve(_pom_child_text(dependency, "artifactId"), properties)
            version = _pom_resolve(_pom_child_text(dependency, "version"), properties)
            if not group or not artifact:
                continue
            name = f"{group}:{artifact}".lower()
            out.setdefault(
                (name, version),
                {"repository_url": f"https://mvnrepository.com/artifact/{group}/{artifact}"},
            )
    return out


# Configurations that never ship: test harnesses, buildscript classpath, and
# annotation processors that run at compile time and produce no runtime jar.
_GRADLE_EXCLUDED_CONFIG_RE = re.compile(
    r"test|classpath|annotationprocessor|kapt|ksp|lint|checkstyle|pmd|spotbugs|jacoco|detekt",
    re.IGNORECASE,
)

_GRADLE_COORD_RE = re.compile(
    r"""^[ \t]*(?P<conf>[A-Za-z][A-Za-z0-9_]*)(?:[ \t]+|[ \t]*\([ \t]*)['"]"""
    r"""(?P<coord>[^'"\s]+:[^'"\s]+(?::[^'"\s]+)?)['"]""",
    re.MULTILINE,
)

_GRADLE_MAP_RE = re.compile(
    r"""^[ \t]*(?P<conf>[A-Za-z][A-Za-z0-9_]*)[ \t(]+group:[ \t]*['"](?P<group>[^'"]+)['"]"""
    r"""[ \t]*,[ \t]*name:[ \t]*['"](?P<artifact>[^'"]+)['"]"""
    r"""(?:[ \t]*,[ \t]*version:[ \t]*['"](?P<version>[^'"]+)['"])?""",
    re.MULTILINE,
)


def parse_gradle(data: bytes) -> Inventory:
    """Return {("group:artifact", version): {repository_url}} from a Gradle build.

    BEST-EFFORT AND KNOWN INCOMPLETE. A build.gradle(.kts) is a program, not
    data: versions can come from a version catalog, a `versions.yml` loaded at
    configuration time, or a computed property, and this reads the file with
    regexes instead of running Gradle. `tools/logstash-plugins/.../build.gradle`
    in this repo already contains one such case
    (`org.jruby:jruby-complete:${gradle.ext.versions.jruby.version}`).

    What that means for the gate: treat a missing Gradle row as possible, not
    as proof of absence. It is still worth having — it catches the common
    `implementation 'group:artifact:version'` and `group:/name:/version:` map
    forms, which is how most dependencies are actually written — but a Gradle
    build is the one place where the OSRB inventory should be spot-checked by
    hand.
    """
    text = data.decode("utf-8", errors="replace")
    out: Inventory = {}

    def add(configuration: str, name: str, version: str) -> None:
        if _GRADLE_EXCLUDED_CONFIG_RE.search(configuration):
            return
        if not name:
            return
        group = name.split(":", 1)[0]
        artifact = name.split(":", 1)[1] if ":" in name else ""
        out.setdefault(
            (name.lower(), version),
            {"repository_url": f"https://mvnrepository.com/artifact/{group}/{artifact}"},
        )

    for match in _GRADLE_COORD_RE.finditer(text):
        parts = match.group("coord").split(":")
        if len(parts) == 2:
            add(match.group("conf"), f"{parts[0]}:{parts[1]}", "")
        elif len(parts) >= 3:
            add(match.group("conf"), f"{parts[0]}:{parts[1]}", parts[2])
    for match in _GRADLE_MAP_RE.finditer(text):
        add(
            match.group("conf"),
            f"{match.group('group')}:{match.group('artifact')}",
            match.group("version") or "",
        )
    return out


_GEM_SPEC_RE = re.compile(r"^ {4}(?P<name>[A-Za-z0-9._-]+) \((?P<version>[^)]+)\)$")


def parse_gemfile_lock(data: bytes) -> Inventory:
    """Return {(gem, version): {repository_url}} from a Gemfile.lock.

    Only the resolved gems under a `specs:` heading are read; they sit at
    exactly four spaces of indent, while the six-space lines beneath them are
    that gem's own version CONSTRAINTS (`rspec-core (~> 3.12.0)`), not
    resolutions. Treating a constraint as a version would put `~> 3.12.0` in
    the CSV as if it were a shipped release.

    Bundler does not record groups in the lock, so a lock alone cannot exclude
    the `:development` group. This therefore over-reports relative to the
    Gemfile — the safe direction for a license review.
    """
    out: Inventory = {}
    in_specs = False
    for raw in data.decode("utf-8", errors="replace").splitlines():
        stripped = raw.strip()
        if not raw.startswith(" "):
            in_specs = False
            continue
        if stripped == "specs:":
            in_specs = True
            continue
        if not in_specs:
            continue
        match = _GEM_SPEC_RE.match(raw.rstrip())
        if not match:
            continue
        name = match.group("name").lower()
        version = match.group("version").strip()
        out.setdefault((name, version), {"repository_url": f"https://rubygems.org/gems/{name}"})
    return out


_GEMSPEC_DEP_RE = re.compile(
    r"""add_(?P<kind>runtime_|development_)?dependency[ \t(]+['"](?P<name>[^'"]+)['"]"""
    r"""(?:[ \t]*,[ \t]*['"](?P<version>[^'"]+)['"])?"""
)


def parse_gemspec(data: bytes) -> Inventory:
    """Return {(gem, spec): {repository_url}} from a .gemspec.

    `add_dependency` and `add_runtime_dependency` are inventoried;
    `add_development_dependency` is not, which is the gemspec spelling of the
    runtime-only rule used everywhere else in this file.

    A gemspec is Ruby and is matched with a regex, never executed — the same
    reasoning as setup.py: a pull request must not get code execution on a CI
    runner holding a token.
    """
    out: Inventory = {}
    for match in _GEMSPEC_DEP_RE.finditer(data.decode("utf-8", errors="replace")):
        if match.group("kind") == "development_":
            continue
        name = match.group("name").lower()
        out.setdefault(
            (name, match.group("version") or ""),
            {"repository_url": f"https://rubygems.org/gems/{name}"},
        )
    return out


_CONAN_REF_RE = re.compile(r"^(?P<name>[A-Za-z0-9_.+-]+)/(?P<version>[A-Za-z0-9_.+-]+)")
_CONAN_PY_REQUIRES_RE = re.compile(
    r"""(?<!build_)(?<!tool_)(?<!test_)requires[ \t]*(?:=|\()[^\n]*""",
)
_CONAN_PY_REF_RE = re.compile(r"""['"](?P<ref>[A-Za-z0-9_.+-]+/[A-Za-z0-9_.+\[\]<>=~ -]+)['"]""")


def parse_conanfile(data: bytes) -> Inventory:
    """Return {(name, version): {}} from a conanfile.txt or conanfile.py.

    conanfile.txt is INI-shaped and read section by section: `[requires]` is
    inventoried, `[build_requires]` / `[tool_requires]` / `[test_requires]`
    are not, per the runtime-only rule.

    conanfile.py is Python and is regex-scanned, not executed, for the same
    reason as setup.py and .gemspec. That makes the .py path BEST-EFFORT: a
    `self.requires(f"zlib/{self.zlib_version}")` cannot be resolved and will be
    missed. The .txt path is exact.
    """
    text = data.decode("utf-8", errors="replace")
    out: Inventory = {}

    section = ""
    saw_section = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            saw_section = True
            continue
        if section != "requires":
            continue
        match = _CONAN_REF_RE.match(line)
        if match:
            out.setdefault(
                (match.group("name").lower(), match.group("version")),
                {"repository_url": f"https://conan.io/center/recipes/{match.group('name').lower()}"},
            )
    if saw_section:
        return out

    for statement in _CONAN_PY_REQUIRES_RE.finditer(text):
        for ref in _CONAN_PY_REF_RE.finditer(statement.group(0)):
            name, _, version = ref.group("ref").partition("/")
            out.setdefault(
                (name.lower(), version),
                {"repository_url": f"https://conan.io/center/recipes/{name.lower()}"},
            )
    return out


def parse_vcpkg_json(data: bytes) -> Inventory:
    """Return {(port, version): {}} from a vcpkg.json manifest.

    A dependency entry is either a bare port name or an object carrying a
    minimum version under `version>=`. `default-features` and `features` are
    ignored: they select within a port, they do not add a new third party.
    """
    doc = json.loads(data.decode("utf-8"))
    out: Inventory = {}
    for entry in doc.get("dependencies") or []:
        if isinstance(entry, str):
            name, version = entry, ""
        elif isinstance(entry, dict):
            name = str(entry.get("name") or "")
            version = str(entry.get("version>=") or entry.get("version") or "")
        else:
            continue
        if not name:
            continue
        out.setdefault(
            (name.lower(), version),
            {"repository_url": f"https://vcpkg.io/en/package/{name.lower()}"},
        )
    for override in doc.get("overrides") or []:
        if not isinstance(override, dict):
            continue
        name = str(override.get("name") or "")
        if name:
            out.setdefault(
                (name.lower(), str(override.get("version") or "")),
                {"repository_url": f"https://vcpkg.io/en/package/{name.lower()}"},
            )
    return out


_CONDA_SPEC_RE = re.compile(r"^(?P<name>[A-Za-z0-9._-]+)\s*(?:[=<>!~]+\s*(?P<version>[^\s#]+))?")


def parse_conda_environment(data: bytes) -> Inventory:
    """Return {(name, version): {}} from an environment.yml.

    Read line by line rather than with a YAML library, which CI does not have.
    Only the `dependencies:` block is walked, and its nested `- pip:` list is
    handed to the requirements parser so a pip entry is normalised the same way
    it would be in a requirements.txt — otherwise `httpx>=0.27.0` would be
    inventoried under one shape here and another shape there, and the dedup
    against the Python lockfiles would stop matching.

    `python` itself is excluded, the same exclusion `parse_pyproject` applies
    to Poetry's `python` constraint: it is the interpreter, not a package OSRB
    reviews.
    """
    out: Inventory = {}
    in_dependencies = False
    in_pip = False
    pip_indent = 0
    pip_specs: list[str] = []
    for raw in data.decode("utf-8", errors="replace").splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0:
            in_dependencies = line.rstrip(":").strip() == "dependencies"
            in_pip = False
            continue
        if not in_dependencies:
            continue
        item = line.strip()
        if not item.startswith("-"):
            continue
        item = item[1:].strip()
        if item.rstrip(":") == "pip" and item.endswith(":"):
            in_pip = True
            pip_indent = indent
            continue
        if in_pip and indent > pip_indent:
            pip_specs.append(item.strip("'\""))
            continue
        in_pip = False
        match = _CONDA_SPEC_RE.match(item.strip("'\""))
        if not match:
            continue
        name = match.group("name").lower()
        if not name or name == "python":
            continue
        out.setdefault((name, match.group("version") or ""), {"repository_url": ""})
    for name, version in parse_requirements("\n".join(pip_specs).encode()).items():
        out.setdefault((name, version), {"repository_url": ""})
    return out


# ---------------------------------------------------------------------------
# Inventory walking and diffing
# ---------------------------------------------------------------------------


def _inventory_at_ref(
    ref: str,
    filename: str,
    parser: Callable[[bytes], Inventory],
    *,
    errors: list[tuple[str, str]] | None = None,
) -> Inventory:
    """Merge one filename's parser across every copy of it at `ref`.

    `errors` is an optional out-parameter collecting (path, reason) for files
    that failed to parse. A parse failure used to be logged and dropped, which
    means a corrupted lockfile produced an empty inventory that looks exactly
    like a lockfile with no dependencies. Callers turn these into
    UNCOVERED_SOURCE rows instead.
    """
    inv: Inventory = {}
    kind = None
    for path in _list_lockfiles(ref, filename):
        data = _git_show(ref, path)
        if data is None:
            continue
        kind = is_dependency_file(path) or KIND_LOCKFILE
        try:
            parsed = parser(data)
        except (tomllib.TOMLDecodeError, json.JSONDecodeError, UnparseableManifest) as exc:
            _log(f"skip {path}@{ref}: {exc}")
            if errors is not None:
                errors.append((path, str(exc)))
            continue
        for key, meta in parsed.items():
            inv.setdefault(key, {**meta, "source_file": path, "source_kind": kind})
    return inv


def node_workspace_names_by_module(
    ref: str, paths: list[str]
) -> dict[str, set[str]]:
    """Return local npm workspace names, grouped by their owning module.

    The ordinary Node inventory deliberately excludes these versionless link
    entries. This companion walk lets declaration consumers suppress their
    package.json ranges without turning first-party workspaces into OSRB rows.
    A malformed lockfile is reported by the primary lockfile parser, so this
    secondary classification pass only skips it.
    """
    grouped: dict[str, set[str]] = {}
    for path in paths:
        if _basename(path).lower() != "package-lock.json":
            continue
        data = _git_show(ref, path)
        if data is None:
            continue
        try:
            names = parse_node_workspace_names(data)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if names:
            grouped.setdefault(owning_module(path), set()).update(names)
    return grouped


def _walk_inventory(
    ref: str,
    paths: list[str],
    selector: Callable[[str], Callable[[bytes], Inventory] | None],
    *,
    errors: list[tuple[str, str]] | None = None,
) -> Inventory:
    """Parse every path at `ref` that `selector` claims, merging the results.

    A single pass over an explicit path list, rather than one `git ls-tree`
    per filename, because the ecosystems added here span a dozen filename
    shapes and re-listing an 8k-path tree for each one is pure overhead.
    """
    inv: Inventory = {}
    for path in paths:
        parser = selector(path)
        if parser is None:
            continue
        data = _git_show(ref, path)
        if data is None:
            continue
        kind = is_dependency_file(path) or KIND_MANIFEST
        try:
            parsed = parser(data)
        except UnparseableManifest as exc:
            _log(f"skip {path}@{ref}: {exc}")
            if errors is not None:
                errors.append((path, str(exc)))
            continue
        except Exception as exc:  # noqa: BLE001 - see below
            # A parser that raises must not be indistinguishable from a file
            # with no dependencies. Record it so the caller can emit an
            # UNCOVERED_SOURCE row; swallowing it here is the silent failure
            # this module exists to prevent.
            _log(f"skip {path}@{ref}: {type(exc).__name__}: {exc}")
            if errors is not None:
                errors.append((path, f"{type(exc).__name__}: {exc}"))
            continue
        for key, meta in parsed.items():
            inv.setdefault(key, {**meta, "source_file": path, "source_kind": kind})
    return inv


_pypi_cache: dict[PackageKey, dict[str, str]] = {}


def _classifier_license(classifiers: list[str]) -> str:
    for c in classifiers:
        if c.startswith("License :: OSI Approved :: "):
            label = c.rsplit("::", 1)[-1].strip()
            return label.removesuffix(" License")
    return ""


def _project_url(urls: dict[str, str], home_page: str) -> str:
    for key in ("Repository", "Source", "Source Code", "Code", "Homepage", "Home", "GitHub"):
        if urls.get(key):
            return urls[key]
    return home_page or ""


def pypi_metadata(name: str, version: str) -> dict[str, str]:
    """Return license + repository_url for one PyPI package version.

    An empty ``version`` resolves the package's latest release (the
    unversioned PyPI endpoint); the resolved version is returned under the
    ``version`` key so callers can label an otherwise-unpinned dependency.
    """
    key = (name.lower(), version)
    if key in _pypi_cache:
        return _pypi_cache[key]
    url = f"{PYPI_INDEX}/{name}/{version}/json" if version else f"{PYPI_INDEX}/{name}/json"
    try:
        with urllib.request.urlopen(url, timeout=PYPI_TIMEOUT) as response:
            doc = json.load(response)
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError):
        result = {"license": "", "repository_url": "", "version": version}
    else:
        info = doc.get("info") or {}
        lic = (info.get("license") or "").strip()
        # PyPI license field sometimes contains full license text. Prefer
        # classifier-derived SPDX-ish label when the freeform field is huge.
        if not lic or len(lic) > 80 or "\n" in lic:
            classifier_lic = _classifier_license(info.get("classifiers") or [])
            if classifier_lic:
                lic = classifier_lic
        repo = _project_url(info.get("project_urls") or {}, info.get("home_page") or "")
        result = {"license": lic, "repository_url": repo, "version": str(info.get("version") or version)}
    _pypi_cache[key] = result
    return result


def diff_language(
    language: str, base: Inventory, head: Inventory
) -> list[dict[str, str]]:
    base_by_name: dict[str, set[str]] = {}
    head_by_name: dict[str, set[str]] = {}
    for name, version in base:
        base_by_name.setdefault(name, set()).add(version)
    for name, version in head:
        head_by_name.setdefault(name, set()).add(version)

    rows: list[dict[str, str]] = []
    for name in sorted(set(base_by_name) | set(head_by_name)):
        base_versions = base_by_name.get(name, set())
        head_versions = head_by_name.get(name, set())
        if base_versions == head_versions:
            continue

        only_old = sorted(base_versions - head_versions)
        only_new = sorted(head_versions - base_versions)

        if not base_versions:
            for v in only_new:
                meta = head[(name, v)]
                if language == "python" and not meta.get("license"):
                    meta = {**meta, **pypi_metadata(name, v)}
                rows.append(_row(language, name, CHANGE_ADDED, "", v, "", meta))
            continue
        if not head_versions:
            for v in only_old:
                meta = base[(name, v)]
                if language == "python" and not meta.get("license"):
                    meta = {**meta, **pypi_metadata(name, v)}
                rows.append(
                    _row(language, name, CHANGE_REMOVED, v, "", meta.get("license", ""), meta)
                )
            continue

        # A package can coexist at several versions across independent
        # modules.  If the head merely contracts or expands that set, report
        # the versions that actually left or entered.  Pairing a removed UI
        # version with unchanged VIOS versions fabricates a cross-module
        # "license changed" update and needlessly blocks the PR.
        if only_old and not only_new:
            for v in only_old:
                meta = base[(name, v)]
                if language == "python" and not meta.get("license"):
                    meta = {**meta, **pypi_metadata(name, v)}
                rows.append(
                    _row(
                        language,
                        name,
                        CHANGE_REMOVED,
                        v,
                        "",
                        meta.get("license", ""),
                        meta,
                    )
                )
            continue
        if only_new and not only_old:
            for v in only_new:
                meta = head[(name, v)]
                if language == "python" and not meta.get("license"):
                    meta = {**meta, **pypi_metadata(name, v)}
                rows.append(_row(language, name, CHANGE_ADDED, "", v, "", meta))
            continue

        # Both sides changed: pair the departing and arriving version sets as
        # one update so a real version/license transition stays reviewable.
        old_v = ",".join(only_old) or ",".join(sorted(base_versions))
        new_v = ",".join(only_new) or ",".join(sorted(head_versions))

        def _licenses(inv: Inventory, names_versions: list[str]) -> str:
            picked: set[str] = set()
            for v in names_versions:
                m = inv.get((name, v), {})
                if language == "python" and not m.get("license"):
                    m = {**m, **pypi_metadata(name, v)}
                if m.get("license"):
                    picked.add(m["license"])
            return ",".join(sorted(picked))

        old_lic = _licenses(base, only_old or sorted(base_versions))
        new_lic = _licenses(head, only_new or sorted(head_versions))

        # Repo URL: prefer head over base.
        repo = ""
        head_meta: dict[str, str] = {}
        for v in only_new or sorted(head_versions):
            m = head.get((name, v), {})
            head_meta = head_meta or m
            if language == "python" and not m.get("repository_url"):
                m = {**m, **pypi_metadata(name, v)}
            if m.get("repository_url"):
                repo = m["repository_url"]
                break
        notes = "license changed" if old_lic and new_lic and old_lic != new_lic else ""
        rows.append(
            make_row(
                language=language,
                package=name,
                change=CHANGE_UPDATED,
                old_version=old_v,
                new_version=new_v,
                old_license=old_lic,
                new_license=new_lic,
                repository_url=repo,
                notes=notes,
                source_kind=head_meta.get("source_kind", ""),
                source_file=head_meta.get("source_file", ""),
            )
        )
    return rows


def _row(
    language: str,
    name: str,
    change: str,
    old_v: str,
    new_v: str,
    old_lic: str,
    meta: dict[str, str],
) -> dict[str, str]:
    return make_row(
        language=language,
        package=name,
        change=change,
        old_version=old_v,
        new_version=new_v,
        old_license=old_lic if change == CHANGE_REMOVED else "",
        new_license=meta.get("license", "") if change != CHANGE_REMOVED else "",
        repository_url=meta.get("repository_url", ""),
        source_kind=meta.get("source_kind", ""),
        source_file=meta.get("source_file", ""),
    )


def _drop_covered(inventory: Inventory, covered: set[str]) -> Inventory:
    """Remove names a lockfile already inventoried more accurately.

    A package declared in package.json as `^18.3.0` and locked at `18.3.1` is
    ONE dependency. Reporting both puts two rows in front of OSRB for the same
    package with two different "versions", one of which is not a version.
    """
    return {key: meta for key, meta in inventory.items() if key[0] not in covered}


def _drop_local_node_workspace_rows(
    inventory: Inventory, names_by_module: dict[str, set[str]]
) -> Inventory:
    """Remove first-party npm links without suppressing another module's row."""
    return {
        key: meta
        for key, meta in inventory.items()
        if key[0]
        not in names_by_module.get(owning_module(meta.get("source_file", "")), set())
    }


def _names(inventory: Inventory) -> set[str]:
    return {name for name, _version in inventory}


def _flatten(inventory: Inventory) -> dict[str, str]:
    """Collapse an Inventory to {name: version}, preferring a pinned version."""
    out: dict[str, str] = {}
    for name, version in inventory:
        if name not in out or (version and not out[name]):
            out[name] = version
    return out


def _flatten_sources(inventory: Inventory) -> dict[str, str]:
    out: dict[str, str] = {}
    for (name, _version), meta in inventory.items():
        out.setdefault(name, meta.get("source_file", ""))
    return out


# ---------------------------------------------------------------------------
# Ecosystem registry
# ---------------------------------------------------------------------------

# (language, {basename: parser}, [(suffix, parser)]) — used by main()'s single
# pass over the tree. Keeping this as data rather than a chain of ifs is what
# makes "is there a parser for this filename" answerable in one place, which
# is what `is_parsed` has to stay consistent with.
_LOCK_PARSERS: dict[str, tuple[str, Callable[[bytes], Inventory]]] = {
    "yarn.lock": ("node", parse_yarn_lock),
    "pnpm-lock.yaml": ("node", parse_pnpm_lock),
    "go.sum": ("go", parse_go_sum),
    "cargo.lock": ("rust", parse_cargo),
    "gemfile.lock": ("ruby", parse_gemfile_lock),
}

_MANIFEST_PARSERS: dict[str, tuple[str, Callable[[bytes], Inventory]]] = {
    "package.json": ("node", parse_package_json),
    "go.mod": ("go", parse_go_mod),
    "cargo.toml": ("rust", parse_cargo),
    "pom.xml": ("java", parse_pom_xml),
    "vcpkg.json": ("cpp", parse_vcpkg_json),
    "environment.yml": ("conda", parse_conda_environment),
    "environment.yaml": ("conda", parse_conda_environment),
    "setup.py": ("python", parse_setup_py),
    "setup.cfg": ("python", parse_setup_cfg),
    "pipfile": ("python", parse_pipfile),
}

_MANIFEST_SUFFIX_PARSERS: tuple[tuple[str, str, Callable[[bytes], Inventory]], ...] = (
    (".gemspec", "ruby", parse_gemspec),
    (".gradle", "java", parse_gradle),
    (".gradle.kts", "java", parse_gradle),
)


def ecosystem_parser(path: str) -> tuple[str, Callable[[bytes], Inventory]] | None:
    """Return (language, parser) for the ecosystems handled by _walk_inventory.

    The Python and package-lock.json paths are deliberately absent: those go
    through the older per-filename walkers that also enrich from PyPI, and
    parsing them twice would double every row.
    """
    if "node_modules/" in path:
        return None
    base_low = _basename(path).lower()
    entry = _LOCK_PARSERS.get(base_low) or _MANIFEST_PARSERS.get(base_low)
    if entry:
        return entry
    for suffix, language, parser in _MANIFEST_SUFFIX_PARSERS:
        if base_low.endswith(suffix):
            return language, parser
    if base_low.startswith("conanfile."):
        return "cpp", parse_conanfile
    return None


def _lock_selector(path: str) -> Callable[[bytes], Inventory] | None:
    entry = ecosystem_parser(path)
    if entry and is_dependency_file(path) == KIND_LOCKFILE:
        return entry[1]
    return None


def _python_manifest_selector(path: str) -> Callable[[bytes], Inventory] | None:
    """requirements*.txt and pyproject.toml, which `ecosystem_parser` does not hold.

    main() inventories these through `requirements_inventory` /
    `pyproject_inventory`, which merge every file into one {name: path} map and
    therefore keep a single path per package name. That is fine for the diff —
    it only needs the name — but it destroys the per-module attribution the
    use-side pass depends on: `httpx` declared by both services/alert and
    services/rtvi/rt-embed ends up credited to one of them, and the other is
    reported as using it undeclared. Re-walk them per path here.
    """
    base_low = _basename(path).lower()
    if base_low == "pyproject.toml":
        return parse_pyproject
    if base_low.startswith("requirements") and base_low.endswith(".txt"):
        if "requirements_apt" in base_low:
            return None
        return parse_requirements
    return None


def _python_lock_selector(path: str) -> Callable[[bytes], Inventory] | None:
    """The Python lockfiles that predate `ecosystem_parser`.

    `python_inventory` reaches these by filename, so they are absent from the
    ecosystem table and a selector-driven walk would miss every one of them —
    which for the use-side pass means every Python service looks undeclared.
    """
    base = _basename(path).lower()
    for filename, parser_fn in PYTHON_LOCKS:
        if base == filename.lower():
            return parser_fn
    return None


def _node_lock_selector(path: str) -> Callable[[bytes], Inventory] | None:
    """package-lock.json, reached by filename by `_inventory_at_ref`."""
    if _basename(path).lower() == "package-lock.json":
        return parse_node_lock
    return None


def _manifest_selector(path: str) -> Callable[[bytes], Inventory] | None:
    entry = ecosystem_parser(path)
    if entry and is_dependency_file(path) == KIND_MANIFEST:
        return entry[1]
    return None


def _by_language(
    ref: str,
    paths: list[str],
    selector: Callable[[str], Callable[[bytes], Inventory] | None],
    *,
    errors: list[tuple[str, str]] | None = None,
) -> dict[str, Inventory]:
    """Group `_walk_inventory` results per language.

    Diffs must not cross languages: a Go module and an npm package can share a
    name, and merging them would report a phantom version change.
    """
    grouped: dict[str, Inventory] = {}
    for path in paths:
        entry = ecosystem_parser(path)
        if entry is None or selector(path) is None:
            continue
        language = entry[0]
        one = _walk_inventory(ref, [path], selector, errors=errors)
        target = grouped.setdefault(language, {})
        for key, meta in one.items():
            target.setdefault(key, meta)
    return grouped


# ---------------------------------------------------------------------------
# Sibling modules (agents B and C)
# ---------------------------------------------------------------------------


def _load_sibling_module(name: str):
    """Import a sibling scanner module, or return None and say so.

    osrb_sources and osrb_usage are separate files owned by separate changes.
    If one is missing or fails to import, the right outcome is a smaller scan
    with a loud annotation, NOT a crashed job: a crash takes the declaration
    side down with it and leaves every PR in the repo unprotected. The gap is
    annotated rather than silently absorbed so the reduced coverage is visible
    on the PR that runs under it.
    """
    directory = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(directory, f"{name}.py")
    if not os.path.isfile(path):
        _annotate(
            "OSRB scan module missing",
            f"{name}.py is not present next to osrb_scan.py; its half of the "
            f"dependency inventory did not run for this PR.",
        )
        return None
    if directory not in sys.path:
        sys.path.insert(0, directory)
    # The sibling imports osrb_scan for make_row/license_risk/owning_module.
    # Register the already-running copy so it binds to this module's state
    # rather than executing a second, divergent copy of this file.
    self_module = sys.modules.get(__name__)
    if self_module is not None:
        sys.modules.setdefault("osrb_scan", self_module)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        _annotate("OSRB scan module missing", f"cannot load {name}.py")
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - a broken sibling must not crash the gate
        sys.modules.pop(name, None)
        _annotate(
            "OSRB scan module failed to load",
            f"{name}.py raised {type(exc).__name__}: {exc}; its half of the "
            f"dependency inventory did not run for this PR.",
        )
        return None
    return module


def _source_identity(row: dict[str, str]) -> tuple[str, str, str]:
    """Identity of a source-side row, ignoring its version.

    Keyed off the published row columns rather than osrb_sources' internal
    dict key, so a change to that module's bookkeeping cannot silently turn
    one image tag bump into an unrelated add/remove pair here.
    """
    return (row.get("source_kind", ""), row.get("module", ""), row.get("package", ""))


def _row_version(row: dict[str, str]) -> str:
    return row.get("new_version", "") or row.get("old_version", "")


def diff_source_rows(
    base_rows: list[dict[str, str]], head_rows: list[dict[str, str]]
) -> list[dict[str, str]]:
    """Diff two sets of osrb_sources rows into added / removed / updated.

    Same identity rule as the package diff: an entry that exists on both sides
    at the same version is unchanged and produces nothing, so an unchanged
    Dockerfile cannot fail a PR that never touched it.
    """
    # UNCOVERED_SOURCE rows are not package rows and must not be diffed. They
    # are what osrb_sources emits when a parser RAISES on a file, so they carry
    # a path where a package name would go. Feeding them through the identity
    # diff rewrote `change` to added/removed/updated: a Dockerfile the parser
    # choked on was filed as a NEW DEPENDENCY needing an OSRB approval that no
    # approval could clear, its real dependencies flipped to `removed` so the
    # reviewer read that the image had lost four packages, and uncovered_rows
    # stayed 0 so the coverage-gap error never fired.
    #
    # Pass through the ones present only at head (a gap this PR introduced);
    # drop the ones present at both refs, which are pre-existing and not this
    # PR's to answer for.
    def _is_uncovered(row: dict[str, str]) -> bool:
        return row.get("change", "").strip().upper() == CHANGE_UNCOVERED_SOURCE

    base_uncovered = {r.get("source_file", "") for r in base_rows if _is_uncovered(r)}
    passthrough = [
        r for r in head_rows
        if _is_uncovered(r) and r.get("source_file", "") not in base_uncovered
    ]
    base_rows = [r for r in base_rows if not _is_uncovered(r)]
    head_rows = [r for r in head_rows if not _is_uncovered(r)]

    base_by_identity: dict[tuple[str, str, str], dict[str, dict[str, str]]] = {}
    head_by_identity: dict[tuple[str, str, str], dict[str, dict[str, str]]] = {}
    for row in base_rows:
        base_by_identity.setdefault(_source_identity(row), {})[_row_version(row)] = row
    for row in head_rows:
        head_by_identity.setdefault(_source_identity(row), {})[_row_version(row)] = row

    rows: list[dict[str, str]] = list(passthrough)
    for identity in sorted(set(base_by_identity) | set(head_by_identity)):
        base_versions = base_by_identity.get(identity, {})
        head_versions = head_by_identity.get(identity, {})
        only_old = sorted(set(base_versions) - set(head_versions))
        only_new = sorted(set(head_versions) - set(base_versions))
        if not only_old and not only_new:
            continue
        if only_old and only_new:
            template = head_versions[only_new[0]]
            rows.append(normalize_row({
                **template,
                "change": CHANGE_UPDATED,
                "old_version": ",".join(only_old),
                "new_version": ",".join(only_new),
                "old_license": base_versions[only_old[0]].get("new_license", ""),
            }))
            continue
        for version in only_new:
            rows.append(normalize_row({**head_versions[version], "change": CHANGE_ADDED}))
        for version in only_old:
            template = base_versions[version]
            rows.append(normalize_row({
                **template,
                "change": CHANGE_REMOVED,
                "old_version": version,
                "new_version": "",
                "old_license": template.get("new_license", ""),
                "new_license": "",
            }))
    return rows


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

# Each (filename, parser) is scanned recursively across the whole repo tree
# at the given ref (_list_lockfiles uses `git ls-tree -r`), so lockfiles at
# any nesting depth — services/<svc>/..., tools/<tool>/..., or the repo
# root — are all picked up. Python deps may be locked by uv (uv.lock),
# pipenv (Pipfile.lock), PDM (pdm.lock), or Poetry (poetry.lock).
PYTHON_LOCKS: list[tuple[str, Callable[[bytes], Inventory]]] = [
    ("uv.lock", parse_uv_lock),
    ("Pipfile.lock", parse_pipfile_lock),
    ("pdm.lock", parse_pdm_lock),
    ("poetry.lock", parse_poetry_lock),
]


def python_inventory(
    ref: str, *, errors: list[tuple[str, str]] | None = None
) -> Inventory:
    merged: Inventory = {}
    for filename, parser_fn in PYTHON_LOCKS:
        for key, meta in _inventory_at_ref(ref, filename, parser_fn, errors=errors).items():
            merged.setdefault(key, meta)
    return merged


def declared_by_module(inventories: list[Inventory]) -> dict[str, set[str]]:
    """Map owning module -> declared package names, for the use-side pass.

    osrb_usage needs to answer "is this import declared anywhere the importing
    file's component can see". Handing it a flat set instead would make every
    dependency of every service look declared for every other service, which
    is the same as not checking.

    DO NOT build the use-side declared set from this function alone. An
    `Inventory` is keyed on (name, version) and merged with `setdefault`, so a
    package locked by three services keeps only the first lockfile's path and
    the other two modules lose their claim to it. Use
    `declared_names_by_module()`, which re-walks the manifests per path.
    This function remains for callers that already hold a single-source
    inventory.
    """
    declared: dict[str, set[str]] = {}
    for inventory in inventories:
        for (name, _version), meta in inventory.items():
            module = owning_module(meta.get("source_file", ""))
            declared.setdefault(module, set()).add(name)
    return declared


def declared_names_by_module(
    ref: str,
    paths: list[str],
    selectors: list[Callable[[str], Callable[[bytes], Inventory] | None]],
) -> dict[str, set[str]]:
    """Map owning module -> every package name declared by a manifest in it.

    Deliberately does NOT merge across paths. The version-keyed `Inventory`
    that the diff is built from collapses duplicates: if `httpx==0.28.1` is
    locked by services/alert, services/rtvi/rt-vlm and services/agent, the
    merged inventory keeps one `source_file` and the other two modules look as
    though they never declared it. Feeding that to the use-side pass made 70 of
    147 USED_UNDECLARED rows false — 23 of rt-vlm's 37 were names sitting in
    its own pdm.lock — which is the rate at which reviewers stop reading a
    section. Here each manifest is attributed to its own module and nothing is
    deduplicated, so being declared anywhere in the module is enough.

    Parse failures are ignored rather than collected: this feeds an advisory
    report, and a manifest that fails to parse is already reported as
    UNCOVERED_SOURCE by the diff path. Failing closed here would turn one
    unparseable lockfile into a wave of false "undeclared" rows.
    """
    declared: dict[str, set[str]] = {}
    for path in paths:
        parser = next(
            (fn for fn in (sel(path) for sel in selectors) if fn is not None), None
        )
        if parser is None:
            continue
        data = _git_show(ref, path)
        if data is None:
            continue
        try:
            parsed = parser(data)
        except Exception:  # noqa: BLE001 - see docstring
            continue
        module = owning_module(path)
        bucket = declared.setdefault(module, set())
        # Two shapes in play: the lockfile parsers return an Inventory keyed on
        # (name, version) tuples, while parse_requirements / parse_pyproject
        # return {name: version}. Unpacking a str key as a 2-tuple would
        # silently succeed for two-character names and raise for the rest, so
        # branch on the key rather than trusting the caller to pass one shape.
        for key in parsed:
            bucket.add(key[0] if isinstance(key, tuple) else key)
    return declared


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-ref", required=True, help="Git ref to diff against.")
    parser.add_argument("--head-ref", default="HEAD", help="Git ref under review.")
    parser.add_argument("--output", default="license-diff.csv", help="CSV output path.")
    parser.add_argument(
        "--skip-usage",
        action="store_true",
        help="Skip the report-only use-side pass (osrb_usage).",
    )
    args = parser.parse_args()

    _log(f"Comparing {args.base_ref} -> {args.head_ref}")
    base_paths = _ls_tree(args.base_ref)
    head_paths = _ls_tree(args.head_ref)
    rows: list[dict[str, str]] = []
    head_errors: list[tuple[str, str]] = []

    # --- Python: lockfiles first, then the name-level manifest passes -------
    py_base = python_inventory(args.base_ref)
    py_head = python_inventory(args.head_ref, errors=head_errors)
    nd_base = _inventory_at_ref(args.base_ref, "package-lock.json", parse_node_lock)
    nd_head = _inventory_at_ref(
        args.head_ref, "package-lock.json", parse_node_lock, errors=head_errors
    )
    node_workspace_base = node_workspace_names_by_module(args.base_ref, base_paths)
    node_workspace_head = node_workspace_names_by_module(args.head_ref, head_paths)

    rows.extend(diff_language("python", py_base, py_head))
    rows.extend(diff_language("node", nd_base, nd_head))

    # Minimal manifest coverage: catch direct deps added to (or removed from)
    # plain requirements.txt / pyproject.toml files that have no recognized
    # lockfile. Deduped against names already in the lockfile inventory, which
    # the diff above covers more accurately (resolved version + transitive
    # closure). pyproject.toml is also deduped against requirements.txt so a
    # package declared in both does not produce two rows.
    lock_names = _names(py_base) | _names(py_head)
    req_base_sources: dict[str, str] = {}
    req_head_sources: dict[str, str] = {}
    req_base = requirements_inventory(args.base_ref, sources=req_base_sources)
    req_head = requirements_inventory(args.head_ref, sources=req_head_sources)
    rows.extend(diff_requirements(
        req_base, req_head, lock_names,
        base_sources=req_base_sources, head_sources=req_head_sources,
    ))
    direct_covered = lock_names | set(req_base) | set(req_head)
    pj_base_sources: dict[str, str] = {}
    pj_head_sources: dict[str, str] = {}
    pj_base = pyproject_inventory(args.base_ref, sources=pj_base_sources)
    pj_head = pyproject_inventory(args.head_ref, sources=pj_head_sources)
    rows.extend(diff_requirements(
        pj_base, pj_head, direct_covered, source="pyproject.toml",
        base_sources=pj_base_sources, head_sources=pj_head_sources,
    ))

    # --- Every other ecosystem: locks, then manifests deduped against them --
    lock_base = _by_language(args.base_ref, base_paths, _lock_selector)
    lock_head = _by_language(args.head_ref, head_paths, _lock_selector, errors=head_errors)
    manifest_base = _by_language(args.base_ref, base_paths, _manifest_selector)
    manifest_head = _by_language(
        args.head_ref, head_paths, _manifest_selector, errors=head_errors
    )

    # package-lock.json is walked by the older Node path above, but its names
    # still have to suppress package.json manifest rows for the same packages.
    covered_by_language: dict[str, set[str]] = {"node": _names(nd_base) | _names(nd_head)}
    for language in set(lock_base) | set(lock_head):
        covered_by_language.setdefault(language, set())
        covered_by_language[language] |= _names(lock_base.get(language, {}))
        covered_by_language[language] |= _names(lock_head.get(language, {}))
        rows.extend(
            diff_language(language, lock_base.get(language, {}), lock_head.get(language, {}))
        )

    python_manifest_covered = direct_covered
    for language in sorted(set(manifest_base) | set(manifest_head)):
        covered = covered_by_language.get(language, set())
        base_manifest = manifest_base.get(language, {})
        head_manifest = manifest_head.get(language, {})
        if language == "node":
            base_manifest = _drop_local_node_workspace_rows(
                base_manifest, node_workspace_base
            )
            head_manifest = _drop_local_node_workspace_rows(
                head_manifest, node_workspace_head
            )
        if language == "python":
            # setup.py / setup.cfg / Pipfile add to the same namespace the
            # requirements.txt and pyproject.toml passes already filled.
            covered = covered | python_manifest_covered
        base_inventory = _drop_covered(base_manifest, covered)
        head_inventory = _drop_covered(head_manifest, covered)
        if language == "python":
            rows.extend(diff_requirements(
                _flatten(base_inventory),
                _flatten(head_inventory),
                set(),
                source="setup.py/setup.cfg/Pipfile",
                base_sources=_flatten_sources(base_inventory),
                head_sources=_flatten_sources(head_inventory),
            ))
            continue
        rows.extend(diff_language(language, base_inventory, head_inventory))

    # --- Source-side inventory (agent B) ------------------------------------
    sources_module = _load_sibling_module("osrb_sources")
    source_head_rows: list[dict[str, str]] = []
    source_failure: str | None = None
    if sources_module is None:
        source_failure = "osrb_sources.py is missing or could not be imported"
    else:
        try:
            base_source_rows = list(
                sources_module.inventory_at_ref(args.base_ref, _git_show, base_paths).values()
            )
            source_head_rows = list(
                sources_module.inventory_at_ref(args.head_ref, _git_show, head_paths).values()
            )
            rows.extend(diff_source_rows(base_source_rows, source_head_rows))
        except Exception as exc:  # noqa: BLE001 - reported as a coverage gap below
            source_failure = f"osrb_sources raised {type(exc).__name__}: {exc}"

    if source_failure is not None:
        # FAIL CLOSED. Degrading to a warning here would drop the entire
        # container / compose / chart / CMake / CI inventory - the classes that
        # carry the AGPL, Elastic-2.0 and GPL findings - and still exit 0 with
        # "No changes require OSRB re-engagement", while the private pipeline
        # downloaded a CSV that had silently lost them. One bad rebase or a
        # syntax error in a sibling file is enough. `is_parsed()` returns True
        # for these kinds unconditionally, so `uncovered_dependency_files()`
        # cannot notice on its own; the rows have to be synthesised here.
        #
        # The use-side pass below is deliberately different and stays advisory:
        # it is report-only by decision, so losing it costs no coverage claim.
        affected = sorted(
            path for path in head_paths
            if (is_dependency_file(path) or "") in _KINDS_PARSED_BY_SOURCES
        )
        rows.extend(
            uncovered_source_rows(
                affected,
                reason=(
                    "not inventoried: the source-side scanner was unavailable "
                    f"({source_failure}). This is a scanner fault, not a "
                    "dependency change; it needs a fix to osrb_sources.py, not "
                    "an OSRB approval."
                ),
            )
        )
        _annotate(
            "OSRB source-side scan failed",
            f"{source_failure}; {len(affected)} container/compose/chart/CMake/CI "
            "file(s) were not inventoried. Failing the scan rather than "
            "reporting an incomplete dependency list as complete.",
            level="error",
        )

    # --- Use-side inventory (agent C) — report only -------------------------
    if not args.skip_usage:
        usage_module = _load_sibling_module("osrb_usage")
        if usage_module is not None:
            try:
                # Per-manifest, NOT from the merged inventories: those are
                # keyed on (name, version) and collapse a package declared by
                # several services down to one source_file, which made ~half
                # of the USED_UNDECLARED rows false. See
                # declared_names_by_module's docstring.
                declared = declared_names_by_module(
                    args.head_ref,
                    head_paths,
                    [
                        _lock_selector,
                        _manifest_selector,
                        _python_lock_selector,
                        _node_lock_selector,
                        _python_manifest_selector,
                    ],
                )
                for name, path in req_head_sources.items():
                    declared.setdefault(owning_module(path), set()).add(name)
                for name, path in pj_head_sources.items():
                    declared.setdefault(owning_module(path), set()).add(name)
                usage_rows = usage_module.undeclared(
                    args.head_ref, _git_show, head_paths, declared
                )
                # Report-only by owner decision: the use side infers a
                # dependency from an import rather than reading a declaration,
                # so a false positive must never be able to block a PR.
                rows.extend(
                    normalize_row({**row, "change": CHANGE_USED_UNDECLARED})
                    for row in usage_rows
                )
            except Exception as exc:  # noqa: BLE001 - report-only must never break the gate
                _annotate(
                    "OSRB use-side scan failed",
                    f"osrb_usage raised {type(exc).__name__}: {exc}; the "
                    f"report-only undeclared-import pass was skipped.",
                )

    # --- Coverage gaps: the loud half --------------------------------------
    uncovered = uncovered_dependency_files(base_paths, head_paths)
    for path in uncovered:
        _annotate(
            "OSRB scanner coverage gap",
            f"{path} carries third-party dependencies that osrb_scan.py cannot parse",
        )
    rows.extend(uncovered_source_rows(uncovered))
    # A file we claim to parse but could not is the same failure wearing a
    # disguise; report it with the reason so the fix is obvious.
    already_reported = set(uncovered)
    rows.extend(
        row
        for path, reason in head_errors
        if path not in already_reported
        for row in uncovered_source_rows([path], reason=f"parse failed: {reason}")
    )

    rows = [normalize_row(row) for row in rows]
    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=ROW_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    _log(f"Wrote {len(rows)} diff rows to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
