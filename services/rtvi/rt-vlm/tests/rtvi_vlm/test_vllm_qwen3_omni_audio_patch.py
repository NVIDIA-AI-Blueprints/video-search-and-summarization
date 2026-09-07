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

"""Regression coverage for the Qwen3-Omni vLLM compatibility patch."""

from __future__ import annotations

import importlib.util
from pathlib import Path


PATCH_PATH = (
    Path(__file__).parents[2]
    / "docker/rtvi_vlm/patches/apply_vllm_qwen3_omni_audio_patch.py"
)


def _load_patch_module():
    spec = importlib.util.spec_from_file_location("qwen3_omni_audio_patch", PATCH_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_qwen3_omni_patch_makes_audio_flag_optional(tmp_path, monkeypatch):
    target = tmp_path / "model_executor/models/qwen3_omni_moe_thinker.py"
    target.parent.mkdir(parents=True)
    target.write_text('if item and item["use_audio_in_video"].data:\n', encoding="utf-8")
    monkeypatch.setenv("VLLM_ROOT", str(tmp_path))
    patch = _load_patch_module()

    patch.apply_patch()
    patch.apply_patch()

    assert target.read_text(encoding="utf-8") == (
        'if item and item.get("use_audio_in_video") and item["use_audio_in_video"].data:\n'
    )
