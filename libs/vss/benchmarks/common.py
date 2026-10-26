# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Capability-independent environment, artifact and source identity helpers."""

from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

from .identity import (
    IDENTITY_VERSION,
    raw,
    software_sha,
    source_names,
)
from .results import Environment, Evidence

COMPONENTS = {
    "video_processing": ("vss", "src/vss/video_processing"),
    "storage": ("vss", "src/vss/storage"),
    "observability": ("vss", "src/vss/observability"),
    "ingestion": ("vss-ingestion", "packages/ingestion/src/vss/ingestion"),
    "summarization": (
        "vss-summarization",
        "packages/summarization/src/vss/summarization",
    ),
}


def import_origins(root: Path, *, required=()) -> dict[str, str]:
    """Admit one complete checkout or wheel installation, never mixed sources."""
    root = Path(root).resolve()
    namespace = importlib.util.find_spec("vss")
    if namespace is None or namespace.origin is not None:
        raise ValueError("namespace_package_required")
    origins = {}
    modes = set()
    portions = set()
    for name, (distribution, relative) in COMPONENTS.items():
        spec = importlib.util.find_spec("vss." + name)
        if spec is None or spec.origin is None:
            if name in {"video_processing", "storage", "observability", *required}:
                raise ValueError("required_component_missing")
            continue
        origin = Path(spec.origin).resolve(strict=True)
        if origin == root / relative / "__init__.py":
            mode = "checkout"
        else:
            metadata = importlib.metadata.distribution(distribution)
            installed = Path(metadata.locate_file("vss/" + name + "/__init__.py"))
            if "site-packages" not in origin.parts or origin != installed.resolve():
                raise ValueError("component_import_origin_mismatch")
            mode = "installed"
        modes.add(mode)
        portions.add(origin.parent.parent)
        origins["vss." + name] = str(origin)
    if len(modes) != 1 or portions != {
        Path(path).resolve() for path in namespace.submodule_search_locations or ()
    }:
        raise ValueError("mixed_namespace_origins")
    origins["mode"] = modes.pop()
    return origins


def import_mode(root: Path) -> str:
    return import_origins(root)["mode"]


def software_hash(root: Path) -> str:
    root = Path(root).resolve()
    origins = import_origins(root)
    if origins["mode"] == "checkout":
        files = {}
        for name in sorted(source_names(root)):
            path = root / name
            if path.resolve() != path.absolute():
                raise ValueError("source_symlink_ancestor")
            files[name] = raw(path)
        return software_sha(files)
    files = {
        name: raw(root / name) for name in source_names(root) if (root / name).is_file()
    }
    for name, (distribution, relative) in COMPONENTS.items():
        key = "vss." + name
        if key not in origins:
            continue
        directory = Path(origins[key]).parent
        for path in directory.rglob("*.py"):
            files[relative + "/" + path.relative_to(directory).as_posix()] = raw(path)
        metadata = importlib.metadata.distribution(distribution)
        files["installed-metadata/" + distribution] = (
            metadata.read_text("METADATA") or ""
        ).encode()
    return software_sha(files)


def environment() -> Environment:
    dependencies = {}
    for name in (
        "vss",
        "vss-ingestion",
        "vss-summarization",
        "numpy",
        "pydantic",
        "opentelemetry-api",
        "openai",
        "httpx",
    ):
        try:
            dependencies[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            dependencies[name] = "not-installed"
    return Environment(
        python=platform.python_version(),
        system=platform.system(),
        architecture=platform.machine(),
        os_release=platform.release(),
        cpu_model_sha256=cpu_model_hash(),
        cpu_count=os.cpu_count(),
        dependencies=dependencies,
        source_identity_version=IDENTITY_VERSION,
    )


def cpu_model_hash() -> str | None:
    model = ""
    if sys.platform == "darwin":
        try:
            model = subprocess.run(
                ["/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"],
                check=True,
                capture_output=True,
                text=True,
                timeout=2,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    elif sys.platform.startswith("linux"):
        try:
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                key, separator, value = line.partition(":")
                if separator and key.strip().lower() in {"model name", "hardware"}:
                    model = value.strip()
                    break
        except OSError:
            pass
    else:
        model = platform.processor()
    if not model or model.lower() in {"arm", "arm64", "x86_64", "amd64"}:
        return None
    return hashlib.sha256(model.encode()).hexdigest()


def save(path: Path, result: Evidence) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(result.model_dump_json(indent=2) + "\n")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
