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
import string
import threading
import traceback
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

_RTVI_VLLM_ENV_ALIASES = {
    "VLLM_GPU_MEMORY_UTILIZATION": "RTVI_VLLM_GPU_MEMORY_UTILIZATION",
    "VLLM_MAX_NUM_BATCHED_TOKENS": "RTVI_VLLM_MAX_NUM_BATCHED_TOKENS",
    "VLLM_ENABLE_PREFIX_CACHING": "RTVI_VLLM_ENABLE_PREFIX_CACHING",
    "VLLM_ENFORCE_EAGER": "RTVI_VLLM_ENFORCE_EAGER",
    "VLLM_DISABLE_MM_PREPROCESSOR_CACHE": "RTVI_VLLM_DISABLE_MM_PREPROCESSOR_CACHE",
    "VLLM_MM_PROCESSOR_CACHE_GB": "RTVI_VLLM_MM_PROCESSOR_CACHE_GB",
    "VLLM_MM_ENCODER_ATTN_BACKEND": "RTVI_VLLM_MM_ENCODER_ATTN_BACKEND",
    "VLLM_MM_TENSOR_IPC": "RTVI_VLLM_MM_TENSOR_IPC",
    "VLLM_MULTIMODAL_TENSOR_IPC": "RTVI_VLLM_MULTIMODAL_TENSOR_IPC",
    "VLLM_MOE_BACKEND": "RTVI_VLLM_MOE_BACKEND",
    "VLLM_IGNORE_EOS": "RTVI_VLLM_IGNORE_EOS",
    "VLLM_MM_PROCESSOR_VIDEO_NUM_FRAMES": "RTVI_VLLM_MM_PROCESSOR_VIDEO_NUM_FRAMES",
    "VLLM_ATTENTION_BACKEND": "RTVI_VLLM_ATTENTION_BACKEND",
    "VLLM_KV_CACHE_DTYPE": "RTVI_VLLM_KV_CACHE_DTYPE",
    "VLLM_KV_CACHE_MEMORY_BYTES": "RTVI_VLLM_KV_CACHE_MEMORY_BYTES",
    "VLLM_MAX_NUM_SEQS": "RTVI_VLLM_MAX_NUM_SEQS",
    "VLLM_NUM_SCHEDULER_STEPS": "RTVI_VLLM_NUM_SCHEDULER_STEPS",
    "VLLM_NUM_PREPROCESS_WORKERS": "RTVI_VLLM_NUM_PREPROCESS_WORKERS",
    "VLLM_ROOT": "RTVI_VLLM_ROOT",
}

_DEFAULT_VLLM_NUM_PREPROCESS_WORKERS = 16
_BLANK_DEFAULT_VLLM_IMPORT_ENV_VARS = (
    "VLLM_CONFIGURE_LOGGING",
    "VLLM_LOGGING_LEVEL",
)


def _get_rtvi_vllm_env(name: str, default: str | None = None) -> str | None:
    alias = _RTVI_VLLM_ENV_ALIASES.get(name)
    if alias and alias in os.environ:
        return os.environ[alias]
    return os.environ.get(name, default)


def _sanitize_rtvi_vllm_env() -> None:
    """Hide or normalize env values that vLLM reads during import."""
    blank_native = []
    for name in _BLANK_DEFAULT_VLLM_IMPORT_ENV_VARS:
        value = os.environ.get(name)
        if value is not None and not value.strip():
            os.environ.pop(name, None)
            blank_native.append(name)

    moved = []
    for source, target in _RTVI_VLLM_ENV_ALIASES.items():
        value = os.environ.pop(source, None)
        if value is None:
            continue
        if target not in os.environ:
            os.environ[target] = value
        moved.append(source)
    if blank_native:
        logger.debug(
            "Unset blank vLLM import env vars before vLLM import: %s",
            ", ".join(sorted(blank_native)),
        )
    if moved:
        logger.debug(
            "Moved RTVI-owned vLLM env aliases before vLLM import: %s",
            ", ".join(sorted(moved)),
        )


def _parse_int_env(name: str, default: int) -> int:
    value = _get_rtvi_vllm_env(name, "") or ""
    if not value.strip():
        return default
    try:
        return int(value)
    except ValueError:
        raise ValueError(f"Invalid value for {name}: '{value}' is not a valid integer")


def _parse_optional_int_env(name: str) -> int | None:
    value = _get_rtvi_vllm_env(name, "") or ""
    if not value.strip():
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"Invalid value for {name}: '{value}' is not a valid integer") from exc
    if parsed < 0:
        raise ValueError(f"Invalid value for {name}: '{value}' must be greater than or equal to 0")
    return parsed


def _get_num_preprocess_workers() -> int:
    num_workers = _parse_int_env(
        "VLLM_NUM_PREPROCESS_WORKERS",
        _DEFAULT_VLLM_NUM_PREPROCESS_WORKERS,
    )
    if num_workers < 1:
        raise ValueError(
            f"Invalid value for VLLM_NUM_PREPROCESS_WORKERS: "
            f"'{num_workers}' must be greater than or equal to 1"
        )
    return num_workers


def _apply_kv_cache_dtype_override(
    engine_args_kwargs: dict[str, object],
    supported_params: set[str],
) -> bool:
    kv_cache_dtype = (_get_rtvi_vllm_env("VLLM_KV_CACHE_DTYPE", "") or "").strip()
    if not kv_cache_dtype:
        return False
    if "kv_cache_dtype" not in supported_params:
        logger.warning(
            "VLLM_KV_CACHE_DTYPE=%s ignored; installed vLLM does not support " "kv_cache_dtype",
            kv_cache_dtype,
        )
        return False

    engine_args_kwargs["kv_cache_dtype"] = kv_cache_dtype
    logger.info("VLLM KV cache dtype override: %s", kv_cache_dtype)
    return True


def _apply_attention_backend_override(
    engine_args_kwargs: dict[str, object],
    supported_params: set[str],
) -> bool:
    attention_backend = (_get_rtvi_vllm_env("VLLM_ATTENTION_BACKEND", "") or "").strip()
    if not attention_backend:
        return False
    if "attention_backend" not in supported_params:
        logger.warning(
            "VLLM_ATTENTION_BACKEND=%s ignored; installed vLLM does not support "
            "attention_backend",
            attention_backend,
        )
        return False

    engine_args_kwargs["attention_backend"] = attention_backend
    logger.info("VLLM attention backend override: %s", attention_backend)
    return True


def _parse_bool_env(name: str, default: bool) -> bool:
    value = _get_rtvi_vllm_env(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _parse_float_env(name: str, default: float) -> float:
    value = _get_rtvi_vllm_env(name, "") or ""
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
    if (_get_rtvi_vllm_env("VLLM_MM_PROCESSOR_CACHE_GB", "") or "").strip():
        return _parse_float_env("VLLM_MM_PROCESSOR_CACHE_GB", 1.0)
    return _parse_float_env("VLLM_MM_INPUT_CACHE_GIB", 1.0)


CPU_COPY_OTHER_THREAD = True


# Both the EA checkpoint (NemotronH_Nano_VL_V2) and the GA checkpoint
# (NemotronH_Nano_Omni_Reasoning_V3) share the same model executor and
# require identical special-casing throughout the inference path.
_NEMOTRON_OMNI_ARCHS = frozenset({"NemotronH_Nano_VL_V2", "NemotronH_Nano_Omni_Reasoning_V3"})


_QWEN35_ARCHS = frozenset(
    {
        "Qwen3_5ForConditionalGeneration",
        "Qwen3_5MoeForConditionalGeneration",
    }
)
_QWEN3VL_ARCHS = frozenset(
    {
        "Qwen3VLForConditionalGeneration",
        "Qwen3VLMoeForConditionalGeneration",
    }
)
_COSMOS3_DIFFUSERS_ARCHS = frozenset({"Cosmos3ForConditionalGeneration"})

# Synthetic source_fps for absolute-timestamp video metadata: 1000 makes
# round(t * fps) / fps reconstruct the real timestamp (ms resolution).
_ABSOLUTE_TIMESTAMP_SOURCE_FPS = 1000.0


def _is_evs_session_enabled() -> bool:
    return os.environ.get("VIA_EVS_SESSION", "").lower() in ("1", "true")


def _build_evs_sampling_kwargs(max_tokens, generation_config):
    """Build the kwargs for an EVS session's ``VideoSessionSamplingParams``.

    The EVS event detector triggers generations server-side with no per-call
    request body, so the session must carry the same sampling policy the
    non-EVS path applies. In particular ``VLLM_IGNORE_EOS`` (or a config-level
    ``ignore_eos``) must be forwarded, otherwise event-gated generations stop
    at the natural EOS instead of reaching the requested output length (the
    behavior seen as gen_tokens=2 vs max_tokens under perf/OSL runs). Mirrors
    the non-EVS sampling-params construction in ``generate``/``generate_stream``.

    ``ignore_eos`` and ``min_tokens`` are only added when set so omitting them
    preserves vLLM ``SamplingParams`` defaults after ``model_dump(exclude_none)``.
    """

    def _gc(name, default):
        if isinstance(generation_config, dict):
            return generation_config.get(name, default)
        return getattr(generation_config, name, default)

    kwargs = dict(
        max_tokens=max_tokens,
        temperature=float(_gc("temperature", 0.4)),
        top_p=float(_gc("top_p", 0.8)),
        top_k=int(_gc("top_k", 20)),
        repetition_penalty=float(_gc("repetition_penalty", 1.1)),
        seed=_gc("seed", 1),
    )

    env_ignore_eos = _get_rtvi_vllm_env("VLLM_IGNORE_EOS", "false").lower() == "true"
    cfg_ignore_eos = _gc("ignore_eos", None)
    if env_ignore_eos or cfg_ignore_eos is not None:
        kwargs["ignore_eos"] = env_ignore_eos or bool(cfg_ignore_eos)

    min_tokens = _gc("min_tokens", None)
    if min_tokens is not None:
        kwargs["min_tokens"] = int(min_tokens)

    return kwargs


# Absolute video metadata changes the temporal positions seen by the model. Keep
# it operator-controlled for non-EVS paths, but enable it internally for EVS so
# EVS++ keeps the same timestamp behavior as dev/aa/evs.
_VIDEO_METADATA_ABSOLUTE_TIMESTAMPS = os.getenv(
    "RTVI_VIDEO_METADATA_ABSOLUTE_TIMESTAMPS", ""
).lower() in ("1", "true")

# Common parameters
FACTOR = 28
MAX_PIXELS = 16384 * 2 * FACTOR * FACTOR
MIN_PIXELS = 4 * 2 * FACTOR * FACTOR

ADD_TIMESTAMP_TO_PROMPT = (
    os.environ.get("RTVI_ADD_TIMESTAMP_TO_VLM_PROMPT", "true").lower() == "true"
)

# Optional prefix/suffix overrides for the timestamp prompt injected before the
# user query. Supported placeholders: {timestamps}, {query}, {first_ts}, {last_ts}.
# Separate vars for file and RTSP sources. When unset, the legacy
# "These are images sampled from the same video at times ..." format is used.
# Applied to any vllm-compatible model (no per-model guard).
_TIMESTAMP_PROMPT_ALLOWED_PLACEHOLDERS = frozenset({"timestamps", "query", "first_ts", "last_ts"})


def _validate_timestamp_prompt_template(env_var: str, template: str) -> str:
    """Validate an operator-supplied timestamp prompt template at load time.

    Rejects templates with malformed braces or unknown placeholder names so a
    misconfigured env var fails once at startup instead of raising on every
    caption request inside ``str.format``. A rejected template is dropped
    (treated as unset) after logging a warning.
    """
    if not template:
        return template
    try:
        fields = [
            field_name
            for _, field_name, _, _ in string.Formatter().parse(template)
            if field_name is not None
        ]
    except ValueError as exc:
        logger.warning("Ignoring %s: malformed prompt template %r (%s)", env_var, template, exc)
        return ""
    unknown = [
        f
        for f in fields
        if (f.split(".")[0].split("[")[0] or "") not in _TIMESTAMP_PROMPT_ALLOWED_PLACEHOLDERS
    ]
    if unknown:
        logger.warning(
            "Ignoring %s: unknown placeholder(s) %s in template %r; allowed: %s",
            env_var,
            sorted(set(unknown)),
            template,
            sorted(_TIMESTAMP_PROMPT_ALLOWED_PLACEHOLDERS),
        )
        return ""
    return template


_TIMESTAMP_PROMPT_PREFIX_FILE = _validate_timestamp_prompt_template(
    "RTVI_TIMESTAMP_PROMPT_PREFIX_FILE_SOURCE",
    os.environ.get("RTVI_TIMESTAMP_PROMPT_PREFIX_FILE_SOURCE", "").strip("\"'"),
)
_TIMESTAMP_PROMPT_SUFFIX_FILE = _validate_timestamp_prompt_template(
    "RTVI_TIMESTAMP_PROMPT_SUFFIX_FILE_SOURCE",
    os.environ.get("RTVI_TIMESTAMP_PROMPT_SUFFIX_FILE_SOURCE", "").strip("\"'"),
)
_TIMESTAMP_PROMPT_PREFIX_RTSP = _validate_timestamp_prompt_template(
    "RTVI_TIMESTAMP_PROMPT_PREFIX_RTSP_SOURCE",
    os.environ.get("RTVI_TIMESTAMP_PROMPT_PREFIX_RTSP_SOURCE", "").strip("\"'"),
)
_TIMESTAMP_PROMPT_SUFFIX_RTSP = _validate_timestamp_prompt_template(
    "RTVI_TIMESTAMP_PROMPT_SUFFIX_RTSP_SOURCE",
    os.environ.get("RTVI_TIMESTAMP_PROMPT_SUFFIX_RTSP_SOURCE", "").strip("\"'"),
)

# EVS default timestamp-grounding instruction. This intentionally matches the
# dev/aa/evs default, but is applied only when VIA_EVS_SESSION is enabled and no
# operator prefix/suffix template is configured for the current source type.
_DEFAULT_EVS_TIMESTAMP_INSTRUCTION = (
    " IMPORTANT: Frame 1 corresponds to timestamp {first_ts} seconds, and the "
    "last frame corresponds to timestamp {last_ts} seconds. All timestamps in "
    "your response MUST be between {first_ts} and {last_ts} seconds. Do NOT use "
    "timestamps starting from 0. The video segment starts at {first_ts} seconds "
    "in the original video."
)


def _get_timestamp_prompt_templates(is_rtsp: bool, use_evs_default: bool = False):
    prefix_tpl = _TIMESTAMP_PROMPT_PREFIX_RTSP if is_rtsp else _TIMESTAMP_PROMPT_PREFIX_FILE
    suffix_tpl = _TIMESTAMP_PROMPT_SUFFIX_RTSP if is_rtsp else _TIMESTAMP_PROMPT_SUFFIX_FILE

    used_evs_default = False
    if use_evs_default and not prefix_tpl and not suffix_tpl:
        suffix_tpl = _DEFAULT_EVS_TIMESTAMP_INSTRUCTION
        used_evs_default = True

    return prefix_tpl, suffix_tpl, used_evs_default


DEFAULT_SYSTEM_PROMPT_CR1 = (
    "Please provide captions of all the events in the video with timestamps using the following format:"
    " <start time> <end time> caption of event 1.\n<start time> <end time> caption of event 2.\n"
    "At each frame, the timestamp is embedded at the bottom of the video. You need to extract"
    " the timestamp and answer the user question."
)


def start_loop(loop):
    asyncio.set_event_loop(loop)
    loop.run_forever()


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


# Canonical Qwen3-VL extra_special_tokens role mapping. Some Qwen3-VL-arch
# checkpoints (e.g. CR3 nano-reasoner modelopt-quantized FP8/NVFP4 builds)
# ship extra_special_tokens as a flat list of token strings, which transformers
# >=4.55 rejects with `AttributeError: 'list' object has no attribute 'keys'`
# in _set_model_specific_special_tokens. vLLM 0.17's new HF renderer
# (vllm/renderers/hf.py) trips this during AsyncLLMEngine init.
# Model vocabulary special tokens (not credentials). The `*_token` key names
# trip the secrets scanner's (secret|token) regex, so each entry carries an
# inline `guardrail-ignore` to silence the false positive.
_QWEN3VL_EXTRA_SPECIAL_TOKENS = {
    "image_token": "<|image_pad|>",  # guardrail-ignore: model vocab token, not a secret
    "video_token": "<|video_pad|>",  # guardrail-ignore: model vocab token, not a secret
    "vision_bos_token": "<|vision_start|>",  # guardrail-ignore: model vocab token, not a secret
    "vision_eos_token": "<|vision_end|>",  # guardrail-ignore: model vocab token, not a secret
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
        except Exception:
            logger.debug("Failed to get model architecture from config.json")

        if self._vlm_model_type == "cosmos-reason1":
            self._system_prompt = DEFAULT_SYSTEM_PROMPT_CR1
        else:
            self._system_prompt = ""

        if self._system_prompt:
            logger.info("VllmCompatible default system prompt: %s", self._system_prompt)

        if _is_evs_session_enabled():
            if not _VIDEO_METADATA_ABSOLUTE_TIMESTAMPS:
                logger.info(
                    "VIA_EVS_SESSION enabled: internally enabling "
                    "RTVI_VIDEO_METADATA_ABSOLUTE_TIMESTAMPS for EVS metadata"
                )
            if self._vlm_model_type != "cosmos-reason1" and ADD_TIMESTAMP_TO_PROMPT:
                _, _, file_uses_default = _get_timestamp_prompt_templates(
                    is_rtsp=False,
                    use_evs_default=True,
                )
                _, _, rtsp_uses_default = _get_timestamp_prompt_templates(
                    is_rtsp=True,
                    use_evs_default=True,
                )
                if file_uses_default or rtsp_uses_default:
                    logger.info(
                        "VIA_EVS_SESSION enabled: using dev/aa/evs-compatible "
                        "default EVS timestamp prompt where no RTVI_TIMESTAMP_PROMPT_* "
                        "override is configured"
                    )
                else:
                    logger.info(
                        "VIA_EVS_SESSION enabled: using configured "
                        "RTVI_TIMESTAMP_PROMPT_* templates for EVS timestamp prompt"
                    )

        # Initialize the actual model components
        logger.info("Using VLLM model for vllm-compatible")
        os.environ["VLLM_CACHE_ROOT"] = os.path.join(self.model_path, ".vllm")

        _sanitize_rtvi_vllm_env()
        _maybe_register_cosmos3_vllm_shim(self._model_architecture)

        from transformers import AutoProcessor
        from vllm.engine.arg_utils import AsyncEngineArgs
        from vllm.engine.async_llm_engine import AsyncLLMEngine

        self._num_time_tokens = 0
        self._model_name = "vllm-compatible"
        model_lock_path = self.model_path + "/.lock"
        with FileLock(model_lock_path):
            logger.info("Initializing VllmCompatible model from: %s", self.model_path)
            gpu_memory_utilization_env = _get_rtvi_vllm_env("VLLM_GPU_MEMORY_UTILIZATION", "0.7")
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
            max_num_batched_tokens_env = _get_rtvi_vllm_env("VLLM_MAX_NUM_BATCHED_TOKENS", "")
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

                _apply_kv_cache_dtype_override(
                    engine_args_kwargs,
                    _engine_supported_params,
                )
                _apply_attention_backend_override(
                    engine_args_kwargs,
                    _engine_supported_params,
                )

                if "enable_prefix_caching" in _engine_supported_params:
                    prefix_caching_env = _get_rtvi_vllm_env(
                        "VLLM_ENABLE_PREFIX_CACHING",
                        "true",
                    )
                    enable_prefix_caching = prefix_caching_env.lower() == "true"
                    engine_args_kwargs["enable_prefix_caching"] = enable_prefix_caching

                disable_mm_cache = _parse_bool_env(
                    "VLLM_DISABLE_MM_PREPROCESSOR_CACHE",
                    default=True,
                )
                if "disable_mm_preprocessor_cache" in _engine_supported_params:
                    engine_args_kwargs["disable_mm_preprocessor_cache"] = disable_mm_cache
                if "mm_processor_cache_gb" in _engine_supported_params:
                    engine_args_kwargs["mm_processor_cache_gb"] = _get_mm_processor_cache_gb()

                mm_tensor_ipc = (_get_rtvi_vllm_env("VLLM_MM_TENSOR_IPC", "") or "").strip()
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

                multimodal_tensor_ipc = (
                    _get_rtvi_vllm_env("VLLM_MULTIMODAL_TENSOR_IPC", "") or ""
                ).strip()
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

                mm_encoder_attn_backend = (
                    _get_rtvi_vllm_env("VLLM_MM_ENCODER_ATTN_BACKEND", "") or ""
                ).strip()
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
                    enforce_eager = (
                        _get_rtvi_vllm_env("VLLM_ENFORCE_EAGER", "false").lower() == "true"
                    )
                    engine_args_kwargs["enforce_eager"] = enforce_eager
                    if enforce_eager:
                        logger.info("VLLM enforce_eager enabled via VLLM_ENFORCE_EAGER")

                vlm_trust_remote_code = _get_vlm_trust_remote_code(self._model_architecture)
                if "trust_remote_code" in _engine_supported_params:
                    engine_args_kwargs["trust_remote_code"] = vlm_trust_remote_code

                if max_num_batched_tokens is not None:
                    engine_args_kwargs["max_num_batched_tokens"] = max_num_batched_tokens
                else:
                    engine_args_kwargs["max_num_batched_tokens"] = engine_args_kwargs[
                        "max_model_len"
                    ]

                moe_backend = (_get_rtvi_vllm_env("VLLM_MOE_BACKEND", "") or "").strip()
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

                # EVS extra engine args (similarity threshold, mm-embeds passthrough,
                # preprocess worker pool). Gated on the engine actually supporting them.
                # Similarity-based (content-dependent) pruning only applies on
                # the EVS session/encode path, so gate it on VIA_EVS_SESSION.
                # When session mode is on: use VLLM_EVS_SIMILARITY_THRESHOLD if
                # provided, else default 0.2. When session mode is off: leave
                # evs_similarity_threshold=None so the engine uses deterministic
                # fixed-rate pruning (video_pruning_rate) instead.
                _evs_session_on = os.environ.get("VIA_EVS_SESSION", "").lower() in ("true", "1")
                if self._model_architecture in _NEMOTRON_OMNI_ARCHS and _evs_session_on:
                    logger.error(
                        "EVS session mode is not supported for NemotronH_Nano_Omni_Reasoning_V3"
                    )
                    raise ValueError(
                        "EVS session mode is not supported for NemotronH_Nano_Omni_Reasoning_V3"
                    )
                if "evs_similarity_threshold" in _engine_supported_params and _evs_session_on:
                    engine_args_kwargs["evs_similarity_threshold"] = float(
                        os.environ.get("VLLM_EVS_SIMILARITY_THRESHOLD") or "0.2"
                    )
                if "enable_mm_embeds" in _engine_supported_params:
                    engine_args_kwargs["enable_mm_embeds"] = True
                if "num_preprocess_workers" in _engine_supported_params:
                    engine_args_kwargs["num_preprocess_workers"] = _get_num_preprocess_workers()

                engine_args = AsyncEngineArgs(**engine_args_kwargs)
                self._llm = AsyncLLMEngine.from_engine_args(engine_args)
                self._processor = AutoProcessor.from_pretrained(
                    self.model_path, trust_remote_code=vlm_trust_remote_code
                )
            except Exception as e:
                logger.error("Error initializing VLLM model: %s", e)
                if _get_rtvi_vllm_env("VLLM_ENFORCE_EAGER", "false").lower() != "true":
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

            # EVS session handler — uses vLLM's serving layer directly (no HTTP)
            self._evs_handler = None  # OpenAIServingVideoSessions, created lazily
            # Per-stream EVS sessions: streamId → session_id
            self._evs_sessions: dict[str, str] = {}
            self._evs_sessions_lock = threading.Lock()
            self._output_tpool = concurrent.futures.ThreadPoolExecutor(
                max_workers=self._max_batch_size
            )

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

    # --- EVS session helpers ---

    def _ensure_evs_handler(self):
        """Lazily create the EVS session handler."""
        if self._evs_handler is None:
            from vllm.entrypoints.openai.serving_video_sessions import (
                OpenAIServingVideoSessions,
            )

            self._evs_handler = OpenAIServingVideoSessions(
                engine_client=self._llm,
                max_sessions=int(os.environ.get("VIA_EVS_MAX_SESSIONS") or "100"),
                pruning_rate=float(os.environ.get("VLM_VIDEO_PRUNING_RATE") or "0.5"),
                similarity_threshold=float(
                    os.environ.get("VLLM_EVS_SIMILARITY_THRESHOLD") or "0.2"
                ),
                pd_server_url=os.environ.get("VIA_PD_SERVER_URL") or None,
                pd_server_timeout_s=float(os.environ.get("VIA_PD_SERVER_TIMEOUT_S") or "120.0"),
            )
            if os.environ.get("VIA_EVS_STREAMING_PREFILL", "").lower() in ("true", "1"):
                self._evs_handler.streaming_prefill = True
        return self._evs_handler

    def _ensure_evs_session(
        self,
        stream_id,
        prompt="",
        max_tokens=2048,
        chunk_size_s=0,
        timestamp_prompt_template=None,
        generation_config=None,
    ):
        """Return (or create) the EVS session for *stream_id*."""
        handler = self._ensure_evs_handler()
        with self._evs_sessions_lock:
            if stream_id in self._evs_sessions:
                return self._evs_sessions[stream_id]

        from vllm.entrypoints.openai.engine.protocol import (
            EvsAdvancedConfig,
            VideoSessionCreateRequest,
            VideoSessionSamplingParams,
        )

        token_budget = int(os.environ.get("VIA_EVS_TOKEN_BUDGET") or "20000")
        model_name = self._vlm_model_type or self.model_dir_name

        event_only = os.environ.get("VIA_EVS_EVENT_ONLY", "true").lower() in ("true", "1")

        event_chunk_duration_s = float(chunk_size_s) if chunk_size_s > 0 else 0.0
        event_ema_memory_s = float(os.environ.get("VIA_EVS_EMA_MEMORY_S") or "0")
        event_spike_std_k = float(os.environ.get("VIA_EVS_SPIKE_STD_K") or "2.0")
        event_settling_std_k = float(os.environ.get("VIA_EVS_SETTLING_STD_K") or "1.5")
        event_std_floor_ratio = float(os.environ.get("VIA_EVS_STD_FLOOR_RATIO") or "0.1")
        event_min_clips = int(os.environ.get("VIA_EVS_MIN_CLIPS") or "3")
        # Later chunks a present chunk waits for before its decision (and idle
        # discard) commits. Buys slack for out-of-order arrivals; 1 suits
        # strictly in-order delivery, where the extra wait buys nothing.
        event_decision_lag = int(os.environ.get("VIA_EVS_DECISION_LAG") or "3")
        # Baseline floor below which downward ("comes to rest") detection is
        # suppressed. Auto-disables downward on static-camera streams (low
        # baseline) while keeping it for moving cameras (high baseline). Set
        # to 0.0 to always allow downward.
        event_downward_baseline_min = float(
            os.environ.get("VIA_EVS_DOWNWARD_BASELINE_MIN") or "0.10"
        )

        # Default sampling policy for this session's event-gated generations.
        # The detector triggers generation server-side with no per-call request
        # body, so the session must carry the same sampling knobs the non-EVS
        # path applies (defaults mirror VlmGenerationConfig). Deliberate
        # differences from the non-EVS path:
        #   - temperature is ALWAYS sent: on the session schema an omitted
        #     temperature defaults to 1.0 (stochastic), so temperature=0 must be
        #     sent as 0.0 to actually get greedy decoding (the inverse of the
        #     non-EVS "omit-when-zero" idiom).
        #   - seed is forwarded into the session: a server-side session is not
        #     affected by the process-global RNG reseed the non-EVS path does.
        #   - ignore_eos / min_tokens are forwarded (see _build_evs_sampling_kwargs)
        #     so VLLM_IGNORE_EOS reaches event-gated generations for OSL/perf runs.
        session_sampling_params = VideoSessionSamplingParams(
            **_build_evs_sampling_kwargs(max_tokens, generation_config)
        )

        request = VideoSessionCreateRequest(
            model=model_name,
            token_budget=token_budget,
            event_only=event_only,
            prompt=prompt,
            timestamp_prompt_template=timestamp_prompt_template,
            sampling_params=session_sampling_params,
            event_chunk_duration_s=(event_chunk_duration_s if event_chunk_duration_s > 0 else None),
            event_ema_memory_s=(event_ema_memory_s if event_ema_memory_s > 0 else None),
            event_advanced=EvsAdvancedConfig(
                spike_std_k=event_spike_std_k,
                settling_std_k=event_settling_std_k,
                std_floor_ratio=event_std_floor_ratio,
                min_clips=event_min_clips,
                downward_baseline_min=event_downward_baseline_min,
                decision_lag=event_decision_lag,
            ),
        )

        async def _create():
            return await handler.create_session(request)

        resp = asyncio.run_coroutine_threadsafe(_create(), self._event_loop).result()
        with self._evs_sessions_lock:
            self._evs_sessions[stream_id] = resp.session_id
        logger.info(
            "EVS session created: %s for stream %s (budget=%d, "
            "event_only=%s, chunk_dur=%.2fs, ema_memory=%.2fs, "
            "spike_k=%.2f, settling_k=%.2f, std_floor_ratio=%.4f, "
            "min_clips=%d, downward_baseline_min=%.4f, decision_lag=%d)",
            resp.session_id,
            stream_id,
            token_budget,
            event_only,
            event_chunk_duration_s,
            event_ema_memory_s,
            event_spike_std_k,
            event_settling_std_k,
            event_std_floor_ratio,
            event_min_clips,
            event_downward_baseline_min,
            event_decision_lag,
        )
        logger.info(
            "EVS session %s sampling: temp=%.2f top_p=%.2f top_k=%d " "rep_pen=%.2f seed=%s",
            resp.session_id,
            session_sampling_params.temperature,
            session_sampling_params.top_p,
            session_sampling_params.top_k,
            session_sampling_params.repetition_penalty,
            session_sampling_params.seed,
        )
        return resp.session_id

    def _close_evs_session(self, stream_id):
        """Delete the EVS session for a single stream."""
        with self._evs_sessions_lock:
            session_id = self._evs_sessions.pop(stream_id, None)
        if session_id is not None and self._evs_handler is not None:

            async def _delete():
                await self._evs_handler.delete_session(session_id)

            asyncio.run_coroutine_threadsafe(_delete(), self._event_loop).result()
            logger.info("EVS session closed: %s (stream %s)", session_id, stream_id)

    def close_evs_session(self):
        """Delete all EVS sessions."""
        with self._evs_sessions_lock:
            stream_ids = list(self._evs_sessions.keys())
        for sid in stream_ids:
            self._close_evs_session(sid)

    def _evs_strip_thinking_tags(self, text: str) -> tuple:
        """Extract reasoning from <think>...</think>, return (content, reasoning).

        Mirrors models.common.utils.strip_thinking_tags used by via-engine; the
        rtvi target does not import that helper, so we inline a minimal version
        consistent with the regex pattern used in _postprocess_vllm above.
        """
        if not text:
            return text or "", ""
        match = re.search(r"<think>(.*?)</think>", text, flags=re.DOTALL)
        reasoning = match.group(1) if match else ""
        if reasoning:
            text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
        else:
            text, reasoning = self._remove_orphan_think_tags(text, reasoning)
        for tag in ["<answer>", "</answer>", "<summary>", "</summary>"]:
            text = text.replace(tag, "")
        return text.strip(), reasoning

    def _uses_absolute_timestamp_metadata(self) -> bool:
        """Whether absolute frame timestamps should be encoded in video metadata."""
        return _VIDEO_METADATA_ABSOLUTE_TIMESTAMPS or _is_evs_session_enabled()

    def _generate_evs_session(
        self,
        prompt,
        images,
        generation_config,
        video_frames_times,
        chunk,
        timestamp_prompt_template=None,
    ):
        """EVS session flow: add clip as tensors, auto-generate when ready.

        Returns a concurrent.futures.Future so the VLM pipeline dispatcher
        thread can proceed to dequeue the next chunk without blocking. Up to
        max_batch_size add_clip_tensors coroutines run on the shared event
        loop concurrently, which is what lets vLLM actually batch across
        clips instead of processing them one at a time.
        """
        stream_id = getattr(chunk, "streamId", None) if chunk else None
        if not stream_id:
            stream_id = "__default__"

        # generation_config may be either a dict (via-engine convention) or a
        # VlmGenerationConfig dataclass (rtvi convention). Support both.
        if isinstance(generation_config, dict):
            max_tokens = generation_config.get("max_new_tokens", 2048)
        else:
            max_tokens = getattr(generation_config, "max_new_tokens", 2048)
        chunk_size_s = 0
        if chunk and chunk.start_pts >= 0 and chunk.end_pts > chunk.start_pts:
            chunk_size_s = (chunk.end_pts - chunk.start_pts) / 1e9
        session_id = self._ensure_evs_session(
            stream_id,
            prompt=prompt,
            max_tokens=max_tokens,
            chunk_size_s=chunk_size_s,
            timestamp_prompt_template=timestamp_prompt_template,
            generation_config=generation_config,
        )
        handler = self._evs_handler
        placeholder = [
            VlmModelOutput(output="", input_tokens=0, output_tokens=0, reasoning_description="")
        ]

        # Prepare images (same transforms as regular path)
        if self._vlm_model_type == "cosmos-reason1":
            images = self.overlay_frame_number_cr1(images, video_frames_times)
            images = self.smart_resize_tensor(images)
            images = images.half()
        else:
            # cosmos-reason2 / Qwen3-VL: keep frames in their native
            # (T, H, W, C) layout and hand the HF processor numpy (see the CPU
            # handoff below). This mirrors the non-EVS video path, which
            # converts raw RTVI frames to numpy "so the model processor handles
            # layout and resizing normally". The previous permute to NCHW +
            # torch tensor made Qwen3-VL compute a degenerate video_grid_thw
            # (0 video tokens -> "0 prompt placeholders" crash at startup).
            pass

        # Build video metadata. Qwen3-VL's HF processor expects
        # frames_indices/fps for video inputs; keep absolute timestamps
        # operator-controlled, but still provide relative metadata by default.
        video_metadata = {}
        if len(images) > 1 and video_frames_times and len(video_frames_times) > 1:
            duration = video_frames_times[-1] - video_frames_times[0]
            if self._uses_absolute_timestamp_metadata():
                video_metadata = self._build_absolute_timestamp_video_metadata(
                    images, video_frames_times, duration
                )
            else:
                fps = 1
                if duration > 0:
                    fps = (len(video_frames_times) - 1) / duration
                video_metadata = {
                    "total_num_frames": len(images),
                    "frames_indices": list(range(len(images))),
                    "fps": fps,
                    "duration": duration,
                }

        is_last = getattr(chunk, "is_last", False) if chunk else False
        client_timestamps = [float(t) for t in video_frames_times] if video_frames_times else []

        ooo_chunk_id = getattr(chunk, "chunkIdx", None) if chunk is not None else None

        if len(images) < 3 and not is_last:
            logger.info("EVS skipping single-frame clip")
            done = concurrent.futures.Future()
            done.set_result(placeholder)
            return done

        # Move to CPU once on the dispatcher thread before handing off.
        # cosmos-reason1 keeps its torch (half) tensor path; all other models
        # (Qwen3-VL / cosmos-reason2) enter vLLM as numpy in native NHWC, which
        # is what the non-EVS video path does so the processor infers channel
        # layout and resizes normally (avoids a collapsed grid -> 0 video
        # tokens).
        if self._vlm_model_type == "cosmos-reason1":
            images_cpu = images.cpu()
        else:
            images_cpu = images.cpu().numpy()

        # Track in-flight count so _is_busy() gates at max_batch_size.
        request_id = str(uuid.uuid4())
        self._inflight_req_ids.append(request_id)

        def _run_evs_clip():
            inflight_released = False

            def _release_inflight():
                nonlocal inflight_released
                if not inflight_released and request_id in self._inflight_req_ids:
                    self._inflight_req_ids.remove(request_id)
                    inflight_released = True

            async def _add():
                return await handler.add_clip_tensors(
                    session_id=session_id,
                    images=images_cpu,
                    metadata=video_metadata,
                    mm_processor_kwargs={
                        "size": {"longest_edge": 180000000, "shortest_edge": 4096},
                        "do_sample_frames": False,
                    },
                    timestamps=client_timestamps,
                    is_last=is_last,
                    chunk_id=ooo_chunk_id,
                    on_encode_done=_release_inflight,
                )

            try:
                clip_resp = asyncio.run_coroutine_threadsafe(_add(), self._event_loop).result()
            except Exception as e:
                logger.error("EVS add_clip_tensors failed: %r\n%s", e, traceback.format_exc())
                return placeholder
            finally:
                _release_inflight()

            logger.debug(
                "EVS clip: tokens=%d/%d, kept=%d, dropped=%d%s",
                clip_resp.tokens_used,
                clip_resp.tokens_used + clip_resp.tokens_remaining,
                clip_resp.frames_kept,
                clip_resp.frames_dropped,
                ", GENERATED" if clip_resp.generated else "",
            )

            if clip_resp.generated:
                content = clip_resp.response_text or ""
                content, reasoning = self._evs_strip_thinking_tags(content)
                usage = clip_resp.response_usage or {}

                # Log which time range the response actually covers
                # (may differ from the current chunk that triggered generation).
                if clip_resp.round_timestamps:
                    ts = clip_resp.round_timestamps
                    logger.debug(
                        "EVS response covers timestamps: %.2f-%.2f " "(%d entries), response: %s",
                        ts[0],
                        ts[-1],
                        len(ts),
                        content,
                    )

                # NOTE: rtvi's VlmModelOutput has no evs_round_timestamps field;
                # we log it above instead of returning it.
                return [
                    VlmModelOutput(
                        output=content,
                        input_tokens=usage.get("prompt_tokens", 0),
                        output_tokens=usage.get("completion_tokens", 0),
                        reasoning_description=reasoning,
                    )
                ]

            return placeholder

        return self._output_tpool.submit(_run_evs_clip)

    def _shutdown_model(self):
        try:
            self.close_evs_session()
        except Exception:
            logger.debug("Error closing EVS sessions during shutdown", exc_info=True)
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

    def _build_absolute_timestamp_video_metadata(self, images, video_frames_times, duration):
        """Build video metadata with absolute timestamps encoded in frames_indices.

        Uses a synthetic fps=1000 so frame_index/fps recovers the real timestamp
        (ms resolution) instead of a 0-based chunk-relative offset. Preserves
        absolute start offset and non-uniform frame spacing.
        """
        return {
            "total_num_frames": len(images),
            "frames_indices": [
                int(round(float(t) * _ABSOLUTE_TIMESTAMP_SOURCE_FPS)) for t in video_frames_times
            ],
            "fps": _ABSOLUTE_TIMESTAMP_SOURCE_FPS,
            "duration": duration,
        }

    def _apply_timestamp_prompt(self, query_text, chunk, video_frames_times):
        """Prepend/append the timestamp prompt to the user query.

        Shared by the regular generate path and the EVS session path so both
        inject an identical, env-configurable timestamp prompt
        (``RTVI_ADD_TIMESTAMP_TO_VLM_PROMPT`` plus the optional
        ``RTVI_TIMESTAMP_PROMPT_*`` templates). Returns ``query_text``
        unchanged when disabled or when chunk / frame-time data is missing.

        NOTE (EVS session path): the session prompt is fixed at session
        creation, so the injected timestamps reflect the *first* clip's frame
        times, not the full merged range seen at generate time.
        """
        add_timestamp_to_prompt = (
            self._vlm_model_type != "cosmos-reason1" and ADD_TIMESTAMP_TO_PROMPT
        )
        if not (add_timestamp_to_prompt and chunk and video_frames_times):
            return query_text

        is_rtsp = chunk.file.startswith("rtsp://")
        prefix_tpl = _TIMESTAMP_PROMPT_PREFIX_RTSP if is_rtsp else _TIMESTAMP_PROMPT_PREFIX_FILE
        suffix_tpl = _TIMESTAMP_PROMPT_SUFFIX_RTSP if is_rtsp else _TIMESTAMP_PROMPT_SUFFIX_FILE

        first_ts = chunk.get_timestamp(video_frames_times[0])
        last_ts = chunk.get_timestamp(video_frames_times[-1])

        if prefix_tpl or suffix_tpl:
            string_of_times = " ".join(chunk.get_timestamp(t) for t in video_frames_times)
            fmt_kwargs = dict(
                timestamps=string_of_times,
                query=query_text,
                first_ts=first_ts,
                last_ts=last_ts,
            )
            parts = []
            if prefix_tpl:
                parts.append(prefix_tpl.format(**fmt_kwargs))
            parts.append(query_text)
            if suffix_tpl:
                parts.append(suffix_tpl.format(**fmt_kwargs))
            return "\n".join(parts)

        # Preserve the established non-EVS prompt when no operator override is
        # supplied. EVS deliberately does not reuse this per-clip wording: an
        # EVS event can merge several clips and needs an explicit template whose
        # timestamps are filled only after the full event range is known.
        string_of_times = "".join(
            chunk.get_timestamp(frame_time) + " " for frame_time in video_frames_times
        )
        return (
            "These are images sampled from the same video at times "
            + string_of_times
            + ". "
            + query_text
        )

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

        # Route to EVS session mode if configured. EVS owns its own prompt
        # construction / mm-data path and returns a concurrent.futures.Future
        # so the caller's contract (Future[List[VlmModelOutput]]) is preserved.
        # NOTE: video_frames[0] is the first batch entry (rtvi wraps frames in
        # an outer list); matches what the non-EVS path uses for `images`.
        if os.environ.get("VIA_EVS_SESSION", "").lower() in ("true", "1"):
            # EVS spans multiple clips, so pass the selected source's template
            # unfilled. vLLM fills timestamps from the complete merged event
            # range. With no prefix/suffix override, EVS uses the dev/aa/evs
            # timestamp instruction while non-EVS profiles keep their defaults.
            want_ts = self._vlm_model_type != "cosmos-reason1" and ADD_TIMESTAMP_TO_PROMPT
            ts_template = None
            if want_ts and chunk:
                is_rtsp = chunk.file.startswith("rtsp://")
                prefix_tpl, suffix_tpl, used_evs_default = _get_timestamp_prompt_templates(
                    is_rtsp,
                    use_evs_default=True,
                )
                if prefix_tpl or suffix_tpl:
                    if used_evs_default:
                        ts_template = "{query}" + suffix_tpl
                    else:
                        template_fields = {
                            field_name.split(".")[0].split("[")[0]
                            for template in (prefix_tpl, suffix_tpl)
                            for _, field_name, _, _ in string.Formatter().parse(template)
                            if field_name is not None
                        }
                        template_parts = []
                        if prefix_tpl:
                            template_parts.append(prefix_tpl)
                        if "query" not in template_fields:
                            template_parts.append("{query}")
                        if suffix_tpl:
                            template_parts.append(suffix_tpl)
                        ts_template = "\n".join(template_parts)
            # Pass the FULL generation config (not just max_new_tokens) so the
            # session can apply the configured sampling params to its
            # event-gated generations; see _ensure_evs_session.
            return self._generate_evs_session(
                query_text,
                video_frames[0],
                config,
                video_frames_times,
                chunk,
                timestamp_prompt_template=ts_template,
            )

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
        max_frames_env = _get_rtvi_vllm_env(
            "VLLM_MM_PROCESSOR_VIDEO_NUM_FRAMES",
            "256",
        )
        max_frames = int(max_frames_env or "256")
        if len(images) > 1 and len(images) > max_frames:
            indices = torch.linspace(0, len(images) - 1, max_frames).long()
            images = images[indices]
            video_frames_times = [video_frames_times[i] for i in indices.tolist()]
            logger.info(
                "VLM generate: subsampled %d frames to %d (VLLM_MM_PROCESSOR_VIDEO_NUM_FRAMES)",
                len(video_frames[0]),
                max_frames,
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

        query_text = self._apply_timestamp_prompt(query_text, chunk, video_frames_times)

        # VLLM model generation

        is_single_image = len(images) == 1

        if is_single_image:
            input = (images if CPU_COPY_OTHER_THREAD else images.cpu().numpy(),)
        else:
            duration = video_frames_times[-1] - video_frames_times[0]

            if self._uses_absolute_timestamp_metadata():
                video_metadata = self._build_absolute_timestamp_video_metadata(
                    images, video_frames_times, duration
                )
            else:
                fps = 1
                if len(video_frames_times) > 1 and duration > 0:
                    fps = (len(video_frames_times) - 1) / duration
                video_metadata = {
                    "total_num_frames": len(images),
                    "frames_indices": list(range(len(images))),
                    "fps": fps,
                    "duration": duration,
                }

            input = (
                images if CPU_COPY_OTHER_THREAD else images.cpu().numpy(),
                video_metadata,
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
            if (
                self._vlm_model_type in ("cosmos-reason2", "cosmos-reason3")
                or self._model_architecture in _QWEN3VL_ARCHS
            ):
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
        env_ignore_eos = _get_rtvi_vllm_env("VLLM_IGNORE_EOS", "false").lower() == "true"
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
        env_ignore_eos = _get_rtvi_vllm_env("VLLM_IGNORE_EOS", "false").lower() == "true"
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
        env_ignore_eos = _get_rtvi_vllm_env("VLLM_IGNORE_EOS", "false").lower() == "true"
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
