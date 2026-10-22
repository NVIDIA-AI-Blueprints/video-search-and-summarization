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

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


def _load_example(name: str = "ingest_multiple_cv") -> ModuleType:
    path = Path(__file__).parents[1] / "examples" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"{name}_example", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_cuda_ipc_example_selects_three_rtvi_backends(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    example = _load_example()
    selected_backends: list[str] = []
    selected_cuda_ipc: bool | None = None

    async def fake_ingest(*_: Any, **kwargs: Any) -> dict[str, list[Any]]:
        nonlocal selected_cuda_ipc
        selected_backends.extend(kwargs["backends"])
        selected_cuda_ipc = kwargs["cuda_ipc"]
        return {name: [] for name in kwargs["backends"]}

    monkeypatch.setattr(example, "ingest", fake_ingest)
    monkeypatch.delenv("VLM_MODEL", raising=False)
    monkeypatch.delenv("RTVI_MODEL", raising=False)
    monkeypatch.setenv("VSS_SHARED_FRAME_TRANSPORT", "owned_cpu")

    await example.main(
        tmp_path / "video.mp4",
        chunk_duration=30,
        rtvi_base_url="http://rtvi.test",
        rtvi_model=None,
        embed_base_url="http://embed.test",
        cv_base_url="http://cv.test",
        cv_kafka_bootstrap_servers="kafka.test:9092",
        cv_kafka_topic="detections",
        socket_dir=tmp_path / "broker",
        device_id=0,
    )

    assert selected_backends == ["rtvi", "rtvi_embed", "rtvi_cv"]
    assert selected_cuda_ipc is True
    assert example.os.environ["VSS_SHARED_FRAME_TRANSPORT"] == "owned_cpu"
    assert "VLM_MODEL" not in example.os.environ


@pytest.mark.asyncio
async def test_owned_cpu_example_overrides_cuda_ipc_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    example = _load_example("ingest_multiple")
    selected_cuda_ipc: bool | None = None

    async def fake_ingest(*_: Any, **kwargs: Any) -> dict[str, list[Any]]:
        nonlocal selected_cuda_ipc
        selected_cuda_ipc = kwargs["cuda_ipc"]
        return {name: [] for name in kwargs["backends"]}

    monkeypatch.setattr(example, "ingest", fake_ingest)
    monkeypatch.setenv("VSS_SHARED_FRAME_TRANSPORT", "cuda_ipc")

    await example.main(
        tmp_path / "video.mp4",
        decoder="pynv",
        chunk_duration=30,
        caption_base_url="http://caption.test/v1",
        caption_model="caption-model",
        caption_api_key=None,
        rtvi_base_url="http://rtvi.test",
        rtvi_model="caption-model",
        embed_base_url="http://embed.test",
    )

    assert selected_cuda_ipc is False
