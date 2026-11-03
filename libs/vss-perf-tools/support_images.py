# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Render service-owned support images into private setup files, never source files.

Uses PyYAML already installed in the service's isolated perf environment.
No container operations or benchmark measurement logic belongs here.
"""

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import tempfile

import yaml


def load(path):
    config = yaml.safe_load(Path(path).read_text())
    if config.get("schema_version") != 1 or not config.get("images"):
        raise ValueError("Expected image configuration schema_version: 1 and images")
    for name, item in config["images"].items():
        ref = item["ref"]
        if not isinstance(ref, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._/:@+-]*", ref
        ):
            raise ValueError(f"Invalid image reference for {name}: {ref!r}")
    return config


def reference(config, name):
    return config["images"][name]["ref"]


def render(config, scope, source, destination):
    """Render only declared image fields. Missing bindings fail before deployment."""
    source, destination = Path(source), Path(destination)
    if (
        source.resolve() == destination.resolve()
        or source.resolve() in destination.resolve().parents
    ):
        raise ValueError("Image rendering requires a private destination")
    # Existing source symlinks remain symlinks; never traverse them while editing.
    shutil.copytree(
        source,
        destination,
        symlinks=True,
        ignore=shutil.ignore_patterns(".env", "generated.env", "__pycache__", ".venv"),
    )
    apply_bindings(config, scope, destination)
    return destination


def apply_bindings(config, scope, destination):
    destination = Path(destination)
    bindings = config["scopes"][scope]
    for relative, fields in bindings.items():
        path = destination / relative
        if Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise ValueError(f"Invalid binding path: {relative}")
        if path.is_symlink() or any(
            p.is_symlink() for p in path.parents if p != destination.parent
        ):
            raise ValueError(f"Refusing symlink binding: {relative}")
        text = path.read_text()
        for service, name in fields.get("services", {}).items():
            pattern = rf"(?ms)(^  {re.escape(service)}:\s*\n(?:(?!^  \S).)*?^    image:)\s*[^\n]+"
            text, count = re.subn(
                pattern, lambda m: m[1] + " " + reference(config, name), text
            )
            if count != 1:
                raise ValueError(
                    f"Expected one image for {relative}: {service}, found {count}"
                )
        stages = fields.get("build_bases", {})
        index = -1

        def replace_base(match):
            nonlocal index
            index += 1
            key = str(index)
            return (
                match[1] + reference(config, stages[key]) + match[3]
                if key in stages
                else match[0]
            )

        text = re.sub(r"(?m)^(FROM\s+)(\S+)([^\n]*)", replace_base, text)
        if any(int(key) > index for key in stages):
            raise ValueError(f"Missing Dockerfile stage in {relative}")
        path.write_text(text)


def prepare_infra(config_path, source, output):
    config = load(config_path)
    runtime = Path(tempfile.mkdtemp(prefix="image-setup.", dir=Path(output).resolve()))
    destination = render(config, "infra", source, runtime / "infra")
    compose = destination / "compose.yml"
    # Build from the private Dockerfiles so their configured FROM references apply.
    compose.write_text(
        compose.read_text().replace(
            "context: $VSS_APPS_DIR/services/infra", f"context: {destination}"
        )
    )
    (Path(output) / "support-images.json").write_text(json.dumps(config, indent=2))
    return destination


def environment(config):
    return {
        key: reference(config, name)
        for key, name in config.get("environment", {}).items()
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--scope")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--shell", action="store_true")
    args = parser.parse_args()
    config = load(args.config)
    if args.shell:
        for key, value in environment(config).items():
            if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
                raise ValueError(f"Invalid environment key: {key}")
            # Explicit existing overrides remain supported, as before this migration.
            print(f"export {key}={shlex.quote(os.environ.get(key) or value)}")
    else:
        if not all((args.scope, args.source, args.destination)):
            parser.error("Rendering requires --scope, --source, --destination")
        render(config, args.scope, args.source, args.destination)
        print(
            json.dumps({"image_config": str(args.config), "images": config["images"]})
        )


if __name__ == "__main__":
    main()
