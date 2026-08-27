#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Compare the repo's dependency inventory against the OSRB-approved baseline.

The scan side of this pipeline (``osrb_scan.py`` and friends) is a DELTA: it
diffs base-ref against head-ref, so it sees only what a pull request changes.
That is the right shape for a merge gate and the wrong shape for a release
question. Everything already sitting in the tree unreviewed produces an empty
diff, and an empty diff reads exactly like a clean repo. This file is the
STATE half: regenerate the whole inventory, put it next to what OSRB actually
approved, and report the difference.

Why usage is a first-class axis
-------------------------------
OSRB does not approve a package. It approves a package *for a particular use*.
The approved sheet carries ``distribution_method``, ``usage_method`` and
``vendored`` for exactly that reason: the same library dynamically linked
against a distro package is a different approval from the same library
vendored into our tree and modified, and a different approval again from one
that only ever runs in CI. So a row can match on package, version and licence
and still be a finding, because the approval on file is for a use we are no
longer making. ``USAGE_DRIFT`` is that finding, and it is the reason this
file exists.

Why the usage check is deliberately timid
-----------------------------------------
It is also the finding most easily turned into noise, and a noisy column gets
the whole report ignored -- which costs more than the check earns. So the
comparison runs off an explicit table (``USAGE_CONFLICTS``) of pairs that
genuinely contradict, and reports nothing at all when either side is silent:

* approved ``usage_method`` blank, ``0.0``, or "Other (Please describe in
  Comments)" -> nothing to compare against, so the row is APPROVED and the
  notes say why. "Other" is a pointer to prose in the comments column; a
  machine cannot read it, and guessing at it is how a reviewer learns to
  distrust the column.
* evidence we could not classify -> same treatment. An unrecognised evidence
  token never contradicts anything.

Only a contradiction between two *known* values is reported: vendored source
committed in the tree where the sheet says the package is not vendored, a
package shipping in a published image where the sheet says test-time only or
build-time only, static linking where the sheet says dynamic.

Data quality in the approved sheet
----------------------------------
The baseline is an export of a human-maintained spreadsheet and shows it. Six
rows carry the literal string ``0.0`` in ``usage_method`` (a spreadsheet
filling an empty cell with a number), and "Pre-installed inside the container"
is spelled two ways. Licences arrive as "Apache 2.0", "Apache-2.0",
"APACHE-2.0" and "Apache License" for one licence, and "BSD (any variant)" for
a family. All of it is normalised before comparison, and every normalisation
that actually changed a value is counted and printed in the summary -- a
silent normaliser is indistinguishable from a bug that drops findings.

Precedence
----------
One verdict per inventory row, at the first gate that fails, in this order:
module -> package -> version -> licence -> usage. A package in an unsubmitted
module is reported as MODULE_UNSUBMITTED and not also as NOT_APPROVED, because
the two send the reader to different places: file a new OSRB bug, versus chase
the existing one for a module already under review.

Usage:
    python3 .github/osrb/osrb_compare.py \\
        --inventory .github/osrb/inventory.csv \\
        --approved .github/osrb/approved.csv \\
        --output osrb-compliance.csv \\
        --summary osrb-compliance.md \\
        [--github-output "$GITHUB_OUTPUT"]
"""

from __future__ import annotations

import argparse
import collections
import pathlib
import csv
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from module_map import (  # noqa: E402
    SUBMITTED_MODULES,
    UNSUBMITTED_SET,
    check_repo_modules_column,
    is_unsubmitted,
    split_repo_modules,
    unmapped_osrb_modules,
)
from osrb_scan import license_risk  # noqa: E402

# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------

VERDICT_APPROVED = "APPROVED"
VERDICT_VERSION_DRIFT = "VERSION_DRIFT"
VERDICT_LICENSE_DRIFT = "LICENSE_DRIFT"
VERDICT_USAGE_DRIFT = "USAGE_DRIFT"
VERDICT_NOT_APPROVED = "NOT_APPROVED"
VERDICT_MODULE_UNSUBMITTED = "MODULE_UNSUBMITTED"
VERDICT_APPROVED_NOT_PRESENT = "APPROVED_NOT_PRESENT"

# Report order, worst first. Also the order counts appear in the summary.
VERDICTS = [
    VERDICT_NOT_APPROVED,
    VERDICT_MODULE_UNSUBMITTED,
    VERDICT_VERSION_DRIFT,
    VERDICT_LICENSE_DRIFT,
    VERDICT_USAGE_DRIFT,
    VERDICT_APPROVED_NOT_PRESENT,
    VERDICT_APPROVED,
]

# Verdicts that mean "somebody has to do something about this package". The
# two that are left out are not findings: APPROVED is the happy path, and
# APPROVED_NOT_PRESENT is a stale row in the sheet, which is a tidy-up for the
# next submission and never a reason to hold a release.
FINDING_VERDICTS = frozenset(
    {
        VERDICT_NOT_APPROVED,
        VERDICT_MODULE_UNSUBMITTED,
        VERDICT_VERSION_DRIFT,
        VERDICT_LICENSE_DRIFT,
        VERDICT_USAGE_DRIFT,
    }
)

# ---------------------------------------------------------------------------
# Output shape
# ---------------------------------------------------------------------------

# One row per finding. This is a published surface the moment a workflow
# uploads it: append new columns at the end, never reorder or rename.
OUTPUT_FIELDS = [
    "verdict",
    "module",
    "language",
    "package",
    "version",
    "license",
    "risk",
    "usage_evidence",
    "approved_module",
    "approved_version",
    "approved_license",
    "approved_vendored",
    "approved_distribution_method",
    "approved_usage_method",
    "source_kind",
    "source_file",
    "notes",
]

# ---------------------------------------------------------------------------
# Reading the two CSVs
# ---------------------------------------------------------------------------

# The inventory is produced by a sibling script in this same directory, but the
# two are edited independently and a column rename in one must not silently
# blank a column in the other. Accepted spellings are listed once, here, and a
# missing required column raises rather than producing an empty comparison
# where every row reads NOT_APPROVED.
INVENTORY_ALIASES = {
    "package": ("package",),
    "version": ("version", "new_version"),
    "license": ("license", "new_license"),
    "module": ("module",),
    "language": ("language",),
    "usage_evidence": ("usage_evidence", "evidence"),
    "source_kind": ("source_kind",),
    "source_file": ("source_file",),
    "risk": ("risk",),
    # Optional, and read when present because they answer the usage question
    # directly instead of by inference from a path. An inventory that does not
    # emit them simply contributes no evidence from them.
    "dep_scope": ("dep_scope",),
    "vendored_in_repo": ("vendored_in_repo",),
    "container_only": ("container_only",),
}

REQUIRED_INVENTORY_COLUMNS = ("package", "version", "module")

APPROVED_REQUIRED_COLUMNS = (
    "package",
    "version",
    "license",
    "module",
    "vendored",
    "distribution_method",
    "usage_method",
)


class InventoryError(ValueError):
    """The inventory CSV cannot be compared -- fail loudly, never silently."""


def _resolve_columns(header: list[str]) -> dict[str, str]:
    """Map our field names onto whatever this CSV actually calls them."""
    present = {name: name for name in header}
    resolved = {}
    for field, spellings in INVENTORY_ALIASES.items():
        for spelling in spellings:
            if spelling in present:
                resolved[field] = spelling
                break
    missing = [name for name in REQUIRED_INVENTORY_COLUMNS if name not in resolved]
    if missing:
        raise InventoryError(
            f"inventory CSV is missing required column(s): {', '.join(missing)}; "
            f"found {', '.join(header)}"
        )
    return resolved


def load_inventory(path: str | Path) -> list[dict[str, str]]:
    """Read the inventory into our own field names, dropping unusable rows.

    A row with no package name is dropped rather than compared: it can only
    ever be spurious, and one spurious NOT_APPROVED in the report is enough to
    make a reviewer stop trusting the rest of it.
    """
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise InventoryError(f"{path}: empty inventory CSV (no header row)")
        columns = _resolve_columns(list(reader.fieldnames))
        rows = []
        for raw in reader:
            row = {
                field: (raw.get(source) or "").strip()
                for field, source in columns.items()
            }
            for field in INVENTORY_ALIASES:
                row.setdefault(field, "")
            if not row["package"]:
                continue
            rows.append(row)
    return rows


def load_approved(path: str | Path) -> list[dict[str, str]]:
    """Read the approved baseline, checking the columns we depend on exist."""
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise InventoryError(f"{path}: empty approved CSV (no header row)")
        missing = [
            name for name in APPROVED_REQUIRED_COLUMNS if name not in reader.fieldnames
        ]
        if missing:
            raise InventoryError(
                f"{path}: approved CSV is missing column(s): {', '.join(missing)}"
            )
        return [
            {key: (value or "").strip() for key, value in row.items() if key}
            for row in reader
        ]


# ---------------------------------------------------------------------------
# Normalisation -- package, version, licence
# ---------------------------------------------------------------------------

_SEPARATOR_RUN = re.compile(r"[-_.]+")


def canonical_package(name: str) -> str:
    """Fold the spellings of one package name together.

    PEP 503's rule (case-insensitive, ``-``/``_``/``.`` equivalent) applied to
    every ecosystem, because the sheet has no language column to switch on and
    npm and Debian names survive it unchanged. It does mean ``a.b`` and ``a-b``
    collide; nothing in the current baseline does, and the alternative --
    treating ``ruamel.yaml`` and ``ruamel-yaml`` as unrelated packages -- is
    the failure that actually happens.
    """
    return _SEPARATOR_RUN.sub("-", name.strip().lower()).strip("-")


def canonical_version(version: str) -> str:
    """Fold ``v1.2.3``, ``==1.2.3`` and ``1.2.3`` together, and nothing else.

    Deliberately not a semver comparison. Distro versions in this baseline
    (``1.24.2-1ubuntu1.1``, ``2.3.3-3build2``) do not parse as semver, and a
    parser that silently treated an unparseable version as equal would hide
    exactly the drift this is looking for. ``==`` is stripped because a
    manifest pin and a lockfile resolution of the same version are the same
    fact written twice.
    """
    text = version.strip()
    if text.startswith("=="):
        text = text[2:].strip()
    if len(text) > 1 and text[0] in "vV" and text[1].isdigit():
        text = text[1:]
    return text


_RANGE_SPEC = re.compile(r"[><~^*,|!]|\s-\s|\bor\b")

# Placeholders an inventory writes when it could not resolve a version. They
# are not versions, and comparing them literally would turn every unresolved
# row into VERSION_DRIFT against a version nobody claimed had changed -- the
# real inventory writes UNKNOWN on 300+ rows, mostly vendored C trees and
# container images with no readable version at all.
_VERSION_PLACEHOLDERS = frozenset(
    {"unknown", "unspecified", "n/a", "na", "none", "null", "tbd", "-", "latest", "?"}
)


def is_comparable_version(version: str) -> bool:
    """True when this cell names one version rather than a range.

    Manifests carry ranges (``httpx>=0.27.0``, ``^1.2.0``), and the scan side
    records the literal committed spec on purpose. A range cannot equal an
    approved version string, so comparing it would report VERSION_DRIFT for
    every manifest row in the repo -- hundreds of findings that say nothing
    except that a manifest is a manifest. An uncomparable version stops at the
    package gate instead: still NOT_APPROVED if OSRB never saw the package,
    but never accused of drifting to a version we did not read.
    """
    text = canonical_version(version)
    if not text or text.lower() in _VERSION_PLACEHOLDERS:
        return False
    return not _RANGE_SPEC.search(text)


# Licence family tokens. The point is not SPDX fidelity -- it is deciding
# whether the sheet and the scanner are talking about the same licence. Order
# matters: the first pattern that matches wins, so LGPL/AGPL are tested before
# the bare GPL pattern, or every LGPL row in the sheet reads as GPL drift.
_LICENSE_FAMILIES: list[tuple[str, re.Pattern[str]]] = [
    ("AGPL", re.compile(r"\bagpl")),
    # The spelling in the baseline is "LGPL (Library or Lesser GPL)", so the
    # alternation has to swallow the trailing "GPL" too. Leaving it behind is
    # how all 281 LGPL rows would come back out of here as GPL.
    ("LGPL", re.compile(r"\blgpl|(?:library\s+or\s+)?lesser\s+gpl")),
    ("GPL", re.compile(r"\bgpl|gnu\s+public|general\s+public")),
    ("APACHE", re.compile(r"\bapache")),
    ("MIT", re.compile(r"\bmit\b|\bexpat\b")),
    ("BSD", re.compile(r"\bbsd\b")),
    ("ISC", re.compile(r"\bisc\b|internet\s+software\s+consortium")),
    ("MPL", re.compile(r"\bmpl\b|mozilla\s+public")),
    ("EPL", re.compile(r"\bepl\b|eclipse\s+public")),
    ("PSF", re.compile(r"\bpsf\b|python\s+software\s+foundation")),
    ("ZLIB", re.compile(r"\bzlib\b")),
    ("CC0", re.compile(r"\bcc0\b|public\s+domain")),
    ("UNLICENSE", re.compile(r"\bunlicense\b")),
    ("ARTISTIC", re.compile(r"\bartistic\b")),
    ("CDDL", re.compile(r"\bcddl\b")),
    ("SSPL", re.compile(r"\bsspl\b")),
    ("ELASTIC", re.compile(r"\belastic[-\s]?2|elastic\s+license")),
    ("BSL", re.compile(r"\bbusl\b|\bbsl\b|business\s+source")),
    ("OFL", re.compile(r"\bofl\b|open\s+font")),
    ("PROPRIETARY", re.compile(r"proprietary|nvidia\s+software\s+license|commercial")),
]

# Values that mean "the sheet declined to say", not "a licence called this".
# They must never be compared: every one of them would otherwise be drift
# against every real licence, and there are 700+ of them in the baseline.
_LICENSE_INDETERMINATE = re.compile(
    r"^(|0\.0|n/?a|none|null|unknown|noassertion|other.*|more\s+than\s+one.*|see\s+.*)$"
)


def license_families(expr: str) -> frozenset[str]:
    """The licence families named in an expression, or empty when unreadable.

    Empty is the important return value: it is what "Other (Please describe in
    Comments)", "More than one license", ``0.0`` and a blank cell all produce,
    and an empty set never contradicts anything downstream.
    """
    text = expr.strip().replace("\xa0", " ").lower()
    if _LICENSE_INDETERMINATE.match(text):
        return frozenset()
    found = set()
    for name, pattern in _LICENSE_FAMILIES:
        if pattern.search(text):
            found.add(name)
            # LGPL and AGPL both contain "gpl"; once matched, stop the bare
            # GPL pattern from adding a second, wrong family for the same text.
            text = pattern.sub(" ", text)
    return frozenset(found)


def licenses_compatible(found: str, approved: str) -> bool:
    """True unless both sides name licence families and they are disjoint.

    "Apache 2.0" against "Apache-2.0" is the same licence spelled two ways.
    "BSD (any variant)" against "BSD-3-Clause" is the sheet recording a family
    where the scanner resolved a member. Neither is drift. "MIT" against
    "GPL-3.0" is.
    """
    left, right = license_families(found), license_families(approved)
    if not left or not right:
        return True
    return bool(left & right)


# ---------------------------------------------------------------------------
# Normalisation -- the three usage axes of the approved sheet
# ---------------------------------------------------------------------------

USAGE_UNSPECIFIED = "unspecified"
USAGE_OTHER = "other"
USAGE_DYNAMIC = "dynamic-linking"
USAGE_STATIC = "static-linking"
USAGE_PREINSTALLED = "preinstalled-in-container"
USAGE_BUILD_TIME = "build-time"
USAGE_OPERATOR_OPTIONAL = "operator-optional"

# Every literal `usage_method` in the baseline, folded onto the vocabulary
# above. "0.0" is a spreadsheet artefact for an empty cell and means exactly
# as much as blank does; "Pre installed inside of container" is the same
# answer as "Pre-installed inside the container" typed by a different person.
USAGE_METHOD_MAP = {
    "": USAGE_UNSPECIFIED,
    "0.0": USAGE_UNSPECIFIED,
    "n/a": USAGE_UNSPECIFIED,
    "dynamic linking": USAGE_DYNAMIC,
    "dynamically linked": USAGE_DYNAMIC,
    "static linking": USAGE_STATIC,
    "statically linked": USAGE_STATIC,
    "pre-installed inside the container": USAGE_PREINSTALLED,
    "pre installed inside of container": USAGE_PREINSTALLED,
    "pre-installed inside of container": USAGE_PREINSTALLED,
    "pre installed inside the container": USAGE_PREINSTALLED,
    "build-time dependency": USAGE_BUILD_TIME,
    "build time dependency": USAGE_BUILD_TIME,
    "optional operator-side installation only": USAGE_OPERATOR_OPTIONAL,
    "other (please describe in comments)": USAGE_OTHER,
    "other": USAGE_OTHER,
}

DIST_UNSPECIFIED = "unspecified"
DIST_CONTAINER = "container"
DIST_IMAGE = "image"
DIST_REFERENCED = "referenced"
DIST_CHART = "chart"
DIST_SOURCE = "source"
DIST_NOT_DISTRIBUTED = "not-distributed"

DISTRIBUTION_MAP = {
    "": DIST_UNSPECIFIED,
    "0.0": DIST_UNSPECIFIED,
    "n/a": DIST_UNSPECIFIED,
    "container": DIST_CONTAINER,
    "container - added to base container listed in row 1": DIST_CONTAINER,
    "container - added to base container listed in row 2": DIST_CONTAINER,
    "image": DIST_IMAGE,
    "chart": DIST_CHART,
    "referenced": DIST_REFERENCED,
    "referenced (container-only)": DIST_REFERENCED,
    "github source (npm)": DIST_SOURCE,
    "not distributed in the default nvidia image (removed during image build)": (
        DIST_NOT_DISTRIBUTED
    ),
}

VENDORED_UNSPECIFIED = "unspecified"
VENDORED_YES = "yes"
VENDORED_NO = "no"
VENDORED_TEST_ONLY = "no-test-time-only"
VENDORED_SAMPLE = "sample-only"

VENDORED_MAP = {
    "": VENDORED_UNSPECIFIED,
    "0.0": VENDORED_UNSPECIFIED,
    "n/a": VENDORED_UNSPECIFIED,
    "yes": VENDORED_YES,
    "yes, within container": VENDORED_YES,
    "yes, within pip wheel": VENDORED_YES,
    "yes, within container and pip wheel": VENDORED_YES,
    "yes, within jar": VENDORED_YES,
    "yes, within jar-gems": VENDORED_YES,
    "no": VENDORED_NO,
    "no, test-time only": VENDORED_TEST_ONLY,
    "no, test time only": VENDORED_TEST_ONLY,
    "provided as part of sample dockerfile": VENDORED_SAMPLE,
}


class Normalisations:
    """Counts every raw value a normaliser actually had to rewrite.

    Kept because a normaliser is a place findings go to disappear. If a future
    export spells "Dynamic Link" and it silently becomes ``unspecified``, the
    usage check quietly stops working on 1,400 rows and the report still looks
    healthy. The summary prints this table, so the change shows up as an
    ``unmapped`` line the first time it happens.
    """

    def __init__(self) -> None:
        self.applied: dict[str, collections.Counter[tuple[str, str]]] = (
            collections.defaultdict(collections.Counter)
        )
        self.unmapped: dict[str, collections.Counter[str]] = collections.defaultdict(
            collections.Counter
        )

    def record(self, axis: str, raw: str, normalised: str) -> None:
        if raw.strip() != normalised:
            self.applied[axis][(raw, normalised)] += 1

    def record_unmapped(self, axis: str, raw: str) -> None:
        self.unmapped[axis][raw] += 1

    def total(self) -> int:
        return sum(sum(counter.values()) for counter in self.applied.values())


def _normalise(
    axis: str,
    raw: str,
    table: dict[str, str],
    fallback: str,
    seen: Normalisations | None,
) -> str:
    """Table lookup with the unmapped case counted rather than swallowed."""
    key = " ".join(raw.strip().lower().split())
    if key in table:
        value = table[key]
    else:
        value = fallback
        if seen is not None and key:
            seen.record_unmapped(axis, raw.strip())
    if seen is not None:
        seen.record(axis, raw, value)
    return value


def normalise_usage_method(raw: str, seen: Normalisations | None = None) -> str:
    """Fold a sheet ``usage_method`` cell onto our vocabulary.

    An unrecognised value becomes ``other``, not ``unspecified``: both are
    inert for the comparison, but ``other`` is the honest label for "a human
    wrote something we cannot interpret".
    """
    return _normalise("usage_method", raw, USAGE_METHOD_MAP, USAGE_OTHER, seen)


def normalise_distribution(raw: str, seen: Normalisations | None = None) -> str:
    return _normalise(
        "distribution_method", raw, DISTRIBUTION_MAP, DIST_UNSPECIFIED, seen
    )


def normalise_vendored(raw: str, seen: Normalisations | None = None) -> str:
    return _normalise("vendored", raw, VENDORED_MAP, VENDORED_UNSPECIFIED, seen)


def normalisation_census(rows: list[dict[str, str]]) -> Normalisations:
    """Normalise every approved row's three usage axes once, and count it.

    Run over the whole sheet rather than only the rows a given inventory
    happens to touch, so the reported normalisation table describes the
    BASELINE and not this run. Otherwise the six ``0.0`` rows appear or vanish
    from the report depending on which services were scanned, and a reviewer
    cannot tell a fixed spreadsheet from an unscanned module.
    """
    seen = Normalisations()
    for row in rows:
        normalise_usage_method(row.get("usage_method", ""), seen)
        normalise_distribution(row.get("distribution_method", ""), seen)
        normalise_vendored(row.get("vendored", ""), seen)
    return seen


# ---------------------------------------------------------------------------
# Our side of the usage vocabulary
# ---------------------------------------------------------------------------

# What the scanner can honestly claim to have observed. Kept short on purpose:
# each token has to mean one checkable thing, because each one is an input to
# a contradiction rule that will accuse a human of shipping something they did
# not declare.
EV_SHIPPED_IN_CONTAINER = "shipped-in-container"  # installed into an image we publish
EV_CONTAINER_IMAGE = "container-image"  # pulled as a whole image at deploy time
EV_VENDORED_SOURCE = "vendored-source"  # third-party source committed in this tree
EV_STATIC_LINK = "static-link"  # linked into a binary we ship
EV_DYNAMIC_LINK = "dynamic-link"  # resolved at runtime from a shared library
EV_BUILD_ONLY = "build-only"  # runs during the build, never ships
EV_TEST_ONLY = "test-only"  # only reachable from test paths
EV_SOURCE_IMPORT = "source-import"  # first-party code imports it
EV_DECLARED = "declared"  # a manifest names it; shipping unknown

EVIDENCE_VOCABULARY = frozenset(
    {
        EV_SHIPPED_IN_CONTAINER,
        EV_CONTAINER_IMAGE,
        EV_VENDORED_SOURCE,
        EV_STATIC_LINK,
        EV_DYNAMIC_LINK,
        EV_BUILD_ONLY,
        EV_TEST_ONLY,
        EV_SOURCE_IMPORT,
        EV_DECLARED,
    }
)

_EVIDENCE_SPLIT = re.compile(r"[;,|]")

# The inventory names its evidence in its own words. This is the translation,
# and it is written out rather than inferred because an evidence token this
# file does not recognise is silently inert -- the usage check would appear to
# run and find nothing, which is the one failure mode a compliance report
# cannot afford. Adding a token to the inventory means adding a line here.
EVIDENCE_ALIASES = {
    "declared-manifest": (EV_DECLARED,),
    "declared-lockfile": (EV_DECLARED,),
    "container-apt": (EV_SHIPPED_IN_CONTAINER,),
    "container-pip": (EV_SHIPPED_IN_CONTAINER,),
    "container-npm": (EV_SHIPPED_IN_CONTAINER,),
    # A FROM line: the base image's contents ship inside the image we publish,
    # and the base image is also a referenced image in its own right.
    "container-base": (EV_SHIPPED_IN_CONTAINER, EV_CONTAINER_IMAGE),
    "container-image": (EV_CONTAINER_IMAGE,),
    "imported-only": (EV_SOURCE_IMPORT,),
    "source-import": (EV_SOURCE_IMPORT,),
    "vendored-source": (EV_VENDORED_SOURCE,),
    "ci-tooling": (EV_BUILD_ONLY,),
    "build-only": (EV_BUILD_ONLY,),
    "test-only": (EV_TEST_ONLY,),
    "static-link": (EV_STATIC_LINK,),
    "dynamic-link": (EV_DYNAMIC_LINK,),
    "declared": (EV_DECLARED,),
    # Deliberately inert: "downloaded during the build" says when the code
    # arrived, not whether it ships. The approved sheet tracks the same fact in
    # its own `downloaded_at_build` column, so guessing here would contradict
    # the sheet on a question the sheet already answers.
    "build-fetch": (),
}

# Fallback when the inventory carries no `usage_evidence` column: derive what
# the source_kind can support and nothing more. A lockfile entry becomes
# `declared`, NOT `shipped-in-container` -- a lockfile says the build resolved
# a package, not that the artefact ships it, and `opencv-python-headless` in
# the AGENT lock (resolved, then deleted during the image build) is the live
# example of why inferring the stronger claim would manufacture a finding.
SOURCE_KIND_EVIDENCE = {
    "lockfile": EV_DECLARED,
    "manifest": EV_DECLARED,
    "attribution": EV_DECLARED,
    "build": EV_DECLARED,
    "container": EV_SHIPPED_IN_CONTAINER,
    "compose": EV_CONTAINER_IMAGE,
    "chart": EV_CONTAINER_IMAGE,
    "ci": EV_BUILD_ONLY,
    "usage": EV_SOURCE_IMPORT,
}

_VENDORED_PATH = re.compile(
    r"(^|/)(3rdparty|3rd_party|third[-_]?party|vendor|vendored|external|externals)(/|$)",
    re.IGNORECASE,
)
_TEST_PATH = re.compile(r"(^|/)(tests?|testing|spec|specs|e2e|fixtures)(/|$)", re.I)


def parse_evidence(raw: str) -> set[str]:
    """Split a ``usage_evidence`` cell and translate it into our vocabulary.

    Unknown tokens are dropped rather than kept as opaque strings. A token no
    rule mentions can only ever be inert, and dropping it here means the
    contradiction table below is the complete list of things that can produce
    a USAGE_DRIFT -- readable in one screen, which is the property that keeps
    the check trustworthy. ``unknown_evidence()`` reports what was dropped so
    a vocabulary drift between the two files shows up as a line in the summary
    instead of as a check that quietly stopped finding anything.
    """
    tokens = set()
    for token in (part.strip().lower() for part in _EVIDENCE_SPLIT.split(raw)):
        if token in EVIDENCE_ALIASES:
            tokens.update(EVIDENCE_ALIASES[token])
        elif token in EVIDENCE_VOCABULARY:
            tokens.add(token)
    return tokens


def unknown_evidence(raw: str) -> set[str]:
    """Tokens in a ``usage_evidence`` cell that neither table knows."""
    return {
        token
        for token in (part.strip().lower() for part in _EVIDENCE_SPLIT.split(raw))
        if token and token not in EVIDENCE_ALIASES and token not in EVIDENCE_VOCABULARY
    }


def derive_evidence(row: dict[str, str]) -> set[str]:
    """Evidence tokens for an inventory row that carries none of its own."""
    tokens = set()
    kind = (row.get("source_kind") or "").strip().lower()
    if kind in SOURCE_KIND_EVIDENCE:
        tokens.add(SOURCE_KIND_EVIDENCE[kind])
    path = (row.get("source_file") or "").split("#", 1)[0]
    if path and _VENDORED_PATH.search(path):
        tokens.add(EV_VENDORED_SOURCE)
    if path and _TEST_PATH.search(path):
        # A test path downgrades the claim rather than adding to it: something
        # seen only under tests/ is not evidence that it ships.
        tokens.discard(EV_SHIPPED_IN_CONTAINER)
        tokens.add(EV_TEST_ONLY)
    return tokens


_YES = frozenset({"yes", "y", "true", "1"})


def column_evidence(row: dict[str, str]) -> set[str]:
    """Evidence from the inventory's dedicated usage columns.

    These are worth more than anything derived from a path, because they are
    the inventory asserting a fact rather than this file guessing from a
    filename. ``UNKNOWN`` in any of them contributes nothing -- it is the
    inventory saying it could not tell, and a maybe must not become a finding.
    """
    tokens = set()
    if (row.get("vendored_in_repo") or "").strip().lower() in _YES:
        tokens.add(EV_VENDORED_SOURCE)
    if (row.get("container_only") or "").strip().lower() in _YES:
        tokens.add(EV_SHIPPED_IN_CONTAINER)
    scope = (row.get("dep_scope") or "").strip().lower()
    if scope in ("test", "dev", "development"):
        tokens.add(EV_TEST_ONLY)
        tokens.discard(EV_SHIPPED_IN_CONTAINER)
    elif scope in ("ci", "build"):
        tokens.add(EV_BUILD_ONLY)
    return tokens


def evidence_for(row: dict[str, str]) -> set[str]:
    """The inventory's own evidence when it has any, else what we can derive.

    Derivation from ``source_kind`` is the fallback only. An inventory that
    states its evidence is not second-guessed, because the two would disagree
    on exactly the rows where the inventory knows something the path does not
    show -- a package installed by a Dockerfile in a directory named tests, for
    one.
    """
    stated = parse_evidence(row.get("usage_evidence") or "") | column_evidence(row)
    return stated or derive_evidence(row)


# ---------------------------------------------------------------------------
# The contradiction table -- the whole of USAGE_DRIFT
# ---------------------------------------------------------------------------

# (axis, approved value) -> {evidence token: why this pair cannot both be true}
#
# Read as: "the sheet says X, we observed Y, and X and Y describe different
# approvals". Anything not listed here is compatible, including every pairing
# involving `unspecified` or `other`. Adding a row here widens the check;
# there is no wildcard and no inference, on purpose.
USAGE_CONFLICTS: dict[tuple[str, str], dict[str, str]] = {
    ("usage_method", USAGE_BUILD_TIME): {
        EV_SHIPPED_IN_CONTAINER: (
            "approved as a build-time dependency, but found installed into a "
            "published image -- shipping it is a different approval"
        ),
    },
    ("usage_method", USAGE_OPERATOR_OPTIONAL): {
        EV_SHIPPED_IN_CONTAINER: (
            "approved as operator-side optional install only, but found "
            "installed into a published image"
        ),
    },
    ("usage_method", USAGE_DYNAMIC): {
        EV_STATIC_LINK: (
            "approved for dynamic linking, but found statically linked -- "
            "static linking carries obligations the approval did not review"
        ),
    },
    ("usage_method", USAGE_STATIC): {
        EV_DYNAMIC_LINK: (
            "approved for static linking, but found dynamically linked -- "
            "the reviewed use and the actual use differ"
        ),
    },
    ("vendored", VENDORED_NO): {
        EV_VENDORED_SOURCE: (
            "sheet says not vendored, but third-party source for it is "
            "committed in this tree -- a vendored copy is a separate approval"
        ),
    },
    ("vendored", VENDORED_TEST_ONLY): {
        EV_VENDORED_SOURCE: (
            "sheet says test-time only and not vendored, but third-party "
            "source for it is committed in this tree"
        ),
        EV_SHIPPED_IN_CONTAINER: (
            "sheet says test-time only, but found installed into a published "
            "image"
        ),
    },
    # Deliberately absent: ("vendored", VENDORED_SAMPLE). "Provided as part of
    # sample dockerfile" is an ANSWER about a Dockerfile, so finding the
    # package in a Dockerfile confirms it rather than contradicting it. The
    # rule was written, run against the tree, and produced 12 findings -- all
    # 12 of them the apt and pip lines of
    # libs/analytics/spatialai-data-utils/docker/Dockerfile, which is the
    # sample Dockerfile the sheet is describing. Distinguishing a sample
    # Dockerfile from a shipping one needs evidence the scanner does not have.
    ("distribution_method", DIST_NOT_DISTRIBUTED): {
        EV_SHIPPED_IN_CONTAINER: (
            "sheet says it is removed during the image build and not "
            "distributed, but found installed into a published image"
        ),
        EV_CONTAINER_IMAGE: (
            "sheet says it is not distributed, but a deployment manifest "
            "references it as an image"
        ),
    },
}


def usage_conflicts(evidence: set[str], approved: dict[str, str]) -> list[str]:
    """Reasons this approval and this evidence cannot both describe one use.

    Empty means no contradiction, which includes every case where the sheet
    said nothing usable. That is the conservative direction and it is chosen
    deliberately: a missed USAGE_DRIFT is one row a human still has to review,
    while a fabricated one teaches the reviewer to skip the column.
    """
    axes = {
        "usage_method": normalise_usage_method(approved.get("usage_method", "")),
        "vendored": normalise_vendored(approved.get("vendored", "")),
        "distribution_method": normalise_distribution(
            approved.get("distribution_method", "")
        ),
    }
    reasons = []
    for axis, value in axes.items():
        for token, reason in USAGE_CONFLICTS.get((axis, value), {}).items():
            if token in evidence:
                reasons.append(reason)
    return reasons


# ---------------------------------------------------------------------------
# The approved index
# ---------------------------------------------------------------------------


class ApprovedIndex:
    """The baseline keyed the way the comparison asks questions of it.

    Keyed by (repo module, canonical package) because that is the unit OSRB
    approves: a package approved for AGENT says nothing about the same package
    in VIOS. One sheet row can land under several repo modules -- the RTVI
    VLM+EMBED submission covers two -- and the derived ``repo_modules`` column
    is what expands it.
    """

    def __init__(self, rows: list[dict[str, str]]) -> None:
        self.rows = rows
        self.by_module_package: dict[tuple[str, str], list[dict[str, str]]] = (
            collections.defaultdict(list)
        )
        self.rows_without_module: list[dict[str, str]] = []
        for row in rows:
            modules = split_repo_modules(row.get("repo_modules", ""))
            if not modules:
                self.rows_without_module.append(row)
                continue
            key_package = canonical_package(row["package"])
            for module in modules:
                self.by_module_package[(module, key_package)].append(row)
        self.modules = {module for module, _ in self.by_module_package}

        # Modules whose every approved row is an inline addition from a bug
        # comment rather than an OSRB submission. `provenance` is derived in
        # the approved.csv generator from the upstream sheet tab; a module here
        # has a record in name only. Absent column -> everything counts as a
        # submission, so an older baseline degrades to the previous behaviour
        # rather than silently reclassifying every module as unsubmitted.
        per_module: dict[str, set[str]] = collections.defaultdict(set)
        for row in rows:
            for module in split_repo_modules(row.get("repo_modules", "")):
                per_module[module].add(row.get("provenance", "submission") or "submission")
        self.inline_only_modules = {
            module for module, kinds in per_module.items() if kinds == {"inline-addition"}
        }

    def candidates(self, module: str, package: str) -> list[dict[str, str]]:
        return self.by_module_package.get((module, canonical_package(package)), [])

    def has_module(self, module: str) -> bool:
        return module in self.modules

    def is_inline_only(self, module: str) -> bool:
        """True when this module's only approvals are inline bug-comment additions."""
        return module in self.inline_only_modules


# ---------------------------------------------------------------------------
# The comparison
# ---------------------------------------------------------------------------


def unsubmitted_scope(module: str, source_file: str) -> str:
    """The UNSUBMITTED entry covering this row, or "" if none does.

    Two lookups, because ``UNSUBMITTED`` is recorded at a finer grain than
    ``owning_module()`` reports at. ``services/vios/ui/vios-ui`` is on the
    unsubmitted list, but ownership collapses everything under
    ``services/vios/**`` to ``services/vios`` -- a module that IS submitted,
    with 325 approved rows. Matching on the module name alone therefore sends
    the whole VIOS UI tree into NOT_APPROVED, telling 734 rows' worth of
    readers to chase an OSRB bug that covers a different piece of software,
    and letting a package name shared with the VIOS backend (``uuid``) match
    an approval that was never about the UI. Same for
    ``deploy/docker/services/infra`` under the submitted ``deploy``.

    So the path is checked too, longest entry first: the answer to "does OSRB
    hold a record for this code" is a property of where the file lives, not of
    the name ownership rounds it to.
    """
    if is_unsubmitted(module):
        return module
    paths = [
        part.split("#", 1)[0].strip()
        for part in (source_file or "").split(";")
        if part.strip()
    ]
    if not paths:
        return ""
    entries = sorted(UNSUBMITTED_SET, key=len, reverse=True)
    covered = []
    for path in paths:
        match = next(
            (e for e in entries if e != "<root>" and path.startswith(e + "/")), ""
        )
        if not match:
            # One citation outside every unsubmitted subtree is enough to keep
            # the row in the comparison. The inventory merges the paths that
            # declare a package into one cell, so a package used by both the
            # unsubmitted VIOS UI and the submitted VIOS backend must still be
            # judged against the backend's approvals -- calling the whole row
            # "never submitted" would be the lenient reading of ambiguous
            # evidence, which is the wrong direction for a compliance report.
            return ""
        covered.append(match)
    return covered[0]


def _output_row(
    verdict: str,
    inventory: dict[str, str],
    approved: dict[str, str] | None,
    evidence: set[str],
    notes: str,
) -> dict[str, str]:
    approved = approved or {}
    license_text = inventory.get("license", "")
    return {
        "verdict": verdict,
        "module": inventory.get("module", ""),
        "language": inventory.get("language", ""),
        "package": inventory.get("package", ""),
        "version": inventory.get("version", ""),
        "license": license_text,
        "risk": inventory.get("risk") or license_risk(license_text),
        "usage_evidence": ";".join(sorted(evidence)),
        "approved_module": approved.get("module", ""),
        "approved_version": approved.get("version", ""),
        "approved_license": approved.get("license", ""),
        "approved_vendored": approved.get("vendored", ""),
        "approved_distribution_method": approved.get("distribution_method", ""),
        "approved_usage_method": approved.get("usage_method", ""),
        "source_kind": inventory.get("source_kind", ""),
        "source_file": inventory.get("source_file", ""),
        "notes": notes,
    }


def local_build_variant(found: str, candidates: list[dict[str, str]]) -> str:
    """The approved version this one differs from only by a PEP 440 local tag.

    ``torch 2.10.0`` and ``torch 2.10.0+cpu`` are the same upstream release,
    and the baseline writes it both ways for the same package. The rows are
    still reported as drift rather than collapsed, because a ``+cpu`` wheel and
    a ``+cu128`` wheel bundle different libraries and that is an OSRB
    difference -- but the note has to say so, or the finding reads as a version
    bump nobody made and the reader stops trusting the column.
    """
    public = canonical_version(found).split("+", 1)[0]
    for candidate in candidates:
        other = canonical_version(candidate["version"])
        if "+" not in (canonical_version(found) + other):
            continue
        if other.split("+", 1)[0] == public:
            return candidate["version"]
    return ""


def _summarise_versions(candidates: list[dict[str, str]]) -> str:
    versions = sorted({row["version"] for row in candidates if row["version"]})
    return ", ".join(versions[:6]) + (" ..." if len(versions) > 6 else "")


def classify(
    row: dict[str, str],
    approved: ApprovedIndex,
    seen: Normalisations | None = None,
) -> dict[str, str]:
    """One inventory row -> one verdict, at the first gate that fails.

    The gates are ordered module -> package -> version -> licence -> usage, so
    a row never carries two verdicts and the reader is never asked to work out
    which one to act on first.
    """
    module = row.get("module", "")
    evidence = evidence_for(row)

    if approved.is_inline_only(module):
        # The module has approved rows, but every one of them came from an
        # inline addition in a bug comment rather than an OSRB submission. That
        # is not a reviewed dependency tree, and treating it as one turns a
        # single missing submission into a per-package pile: services/ui has two
        # inline @img/sharp rows against 2157 resolved npm packages, which
        # reported as 1350 individual NOT_APPROVED findings and buried every
        # other verdict in the report.
        #
        # Report it once, at module level, pointing at the actual action.
        return _output_row(
            VERDICT_MODULE_UNSUBMITTED,
            row,
            None,
            evidence,
            f"{module!r} has no OSRB submission -- its only approved rows are "
            "inline additions from a bug comment, not a reviewed dependency "
            "tree. File an OSRB submission for the module rather than chasing "
            "this package",
        )

    scope = unsubmitted_scope(module, row.get("source_file", ""))
    if scope:
        where = scope if scope == module else f"{scope!r} (inside {module})"
        return _output_row(
            VERDICT_MODULE_UNSUBMITTED,
            row,
            None,
            evidence,
            f"{where} has no OSRB submission of any kind -- file a new OSRB "
            "bug for it rather than chasing this package",
        )

    candidates = approved.candidates(module, row["package"])
    if not candidates:
        if not approved.has_module(module) and module not in SUBMITTED_MODULES:
            # Neither submitted nor recorded as unsubmitted: the module map is
            # behind the tree. Reported as unsubmitted rather than rejected --
            # it is still the case that OSRB holds no record -- but the note
            # says where the fix goes.
            return _output_row(
                VERDICT_MODULE_UNSUBMITTED,
                row,
                None,
                evidence,
                f"module {module!r} is in neither MODULE_MAP nor UNSUBMITTED; "
                "module_map.py needs updating before this row can be judged",
            )
        return _output_row(
            VERDICT_NOT_APPROVED,
            row,
            None,
            evidence,
            "no approved row for this package in this module",
        )

    raw_version = row.get("version", "")
    version = canonical_version(raw_version)
    if is_comparable_version(raw_version):
        version_note = ""
        version_matched = [
            row_
            for row_ in candidates
            if is_comparable_version(row_["version"])
            and canonical_version(row_["version"]) == version
        ]
        if not version_matched:
            # An approval that records no version at all cannot disagree with
            # one. 248 baseline rows are in that state, and treating them as a
            # mismatch invents drift against a version the sheet never claimed
            # -- the note read "approved at ; repo has 13.610.43", which tells
            # a reviewer nothing they can act on.
            version_matched = [
                row_ for row_ in candidates if not is_comparable_version(row_["version"])
            ]
            version_note = (
                "version not compared: the approved row records no version"
                if version_matched
                else ""
            )
        if not version_matched:
            best = candidates[0]
            note = (
                f"approved at {_summarise_versions(candidates)}; repo has "
                f"{raw_version or '(no version)'}"
            )
            variant = local_build_variant(raw_version, candidates)
            if variant:
                note += (
                    f" -- same upstream release as {variant}, differing only in "
                    "the PEP 440 local build tag; reported because build "
                    "variants bundle different libraries"
                )
            return _output_row(
                VERDICT_VERSION_DRIFT, row, best, evidence, note
            )
    else:
        version_matched = candidates
        version_note = (
            "version not compared: the inventory carries a range or no version "
            f"({raw_version or 'blank'}), not a resolved one"
        )

    license_matched = [
        row_
        for row_ in version_matched
        if licenses_compatible(row.get("license", ""), row_.get("license", ""))
    ]
    if not license_matched:
        best = version_matched[0]
        found = row.get("license", "") or "(none resolved)"
        return _output_row(
            VERDICT_LICENSE_DRIFT,
            row,
            best,
            evidence,
            f"approved as {best.get('license', '') or '(blank)'}; resolved as "
            f"{found}"
            + (
                f" -- risk {license_risk(row.get('license', ''))}"
                if license_risk(row.get("license", "")) in ("High", "Medium")
                else ""
            ),
        )

    # Usage last, and only against rows that already matched on everything
    # else. A row is approved if ANY surviving approval covers our use: the
    # same package is often approved once per submission, and drift means no
    # approval fits, not that the first one did not.
    conflicts_by_row = [
        (candidate, usage_conflicts(evidence, candidate))
        for candidate in license_matched
    ]
    for candidate, conflicts in conflicts_by_row:
        if not conflicts:
            note = _approved_note(evidence, candidate, seen)
            return _output_row(
                VERDICT_APPROVED,
                row,
                candidate,
                evidence,
                f"{note}; {version_note}" if version_note else note,
            )

    candidate, conflicts = conflicts_by_row[0]
    return _output_row(
        VERDICT_USAGE_DRIFT, row, candidate, evidence, "; ".join(conflicts)
    )


def _approved_note(
    evidence: set[str], candidate: dict[str, str], seen: Normalisations | None
) -> str:
    """Say when an APPROVED row got there because usage was uncheckable.

    "Approved, and we checked the use" and "approved, and there was nothing to
    check the use against" are different amounts of assurance. Writing the
    second one down is what lets a reviewer sort by it and decide whether the
    sheet needs filling in.
    """
    usage = normalise_usage_method(candidate.get("usage_method", ""), seen)
    if usage == USAGE_UNSPECIFIED:
        return "approved; usage_method blank in the sheet, so usage not compared"
    if usage == USAGE_OTHER:
        return (
            "approved; usage_method is 'Other', which points at free-text "
            "comments, so usage not compared"
        )
    if not evidence:
        return "approved; no usage evidence in the inventory, so usage not compared"
    return f"approved; usage evidence consistent with {usage}"


def stale_approvals(
    inventory: list[dict[str, str]], approved: ApprovedIndex
) -> list[dict[str, str]]:
    """Approved rows whose package is gone from a module we actually scanned.

    Scoped to modules the inventory covered. Without that scope, a run over
    one service would report every package of the other 20 as vanished, and
    3,000 informational rows would bury the six that matter.
    """
    scanned_modules = {row["module"] for row in inventory}
    present = {
        (row["module"], canonical_package(row["package"])) for row in inventory
    }
    stale = []
    for (module, package), rows in sorted(approved.by_module_package.items()):
        if module not in scanned_modules or (module, package) in present:
            continue
        row = rows[0]
        stale.append(
            _output_row(
                VERDICT_APPROVED_NOT_PRESENT,
                {"module": module, "package": row["package"], "version": ""},
                row,
                set(),
                "approved but not found in the repo -- stale approval, "
                "informational only",
            )
        )
    return stale


def compare(
    inventory: list[dict[str, str]],
    approved: ApprovedIndex,
    seen: Normalisations | None = None,
) -> list[dict[str, str]]:
    """Every inventory row judged, then the stale approvals appended."""
    rows = [classify(row, approved, seen) for row in inventory]
    rows.extend(stale_approvals(inventory, approved))
    order = {verdict: index for index, verdict in enumerate(VERDICTS)}
    rows.sort(
        key=lambda row: (
            order.get(row["verdict"], len(order)),
            row["module"],
            canonical_package(row["package"]),
            row["version"],
        )
    )
    return rows


def count_verdicts(rows: list[dict[str, str]]) -> dict[str, int]:
    counts = {verdict: 0 for verdict in VERDICTS}
    for row in rows:
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------


def write_csv(rows: list[dict[str, str]], path: str | Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _module_table(rows: list[dict[str, str]]) -> list[str]:
    per_module: dict[str, collections.Counter[str]] = collections.defaultdict(
        collections.Counter
    )
    for row in rows:
        per_module[row["module"]][row["verdict"]] += 1
    findings = [
        (module, counts)
        for module, counts in per_module.items()
        if any(counts[verdict] for verdict in FINDING_VERDICTS)
    ]
    findings.sort(
        key=lambda item: -sum(item[1][verdict] for verdict in FINDING_VERDICTS)
    )
    lines = ["| module | " + " | ".join(VERDICTS) + " |"]
    lines.append("|---" * (len(VERDICTS) + 1) + "|")
    for module, counts in findings:
        cells = " | ".join(str(counts[verdict]) for verdict in VERDICTS)
        lines.append(f"| `{module or '(none)'}` | {cells} |")
    return lines


def write_summary(
    rows: list[dict[str, str]],
    counts: dict[str, int],
    seen: Normalisations,
    path: str | Path,
    *,
    inventory_rows: int,
    approved_rows: int,
    warnings: list[str],
) -> None:
    """The human-readable half. Findings first, provenance last."""
    findings = sum(counts[verdict] for verdict in FINDING_VERDICTS)
    lines = [
        "# OSRB compliance — repository state vs approved baseline",
        "",
        f"Inventory rows: **{inventory_rows}** · approved rows: "
        f"**{approved_rows}** · findings: **{findings}**",
        "",
        "| verdict | count | meaning |",
        "|---|---|---|",
    ]
    meanings = {
        VERDICT_NOT_APPROVED: "in the repo, no approved row for it in this module",
        VERDICT_MODULE_UNSUBMITTED: "module has no OSRB record at all — "
        "never submitted, **not** rejected",
        VERDICT_VERSION_DRIFT: "package approved for this module at another version",
        VERDICT_LICENSE_DRIFT: "approved, but the resolved licence differs",
        VERDICT_USAGE_DRIFT: "approved, but our usage contradicts the approved usage",
        VERDICT_APPROVED_NOT_PRESENT: "approved and no longer present "
        "(stale approval, informational)",
        VERDICT_APPROVED: "package, version, licence and usage all match",
    }
    for verdict in VERDICTS:
        lines.append(f"| `{verdict}` | {counts[verdict]} | {meanings[verdict]} |")

    lines += ["", "## Findings by module", ""]
    lines += _module_table(rows)

    for verdict in (
        VERDICT_USAGE_DRIFT,
        VERDICT_LICENSE_DRIFT,
        VERDICT_VERSION_DRIFT,
    ):
        subset = [row for row in rows if row["verdict"] == verdict][:25]
        if not subset:
            continue
        lines += ["", f"## {verdict} (first {len(subset)})", ""]
        lines += ["| module | package | version | approved | note |", "|---|---|---|---|---|"]
        for row in subset:
            lines.append(
                f"| `{row['module']}` | `{row['package']}` | {row['version']} | "
                f"{row['approved_version']} | {row['notes']} |"
            )

    lines += ["", "## Normalisation applied to the approved sheet", ""]
    if seen.total() == 0:
        lines.append("None — every value was already in canonical form.")
    else:
        lines += ["| axis | raw value | normalised to | rows |", "|---|---|---|---|"]
        for axis in sorted(seen.applied):
            for (raw, value), count in seen.applied[axis].most_common():
                shown = f"`{raw}`" if raw else "*(blank)*"
                lines.append(f"| {axis} | {shown} | `{value}` | {count} |")
    if seen.unmapped:
        lines += [
            "",
            "**Values no normalisation table knows.** Each one is inert in the "
            "comparison — it can neither confirm nor contradict a usage — so a "
            "line here is a check that has silently stopped running:",
            "",
        ]
        for axis in sorted(seen.unmapped):
            for raw, count in seen.unmapped[axis].most_common():
                lines.append(f"- `{axis}` = `{raw}` ({count} rows)")

    if warnings:
        lines += ["", "## Warnings", ""]
        lines += [f"- {warning}" for warning in warnings]

    lines.append("")
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def write_github_output(counts: dict[str, int], path: str | Path) -> None:
    """Counts a workflow can gate on, one lowercase key per verdict.

    Appended, never truncated: ``$GITHUB_OUTPUT`` is shared with every other
    step in the job.
    """
    findings = sum(counts[verdict] for verdict in FINDING_VERDICTS)
    with open(path, "a", encoding="utf-8") as handle:
        for verdict in VERDICTS:
            handle.write(f"{verdict.lower()}={counts[verdict]}\n")
        handle.write(f"findings={findings}\n")
        handle.write(f"total={sum(counts.values())}\n")


def _log(message: str) -> None:
    print(message, file=sys.stderr)



def write_submission_packs(rows: list[dict[str, str]], directory: str) -> list[tuple[str, int]]:
    """One CSV per unsubmitted module, ready to attach to an OSRB bug.

    Reporting that a module has no OSRB record is only half an answer; the
    other half is the list of what would be in the submission, and that list is
    exactly the MODULE_UNSUBMITTED rows for that module. Writing them out turns
    "services/ui has no submission" from a research task into an attachment.

    Filename is the module path with "/" replaced, because a module path is not
    a filename and silently creating nested directories would scatter the packs.
    """
    out_dir = pathlib.Path(directory)
    out_dir.mkdir(parents=True, exist_ok=True)
    by_module: dict[str, list[dict[str, str]]] = collections.defaultdict(list)
    for row in rows:
        if row.get("verdict") == VERDICT_MODULE_UNSUBMITTED:
            by_module[row.get("module", "")].append(row)

    written = []
    for module, module_rows in sorted(by_module.items()):
        safe = module.replace("/", "__").replace("<", "").replace(">", "") or "root"
        path = out_dir / f"osrb-submission-{safe}.csv"
        fields = ["package", "version", "license", "module", "usage_evidence",
                  "source_kind", "source_file", "notes"]
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer_ = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer_.writeheader()
            writer_.writerows(
                sorted(module_rows, key=lambda r: (r.get("package", "").lower(),
                                                   r.get("version", "")))
            )
        written.append((str(path), len(module_rows)))
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--inventory", required=True, help="inventory CSV to judge")
    parser.add_argument("--approved", required=True, help="OSRB approved baseline CSV")
    parser.add_argument("--output", default="osrb-compliance.csv")
    parser.add_argument("--summary", default="osrb-compliance.md")
    parser.add_argument(
        "--submissions",
        default=None,
        metavar="DIR",
        help=(
            "write one submission-ready CSV per MODULE_UNSUBMITTED module. "
            "The rows OSRB needs to review a module ARE the findings for it, "
            "so emit them in a form that can be attached to a bug instead of "
            "leaving someone to reconstruct the list by filtering."
        ),
    )
    parser.add_argument(
        "--github-output",
        default=None,
        help="path to $GITHUB_OUTPUT; per-verdict counts are appended to it",
    )
    args = parser.parse_args(argv)

    inventory = load_inventory(args.inventory)
    approved_rows = load_approved(args.approved)
    warnings = []

    unmapped = unmapped_osrb_modules([row.get("module", "") for row in approved_rows])
    if unmapped:
        warnings.append(
            "OSRB module label(s) with no entry in module_map.MODULE_MAP, so their "
            f"approvals can never match a repo module: {', '.join(unmapped)}"
        )
    drifted = check_repo_modules_column(approved_rows)
    if drifted:
        warnings.append(
            f"{len(drifted)} approved row(s) whose derived repo_modules column "
            f"disagrees with module_map.py, e.g. {drifted[0]}"
        )

    index = ApprovedIndex(approved_rows)
    if index.rows_without_module:
        warnings.append(
            f"{len(index.rows_without_module)} approved row(s) have an empty "
            "repo_modules column and were excluded from the comparison"
        )

    stray = collections.Counter()
    for row in inventory:
        for token in unknown_evidence(row.get("usage_evidence") or ""):
            stray[token] += 1
    if stray:
        warnings.append(
            "usage_evidence token(s) this comparator has no entry for in "
            "EVIDENCE_ALIASES, so they contributed nothing to the usage check: "
            + ", ".join(f"{token} ({count} rows)" for token, count in stray.most_common())
        )

    seen = normalisation_census(approved_rows)
    rows = compare(inventory, index)
    counts = count_verdicts(rows)

    write_csv(rows, args.output)
    write_summary(
        rows,
        counts,
        seen,
        args.summary,
        inventory_rows=len(inventory),
        approved_rows=len(approved_rows),
        warnings=warnings,
    )
    if args.github_output:
        write_github_output(counts, args.github_output)

    for warning in warnings:
        _log(f"WARNING: {warning}")
    for verdict in VERDICTS:
        _log(f"{verdict}: {counts[verdict]}")
    _log(f"wrote {args.output} and {args.summary}")
    if args.submissions:
        packs = write_submission_packs(rows, args.submissions)
        for path, count in packs:
            print(f"submission pack: {path} ({count} packages)", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
