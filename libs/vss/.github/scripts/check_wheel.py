#!/usr/bin/env python3
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

"""Build-independent smoke test for an installed VSS wheel."""

from __future__ import annotations

import argparse
import os
import shutil
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
import vss.observability as observability

assert "site-packages" in vp.__file__
assert "site-packages" in observability.__file__
import vss.storage as storage_module
import vss
assert vss.__file__ is None
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

with observability.collect_vss_metrics() as metrics:
    observability.record("summarization.events", 1, library="summarization")
assert metrics.complete
assert len(metrics.observations) == 1
observation = metrics.observations[0]
restored = observability.Observation.model_validate_json(observation.model_dump_json())
assert restored == observation

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

SUMMARY_TEST = """
import asyncio
from vss.ingestion import ChunkInfo, VlmChunkResponse
from vss.summarization import (
    GeneratedSummary, OpenAISummaryGenerator, SummaryGenerator, summarize_video,
)

try:
    OpenAISummaryGenerator(model="fixture", base_url="http://unused.invalid/v1")
except RuntimeError as error:
    assert 'pip install "vss-summarization[openai]"' in str(error)
else:
    raise AssertionError("OpenAI must be absent before the extra is installed")

class Generator(SummaryGenerator):
    async def generate(self, events, **kwargs):
        return GeneratedSummary(text="wheel fixture", model="fixture")

async def check_summary():
    chunks = [VlmChunkResponse(
        chunk=ChunkInfo(sourceId="wheel", chunkIdx=0, start_pts=0, end_pts=1),
        vlm_response='{"events":[{"description":"fixture"}]}',
    )]
    result = await summarize_video(chunks, generator=Generator(), batch_size=1)
    assert "wheel fixture" in result.choices[0].message.content

asyncio.run(check_summary())
"""


RUNTIME_TEST = """
import sys
import asyncio
import json
from pathlib import Path

root = Path(sys.argv[1])
sys.path.insert(0, str(root / "harness"))
from benchmarks.processing import worker, preflight
from benchmarks.processing_results import ProcessingConfiguration
from benchmarks.media import generated
from benchmarks.e2e.__main__ import fixture_config
from benchmarks.e2e.config import Recipe
from benchmarks.e2e.worker import run_worker

trial = asyncio.run(worker(ProcessingConfiguration(warmups=0, repetitions=1)))
assert trial.status == "ok", trial.failure
assert trial.import_origin == "installed"
video = generated(root, "tiny", preflight()[0]["ffmpeg"])
recipe = Recipe.model_validate_json(json.dumps(fixture_config(video, 2)), strict=True)
results = [run_worker(recipe, arm, 1) for arm in recipe.arms]
assert all(result["status"] == "ok" for result in results), results
print("Installed CPU decode and memory/file workflow checks passed")
"""


def main() -> None:
    """Install the built wheel into an isolated environment and smoke-test it."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-profiles", action="store_true")
    args = parser.parse_args()
    if args.runtime_profiles and not all(
        shutil.which(tool) for tool in ("ffmpeg", "ffprobe")
    ):
        parser.error("--runtime-profiles requires caller-installed FFmpeg/FFprobe")
    wheel_paths = list(Path("dist").glob("*.whl"))
    wheels = {
        wheel.name.split("-")[0].replace("_", "-"): wheel.resolve()
        for wheel in wheel_paths
    }
    if len(wheel_paths) != 3 or set(wheels) != {
        "vss",
        "vss-ingestion",
        "vss-summarization",
    }:
        raise RuntimeError("expected the three workspace wheels in dist")

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        clean_environment = {
            key: value
            for key, value in os.environ.items()
            if key not in {"PYTHONPATH", "PYTHONHOME", "PYTHONOPTIMIZE"}
        }
        shutil.copytree(
            "benchmarks",
            root / "harness/benchmarks",
            ignore=shutil.ignore_patterns("__pycache__", "evidence"),
        )
        for name in (
            "pyproject.toml",
            "packages/ingestion/pyproject.toml",
            "packages/summarization/pyproject.toml",
            "uv.lock",
        ):
            target = root / "harness" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(name, target)
        for index, selected in enumerate(
            (
                ("vss",),
                ("vss", "vss-ingestion"),
                ("vss", "vss-ingestion", "vss-summarization"),
            )
        ):
            environment = root / f"wheel-test-{index}"
            subprocess.run(
                ["uv", "venv", "--python", sys.executable, str(environment)],
                check=True,
            )
            executable = environment / (
                "Scripts/python.exe" if os.name == "nt" else "bin/python"
            )

            def install(*requirements, executable=executable):
                subprocess.run(
                    [
                        "uv",
                        "pip",
                        "install",
                        "--python",
                        str(executable),
                        *requirements,
                    ],
                    check=True,
                    cwd=root,
                    env=clean_environment,
                )

            def check(code, executable=executable):
                subprocess.run(
                    [str(executable), "-I", "-c", code],
                    check=True,
                    cwd=root,
                    env=clean_environment,
                )

            install(*(str(wheels[name]) for name in selected))
            check(SMOKE_TEST)
            check(
                "import importlib.util; "
                "assert (importlib.util.find_spec('vss.ingestion') is not None)"
                f" == {index > 0}; "
                "assert (importlib.util.find_spec('vss.summarization') is not None)"
                f" == {index > 1}"
            )
            harness = str(root / "harness")
            check(
                "import sys; from pathlib import Path; "
                f"sys.path.insert(0, {harness!r}); "
                "from benchmarks.common import "
                "environment, import_origins, software_hash; "
                "import benchmarks.processing; "
                f"assert import_origins(Path({harness!r}))['mode'] == 'installed'; "
                f"assert len(software_hash(Path({harness!r}))) == 64; "
                "assert 'vss.ingestion' not in sys.modules; "
                "assert 'vss.summarization' not in sys.modules"
            )
            if index == 2:
                check(SUMMARY_TEST)
                check(
                    "import sys; "
                    f"sys.path.insert(0, {harness!r}); "
                    "sys.argv=['benchmarks.run','--warmups','0','--repetitions','1',"
                    f"'--output',{str(root / 'offline.json')!r}]; "
                    "from benchmarks.run import main; raise SystemExit(main())"
                )
                install(
                    "vss-summarization[openai] @ "
                    + wheels["vss-summarization"].as_uri()
                )
                check(
                    "import openai; "
                    "from vss.summarization import OpenAISummaryGenerator"
                )
                if args.runtime_profiles:
                    install(
                        "opencv-python-headless>=4.13,<4.14",
                        "psutil>=7.2,<8",
                        "requests>=2.34.2,<3",
                    )
                    check(
                        f"import sys; sys.argv=['runtime', {str(root)!r}]\n"
                        + RUNTIME_TEST
                    )
            elif index == 0:
                install(f"vss[telemetry] @ {wheels['vss'].as_uri()}")
                check(
                    "from vss.observability import OpenTelemetrySink; "
                    "OpenTelemetrySink()"
                )


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        sys.exit(error.returncode)
