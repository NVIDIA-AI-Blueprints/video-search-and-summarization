#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Build-independent smoke test for an installed VSS wheel."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

SMOKE_TEST = """
import asyncio
import importlib.util
import tempfile
from pathlib import Path

import vss.video_processing as vp
import vss.storage as storage_module
from vss.ingestion import ImmutableSourceIdentity, SourceIdentityProvider
from vss.summarization import InMemoryChunkStore as SummaryMemoryStore
from vss.storage import (
    ElasticsearchConfig,
    FileStorageAdapter,
    InMemoryStorageAdapter,
    StorageConfig,
    StorageConfigurationError,
    create_storage,
)

assert "site-packages" in vp.__file__
assert "site-packages" in storage_module.__file__
assert importlib.util.find_spec("openai") is None
assert importlib.util.find_spec("elasticsearch") is None
assert importlib.util.find_spec("cv2") is None
assert importlib.util.find_spec("PyNvVideoCodec") is None
assert ImmutableSourceIdentity.__module__.startswith("vss.ingestion.")
assert SourceIdentityProvider.__module__.startswith("vss.ingestion.")
assert SummaryMemoryStore.__module__.startswith("vss.summarization.")

plan = vp.DecodePlan(window=vp.TimeWindow(start_ns=0, end_ns=1_000_000_000))
assert vp.DecodePlan.model_validate_json(plan.model_dump_json()) == plan


async def check(path):
    for operation in (vp.probe_video(path), vp.extract_frames(path, [0])):
        try:
            await operation
        except vp.DecoderUnavailableError as error:
            assert "OpenCV" in str(error)
        else:
            raise AssertionError("Expected actionable missing-decoder error")


with tempfile.TemporaryDirectory() as directory:
    path = Path(directory) / "input.mp4"
    path.write_bytes(b"decoder-free smoke input")
    asyncio.run(check(path))

    async def storage_check():
        config = StorageConfig(backend="file", root=Path(directory) / "store")
        async with FileStorageAdapter(root=config.root) as adapter:
            written = await adapter.write(
                "captions", "job", [{"caption": "wheel smoke"}],
                expected_revision=None,
            )
        async with FileStorageAdapter(root=config.root) as adapter:
            assert await adapter.read("captions", "job") == written
        async with InMemoryStorageAdapter() as adapter:
            assert not adapter.persistent
            await adapter.write("captions", "job", [], expected_revision=None)

    asyncio.run(storage_check())

try:
    create_storage(StorageConfig(
        backend="elasticsearch",
        elasticsearch=ElasticsearchConfig(url="http://localhost:9200"),
    ))
except StorageConfigurationError as error:
    assert "vss[storage]" in str(error)
else:
    raise AssertionError("Expected actionable missing-Elasticsearch-extra error")
"""


def main() -> None:
    """Install the built wheel into an isolated environment and smoke-test it."""

    wheels = sorted(Path("dist").glob("*.whl"))
    if len(wheels) != 3:
        raise RuntimeError(f"expected three wheels in dist, found {len(wheels)}")

    with tempfile.TemporaryDirectory() as directory:
        environment = Path(directory) / "wheel-test"
        subprocess.run(
            ["uv", "venv", "--python", "3.13", str(environment)],
            check=True,
        )
        executable = environment / (
            "Scripts/python.exe" if os.name == "nt" else "bin/python"
        )
        subprocess.run(
            [
                "uv",
                "pip",
                "install",
                "--python",
                str(executable),
                *(str(wheel) for wheel in wheels),
            ],
            check=True,
        )
        subprocess.run([str(executable), "-I", "-c", SMOKE_TEST], check=True)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        sys.exit(error.returncode)
