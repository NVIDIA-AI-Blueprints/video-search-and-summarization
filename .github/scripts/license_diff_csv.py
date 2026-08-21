#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Generate a license-diff CSV between two git refs for OSRB review.

Walks every Python lockfile (`uv.lock`, `Pipfile.lock`, `pdm.lock`,
`poetry.lock`) and Node lockfile (`package-lock.json`) tracked by the repo at
the base and head refs —
at any nesting depth (services/*, tools/*, repo root) — diffs the
(package, version) sets, and writes one CSV row per change. Python rows are
enriched with license + repository URL from PyPI; Node rows use the metadata
embedded in the lockfile.

For Pipfile.lock only the `default` (runtime) section is inventoried — dev-only
deps never ship, so OSRB does not review them. PDM and Poetry follow the same
rule: only packages in the `default` / `main` groups are inventoried.

Services that ship a plain `requirements.txt` or `pyproject.toml` (no
recognized lockfile) get a lighter, name-level pass: direct dependencies
ADDED to / REMOVED from those manifests are reported (with the license of the
pinned version, or of the latest release when the line is unpinned), and
`==`-pinned bumps are flagged. This is driven by the committed file diff, so
it is deterministic — unchanged unpinned lines never produce phantom rows.
It does NOT resolve the transitive closure; a committed lockfile remains the
way to get full coverage.

Added files whose names look like a lock or manifest (`pyproject.toml`,
`*.lock`, `requirements*.txt`) but are not in that scan set are logged as a
WARNING so a new lock format cannot stay invisible PR-by-PR.

CSV columns: language, package, change, old_version, new_version, old_license,
new_license, repository_url, notes.

Usage:
    python license_diff_csv.py --base-ref origin/develop --output license-diff.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request

PYPI_TIMEOUT = 10
PYPI_INDEX = "https://pypi.org/pypi"

PackageKey = tuple[str, str]
Inventory = dict[PackageKey, dict[str, str]]


def _log(msg: str) -> None:
    print(f"[license-diff] {msg}", file=sys.stderr)


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True)


def _git_show(ref: str, path: str) -> bytes | None:
    try:
        return subprocess.check_output(
            ["git", "show", f"{ref}:{path}"], stderr=subprocess.DEVNULL
        )
    except subprocess.CalledProcessError:
        return None


def _ls_tree(ref: str) -> list[str]:
    try:
        out = _git("ls-tree", "-r", "--name-only", ref)
    except subprocess.CalledProcessError:
        return []
    return out.splitlines()


def _list_lockfiles(ref: str, filename: str) -> list[str]:
    return [
        p
        for p in _ls_tree(ref)
        if p.endswith("/" + filename) or p == filename
        if "node_modules/" not in p
    ]


# Basenames this scanner inventories. The tool is filename-recursive (any
# nesting depth), not a path allow-list — the coverage gap is an unrecognized
# *name*, which is how pdm.lock on RTVI-VLM slipped past OSRB.
_SCANNED_BASENAMES = {
    "uv.lock",
    "pipfile.lock",
    "pdm.lock",
    "poetry.lock",
    "package-lock.json",
    "pyproject.toml",
}

# Lock-shaped files that are not language package inventories.
_NON_PACKAGE_LOCK_BASENAMES = {
    "chart.lock",  # Helm chart dependency pin, not a language lockfile
}


def _basename(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def looks_like_manifest(path: str) -> bool:
    """True for added-file names that OSRB would expect License Diff to read."""
    if "node_modules/" in path:
        return False
    base = _basename(path).lower()
    if base in _NON_PACKAGE_LOCK_BASENAMES:
        return False
    if base == "pyproject.toml" or base.endswith(".lock"):
        return True
    return base.startswith("requirements") and base.endswith(".txt")


def is_scanned_manifest(path: str) -> bool:
    """True when this filename is one the inventory walkers already handle."""
    base = _basename(path).lower()
    if base in _SCANNED_BASENAMES:
        return True
    # requirements*.txt is handled, including the apt exclusion — that skip
    # is deliberate, not a coverage gap.
    return base.startswith("requirements") and base.endswith(".txt")


def unscanned_added_manifests(base_paths: list[str], head_paths: list[str]) -> list[str]:
    """Return added lock/manifest paths that no inventory walker will read."""
    added = sorted(set(head_paths) - set(base_paths))
    return [
        path
        for path in added
        if looks_like_manifest(path) and not is_scanned_manifest(path)
    ]


def warn_unscanned_added_manifests(paths: list[str]) -> None:
    """Log each coverage gap to stderr and as a GitHub Actions warning."""
    for path in paths:
        message = f"Skipped path — not in scan set: {path}"
        _log(f"WARNING: {message}")
        print(
            f"::warning title=License Diff coverage gap::{message}",
            file=sys.stderr,
        )


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
        if "://" in line or line.startswith(("git+", "http", "file:")):
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


def requirements_inventory(ref: str) -> dict[str, str]:
    """Merge every requirements*.txt at `ref` into {name: pinned_version_or_''}.

    `requirements_apt.txt` (system/apt packages, not PyPI) is excluded.
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


def pyproject_inventory(ref: str) -> dict[str, str]:
    """Merge every pyproject.toml at `ref` into {name: pinned_version_or_''}."""
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
    return merged


def diff_requirements(
    base: dict[str, str],
    head: dict[str, str],
    covered_names: set[str],
    *,
    source: str = "requirements.txt",
) -> list[dict[str, str]]:
    """Diff direct-dependency NAME sets across requirements.txt / pyproject.toml.

    Reports packages added to / removed from the manifest, and `==`-pinned
    version bumps. Packages already inventoried by a lockfile (`covered_names`)
    are skipped — the lockfile diff covers them more accurately. Driven purely
    by the committed file contents, so it is deterministic: unchanged unpinned
    lines never produce phantom rows.
    """
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
            rows.append({
                "language": "python", "package": name, "change": "added",
                "old_version": "", "new_version": (hv or f"latest ({resolved})"),
                "old_license": "", "new_license": meta.get("license", ""),
                "repository_url": meta.get("repository_url", ""), "notes": note,
            })
        elif in_base and not in_head:  # removed direct dependency
            rows.append({
                "language": "python", "package": name, "change": "removed",
                "old_version": bv or "(unpinned)", "new_version": "",
                "old_license": "", "new_license": "",
                "repository_url": "", "notes": f"removed from {source}",
            })
        elif bv != hv and bv and hv:  # pinned == bump on both sides
            old_meta = pypi_metadata(name, bv)
            new_meta = pypi_metadata(name, hv)
            old_license = old_meta.get("license", "")
            new_license = new_meta.get("license", "")
            notes = f"{source} version pin changed"
            if old_license and new_license and old_license != new_license:
                notes += "; license changed"
            rows.append({
                "language": "python", "package": name, "change": "updated",
                "old_version": bv, "new_version": hv,
                "old_license": old_license, "new_license": new_license,
                "repository_url": new_meta.get("repository_url", ""),
                "notes": notes,
            })
    return rows


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
            repo = f"https://www.npmjs.com/package/{name}/v/{version}"
        repo = repo.removeprefix("git+").removesuffix(".git")
        out[(name, version)] = {
            "license": str(lic),
            "repository_url": repo,
        }
    return out


def _inventory_at_ref(
    ref: str, filename: str, parser
) -> Inventory:
    inv: Inventory = {}
    for path in _list_lockfiles(ref, filename):
        data = _git_show(ref, path)
        if data is None:
            continue
        try:
            for key, meta in parser(data).items():
                inv.setdefault(key, meta)
        except (tomllib.TOMLDecodeError, json.JSONDecodeError) as exc:
            _log(f"skip {path}@{ref}: {exc}")
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
                rows.append(_row(language, name, "added", "", v, "", meta))
            continue
        if not head_versions:
            for v in only_old:
                meta = base[(name, v)]
                if language == "python" and not meta.get("license"):
                    meta = {**meta, **pypi_metadata(name, v)}
                rows.append(_row(language, name, "removed", v, "", meta.get("license", ""), meta))
            continue

        # Coexisting set changed (version bump, license change, or both).
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
        for v in only_new or sorted(head_versions):
            m = head.get((name, v), {})
            if language == "python" and not m.get("repository_url"):
                m = {**m, **pypi_metadata(name, v)}
            if m.get("repository_url"):
                repo = m["repository_url"]
                break
        notes = "license changed" if old_lic and new_lic and old_lic != new_lic else ""
        rows.append(
            {
                "language": language,
                "package": name,
                "change": "updated",
                "old_version": old_v,
                "new_version": new_v,
                "old_license": old_lic,
                "new_license": new_lic,
                "repository_url": repo,
                "notes": notes,
            }
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
    return {
        "language": language,
        "package": name,
        "change": change,
        "old_version": old_v,
        "new_version": new_v,
        "old_license": old_lic if change == "removed" else "",
        "new_license": meta.get("license", "") if change != "removed" else "",
        "repository_url": meta.get("repository_url", ""),
        "notes": "",
    }


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-ref", required=True, help="Git ref to diff against.")
    parser.add_argument("--head-ref", default="HEAD", help="Git ref under review.")
    parser.add_argument("--output", default="license-diff.csv", help="CSV output path.")
    args = parser.parse_args()

    _log(f"Comparing {args.base_ref} -> {args.head_ref}")
    warn_unscanned_added_manifests(
        unscanned_added_manifests(_ls_tree(args.base_ref), _ls_tree(args.head_ref))
    )

    # Each (filename, parser) is scanned recursively across the whole repo tree
    # at the given ref (_list_lockfiles uses `git ls-tree -r`), so lockfiles at
    # any nesting depth — services/<svc>/..., tools/<tool>/..., or the repo
    # root — are all picked up. Python deps may be locked by uv (uv.lock),
    # pipenv (Pipfile.lock), PDM (pdm.lock), or Poetry (poetry.lock).
    PYTHON_LOCKS = [
        ("uv.lock", parse_uv_lock),
        ("Pipfile.lock", parse_pipfile_lock),
        ("pdm.lock", parse_pdm_lock),
        ("poetry.lock", parse_poetry_lock),
    ]

    def python_inventory(ref: str) -> Inventory:
        merged: Inventory = {}
        for filename, parser_fn in PYTHON_LOCKS:
            for key, meta in _inventory_at_ref(ref, filename, parser_fn).items():
                merged.setdefault(key, meta)
        return merged

    py_base = python_inventory(args.base_ref)
    py_head = python_inventory(args.head_ref)
    nd_base = _inventory_at_ref(args.base_ref, "package-lock.json", parse_node_lock)
    nd_head = _inventory_at_ref(args.head_ref, "package-lock.json", parse_node_lock)

    rows: list[dict[str, str]] = []
    rows.extend(diff_language("python", py_base, py_head))
    rows.extend(diff_language("node", nd_base, nd_head))

    # Minimal manifest coverage: catch direct deps added to (or removed from)
    # plain requirements.txt / pyproject.toml files that have no recognized
    # lockfile. Deduped against names already in the lockfile inventory, which
    # the diff above covers more accurately (resolved version + transitive
    # closure). pyproject.toml is also deduped against requirements.txt so a
    # package declared in both does not produce two rows.
    lock_names = {name for name, _ in py_base} | {name for name, _ in py_head}
    req_base = requirements_inventory(args.base_ref)
    req_head = requirements_inventory(args.head_ref)
    rows.extend(diff_requirements(req_base, req_head, lock_names))
    direct_covered = lock_names | set(req_base) | set(req_head)
    pj_base = pyproject_inventory(args.base_ref)
    pj_head = pyproject_inventory(args.head_ref)
    rows.extend(
        diff_requirements(pj_base, pj_head, direct_covered, source="pyproject.toml")
    )

    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    _log(f"Wrote {len(rows)} diff rows to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
