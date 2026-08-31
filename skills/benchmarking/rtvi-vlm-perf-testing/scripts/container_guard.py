#!/usr/bin/env python3
######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
######################################################################################################
"""Find or remove containers proven to belong to one RTVI harness run."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Iterable
from typing import Any

RUN_ID_LABEL = "com.nvidia.rtvi.harness.run_id"
COMPOSE_PROJECT_LABEL = "com.docker.compose.project"


def project_for_run(run_id: str) -> str:
    prefix = re.sub(r"[^a-z0-9]", "", run_id.lower())[:30]
    if not prefix:
        raise ValueError("run ID must resolve to a non-empty Docker project name")
    digest = hashlib.sha256(run_id.encode()).hexdigest()[:32]
    project = f"{prefix}-{digest}"
    if len(project) > 63:
        raise ValueError(
            "run ID must resolve to a non-empty Docker project name of at most 63 characters"
        )
    return project


def owned_container_ids(
    records: Iterable[dict[str, Any]],
    run_id: str,
    project: str,
    expected_names: Iterable[str],
) -> list[str]:
    if project != project_for_run(run_id):
        raise ValueError("project must be derived exactly from run ID")
    expected = set(expected_names)
    owned = []
    for record in records:
        name = str(record.get("Name", "")).removeprefix("/")
        labels = (record.get("Config") or {}).get("Labels") or {}
        run_label = labels.get(RUN_ID_LABEL)
        project_label = labels.get(COMPOSE_PROJECT_LABEL)
        matches = run_label == run_id or project_label == project
        if name in expected and not matches:
            raise ValueError(f"refusing unlabeled container with expected name: {name}")
        if run_label not in (None, run_id) or project_label not in (None, project):
            raise ValueError(
                f"refusing mismatched ownership labels on container: {name}"
            )
        if matches:
            owned.append(str(record["Id"]))
    return sorted(set(owned))


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args],
        check=check,
        text=True,
        capture_output=True,
    )


def _find_records(
    run_id: str, project: str, expected_names: Iterable[str]
) -> list[dict[str, Any]]:
    ids = set()
    for label in (f"{RUN_ID_LABEL}={run_id}", f"{COMPOSE_PROJECT_LABEL}={project}"):
        result = _docker("ps", "-aq", "--filter", f"label={label}")
        ids.update(result.stdout.split())
    for name in expected_names:
        result = _docker("inspect", "--type", "container", name, check=False)
        if result.returncode == 0:
            ids.add(str(json.loads(result.stdout)[0]["Id"]))
    if not ids:
        return []
    return list(
        json.loads(_docker("inspect", "--type", "container", *sorted(ids)).stdout)
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--project")
    parser.add_argument("--name", action="append", default=[], dest="names")
    parser.add_argument(
        "--execute", action="store_true", help="force-remove verified owned containers"
    )
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    try:
        project = args.project or project_for_run(args.run_id)
        records = _find_records(args.run_id, project, args.names)
        ids = owned_container_ids(records, args.run_id, project, args.names)
        if args.execute and ids:
            _docker("rm", "-f", *ids)
        output = {
            "run_id": args.run_id,
            "project": project,
            "matched": ids,
            "removed": ids if args.execute else [],
            "mode": "execute" if args.execute else "dry-run",
        }
    except (
        KeyError,
        ValueError,
        json.JSONDecodeError,
        OSError,
        subprocess.CalledProcessError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    if args.as_json:
        print(json.dumps(output, indent=2, sort_keys=True))
    else:
        print(f"{output['mode']}: {len(ids)} owned container(s) matched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
