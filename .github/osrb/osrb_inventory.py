#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Emit the FULL third-party dependency inventory of this repo at one git ref.

``osrb_scan`` is a DELTA pipeline: it diffs a base ref against a head ref, so
it only ever sees what a pull request changes. That is the right shape for a
merge gate and the wrong shape for OSRB, because it is structurally blind to
drift that is already in the tree — a dependency that landed before the gate
existed, or through a path no parser covered at the time, never appears in any
diff again and is therefore never reviewed. This module is the STATE half:
regenerate the whole inventory from the tree, commit it, and let `git diff`
(and the private OSRB comparison against ``approved.csv``) do the comparing.

Approval is per USE, not per package
------------------------------------
The OSRB baseline in ``approved.csv`` carries ``distribution_method`` and
``usage_method`` columns: the same library dynamically linked is a *different*
approval from the same library vendored and modified, and a package can be
approved on name+version+licence and still be a finding because the way it
enters the product changed. So the row shape here is deliberately not "package,
version, licence" — it is "package, version, licence, and the evidence for how
this package reaches the customer", in the closed ``usage_evidence``
vocabulary below. A comparison tool can then match a row against the approved
sheet's usage_method instead of assuming every use of a package is the use
that was approved.

usage_evidence vocabulary (closed; a row may carry several, ";"-joined, sorted)
------------------------------------------------------------------------------
    declared-manifest   named in a language manifest or lockfile
    vendored-source     its source is committed in this repository
    container-apt       apt/apk installed into an image
    container-pip       pip installed inside a Dockerfile, not via a manifest
    container-base      arrives inside a FROM (or COPY --from) base image
    container-image     a whole image pulled by compose or a chart
    build-fetch         fetched at build time (git clone / wget / curl /
                        FetchContent / ExternalProject)
    imported-only       reached by source or by the build system, declared in
                        no manifest
    ci-tooling          a GitHub Action pin or a pre-commit hook; does not ship

Two mappings in that list are judgement calls and are called out rather than
buried. CMake ``find_package`` and ``pkg_check_modules`` link against a library
the build image already has: nothing fetches it and no manifest names it, which
is the same evidence class as an undeclared import, so it is filed as
``imported-only``. A Helm chart dependency is filed as ``container-image``
because a subchart's whole point is the images it deploys; the vocabulary has
no separate chart term.

Determinism is the hard requirement
-----------------------------------
This file is committed and diffed, so the same tree must always produce
byte-identical bytes. Everything is read out of `git` at an explicit ref (never
the working tree), every collection is sorted before it is written, no
timestamp, hostname, run id or absolute path is ever emitted, and there are NO
network calls — which is the one real behavioural difference from ``osrb_scan``,
whose licence column is filled from PyPI. Determinism is verified in
``test_osrb_inventory.py`` by generating the inventory twice and comparing.

Where licences come from, and why ``--previous`` exists
-------------------------------------------------------
Only what is in the tree, in this order:

1. the parser's own metadata (``package-lock.json`` records a licence; a
   ``uv.lock`` does not);
2. the attribution files this repo already publishes
   (``3rdParty_Licenses.md``, ``LICENSE-3rd-party.txt``), matched on the exact
   (package, version) they document;
3. ``SPDX-License-Identifier:`` headers inside a vendored source tree;
4. another parser in the SAME tree, when every one of them that recorded a
   licence for that exact release recorded the same one — a licence belongs to
   a release, not to the lockfile that happened to spell it out;
5. ``--previous <csv>``: the inventory committed by the last run, matched on an
   unchanged (package, version).

(5) is the carry-forward and it exists because of the no-network rule. Most
Python lockfiles record no licence at all, and the delta pipeline resolves
those from pypi.org — which this module may not do, because a network answer is
not reproducible and would make a committed artifact flap. A (package, version)
pair that has not changed cannot have changed licence either, so the licence
the previous inventory recorded is carried forward instead of being re-fetched.
It is a cache with a correctness argument, not a convenience: the pair is the
cache key, so any version bump drops back to UNKNOWN and asks for a human.

Anything still unresolved after all five is ``UNKNOWN``. Never a guess: a row
that claims MIT when the truth is GPL-2.0 ends a review, a row that admits it
does not know starts one.

What is deliberately NOT a row
------------------------------
* First-party code: in-repo Helm subcharts (``repository: file://…``), npm
  ``file:``/``workspace:`` deps and VSS's own published images are all already
  filtered by the parsers this module reuses.
* Attribution files as a source of PACKAGES. They are the OUTPUT of the licence
  process, not a declaration of a dependency, so they are read for licences
  only — the same stance ``osrb_scan.blocks_merge`` takes.
* Dev/test-only dependencies, because the reused parsers exclude them by
  design: a linter never reaches a release artifact.
* ``UNCOVERED_SOURCE`` markers from ``osrb_sources``. Those are a path where a
  package name would go, and a package-shaped CSV cannot represent them without
  lying. They are counted and printed to stderr instead, so a file this
  repository cannot parse is still visible to whoever runs this.

Usage:
    python3 .github/osrb/osrb_inventory.py --ref HEAD \\
        --output .github/osrb/inventory.csv \\
        [--previous .github/osrb/inventory.csv]
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import subprocess
import sys

# CI runs these as plain files (`python .github/osrb/osrb_inventory.py`) and the
# tests load them by path, so neither entry point leaves this directory
# importable by name. The siblings do the same for their own imports.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import osrb_scan  # noqa: E402
import osrb_sources  # noqa: E402
import osrb_usage  # noqa: E402

UNKNOWN = "UNKNOWN"

# --- usage_evidence: the closed vocabulary ---------------------------------

EV_DECLARED_MANIFEST = "declared-manifest"
EV_VENDORED_SOURCE = "vendored-source"
EV_CONTAINER_APT = "container-apt"
EV_CONTAINER_PIP = "container-pip"
EV_CONTAINER_BASE = "container-base"
EV_CONTAINER_IMAGE = "container-image"
EV_BUILD_FETCH = "build-fetch"
EV_IMPORTED_ONLY = "imported-only"
EV_CI_TOOLING = "ci-tooling"

USAGE_EVIDENCE = (
    EV_BUILD_FETCH,
    EV_CI_TOOLING,
    EV_CONTAINER_APT,
    EV_CONTAINER_BASE,
    EV_CONTAINER_IMAGE,
    EV_CONTAINER_PIP,
    EV_DECLARED_MANIFEST,
    EV_IMPORTED_ONLY,
    EV_VENDORED_SOURCE,
)

#: Evidence that only ever exists inside a built image — used by the
#: `container_only` column, which answers "is there anything in this source
#: tree that declares this, or does it appear only once an image is built".
_CONTAINER_EVIDENCE = frozenset(
    {EV_CONTAINER_APT, EV_CONTAINER_BASE, EV_CONTAINER_IMAGE, EV_CONTAINER_PIP}
)

# --- dep_scope --------------------------------------------------------------

SCOPE_RUNTIME = "runtime"
SCOPE_CI = "ci"

#: There is no "build" scope on purpose. Every non-CI evidence class here ends
#: up inside a shipped artifact — a `FetchContent` library is linked into the
#: binary, an apt package sits in the image, a `find_package` library is the
#: .so the image loads — so calling any of them build-only would understate the
#: distribution. Dev/test dependencies never reach this module at all: the
#: parsers reused below drop them. What is left to distinguish is the tooling
#: that genuinely never ships, and that is what `ci` marks.

#: Extra source_kind beyond `osrb_scan.SOURCE_KINDS`: source committed into
#: this tree is a shape neither sibling module emits, because neither one has a
#: reason to walk a vendored directory.
KIND_VENDORED = "vendored"

COLUMNS = [
    "package",
    "version",
    "license",
    "module",
    "language",
    "source_kind",
    "source_file",
    "dep_scope",
    "vendored_in_repo",
    "copied_adapted",
    "container_only",
    "usage_evidence",
    "risk",
]


def _log(msg: str) -> None:
    print(f"[osrb-inventory] {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


class _Entry:
    """One inventory row while it is still collecting evidence.

    Identity is (package, version, module, language). ``module`` is in the key
    because OSRB approves per component: the same package pinned by two
    services is two approvals, and merging them would hide one of them. Version
    is in the key because a licence is a property of a release, not of a name.

    Written out by hand rather than as a ``@dataclass`` because the test files
    in this directory load their module with ``importlib`` without registering
    it in ``sys.modules``, and ``dataclasses`` resolves field types through
    that registration — a dataclass here would make this module the only one
    the house test idiom cannot import.
    """

    def __init__(
        self,
        package: str,
        version: str,
        module: str,
        language: str,
        licenses: set[str] | None = None,
        source_files: set[str] | None = None,
        source_kinds: set[str] | None = None,
        evidence: set[str] | None = None,
        scopes: set[str] | None = None,
    ) -> None:
        self.package = package
        self.version = version
        self.module = module
        self.language = language
        self.licenses: set[str] = licenses or set()
        self.source_files: set[str] = source_files or set()
        self.source_kinds: set[str] = source_kinds or set()
        self.evidence: set[str] = evidence or set()
        self.scopes: set[str] = scopes or set()


def _key(package: str, version: str, module: str, language: str) -> tuple[str, ...]:
    return (package, version, module, language)


class Inventory:
    """Collects evidence into one entry per (package, version, module, language)."""

    def __init__(self) -> None:
        self._entries: dict[tuple[str, ...], _Entry] = {}

    def add(
        self,
        *,
        package: str,
        version: str,
        module: str,
        language: str,
        evidence: str,
        source_file: str,
        source_kind: str,
        license_expr: str = "",
        scope: str = SCOPE_RUNTIME,
    ) -> None:
        package = package.strip()
        if not package:
            return
        version = version.strip() or UNKNOWN
        language = (language or UNKNOWN).strip() or UNKNOWN
        entry = self._entries.get(_key(package, version, module, language))
        if entry is None:
            entry = _Entry(package, version, module, language)
            self._entries[_key(package, version, module, language)] = entry
        if license_expr.strip():
            entry.licenses.add(license_expr.strip())
        # The "#L12" evidence suffix osrb_sources appends is dropped here. It
        # is exactly right for a PR diff, where it lands the reviewer on the
        # instruction that changed, and wrong for a committed state file: an
        # unrelated edit near the top of a Dockerfile would renumber every row
        # below it and fill the inventory diff with movement nobody made.
        entry.source_files.add(source_file.split("#", 1)[0])
        entry.source_kinds.add(source_kind)
        entry.evidence.add(evidence)
        entry.scopes.add(scope)

    def entries(self) -> list[_Entry]:
        return list(self._entries.values())

    def names_by_module(self) -> dict[str, set[str]]:
        by_module: dict[str, set[str]] = {}
        for entry in self._entries.values():
            by_module.setdefault(entry.module, set()).add(entry.package)
        return by_module

    def drop_manifest_rows_covered_by_a_lockfile(self) -> int:
        """Drop manifest rows for names the same module already locks.

        ``osrb_scan`` makes the same call and for the same reason: a lockfile
        records the resolved version and the transitive closure, a manifest
        records a range that has not been resolved yet. Keeping both produces
        two rows for one dependency — one of them with version UNKNOWN — and an
        OSRB reviewer cannot tell which to review. Scoped per module and per
        language so one service's lockfile cannot suppress another's manifest.
        """
        locked: set[tuple[str, str, str]] = {
            (entry.module, entry.language, entry.package.lower())
            for entry in self._entries.values()
            if osrb_scan.KIND_LOCKFILE in entry.source_kinds
        }
        dropped = 0
        for key, entry in list(self._entries.items()):
            if entry.source_kinds != {osrb_scan.KIND_MANIFEST}:
                continue
            if (entry.module, entry.language, entry.package.lower()) in locked:
                del self._entries[key]
                dropped += 1
        return dropped

    def drop_local_node_workspace_rows(
        self, names_by_module: dict[str, set[str]]
    ) -> int:
        """Drop first-party npm workspaces declared with ordinary ranges.

        npm commonly spells an in-repo workspace dependency as ``"*"`` in
        package.json and records its first-party nature only as ``link: true``
        in package-lock.json. Keep the declaration long enough for the usage
        pass to recognize imports, then remove it from the third-party state
        inventory.
        """
        dropped = 0
        for key, entry in list(self._entries.items()):
            if (
                entry.language != "node"
                or entry.source_kinds != {osrb_scan.KIND_MANIFEST}
            ):
                continue
            local_names = names_by_module.get(entry.module, set())
            if entry.package.lower() not in local_names:
                continue
            del self._entries[key]
            dropped += 1
        return dropped


# ---------------------------------------------------------------------------
# Declaration side — reuse osrb_scan's parsers, one path at a time
# ---------------------------------------------------------------------------

#: The same selector list `osrb_scan.main()` hands to `declared_names_by_module`.
#: Between them they cover every manifest and lockfile the scanner can read:
#: ecosystem locks and manifests, the Python locks that predate the ecosystem
#: table, package-lock.json, and requirements*.txt / pyproject.toml.
def _selectors() -> list:
    return [
        osrb_scan._lock_selector,
        osrb_scan._manifest_selector,
        osrb_scan._python_lock_selector,
        osrb_scan._node_lock_selector,
        osrb_scan._python_manifest_selector,
    ]


def _language_of(path: str) -> str:
    """Language for a manifest path, from osrb_scan's own ecosystem table.

    The two filename families that table deliberately omits — the Python locks
    and package-lock.json, which `osrb_scan` reaches by filename instead — are
    named here explicitly rather than defaulted, so a new ecosystem shows up as
    UNKNOWN instead of being silently filed as Python.
    """
    entry = osrb_scan.ecosystem_parser(path)
    if entry is not None:
        return entry[0]
    base = path.rsplit("/", 1)[-1].lower()
    if base == "package-lock.json":
        return "node"
    if osrb_scan._python_lock_selector(path) or osrb_scan._python_manifest_selector(path):
        return "python"
    return UNKNOWN


def collect_declared(ref: str, paths: list[str], inventory: Inventory) -> list[str]:
    """Parse every manifest and lockfile at `ref`, one path at a time.

    Per path, NOT through `osrb_scan`'s merged inventories: those are keyed on
    (name, version) and merged with `setdefault`, so a package locked by three
    services keeps one service's `source_file` and the other two lose their
    claim to it. That is tolerable for a diff, which only needs the name, and
    fatal for a state inventory whose whole purpose is per-component evidence.

    Returns the paths that failed to parse, for the caller to report.
    """
    selectors = _selectors()
    failures: list[str] = []
    for path in sorted(paths):
        parser = next(
            (fn for fn in (select(path) for select in selectors) if fn is not None), None
        )
        if parser is None:
            continue
        data = osrb_scan._git_show(ref, path)
        if data is None:
            continue
        try:
            parsed = parser(data)
        except Exception as exc:  # noqa: BLE001 - reported, never swallowed
            _log(f"WARNING: cannot parse {path}@{ref}: {type(exc).__name__}: {exc}")
            failures.append(path)
            continue
        kind = osrb_scan.is_dependency_file(path) or osrb_scan.KIND_MANIFEST
        language = _language_of(path)
        module = osrb_scan.owning_module(path)
        for key, meta in parsed.items():
            # Two parser shapes: the lockfile parsers return {(name, version):
            # meta}, `parse_requirements` / `parse_pyproject` return
            # {name: version}. Branch on the key rather than trusting one.
            if isinstance(key, tuple):
                name, version = key
                license_expr = str((meta or {}).get("license") or "")
            else:
                name, version = key, str(meta or "")
                license_expr = ""
            inventory.add(
                package=str(name),
                version=str(version),
                module=module,
                language=language,
                evidence=EV_DECLARED_MANIFEST,
                source_file=path,
                source_kind=kind,
                license_expr=license_expr,
            )
    return failures


# ---------------------------------------------------------------------------
# Source side — reuse osrb_sources, then classify its evidence
# ---------------------------------------------------------------------------

#: (source_kind, note prefix) -> usage_evidence. The note is `osrb_sources`'
#: own description of the instruction it read, so this table is the seam
#: between the two files; `test_osrb_inventory.py` asserts that every note the
#: real tree produces lands in it, which is what stops a new evidence shape in
#: osrb_sources from being silently filed as UNKNOWN.
_EVIDENCE_BY_NOTE: tuple[tuple[str, str, str], ...] = (
    (osrb_sources.KIND_CONTAINER, "base image (FROM)", EV_CONTAINER_BASE),
    (osrb_sources.KIND_CONTAINER, "external image (COPY --from)", EV_CONTAINER_BASE),
    (osrb_sources.KIND_CONTAINER, "OS package installed into the image", EV_CONTAINER_APT),
    (osrb_sources.KIND_CONTAINER, "pip install in a Dockerfile", EV_CONTAINER_PIP),
    (osrb_sources.KIND_CONTAINER, "fetched into the image with", EV_BUILD_FETCH),
    (osrb_sources.KIND_CONTAINER, "upstream source cloned into the image", EV_BUILD_FETCH),
    (osrb_sources.KIND_COMPOSE, "compose service image", EV_CONTAINER_IMAGE),
    (osrb_sources.KIND_CHART, "Helm chart dependency", EV_CONTAINER_IMAGE),
    (osrb_sources.KIND_BUILD, "third-party source built during the build", EV_BUILD_FETCH),
    (osrb_sources.KIND_BUILD, "native dependency required at build time", EV_IMPORTED_ONLY),
    (osrb_sources.KIND_BUILD, "native dependency resolved via pkg-config", EV_IMPORTED_ONLY),
    (osrb_sources.KIND_CI, "GitHub Actions dependency", EV_CI_TOOLING),
    (osrb_sources.KIND_CI, "pre-commit hook", EV_CI_TOOLING),
    (osrb_sources.KIND_CI, "container action image", EV_CI_TOOLING),
)

#: A row `osrb_sources` emits to say "this is ours, not a third party's". It is
#: emitted rather than dropped there so the diff can show a subchart appearing;
#: a third-party inventory must not carry it at all.
_FIRST_PARTY_NOTE = "in-repo subchart"


def evidence_for_source_row(row: dict[str, str]) -> str:
    """Classify one `osrb_sources` row, or return UNKNOWN and say so.

    Returning UNKNOWN rather than falling back to a per-kind default is the
    whole point: a default would quietly file a new kind of container evidence
    as a base image, and the row would read as reviewed when it was guessed.
    """
    kind = row.get("source_kind", "")
    note = row.get("notes", "").split(";", 1)[0].strip()
    for candidate_kind, prefix, evidence in _EVIDENCE_BY_NOTE:
        if kind == candidate_kind and note.startswith(prefix):
            return evidence
    return UNKNOWN


def collect_sources(ref: str, paths: list[str], inventory: Inventory) -> tuple[int, int]:
    """Inventory Dockerfiles, compose files, charts, CMake and CI pins.

    Returns (unparsed file count, unclassified row count).
    """
    rows = osrb_sources.inventory_at_ref(ref, osrb_scan._git_show, paths)
    unparsed = 0
    unclassified = 0
    for row in sorted(rows.values(), key=lambda r: (r["source_file"], r["package"])):
        if row.get("change") == osrb_sources.CHANGE_UNCOVERED:
            # Not a package: `package` holds a path this repo cannot parse.
            _log(f"WARNING: {row['source_file']} could not be parsed: {row.get('notes', '')}")
            unparsed += 1
            continue
        if _FIRST_PARTY_NOTE in row.get("notes", ""):
            continue
        evidence = evidence_for_source_row(row)
        if evidence == UNKNOWN:
            _log(
                "WARNING: unclassified evidence "
                f"{row['source_kind']}/{row.get('notes', '')!r} for {row['package']}"
            )
            unclassified += 1
        inventory.add(
            package=row["package"],
            version=row.get("new_version", ""),
            module=row.get("module", "") or osrb_scan.owning_module(row["source_file"]),
            language=row.get("language", "") or UNKNOWN,
            evidence=evidence,
            source_file=row["source_file"],
            source_kind=row["source_kind"],
            license_expr=row.get("new_license", ""),
            scope=SCOPE_CI if evidence == EV_CI_TOOLING else SCOPE_RUNTIME,
        )
    return unparsed, unclassified


# ---------------------------------------------------------------------------
# Use side — reuse osrb_usage
# ---------------------------------------------------------------------------


def collect_usage(
    ref: str, paths: list[str], declared: dict[str, set[str]], inventory: Inventory
) -> None:
    """Add the third-party names the source reaches that its module never declares.

    `osrb_usage` is report-only in the delta gate because an import is
    heuristic evidence. Here it is a row like any other and it has to be: a
    package that ships and that no manifest names is precisely the drift a
    state inventory exists to surface. The row is still labelled for what it is
    — `imported-only`, `source_kind=usage` — so a reviewer can weigh it.
    """
    for row in osrb_usage.undeclared(ref, osrb_scan._git_show, paths, declared):
        inventory.add(
            package=row["package"],
            version=row.get("new_version", ""),
            module=row["module"],
            language=row.get("language", "") or UNKNOWN,
            evidence=EV_IMPORTED_ONLY,
            source_file=row["source_file"],
            source_kind=row["source_kind"],
            license_expr=row.get("new_license", ""),
        )


# ---------------------------------------------------------------------------
# Vendored source — nothing else walks it
# ---------------------------------------------------------------------------

#: Directory names that mean "upstream's code, committed here". Taken from
#: `osrb_usage`, which uses the same set to decide what not to scan as our
#: source; the two must agree, or a tree would be both first-party for imports
#: and third-party for vendoring.
_VENDOR_DIR_NAMES = frozenset(osrb_usage._VENDOR_DIR_NAMES) | {"node_modules"}

_ARCHIVE_SUFFIXES = (".tar.gz", ".tar.xz", ".tar.bz2", ".tgz", ".txz", ".zip")

#: `FFmpeg-n8.0.1.tar.gz` -> `8.0.1`. Deliberately narrow: only a trailing
#: `-<version>` on an archive filename counts, and the `v`/`n` release-tag
#: prefix is stripped. A directory name like `libjpeg-8b` is NOT mined for a
#: version — that is a guess about where a name ends, and a wrong version is
#: worse than UNKNOWN because it silently matches the wrong approved row.
_ARCHIVE_VERSION_RE = re.compile(r"-(?P<version>[vn]?\d[0-9A-Za-z.]*)$")

_SPDX_RE = re.compile(r"SPDX-License-Identifier:\s*(?P<expr>[^\r\n]+)")
_SPDX_VALID_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9.+-]*(?: (?:AND|OR|WITH) [A-Za-z0-9][A-Za-z0-9.+-]*)*$"
)


def vendored_roots(paths: list[str]) -> dict[str, list[str]]:
    """Map each vendored package directory to the files committed under it.

    The package is the directory immediately inside the vendor directory —
    `services/vios/include/3rdparty/aws/...` is the vendored package `aws`. The
    LAST vendor segment wins, so a vendor directory nested inside another one
    (webrtc's `third_party` under our `include/`) attributes to the inner
    upstream project rather than to the outer one.
    """
    roots: dict[str, list[str]] = {}
    for path in paths:
        parts = path.split("/")
        index = next(
            (
                i
                for i in range(len(parts) - 2, -1, -1)
                if parts[i].lower() in _VENDOR_DIR_NAMES
            ),
            None,
        )
        if index is None:
            continue
        roots.setdefault("/".join(parts[: index + 2]), []).append(path)
    return {root: sorted(files) for root, files in sorted(roots.items())}


def vendored_version(files: list[str]) -> str:
    """Version stated by a vendored archive's filename, else UNKNOWN.

    Only unanimous evidence counts: two archives naming two versions is a tree
    whose version a human has to settle, not one this can average.
    """
    versions = set()
    for path in files:
        base = path.rsplit("/", 1)[-1]
        for suffix in _ARCHIVE_SUFFIXES:
            if not base.lower().endswith(suffix):
                continue
            match = _ARCHIVE_VERSION_RE.search(base[: -len(suffix)])
            if match:
                versions.add(match.group("version").lstrip("vn"))
            break
    return versions.pop() if len(versions) == 1 else UNKNOWN


def vendored_language(files: list[str]) -> str:
    """Language of a vendored tree, from `osrb_usage`'s extension table.

    Most-common extension wins, ties broken alphabetically so the answer never
    depends on directory iteration order. A tree of archives or of files with
    no recognised extension stays UNKNOWN rather than being assigned one.
    """
    counts: dict[str, int] = {}
    for path in files:
        dot = path.rfind(".")
        language = osrb_usage.LANGUAGE_BY_EXT.get(path[dot:].lower()) if dot >= 0 else None
        if language:
            counts[language] = counts.get(language, 0) + 1
    if not counts:
        return UNKNOWN
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def spdx_licenses(ref: str, root: str) -> str:
    """Every distinct SPDX id declared inside a vendored tree, ";"-joined.

    Read with one `git grep` at the ref rather than by opening files, so this
    stays as cheap as the rest of the module and never touches the working
    tree. Every distinct id is kept, not the most common one: `aws` is 594
    files of Apache-2.0 and one of `GPL-2.0-only OR BSD-3-Clause`, and the
    single GPL file is the entire reason an OSRB reviewer is looking. ";" is a
    separator `osrb_scan.license_risk` already splits on, so the row's risk
    resolves to the worst operand.
    """
    try:
        out = subprocess.run(
            ["git", "grep", "-h", "-I", "-E", "SPDX-License-Identifier:", ref, "--", root],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    found: set[str] = set()
    for match in _SPDX_RE.finditer(out):
        # Strip the comment furniture an id is embedded in: `// SPDX-...: MIT`,
        # ` * SPDX-...: Apache-2.0. */`, a trailing full stop.
        expr = match.group("expr").strip().strip("*/").strip().rstrip(".").strip()
        if _SPDX_VALID_RE.match(expr):
            found.add(expr)
    return ";".join(sorted(found))


def collect_vendored(ref: str, paths: list[str], inventory: Inventory) -> None:
    """Add one row per third-party source tree committed into this repo.

    Neither sibling module walks these: `osrb_scan` reads declarations and
    there is none, and `osrb_usage` treats a vendored tree as somewhere imports
    RESOLVE to, not as a dependency in its own right. Vendoring is also the
    usage OSRB cares most about — a vendored copy can be modified, which is a
    different approval from linking the same library.
    """
    for root, files in vendored_roots(paths).items():
        inventory.add(
            package=root.rsplit("/", 1)[-1],
            version=vendored_version(files),
            module=osrb_scan.owning_module(root),
            language=vendored_language(files),
            evidence=EV_VENDORED_SOURCE,
            source_file=root,
            source_kind=KIND_VENDORED,
            license_expr=spdx_licenses(ref, root),
        )


# ---------------------------------------------------------------------------
# Licences from the tree, and the carry-forward
# ---------------------------------------------------------------------------

#: The two heading shapes this repo's attribution files use:
#: `## attrs:26.1.0` + `**License Type:** MIT` (3rdParty_Licenses.md), and
#: `## aioboto3 (15.5.0)` + `**License:** Apache-2.0` (LICENSE-3rd-party.txt).
_ATTRIBUTION_HEADING_RE = re.compile(
    r"^##\s+(?P<name>[^\s(][^(\n]*?)\s*(?:\((?P<paren>[^()\n]*)\)|:(?P<colon>[^\s:]+))\s*$"
)
_ATTRIBUTION_LICENSE_RE = re.compile(
    r"^\*\*License(?:\s+Type)?:\*\*\s*(?P<license>.+?)\s*$"
)


def parse_attribution(text: str) -> dict[tuple[str, str], str]:
    """Return {(lowercased name, version): license} from one attribution file.

    Only the licence LABEL is read; the licence body below it is prose and is
    not evidence of anything a parser can check. A heading with no licence line
    before the next heading contributes nothing rather than inheriting the
    previous package's licence.
    """
    out: dict[tuple[str, str], str] = {}
    current: tuple[str, str] | None = None
    for line in text.splitlines():
        heading = _ATTRIBUTION_HEADING_RE.match(line)
        if heading:
            version = (heading.group("paren") or heading.group("colon") or "").strip()
            name = heading.group("name").strip()
            current = (name.lower(), version) if name and version else None
            continue
        if current is None:
            continue
        license_match = _ATTRIBUTION_LICENSE_RE.match(line)
        if license_match:
            license_expr = license_match.group("license").strip()
            if license_expr and license_expr.lower() not in {"unknown", "n/a", "-"}:
                out.setdefault(current, license_expr)
            current = None
    return out


def attribution_licenses(ref: str, paths: list[str]) -> dict[tuple[str, str, str], str]:
    """Merge every attribution file in the tree into {(module, name, version): license}.

    Keyed by module first, with a ``("", name, version)`` entry as the
    cross-repo fallback, because these files disagree with each other on
    spelling far more often than on substance — "BSD License" here,
    "BSD-3-Clause" there, for the same release. A component's own attribution
    file is the one its reviewer signed, so it wins for that component's rows,
    and only a component contradicting ITSELF is worth a warning. Ties inside
    one scope keep the first file in sorted path order so the result never
    depends on traversal order.
    """
    merged: dict[tuple[str, str, str], str] = {}
    cross_module_disagreements = 0
    for path in sorted(paths):
        base = path.rsplit("/", 1)[-1].lower()
        if not osrb_scan._is_attribution(base):
            continue
        data = osrb_scan._git_show(ref, path)
        if data is None:
            continue
        module = osrb_scan.owning_module(path)
        for (name, version), license_expr in parse_attribution(
            data.decode("utf-8", "replace")
        ).items():
            scoped = merged.get((module, name, version))
            if scoped is None:
                merged[(module, name, version)] = license_expr
            elif scoped != license_expr:
                _log(
                    f"WARNING: {module} contradicts itself: {path} says {name} "
                    f"{version} is {license_expr!r}, an earlier file in the same "
                    f"module said {scoped!r}. Keeping the first."
                )
            fallback = merged.get(("", name, version))
            if fallback is None:
                merged[("", name, version)] = license_expr
            elif fallback != license_expr:
                cross_module_disagreements += 1
    if cross_module_disagreements:
        _log(
            f"{cross_module_disagreements} attribution entries are spelled "
            "differently in different modules; each module's own file wins for "
            "its own rows."
        )
    return merged


def previous_licenses(path: str) -> dict[tuple[str, str], str]:
    """Return {(name, version): license} from a previously committed inventory.

    See the module docstring: this is the no-network carry-forward, keyed on
    the (package, version) pair so a version bump can never inherit the old
    release's licence. UNKNOWN is not carried forward — there is nothing to
    carry.
    """
    out: dict[tuple[str, str], str] = {}
    with open(path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            license_expr = (row.get("license") or "").strip()
            package = (row.get("package") or "").strip()
            version = (row.get("version") or "").strip()
            if not package or not license_expr or license_expr == UNKNOWN:
                continue
            out.setdefault((package.lower(), version), license_expr)
    return out


def unanimous_parser_licenses(entries: list[_Entry]) -> dict[tuple[str, str], str]:
    """{(name, version): license} for releases every parser in the tree agrees on.

    npm records a licence per package, but not every lockfile in this repo
    carries the field: `deepmerge 4.3.1` is bare in one service's
    package-lock.json and MIT in another's. A licence is a property of a
    release, not of the file that happened to mention it, so one module's
    metadata answers for the same release elsewhere in the same tree.

    Unanimity is required. Two lockfiles disagreeing about one release is a
    question for a human, and picking a side would make the answer depend on
    which module was walked first — the exact non-determinism this file cannot
    have.
    """
    candidates: dict[tuple[str, str], set[str]] = {}
    for entry in entries:
        if not entry.licenses:
            continue
        candidates.setdefault((entry.package.lower(), entry.version), set()).update(
            entry.licenses
        )
    return {key: next(iter(values)) for key, values in candidates.items() if len(values) == 1}


def resolve_license(
    entry: _Entry,
    attribution: dict[tuple[str, str, str], str],
    previous: dict[tuple[str, str], str],
    in_tree: dict[tuple[str, str], str] | None = None,
) -> tuple[str, str]:
    """Pick a licence for one entry, or UNKNOWN. Never guesses.

    Order: what the parser read out of this row's own lockfile, then this
    component's attribution file for that exact release, then any other
    component's, then what another parser in the same tree recorded for the
    same release, then the carry-forward.

    Returns (licence, where it came from). The provenance is not a CSV column —
    it would be one more published surface to keep stable — but it is what the
    run summary counts, so "how much of this file is carried forward rather
    than read out of the tree" is answerable without diffing two runs.
    """
    if entry.licenses:
        # More than one only happens when the tree itself says two different
        # things (a vendored tree with mixed SPDX headers, or the same release
        # locked twice with different metadata). Keep both, sorted: the risk
        # column then resolves to the worst operand instead of to whichever
        # answer happened to be read first.
        return ";".join(sorted(entry.licenses)), "parser"
    name = entry.package.lower()
    scoped = attribution.get((entry.module, name, entry.version))
    if scoped:
        return scoped, "attribution"
    shared = attribution.get(("", name, entry.version))
    if shared:
        return shared, "attribution"
    elsewhere = (in_tree or {}).get((name, entry.version))
    if elsewhere:
        return elsewhere, "another-parser"
    carried = previous.get((name, entry.version))
    if carried:
        return carried, "carried-forward"
    return UNKNOWN, "unresolved"


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def row_for(entry: _Entry, license_expr: str) -> dict[str, str]:
    """Render one collected entry as the CSV row. Pure formatting."""
    vendored = EV_VENDORED_SOURCE in entry.evidence
    return {
        "package": entry.package,
        "version": entry.version,
        "license": license_expr,
        "module": entry.module,
        "language": entry.language,
        "source_kind": ";".join(sorted(entry.source_kinds)),
        "source_file": ";".join(sorted(entry.source_files)),
        "dep_scope": SCOPE_CI if entry.scopes == {SCOPE_CI} else SCOPE_RUNTIME,
        "vendored_in_repo": "yes" if vendored else "no",
        # Whether a vendored copy was modified cannot be answered from this
        # tree alone — it needs the upstream release to diff against, which is
        # a network fetch this module may not make. UNKNOWN is the honest
        # answer and it is also the one that gets a human to look, which is
        # right: a modified copy is a different OSRB approval.
        "copied_adapted": UNKNOWN if vendored else "no",
        "container_only": "yes" if entry.evidence <= _CONTAINER_EVIDENCE else "no",
        "usage_evidence": ";".join(sorted(entry.evidence)),
        "risk": osrb_scan.license_risk("" if license_expr == UNKNOWN else license_expr),
    }


def sort_key(row: dict[str, str]) -> tuple[str, ...]:
    """Total order over the CSV.

    Case-folded on the package so `Flask` and `flask` sort together, with the
    exact spelling as a tiebreak so the order is total and cannot depend on
    which row was built first.
    """
    return (
        row["package"].lower(),
        row["package"],
        row["version"],
        row["language"],
        row["module"],
    )


def build(
    ref: str, previous_path: str | None = None
) -> tuple[list[dict[str, str]], dict[str, int]]:
    """Return (sorted rows, counters) for the whole tree at `ref`."""
    paths = osrb_scan._ls_tree(ref)
    if not paths:
        raise SystemExit(f"no tracked files at ref {ref!r} — is this a git checkout?")
    inventory = Inventory()

    manifest_failures = collect_declared(ref, paths, inventory)
    node_workspace_names = osrb_scan.node_workspace_names_by_module(ref, paths)
    # The use-side pass compares per module against what that module declares,
    # so it has to run after the declaration pass and read its result.
    declared = inventory.names_by_module()
    collect_vendored(ref, paths, inventory)
    unparsed, unclassified = collect_sources(ref, paths, inventory)
    collect_usage(ref, paths, declared, inventory)
    workspace_deduped = inventory.drop_local_node_workspace_rows(node_workspace_names)
    deduped = inventory.drop_manifest_rows_covered_by_a_lockfile()

    attribution = attribution_licenses(ref, paths)
    previous = previous_licenses(previous_path) if previous_path else {}

    entries = inventory.entries()
    in_tree = unanimous_parser_licenses(entries)

    rows: list[dict[str, str]] = []
    provenance: dict[str, int] = {}
    for entry in entries:
        license_expr, source = resolve_license(entry, attribution, previous, in_tree)
        provenance[source] = provenance.get(source, 0) + 1
        rows.append(row_for(entry, license_expr))
    rows.sort(key=sort_key)

    counters = {
        "rows": len(rows),
        "unparsed_source_files": unparsed,
        "unparsed_manifests": len(manifest_failures),
        "manifest_rows_suppressed_as_node_workspaces": workspace_deduped,
        "unclassified_evidence": unclassified,
        "manifest_rows_superseded_by_a_lockfile": deduped,
        "license_from_parser": provenance.get("parser", 0),
        "license_from_attribution_file": provenance.get("attribution", 0),
        "license_from_another_parser_in_tree": provenance.get("another-parser", 0),
        "license_carried_forward": provenance.get("carried-forward", 0),
        "license_unknown": provenance.get("unresolved", 0),
    }
    return rows, counters


def write_csv(rows: list[dict[str, str]], output: str) -> None:
    """Write the inventory with LF endings, because it is committed and diffed.

    `csv` defaults to CRLF; a CRLF file in a repo full of LF files fights
    .gitattributes and every editor that touches it.
    """
    with open(output, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--ref", default="HEAD", help="Git ref to inventory.")
    parser.add_argument(
        "--output", default=".github/osrb/inventory.csv", help="CSV output path."
    )
    parser.add_argument(
        "--previous",
        default=None,
        help=(
            "A previously committed inventory CSV. Licences already resolved "
            "there are carried forward for any (package, version) that has not "
            "changed, because this tool makes no network calls."
        ),
    )
    args = parser.parse_args(argv)

    rows, counters = build(args.ref, args.previous)
    write_csv(rows, args.output)

    _log(f"Wrote {len(rows)} rows to {args.output}")
    for name, value in sorted(counters.items()):
        if name != "rows":
            _log(f"  {name}: {value}")
    buckets: dict[str, int] = {}
    for row in rows:
        for evidence in row["usage_evidence"].split(";"):
            buckets[evidence] = buckets.get(evidence, 0) + 1
    for evidence, count in sorted(buckets.items()):
        _log(f"  usage_evidence {evidence}: {count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
