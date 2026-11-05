# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Load service-owned image variables without copying or rewriting setup files."""

import argparse
import json
import os
from pathlib import Path
import re
import shlex

import yaml


NAME = re.compile(
    r"(?:PERF_[A-Z0-9_]+|VST_(?:STREAMPROCESSING|SENSOR|INGRESS|NVSTREAMER)|POSTGRES|REDIS)_IMAGE"
)
REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/:@+-]*")


def validate(name, reference):
    if (
        not isinstance(name, str)
        or not NAME.fullmatch(name)
        or name == "PERF_ALERT_IMAGE"
    ):
        raise ValueError(f"Invalid supporting image variable: {name!r}")
    if not isinstance(reference, str) or not REFERENCE.fullmatch(reference):
        raise ValueError(f"Invalid image reference for {name}: {reference!r}")


def load(path):
    config = yaml.safe_load(Path(path).read_text())
    if not isinstance(config, dict) or config.get("schema_version") != 1:
        raise ValueError("Expected image configuration schema_version: 1")
    if set(config) - {"schema_version", "images", "package_managed_images"}:
        raise ValueError(
            "Only schema_version, images, and package_managed_images are supported"
        )
    if not isinstance(config.get("images"), dict) or not config["images"]:
        raise ValueError(
            "Expected a nonempty images mapping of variable names to references"
        )
    for name, reference in config["images"].items():
        validate(name, reference)
    return config


def environment(config, current=None):
    """Retain explicit nonempty environment overrides, validating their values too."""
    current = os.environ if current is None else current
    result = {}
    for name, reference in config["images"].items():
        result[name] = current.get(name) or reference
        validate(name, result[name])
    return result


def save_snapshot(path, values):
    Path(path).write_text(
        json.dumps({"schema_version": 1, "images": values}, indent=2) + "\n"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--shell", action="store_true")
    parser.add_argument("--snapshot", type=Path)
    args = parser.parse_args()
    values = environment(load(args.config))
    if args.snapshot:
        save_snapshot(args.snapshot, values)
    if args.shell:
        for name, reference in values.items():
            print(f"export {name}={shlex.quote(reference)}")


if __name__ == "__main__":
    main()
