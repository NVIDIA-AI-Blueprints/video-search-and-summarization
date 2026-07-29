#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Verify NemoClaw's direct Docker repair target before CI mutates config.

NemoClaw v0.0.97 routes ordinary OpenShell RPCs through the owning gateway,
but its post-exec mutable-config repair discovers a container from host Docker
labels. On a warm multi-user or multi-gateway worker, a stale same-name
container can therefore be selected independently of the sandbox that handled
the command. This preflight mirrors that label lookup and fails closed unless
the singleton target is the freshly built NemoClaw image with the trusted
repair helper and OpenClaw config tree.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Callable, Sequence

SANDBOX_NAME_RE = re.compile(r"[a-z][a-z0-9-]{0,61}[a-z0-9]|[a-z]")
CONTAINER_ID_RE = re.compile(r"[0-9a-f]{64}")
IMAGE_ID_RE = re.compile(r"sha256:[0-9a-f]{64}")
RunCommand = Callable[..., subprocess.CompletedProcess[str]]


class DirectContainerPreflightError(RuntimeError):
    """A fixed, secret-safe direct-container preflight refusal."""

    def __init__(self, code: str):
        if re.fullmatch(r"[a-z0-9_]+", code) is None:
            raise ValueError("preflight errors require a fixed reason code")
        self.code = code
        super().__init__(code)


def _run_checked(
    argv: Sequence[str],
    *,
    run: RunCommand,
    failure_code: str,
) -> str:
    try:
        result = run(
            list(argv),
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DirectContainerPreflightError(failure_code) from exc
    if result.returncode != 0:
        raise DirectContainerPreflightError(failure_code)
    return result.stdout or ""


def _list_candidates(
    sandbox_name: str,
    *,
    run: RunCommand,
) -> list[tuple[str, str]]:
    output = _run_checked(
        [
            "docker",
            "ps",
            "--no-trunc",
            "--filter",
            "label=openshell.ai/managed-by=openshell",
            "--filter",
            f"label=openshell.ai/sandbox-name={sandbox_name}",
            "--format",
            "{{.ID}}\t{{.Names}}",
        ],
        run=run,
        failure_code="container_discovery_failed",
    )
    rows: list[tuple[str, str]] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) != 2 or CONTAINER_ID_RE.fullmatch(parts[0]) is None:
            raise DirectContainerPreflightError("container_metadata_invalid")
        rows.append((parts[0], parts[1]))
    if len(rows) != 1:
        raise DirectContainerPreflightError("container_count_invalid")
    return rows


def _require_path_type(
    container_id: str,
    path: str,
    expected_type: str,
    *,
    run: RunCommand,
    failure_code: str,
) -> None:
    actual = _run_checked(
        [
            "docker",
            "exec",
            "--env",
            "LC_ALL=C",
            "--user",
            "root",
            container_id,
            "/usr/bin/stat",
            "-c",
            "%F",
            "--",
            path,
        ],
        run=run,
        failure_code=failure_code,
    ).strip()
    if actual != expected_type:
        raise DirectContainerPreflightError(failure_code)


def verify_direct_container(
    sandbox_name: str,
    *,
    run: RunCommand = subprocess.run,
) -> None:
    """Verify the singleton container selected by NemoClaw's repair path."""
    if SANDBOX_NAME_RE.fullmatch(sandbox_name) is None:
        raise DirectContainerPreflightError("sandbox_name_invalid")

    candidates = _list_candidates(sandbox_name, run=run)
    container_id, container_name = candidates[0]
    expected_name = f"openshell-{sandbox_name}"
    if container_name != expected_name and not container_name.startswith(
        f"{expected_name}-"
    ):
        raise DirectContainerPreflightError("container_name_invalid")

    inspection = _run_checked(
        [
            "docker",
            "inspect",
            "--format",
            "{{.Config.Image}}\t{{.Image}}\t{{.State.Running}}",
            container_id,
        ],
        run=run,
        failure_code="container_inspection_failed",
    ).strip()
    fields = inspection.split("\t")
    if len(fields) != 3:
        raise DirectContainerPreflightError("container_inspection_invalid")
    image_ref, image_id, running = fields
    if not image_ref.startswith(f"nemoclaw-sandbox-local:{sandbox_name}-"):
        raise DirectContainerPreflightError("container_image_invalid")
    if IMAGE_ID_RE.fullmatch(image_id) is None or running != "true":
        raise DirectContainerPreflightError("container_identity_invalid")

    _require_path_type(
        container_id,
        "/usr/local/lib/nemoclaw/normalize_mutable_config_perms.py",
        "regular file",
        run=run,
        failure_code="repair_helper_invalid",
    )
    _require_path_type(
        container_id,
        "/sandbox/.openclaw",
        "directory",
        run=run,
        failure_code="openclaw_config_directory_invalid",
    )
    _require_path_type(
        container_id,
        "/sandbox/.openclaw/openclaw.json",
        "regular file",
        run=run,
        failure_code="openclaw_config_file_invalid",
    )
    for identity_flag in ("-u", "-g"):
        sandbox_identity = _run_checked(
            [
                "docker",
                "exec",
                "--user",
                "root",
                container_id,
                "/usr/bin/id",
                identity_flag,
                "sandbox",
            ],
            run=run,
            failure_code="sandbox_identity_invalid",
        ).strip()
        if re.fullmatch(r"[1-9][0-9]*", sandbox_identity) is None:
            raise DirectContainerPreflightError("sandbox_identity_invalid")

    # Close the inspection race: the label lookup must still resolve to the
    # exact container whose image and trusted paths were inspected.
    if _list_candidates(sandbox_name, run=run) != candidates:
        raise DirectContainerPreflightError("container_identity_changed")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sandbox-name", required=True)
    args = parser.parse_args(argv)
    try:
        verify_direct_container(args.sandbox_name)
    except DirectContainerPreflightError as exc:
        print(
            f"NemoClaw direct-container preflight refused: {exc.code}",
            file=sys.stderr,
        )
        return 1
    print("NemoClaw CI direct-container preflight verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
