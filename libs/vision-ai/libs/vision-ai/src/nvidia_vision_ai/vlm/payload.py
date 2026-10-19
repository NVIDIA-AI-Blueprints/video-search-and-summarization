# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Backend-specific chat-completion bodies.

Ported from ``vss_cli.vlm.group`` (video-search-and-summarization, develop
@ e43ae475e6). Internal, not exported: a :class:`VlmRequest` plus the media
URL to embed in, a JSON-ready dict out.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from .models import VlmRequest

_LOG = logging.getLogger(__name__)

#: Qwen3-VL video processor's default clip floor (128 * 32 * 32). The HF
#: processor rejects a ``size`` missing either edge, so the floor is always sent
#: alongside ``longest_edge``.
_QWEN3_VL_MIN_CLIP_PIXELS = 128 * 32 * 32

_SAMPLING_FIELDS = ("fps", "max_frames", "total_pixels")


def build_payload(request: VlmRequest, media_url: str) -> dict[str, Any]:
    """Build the request body for ``request.backend`` with ``media_url`` as the video."""
    if request.backend in {"rt_vlm", "cosmos_reason_nim"}:
        return _rt_vlm(request, media_url)
    if request.backend == "vllm":
        return _vllm(request, media_url)
    return _openai(request, media_url)


def _base(request: VlmRequest, media_url: str) -> dict[str, Any]:
    """Fields shared by every backend."""
    body: dict[str, Any] = {
        "model": request.model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "video_url", "video_url": {"url": media_url}},
                    {"type": "text", "text": request.prompt},
                ],
            }
        ],
    }
    if request.temperature is not None:
        body["temperature"] = request.temperature
    if request.max_tokens is not None:
        body["max_tokens"] = request.max_tokens
    if request.seed is not None:
        body["seed"] = request.seed
    return body


def _video_io(request: VlmRequest, *, qwen3_loader_cap: bool = False) -> dict[str, Any]:
    """``media_io_kwargs.video`` for the requested sampling; empty means server default.

    ``num_frames`` is the cap for vLLM's uniform loader and the fixed count for
    RT-VLM and NIM. vLLM's ``qwen3_vl`` loader ignores ``num_frames`` and caps
    with ``max_frames`` instead, so vLLM gets both. RT-VLM and NIM reject
    ``fps`` with ``num_frames`` (HTTP 400), so with ``fps`` set they get ``fps``
    alone and their deployment-wide frame cap applies.

    vLLM's ``VideoMediaIO`` hands its loader ``num_frames=32`` unless the
    request sends one, so ``fps`` alone would stop at 32 frames on the uniform
    loader. With ``fps`` and no ``max_frames``, vLLM gets ``num_frames: -1`` so
    the rate decides.
    """
    video: dict[str, Any] = {}
    if request.fps is not None:
        video["fps"] = request.fps
        if qwen3_loader_cap and request.max_frames is None:
            video["num_frames"] = -1
    if request.max_frames is not None:
        if qwen3_loader_cap:
            video["num_frames"] = request.max_frames
            video["max_frames"] = request.max_frames
        elif request.fps is None:
            video["num_frames"] = request.max_frames
        else:
            _LOG.warning(
                "max_frames %s not sent: this backend takes fps or a frame count, not both; "
                "its deployment frame cap applies",
                request.max_frames,
            )
    return video


def _processor_kwargs(request: VlmRequest) -> dict[str, Any]:
    """``mm_processor_kwargs`` for the pixel budget; empty means server default."""
    if request.total_pixels is None:
        return {}
    return {
        "size": {
            "shortest_edge": min(_QWEN3_VL_MIN_CLIP_PIXELS, request.total_pixels),
            "longest_edge": request.total_pixels,
        }
    }


def _rt_vlm(request: VlmRequest, media_url: str) -> dict[str, Any]:
    """RT-VLM maps ``media_io_kwargs.video`` onto its own frame selector.

    Cosmos Reason NIM uses the same schema for now. Upstream marks this alpha:
    the NIM's own request schema has not been verified against these fields.
    """
    if request.backend == "cosmos_reason_nim":
        _LOG.warning(
            "Cosmos Reason NIM backend support is alpha; request construction "
            "currently uses the RT-VLM request schema and is pending refinement."
        )
    body = _base(request, media_url)
    video = _video_io(request)
    if video:
        body["media_io_kwargs"] = {"video": video}
    if request.enable_reasoning is not None:
        body["enable_reasoning"] = request.enable_reasoning
    if request.chunk_duration is not None:
        body["chunk_duration"] = request.chunk_duration
    processor = _processor_kwargs(request)
    if processor:
        body["mm_processor_kwargs"] = processor
    return body


def _vllm(request: VlmRequest, media_url: str) -> dict[str, Any]:
    """vLLM's video loader samples; Qwen then consumes that selection unchanged."""
    body = _base(request, media_url)
    if request.enable_reasoning is not None:
        body["chat_template_kwargs"] = {"enable_thinking": request.enable_reasoning}
    processor: dict[str, Any] = {}
    video = _video_io(request, qwen3_loader_cap=True)
    if video:
        body["media_io_kwargs"] = {"video": video}
        # Stops the processor re-sampling frames the loader already selected.
        processor["do_sample_frames"] = False
    processor.update(_processor_kwargs(request))
    if processor:
        body["mm_processor_kwargs"] = processor
    return body


def _openai(request: VlmRequest, media_url: str) -> dict[str, Any]:
    """A plain OpenAI chat completion: no engine-specific fields (e.g. Inference Hub)."""
    ignored = [name for name in (*_SAMPLING_FIELDS, "enable_reasoning") if getattr(request, name) is not None]
    if ignored:
        _LOG.warning(
            "%s not sent: the openai backend sends a plain chat completion, so the endpoint's defaults apply",
            ", ".join(ignored),
        )
    return _base(request, media_url)
