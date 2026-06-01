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

import asyncio
import sys
from importlib import metadata
from types import SimpleNamespace

import numpy as np
import pytest
import torch

import models.vllm_compatible.vllm_compatible_model as vllm_compatible_model
from common.service_exception import ServiceException
from models.vllm_compatible.vllm_compatible_model import VllmCompatible


class _FailingLLM:
    def __init__(self, message):
        self.message = message

    async def generate(self, *args, **kwargs):
        raise ValueError(self.message)
        yield


class _RecordingLLM:
    def __init__(self):
        self.llm_inputs = None

    async def generate(self, llm_inputs, *args, **kwargs):
        self.llm_inputs = llm_inputs
        yield SimpleNamespace()


def _make_model(message):
    model = VllmCompatible.__new__(VllmCompatible)
    model._llm = _FailingLLM(message)
    model._inflight_req_ids = ["req-1"]
    return model


class _DummyTensor:
    def cuda(self):
        return self

    def __eq__(self, other):
        return isinstance(other, _DummyTensor)


class _CompletedFuture:
    def __init__(self, value):
        self._value = value

    def result(self):
        return self._value


async def _run_process_async_vllm(model):
    await model.process_async_vllm(
        {"multi_modal_data": {}},
        SimpleNamespace(ignore_eos=False),
        [],
        "req-1",
    )


@pytest.mark.parametrize(
    "vllm_error",
    [
        "The decoder prompt (length 76445) is longer than the maximum model length of 32768",
        "At most 32 images may be provided in one prompt",
    ],
)
def test_input_limit_value_errors_return_service_exception(monkeypatch, vllm_error):
    monkeypatch.setattr(vllm_compatible_model, "CPU_COPY_OTHER_THREAD", False)
    model = _make_model(vllm_error)

    with pytest.raises(ServiceException) as exc_info:
        asyncio.run(_run_process_async_vllm(model))

    assert exc_info.value.status_code == 400
    assert exc_info.value.code == "InvalidParameter"
    assert "Input exceeds model limits" in exc_info.value.message
    assert "Reduce frames per chunk or raise VLM_MAX_MODEL_LEN" in exc_info.value.message
    assert model._inflight_req_ids == []


def test_unrelated_value_error_still_propagates(monkeypatch):
    monkeypatch.setattr(vllm_compatible_model, "CPU_COPY_OTHER_THREAD", False)
    model = _make_model("scheduler failed before token generation")

    with pytest.raises(ValueError, match="scheduler failed before token generation"):
        asyncio.run(_run_process_async_vllm(model))

    assert model._inflight_req_ids == []


def test_qwen3vl_nvfp4_uses_side_installed_vllm_017():
    model_config = {"quantization_config": {"format": "nvfp4-pack-quantized"}}

    assert vllm_compatible_model._requires_vllm017("Qwen3VLForConditionalGeneration", model_config)


def test_qwen3vl_fp8_uses_default_side_installed_vllm():
    model_config = {"quantization_config": {"format": "float-quantized"}}

    assert not vllm_compatible_model._requires_vllm017(
        "Qwen3VLForConditionalGeneration", model_config
    )


def test_optional_int_env_returns_none_when_unset(monkeypatch):
    monkeypatch.delenv("VLLM_KV_CACHE_MEMORY_BYTES", raising=False)

    assert vllm_compatible_model._parse_optional_int_env("VLLM_KV_CACHE_MEMORY_BYTES") is None


def test_optional_int_env_parses_zero_and_positive_values(monkeypatch):
    monkeypatch.setenv("VLLM_KV_CACHE_MEMORY_BYTES", "0")
    assert vllm_compatible_model._parse_optional_int_env("VLLM_KV_CACHE_MEMORY_BYTES") == 0

    monkeypatch.setenv("VLLM_KV_CACHE_MEMORY_BYTES", "8589934592")
    assert vllm_compatible_model._parse_optional_int_env("VLLM_KV_CACHE_MEMORY_BYTES") == 8589934592


def test_optional_int_env_rejects_invalid_values(monkeypatch):
    monkeypatch.setenv("VLLM_KV_CACHE_MEMORY_BYTES", "not-an-int")

    with pytest.raises(ValueError, match="VLLM_KV_CACHE_MEMORY_BYTES"):
        vllm_compatible_model._parse_optional_int_env("VLLM_KV_CACHE_MEMORY_BYTES")


def test_optional_int_env_rejects_negative_values(monkeypatch):
    monkeypatch.setenv("VLLM_KV_CACHE_MEMORY_BYTES", "-1")

    with pytest.raises(ValueError, match="greater than or equal to 0"):
        vllm_compatible_model._parse_optional_int_env("VLLM_KV_CACHE_MEMORY_BYTES")


def test_video_tensor_is_converted_to_numpy_before_vllm_processor():
    model = VllmCompatible.__new__(VllmCompatible)
    model._llm = _RecordingLLM()
    model._inflight_req_ids = ["req-1"]
    model._postprocess_vllm = lambda *args, **kwargs: model._llm.llm_inputs

    video_tensor = torch.ones((2, 4, 4, 3), dtype=torch.uint8)
    video_metadata = {"fps": 1.0}
    llm_inputs = {"multi_modal_data": {"video": [(video_tensor, video_metadata)]}}

    result = asyncio.run(
        model.process_async_vllm(
            llm_inputs,
            SimpleNamespace(ignore_eos=False),
            [],
            "req-1",
        )
    )

    converted_video, converted_metadata = result["multi_modal_data"]["video"][0]
    assert isinstance(converted_video, np.ndarray)
    assert converted_video.shape == (2, 4, 4, 3)
    assert converted_metadata is video_metadata
    assert model._inflight_req_ids == []


def test_warmup_runs_video_and_text_only_paths(monkeypatch):
    model = VllmCompatible.__new__(VllmCompatible)
    calls = []

    monkeypatch.setattr(
        vllm_compatible_model.torch,
        "ones",
        lambda *args, **kwargs: _DummyTensor(),
    )
    monkeypatch.setattr(
        vllm_compatible_model.torch,
        "stack",
        lambda tensors: ("stacked_dummy_frames", list(tensors)),
    )

    def generate(query, chunks, video_frames, video_frames_times, generation_config):
        calls.append(
            (
                "video",
                query,
                len(chunks),
                video_frames,
                video_frames_times,
                generation_config.max_new_tokens,
            )
        )
        return _CompletedFuture(["video"])

    def generate_text_only(messages, generation_config):
        calls.append(("text", messages, generation_config.max_new_tokens))
        return _CompletedFuture(["text"])

    model.generate = generate
    model.generate_text_only = generate_text_only

    assert model.warmup() == ["text"]
    assert calls == [
        (
            "video",
            "Describe this video briefly.",
            1,
            [("stacked_dummy_frames", [_DummyTensor()] * 8)],
            [list(range(8))],
            50,
        ),
        ("text", [{"role": "user", "content": "Reply with: ok"}], 8),
    ]


def test_mm_preprocessor_cache_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("VLLM_DISABLE_MM_PREPROCESSOR_CACHE", raising=False)

    assert (
        vllm_compatible_model._parse_bool_env(
            "VLLM_DISABLE_MM_PREPROCESSOR_CACHE",
            default=True,
        )
        is True
    )


def test_mm_preprocessor_cache_can_be_enabled_explicitly(monkeypatch):
    monkeypatch.setenv("VLLM_DISABLE_MM_PREPROCESSOR_CACHE", "false")

    assert (
        vllm_compatible_model._parse_bool_env(
            "VLLM_DISABLE_MM_PREPROCESSOR_CACHE",
            default=True,
        )
        is False
    )


def test_mm_processor_cache_size_defaults_to_one_gb(monkeypatch):
    monkeypatch.delenv("VLLM_MM_PROCESSOR_CACHE_GB", raising=False)
    monkeypatch.delenv("VLLM_MM_INPUT_CACHE_GIB", raising=False)

    assert vllm_compatible_model._get_mm_processor_cache_gb() == 1.0


def test_mm_processor_cache_size_uses_async_engine_arg_env(monkeypatch):
    monkeypatch.setenv("VLLM_MM_PROCESSOR_CACHE_GB", "0.5")
    monkeypatch.setenv("VLLM_MM_INPUT_CACHE_GIB", "2")

    assert vllm_compatible_model._get_mm_processor_cache_gb() == 0.5


def test_mm_processor_cache_size_falls_back_to_vllm_env(monkeypatch):
    monkeypatch.delenv("VLLM_MM_PROCESSOR_CACHE_GB", raising=False)
    monkeypatch.setenv("VLLM_MM_INPUT_CACHE_GIB", "2")

    assert vllm_compatible_model._get_mm_processor_cache_gb() == 2.0


def test_mm_processor_cache_size_rejects_invalid_values(monkeypatch):
    monkeypatch.setenv("VLLM_MM_PROCESSOR_CACHE_GB", "not-a-number")

    with pytest.raises(ValueError, match="VLLM_MM_PROCESSOR_CACHE_GB"):
        vllm_compatible_model._get_mm_processor_cache_gb()


def test_mm_processor_cache_size_rejects_negative_values(monkeypatch):
    monkeypatch.setenv("VLLM_MM_PROCESSOR_CACHE_GB", "-1")

    with pytest.raises(ValueError, match="greater than or equal to 0"):
        vllm_compatible_model._get_mm_processor_cache_gb()


def test_cosmos3_diffusers_shim_registers_plugin_and_disables_deep_gemm(monkeypatch):
    calls = []

    monkeypatch.delenv("VLLM_USE_DEEP_GEMM", raising=False)
    monkeypatch.setitem(
        sys.modules,
        "vllm_cosmos3",
        SimpleNamespace(register=lambda: calls.append("registered")),
    )

    registered = vllm_compatible_model._maybe_register_cosmos3_vllm_shim(
        "Cosmos3ForConditionalGeneration"
    )

    assert registered is True
    assert calls == ["registered"]
    assert vllm_compatible_model.os.environ["VLLM_USE_DEEP_GEMM"] == "0"


def test_cosmos3_diffusers_shim_preserves_explicit_deep_gemm(monkeypatch):
    calls = []

    monkeypatch.setenv("VLLM_USE_DEEP_GEMM", "1")
    monkeypatch.setitem(
        sys.modules,
        "vllm_cosmos3",
        SimpleNamespace(register=lambda: calls.append("registered")),
    )

    vllm_compatible_model._maybe_register_cosmos3_vllm_shim("Cosmos3ForConditionalGeneration")

    assert calls == ["registered"]
    assert vllm_compatible_model.os.environ["VLLM_USE_DEEP_GEMM"] == "1"


def test_cosmos3_diffusers_shim_skips_other_architectures(monkeypatch):
    calls = []

    monkeypatch.delenv("VLLM_USE_DEEP_GEMM", raising=False)
    monkeypatch.setitem(
        sys.modules,
        "vllm_cosmos3",
        SimpleNamespace(register=lambda: calls.append("registered")),
    )

    registered = vllm_compatible_model._maybe_register_cosmos3_vllm_shim(
        "Qwen3_5ForConditionalGeneration"
    )

    assert registered is False
    assert calls == []
    assert "VLLM_USE_DEEP_GEMM" not in vllm_compatible_model.os.environ


def test_cosmos3_diffusers_forces_trust_remote_code(monkeypatch):
    monkeypatch.setenv("VLM_TRUST_REMOTE_CODE", "false")

    assert (
        vllm_compatible_model._get_vlm_trust_remote_code("Cosmos3ForConditionalGeneration") is True
    )


def test_non_cosmos3_respects_trust_remote_code_env(monkeypatch):
    monkeypatch.setenv("VLM_TRUST_REMOTE_CODE", "true")

    assert (
        vllm_compatible_model._get_vlm_trust_remote_code("Qwen3_5ForConditionalGeneration") is True
    )


def test_cosmos3_vllm_plugin_entry_point_is_discoverable():
    entry_points = metadata.entry_points()
    if hasattr(entry_points, "select"):
        general_plugins = entry_points.select(group="vllm.general_plugins")
    else:
        general_plugins = entry_points.get("vllm.general_plugins", [])

    assert any(
        ep.name == "register_cosmos3" and ep.value == "vllm_cosmos3:register"
        for ep in general_plugins
    )
