#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Fetch an exact Compose resource and inspect its images without persisting rendered secrets."""

import json
import os
import re
import stat
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

SERVICES = (
    "rtvi-cv",
    "sensor-ms",
    "streamprocessing-ms",
    "vst-ingress",
    "vss-video-analytics-api",
)
MAX_BYTES, MAX_FILES = 2 * 1024**3, 100_000


def check_tree(root):
    size, count = 0, 0
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("Resource links are unsupported")
        if path.is_file():
            size, count = size + path.stat().st_size, count + 1
            if size > MAX_BYTES or count > MAX_FILES:
                raise ValueError("Resource exceeds size/file limits")


def unpack(path, destination):
    destination.mkdir(parents=True)
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if (
                len(entries) > MAX_FILES
                or sum(e.file_size for e in entries) > MAX_BYTES
            ):
                raise ValueError("Archive exceeds size/file limits")
            for entry in entries:
                name, mode = PurePosixPath(entry.filename), entry.external_attr >> 16
                if (
                    name.is_absolute()
                    or ".." in name.parts
                    or "\\" in entry.filename
                    or ":" in entry.filename
                    or stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)
                ):
                    raise ValueError("Unsafe archive path or file type")
            archive.extractall(destination)
            for entry in entries:
                if not entry.is_dir():
                    (destination / entry.filename).chmod(
                        0o644 | ((entry.external_attr >> 16) & 0o111)
                    )
    else:
        with tarfile.open(path) as archive:
            entries = archive.getmembers()
            if (
                len(entries) > MAX_FILES
                or sum(e.size for e in entries) > MAX_BYTES
                or any(not (e.isfile() or e.isdir()) for e in entries)
            ):
                raise ValueError("Unsafe archive type or size/file limits")
            archive.extractall(destination, members=entries, filter="data")


def inspect_images(root, recipe):
    # shortcut: modern containers.env/services resources only; extend after inspecting a real new package.
    candidates = [
        p.parent
        for p in root.rglob("containers.env")
        if (p.parent / "services").is_dir()
    ]
    if len(candidates) != 1:
        raise ValueError(
            "Unsupported resource layout: expected one containers.env/services tree"
        )
    compose_root = candidates[0]
    profile = compose_root / "developer-profiles/dev-profile-base"
    files = [compose_root / "containers.env"] + [
        profile / name
        for name in (".env", "overrides.env", "generated.env")
        if (profile / name).is_file()
    ]
    # Remove caller image overrides; only the published resource selects pins.
    environment = {
        k: v
        for k, v in os.environ.items()
        if not (
            k.startswith("VSS_CONTAINER_")
            or (
                k.startswith("VSS_")
                and k.endswith(("_IMAGE", "_TAG", "_REGISTRY", "_TAG_SUFFIX"))
            )
        )
    }
    environment.update(
        VSS_APPS_DIR=str(compose_root),
        VSS_PERF_ROOT=str(Path(__file__).parents[1]),
        VSS_REPO_ROOT=str(Path(__file__).parents[2]),
    )
    command = (
        ["sudo", "-n", "docker"]
        if environment.get("DOCKER_USE_SUDO") == "true"
        else ["docker"]
    )
    command += ["compose", "--profile", "*"]
    for path in files:
        command += ["--env-file", str(path)]
    result = subprocess.run(
        command
        + ["-f", str(recipe), "config", "--format", "json", "--no-env-resolution"],
        env=environment,
        check=True,
        capture_output=True,
        timeout=120,
    )
    services, pins = json.loads(result.stdout)["services"], {}
    for name in SERVICES:
        pin = services.get(name, {}).get("image", "")
        if not re.fullmatch(
            r"[A-Za-z0-9_.:/-]+(?::[A-Za-z0-9_.-]+|@sha256:[0-9a-f]{64})", pin
        ):
            raise ValueError(
                f"Published {name} image is absent, unresolved or unversioned"
            )
        tag = pin.rsplit(":", 1)[-1].lower()
        if tag in {"latest", "main", "develop", "nightly", "stable"} or tag.endswith(
            "-latest"
        ):
            raise ValueError(f"Published {name} image has a moving tag")
        pins[name] = pin
    return compose_root, files, pins


def acquire(reference, destination, recipe):
    destination = destination.resolve()
    if not re.fullmatch(
        r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/vss-blueprints-compose-base:[A-Za-z0-9_.-]+",
        reference,
    ):
        raise ValueError("Expected org/team/vss-blueprints-compose-base:version")
    if destination.exists():
        raise ValueError("Choose a fresh promoted-resource destination")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        payload = Path(temporary) / "payload"
        downloaded = payload / "downloaded"
        downloaded.mkdir(parents=True)
        environment = os.environ.copy()
        environment["NGC_CLI_API_KEY"] = environment.get(
            "NGC_CLI_API_KEY"
        ) or environment.get("NGC_API_KEY", "")
        subprocess.run(
            [
                "ngc",
                "registry",
                "resource",
                "download-version",
                "--dest",
                str(downloaded),
                reference,
            ],
            env=environment,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=1800,
        )
        if not any(downloaded.rglob("*")):
            raise ValueError("NGC download produced no files")
        check_tree(downloaded)
        archives = [
            p
            for p in downloaded.rglob("*")
            if p.is_file() and (zipfile.is_zipfile(p) or tarfile.is_tarfile(p))
        ]
        for index, archive in enumerate(sorted(archives)):
            unpack(archive, payload / "extracted" / str(index))
        root, files, pins = inspect_images(payload, recipe)
        root_relative, relative_files = (
            root.relative_to(payload),
            [p.relative_to(payload) for p in files],
        )
        check_tree(root)
        payload.rename(destination)
    return {
        "apps": str(destination / root_relative),
        "env_files": [str(destination / p) for p in relative_files],
        "promoted_artifact": {
            "artifact_ref": reference,
            "resource_version": reference.rsplit(":", 1)[1],
            "verified": True,
            "container_pins": pins,
        },
    }
