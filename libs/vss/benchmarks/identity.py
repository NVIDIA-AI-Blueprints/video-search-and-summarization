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

"""Portable source identity; no deployment or cloud dependencies."""

import hashlib
import os
import stat

MAX_MEMBER = 32 * 1024**2
IDENTITY_VERSION = "namespace_workspace.v2"
SOURCE_ROOTS = (
    "src/vss",
    "packages/ingestion/src/vss",
    "packages/summarization/src/vss",
    "benchmarks",
    "packages/ingestion/native/cuda_ipc_broker",
)
PROJECT_INPUTS = (
    "pyproject.toml",
    "packages/ingestion/pyproject.toml",
    "packages/summarization/pyproject.toml",
    "uv.lock",
    "benchmarks/run_perf.sh",
    "benchmarks/matrix/recipe.toml",
)
GUARD_INPUTS = (
    "tests/test_frame_gate.py",
    "tests/test_workflow_qualification.py",
    "tests/test_workflow_modules.py",
    "tests/test_namespace_benchmarks.py",
    "tests/test_provider_probes.py",
    "tests/test_workflow_provider_budget.py",
    "tests/test_perf_matrix.py",
)


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def raw(path, maximum=MAX_MEMBER):
    before = os.lstat(path)
    require(stat.S_ISREG(before.st_mode), "regular_file_required")
    flags = os.O_RDONLY
    for option in ("O_NOFOLLOW", "O_NONBLOCK", "O_BINARY"):
        flags |= getattr(os, option, 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode), "regular_file_required")
        require(
            (before.st_dev, before.st_ino) == (info.st_dev, info.st_ino),
            "source_file_replaced",
        )
        require(0 <= info.st_size <= maximum, "file_size_limit")
        data = stream.read(maximum + 1)
        after = os.fstat(stream.fileno())
    current = os.lstat(path)
    require(stat.S_ISREG(current.st_mode), "regular_file_required")
    require(
        (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
        == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        == (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns),
        "source_file_changed",
    )
    require(len(data) <= maximum, "file_size_limit")
    return data


def source_names(root):
    """The same bounded source inventory is used by bundling and workers."""
    names = set(PROJECT_INPUTS) | set(GUARD_INPUTS)
    for directory in SOURCE_ROOTS:
        for path in (root / directory).rglob("*"):
            if path.is_file() and (
                path.suffix == ".py"
                or directory == "packages/ingestion/native/cuda_ipc_broker"
                and (path.suffix in {".h", ".cpp"} or path.name == "Makefile")
            ):
                names.add(path.relative_to(root).as_posix())
                require(len(names) <= 1000, "source_file_count")
    require(len(names) <= 1000, "source_file_count")
    return names


def software_sha(files, version=IDENTITY_VERSION):

    digest = hashlib.sha256()
    if version == IDENTITY_VERSION:
        digest.update((IDENTITY_VERSION + "\x00").encode())
        for name in sorted(files):
            if (
                name in PROJECT_INPUTS
                or name in GUARD_INPUTS
                or any(
                    name.startswith(directory + "/")
                    and (
                        name.endswith(".py")
                        or directory == "packages/ingestion/native/cuda_ipc_broker"
                        and (
                            name.endswith((".h", ".cpp")) or name.endswith("/Makefile")
                        )
                    )
                    for directory in SOURCE_ROOTS
                )
                or name.startswith("installed-metadata/")
            ):
                digest.update(name.encode() + b"\x00" + files[name] + b"\x00")
        return digest.hexdigest()
    require(version == "legacy_monolith.v1", "source_identity_version")
    for directory, prefix in (("src/vss/", "vss/"), ("benchmarks/", "benchmarks/")):
        for name in sorted(files):
            if name.startswith(directory) and name.endswith(".py"):
                digest.update((prefix + name.removeprefix(directory)).encode())
                digest.update(b"\x00" + files[name] + b"\x00")
    return digest.hexdigest()
