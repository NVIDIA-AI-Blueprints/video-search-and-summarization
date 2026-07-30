#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Verify NemoClaw's direct Docker repair target through its lifecycle CLI.

NemoClaw v0.0.97 routes ordinary OpenShell RPCs through the owning gateway,
but its post-exec mutable-config repair discovers a container from host Docker
labels. On a warm multi-user or multi-gateway worker, a stale same-name
container can therefore be selected independently of the sandbox that handled
the command. This preflight mirrors that label lookup, attests the mutable image
tag against the container's immutable image ID, and uses NemoClaw's supported
start/exec lifecycle so OpenShell owns activation and post-exec cleanup.
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
DOCKER_COMMAND_TIMEOUT_SECONDS = 15
# NemoClaw v0.0.97's supported start probe owns a 300-second readiness
# budget. Leave process-launch/cleanup margin outside that internal deadline.
SANDBOX_START_TIMEOUT_SECONDS = 360
SANDBOX_EXEC_TIMEOUT_SECONDS = 90
LIFECYCLE_PROBE_SENTINEL = "NEMOCLAW_CI_LIFECYCLE_PREFLIGHT_OK_V1"
REPAIR_HELPER_PATH = "/usr/local/lib/nemoclaw/normalize_mutable_config_perms.py"
OPENCLAW_CONFIG_DIR = "/sandbox/.openclaw"
OPENCLAW_CONFIG_PATH = "/sandbox/.openclaw/openclaw.json"
SANDBOX_USER = "sandbox"
LIFECYCLE_PROBE_SOURCE = f"""\
set -eu
export LC_ALL=C
[ "$#" -eq 4 ] || exit 19
helper=$1
config_dir=$2
config_file=$3
sandbox_user=$4
[ -f "$helper" ] && [ ! -L "$helper" ] || exit 20
[ -d "$config_dir" ] && [ ! -L "$config_dir" ] || exit 21
[ -f "$config_file" ] && [ ! -L "$config_file" ] || exit 22
sandbox_uid=$(/usr/bin/id -u "$sandbox_user" 2>/dev/null) || exit 23
sandbox_gid=$(/usr/bin/id -g "$sandbox_user" 2>/dev/null) || exit 23
case "$sandbox_uid" in ""|*[!0-9]*) exit 23 ;; esac
case "$sandbox_gid" in ""|*[!0-9]*) exit 23 ;; esac
[ "$sandbox_uid" -gt 0 ] && [ "$sandbox_gid" -gt 0 ] || exit 23
printf '%s\\n' '{LIFECYCLE_PROBE_SENTINEL}'
"""
LIFECYCLE_PROBE_ARGV = (
    "/bin/sh",
    "-c",
    LIFECYCLE_PROBE_SOURCE,
    "nemoclaw-ci-preflight",
    REPAIR_HELPER_PATH,
    OPENCLAW_CONFIG_DIR,
    OPENCLAW_CONFIG_PATH,
    SANDBOX_USER,
)
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
    timeout: int = DOCKER_COMMAND_TIMEOUT_SECONDS,
) -> str:
    try:
        result = run(
            list(argv),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        raise DirectContainerPreflightError(failure_code) from None
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


def _inspect_and_attest_image(
    sandbox_name: str,
    container_id: str,
    *,
    run: RunCommand,
) -> tuple[str, str]:
    inspection = _run_checked(
        [
            "docker",
            "inspect",
            "--format",
            (
                "{{.Config.Image}}\t{{.Image}}\t"
                "{{.State.Running}}\t{{.State.Paused}}"
            ),
            container_id,
        ],
        run=run,
        failure_code="container_inspection_failed",
    ).strip()
    fields = inspection.split("\t")
    if len(fields) != 4:
        raise DirectContainerPreflightError("container_inspection_invalid")
    image_ref, image_id, running, paused = fields
    trusted_image_prefix = f"nemoclaw-sandbox-local:{sandbox_name}-"
    image_tag = image_ref.removeprefix(trusted_image_prefix)
    if (
        image_ref == image_tag
        or re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", image_tag) is None
    ):
        raise DirectContainerPreflightError("container_image_invalid")
    if (
        IMAGE_ID_RE.fullmatch(image_id) is None
        or running != "true"
        or paused not in {"true", "false"}
    ):
        raise DirectContainerPreflightError("container_identity_invalid")

    resolved_image_id = _run_checked(
        [
            "docker",
            "image",
            "inspect",
            "--format",
            "{{.Id}}",
            image_ref,
        ],
        run=run,
        failure_code="container_image_resolution_failed",
    ).strip()
    if IMAGE_ID_RE.fullmatch(resolved_image_id) is None:
        raise DirectContainerPreflightError("container_image_resolution_invalid")
    if resolved_image_id != image_id:
        raise DirectContainerPreflightError("container_image_id_mismatch")
    return image_ref, image_id


def verify_direct_container(
    sandbox_name: str,
    *,
    run: RunCommand = subprocess.run,
) -> None:
    """Verify and exercise the singleton selected by NemoClaw's repair path."""
    if SANDBOX_NAME_RE.fullmatch(sandbox_name) is None:
        raise DirectContainerPreflightError("sandbox_name_invalid")

    candidates = _list_candidates(sandbox_name, run=run)
    container_id, container_name = candidates[0]
    expected_name = f"openshell-{sandbox_name}"
    if container_name != expected_name and not container_name.startswith(
        f"{expected_name}-"
    ):
        raise DirectContainerPreflightError("container_name_invalid")

    initial_image = _inspect_and_attest_image(
        sandbox_name,
        container_id,
        run=run,
    )

    _run_checked(
        [
            "nemoclaw",
            "sandbox",
            "start",
            sandbox_name,
        ],
        run=run,
        failure_code="sandbox_start_failed",
        timeout=SANDBOX_START_TIMEOUT_SECONDS,
    )

    # `sandbox start` may activate a paused container, but must not redirect
    # the subsequent exec to a replacement that merely reused the labels.
    if _list_candidates(sandbox_name, run=run) != candidates:
        raise DirectContainerPreflightError("container_identity_changed")
    started_image = _inspect_and_attest_image(
        sandbox_name,
        container_id,
        run=run,
    )
    if started_image != initial_image:
        raise DirectContainerPreflightError("container_identity_changed")

    probe_output = _run_checked(
        [
            "nemoclaw",
            "sandbox",
            "exec",
            sandbox_name,
            "--no-tty",
            "--no-stdin",
            "--timeout",
            "30",
            "--",
            *LIFECYCLE_PROBE_ARGV,
        ],
        run=run,
        failure_code="sandbox_exec_failed",
        timeout=SANDBOX_EXEC_TIMEOUT_SECONDS,
    )
    if LIFECYCLE_PROBE_SENTINEL not in {
        line.strip() for line in probe_output.splitlines()
    }:
        raise DirectContainerPreflightError("sandbox_exec_sentinel_missing")

    # Close the lifecycle race: the label lookup and mutable tag must still
    # resolve to the exact container/image attested before supported exec.
    if _list_candidates(sandbox_name, run=run) != candidates:
        raise DirectContainerPreflightError("container_identity_changed")
    final_image = _inspect_and_attest_image(
        sandbox_name,
        container_id,
        run=run,
    )
    if final_image != initial_image:
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
