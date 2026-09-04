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
"""rt-embed carries its own copy of the vLLM-compatible model module.

The copies must agree on how the multimodal processor cache is disabled, so
the size-based disable is asserted here too rather than only under rt-vlm.
rt-embed has no mm_processor_cache_type handling, which is the one intended
difference between the two helpers.

Every test here runs without vLLM, so CI selects this file by path. rt-embed
declares no pytest markers, unlike rt-vlm's mixed test module.
"""

import models.vllm_compatible.vllm_compatible_model as vllm_compatible_model


def _clear_vllm_env(monkeypatch):
    for source, target in vllm_compatible_model._RTVI_VLLM_ENV_ALIASES.items():
        monkeypatch.delenv(source, raising=False)
        monkeypatch.delenv(target, raising=False)
    monkeypatch.delenv("VLLM_MM_INPUT_CACHE_GIB", raising=False)


def test_mm_processor_cache_disable_uses_zero_gb_when_legacy_flag_missing(monkeypatch):
    _clear_vllm_env(monkeypatch)
    engine_args = {}

    vllm_compatible_model._apply_mm_processor_cache_engine_args(
        engine_args,
        {"mm_processor_cache_gb"},
    )

    assert engine_args == {"mm_processor_cache_gb": 0.0}


def test_mm_processor_cache_disable_zeros_gb_even_when_legacy_flag_exists(monkeypatch):
    _clear_vllm_env(monkeypatch)
    monkeypatch.setenv("VLLM_MM_PROCESSOR_CACHE_GB", "1")
    engine_args = {}

    vllm_compatible_model._apply_mm_processor_cache_engine_args(
        engine_args,
        {"disable_mm_preprocessor_cache", "mm_processor_cache_gb"},
    )

    assert engine_args == {
        "disable_mm_preprocessor_cache": True,
        "mm_processor_cache_gb": 0.0,
    }


def test_mm_processor_cache_opt_in_uses_configured_size(monkeypatch):
    _clear_vllm_env(monkeypatch)
    monkeypatch.setenv("VLLM_DISABLE_MM_PREPROCESSOR_CACHE", "false")
    monkeypatch.setenv("VLLM_MM_PROCESSOR_CACHE_GB", "0.5")
    engine_args = {}

    vllm_compatible_model._apply_mm_processor_cache_engine_args(
        engine_args,
        {"mm_processor_cache_gb"},
    )

    assert engine_args == {"mm_processor_cache_gb": 0.5}


def test_mm_processor_cache_type_is_never_applied(monkeypatch):
    """rt-embed does not own the cache-type override; the copies differ here."""
    _clear_vllm_env(monkeypatch)
    engine_args = {}

    vllm_compatible_model._apply_mm_processor_cache_engine_args(
        engine_args,
        {"mm_processor_cache_gb", "mm_processor_cache_type"},
    )

    assert engine_args == {"mm_processor_cache_gb": 0.0}
