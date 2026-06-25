# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
import math
import os
import random
import re
import sys
import threading
import uuid
from typing import List, Optional

import numpy
import torch
import torchvision.transforms.functional as TF
from filelock import FileLock
from PIL import Image, ImageDraw, ImageFont

from common.chunk_info import ChunkInfo
from common.logger import TimeMeasure, logger
from models.base_vlm_model import (
    BaseVlmModel,
    InputConfig,
    VlmGenerationConfig,
    VlmModelOutput,
)


def _parse_int_env(name: str, default: int) -> int:
    value = os.environ.get(name, "") or ""
    if not value.strip():
        return default
    try:
        return int(value)
    except ValueError:
        raise ValueError(f"Invalid value for {name}: '{value}' is not a valid integer")


def _parse_optional_int_env(name: str) -> int | None:
    value = os.environ.get(name, "") or ""
    if not value.strip():
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"Invalid value for {name}: '{value}' is not a valid integer") from exc
    if parsed < 0:
        raise ValueError(f"Invalid value for {name}: '{value}' must be greater than or equal to 0")
    return parsed


def _parse_bool_env(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _parse_float_env(name: str, default: float) -> float:
    value = os.environ.get(name, "") or ""
    if not value.strip():
        return default
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(f"Invalid value for {name}: '{value}' is not a valid float") from exc
    if parsed < 0:
        raise ValueError(f"Invalid value for {name}: '{value}' must be greater than or equal to 0")
    return parsed


def _get_mm_processor_cache_gb() -> float:
    if (os.environ.get("VLLM_MM_PROCESSOR_CACHE_GB", "") or "").strip():
        return _parse_float_env("VLLM_MM_PROCESSOR_CACHE_GB", 1.0)
    return _parse_float_env("VLLM_MM_INPUT_CACHE_GIB", 1.0)


CPU_COPY_OTHER_THREAD = True


# Both the EA checkpoint (NemotronH_Nano_VL_V2) and the GA checkpoint
# (NemotronH_Nano_Omni_Reasoning_V3) share the same model executor and
# require identical special-casing throughout the inference path.
_NEMOTRON_OMNI_ARCHS = frozenset({"NemotronH_Nano_VL_V2", "NemotronH_Nano_Omni_Reasoning_V3"})

# Some architectures require a custom vLLM 0.17 build installed at
# /opt/nvidia/vllm-0.17.0 (see docker/rtvi_vlm/Dockerfile). The default
# vLLM 25.11 stays in dist-packages.
_VLLM017_PATH = "/opt/nvidia/vllm-0.17.0"
_VLLM017_CUTLASS_PATH = os.path.join(_VLLM017_PATH, "nvidia_cutlass_dsl", "python_packages")
_VLLM012_PATH = "/opt/nvidia/vllm-0.12.0"
_QWEN35_ARCHS = frozenset(
    {
        "Qwen3_5ForConditionalGeneration",
        "Qwen3_5MoeForConditionalGeneration",
    }
)
_QWEN3VL_ARCHS = frozenset(
    {
        "Qwen3VLForConditionalGeneration",
    }
)
_VLLM017_ARCHS = _QWEN35_ARCHS

_COSMOS3_DIFFUSERS_ARCHS = frozenset({"Cosmos3ForConditionalGeneration"})

# Common parameters
FACTOR = 28
MAX_PIXELS = 16384 * 2 * FACTOR * FACTOR
MIN_PIXELS = 4 * 2 * FACTOR * FACTOR

ADD_TIMESTAMP_TO_PROMPT = (
    os.environ.get("RTVI_ADD_TIMESTAMP_TO_VLM_PROMPT", "true").lower() == "true"
)

# Gates the absolute-timestamp prompt format (mirrors the openai-compat / NIM
# format) for the integrated Cosmos-Reason2 backend only. When false (or when
# the model is not cosmos-reason2), fall back to the legacy "These are images
# sampled from the same video at times ..." prompt — i.e., main behavior.
ENABLE_LVS_CR2_TIMESTAMP_PROMPT = (
    os.environ.get("ENABLE_LVS_CR2_TIMESTAMP_PROMPT", "false").lower() == "true"
)

DEFAULT_SYSTEM_PROMPT_CR1 = (
    "Please provide captions of all the events in the video with timestamps using the following format:"
    " <start time> <end time> caption of event 1.\n<start time> <end time> caption of event 2.\n"
    "At each frame, the timestamp is embedded at the bottom of the video. You need to extract"
    " the timestamp and answer the user question."
)


def start_loop(loop):
    asyncio.set_event_loop(loop)
    loop.run_forever()


def _prepend_python_paths(paths: List[str]):
    existing_paths = [path for path in os.environ.get("PYTHONPATH", "").split(os.pathsep) if path]
    valid_paths = [path for path in paths if os.path.exists(path)]

    for path in reversed(valid_paths):
        while path in sys.path:
            sys.path.remove(path)
        sys.path.insert(0, path)

    os.environ["PYTHONPATH"] = os.pathsep.join(
        valid_paths + [path for path in existing_paths if path not in valid_paths]
    )


def _is_cosmos3_diffusers_shim_arch(model_architecture: str) -> bool:
    return model_architecture in _COSMOS3_DIFFUSERS_ARCHS


def _maybe_register_cosmos3_vllm_shim(model_architecture: str) -> bool:
    if not _is_cosmos3_diffusers_shim_arch(model_architecture):
        return False

    os.environ.setdefault("VLLM_USE_DEEP_GEMM", "0")
    try:
        import vllm_cosmos3

        vllm_cosmos3.register()
    except Exception as exc:
        raise RuntimeError(
            "Cosmos3 diffusers checkpoints require the vllm_cosmos3 shim package. "
            "Ensure the RTVI VLM image includes src/vllm_cosmos3 from the Cosmos3 "
            "vLLM shim layer."
        ) from exc

    logger.info(
        "Enabled Cosmos3 vLLM diffusers shim for architecture %s",
        model_architecture,
    )
    return True


def _get_vlm_trust_remote_code(model_architecture: str) -> bool:
    trust_remote_code = _parse_bool_env("VLM_TRUST_REMOTE_CODE", False)
    if _is_cosmos3_diffusers_shim_arch(model_architecture):
        if not trust_remote_code:
            logger.info(
                "Enabling trust_remote_code for Cosmos3 diffusers shim architecture %s",
                model_architecture,
            )
        return True
    return trust_remote_code


def _is_nvfp4_quantized(model_config: dict) -> bool:
    quantization_config = model_config.get("quantization_config") or {}
    quantization_format = str(quantization_config.get("format", "")).lower()
    if "nvfp4" in quantization_format:
        return True

    for group in (quantization_config.get("config_groups") or {}).values():
        for section_name in ("weights", "input_activations", "output_activations"):
            section = group.get(section_name) or {}
            if section.get("num_bits") == 4 and section.get("type") == "float":
                return True
    return False


def _is_fp8_quantized(model_config: dict) -> bool:
    quantization_config = model_config.get("quantization_config") or {}
    quantization_format = str(quantization_config.get("format", "")).lower()
    if "fp8" in quantization_format:
        return True

    for group in (quantization_config.get("config_groups") or {}).values():
        for section_name in ("weights", "input_activations", "output_activations"):
            section = group.get(section_name) or {}
            if section.get("num_bits") == 8 and section.get("type") == "float":
                return True
    return False


def _is_cr3_quantized_qwen3vl(model_architecture: str, model_config: dict) -> bool:
    if model_architecture not in _QWEN3VL_ARCHS:
        return False
    return _is_nvfp4_quantized(model_config) or _is_fp8_quantized(model_config)


def _requires_vllm017(model_architecture: str, model_config: dict) -> bool:
    if model_architecture in _VLLM017_ARCHS:
        return True
    if _is_cosmos3_diffusers_shim_arch(model_architecture):
        return True
    return model_architecture in _QWEN3VL_ARCHS and _is_nvfp4_quantized(model_config)


# Canonical Qwen3-VL extra_special_tokens role mapping. Some Qwen3-VL-arch
# checkpoints (e.g. CR3 nano-reasoner modelopt-quantized FP8/NVFP4 builds)
# ship extra_special_tokens as a flat list of token strings, which transformers
# >=4.55 rejects with `AttributeError: 'list' object has no attribute 'keys'`
# in _set_model_specific_special_tokens. vLLM 0.17's new HF renderer
# (vllm/renderers/hf.py) trips this during AsyncLLMEngine init.
_QWEN3VL_EXTRA_SPECIAL_TOKENS = {
    "image_token": "<|image_pad|>",
    "video_token": "<|video_pad|>",
    "vision_bos_token": "<|vision_start|>",
    "vision_eos_token": "<|vision_end|>",
}


def _normalize_cosmos3_diffusers_config(model_path: str) -> None:
    """Rewrite the top-level Cosmos3 omni-diffusers-pipeline config.json into a
    Transformers-loadable Qwen3-VL config that vLLM's ModelConfig can validate.

    Post-04/17 revisions of nvidia-cosmos-ea/Cosmos3-{Nano,Super} ship a top-level
    config with model_type=cosmos3_omni and the full OmniMoTModel diffusion config
    nested under the `model` key. Transformers doesn't know cosmos3_omni and the
    repo ships no auto_map, so vLLM's ModelConfig pydantic validation aborts via
    AutoConfig before architecture-based dispatch reaches the
    Cosmos3ForConditionalGeneration shim. The same checkpoint already carries
    top-level text_config (qwen3_vl_text) and vision_config (qwen3_vl) alongside
    image/video/vision token IDs, so synthesis is a structural rename:
      - drop the `model` key (OmniMoTModel diffusion config, unused by the shim)
      - flip model_type from `cosmos3_omni` to `qwen3_vl`
    Other top-level keys (architectures, *_token_id, tie_word_embeddings,
    allow_patterns_overrides, transformers_version) are preserved verbatim.

    The original is preserved at config.cosmos3_omni.json so diffusers consumers
    of the same checkpoint dir keep a usable pipeline config. Idempotent: re-runs
    no-op once model_type has been flipped."""
    cfg_path = os.path.join(model_path, "config.json")
    if not os.path.exists(cfg_path):
        return
    try:
        with open(cfg_path) as f:
            cfg = json.load(f)
        if cfg.get("model_type") != "cosmos3_omni":
            return
        if "text_config" not in cfg or "vision_config" not in cfg:
            logger.warning(
                "Cosmos3 omni config at %s lacks text_config/vision_config; "
                "cannot synthesize Qwen3-VL config",
                cfg_path,
            )
            return

        backup_path = os.path.join(model_path, "config.cosmos3_omni.json")
        if not os.path.exists(backup_path):
            with open(backup_path, "w") as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
                f.write("\n")

        synthesized = {k: v for k, v in cfg.items() if k != "model"}
        synthesized["model_type"] = "qwen3_vl"

        tmp_path = cfg_path + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(synthesized, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp_path, cfg_path)
        logger.warning(
            "Synthesized Qwen3-VL config at %s from cosmos3_omni omni-diffusers "
            "checkpoint (original preserved at %s)",
            cfg_path,
            backup_path,
        )
    except Exception:
        logger.exception("Failed to synthesize Cosmos3 Qwen3-VL config at %s", cfg_path)


def _normalize_qwen3vl_tokenizer_config(model_path: str) -> None:
    """Rewrite a malformed list-shaped extra_special_tokens to the canonical
    Qwen3-VL role->token dict, in-place in the model dir. No-op when already
    a dict or absent. Only intended for CR3 FP8/NVFP4 Qwen3-VL checkpoints."""
    cfg_path = os.path.join(model_path, "tokenizer_config.json")
    if not os.path.exists(cfg_path):
        return
    try:
        with open(cfg_path) as f:
            cfg = json.load(f)
        cur = cfg.get("extra_special_tokens")
        if not isinstance(cur, list):
            return
        present = set(cur)
        mapped = {k: v for k, v in _QWEN3VL_EXTRA_SPECIAL_TOKENS.items() if v in present}
        cfg["extra_special_tokens"] = mapped
        with open(cfg_path, "w") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
            f.write("\n")
        logger.warning(
            "Normalized %s: extra_special_tokens list -> dict %s",
            cfg_path,
            sorted(mapped.keys()),
        )
    except Exception:
        logger.exception("Failed to normalize tokenizer_config at %s", cfg_path)


class VllmCompatible(BaseVlmModel):
    def _initialize_model(self, vlm_model_type="", **kwargs):
        """Initialize the VllmCompatible model"""
        # Initialize model-specific attributes
        self._vlm_model_type = vlm_model_type
        self.model_dir_name = os.path.basename(os.path.normpath(self.model_path))

        # Set resize parameters
        self._max_pixels = MAX_PIXELS
        self._min_pixels = MIN_PIXELS

        self._model_architecture = ""
        try:
            with open(os.path.join(self.model_path, "config.json")) as f:
                model_config = json.load(f)
            self._model_architecture = model_config.get("architectures", [""])[0]
            if _is_cr3_quantized_qwen3vl(self._model_architecture, model_config):
                _normalize_qwen3vl_tokenizer_config(self.model_path)
            if _is_cosmos3_diffusers_shim_arch(self._model_architecture):
                _normalize_cosmos3_diffusers_config(self.model_path)
            if os.path.exists(_VLLM017_PATH) and _requires_vllm017(
                self._model_architecture, model_config
            ):
                # vLLM 0.17 spawns subprocesses (e.g. registry inspection) that
                # must also load the side-installed vLLM, not the default 25.11
                # in dist-packages. sys.path is process-local; PYTHONPATH propagates.
                _prepend_python_paths([_VLLM017_CUTLASS_PATH, _VLLM017_PATH])
                logger.debug(
                    "Using vllm from %s for architecture %s",
                    _VLLM017_PATH,
                    self._model_architecture,
                )
            elif (
                os.path.exists(_VLLM012_PATH)
                and self._model_architecture not in _NEMOTRON_OMNI_ARCHS
            ):
                _prepend_python_paths([_VLLM012_PATH])
                logger.debug(
                    "Using vllm from %s for architecture %s",
                    _VLLM012_PATH,
                    self._model_architecture,
                )
        except Exception:
            logger.debug("Failed to get model architecture from config.json")

        if self._vlm_model_type == "cosmos-reason1":
            self._system_prompt = DEFAULT_SYSTEM_PROMPT_CR1
        else:
            self._system_prompt = ""

        if self._system_prompt:
            logger.info("VllmCompatible default system prompt: %s", self._system_prompt)

        # Initialize the actual model components
        logger.info("Using VLLM model for vllm-compatible")
        os.environ["VLLM_CACHE_ROOT"] = os.path.join(self.model_path, ".vllm")

        _maybe_register_cosmos3_vllm_shim(self._model_architecture)

        from transformers import AutoProcessor
        from vllm.engine.arg_utils import AsyncEngineArgs
        from vllm.engine.async_llm_engine import AsyncLLMEngine

        self._num_time_tokens = 0
        self._model_name = "vllm-compatible"
        model_lock_path = self.model_path + "/.lock"
        with FileLock(model_lock_path):
            logger.info("Initializing VllmCompatible model from: %s", self.model_path)
            gpu_memory_utilization_env = os.environ.get("VLLM_GPU_MEMORY_UTILIZATION", "0.7")
            if not gpu_memory_utilization_env.strip():
                gpu_memory_utilization_env = "0.7"
            try:
                gpu_memory_utilization = float(gpu_memory_utilization_env)
            except ValueError:
                raise ValueError(
                    f"Invalid value for VLLM_GPU_MEMORY_UTILIZATION: "
                    f"'{gpu_memory_utilization_env}' is not a valid float"
                )

            logger.debug(
                "VLLM GPU memory utilization requirement set to: %s%%",
                gpu_memory_utilization * 100,
            )
            max_num_batched_tokens_env = os.environ.get("VLLM_MAX_NUM_BATCHED_TOKENS", "")
            max_num_batched_tokens = None
            if max_num_batched_tokens_env.strip():
                try:
                    max_num_batched_tokens = int(max_num_batched_tokens_env)
                except ValueError:
                    raise ValueError(
                        f"Invalid value for VLLM_MAX_NUM_BATCHED_TOKENS: "
                        f"'{max_num_batched_tokens_env}' is not a valid integer"
                    )
            try:
                # Check if model supports audio via environment variable
                vlm_supports_audio = (
                    os.environ.get("VLM_MODEL_SUPPORTS_AUDIO", "false").lower() == "true"
                )

                limit_mm_per_prompt = {"image": 1, "video": 1}

                # Add audio limit if VLM model supports native audio processing
                if vlm_supports_audio:
                    limit_mm_per_prompt["audio"] = 1

                import inspect

                _engine_supported_params = set(
                    inspect.signature(AsyncEngineArgs.__init__).parameters
                )

                # Build engine args, only including params supported by the installed vLLM version
                engine_args_kwargs = {
                    "model": self.model_path,
                    "max_model_len": _parse_int_env("VLM_MAX_MODEL_LEN", 32768),
                    "limit_mm_per_prompt": limit_mm_per_prompt,
                    "gpu_memory_utilization": gpu_memory_utilization,
                    "max_num_seqs": self._max_batch_size,
                    "tensor_parallel_size": torch.cuda.device_count(),
                }

                kv_cache_memory_bytes = _parse_optional_int_env("VLLM_KV_CACHE_MEMORY_BYTES")
                if kv_cache_memory_bytes is not None:
                    if "kv_cache_memory_bytes" in _engine_supported_params:
                        engine_args_kwargs["kv_cache_memory_bytes"] = kv_cache_memory_bytes
                        logger.info(
                            "VLLM KV cache memory bytes override: %s",
                            kv_cache_memory_bytes,
                        )
                    else:
                        logger.warning(
                            "VLLM_KV_CACHE_MEMORY_BYTES=%s ignored; installed vLLM does not "
                            "support kv_cache_memory_bytes",
                            kv_cache_memory_bytes,
                        )

                if "enable_prefix_caching" in _engine_supported_params:
                    engine_args_kwargs["enable_prefix_caching"] = (
                        os.environ.get("VLLM_ENABLE_PREFIX_CACHING", "true").lower() == "true"
                    )

                disable_mm_cache = _parse_bool_env(
                    "VLLM_DISABLE_MM_PREPROCESSOR_CACHE",
                    default=True,
                )
                if "disable_mm_preprocessor_cache" in _engine_supported_params:
                    engine_args_kwargs["disable_mm_preprocessor_cache"] = disable_mm_cache
                if disable_mm_cache and "mm_processor_cache_gb" in _engine_supported_params:
                    engine_args_kwargs["mm_processor_cache_gb"] = float(
                        os.environ.get("VLLM_MM_PROCESSOR_CACHE_GB", "0") or "0"
                    )

                mm_tensor_ipc = os.environ.get("VLLM_MM_TENSOR_IPC", "").strip()
                if mm_tensor_ipc:
                    if "mm_tensor_ipc" in _engine_supported_params:
                        engine_args_kwargs["mm_tensor_ipc"] = mm_tensor_ipc
                        logger.info("VLLM MM tensor IPC mode: %s", mm_tensor_ipc)
                    else:
                        logger.warning(
                            "VLLM_MM_TENSOR_IPC=%s ignored; installed vLLM does not support "
                            "mm_tensor_ipc",
                            mm_tensor_ipc,
                        )

                multimodal_tensor_ipc = os.environ.get("VLLM_MULTIMODAL_TENSOR_IPC", "").strip()
                if multimodal_tensor_ipc:
                    if "multimodal_tensor_ipc" in _engine_supported_params:
                        engine_args_kwargs["multimodal_tensor_ipc"] = (
                            multimodal_tensor_ipc.lower() == "true"
                        )
                        logger.info(
                            "VLLM multimodal tensor IPC enabled: %s",
                            engine_args_kwargs["multimodal_tensor_ipc"],
                        )
                    else:
                        logger.warning(
                            "VLLM_MULTIMODAL_TENSOR_IPC=%s ignored; installed vLLM does not "
                            "support multimodal_tensor_ipc",
                            multimodal_tensor_ipc,
                        )

                mm_encoder_attn_backend = os.environ.get("VLLM_MM_ENCODER_ATTN_BACKEND", "").strip()
                if mm_encoder_attn_backend:
                    if "mm_encoder_attn_backend" in _engine_supported_params:
                        engine_args_kwargs["mm_encoder_attn_backend"] = mm_encoder_attn_backend
                        logger.info(
                            "VLLM MM encoder attention backend override: %s",
                            mm_encoder_attn_backend,
                        )
                    else:
                        logger.warning(
                            "VLLM_MM_ENCODER_ATTN_BACKEND=%s ignored; installed vLLM does not "
                            "support mm_encoder_attn_backend",
                            mm_encoder_attn_backend,
                        )

                if "enable_chunked_prefill" in _engine_supported_params:
                    engine_args_kwargs["enable_chunked_prefill"] = True

                if "enforce_eager" in _engine_supported_params:
                    enforce_eager = os.environ.get("VLLM_ENFORCE_EAGER", "false").lower() == "true"
                    engine_args_kwargs["enforce_eager"] = enforce_eager
                    if enforce_eager:
                        logger.info("VLLM enforce_eager enabled via VLLM_ENFORCE_EAGER")

                vlm_trust_remote_code = _get_vlm_trust_remote_code(self._model_architecture)
                if "trust_remote_code" in _engine_supported_params:
                    engine_args_kwargs["trust_remote_code"] = vlm_trust_remote_code

                if max_num_batched_tokens is not None:
                    engine_args_kwargs["max_num_batched_tokens"] = max_num_batched_tokens

                moe_backend = os.environ.get("VLLM_MOE_BACKEND", "").strip()
                moe_backend_source = "override"
                if not moe_backend and self._model_architecture in _QWEN35_ARCHS:
                    gpu_names = [
                        torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())
                    ]
                    if any("B200" in name for name in gpu_names):
                        moe_backend = "triton"
                        moe_backend_source = "B200 default"
                        logger.info(
                            "Defaulting Qwen3.5 MoE backend to triton on B200; "
                            "set VLLM_MOE_BACKEND to override"
                        )
                if moe_backend and "moe_backend" in _engine_supported_params:
                    engine_args_kwargs["moe_backend"] = moe_backend
                    logger.info("Using vLLM MoE backend %s: %s", moe_backend_source, moe_backend)

                # EVS (Efficient Video Sampling): prune redundant video tokens
                # Set VLM_VIDEO_PRUNING_RATE=0.5 for 50% pruning. 0 or empty = disabled.
                video_pruning_rate_str = os.environ.get("VLM_VIDEO_PRUNING_RATE", "")
                if video_pruning_rate_str and "video_pruning_rate" in _engine_supported_params:
                    try:
                        rate = float(video_pruning_rate_str)
                        if 0 < rate < 1:
                            engine_args_kwargs["video_pruning_rate"] = rate
                            logger.info("EVS enabled: video_pruning_rate=%.2f", rate)
                        elif rate != 0:
                            logger.warning(
                                "VLM_VIDEO_PRUNING_RATE=%.2f out of range (0,1), EVS disabled",
                                rate,
                            )
                    except ValueError:
                        logger.warning(
                            "Invalid VLM_VIDEO_PRUNING_RATE='%s', EVS disabled",
                            video_pruning_rate_str,
                        )

                engine_args = AsyncEngineArgs(**engine_args_kwargs)
                self._llm = AsyncLLMEngine.from_engine_args(engine_args)
                self._processor = AutoProcessor.from_pretrained(
                    self.model_path, trust_remote_code=vlm_trust_remote_code
                )
            except Exception as e:
                logger.error("Error initializing VLLM model: %s", e)
                if os.environ.get("VLLM_ENFORCE_EAGER", "false").lower() != "true":
                    logger.warning(
                        "If this vLLM initialization failure is in the torch.compile/CUDA graph "
                        "path, retry with VLLM_ENFORCE_EAGER=true to disable CUDA graph capture."
                    )
                raise

            self._event_loop = asyncio.new_event_loop()
            logger.debug("Event loop created")
            self._event_loop_thread = threading.Thread(target=start_loop, args=(self._event_loop,))
            logger.debug("Starting event loop thread")
            self._event_loop_thread.start()
            logger.debug("Event loop thread started")
            logger.info("VllmCompatible VLLM model initialized successfully")

    @property
    def model_name(self):
        return self._model_name

    def get_conv(self):
        # Initialize _conv if not already done
        if not hasattr(self, "_conv"):
            self._conv = []
        return self._conv.copy()

    def _get_apply_chat_template_kwargs(self, config: VlmGenerationConfig) -> dict:
        # Reasoning-capable chat templates open a <think> block by default. Keep the RTVI
        # default non-reasoning unless the request explicitly enables reasoning.
        if (
            self._model_architecture in _NEMOTRON_OMNI_ARCHS
            or self._model_architecture in _QWEN35_ARCHS
        ):
            return {"enable_thinking": bool(config.enable_reasoning)}
        return {}

    def _remove_orphan_think_tags(self, text: str, reasoning_description: str) -> tuple:
        # Handle orphan </think> (no opening <think> — start token was cut off or never generated).
        # Everything before </think> is reasoning; everything after is the actual answer.
        close_idx = text.find("</think>")
        if close_idx != -1:
            if not reasoning_description:
                reasoning_description = text[:close_idx]
            text = text[close_idx + len("</think>") :]
        # Handle orphan <think> (no closing </think> — truncated generation mid-reasoning).
        # Answer always follows </think>, so there is no answer text here; warn the user.
        think_idx = text.find("<think>")
        if think_idx != -1:
            if not reasoning_description:
                reasoning_description = text[think_idx + len("<think>") :]
            text = ""
            logger.warning(
                "Generated text is empty after removing incomplete reasoning block. "
                "The model likely ran out of tokens mid-reasoning. "
                "Consider increasing MAX_MODEL_LEN or max_tokens."
            )
        return text, reasoning_description

    def _postprocess_vllm(
        self,
        output,
        video_frames_times,
        chunk=None,
        ignore_eos=False,
        preserve_reasoning_tags=False,
    ):
        with TimeMeasure("VLLM postprocess"):
            original_output = output
            if hasattr(output, "result"):
                output = output.result()
                if original_output in self._inflight_req_ids:
                    self._inflight_req_ids.remove(original_output)
            elif isinstance(output, concurrent.futures.Future):
                output = output.result()
                if original_output in self._inflight_req_ids:
                    self._inflight_req_ids.remove(original_output)

            # Extract and validate response
            if not output or not output[0].outputs:
                logger.warning("No output generated from model")
                return [
                    VlmModelOutput(
                        output="Error: No response generated", input_tokens=0, output_tokens=0
                    )
                ]

            generated_text = output[0].outputs[0].text
            logger.debug("VLLM raw text output: %s", generated_text)
            if not generated_text:
                logger.warning("Empty response from model")
                return [VlmModelOutput(output="", input_tokens=0, output_tokens=0)]

            if preserve_reasoning_tags:
                final_response = generated_text.strip() if not ignore_eos else generated_text
                reasoning_description = ""
            else:
                # Step 1: Strip leading/trailing whitespace
                cleaned_text = generated_text.strip() if not ignore_eos else generated_text
                # Step 2: Extract reasoning description
                reasoning_description = re.search(
                    r"<think>(.*?)</think>", cleaned_text, flags=re.DOTALL
                )
                if reasoning_description:
                    reasoning_description = reasoning_description.group(1)
                else:
                    reasoning_description = ""
                # Step 3: Remove complete <think>...</think> block if found, otherwise handle orphan tags
                if reasoning_description:
                    cleaned_text = re.sub(r"<think>.*?</think>", "", cleaned_text, flags=re.DOTALL)
                else:
                    cleaned_text, reasoning_description = self._remove_orphan_think_tags(
                        cleaned_text, reasoning_description
                    )
                logger.debug("VLLM reasoning description: %s", reasoning_description)
                # Step 4: Remove <answer>, </answer>, <summary>, and </summary> tags, but keep their content
                for tag in ["<answer>", "</answer>", "<summary>", "</summary>"]:
                    cleaned_text = cleaned_text.replace(tag, "")
                # Step 4: Final cleanup (strip whitespace)
                final_response = cleaned_text.strip() if not ignore_eos else cleaned_text
            logger.debug("VLLM cleaned text output: %s", final_response)

            try:
                input_tokens = (
                    len(output[0].prompt_token_ids) if hasattr(output[0], "prompt_token_ids") else 0
                )
                output_tokens = (
                    len(output[0].outputs[0].token_ids)
                    if hasattr(output[0].outputs[0], "token_ids")
                    else 0
                )
            except (AttributeError, IndexError):
                input_tokens = 0
                output_tokens = 0

            logger.debug(
                "VLM result: total_prompt_tokens=%d (text+visual), output_tokens=%d",
                input_tokens,
                output_tokens,
            )

            try:
                if chunk and self._vlm_model_type == "cosmos-reason1":
                    final_response = self._update_video_frames_times(
                        final_response, chunk, video_frames_times
                    )
            except Exception as e:
                logger.error("Error updating video frames times: %s", e)

            return [
                VlmModelOutput(
                    output=final_response,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    reasoning_description=reasoning_description,
                )
            ]

    def _update_video_frames_times(self, text, chunk, video_frames_times):
        updated_text = re.sub(
            r"<([0-9]+(?:\.[0-9]+)?)>",
            lambda m: "<"
            + chunk.get_timestamp(float(video_frames_times[0]) + float(m.group(1)))
            + ">",
            text,
        )
        return updated_text

    async def process_async_vllm(
        self,
        llm_inputs,
        vllm_sampling_params,
        video_frames_times,
        request_id,
        chunk=None,
        preserve_reasoning_tags=False,
    ):
        if CPU_COPY_OTHER_THREAD:
            if "video" in llm_inputs["multi_modal_data"]:
                video_tensor = llm_inputs["multi_modal_data"]["video"][0][0]
                video_metadata = llm_inputs["multi_modal_data"]["video"][0][1]

                # Run CPU copy in thread pool to avoid blocking event loop. Even
                # with vLLM mm_tensor_ipc=torch_shm, raw RTVI video frames must
                # enter vLLM as numpy so the model processor handles layout and
                # resizing normally. Tensor IPC is for vLLM's processed tensors.
                video_tensor_cpu = await asyncio.to_thread(lambda: video_tensor.cpu().numpy())

                llm_inputs["multi_modal_data"]["video"][0] = (
                    video_tensor_cpu,
                    video_metadata,
                )
            else:
                # Single image: extract tensor, convert to CPU for vLLM.
                # NemotronH_Nano_VL_V2/Omni_Reasoning_V3 use NanoNemotronVLProcessor whose image path
                # calls image.size expecting a PIL Image (tuple), not a numpy array (int).
                # The video path works because video_to_pixel_values does Image.fromarray
                # internally, but the image path has no such conversion.
                images_tensor = llm_inputs["multi_modal_data"]["image"][0]
                images_numpy = await asyncio.to_thread(lambda: images_tensor.cpu().numpy())
                if self._model_architecture in _NEMOTRON_OMNI_ARCHS:
                    # Squeeze batch dim if present: (1, H, W, C) → (H, W, C)
                    img_arr = images_numpy.squeeze(0) if images_numpy.ndim == 4 else images_numpy
                    llm_inputs["multi_modal_data"]["image"] = Image.fromarray(img_arr, mode="RGB")
                else:
                    llm_inputs["multi_modal_data"]["image"] = images_numpy

        logger.debug(
            f"Request {request_id} entering AsyncLLMEngine queue. "
            f"Inflight requests: {len(self._inflight_req_ids)}"
        )

        final_output = None
        with TimeMeasure("vLLM generate"):
            try:
                async for output_item in self._llm.generate(
                    llm_inputs, sampling_params=vllm_sampling_params, request_id=request_id
                ):
                    final_output = output_item
            except ValueError as e:
                # vLLM raises ValueError for input-validation failures: decoder
                # prompt longer than max_model_len, image count over the
                # processor's per-prompt cap, etc. These are user-input issues
                # (too many frames for the chosen model), not server crashes.
                # Surface as ServiceException 400 with an actionable message
                # so the client knows what to change, and log a single-line
                # WARNING with the suggested fix instead of a multi-frame
                # traceback that drowns the logs (nvbug 6110762).
                self._inflight_req_ids.remove(request_id)
                err_msg = str(e)
                if (
                    "is longer than the maximum model length" in err_msg
                    or "may be provided in one prompt" in err_msg
                ):
                    logger.warning(
                        "vLLM rejected input as exceeding model limits: %s. "
                        "Reduce num-frames-per-second-or-fixed-frames-chunk, "
                        "shorten chunk_duration, or raise VLM_MAX_MODEL_LEN to "
                        "cover the prompt length.",
                        err_msg,
                    )
                    from common.service_exception import ServiceException

                    raise ServiceException(
                        f"Input exceeds model limits: {err_msg} Reduce frames "
                        f"per chunk or raise VLM_MAX_MODEL_LEN.",
                        "InvalidParameter",
                        400,
                    ) from e
                logger.error("Error during vLLM generate: %s", e)
                raise
            except Exception as e:
                logger.error("Error during vLLM generate: %s", e)
                self._inflight_req_ids.remove(request_id)
                raise e

        if not final_output:
            logger.warning("Async for retuned no output")
            self._inflight_req_ids.remove(request_id)
            return [
                VlmModelOutput(
                    output="Error: No response generated", input_tokens=0, output_tokens=0
                )
            ]
        self._inflight_req_ids.remove(request_id)

        return self._postprocess_vllm(
            [final_output],
            video_frames_times,
            chunk,
            (
                vllm_sampling_params.ignore_eos
                if hasattr(vllm_sampling_params, "ignore_eos")
                else False
            ),
            preserve_reasoning_tags,
        )

    def can_enqueue_requests(self):
        """Check if the model can accept new requests."""
        return len(self._inflight_req_ids) < self._max_batch_size

    def warmup(self):
        """Warm up the model with dummy tensors to initialize CUDA kernels and memory."""
        logger.info("Starting model warmup...")

        # VLLM multimodal warmup - create dummy tensors and follow the complete VLLM flow.
        dummy_images = torch.stack(
            [torch.ones(100, 100, 3, dtype=torch.uint8).cuda() for _ in range(8)]
        )
        video_warmup_prompt = "Describe this video briefly."
        video_warmup_config = VlmGenerationConfig(
            temperature=0.7,
            max_new_tokens=50,  # Short for warmup
            top_p=0.9,
            top_k=100,
            repetition_penalty=1.1,
            seed=42,
        )
        text_warmup_config = VlmGenerationConfig(
            temperature=0.4,
            max_new_tokens=8,
            top_p=0.8,
            top_k=20,
            repetition_penalty=1.1,
            seed=42,
        )
        try:
            logger.info("Starting video model warmup...")
            video_ret = self.generate(
                video_warmup_prompt,
                chunks=[ChunkInfo()],  # chunks
                video_frames=[dummy_images],  # video_frames
                video_frames_times=[list(range(8))],  # video_frames_times
                generation_config=video_warmup_config,
            )
            video_result = video_ret.result()
            logger.info("Video model warmup completed")

            logger.info("Starting text-only model warmup...")
            text_ret = self.generate_text_only(
                messages=[{"role": "user", "content": "Reply with: ok"}],
                generation_config=text_warmup_config,
            )
            text_result = text_ret.result()
            logger.info("Text-only model warmup completed")

            return text_result or video_result
        except Exception as e:
            logger.error("Error during model warmup: %s", e)
            raise e

    def _shutdown_model(self):
        logger.info("Shutting down VllmCompatibleModel...")

        # Shutdown the AsyncLLMEngine
        async def shutdown_engine():
            self._llm.shutdown()

        asyncio.run_coroutine_threadsafe(shutdown_engine(), self._event_loop).result(timeout=5.0)

        # Stop the event loop gracefully
        logger.debug("Stopping event loop")
        self._event_loop.call_soon_threadsafe(self._event_loop.stop)
        self._event_loop_thread.join(timeout=5.0)

        # Close the event loop
        if not self._event_loop.is_closed():
            self._event_loop.close()

        logger.info("VllmCompatibleModel shutdown complete")

    @property
    def num_time_tokens(self):
        return self._num_time_tokens

    def smart_resize_tensor(self, images: torch.Tensor) -> torch.Tensor:
        """
        Resize a tensor image so that:
        - Its total pixels are between min_pixels and max_pixels.
        - Height and width are divisible by 'factor'.
        - Aspect ratio is preserved.
        """
        # Assuming image is in (H, W, C) format
        n, c, h, w = images.shape
        logger.debug("smart_resize_tensor: n: %d, h: %d, w: %d, c: %d", n, h, w, c)
        orig_pixels = h * w
        n = n + n % 2

        min_pixels = MIN_PIXELS / n
        max_pixels = MAX_PIXELS / n

        # Determine scaling factor based on pixel bounds
        scale = None
        if orig_pixels < min_pixels:
            scale = math.sqrt(min_pixels / orig_pixels)
        elif orig_pixels > max_pixels:
            scale = math.sqrt(max_pixels / orig_pixels)
        logger.debug(
            "smart_resize_tensor: scale: %s, orig_pixels: %d, min_pixels: %f, max_pixels: %f",
            scale,
            orig_pixels,
            min_pixels,
            max_pixels,
        )

        if scale is not None:
            new_h = int(round(h * scale))
            new_w = int(round(w * scale))

            new_w = new_w // FACTOR * FACTOR
            new_h = new_h // FACTOR * FACTOR

            images = TF.resize(
                images,
                [new_h, new_w],
                interpolation=TF.InterpolationMode.BICUBIC,
                antialias=True,
            )

        logger.debug("smart_resize_tensor: resized tensor shape: %s", images.shape)

        return images

    def generate(
        self,
        query: str,
        chunks: List[ChunkInfo],
        video_frames: Optional[List[torch.Tensor]] = None,
        video_frames_times: List[List[float]] = None,
        generation_config: Optional[VlmGenerationConfig] = None,
        audio_frames=None,
        **kwargs,
    ):
        """Generate a response for prompt using the video frames

        Args:
            query: Prompt for the VLM model or ChatConversation object
            chunks: List of chunk information
            video_frames: List of video frames
            video_frames_times: List of video frame times
            generation_config: VLM generation config. Defaults to None.
            audio_frames: Decoded audio frames from video. Defaults to None.
            **kwargs: Additional keyword arguments for future extensibility and API compatibility
                     across different model implementations. Currently unused but preserved for
                     maintaining consistent interface across all model classes.

        Returns:
            List of responses for the batch of chunks
        """
        query_text = query

        video_frames_times = video_frames_times[0]
        chunk = chunks[0]

        # Get generation config with defaults
        config = generation_config or VlmGenerationConfig()

        # Build generation params dict for the model (excluding non-generation params)
        generation_params = {
            "max_new_tokens": config.max_new_tokens,
            "top_p": config.top_p,
            "top_k": int(config.top_k),
            "repetition_penalty": config.repetition_penalty,
        }

        # Only include temperature if it's not 0
        if config.temperature != 0:
            generation_params["temperature"] = config.temperature

        # Set the seed
        seed = config.seed
        random.seed(seed)
        numpy.random.seed(seed)
        torch.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)

        # Handle system prompt
        system_prompt = config.system_prompt if config.system_prompt else self._system_prompt

        # Override system prompt in environment variable with reasoning prompt if enable_reasoning is True
        if (
            self._vlm_model_type == "cosmos-reason1"
            and config.enable_reasoning
            and "<think>" not in system_prompt
        ):
            system_prompt += (
                " Answer the question in the following format: "
                "<think>\nyour reasoning\n</think>\n\n<answer>\nyour answer\n</answer>.\n"
            )

        if (
            self._vlm_model_type in ("cosmos-reason2", "cosmos-reason3")
            and config.enable_reasoning
            and "<think>" not in query_text
            and "<think>" not in system_prompt
        ):
            query_text += (
                "Answer the question using the following format:\n\n"
                "<think>\n"
                "Your reasoning.\n"
                "</think>\n\n"
                "Write your final answer immediately after the </think> tag.\n"
            )

        if self._vlm_model_type == "cosmos-reason1":
            cr1_frames = video_frames[0]
            if isinstance(cr1_frames, torch.Tensor) and not cr1_frames.is_cuda:
                cr1_frames = cr1_frames.cuda(non_blocking=True)
            images = self.overlay_frame_number_cr1(cr1_frames, video_frames_times).half()

            # convert PIL Images to tensors
            images = self.smart_resize_tensor(images)
        else:
            images = video_frames[0]

        # Cap frames to VLLM_MM_PROCESSOR_VIDEO_NUM_FRAMES to prevent vLLM rejecting
        # >256 images. When fps-based chunking produces many frames (e.g. 10fps × 60s = 600),
        # uniformly subsample to the configured limit before sending to the engine.
        max_frames = int(os.environ.get("VLLM_MM_PROCESSOR_VIDEO_NUM_FRAMES", "256") or "256")
        if len(images) > 1 and len(images) > max_frames:
            indices = torch.linspace(0, len(images) - 1, max_frames).long()
            images = images[indices]
            video_frames_times = [video_frames_times[i] for i in indices.tolist()]
            logger.info(
                "VLM generate: subsampled %d frames to %d (VLLM_MM_PROCESSOR_VIDEO_NUM_FRAMES)",
                len(video_frames[0]),
                max_frames,
            )

        add_timestamp_to_prompt = (
            self._vlm_model_type != "cosmos-reason1" and ADD_TIMESTAMP_TO_PROMPT
        )

        # Audio is processed natively by the VLM when VLM_MODEL_SUPPORTS_AUDIO=true.
        # RIVA ASR is not yet supported; process_audio_in_vlm is true only for Omni models.
        process_audio_in_vlm = os.environ.get("VLM_MODEL_SUPPORTS_AUDIO", "false").lower() == "true"

        # Handle nested list structure: audio_frames is [[dict, ...]]
        # Only check for audio data if VLM should process it
        has_audio = False
        if process_audio_in_vlm and audio_frames is not None and len(audio_frames) > 0:
            if isinstance(audio_frames[0], list):
                # Nested list structure: [[dict, ...]]
                inner = audio_frames[0]
                has_audio = (
                    len(inner) > 0
                    and isinstance(inner[0], dict)
                    and inner[0].get("audio") is not None
                )
            elif isinstance(audio_frames[0], dict):
                # Flat list structure: [dict, ...]
                has_audio = audio_frames[0].get("audio") is not None

        if add_timestamp_to_prompt and chunk and video_frames_times:
            # Only the integrated Cosmos-Reason2 backend opts into the new
            # absolute-timestamp prompt format, and only when the flag is on.
            # All other models (cosmos-reason1, cosmos-reason3, qwen, etc.)
            # keep the legacy prompt to preserve main-branch behavior.
            use_cr2_timestamp_prompt = (
                ENABLE_LVS_CR2_TIMESTAMP_PROMPT and self._vlm_model_type == "cosmos-reason2"
            )
            if use_cr2_timestamp_prompt:
                # Mirror the openai-compat (NIM) prompt format so the model is
                # explicitly told that the listed frame times are absolute and
                # that its response must use them. Without these cues the model
                # tends to emit chunk-relative offsets starting at 0.
                if chunk.file.startswith("rtsp://"):
                    time_format_str = " at timestamps in RFC3339 format"
                else:
                    time_format_str = " at timestamps in seconds"

                string_of_times = ""
                for frame_time in video_frames_times:
                    string_of_times += "<" + chunk.get_timestamp(frame_time) + "> "

                if len(video_frames_times) > 1:
                    first_ts = chunk.get_timestamp(video_frames_times[0])
                    last_ts = chunk.get_timestamp(video_frames_times[-1])
                    frame_mapping = (
                        f"Frame 1 corresponds to timestamp {first_ts} seconds, "
                        f"and the last frame corresponds to timestamp {last_ts} seconds. "
                    )
                    timestamp_instruction = (
                        f" IMPORTANT: {frame_mapping}"
                        f"All timestamps in your response MUST be between {first_ts} and {last_ts}"
                        f" seconds. Do NOT use timestamps starting from 0. The video segment"
                        f" starts at {first_ts} seconds in the original video."
                    )
                else:
                    timestamp_instruction = "Make sure the answer contains correct timestamps."

                query_text = (
                    "These are images sampled from a video"
                    + time_format_str
                    + " : "
                    + string_of_times
                    + ".\n"
                    + query_text
                    + "\n"
                    + timestamp_instruction
                )
            else:
                string_of_times = ""
                for frame_time in video_frames_times:
                    string_of_times += chunk.get_timestamp(frame_time)
                    string_of_times += " "
                query_text = (
                    "These are images sampled from the same video at times "
                    + string_of_times
                    + ". "
                    + query_text
                )

        # VLLM model generation

        is_single_image = len(images) == 1

        if is_single_image:
            input = (images if CPU_COPY_OTHER_THREAD else images.cpu().numpy(),)
        else:
            duration = video_frames_times[-1] - video_frames_times[0]
            fps = 1
            if len(video_frames_times) > 1 and duration > 0:
                fps = (len(video_frames_times) - 1) / duration

            input = (
                images if CPU_COPY_OTHER_THREAD else images.cpu().numpy(),
                {
                    "total_num_frames": len(images),
                    "frames_indices": list(range(len(images))),
                    "fps": fps,
                    "duration": duration,
                },
            )

        # Single query mode
        messages = []
        logger.debug("System prompt %s user prompt %s", system_prompt, query_text)
        if system_prompt:
            messages.append(
                {
                    "role": "system",
                    "content": system_prompt,
                }
            )

        # Build message content based on modalities
        message_content = []

        if is_single_image:
            message_content.extend(
                [
                    {"type": "text", "text": query_text},
                    {"type": "image", "image": "sample.jpg"},
                ]
            )
        else:
            if self._vlm_model_type in ("cosmos-reason2", "cosmos-reason3"):
                message_content.append({"type": "video", "video": "sample.mp4"})
                message_content.append({"type": "text", "text": query_text})
            else:
                message_content.append({"type": "text", "text": query_text})
                message_content.append({"type": "video", "video": "sample.mp4"})

        # Add audio if VLM should process it natively (not handled by RIVA ASR)
        if process_audio_in_vlm and has_audio:
            message_content.append({"type": "audio", "audio": "sample.wav"})

        messages.append(
            {
                "role": "user",
                "content": message_content,
            }
        )

        # Reasoning-capable chat templates open a <think> block by default. Keep the RTVI
        # default non-reasoning unless the request explicitly enables reasoning.
        apply_chat_template_kwargs = self._get_apply_chat_template_kwargs(config)

        prompt = self._processor.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
            **apply_chat_template_kwargs,
        )

        # NemotronH_Nano_VL_V2/Omni_Reasoning_V3 chat template stringifies multimodal content
        # dicts rather than inserting placeholder tokens. Detect this and rebuild with explicit
        # placeholders so vLLM can find and replace them with visual features.
        if self._model_architecture in _NEMOTRON_OMNI_ARCHS:
            if is_single_image and "<image>" not in prompt:
                image_placeholder = f"{query_text}\n<image>"
                fallback_messages = (
                    [{"role": "system", "content": system_prompt}] if system_prompt else []
                ) + [{"role": "user", "content": image_placeholder}]
                prompt = self._processor.apply_chat_template(
                    fallback_messages,
                    tokenize=False,
                    add_generation_prompt=True,
                    **apply_chat_template_kwargs,
                )
            elif not is_single_image and "<video>" not in prompt:
                video_placeholder = f"{query_text}\n<video>"
                fallback_messages = (
                    [{"role": "system", "content": system_prompt}] if system_prompt else []
                ) + [{"role": "user", "content": video_placeholder}]
                prompt = self._processor.apply_chat_template(
                    fallback_messages,
                    tokenize=False,
                    add_generation_prompt=True,
                    **apply_chat_template_kwargs,
                )

        # NemotronH_Nano_VL_V2/Omni_Reasoning_V3: when audio is passed directly (not extracted
        # from video bytes), vllm's processor expects <so_embedding> in the prompt to locate the
        # audio modality. The model's own apply() does: prompt.replace("<video>", "<video><so_embedding>", 1)
        # We replicate that here for both video and single-image (audio-only chunks) input.
        if (
            process_audio_in_vlm
            and has_audio
            and self._model_architecture in _NEMOTRON_OMNI_ARCHS
            and "<so_embedding>" not in prompt
        ):
            if "<video>" in prompt:
                prompt = prompt.replace("<video>", "<video><so_embedding>", 1)
            elif "<image>" in prompt:
                prompt = prompt.replace("<image>", "<image><so_embedding>", 1)

        # Tokenize the prompt to get token IDs
        prompt_token_ids = self._processor.tokenizer.encode(prompt, add_special_tokens=False)

        # Prepare multimodal data
        if is_single_image:
            mm_data = {"image": input}
        else:
            mm_data = {"video": [input]}

        # Add audio if VLM should process it natively (not handled by RIVA ASR)
        if process_audio_in_vlm and has_audio:
            # Flatten nested list structure if needed: [[dict, ...]] -> [dict, ...]
            flat_audio_frames = (
                audio_frames[0] if isinstance(audio_frames[0], list) else audio_frames
            )
            audio_data = self._process_audio_frames(flat_audio_frames)
            if audio_data is not None:
                mm_data["audio"] = audio_data
            else:
                logger.warning("Audio processing returned None — audio will NOT be sent to model")

        # Prepare LLM inputs
        mm_processor_kwargs = {}

        if self._vlm_model_type == "cosmos-reason1":
            mm_processor_kwargs["chain_of_thought"] = True

        # Merge user-provided mm_processor_kwargs from request
        if config.mm_processor_kwargs:
            # Validate 'size' requires both shortest_edge and longest_edge
            if "size" in config.mm_processor_kwargs:
                size = config.mm_processor_kwargs["size"]
                if isinstance(size, dict):
                    if "shortest_edge" in size and "longest_edge" not in size:
                        size["longest_edge"] = 12845056  # Default from NIM docs
                    elif "longest_edge" in size and "shortest_edge" not in size:
                        size["shortest_edge"] = 3136  # Default from NIM docs
            mm_processor_kwargs.update(config.mm_processor_kwargs)

        # Note: media_io_kwargs (fps/num_frames) controls frame sampling at the
        # RTVI pipeline level (video_file_frame_getter), NOT at the vLLM engine level.
        # Do NOT merge it into mm_processor_kwargs — it would conflict with
        # multi_modal_data's video key and cause hash_kwargs() errors.

        # Pass prompt_token_ids instead of text prompt for better performance
        llm_inputs = {
            "prompt_token_ids": prompt_token_ids,
            "multi_modal_data": mm_data,
            "mm_processor_kwargs": mm_processor_kwargs,
        }
        multi_modal_uuids = {}
        if not is_single_image:
            multi_modal_uuids["video"] = [None]
        if "audio" in mm_data:
            multi_modal_uuids["audio"] = [None]
        # vLLM requires all modalities in multi_modal_data to have uuids when the dict is set.
        # Single-image + audio leaves "image" missing from uuids while image data is present.
        if is_single_image and multi_modal_uuids:
            multi_modal_uuids["image"] = [None]
        if multi_modal_uuids:
            llm_inputs["multi_modal_uuids"] = multi_modal_uuids

        # Log effective params for debugging NIM API compatibility
        num_frames = len(images) if images is not None else 0
        logger.debug(
            "VLM generate: prompt_tokens=%d, num_frames=%d, is_single_image=%s, "
            "mm_processor_kwargs=%s, generation_params=%s",
            len(prompt_token_ids),
            num_frames,
            is_single_image,
            {k: v for k, v in mm_processor_kwargs.items() if k != "chain_of_thought"},
            {
                "max_tokens": generation_params["max_new_tokens"],
                "top_p": generation_params["top_p"],
                "top_k": generation_params["top_k"],
                "temperature": generation_params.get("temperature", "default"),
                "repetition_penalty": generation_params["repetition_penalty"],
            },
        )

        # Generate response using generation parameters
        from vllm import SamplingParams

        sp_kwargs = {
            "top_p": generation_params["top_p"],
            "top_k": generation_params["top_k"],
            "max_tokens": generation_params["max_new_tokens"],
            "repetition_penalty": generation_params["repetition_penalty"],
        }
        if config.min_tokens is not None:
            sp_kwargs["min_tokens"] = config.min_tokens
        env_ignore_eos = os.getenv("VLLM_IGNORE_EOS", "false").lower() == "true"
        if env_ignore_eos or config.ignore_eos is not None:
            sp_kwargs["ignore_eos"] = env_ignore_eos or bool(config.ignore_eos)
        vllm_sampling_params = SamplingParams(**sp_kwargs)
        if "temperature" in generation_params:
            vllm_sampling_params.temperature = generation_params["temperature"]
        if self._vlm_model_type in ("cosmos-reason2", "cosmos-reason3"):
            vllm_sampling_params.no_repeat_ngram_size = 3

        try:
            request_id = str(uuid.uuid4())
            self._inflight_req_ids.append(request_id)

            return asyncio.run_coroutine_threadsafe(
                self.process_async_vllm(
                    llm_inputs,
                    vllm_sampling_params,
                    video_frames_times,
                    request_id,
                    chunks[0],
                    config.preserve_reasoning_tags,
                ),
                self._event_loop,
            )

        except Exception as e:
            logger.error("Error during VLLM async generation: %s", e)
            return [
                VlmModelOutput(output="Error: Generation failed", input_tokens=0, output_tokens=0)
            ]

    def generate_text_only(
        self,
        messages: list[dict],
        generation_config: Optional[VlmGenerationConfig] = None,
    ):
        """Text-only generation using the vLLM engine (no multimodal data)."""
        config = generation_config or VlmGenerationConfig()

        generation_params = {
            "max_new_tokens": config.max_new_tokens,
            "top_p": config.top_p,
            "top_k": int(config.top_k),
            "repetition_penalty": config.repetition_penalty,
        }
        if config.temperature != 0:
            generation_params["temperature"] = config.temperature

        prompt = self._processor.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
            **self._get_apply_chat_template_kwargs(config),
        )
        prompt_token_ids = self._processor.tokenizer.encode(prompt, add_special_tokens=False)

        llm_inputs = {"prompt_token_ids": prompt_token_ids}

        from vllm import SamplingParams

        sp_kwargs = {
            "top_p": generation_params["top_p"],
            "top_k": generation_params["top_k"],
            "max_tokens": generation_params["max_new_tokens"],
            "repetition_penalty": generation_params["repetition_penalty"],
        }
        if config.min_tokens is not None:
            sp_kwargs["min_tokens"] = config.min_tokens
        env_ignore_eos = os.getenv("VLLM_IGNORE_EOS", "false").lower() == "true"
        if env_ignore_eos or config.ignore_eos is not None:
            sp_kwargs["ignore_eos"] = env_ignore_eos or bool(config.ignore_eos)
        vllm_sampling_params = SamplingParams(**sp_kwargs)
        if "temperature" in generation_params:
            vllm_sampling_params.temperature = generation_params["temperature"]

        request_id = str(uuid.uuid4())
        self._inflight_req_ids.append(request_id)

        return asyncio.run_coroutine_threadsafe(
            self._process_text_only_async(
                llm_inputs,
                vllm_sampling_params,
                request_id,
                config.preserve_reasoning_tags,
            ),
            self._event_loop,
        )

    async def _process_text_only_async(
        self,
        llm_inputs,
        vllm_sampling_params,
        request_id,
        preserve_reasoning_tags=False,
    ):
        """Async vLLM generation without multimodal data."""
        final_output = None
        try:
            async for output_item in self._llm.generate(
                llm_inputs, sampling_params=vllm_sampling_params, request_id=request_id
            ):
                final_output = output_item
        except Exception as e:
            logger.error("Error during text-only vLLM generate: %s", e)
            self._inflight_req_ids.remove(request_id)
            raise

        self._inflight_req_ids.remove(request_id)

        if not final_output or not final_output.outputs:
            return [
                VlmModelOutput(
                    output="Error: No response generated", input_tokens=0, output_tokens=0
                )
            ]

        reasoning_description = ""
        generated_text = final_output.outputs[0].text.strip()
        if preserve_reasoning_tags:
            logger.debug("Preserving vLLM reasoning tags in text-only output")
        else:
            # Extract reasoning if present
            reasoning_match = re.search(r"<think>(.*?)</think>", generated_text, flags=re.DOTALL)
            if reasoning_match:
                reasoning_description = reasoning_match.group(1)
                generated_text = re.sub(r"<think>.*?</think>", "", generated_text, flags=re.DOTALL)
            else:
                generated_text, reasoning_description = self._remove_orphan_think_tags(
                    generated_text, reasoning_description
                )
            for tag in ["<answer>", "</answer>", "<summary>", "</summary>"]:
                generated_text = generated_text.replace(tag, "")
            generated_text = generated_text.strip()

        try:
            input_tokens = (
                len(final_output.prompt_token_ids)
                if hasattr(final_output, "prompt_token_ids")
                else 0
            )
            output_tokens = (
                len(final_output.outputs[0].token_ids)
                if hasattr(final_output.outputs[0], "token_ids")
                else 0
            )
        except (AttributeError, IndexError):
            input_tokens = 0
            output_tokens = 0

        return [
            VlmModelOutput(
                output=generated_text,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                reasoning_description=reasoning_description,
            )
        ]

    async def generate_text_only_stream(
        self,
        messages: list[dict],
        generation_config: Optional[VlmGenerationConfig] = None,
    ):
        """Async generator yielding text deltas for token-level streaming."""
        config = generation_config or VlmGenerationConfig()

        generation_params = {
            "max_new_tokens": config.max_new_tokens,
            "top_p": config.top_p,
            "top_k": int(config.top_k),
            "repetition_penalty": config.repetition_penalty,
        }
        if config.temperature != 0:
            generation_params["temperature"] = config.temperature

        prompt = self._processor.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
            **self._get_apply_chat_template_kwargs(config),
        )
        prompt_token_ids = self._processor.tokenizer.encode(prompt, add_special_tokens=False)

        llm_inputs = {"prompt_token_ids": prompt_token_ids}

        from vllm import SamplingParams

        sp_kwargs = {
            "top_p": generation_params["top_p"],
            "top_k": generation_params["top_k"],
            "max_tokens": generation_params["max_new_tokens"],
            "repetition_penalty": generation_params["repetition_penalty"],
        }
        if config.min_tokens is not None:
            sp_kwargs["min_tokens"] = config.min_tokens
        env_ignore_eos = os.getenv("VLLM_IGNORE_EOS", "false").lower() == "true"
        if env_ignore_eos or config.ignore_eos is not None:
            sp_kwargs["ignore_eos"] = env_ignore_eos or bool(config.ignore_eos)
        vllm_sampling_params = SamplingParams(**sp_kwargs)
        if "temperature" in generation_params:
            vllm_sampling_params.temperature = generation_params["temperature"]

        request_id = str(uuid.uuid4())
        self._inflight_req_ids.append(request_id)

        previous_text = ""
        try:
            async for output_item in self._llm.generate(
                llm_inputs, sampling_params=vllm_sampling_params, request_id=request_id
            ):
                if output_item.outputs:
                    current_text = output_item.outputs[0].text
                    delta = current_text[len(previous_text) :]
                    if delta:
                        previous_text = current_text
                        yield delta
        except Exception as e:
            logger.error("Error during text-only vLLM streaming: %s", e)
            raise
        finally:
            if request_id in self._inflight_req_ids:
                self._inflight_req_ids.remove(request_id)

    def _process_audio_frames(self, audio_frames):
        """
        Process audio frames into format expected by Nemotron Nano and other audio-capable VLMs.

        Args:
            audio_frames: List of dicts with structure
                         [{"audio": numpy_array, "start": timestamp, "end": timestamp}]
                         Audio is expected to be PCM at 16kHz sample rate from GStreamer.

        Returns:
            numpy float32 array of audio samples, or None on failure.
        """
        try:
            if not audio_frames or len(audio_frames) == 0:
                return None

            # Concatenate all audio chunks
            audio_chunks = []
            for frame_dict in audio_frames:
                if frame_dict.get("audio") is not None:
                    audio_data = frame_dict["audio"]
                    if isinstance(audio_data, torch.Tensor):
                        audio_data = audio_data.cpu().numpy()
                    audio_chunks.append(audio_data)

            if not audio_chunks:
                logger.warning("No valid audio data found in audio_frames")
                return None

            concatenated_audio = numpy.concatenate(audio_chunks, axis=0)

            # Convert to float32 normalized to [-1, 1] range as expected by most audio models
            if concatenated_audio.dtype == numpy.int16:
                audio_float = concatenated_audio.astype(numpy.float32) / 32768.0
            else:
                audio_float = concatenated_audio.astype(numpy.float32)

            # Return as plain numpy array (not a tuple with sample rate).
            # GStreamer provides audio at 16kHz which matches the model's expected rate.
            # Passing as a tuple (audio, sr) causes vLLM to attempt resampling, which
            # fails when the model's data parser has no target_sr configured.
            return audio_float

        except Exception as e:
            logger.error("Error processing audio frames: %s", e, exc_info=True)
            return None

    def overlay_frame_number_cr1(
        self,
        images: torch.Tensor,
        video_frames_times: List[float],
        border_height: int = 28,  # this is due to patch size of 28
        temporal_path_size: int = 2,  # Number of positions to cycle through
        font_size: int = 20,
        font_color: str = "white",
    ) -> torch.Tensor:
        """
        Overlay text on a batch of image tensors with black border using GPU acceleration.
        The timestamp position cycles through available positions.

        Args:
            images: Tensor of images on GPU with shape (N, H, W, C) with values in [0, 255]
            video_frames_times: List of timestamps for each frame
            border_height: Height of the black border in pixels (default: 28)
            temporal_path_size: Number of positions to cycle through (default: 2)
            font_size: Font size for the text (default: 20)
            font_color: Color of the text (default: "white")

        Returns:
            Tensor of images with text overlay, shape (N, C, H+border_height, W) in [0, 255] range
        """
        if images.numel() == 0:
            return images

        # Get dimensions from tensor shape (N, H, W, C)
        num_images, height, width, channels = images.shape
        new_height = height + border_height

        # Try to use DejaVu Sans Mono font for better readability
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", font_size)

        batch_images = images.permute(0, 3, 1, 2).float()
        batch_with_borders = torch.zeros(
            (num_images, channels, new_height, width), dtype=batch_images.dtype, device="cuda"
        )

        # Paste original images at the top (vectorized operation on GPU)
        batch_with_borders[:, :, :height, :] = batch_images

        text_tensors = []
        for i in range(num_images):
            text_overlay = Image.new("RGBA", (width, border_height), color=(0, 0, 0, 0))
            draw = ImageDraw.Draw(text_overlay)

            text = f"{float(video_frames_times[i])-float(video_frames_times[0]):.2f}s"

            try:
                bbox = draw.textbbox((0, 0), text, font=font)
                text_width = bbox[2] - bbox[0]
                text_height = bbox[3] - bbox[1]
            except AttributeError:
                text_width, text_height = draw.textsize(text, font=font)

            # Calculate position (cycling through horizontal positions)
            position_idx = i % temporal_path_size
            section_width = width // temporal_path_size
            section_center_x = position_idx * section_width + section_width // 2
            text_x = section_center_x - text_width // 2
            text_x = max(0, min(text_x, width - text_width))
            text_y = (border_height - text_height) // 2

            # Draw text
            draw.text((text_x, text_y), text, fill=font_color, font=font)

            # Convert RGBA to RGB (composite on black background)
            text_rgb = Image.new("RGB", (width, border_height), color="black")
            text_rgb.paste(text_overlay, (0, 0), text_overlay)

            # Convert PIL image directly to tensor without normalization
            # PIL format: (H, W, C) with [0, 255] -> Tensor: (C, H, W) with [0, 255]
            text_array = numpy.array(text_rgb)
            text_tensor = torch.from_numpy(text_array).cuda().permute(2, 0, 1).float()
            text_tensors.append(text_tensor)

        batch_text = torch.stack(text_tensors).cuda()

        batch_with_borders[:, :, height:, :] = batch_text

        return batch_with_borders

    @staticmethod
    def get_model_info(model_path: str, vlm_model_type: str = ""):
        model_dir_name = os.path.basename(os.path.normpath(model_path))
        return (
            model_dir_name,
            "internal",
            (
                "NVIDIA"
                if vlm_model_type in ["cosmos-reason1", "cosmos-reason2", "cosmos-reason3"]
                else "custom"
            ),
        )

    @staticmethod
    def get_input_config(model_path: str, vlm_model_type: str = "") -> InputConfig:
        """Get input-specific configuration parameters for VllmCompatible."""

        num_frames = 20
        try:
            with open(model_path + "/config.json") as f:
                model_config = json.load(f)
            num_frames = model_config.get("num_video_frames", 20)
        except Exception as e:
            logger.warning(f"Could not load VllmCompatible input config from {model_path}: {e}")

        return InputConfig(
            num_frames=num_frames,
            use_jpeg_encoding=False,
            width=608 if vlm_model_type in ["cosmos-reason2", "cosmos-reason3"] else 532,
            height=320 if vlm_model_type in ["cosmos-reason2", "cosmos-reason3"] else 280,
        )
