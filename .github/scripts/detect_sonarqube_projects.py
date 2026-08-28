#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Decide which SonarQube matrix legs a workflow run must execute.

Emits a GitHub Actions matrix (JSON) with one entry per catalogued project
that needs a scan.

Diff-range rules:

* ``pull_request`` → diff ``merge-base(PR base, HEAD)..HEAD`` so the matrix
  reflects the whole PR, not just its last push. Every catalogued
  ``SonarQube Scan (<name>)`` check is still *reported* (GitHub has no
  path filter on required contexts, and the same no-op is used for the
  rest so a later ruleset addition cannot stall merge). Untouched legs
  run as no-ops (``scan=false``). A real scan runs only when that tree
  changed — that is when the check can fail and actually gate the PR.
* ``push`` to ``main`` / ``develop`` / ``release/**``, and
  ``workflow_dispatch`` → scan every project. Branch analysis and a manual
  run must not silently drop a leg.
* Unresolvable PR base, or a failed ``git diff`` → **scan everything**.
  Scanning too much is safe; skipping a project that changed is the
  failure mode this replaces.
* A change to this script or ``.github/workflows/sonarqube.yml`` also
  scans every project (the scan contract changed).

Do not put a top-level ``paths:`` filter on the workflow. GitHub evaluates
that against the pushed commits, not the cumulative PR, and can skip the
workflow entirely on an unrelated follow-up push.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from detect_changed_images import (  # noqa: E402
    changed_paths,
    commit_exists,
    paths_changed_under,
    run_git,
)

CONTRACT_PATHS = (
    ".github/workflows/sonarqube.yml",
    ".github/scripts/detect_sonarqube_projects.py",
)

SONAR_RUNNER = "sonarqube-workflows-bp-sre"
SKIP_RUNNER = "ubuntu-latest"

# ``paths`` is the skip gate. ``sources`` / ``tests`` stay the scanner
# configuration and may be a narrower list than the directory we watch.
PROJECTS: list[dict[str, Any]] = [
    {
        "name": "agent",
        "project_key": (
            "TEGRASW_metropolis_video-search-and-summarization-agent"
            "_video-search-and-summarization"
        ),
        "project_name": "video-search-and-summarization-agent",
        "sources": "services/agent",
        "tests": (
            "services/agent/packages/vss_core/tests,"
            "services/agent/packages/vss_agents/tests,"
            "services/agent/packages/vss_cli/tests"
        ),
        "python_version": "3.13",
        "paths": ["services/agent"],
    },
    {
        "name": "alert",
        "project_key": "TEGRASW_metropolis_vss-alert-verification_alert_agent",
        "project_name": "metropolis-vss-alert-verification",
        "sources": "services/alert/src",
        "tests": "services/alert/test/unit",
        "python_version": "3.13",
        "paths": ["services/alert"],
    },
    {
        "name": "ui",
        "project_key": (
            "TEGRASW_metropolis_video-search-and-summarization-ui"
            "_video-search-and-summarization"
        ),
        "project_name": "video-search-and-summarization-ui",
        "sources": "services/ui",
        "tests": "",
        "paths": ["services/ui"],
    },
    {
        "name": "skills",
        "project_key": (
            "TEGRASW_metropolis_video-search-and-summarization-skills"
            "_video-search-and-summarization"
        ),
        "project_name": "video-search-and-summarization-skills",
        "sources": "skills",
        "tests": "",
        "paths": ["skills"],
    },
    {
        "name": "behavior-analytics",
        "project_key": "TEGRASW_metropolis_vss-behavior-analytics_py-analytics-stream",
        "project_name": "metropolis-vss-behavior-analytics",
        "sources": (
            "services/analytics/behavior-analytics/src,"
            "services/analytics/behavior-analytics/docker"
        ),
        "tests": "services/analytics/behavior-analytics/tests",
        "python_version": "3.13",
        "paths": ["services/analytics/behavior-analytics"],
    },
    {
        "name": "video-analytics-api",
        "project_key": "TEGRASW_metropolis_vss-video-analytics-api_mdata_web-apis",
        "project_name": "metropolis-vss-video-analytics-api",
        "sources": (
            "services/analytics/video-analytics-api/src,"
            "services/analytics/video-analytics-api/docker"
        ),
        "tests": "services/analytics/video-analytics-api/test",
        "node_version": "22.22.3",
        "paths": ["services/analytics/video-analytics-api"],
    },
    {
        "name": "spatialai-data-utils",
        "project_key": (
            "TEGRASW_METROPOLIS_spatialai-data-utils_video-search-and-summarization"
        ),
        "project_name": "METROPOLIS-spatialai-data-utils",
        "sources": "libs/analytics/spatialai-data-utils/spatialai_data_utils",
        "tests": "libs/analytics/spatialai-data-utils/tests",
        "python_version": "3.13",
        "paths": ["libs/analytics/spatialai-data-utils"],
    },
    {
        "name": "sdr-mw-l",
        "project_key": "TEGRASW_A3IENG_embedded-metropolis-sdr_wdm",
        "project_name": "embedded-metropolis-sdr",
        "sources": (
            "services/sdrc/app.py,services/sdrc/config.py,"
            "services/sdrc/run_workloads.py,services/sdrc/lib,services/sdrc/envoy"
        ),
        "tests": "services/sdrc/tests",
        "python_version": "3.10",
        "paths": ["services/sdrc"],
    },
    {
        "name": "vss-configurator",
        "project_key": "TEGRASW_metropolis_vss-configurator_blueprint-configurator",
        "project_name": "metropolis-vss-configurator",
        "sources": (
            "services/configurators/vss-configurator/app,"
            "services/configurators/vss-configurator/docker"
        ),
        "tests": "services/configurators/vss-configurator/tests",
        "python_version": "3.13",
        "paths": ["services/configurators/vss-configurator"],
    },
    {
        "name": "vss-rt-config-adaptor",
        "project_key": "TEGRASW_metropolis_vss-rt-config-adaptor_ds-configurator",
        "project_name": "metropolis-vss-rt-config-adaptor",
        "sources": (
            "services/configurators/vss-rt-config-adaptor/app,"
            "services/configurators/vss-rt-config-adaptor/docker"
        ),
        "tests": "services/configurators/vss-rt-config-adaptor/tests",
        "python_version": "3.13",
        "paths": ["services/configurators/vss-rt-config-adaptor"],
    },
]


def resolve_sonar_diff_base(
    repo: Path, event_name: str, base_ref: str, pr_base_sha: str
) -> tuple[str | None, str]:
    """Return ``(base_commit, reason)``; ``None`` means scan every project."""
    if event_name != "pull_request":
        return None, f"{event_name}; scanning all projects"

    candidates: list[str] = []
    if pr_base_sha:
        candidates.append(pr_base_sha)
    if base_ref:
        candidates.extend((f"origin/{base_ref}", base_ref))

    for candidate in candidates:
        if candidate == pr_base_sha and not commit_exists(repo, pr_base_sha):
            continue
        result = run_git(repo, "merge-base", candidate, "HEAD")
        if result.returncode == 0:
            base = result.stdout.strip()
            return base, f"PR merge-base with {candidate}: {base[:12]}"
    return None, "could not resolve PR base; scanning all projects"


def matrix_entry(project: dict[str, Any], *, scan: bool) -> dict[str, Any]:
    entry = {key: value for key, value in project.items() if key != "paths"}
    entry["scan"] = "true" if scan else "false"
    entry["runner"] = SONAR_RUNNER if scan else SKIP_RUNNER
    return entry


def to_matrix(entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {"include": entries}


def matrix_for(
    selected: list[dict[str, Any]], event_name: str, _base_ref: str
) -> list[dict[str, Any]]:
    """On PRs, report every project; scan only those whose trees changed."""
    selected_names = {project["name"] for project in selected}
    if event_name == "pull_request":
        return [
            matrix_entry(project, scan=project["name"] in selected_names)
            for project in PROJECTS
        ]
    return [matrix_entry(project, scan=True) for project in selected]


def contract_changed(changed: list[str]) -> bool:
    return any(
        path == contract or path.startswith(contract.rstrip("/") + "/")
        for path in changed
        for contract in CONTRACT_PATHS
    )


def select_projects(
    changed: list[str] | None,
) -> tuple[list[dict[str, Any]], str]:
    if changed is None:
        return list(PROJECTS), "scanning all SonarQube projects"
    if contract_changed(changed):
        return list(PROJECTS), "scan contract changed; scanning all projects"
    selected = [
        project
        for project in PROJECTS
        if any(paths_changed_under(changed, directory) for directory in project["paths"])
    ]
    if selected:
        names = ", ".join(project["name"] for project in selected)
        return selected, f"scanning {names}"
    return [], f"0 of {len(PROJECTS)} projects changed"


def plan(
    repo: Path, event_name: str, base_ref: str, pr_base_sha: str
) -> dict[str, Any]:
    base, reason = resolve_sonar_diff_base(repo, event_name, base_ref, pr_base_sha)
    changed = changed_paths(repo, base) if base else None
    if base and changed is None:
        reason += "; diff failed; scanning all projects"
        changed = None
    selected, selection_reason = select_projects(changed)
    include = matrix_for(selected, event_name, base_ref)
    noop_count = sum(1 for row in include if row["scan"] != "true")
    if noop_count:
        selection_reason += f"; {noop_count} no-op report(s)"
    return {
        "reason": f"{reason}; {selection_reason}",
        "count": len(selected),
        "any": len(include) > 0,
        "projects": [project["name"] for project in selected],
        "matrix": to_matrix(include),
    }


def write_github_output(path: Path, result: dict[str, Any]) -> None:
    matrix_json = json.dumps(result["matrix"], separators=(",", ":"))
    projects_json = json.dumps(result["projects"], separators=(",", ":"))
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"any={str(result['any']).lower()}\n")
        handle.write(f"count={result['count']}\n")
        handle.write(f"projects={projects_json}\n")
        handle.write(f"matrix={matrix_json}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--base-ref", default="")
    parser.add_argument("--pr-base-sha", default="")
    parser.add_argument(
        "--github-output",
        type=Path,
        default=None,
        help="Append GITHUB_OUTPUT keys (any, count, projects, matrix).",
    )
    args = parser.parse_args()
    result = plan(
        args.repo_root.resolve(),
        args.event_name,
        args.base_ref,
        args.pr_base_sha,
    )
    print(json.dumps(result, indent=2))
    print(result["reason"], file=sys.stderr)
    if args.github_output is not None:
        write_github_output(args.github_output, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
