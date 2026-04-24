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
"""Emit ``license-checker``-shaped JSON for the production dependency
tree of an npm workspace project.

The standard ``license-checker --production`` only inspects the package
at the working directory, which is useless for a Turborepo / npm
workspaces monorepo where the root ``package.json`` declares no
production dependencies (everything is in ``apps/*`` and ``packages/*``).

This script walks the project with::

    npm ls --omit=dev --all --json --workspaces --include-workspace-root

then resolves each ``(name, version)`` against the hoisted
``node_modules`` tree to read the ``license`` field of each package's
``package.json``.

The output is written to stdout in the same shape as
``license-checker-rseidelsohn --json`` so that
``.github/scripts/check_licenses.py`` can consume it directly::

    {
        "<name>@<version>": {
            "licenses": "<spdx or string>",
            "repository": "<url>",
            "path": "node_modules/.../<name>"
        },
        ...
    }
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys


def run_npm_ls(project_dir: Path) -> dict:
    cmd = [
        "npm",
        "ls",
        "--omit=dev",
        "--all",
        "--json",
        "--workspaces",
        "--include-workspace-root",
    ]
    res = subprocess.run(cmd, cwd=project_dir, capture_output=True, text=True, check=False)
    if not res.stdout:
        sys.stderr.write(res.stderr)
        sys.exit(2)
    try:
        return json.loads(res.stdout)
    except json.JSONDecodeError as e:
        sys.stderr.write(f"failed to parse `npm ls` output: {e}\n")
        sys.stderr.write(res.stdout[:1000])
        sys.exit(2)


def collect_packages(tree: dict) -> dict[tuple[str, str], dict]:
    """Walk the npm-ls tree, returning {(name, version): node}."""
    seen: dict[tuple[str, str], dict] = {}

    def visit(node: dict) -> None:
        for name, sub in (node.get("dependencies") or {}).items():
            version = sub.get("version") or ""
            key = (name, version)
            if key in seen:
                continue
            seen[key] = sub
            visit(sub)

    visit(tree)
    return seen


def find_package_json(project_dir: Path, name: str) -> Path | None:
    """Find a package's ``package.json`` in any nested ``node_modules``.

    npm hoists most deps to the top-level ``node_modules`` but a
    package can also sit in a nested ``node_modules`` (workspace local
    or version conflict). We search the top-level first, then any
    ``node_modules/**/<name>/package.json``.
    """
    direct = project_dir / "node_modules" / name / "package.json"
    if direct.exists():
        return direct
    for p in project_dir.glob(f"node_modules/**/{name}/package.json"):
        if p.is_file():
            return p
    return None


def extract_license(meta: dict) -> str:
    """Return the most useful license string from a ``package.json`` dict."""
    lic = meta.get("license")
    if isinstance(lic, str) and lic:
        return lic
    if isinstance(lic, dict):
        return str(lic.get("type") or lic.get("name") or "")
    licenses = meta.get("licenses")
    if isinstance(licenses, list) and licenses:
        names = []
        for entry in licenses:
            if isinstance(entry, str):
                names.append(entry)
            elif isinstance(entry, dict) and (entry.get("type") or entry.get("name")):
                names.append(str(entry.get("type") or entry.get("name")))
        if names:
            return " AND ".join(names)
    return "UNKNOWN"


def repository_url(meta: dict) -> str:
    repo = meta.get("repository")
    if isinstance(repo, str):
        return repo
    if isinstance(repo, dict):
        return str(repo.get("url", ""))
    return ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--project-dir",
        type=Path,
        default=Path.cwd(),
        help="npm workspace root containing package.json + node_modules.",
    )
    args = parser.parse_args(argv)

    project_dir = args.project_dir.resolve()
    if not (project_dir / "node_modules").exists():
        sys.stderr.write(f"node_modules not found in {project_dir}; run `npm ci` first.\n")
        return 2

    tree = run_npm_ls(project_dir)
    packages = collect_packages(tree)

    out: dict[str, dict] = {}
    for (name, version), node in sorted(packages.items()):
        meta_path = None
        node_path = node.get("path") or node.get("realpath")
        if node_path:
            candidate = Path(node_path) / "package.json"
            if candidate.exists():
                meta_path = candidate
        if meta_path is None:
            meta_path = find_package_json(project_dir, name)

        license_str = "UNKNOWN"
        repo = ""
        if meta_path:
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                meta = {}
            license_str = extract_license(meta)
            repo = repository_url(meta)

        out[f"{name}@{version}"] = {
            "licenses": license_str,
            "repository": repo,
            "path": str(meta_path.parent if meta_path else ""),
        }

    json.dump(out, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
