#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Acquire and inspect an exact NGC Compose resource without executing its files.

The resource supplies production Compose definitions and container coordinates;
the source checkout supplies the performance harness. Unsupported packages or
moving image tags fail before deployment. Credentials only enter the NGC CLI
environment, and its output is never echoed because it may contain signed URLs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath

import yaml


class ArtifactError(RuntimeError):
    """The promoted resource could not be acquired or safely used."""


REQUIRED_IMAGES = (
    "VSS_RT_CV",
    "VSS_VIOS_SENSOR",
    "VSS_VIOS_STREAMPROCESSING",
    "VSS_VIOS_INGRESS",
    "VSS_VIDEO_ANALYTICS_API",
)
REQUIRED_DEFINITIONS = (
    "services/infra/compose.yml",
    "services/vios/compose.yml",
    "services/rtvi/rtvi-cv/compose.yaml",
    "services/analytics/video-analytics-api/compose.yml",
)
# These are the production definitions included by services/vios/compose.yml
# and extended by the benchmark recipe. Read the image field itself: CI can
# replace a parameterized image with a literal promoted pullspec.
IMAGE_DEFINITIONS = {
    "VSS_RT_CV": ("services/rtvi/rtvi-cv/compose.yaml", "perception"),
    "VSS_VIOS_SENSOR": ("services/vios/initiator/docker-compose.yaml", "sensor-ms"),
    "VSS_VIOS_STREAMPROCESSING": (
        "services/vios/streamprocessing/docker-compose.yaml",
        "streamprocessing-ms",
    ),
    "VSS_VIOS_INGRESS": (
        "services/vios/foundational/docker-compose.yaml",
        "vst-ingress",
    ),
    "VSS_VIDEO_ANALYTICS_API": (
        "services/analytics/video-analytics-api/compose.yml",
        "vss-video-analytics-api",
    ),
}
_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(:-|-)?")
_REFERENCE = re.compile(
    r"(?P<org>[A-Za-z0-9_.-]+)/(?P<team>[A-Za-z0-9_.-]+)/"
    r"(?P<name>[A-Za-z0-9_.-]+):(?P<version>[A-Za-z0-9_.-]+)"
)
MAX_FILES = 100_000
MAX_BYTES = 2 * 1024**3


def parse_reference(reference: str) -> dict[str, str]:
    match = _REFERENCE.fullmatch(reference)
    if match is None or any(v in {".", ".."} for v in match.groupdict().values()):
        raise ArtifactError("Expected an NGC resource reference org/team/name:version")
    if match["name"] != "vss-blueprints-compose-base":
        raise ArtifactError(
            "The benchmark requires the vss-blueprints-compose-base resource"
        )
    return match.groupdict()


def resolve_defaults(value: str, values: dict[str, str]) -> str:
    """Resolve nested dotenv defaults without shell evaluation or process env."""
    result = []
    cursor = 0
    while cursor < len(value):
        match = _VAR.match(value, cursor)
        if not match:
            result.append(value[cursor])
            cursor += 1
            continue
        name, operator = match.groups()
        end = match.end()
        if operator is None and (end >= len(value) or value[end] != "}"):
            raise ArtifactError("Unsupported expansion in published containers.env")
        depth = 1
        while end < len(value) and depth:
            if value.startswith("${", end):
                depth += 1
                end += 2
            elif value[end] == "}":
                depth -= 1
                end += 1
            else:
                end += 1
        if depth:
            raise ArtifactError("Unclosed variable in published containers.env")
        present = name in values and (operator != ":-" or values[name] != "")
        if present:
            result.append(values[name])
        elif operator:
            result.append(resolve_defaults(value[match.end() : end - 1], values))
        else:
            raise ArtifactError(f"Unresolved published container variable {name}")
        cursor = end
    return "".join(result)


def published_environment(
    path: Path,
    initial: dict[str, str] | None = None,
    *,
    coordinates_only: bool = False,
    declared: set[str] | None = None,
) -> dict[str, str]:
    values: dict[str, str] = dict(initial or {})
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            if coordinates_only:
                continue
            raise ArtifactError("Unsupported statement in published containers.env")
        name, value = line.split("=", 1)
        name, value = name.strip(), value.strip()
        if coordinates_only and not (
            name.startswith("VSS_CONTAINER_")
            or name
            in {
                prefix + suffix
                for prefix in REQUIRED_IMAGES
                for suffix in ("_IMAGE", "_TAG")
            }
        ):
            continue
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ArtifactError("Invalid variable name in published containers.env")
        if declared is not None:
            declared.add(name)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        # This restricted format covers VSS's nested default coordinate variables.
        # Shell commands and expansions never run, even for unused assignments.
        if any(token in value for token in ("$(", "`", "\n", "\r")):
            raise ArtifactError("Executable expansion in published containers.env")
        values[name] = resolve_defaults(value, values)
    return values


def inspect_payload(root: Path) -> dict:
    candidates = [
        p.parent
        for p in root.rglob("containers.env")
        if (p.parent / "services").is_dir()
    ]
    if len(candidates) != 1:
        raise ArtifactError(
            "Resource must contain exactly one production containers.env/services tree"
        )
    compose_root = candidates[0]
    for relative in REQUIRED_DEFINITIONS:
        if not (compose_root / relative).is_file():
            raise ArtifactError(f"Promoted Compose resource is missing {relative}")
    values = published_environment(compose_root / "containers.env")
    # Matches the developer-profile deployment's published image override
    # layers. Runtime host paths, API credentials and process env never become
    # coordinate inputs. generated.env is the last layer produced by the
    # production dev-profile script from .env and overrides.env.
    profile_root = compose_root / "developer-profiles/dev-profile-base"
    coordinate_sources = ["containers.env"]
    profile_paths = []
    declared = set()
    for filename in (".env", "overrides.env", "generated.env"):
        path = profile_root / filename
        if path.is_file():
            values = published_environment(
                path, values, coordinates_only=True, declared=declared
            )
            profile_paths.append(path)
            coordinate_sources.append(str(path.relative_to(compose_root)))
    # CI's apply_vss_container_images_to_env_file.sh records a selected shared
    # registry/tag pair, rather than duplicating every derived image variable.
    # Resolve the packaged default templates against that explicit published
    # channel, then replay per-image layers. The resource version is never an
    # input and no process environment is inherited.
    shared_channel = {
        key: values[key] for key in declared if key.startswith("VSS_CONTAINER_")
    }
    if shared_channel:
        values = published_environment(compose_root / "containers.env", shared_channel)
        for path in profile_paths:
            values = published_environment(path, values, coordinates_only=True)
    environment, pins = {}, {}
    for prefix in REQUIRED_IMAGES:
        relative, service = IMAGE_DEFINITIONS[prefix]
        path = compose_root / relative
        if not path.is_file():
            raise ArtifactError(f"Promoted Compose resource is missing {relative}")
        try:
            document = yaml.safe_load(path.read_text())
            raw_reference = document["services"][service]["image"]
        except (yaml.YAMLError, KeyError, TypeError) as exc:
            raise ArtifactError(
                f"Published {relative} lacks the {service} image definition"
            ) from exc
        if not isinstance(raw_reference, str):
            raise ArtifactError(f"Published {service} image must be a string")
        reference = resolve_defaults(raw_reference, values)
        if re.fullmatch(r"[A-Za-z0-9_.:/-]+@sha256:[0-9a-f]{64}", reference):
            if "$" in raw_reference:
                raise ArtifactError(
                    f"Published {prefix} digest must be literal in its Compose image field"
                )
            # CI retagging supports literal digest pullspecs. Their production
            # image field is already exact, so adding IMAGE/TAG overrides would
            # unnecessarily turn a digest into the wrong colon-tag syntax.
            pins[prefix] = reference
            continue
        if ":" not in reference.rsplit("/", 1)[-1]:
            raise ArtifactError(f"Published {prefix} image has no versioned tag")
        image, tag = reference.rsplit(":", 1)
        if not re.fullmatch(r"[A-Za-z0-9_.:/-]+", image) or not re.fullmatch(
            r"[A-Za-z0-9_.-]+", tag
        ):
            raise ArtifactError(
                f"Published {prefix} coordinates are absent or unresolved"
            )
        if tag.lower() in {
            "latest",
            "main",
            "develop",
            "nightly",
            "stable",
        } or tag.lower().endswith("-latest"):
            raise ArtifactError(
                f"Published {prefix} tag is moving; publish a versioned container pin"
            )
        environment[prefix + "_IMAGE"] = image
        environment[prefix + "_TAG"] = tag
        pins[prefix] = f"{image}:{tag}"
    checksums = {}
    total_bytes = 0
    for path in sorted(compose_root.rglob("*")):
        if path.is_symlink():
            raise ArtifactError("Links are not supported in promoted Compose resources")
        if path.is_file():
            total_bytes += path.stat().st_size
            if len(checksums) >= MAX_FILES or total_bytes > MAX_BYTES:
                raise ArtifactError("Promoted Compose tree exceeds verification limits")
            checksum = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    checksum.update(chunk)
            checksums[str(path.relative_to(compose_root))] = checksum.hexdigest()
    digest = hashlib.sha256(json.dumps(checksums, sort_keys=True).encode()).hexdigest()
    return {
        "compose_root": str(compose_root.resolve()),
        "environment": environment,
        "container_pins": pins,
        "compose_tree_sha256": digest,
        "files_sha256": checksums,
        "coordinate_sources": coordinate_sources,
    }


def member_path(root: Path, name: str) -> Path:
    relative = PurePosixPath(name)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or "\\" in name
        or (relative.parts and ":" in relative.parts[0])
    ):
        raise ArtifactError("Unsafe path in promoted resource archive")
    result = root.joinpath(*relative.parts)
    if not result.resolve().is_relative_to(root.resolve()):
        raise ArtifactError("Archive path escapes extraction directory")
    return result


def unpack_archive(archive: Path, destination: Path) -> None:
    """Extract only directories and regular files, with bounded uncompressed size."""
    destination.mkdir(parents=True, exist_ok=True)
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as bundle:
            members = bundle.infolist()
            if (
                len(members) > MAX_FILES
                or sum(m.file_size for m in members) > MAX_BYTES
            ):
                raise ArtifactError("Promoted archive exceeds extraction limits")
            for member in members:
                path = member_path(destination, member.filename)
                mode = member.external_attr >> 16
                if stat.S_ISLNK(mode) or (
                    stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR}
                ):
                    raise ArtifactError(
                        "Archive links and special files are not supported"
                    )
                if member.is_dir():
                    path.mkdir(parents=True, exist_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with bundle.open(member) as source, path.open("xb") as target:
                        shutil.copyfileobj(source, target)
                    path.chmod(0o644 | (mode & 0o111))
    elif tarfile.is_tarfile(archive):
        with tarfile.open(archive) as bundle:
            members = bundle.getmembers()
            if len(members) > MAX_FILES or sum(m.size for m in members) > MAX_BYTES:
                raise ArtifactError("Promoted archive exceeds extraction limits")
            for member in members:
                path = member_path(destination, member.name)
                if member.isdir():
                    path.mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    source = bundle.extractfile(member)
                    if source is None:
                        raise ArtifactError("Could not read promoted archive member")
                    with source, path.open("xb") as target:
                        shutil.copyfileobj(source, target)
                    path.chmod(0o644 | (member.mode & 0o111))
                else:
                    raise ArtifactError(
                        "Archive links and special files are not supported"
                    )
    else:
        raise ArtifactError("Unsupported resource archive format")


def download_resource(reference: str, destination: Path) -> None:
    parse_reference(reference)
    if shutil.which("ngc") is None:
        raise ArtifactError(
            "Install the official NGC CLI before acquiring a promoted resource"
        )
    environment = os.environ.copy()
    if not environment.get("NGC_CLI_API_KEY") and environment.get("NGC_API_KEY"):
        environment["NGC_CLI_API_KEY"] = environment["NGC_API_KEY"]
    try:
        result = subprocess.run(
            [
                "ngc",
                "registry",
                "resource",
                "download-version",
                "--dest",
                str(destination),
                reference,
            ],
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=1800,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ArtifactError("NGC resource download did not complete") from exc
    if result.returncode:
        raise ArtifactError(
            f"NGC resource download failed (exit {result.returncode}); verify access to the promoted version"
        )
    if not any(destination.rglob("*")):
        raise ArtifactError(
            "NGC download produced no files; the promoted resource was not verified"
        )


def acquire(
    reference: str,
    destination: Path,
    downloader: Callable[[str, Path], None] = download_resource,
) -> dict:
    spec = parse_reference(reference)
    destination = destination.resolve()
    if destination.exists():
        raise ArtifactError(
            "Promoted resource destination already exists; choose a fresh run directory"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".promoted-compose-", dir=destination.parent
    ) as temporary:
        staging = Path(temporary) / "payload"
        downloaded = staging / "downloaded"
        downloaded.mkdir(parents=True)
        downloader(reference, downloaded)
        for path in downloaded.rglob("*"):
            if path.is_symlink():
                raise ArtifactError("NGC resource contains an unsupported link")
        archives = [
            p
            for p in downloaded.rglob("*")
            if p.is_file() and (zipfile.is_zipfile(p) or tarfile.is_tarfile(p))
        ]
        for index, archive in enumerate(sorted(archives)):
            unpack_archive(archive, staging / "extracted" / str(index))
        report = inspect_payload(staging)
        compose_relative = Path(report["compose_root"]).relative_to(staging)
        staging.rename(destination)
        report.update(
            {
                "artifact_ref": reference,
                "resource_version": spec["version"],
                "verified": True,
                "acquired_from": "ngc_cli",
                "compose_root": str(destination / compose_relative),
            }
        )
        return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-ref", required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = acquire(args.artifact_ref, args.destination)
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(report, indent=2) + "\n")
    except (
        ArtifactError,
        OSError,
        ValueError,
        tarfile.TarError,
        zipfile.BadZipFile,
    ) as exc:
        print(f"Promoted artifact verification failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
