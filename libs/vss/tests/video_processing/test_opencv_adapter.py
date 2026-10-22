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

"""Preserve available native codec tags without inventing missing metadata.

Added after the traffic demo established that CAP_PROP_FOURCC returns h264
while the public probe discards it. Only the native cv2 module is replaced;
public probing, the built-in backend and worker cleanup run unchanged.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


@pytest.fixture
def native_probe(monkeypatch: pytest.MonkeyPatch) -> Any:
    from vss.video_processing.decoders import registry
    from vss.video_processing.decoders.opencv import OpenCVBackend

    state = SimpleNamespace(fourcc=0.0, released=False)

    class Capture:
        def isOpened(self) -> bool:
            return True

        def get(self, key: int) -> float:
            return {1: 30.0, 2: 300.0, 3: 1920.0, 4: 1080.0, 5: state.fourcc}[key]

        def release(self) -> None:
            state.released = True

    cv2 = SimpleNamespace(
        __version__="4.13.0",
        VideoCapture=lambda path: Capture(),
        CAP_PROP_FPS=1,
        CAP_PROP_FRAME_COUNT=2,
        CAP_PROP_FRAME_WIDTH=3,
        CAP_PROP_FRAME_HEIGHT=4,
        CAP_PROP_FOURCC=5,
    )
    state.cv2 = cv2
    monkeypatch.setitem(sys.modules, "cv2", cv2)
    monkeypatch.setattr(registry, "_DECODER_REGISTRY", {"opencv": OpenCVBackend})
    return state


@pytest.mark.parametrize("tag", ["h264", "avc1", "MJPG", "AV01"])
async def test_probe_retains_backend_codec_tag(
    processing: Any, video_path: Path, native_probe: Any, tag: str
) -> None:
    native_probe.fourcc = float(int.from_bytes(tag.encode("ascii"), "little"))
    facts = await processing.probe_video(video_path, source_id="original")
    assert facts.codec == tag
    assert facts.source_id == "original"
    assert facts.audio is None
    assert facts.container is None
    assert native_probe.released


@pytest.mark.parametrize(
    "value",
    [0.0, -1.0, float("nan"), float("inf"), float(2**32), 42.5, 65.0, 16843009.0],
)
async def test_unavailable_or_invalid_fourcc_remains_unknown(
    processing: Any, video_path: Path, native_probe: Any, value: float
) -> None:
    native_probe.fourcc = value
    facts = await processing.probe_video(video_path)
    assert facts.codec is None
    assert facts.audio is None
    assert facts.container is None
    assert native_probe.released


async def test_missing_fourcc_property_does_not_break_probe(
    processing: Any, video_path: Path, native_probe: Any
) -> None:
    del native_probe.cv2.CAP_PROP_FOURCC
    facts = await processing.probe_video(video_path)
    assert facts.codec is None
    assert native_probe.released
