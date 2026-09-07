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

"""Regression tests for the vLLM Parakeet Transformers 5 build patch."""

from __future__ import annotations

import importlib.util
from pathlib import Path


PATCH_PATH = (
    Path(__file__).parents[2]
    / "docker/rtvi_vlm/patches/apply_vllm_parakeet_transformers5_patch.py"
)


def _load_patch_module():
    spec = importlib.util.spec_from_file_location("parakeet_patch", PATCH_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parakeet_patch_makes_subclass_fields_keyword_only(tmp_path, monkeypatch):
    target = tmp_path / "transformers_utils/configs/parakeet.py"
    target.parent.mkdir(parents=True)
    target.write_text(
        "from dataclasses import dataclass\n\n"
        "class ParakeetConfig:\n"
        "    llm_hidden_size: int\n"
        "    projection_hidden_size: int\n"
        "    projection_bias: bool\n"
        "    projection_eps: float = 1e-5\n"
        "    sampling_rate: int\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("VLLM_ROOT", str(tmp_path))
    patch = _load_patch_module()

    patch.apply_patch()
    patch.apply_patch()

    assert target.read_text(encoding="utf-8") == (
        "from dataclasses import dataclass, field\n\n"
        "class ParakeetConfig:\n"
        "    llm_hidden_size: int = field(kw_only=True)\n"
        "    projection_hidden_size: int = field(kw_only=True)\n"
        "    projection_bias: bool = field(kw_only=True)\n"
        "    projection_eps: float = 1e-5\n"
        "    sampling_rate: int = field(kw_only=True)\n"
    )
