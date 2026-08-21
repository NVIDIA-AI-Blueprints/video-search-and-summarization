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

"""The EVS Qwen3-VL overlay survives video metadata without ``frames_indices``.

``VideoMetadata`` declares the field optional and defaults it to ``None``, so a
model must not assume a caller filled it in. The overlay used to index it
straight into ``_calculate_timestamps``, which killed the request with
``AttributeError: 'NoneType' object has no attribute 'tolist'``. Recomputing the
indices only happens under ``do_sample_frames``, which the EVS path pins off.

This exercises the patched file under ``docker/rtvi_vlm/patches/`` directly --
it is normally only reachable inside the built image, where it overwrites
vLLM's own ``qwen3_vl.py``.
"""

import importlib.util
import os

import pytest

pytest.importorskip("vllm", reason="EVS overlay imports vLLM; container-only test")

_OVERLAY_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "..",
    "docker",
    "rtvi_vlm",
    "patches",
    "evs_vllm_files",
    "model_executor",
    "models",
    "qwen3_vl.py",
)


@pytest.fixture(scope="module")
def processing_info():
    """The overlay's Qwen3VLProcessingInfo, with a stand-in video processor.

    Loaded under a name inside the real ``vllm`` package so its relative
    imports (``from .interfaces import ...``) resolve.
    """
    import sys

    name = "vllm.model_executor.models.qwen3_vl_evs_overlay"
    spec = importlib.util.spec_from_file_location(name, os.path.abspath(_OVERLAY_PATH))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
        info = module.Qwen3VLProcessingInfo.__new__(module.Qwen3VLProcessingInfo)
        info.get_video_processor = lambda *a, **k: type(
            "VideoProcessor", (), {"merge_size": 2, "fps": 2, "min_frames": 1, "max_frames": 64}
        )()
        yield info
    finally:
        sys.modules.pop(name, None)


def test_missing_frame_indices_fall_back_to_frame_order(processing_info):
    """Absent indices mean "assume frame order", not "fail the clip"."""
    metadata = {"frames_indices": None, "fps": 1000.0, "total_num_frames": 4}

    timestamps = processing_info._get_video_second_idx(metadata, do_sample_frames=False)

    # indices [0, 1, 2, 3] at 1000 fps, averaged within each merge_size=2 pair.
    assert timestamps == pytest.approx([0.0005, 0.0025])


def test_supplied_frame_indices_are_still_honored(processing_info):
    """The fallback must not displace real indices when they are present."""
    metadata = {"frames_indices": [0, 2000, 4000, 6000], "fps": 1000.0, "total_num_frames": 4}

    timestamps = processing_info._get_video_second_idx(metadata, do_sample_frames=False)

    assert timestamps == pytest.approx([1.0, 5.0])
