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
from models.base_vlm_model import VlmGenerationConfig
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


def test_qwen3vl_chat_template_disables_thinking_by_default():
    model = VllmCompatible.__new__(VllmCompatible)
    model._model_architecture = "Qwen3VLForConditionalGeneration"

    assert model._get_apply_chat_template_kwargs(VlmGenerationConfig()) == {
        "enable_thinking": False
    }
    assert model._get_apply_chat_template_kwargs(VlmGenerationConfig(enable_reasoning=True)) == {
        "enable_thinking": True
    }


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


def test_rtvi_vllm_env_sanitizer_moves_compatibility_aliases(monkeypatch):
    for source, target in vllm_compatible_model._RTVI_VLLM_ENV_ALIASES.items():
        monkeypatch.delenv(source, raising=False)
        monkeypatch.delenv(target, raising=False)

    monkeypatch.setenv("VLLM_GPU_MEMORY_UTILIZATION", "0.6")
    monkeypatch.setenv("VLLM_MAX_NUM_BATCHED_TOKENS", "8192")
    monkeypatch.setenv("VLLM_NUM_PREPROCESS_WORKERS", "16")
    monkeypatch.setenv("VLLM_ROOT", "/custom/vllm")
    monkeypatch.setenv("VLLM_USE_AOT_COMPILE", "0")

    vllm_compatible_model._sanitize_rtvi_vllm_env()

    assert "VLLM_GPU_MEMORY_UTILIZATION" not in vllm_compatible_model.os.environ
    assert "VLLM_MAX_NUM_BATCHED_TOKENS" not in vllm_compatible_model.os.environ
    assert "VLLM_NUM_PREPROCESS_WORKERS" not in vllm_compatible_model.os.environ
    assert "VLLM_ROOT" not in vllm_compatible_model.os.environ
    assert vllm_compatible_model.os.environ["RTVI_VLLM_GPU_MEMORY_UTILIZATION"] == "0.6"
    assert vllm_compatible_model.os.environ["RTVI_VLLM_MAX_NUM_BATCHED_TOKENS"] == "8192"
    assert vllm_compatible_model.os.environ["RTVI_VLLM_NUM_PREPROCESS_WORKERS"] == "16"
    assert vllm_compatible_model.os.environ["RTVI_VLLM_ROOT"] == "/custom/vllm"
    assert vllm_compatible_model.os.environ["VLLM_USE_AOT_COMPILE"] == "0"


def test_rtvi_vllm_env_sanitizer_preserves_internal_override(monkeypatch):
    for source, target in vllm_compatible_model._RTVI_VLLM_ENV_ALIASES.items():
        monkeypatch.delenv(source, raising=False)
        monkeypatch.delenv(target, raising=False)

    monkeypatch.setenv("VLLM_ENFORCE_EAGER", "true")
    monkeypatch.setenv("RTVI_VLLM_ENFORCE_EAGER", "false")

    vllm_compatible_model._sanitize_rtvi_vllm_env()

    assert "VLLM_ENFORCE_EAGER" not in vllm_compatible_model.os.environ
    assert vllm_compatible_model._parse_bool_env("VLLM_ENFORCE_EAGER", default=True) is False


def test_evs_similarity_threshold_is_moved_off_the_vllm_namespace(monkeypatch):
    """vLLM warns on any VLLM_-prefixed name it does not define.

    ``envs.validate_environ()`` walks os.environ at engine-config time and logs
    "Unknown vLLM environment variable detected: VLLM_EVS_SIMILARITY_THRESHOLD"
    for this RTVI-owned name. The alias map is how every other RTVI VLLM_ var
    already avoids that, so this one belongs there too rather than being
    registered into vLLM's own table.
    """
    for source, target in vllm_compatible_model._RTVI_VLLM_ENV_ALIASES.items():
        monkeypatch.delenv(source, raising=False)
        monkeypatch.delenv(target, raising=False)

    monkeypatch.setenv("VLLM_EVS_SIMILARITY_THRESHOLD", "0.35")

    vllm_compatible_model._sanitize_rtvi_vllm_env()

    assert "VLLM_EVS_SIMILARITY_THRESHOLD" not in vllm_compatible_model.os.environ
    assert vllm_compatible_model.os.environ["RTVI_VLLM_EVS_SIMILARITY_THRESHOLD"] == "0.35"


def test_evs_similarity_threshold_survives_the_rename(monkeypatch):
    """Both consumers must read through the alias, not raw os.environ.

    The rename happens in _load_model before the engine is built; a consumer
    still reading the original name would silently fall back to the default.
    """
    for source, target in vllm_compatible_model._RTVI_VLLM_ENV_ALIASES.items():
        monkeypatch.delenv(source, raising=False)
        monkeypatch.delenv(target, raising=False)

    monkeypatch.setenv("VLLM_EVS_SIMILARITY_THRESHOLD", "0.35")
    vllm_compatible_model._sanitize_rtvi_vllm_env()

    assert vllm_compatible_model._get_evs_similarity_threshold() == 0.35


@pytest.mark.parametrize("value", [None, "", "   "])
def test_evs_similarity_threshold_defaults_to_0_4(monkeypatch, value):
    """Unset or blank falls back to 0.4 -- the tuned EVS++ operating point.

    Was 0.2. 0.4 is what the perf guide and the perf-testing skill already
    prescribe as the standard EVS++ configuration, so the shipped default now
    matches the configuration people actually run.
    """
    for source, target in vllm_compatible_model._RTVI_VLLM_ENV_ALIASES.items():
        monkeypatch.delenv(source, raising=False)
        monkeypatch.delenv(target, raising=False)
    if value is not None:
        monkeypatch.setenv("VLLM_EVS_SIMILARITY_THRESHOLD", value)

    assert vllm_compatible_model._get_evs_similarity_threshold() == 0.4


@pytest.mark.parametrize("value", [None, "", "   "])
def test_evs_token_budget_defaults_to_1(monkeypatch, value):
    """Unset or blank falls back to 1 -- generate per clip, do not accumulate.

    Was 20000. A budget of 1 forces generation on every clip instead of packing
    visual tokens across clips, which is what the perf guide and the
    perf-testing skill already prescribe as the standard EVS++ configuration.
    """
    monkeypatch.delenv("VIA_EVS_TOKEN_BUDGET", raising=False)
    if value is not None:
        monkeypatch.setenv("VIA_EVS_TOKEN_BUDGET", value)

    assert vllm_compatible_model._get_evs_token_budget() == 1


def test_evs_token_budget_honors_an_explicit_value(monkeypatch):
    monkeypatch.setenv("VIA_EVS_TOKEN_BUDGET", "20000")

    assert vllm_compatible_model._get_evs_token_budget() == 20000


def test_rtvi_vllm_env_sanitizer_unsets_blank_import_env(monkeypatch):
    monkeypatch.setenv("VLLM_CONFIGURE_LOGGING", "")
    monkeypatch.setenv("VLLM_LOGGING_LEVEL", "")

    vllm_compatible_model._sanitize_rtvi_vllm_env()

    assert "VLLM_CONFIGURE_LOGGING" not in vllm_compatible_model.os.environ
    assert "VLLM_LOGGING_LEVEL" not in vllm_compatible_model.os.environ


def test_rtvi_vllm_env_sanitizer_preserves_explicit_import_env(monkeypatch):
    monkeypatch.setenv("VLLM_CONFIGURE_LOGGING", "0")
    monkeypatch.setenv("VLLM_LOGGING_LEVEL", "debug")

    vllm_compatible_model._sanitize_rtvi_vllm_env()

    assert vllm_compatible_model.os.environ["VLLM_CONFIGURE_LOGGING"] == "0"
    assert vllm_compatible_model.os.environ["VLLM_LOGGING_LEVEL"] == "debug"


def test_kv_cache_dtype_override_is_forwarded_when_supported(monkeypatch):
    monkeypatch.setenv("VLLM_KV_CACHE_DTYPE", "auto")
    engine_args = {}

    applied = vllm_compatible_model._apply_kv_cache_dtype_override(
        engine_args,
        {"kv_cache_dtype"},
    )

    assert applied is True
    assert engine_args["kv_cache_dtype"] == "auto"


def test_attention_backend_override_is_forwarded_when_supported(monkeypatch):
    monkeypatch.setenv("VLLM_ATTENTION_BACKEND", "TRITON_ATTN")
    engine_args = {}

    applied = vllm_compatible_model._apply_attention_backend_override(
        engine_args,
        {"attention_backend"},
    )

    assert applied is True
    assert engine_args["attention_backend"] == "TRITON_ATTN"


def test_num_preprocess_workers_defaults_to_parallel_video_value(monkeypatch):
    monkeypatch.delenv("VLLM_NUM_PREPROCESS_WORKERS", raising=False)
    monkeypatch.delenv("RTVI_VLLM_NUM_PREPROCESS_WORKERS", raising=False)

    assert vllm_compatible_model._get_num_preprocess_workers() == 16


def test_num_preprocess_workers_uses_rtvi_alias(monkeypatch):
    monkeypatch.setenv("VLLM_NUM_PREPROCESS_WORKERS", "8")
    monkeypatch.setenv("RTVI_VLLM_NUM_PREPROCESS_WORKERS", "12")

    assert vllm_compatible_model._get_num_preprocess_workers() == 12


def test_num_preprocess_workers_rejects_non_positive_values(monkeypatch):
    monkeypatch.setenv("VLLM_NUM_PREPROCESS_WORKERS", "0")

    with pytest.raises(ValueError, match="VLLM_NUM_PREPROCESS_WORKERS"):
        vllm_compatible_model._get_num_preprocess_workers()


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


# --- EVS session sampling kwargs (VLLM_IGNORE_EOS propagation) -----------------


def test_vllm_sampling_kwargs_preserves_zero_temperature(monkeypatch):
    monkeypatch.delenv("VLLM_IGNORE_EOS", raising=False)
    monkeypatch.delenv("RTVI_VLLM_IGNORE_EOS", raising=False)

    kwargs = vllm_compatible_model._build_vllm_sampling_kwargs(VlmGenerationConfig(temperature=0.0))

    assert kwargs["temperature"] == 0.0


def test_vllm_sampling_kwargs_preserves_nonzero_temperature(monkeypatch):
    monkeypatch.delenv("VLLM_IGNORE_EOS", raising=False)
    monkeypatch.delenv("RTVI_VLLM_IGNORE_EOS", raising=False)

    kwargs = vllm_compatible_model._build_vllm_sampling_kwargs(VlmGenerationConfig(temperature=0.7))

    assert kwargs["temperature"] == 0.7


def test_evs_sampling_kwargs_passes_max_tokens_and_defaults(monkeypatch):
    monkeypatch.delenv("VLLM_IGNORE_EOS", raising=False)
    monkeypatch.delenv("RTVI_VLLM_IGNORE_EOS", raising=False)

    kwargs = vllm_compatible_model._build_evs_sampling_kwargs(100, {})

    assert kwargs["max_tokens"] == 100
    assert kwargs["temperature"] == 0.4
    assert kwargs["top_p"] == 0.8
    assert kwargs["top_k"] == 20
    assert kwargs["repetition_penalty"] == 1.1
    assert kwargs["seed"] == 1


def test_evs_sampling_kwargs_omits_ignore_eos_by_default(monkeypatch):
    monkeypatch.delenv("VLLM_IGNORE_EOS", raising=False)
    monkeypatch.delenv("RTVI_VLLM_IGNORE_EOS", raising=False)

    kwargs = vllm_compatible_model._build_evs_sampling_kwargs(100, {})

    assert "ignore_eos" not in kwargs


def test_evs_sampling_kwargs_sets_ignore_eos_from_env(monkeypatch):
    monkeypatch.delenv("RTVI_VLLM_IGNORE_EOS", raising=False)
    monkeypatch.setenv("VLLM_IGNORE_EOS", "true")

    kwargs = vllm_compatible_model._build_evs_sampling_kwargs(100, {})

    assert kwargs["ignore_eos"] is True


def test_evs_sampling_kwargs_sets_ignore_eos_from_config(monkeypatch):
    monkeypatch.delenv("VLLM_IGNORE_EOS", raising=False)
    monkeypatch.delenv("RTVI_VLLM_IGNORE_EOS", raising=False)

    kwargs = vllm_compatible_model._build_evs_sampling_kwargs(100, SimpleNamespace(ignore_eos=True))

    assert kwargs["ignore_eos"] is True


def test_evs_sampling_kwargs_forwards_min_tokens_from_config(monkeypatch):
    monkeypatch.delenv("VLLM_IGNORE_EOS", raising=False)

    kwargs = vllm_compatible_model._build_evs_sampling_kwargs(100, SimpleNamespace(min_tokens=8))

    assert kwargs["min_tokens"] == 8


def test_evs_sampling_kwargs_omits_min_tokens_when_unset(monkeypatch):
    monkeypatch.delenv("VLLM_IGNORE_EOS", raising=False)

    kwargs = vllm_compatible_model._build_evs_sampling_kwargs(100, {})

    assert "min_tokens" not in kwargs
