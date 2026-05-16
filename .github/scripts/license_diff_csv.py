#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Generate a license-diff CSV between two git refs for OSRB review.

Walks every `uv.lock` (Python) and `package-lock.json` (Node) tracked by the
repo at the base and head refs, diffs the (package, version) sets, and writes
one CSV row per change. Python rows are enriched with license + repository URL
from PyPI; Node rows use the metadata embedded in the lockfile.

CSV columns: language, package, change, old_version, new_version, old_license,
new_license, repository_url, notes.

Usage:
    python license_diff_csv.py --base-ref origin/develop --output license-diff.csv
"""

from __future__ import annotations

import argparse
import csv
import json
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


def parse_uv_lock(data: bytes) -> Inventory:
    """Return {(name, version): {repository_url}} parsed from uv.lock."""
    doc = tomllib.loads(data.decode("utf-8"))
    out: Inventory = {}
    for pkg in doc.get("package", []) or []:
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
    """Return license + repository_url for one PyPI package version."""
    key = (name.lower(), version)
    if key in _pypi_cache:
        return _pypi_cache[key]
    url = f"{PYPI_INDEX}/{name}/{version}/json"
    try:
        with urllib.request.urlopen(url, timeout=PYPI_TIMEOUT) as response:
            doc = json.load(response)
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError):
        result = {"license": "", "repository_url": ""}
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
        result = {"license": lic, "repository_url": repo}
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

    py_base = _inventory_at_ref(args.base_ref, "uv.lock", parse_uv_lock)
    py_head = _inventory_at_ref(args.head_ref, "uv.lock", parse_uv_lock)
    nd_base = _inventory_at_ref(args.base_ref, "package-lock.json", parse_node_lock)
    nd_head = _inventory_at_ref(args.head_ref, "package-lock.json", parse_node_lock)

    rows: list[dict[str, str]] = []
    rows.extend(diff_language("python", py_base, py_head))
    rows.extend(diff_language("node", nd_base, nd_head))

    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    _log(f"Wrote {len(rows)} diff rows to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
