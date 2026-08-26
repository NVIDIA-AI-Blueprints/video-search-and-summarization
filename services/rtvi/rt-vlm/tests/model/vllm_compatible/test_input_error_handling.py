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
import concurrent.futures
import json
import sys
import threading
from importlib import metadata
from threading import Lock
from types import SimpleNamespace

import numpy as np
import pytest
import torch

import models.vllm_compatible.vllm_compatible_model as vllm_compatible_model
from common.service_exception import ServiceException
from models.base_vlm_model import VlmGenerationConfig
from models.vllm_compatible.adaptive_preprocess_limiter import (
    AdaptivePreprocessConfig,
    AdaptivePreprocessLimiter,
)
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


class _RequestQueue:
    def __init__(self):
        self.request_id = "req-1"
        self._items = [SimpleNamespace(finished=False), SimpleNamespace(finished=True)]
        self.closed = False

    def get_nowait(self):
        return self._items.pop(0)

    async def get(self):
        return self._items.pop(0)

    def close(self):
        self.closed = True


class _AdmissionRecordingLLM:
    def __init__(self, limiter):
        self.limiter = limiter
        self.active_during_add = None
        self.queue = _RequestQueue()

    async def add_request(self, *args, **kwargs):
        self.active_during_add = self.limiter.snapshot().active
        return self.queue

    async def abort(self, *args, **kwargs):
        return None


class _AbortRecordingLLM:
    def __init__(self):
        self.aborted_request_ids = None

    async def abort(self, request_ids):
        self.aborted_request_ids = tuple(request_ids)


class _IdleReleaseLLM:
    def __init__(self):
        self.encoder_cache_reset = False
        self.collective_method = None

    async def reset_encoder_cache(self):
        self.encoder_cache_reset = True

    async def collective_rpc(self, method, timeout=None):
        self.collective_method = method
        return [{"free_mib": 1024, "total_mib": 2048}]


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


def test_abort_live_stream_requests_targets_only_owned_request_ids(monkeypatch):
    model = VllmCompatible.__new__(VllmCompatible)
    model._llm = _AbortRecordingLLM()
    model._event_loop = object()
    model._live_request_ids_lock = Lock()
    model._live_request_ids = {
        "stream-a": {"request-1", "request-2"},
        "stream-b": {"request-3"},
    }
    pending_request = concurrent.futures.Future()
    model._live_request_futures = {"request-1": pending_request}
    model._live_stream_abort_requested = set()

    class _CompletedAbort:
        def result(self, timeout):
            assert timeout == 5.0

    def run_coroutine(coroutine, event_loop):
        assert event_loop is model._event_loop
        asyncio.run(coroutine)
        return _CompletedAbort()

    monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", run_coroutine)

    assert model.abort_live_stream_requests("stream-a", timeout_sec=5.0) == 2
    assert set(model._llm.aborted_request_ids) == {"request-1", "request-2"}
    assert pending_request.cancelled()


def test_request_scheduled_after_stream_abort_is_cancelled():
    model = VllmCompatible.__new__(VllmCompatible)
    model._inflight_req_ids = []
    model._live_request_ids_lock = Lock()
    model._live_request_ids = {}
    model._live_request_futures = {}
    model._live_stream_abort_requested = set()
    pending_request = concurrent.futures.Future()

    assert model.abort_live_stream_requests("stream-a") == 0
    model._register_live_request("stream-a", "request-1")
    model._attach_live_request_future("stream-a", "request-1", pending_request)

    assert pending_request.cancelled()


def test_abort_live_stream_request_cancels_event_loop_task_and_cleans_tracking():
    model = VllmCompatible.__new__(VllmCompatible)
    model._inflight_req_ids = ["request-1"]
    model._live_request_ids_lock = Lock()
    model._live_request_ids = {}
    model._live_request_futures = {}
    model._live_stream_abort_requested = set()
    model._event_loop = asyncio.new_event_loop()

    task_started = threading.Event()
    task_cancelled = threading.Event()
    task_cleaned = threading.Event()

    async def pending_request():
        task_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            task_cancelled.set()
            raise
        finally:
            task_cleaned.set()

    class _CancellationCheckingLLM:
        aborted_request_ids = None
        cancelled_before_abort = False

        async def abort(self, request_ids):
            self.aborted_request_ids = request_ids
            self.cancelled_before_abort = request_future.cancelled()

    model._llm = _CancellationCheckingLLM()
    loop_thread = threading.Thread(target=model._event_loop.run_forever)
    loop_thread.start()
    try:
        model._register_live_request("stream-a", "request-1")
        request_future = asyncio.run_coroutine_threadsafe(pending_request(), model._event_loop)
        model._attach_live_request_future("stream-a", "request-1", request_future)
        request_future.add_done_callback(
            lambda _future: model._release_live_request("stream-a", "request-1")
        )
        assert task_started.wait(timeout=1.0)

        assert model.abort_live_stream_requests("stream-a", timeout_sec=1.0) == 1

        assert model._llm.cancelled_before_abort
        assert tuple(model._llm.aborted_request_ids) == ("request-1",)
        assert task_cancelled.wait(timeout=1.0)
        assert task_cleaned.wait(timeout=1.0)
        assert "stream-a" not in model._live_request_ids
        assert "request-1" not in model._live_request_futures
        assert model._inflight_req_ids == []
    finally:
        model._event_loop.call_soon_threadsafe(model._event_loop.stop)
        loop_thread.join(timeout=1.0)
        model._event_loop.close()


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
    monkeypatch.setenv("VLLM_CUDAGRAPH_MODE", "PIECEWISE")
    monkeypatch.setenv("VLLM_ROOT", "/custom/vllm")
    monkeypatch.setenv("VLLM_USE_AOT_COMPILE", "0")

    vllm_compatible_model._sanitize_rtvi_vllm_env()

    assert "VLLM_GPU_MEMORY_UTILIZATION" not in vllm_compatible_model.os.environ
    assert "VLLM_MAX_NUM_BATCHED_TOKENS" not in vllm_compatible_model.os.environ
    assert "VLLM_NUM_PREPROCESS_WORKERS" not in vllm_compatible_model.os.environ
    assert "VLLM_CUDAGRAPH_MODE" not in vllm_compatible_model.os.environ
    assert "VLLM_ROOT" not in vllm_compatible_model.os.environ
    assert vllm_compatible_model.os.environ["RTVI_VLLM_GPU_MEMORY_UTILIZATION"] == "0.6"
    assert vllm_compatible_model.os.environ["RTVI_VLLM_MAX_NUM_BATCHED_TOKENS"] == "8192"
    assert vllm_compatible_model.os.environ["RTVI_VLLM_NUM_PREPROCESS_WORKERS"] == "16"
    assert vllm_compatible_model.os.environ["RTVI_VLLM_CUDAGRAPH_MODE"] == "PIECEWISE"
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
    monkeypatch.delenv("RTVI_VLLM_NUM_PREPROCESS_WORKERS", raising=False)
    monkeypatch.setenv("VLLM_NUM_PREPROCESS_WORKERS", "0")

    with pytest.raises(ValueError, match="VLLM_NUM_PREPROCESS_WORKERS"):
        vllm_compatible_model._get_num_preprocess_workers()


@pytest.mark.parametrize(
    "value",
    ("NONE", "piecewise", "FULL", "FULL_DECODE_ONLY", "FULL_AND_PIECEWISE"),
)
def test_vllm_compilation_config_accepts_cudagraph_modes(monkeypatch, value):
    monkeypatch.delenv("RTVI_VLLM_CUDAGRAPH_MODE", raising=False)
    monkeypatch.setenv("VLLM_CUDAGRAPH_MODE", value)

    assert vllm_compatible_model._get_vllm_compilation_config() == {
        "mode": "VLLM_COMPILE",
        "cudagraph_mode": value.upper(),
    }


@pytest.mark.parametrize("value", (None, "", "   "))
def test_vllm_compilation_config_is_opt_in(monkeypatch, value):
    monkeypatch.delenv("VLLM_CUDAGRAPH_MODE", raising=False)
    monkeypatch.delenv("RTVI_VLLM_CUDAGRAPH_MODE", raising=False)
    if value is not None:
        monkeypatch.setenv("VLLM_CUDAGRAPH_MODE", value)

    assert vllm_compatible_model._get_vllm_compilation_config() is None


def test_vllm_compilation_config_rejects_unknown_cudagraph_mode(monkeypatch):
    monkeypatch.delenv("RTVI_VLLM_CUDAGRAPH_MODE", raising=False)
    monkeypatch.setenv("VLLM_CUDAGRAPH_MODE", "invalid")

    with pytest.raises(ValueError, match="VLLM_CUDAGRAPH_MODE"):
        vllm_compatible_model._get_vllm_compilation_config()


def test_adaptive_preprocess_is_opt_in_and_shadowed_by_default(monkeypatch):
    monkeypatch.delenv("RTVI_VLLM_ADAPTIVE_PREPROCESS", raising=False)
    monkeypatch.setenv("RTVI_VLLM_NUM_PREPROCESS_WORKERS", "16")

    config = vllm_compatible_model._get_adaptive_preprocess_config()

    assert config.enabled is False
    assert config.shadow_mode is True
    assert config.min_workers == 1
    assert config.max_workers == 16


@pytest.mark.parametrize(
    ("mode", "enabled", "shadow_mode"),
    (
        ("disabled", False, True),
        ("shadow", True, True),
        ("enforced", True, False),
    ),
)
def test_adaptive_preprocess_supports_named_modes(monkeypatch, mode, enabled, shadow_mode):
    monkeypatch.setenv("RTVI_VLLM_ADAPTIVE_PREPROCESS", mode)

    config = vllm_compatible_model._get_adaptive_preprocess_config()

    assert config.enabled is enabled
    assert config.shadow_mode is shadow_mode


def test_adaptive_preprocess_max_is_capped_by_vllm_executor(monkeypatch):
    monkeypatch.setenv("RTVI_VLLM_NUM_PREPROCESS_WORKERS", "8")
    monkeypatch.setenv(
        "RTVI_VLLM_ADAPTIVE_PREPROCESS",
        '{"mode":"enforced","max_workers":16}',
    )

    config = vllm_compatible_model._get_adaptive_preprocess_config()

    assert config.max_workers == 8


def test_adaptive_preprocess_supports_advanced_json_configuration(monkeypatch):
    monkeypatch.setenv(
        "RTVI_VLLM_ADAPTIVE_PREPROCESS",
        json.dumps(
            {
                "mode": "enforced",
                "min_workers": 2,
                "max_workers": 8,
                "gpu_headroom_mb": 2048,
                "initial_estimated_request_mb": 768,
                "estimate_safety_factor": 1.5,
                "admission_timeout_seconds": 45,
                "healthy_completions_for_increase": 4,
                "calibration_samples_required": 5,
                "scale_up_cooldown_seconds": 60,
                "scale_up_gpu_utilization_threshold_percent": 85,
                "estimate_ewma_alpha": 0.5,
                "poll_interval_seconds": 0.1,
            }
        ),
    )

    config = vllm_compatible_model._get_adaptive_preprocess_config()

    assert config.enabled is True
    assert config.shadow_mode is False
    assert config.min_workers == 2
    assert config.max_workers == 8
    assert config.gpu_headroom_mb == 2048
    assert config.initial_estimated_request_mb == 768
    assert config.estimate_safety_factor == 1.5
    assert config.admission_timeout_seconds == 45
    assert config.healthy_completions_for_increase == 4
    assert config.calibration_samples_required == 5
    assert config.scale_up_cooldown_seconds == 60
    assert config.scale_up_gpu_utilization_threshold_percent == 85
    assert config.estimate_ewma_alpha == 0.5
    assert config.poll_interval_seconds == 0.1


def test_can_enqueue_records_backpressure_when_adaptive_submission_queue_is_full():
    limiter = AdaptivePreprocessLimiter(
        AdaptivePreprocessConfig(
            enabled=True,
            shadow_mode=False,
            min_workers=1,
            max_workers=4,
        ),
        lambda: 10000,
    )
    model = VllmCompatible.__new__(VllmCompatible)
    model._multimodal_preprocess_limiter = limiter
    model._cuda_mm_residency_lock = threading.Lock()
    model._adaptive_preprocess_pending_submission_ids = {"pending"}
    model._use_cuda_mm_tensor_ipc = False
    model._inflight_req_ids = []
    model._max_batch_size = 256

    assert model.can_enqueue_requests() is False
    assert limiter.snapshot().backpressure_observed is True


def test_enforced_adaptive_preprocess_honors_cuda_mm_residency_limit():
    limiter = AdaptivePreprocessLimiter(
        AdaptivePreprocessConfig(
            enabled=True,
            shadow_mode=False,
            min_workers=1,
            max_workers=16,
        ),
        lambda: 10000,
    )
    model = VllmCompatible.__new__(VllmCompatible)
    model._multimodal_preprocess_limiter = limiter
    model._cuda_mm_residency_lock = threading.Lock()
    model._adaptive_preprocess_pending_submission_ids = set()
    model._cuda_mm_pending_submission_ids = set()
    model._cuda_mm_resident_units_by_request = {
        "resident": vllm_compatible_model._MAX_RESIDENT_CUDA_MM_2K_EQUIVALENT_UNITS
    }
    model._use_cuda_mm_tensor_ipc = True
    model._inflight_req_ids = []
    model._max_batch_size = 256

    assert model.can_enqueue_requests() is False


def test_release_idle_resources_clears_frontend_and_engine_caches(monkeypatch):
    model = VllmCompatible.__new__(VllmCompatible)
    model._cuda_mm_residency_lock = threading.Lock()
    model._adaptive_preprocess_pending_submission_ids = set()
    model._cuda_mm_pending_submission_ids = set()
    model._cuda_mm_resident_units_by_request = {}
    model._live_request_ids_lock = threading.Lock()
    model._live_request_ids = {}
    model._live_request_futures = {}
    model._inflight_req_ids = []
    model._llm = _IdleReleaseLLM()

    calls = []
    monkeypatch.setattr(torch.cuda, "synchronize", lambda: calls.append("synchronize"))
    monkeypatch.setattr(torch.cuda, "ipc_collect", lambda: calls.append("ipc_collect"))
    monkeypatch.setattr(torch.cuda, "empty_cache", lambda: calls.append("empty_cache"))
    monkeypatch.setattr(torch.cuda, "mem_get_info", lambda: (1024 * 1024, 2 * 1024 * 1024))

    loop = asyncio.new_event_loop()
    loop_thread = threading.Thread(target=loop.run_forever)
    loop_thread.start()
    model._event_loop = loop
    try:
        assert model.release_idle_resources() is True
    finally:
        loop.call_soon_threadsafe(loop.stop)
        loop_thread.join()
        loop.close()

    assert model._llm.encoder_cache_reset is True
    import cloudpickle

    assert isinstance(model._llm.collective_method, bytes)
    assert (
        cloudpickle.loads(model._llm.collective_method)
        is vllm_compatible_model._empty_vllm_worker_cuda_cache
    )
    assert calls == ["synchronize", "ipc_collect", "empty_cache"]


def test_release_idle_resources_skips_active_requests():
    model = VllmCompatible.__new__(VllmCompatible)
    model._cuda_mm_residency_lock = threading.Lock()
    model._adaptive_preprocess_pending_submission_ids = set()
    model._cuda_mm_pending_submission_ids = set()
    model._cuda_mm_resident_units_by_request = {}
    model._live_request_ids_lock = threading.Lock()
    model._live_request_ids = {}
    model._live_request_futures = {}
    model._inflight_req_ids = ["active"]

    assert model.release_idle_resources() is False


@pytest.mark.parametrize(
    ("value", "message"),
    (
        ("true", "mode must be disabled, shadow, or enforced"),
        ("{not-json}", "malformed JSON"),
        ('{"mode":"automatic"}', "mode must be disabled, shadow, or enforced"),
        ('{"mode":true}', "'mode' must be a string"),
        ('{"mode":"shadow","workers":4}', "unknown field.*workers"),
        ('{"mode":"shadow","max_workers":true}', "'max_workers' must be an integer"),
        (
            '{"mode":"shadow","estimate_safety_factor":false}',
            "'estimate_safety_factor' must be a number",
        ),
        (
            '{"mode":"shadow","estimate_safety_factor":NaN}',
            "'estimate_safety_factor' must be a number",
        ),
        (
            '{"mode":"shadow","scale_up_cooldown_seconds":-1}',
            "scale_up_cooldown_seconds must be",
        ),
        (
            '{"mode":"shadow","scale_up_gpu_utilization_threshold_percent":101}',
            "scale_up_gpu_utilization_threshold_percent must be",
        ),
        ('{"mode":"shadow","min_workers":0}', "min_workers must be"),
    ),
)
def test_adaptive_preprocess_rejects_invalid_single_variable_config(monkeypatch, value, message):
    monkeypatch.setenv("RTVI_VLLM_ADAPTIVE_PREPROCESS", value)

    with pytest.raises(ValueError, match=message):
        vllm_compatible_model._get_adaptive_preprocess_config()


def test_multimodal_preprocess_workload_is_model_agnostic_and_shape_sensitive():
    first = torch.empty((10, 3, 640, 640), dtype=torch.uint8, device="meta")
    second = torch.empty((20, 640, 640, 3), dtype=torch.uint8, device="meta")

    first_key, first_payload_mb = VllmCompatible._multimodal_preprocess_workload(
        {"multi_modal_data": {"video": [(first, {})]}}
    )
    repeated_key, repeated_payload_mb = VllmCompatible._multimodal_preprocess_workload(
        {"multi_modal_data": {"video": [(first, {})]}}
    )
    second_key, second_payload_mb = VllmCompatible._multimodal_preprocess_workload(
        {"multi_modal_data": {"video": [(second, {})]}}
    )

    assert (first_key, first_payload_mb) == (repeated_key, repeated_payload_mb)
    assert first_key != second_key
    assert first_payload_mb == 12
    assert second_payload_mb == 24


def test_multimodal_preprocess_workload_includes_processor_options():
    tensor = torch.empty((10, 3, 640, 640), dtype=torch.uint8, device="meta")
    base = {"multi_modal_data": {"video": [(tensor, {})]}}
    resized = {
        **base,
        "mm_processor_kwargs": {"size": {"shortest_edge": 448, "longest_edge": 448}},
    }

    base_key, _ = VllmCompatible._multimodal_preprocess_workload(base)
    resized_key, _ = VllmCompatible._multimodal_preprocess_workload(resized)

    assert base_key != resized_key


def test_multimodal_preprocess_workload_buckets_prompt_and_output_tokens():
    tensor = torch.empty((10, 3, 640, 640), dtype=torch.uint8, device="meta")
    base = {
        "prompt_token_ids": list(range(50)),
        "multi_modal_data": {"video": [(tensor, {})]},
    }

    osl_one_key, _ = VllmCompatible._multimodal_preprocess_workload(
        base,
        SimpleNamespace(max_tokens=1),
    )
    osl_hundred_key, _ = VllmCompatible._multimodal_preprocess_workload(
        base,
        SimpleNamespace(max_tokens=100),
    )

    assert osl_one_key != osl_hundred_key


def test_cuda_free_memory_uses_lowest_visible_device(monkeypatch):
    monkeypatch.setattr(torch.cuda, "device_count", lambda: 3)
    free_by_device = {
        0: (12 * 1024 * 1024, 80 * 1024 * 1024),
        1: (7 * 1024 * 1024, 80 * 1024 * 1024),
        2: (10 * 1024 * 1024, 80 * 1024 * 1024),
    }
    monkeypatch.setattr(torch.cuda, "mem_get_info", lambda device: free_by_device[device])

    assert VllmCompatible._get_cuda_free_memory_mb() == 7


@pytest.mark.parametrize(
    ("use_tensor_ipc", "limiter", "expected"),
    [
        (False, None, False),
        (True, None, True),
        (False, object(), True),
        (True, object(), True),
    ],
)
def test_preprocess_admission_wraps_default_and_tensor_ipc_paths(use_tensor_ipc, limiter, expected):
    assert VllmCompatible._should_use_preprocess_admission(use_tensor_ipc, limiter) is expected


def test_default_path_holds_admission_until_first_engine_output():
    async def run():
        limiter = AdaptivePreprocessLimiter(
            AdaptivePreprocessConfig(
                enabled=True,
                shadow_mode=False,
                min_workers=1,
                max_workers=1,
                gpu_headroom_mb=100,
                initial_estimated_request_mb=500,
            ),
            lambda: 10000,
        )
        model = VllmCompatible.__new__(VllmCompatible)
        model._use_cuda_mm_tensor_ipc = False
        model._multimodal_preprocess_limiter = limiter
        model._llm = _AdmissionRecordingLLM(limiter)
        model._finish_preprocess_submission = lambda request_id: None
        llm_inputs = {
            "multi_modal_data": {"video": [(torch.empty((10, 3, 640, 640), device="meta"), {})]}
        }

        stream = model._generate_with_preprocess_admission(
            llm_inputs,
            SimpleNamespace(),
            "req-1",
        )
        first_output = await stream.__anext__()

        assert first_output.finished is False
        assert model._llm.active_during_add == 1
        assert limiter.snapshot().active == 0

        async for _ in stream:
            pass
        assert model._llm.queue.closed is True

    asyncio.run(run())


def test_preprocess_admission_timeout_is_reported_as_server_busy():
    async def run():
        limiter = AdaptivePreprocessLimiter(
            AdaptivePreprocessConfig(
                enabled=True,
                shadow_mode=False,
                min_workers=1,
                max_workers=1,
                gpu_headroom_mb=100,
                initial_estimated_request_mb=500,
                admission_timeout_seconds=0.01,
                poll_interval_seconds=0.001,
            ),
            lambda: 100,
        )
        model = VllmCompatible.__new__(VllmCompatible)
        model._use_cuda_mm_tensor_ipc = False
        model._multimodal_preprocess_limiter = limiter
        model._llm = _AdmissionRecordingLLM(limiter)
        model._finish_preprocess_submission = lambda request_id: None
        llm_inputs = {
            "multi_modal_data": {"video": [(torch.empty((10, 3, 640, 640), device="meta"), {})]}
        }

        stream = model._generate_with_preprocess_admission(
            llm_inputs,
            SimpleNamespace(),
            "blocked",
        )
        with pytest.raises(ServiceException) as exc_info:
            await stream.__anext__()

        assert exc_info.value.code == "ServerBusy"
        assert exc_info.value.status_code == 503
        assert limiter.snapshot().timeouts == 1

    asyncio.run(run())


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


def test_mm_processor_cache_type_defaults_to_shared_memory(monkeypatch):
    monkeypatch.delenv("VLLM_MM_PROCESSOR_CACHE_TYPE", raising=False)
    monkeypatch.delenv("RTVI_VLLM_MM_PROCESSOR_CACHE_TYPE", raising=False)

    assert vllm_compatible_model._get_mm_processor_cache_type() == "shm"


def test_mm_processor_cache_type_accepts_lru_override(monkeypatch):
    monkeypatch.setenv("VLLM_MM_PROCESSOR_CACHE_TYPE", "lru")

    assert vllm_compatible_model._get_mm_processor_cache_type() == "lru"


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


def test_vllm_sampling_kwargs_enforces_json_schema(monkeypatch):
    monkeypatch.delenv("VLLM_IGNORE_EOS", raising=False)
    monkeypatch.delenv("RTVI_VLLM_IGNORE_EOS", raising=False)
    schema = {
        "type": "object",
        "properties": {"person_visible": {"type": "boolean"}},
        "required": ["person_visible"],
        "additionalProperties": False,
    }
    config = VlmGenerationConfig(
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "alerts", "strict": True, "schema": schema},
        }
    )

    kwargs = vllm_compatible_model._build_vllm_sampling_kwargs(config)

    assert kwargs["structured_outputs"].json == schema


def test_vllm_engine_config_enforces_strict_compact_structured_output():
    kwargs = {}

    vllm_compatible_model._configure_structured_outputs(kwargs, {"structured_outputs_config"})

    config = kwargs["structured_outputs_config"]
    assert config.backend == "xgrammar"
    assert config.disable_fallback is True
    assert config.disable_any_whitespace is True


def test_vllm_sampling_kwargs_enforces_json_object(monkeypatch):
    monkeypatch.setenv("VLLM_IGNORE_EOS", "true")
    monkeypatch.delenv("RTVI_VLLM_IGNORE_EOS", raising=False)

    kwargs = vllm_compatible_model._build_vllm_sampling_kwargs(
        VlmGenerationConfig(response_format={"type": "json_object"})
    )

    assert kwargs["structured_outputs"].json_object is True
    assert kwargs["ignore_eos"] is False


def test_cosmos_no_repeat_ngram_is_disabled_for_structured_output():
    params = SimpleNamespace(structured_outputs=object())

    vllm_compatible_model._set_cosmos_no_repeat_ngram_size(params, "cosmos-reason3")

    assert not hasattr(params, "no_repeat_ngram_size")


def test_cosmos_no_repeat_ngram_is_preserved_for_text_output():
    params = SimpleNamespace(structured_outputs=None)

    vllm_compatible_model._set_cosmos_no_repeat_ngram_size(params, "cosmos-reason3")

    assert params.no_repeat_ngram_size == 3


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
