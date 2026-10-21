#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Verify a running evaluation embed image/settings, emitting a safe JSON receipt."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import UTC, datetime

VERSION = "index-linspace-round-aa-v1"
REVISION_LABEL = "org.opencontainers.image.revision"
TRUE = {"true", "1", "yes"}


def docker(*args):
    result = subprocess.run(
        ["docker", *args], capture_output=True, text=True, check=False, timeout=60
    )
    if result.returncode:
        raise ValueError(f"docker {args[0]} failed with exit {result.returncode}")
    return result


def inspect_one(kind, identity):
    values = json.loads(docker(kind, "inspect", identity).stdout)
    if (
        not isinstance(values, list)
        or len(values) != 1
        or not isinstance(values[0], dict)
    ):
        raise ValueError(f"Expected one inspected {kind}")
    return values[0]


def verify(container, revision):
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Expected the full lowercase Git commit SHA")
    if not container or container.startswith("-"):
        raise ValueError("Container must be an identifier read from Docker listings")
    instance = inspect_one("container", container)
    if not instance.get("State", {}).get("Running"):
        raise ValueError("Evaluation embed container is not running")
    started_at = instance["State"]["StartedAt"]
    if (
        not isinstance(started_at, str)
        or not started_at
        or started_at.startswith("0001-")
    ):
        raise ValueError("Container start time is unavailable")
    image_id = instance["Image"]
    image_tag = instance["Config"]["Image"]
    for identity in (image_id, image_tag):
        image = inspect_one("image", identity)
        if image.get("Id") != image_id:
            raise ValueError(
                "Configured image tag no longer matches the running image ID"
            )
        if image.get("Config", {}).get("Labels", {}).get(REVISION_LABEL) != revision:
            raise ValueError(
                "Running image revision label does not match requested commit"
            )
    environment = {}
    for value in instance["Config"].get("Env", []):
        key, separator, content = value.partition("=")
        if separator:
            if key in environment:
                raise ValueError(f"Duplicate effective environment key: {key}")
            environment[key] = content
    for key in ("COSMOS_FILE_INDEX_SAMPLING", "COSMOS_FILE_TORCHVISION_RESIZE"):
        if environment.get(key, "").lower() not in TRUE:
            raise ValueError(f"{key} must be enabled")
    settings = {}
    for key in ("COSMOS_FILE_MAX_RETAINED_FRAMES", "COSMOS_FILE_MAX_RETAINED_BYTES"):
        try:
            settings[key] = int(environment.get(key, ""))
        except ValueError as exc:
            raise ValueError(f"{key} must be an explicit positive integer") from exc
        if settings[key] <= 0:
            raise ValueError(f"{key} must be positive")
    for key in ("NUM_VLM_PROCS", "VLM_BATCH_SIZE"):
        if environment.get(key) != "1":
            raise ValueError(f"{key} must be 1 for the controlled campaign")
    logs = docker("logs", "--since", started_at, "--tail", "10000", instance["Id"])
    pattern = re.compile(
        r"Cosmos file preprocessing version=(\S+) index_sampling=(\S+) "
        r"antialias_resize=(\S+) cache_resize_before_retention=(\S+) "
        r"retained_frames=(\d+) retained_bytes=(\d+) per_decoder=true"
    )
    matches = pattern.findall(logs.stdout + "\n" + logs.stderr)
    if not matches:
        raise ValueError(
            "No effective preprocessing startup settings found for this container start"
        )
    expected = (
        VERSION,
        "true",
        "true",
        "true",
        str(settings["COSMOS_FILE_MAX_RETAINED_FRAMES"]),
        str(settings["COSMOS_FILE_MAX_RETAINED_BYTES"]),
    )
    if any(tuple(value.lower() for value in match) != expected for match in matches):
        raise ValueError(
            "Startup preprocessing settings disagree with requested effective configuration"
        )
    latest = inspect_one("container", instance["Id"])
    if (
        latest.get("State", {}).get("Running") is not True
        or latest.get("State", {}).get("StartedAt") != started_at
        or latest.get("Image") != image_id
    ):
        raise ValueError("Container changed or restarted during verification")
    return {
        "verified": True,
        "verified_at": datetime.now(UTC).isoformat(),
        "container_id": instance["Id"],
        "container_name": instance.get("Name"),
        "started_at": started_at,
        "image_id": image_id,
        "image_tag": image_tag,
        "revision": revision,
        "preprocessing_version": VERSION,
        "settings": {
            **settings,
            "COSMOS_FILE_INDEX_SAMPLING": True,
            "COSMOS_FILE_TORCHVISION_RESIZE": True,
            "NUM_VLM_PROCS": 1,
            "VLM_BATCH_SIZE": 1,
        },
        "model_path": environment.get("MODEL_PATH"),
        "model_implementation_path": environment.get("MODEL_IMPLEMENTATION_PATH"),
        "startup_settings_records": len(matches),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--container", required=True, help="Actual container handle from docker ps"
    )
    parser.add_argument(
        "--revision", required=True, help="Full commit SHA used to build the image"
    )
    args = parser.parse_args(argv)
    try:
        result = verify(args.container, args.revision)
    except (
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        OSError,
        subprocess.SubprocessError,
    ) as exc:
        print(json.dumps({"verified": False, "error": str(exc)}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
