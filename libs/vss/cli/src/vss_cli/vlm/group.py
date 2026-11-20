# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""``vss vlm`` on the fixed verb grammar.

Visual question-answering (VQA) over video in a single bounded synchronous
call: ask a question, get an answer, persist the result to unified memory.

Unlike ``vss summarize``, ``vss vlm run`` is a **point call** (SDD §3.3). The
lifecycle record is written once with a terminal status; there is no
``submitted`` intermediate. One call produces one answer -- never routes to the
summarize pipeline and never re-enters a long-running job.

Media reaches the VLM one of two ways (VLM-1 / VLM-2):

* **Path B** (default): ``--sensor <name> [--start-time T --end-time T]``.
  VIOS resolves the sensor to a clip URL and the VLM receives that URL.
  ``--use-base64`` downloads the clip and sends its bytes inline instead;
  loopback URLs use this automatically.
* **Path A** (escape hatch): ``--media-url <url>``. A pre-resolved handle --
  any HTTP/HTTPS URL -- is sent directly as ``video_url``. Add ``--use-base64``
  to read a local file and send it as base64-encoded bytes instead.

The VLM endpoint is the deployment's ``rt_vlm`` service (discovered by
``vss configure``), called via the OpenAI-compatible ``/v1/chat/completions``
API. ``vss configure --base-url`` accepts a VSS ingress or a bare VLM endpoint
(vLLM, NIM, RT-VLM, Inference Hub); a bare endpoint has no VIOS, so
``--sensor`` is unavailable there. ``VSS_VLM_API_KEY``, when set, is sent as a
Bearer token. The model is ``--model``, else the configured one, else the
endpoint's only listed model.

Intent (VLM-6) classifies what this call is for: ``qa`` (default), ``critic``,
``report``, or ``introspection``. Stored in ``output.ext.intent`` and available
to harness routing (NFR-5) without the CLI implementing the routing itself.

Persistence (VLM-5) stores ``output.answer``, ``output.ext`` (model, intent,
completion_id) and ``output.handles.media_urls``. Opt out with ``--no-persist``.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
from datetime import UTC
from email.utils import parsedate_to_datetime
import json as _json_mod
import logging
import math
import os
import secrets
import tempfile
import time
from typing import TYPE_CHECKING
from typing import Any
from typing import ClassVar
from typing import Literal
import urllib.parse

import click
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import ValidationError
from pydantic import model_validator

from vss_cli import config as config_mod
from vss_cli import memory as memory_mod
from vss_cli import params as params_mod
from vss_cli.exits import Exit
from vss_cli.group import CommandGroup
from vss_cli.group import Context
from vss_cli.group import InvalidInput
from vss_cli.group import Result

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from collections.abc import Sequence

    import httpx

    from vss_core.memory.models import MemoryInput

_JOB_DOMAIN = "vlm"
_CROCKFORD32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_COMPLETIONS_PATH = "/v1/chat/completions"
_LOG = logging.getLogger(__name__)


def _ulid() -> str:
    value = (int(time.time() * 1000) & ((1 << 48) - 1)) << 80 | secrets.randbits(80)
    return "".join(_CROCKFORD32[(value >> shift) & 0x1F] for shift in range(125, -1, -5))


def _mint_job_id() -> str:
    return f"{_JOB_DOMAIN}-{_ulid()}"


def _default_model(deployment: config_mod.Deployment) -> str:
    """The one model the VLM endpoint reports serving, or a ConfigError.

    An endpoint listing several (Inference Hub lists its whole catalog) has no
    defensible default, so the caller chooses rather than getting the first.
    """
    service = deployment.services.get("rt_vlm")
    models = service.models if service else []
    choose = f"Pass --model, run `vss configure vlm --model <id>`, or export {config_mod.VLM_ENV['model']}."
    if len(models) == 1:
        return models[0]
    if models:
        shown = ", ".join(models[:10]) + (", ..." if len(models) > 10 else "")
        raise config_mod.ConfigError(
            f"the VLM endpoint at {deployment.base_url} lists {len(models)} models ({shown}). {choose}"
        )
    raise config_mod.ConfigError(
        f"deployment at {deployment.base_url} reports no VLM model, so --model cannot be defaulted. "
        f"{choose} Or re-run `vss configure --base-url {deployment.base_url}`."
    )


def _vlm_headers() -> dict[str, str]:
    """Request headers, with ``VSS_VLM_API_KEY`` as a Bearer token when set."""
    headers = {"Content-Type": "application/json"}
    key = config_mod.vlm_api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def _is_loopback_url(url: str) -> bool:
    """True when the URL's host is a loopback address that a remote service cannot reach.

    VIOS on Docker resolves clip URLs using the HAProxy-facing hostname, which is
    often ``localhost`` or ``127.0.0.1`` from the CLI's perspective.  That URL is
    reachable from the CLI host but not from inside the rt_vlm container; sending it
    as ``video_url`` triggers SSRF protection (or a silent fetch failure), returning
    an error or an empty answer.  Detecting this early lets the CLI fall back to
    fetching the clip itself and inlining it as base64 before the VLM call.
    """
    try:
        host = urllib.parse.urlparse(url).hostname or ""
    except Exception:
        return False
    return host in ("localhost", "127.0.0.1", "::1", "host.openshell.internal") or host.startswith("127.")


def _vios_exit_for(exc: Exception) -> tuple[Exit, str]:
    """Map a VIOS/backend exception to (exit_code, status_string) without importing vss_core at module scope."""
    by_name: dict[str, tuple[Exit, str]] = {
        "VIOSInvalidInputError": (Exit.INVALID_INPUT, "failed"),
        "VIOSNotFoundError": (Exit.NOT_FOUND, "failed"),
        "VIOSTimeoutError": (Exit.TIMEOUT, "timeout"),
        "BackendUnreachableError": (Exit.BACKEND_UNREACHABLE, "failed"),
    }
    return by_name.get(type(exc).__name__, (Exit.BACKEND_UNREACHABLE, "failed"))


def _extract_answer(completion: dict[str, Any]) -> str:
    """Pull the text answer out of an OpenAI-style completion."""
    try:
        content = completion["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("VLM response has no choices[0].message.content") from exc
    if not isinstance(content, str):
        raise ValueError(f"VLM response content is not a string: {type(content).__name__}")
    return content


class VlmInput(BaseModel):
    """Ask a visual question about video and persist the answer to unified memory.

    Exactly one media source is required:

    * ``--sensor <name>`` to pull a clip from VIOS (optionally windowed with
      ``--start-time`` / ``--end-time``).
    * ``--media-url <url>`` to send an already-resolved HTTP/HTTPS handle.

    ``--prompt`` is the only other required field. Everything else defaults.
    """

    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(..., max_length=512_000, description="Visual question to answer.")
    sensor: str | None = Field(
        None,
        description="VIOS sensor name. Resolves to a clip URL that the VLM fetches directly (Path B).",
    )
    start_time: str | None = Field(
        None,
        description="Clip window start, ISO-8601 UTC. Only valid with --sensor.",
    )
    end_time: str | None = Field(
        None,
        description="Clip window end, ISO-8601 UTC. Only valid with --sensor.",
    )
    relook_start_time: str | None = Field(
        None,
        description="Opt in to one narrower read: start in ISO time or source seconds for a recorded file.",
    )
    relook_end_time: str | None = Field(
        None,
        description="Second-read end, paired with --relook-start-time. Requires --sensor.",
    )
    relook_fps: float | None = Field(
        None,
        gt=0,
        le=256,
        description="Frames per second for the second read only. Requires a relook window; honors policy locks.",
    )
    relook_max_frames: int | None = Field(
        None,
        ge=1,
        le=2**31 - 1,
        description="Frame budget for the second read only. Combines with --relook-fps on vLLM; honors policy locks.",
    )
    media_url: str | None = Field(
        None,
        description="Pre-resolved HTTP/HTTPS video URL (Path A). Mutually exclusive with --sensor and --file.",
    )
    file: str | None = Field(
        None,
        description=(
            "Path to a local video file (Path A). The file is read and sent as base64-encoded bytes. "
            "Mutually exclusive with --sensor and --media-url."
        ),
    )
    intent: Literal["critic", "report", "qa", "introspection"] = Field(
        "qa",
        description="Semantic intent of this call. Stored in memory; used by harness routing (NFR-5).",
    )
    model: str | None = Field(
        None,
        max_length=1024,
        description="VLM model id. Defaults to the configured model, else the endpoint's only listed model.",
    )
    timeout: int = Field(
        30,
        ge=1,
        le=3600,
        description="HTTP timeout for the VLM call, in seconds.",
    )
    max_tokens: int | None = Field(None, ge=1, le=1_000_000, description="Maximum tokens to generate.")
    temperature: float | None = Field(None, ge=0.0, le=1.0, description="Sampling temperature.")
    seed: int | None = Field(
        None,
        ge=1,
        le=2**32 - 1,
        description="Sampling seed for reproducible generation.",
    )
    enable_reasoning: bool | None = Field(
        None,
        description="Enable or disable reasoning output from the VLM.",
        json_schema_extra={params_mod.NEGATIVE_FLAG_KEY: "--disable-reasoning"},
    )
    chunk_duration: int | None = Field(
        None,
        ge=0,
        le=3600,
        description="Video chunk duration in seconds. Set 0 to disable chunking.",
    )
    fps: float | None = Field(
        None,
        gt=0,
        le=256,
        description="Frames sampled per second. Unset: the VLM server's sampling default applies.",
    )
    max_frames: int | None = Field(
        None,
        ge=1,
        le=2**31 - 1,
        description="Upper bound on frames sent to the model; the backend applies it. Combines with --fps.",
    )
    total_pixels: int | None = Field(
        None,
        ge=1,
        le=2**31 - 1,
        description=(
            "Pixel budget for the whole clip (Qwen3-VL-family processors), sent as "
            "mm_processor_kwargs.size.longest_edge. About 2048 pixels per vision token."
        ),
    )

    @model_validator(mode="after")
    def _validate_media_source(self) -> VlmInput:
        has_sensor = bool(self.sensor)
        has_url = bool(self.media_url)
        has_file = bool(self.file)
        sources_count = sum([has_sensor, has_url, has_file])
        if sources_count != 1:
            raise ValueError("exactly one of --sensor, --media-url, or --file is required")
        if not has_sensor and (self.start_time or self.end_time):
            raise ValueError("--start-time / --end-time require --sensor")
        if (
            any(value is not None for value in (self.relook_fps, self.relook_max_frames))
            and self.relook_start_time is None
        ):
            raise ValueError("--relook-fps and --relook-max-frames require a relook window")
        if any(value is not None for value in (self.relook_start_time, self.relook_end_time)):
            if not self.relook_start_time or not self.relook_end_time:
                raise ValueError("--relook-start-time and --relook-end-time must be supplied together")
            if not has_sensor:
                raise ValueError("second look requires --sensor")
            if self.chunk_duration:
                raise ValueError("second look requires --chunk-duration 0")
        return self


class VlmOptions(BaseModel):
    """Job and transport options. Not sent to the VLM backend."""

    model_config = ConfigDict(extra="forbid")

    no_persist: bool = Field(False, description="Skip writing the answer to unified memory.")
    use_base64: bool = Field(
        False,
        description=(
            "Send video bytes inline as base64: download a --sensor clip, "
            "or read the --media-url value as a local file path."
        ),
    )
    relook_container: Literal["mp4", "mkv"] | None = Field(
        None,
        description="VIOS container for the second clip only. Requires a relook window; default is unchanged.",
    )


_VLM_POLICY_FIELDS = (
    "model",
    "timeout",
    "temperature",
    "max_tokens",
    "seed",
    "enable_reasoning",
    "chunk_duration",
    "fps",
    "max_frames",
    "total_pixels",
)


_ONE_OF_FPS_OR_FRAMES_BACKENDS = frozenset({"rt_vlm", "cosmos_reason_nim"})


def _apply_vlm_policy(inputs: VlmInput, policy: config_mod.VlmConfig | None) -> VlmInput:
    """Apply configured defaults and reject overrides when the policy is locked."""
    if policy is None:
        return inputs

    explicit = inputs.model_fields_set
    updates: dict[str, Any] = {}
    for name in _VLM_POLICY_FIELDS:
        configured = getattr(policy, name)
        if configured is None:
            continue
        if name in explicit:
            requested = getattr(inputs, name)
            if policy.locked and requested != configured:
                flag = name.replace("_", "-")
                raise InvalidInput(f"--{flag} is locked to {configured!r}; received {requested!r}")
            continue
        updates[name] = configured

    # RT-VLM and the NIM take a rate or a frame count, not both. A frame count
    # the caller asked for (a benchmark's fixed --max-frames) replaces a rate
    # it only inherited from the environment or saved policy; a lock keeps it.
    if (
        policy.backend in _ONE_OF_FPS_OR_FRAMES_BACKENDS
        and "max_frames" in explicit
        and "fps" not in explicit
        and not policy.locked
    ):
        updates.pop("fps", None)

    merged = inputs.model_dump()
    merged.update(updates)
    try:
        return VlmInput.model_validate(merged)
    except ValidationError as exc:
        raise InvalidInput(f"configured VLM policy and run arguments are incompatible: {exc}") from exc


def _resolve_vios_clip(
    deployment: config_mod.Deployment,
    sensor: str,
    start_time: str | None,
    end_time: str | None,
    *,
    transcode: Literal["full"] | None = None,
    container: Literal["mp4", "mkv"] | None = None,
) -> tuple[str, str | None, str | None]:
    """Resolve a VIOS sensor to a clip URL and the effective window bounds.

    Returns ``(media_url, resolved_start, resolved_end)`` with requested bounds
    resolved against the recording. Full transcoding avoids the earlier
    keyframe that the default remux can include.
    """
    from vss_core import vios
    from vss_core._foundation.time import iso8601_to_datetime

    origin = deployment.base_url.rstrip("/")

    async def _fetch() -> tuple[str, str | None, str | None]:
        ref = await vios.resolve_sensor(origin, sensor)
        segments = await vios.recorded_segments(origin, ref.stream_id)
        start, end = vios.resolve_window(segments, start_time, end_time, ref.kind)
        window = tuple(iso8601_to_datetime(value) for value in (start, end))
        whole_segment = any(window == tuple(iso8601_to_datetime(value) for value in segment) for segment in segments)
        url = await vios.get_video_clip_url(
            stream_id=ref.stream_id,
            start_time=start,
            end_time=end,
            vst_internal_url=origin,
            transcode=None if whole_segment else transcode,
            container=container,
        )
        url = vios.normalise_media_url(url, origin)
        await vios.warm_media_url(url)
        return url, start, end

    return asyncio.run(_fetch())


#: Qwen3-VL video processor's default clip floor (128 * 32 * 32). The HF
#: processor rejects a ``size`` missing either edge, so the floor is always sent
#: alongside ``longest_edge``.
_QWEN3_VL_MIN_CLIP_PIXELS = 128 * 32 * 32


def _base_request(
    *,
    prompt: str,
    media_url: str,
    model: str,
    inputs: VlmInput,
) -> dict[str, Any]:
    """Build the request fields shared by every backend."""
    request: dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "video_url", "video_url": {"url": media_url}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    }
    if inputs.temperature is not None:
        request["temperature"] = inputs.temperature
    if inputs.max_tokens is not None:
        request["max_tokens"] = inputs.max_tokens
    if inputs.seed is not None:
        request["seed"] = inputs.seed
    return request


def _video_io(inputs: VlmInput, *, qwen3_loader_cap: bool = False) -> dict[str, Any]:
    """``media_io_kwargs.video`` for the configured sampling; empty means server default.

    ``num_frames`` is the cap for vLLM's uniform loader and the fixed count for
    RT-VLM and NIM. vLLM's ``qwen3_vl`` loader ignores ``num_frames`` and caps
    with ``max_frames`` instead, so vLLM gets both. RT-VLM and NIM reject
    ``fps`` with ``num_frames`` (HTTP 400), so with ``fps`` set they get ``fps``
    alone and their deployment-wide frame cap applies.

    vLLM's ``VideoMediaIO`` hands its loader ``num_frames=32`` unless the
    request sends one (vllm/multimodal/media/video.py, v0.28), so ``fps``
    alone would stop at 32 frames on the uniform loader. With ``fps`` and no
    ``max_frames``, vLLM gets ``num_frames: -1`` so the rate decides.
    """
    video: dict[str, Any] = {}
    if inputs.fps is not None:
        video["fps"] = inputs.fps
        if qwen3_loader_cap and inputs.max_frames is None:
            video["num_frames"] = -1
    if inputs.max_frames is not None:
        if qwen3_loader_cap:
            video["num_frames"] = inputs.max_frames
            video["max_frames"] = inputs.max_frames
        elif inputs.fps is None:
            video["num_frames"] = inputs.max_frames
        else:
            _LOG.warning(
                "max_frames %s not sent: this backend takes fps or a frame count, not both; "
                "its deployment frame cap applies",
                inputs.max_frames,
            )
    return video


def _processor_kwargs(inputs: VlmInput) -> dict[str, Any]:
    """``mm_processor_kwargs`` for the pixel budget; empty means server default."""
    if inputs.total_pixels is None:
        return {}
    return {
        "size": {
            "shortest_edge": min(_QWEN3_VL_MIN_CLIP_PIXELS, inputs.total_pixels),
            "longest_edge": inputs.total_pixels,
        }
    }


def _build_rt_vlm_request(
    *,
    prompt: str,
    media_url: str,
    model: str,
    inputs: VlmInput,
) -> dict[str, Any]:
    """RT-VLM maps ``media_io_kwargs.video`` onto its own frame selector."""
    request = _base_request(prompt=prompt, media_url=media_url, model=model, inputs=inputs)
    video = _video_io(inputs)
    if video:
        request["media_io_kwargs"] = {"video": video}
    if inputs.enable_reasoning is not None:
        request["enable_reasoning"] = inputs.enable_reasoning
    if inputs.chunk_duration is not None:
        request["chunk_duration"] = inputs.chunk_duration
    processor = _processor_kwargs(inputs)
    if processor:
        request["mm_processor_kwargs"] = processor
    return request


def _build_vllm_request(
    *,
    prompt: str,
    media_url: str,
    model: str,
    inputs: VlmInput,
) -> dict[str, Any]:
    """vLLM's video loader samples; Qwen then consumes that selection unchanged."""
    request = _base_request(prompt=prompt, media_url=media_url, model=model, inputs=inputs)
    if inputs.chunk_duration is not None and inputs.chunk_duration != 0:
        raise InvalidInput("positive --chunk-duration is not supported by the standalone vLLM backend")
    if inputs.enable_reasoning is not None:
        request["chat_template_kwargs"] = {"enable_thinking": inputs.enable_reasoning}
    mm_processor_kwargs: dict[str, Any] = {}
    video = _video_io(inputs, qwen3_loader_cap=True)
    if video:
        request["media_io_kwargs"] = {"video": video}
        # Stops the processor re-sampling frames the loader already selected.
        mm_processor_kwargs["do_sample_frames"] = False
    mm_processor_kwargs.update(_processor_kwargs(inputs))
    if mm_processor_kwargs:
        request["mm_processor_kwargs"] = mm_processor_kwargs
    return request


def _build_cosmos_reason_nim_request(
    *,
    prompt: str,
    media_url: str,
    model: str,
    inputs: VlmInput,
) -> dict[str, Any]:
    """Temporarily translate Cosmos Reason NIM calls with RT-VLM's schema."""
    _LOG.warning(
        "Cosmos Reason NIM backend support is alpha; request construction "
        "currently uses the RT-VLM request schema and is pending refinement."
    )
    return _build_rt_vlm_request(
        prompt=prompt,
        media_url=media_url,
        model=model,
        inputs=inputs,
    )


def _build_openai_request(
    *,
    prompt: str,
    media_url: str,
    model: str,
    inputs: VlmInput,
) -> dict[str, Any]:
    """A plain OpenAI chat completion: no engine-specific fields (Inference Hub)."""
    if inputs.chunk_duration is not None and inputs.chunk_duration != 0:
        raise InvalidInput("positive --chunk-duration is not supported by the openai backend")
    ignored = [
        name for name in (*config_mod.VLM_SAMPLING_FIELDS, "enable_reasoning") if getattr(inputs, name) is not None
    ]
    if ignored:
        _LOG.warning(
            "%s not sent: the openai backend sends a plain chat completion, so the endpoint's defaults apply",
            ", ".join(ignored),
        )
    return _base_request(prompt=prompt, media_url=media_url, model=model, inputs=inputs)


def _build_vlm_request(
    *,
    backend: str,
    prompt: str,
    media_url: str,
    model: str,
    inputs: VlmInput,
) -> dict[str, Any]:
    """Build a backend-specific OpenAI-compatible request."""
    if backend == "rt_vlm":
        return _build_rt_vlm_request(prompt=prompt, media_url=media_url, model=model, inputs=inputs)
    if backend == "vllm":
        return _build_vllm_request(prompt=prompt, media_url=media_url, model=model, inputs=inputs)
    if backend == "cosmos_reason_nim":
        return _build_cosmos_reason_nim_request(
            prompt=prompt,
            media_url=media_url,
            model=model,
            inputs=inputs,
        )
    if backend == "openai":
        return _build_openai_request(prompt=prompt, media_url=media_url, model=model, inputs=inputs)
    raise config_mod.ConfigError(f"unsupported VLM backend: {backend}")


def _iter_base64_json(
    *,
    backend: str,
    prompt: str,
    file_path: str,
    model: str,
    inputs: VlmInput,
) -> Any:
    """Yield the VLM request body as a JSON byte stream, reading the file in 192 KB chunks.

    At most one raw chunk (~192 KB) and its base64 encoding (~256 KB) live in memory
    at a time.  The previous list-then-join approach kept the entire encoded payload in
    memory simultaneously with the joined string, the data-URI f-string, and the
    json.dumps output -- typically 4-5x the encoded file size.
    """
    sentinel = f"__b64_{secrets.token_hex(8)}__"
    payload = _build_vlm_request(
        backend=backend,
        prompt=prompt,
        media_url=sentinel,
        model=model,
        inputs=inputs,
    )

    raw = _json_mod.dumps(payload)
    # json.dumps quotes the sentinel; partition on the quoted form.
    sentinel_quoted = _json_mod.dumps(sentinel)  # e.g. '"__b64_abc123__"'
    pre, _, post = raw.partition(sentinel_quoted)

    try:
        with open(file_path, "rb") as fh:
            media_type = "video/x-matroska" if file_path.lower().endswith(".mkv") else "video/mp4"
            yield (pre + f'"data:{media_type};base64,').encode()
            while chunk := fh.read(3 * 65536):
                yield base64.b64encode(chunk)
            yield ('"' + post).encode()
    except OSError as exc:
        raise InvalidInput(f"cannot read local file {file_path!r}: {exc}") from exc


def _retry_delay(response: httpx.Response, minimum: float) -> float:
    try:
        value = response.headers["Retry-After"]
        try:
            delay = float(value)
        except ValueError:
            retry_at = parsedate_to_datetime(value)
            delay = retry_at.replace(tzinfo=retry_at.tzinfo or UTC).timestamp() - time.time()
        if math.isfinite(delay) and delay >= 0:
            return max(minimum, delay)
    except (KeyError, TypeError, ValueError, OverflowError):
        pass
    return minimum


async def _post_second_look(
    url: str, *, backend: str, model: str, inputs: VlmInput, media_url: str, inline: bool
) -> httpx.Response:
    """Retry explicit refusals within one cancellable model-request budget."""
    import httpx

    async def body() -> AsyncGenerator[bytes]:
        with contextlib.closing(
            _iter_base64_json(backend=backend, prompt=inputs.prompt, file_path=media_url, model=model, inputs=inputs)
        ) as chunks:
            for chunk in chunks:
                yield chunk

    deadline = time.monotonic() + float(inputs.timeout)
    try:
        async with asyncio.timeout(float(inputs.timeout)), httpx.AsyncClient() as client:
            for attempt in range(3):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                if inline:
                    async with contextlib.aclosing(body()) as content:
                        response = await client.post(url, content=content, headers=_vlm_headers(), timeout=remaining)
                else:
                    response = await client.post(
                        url,
                        json=_build_vlm_request(
                            backend=backend, prompt=inputs.prompt, media_url=media_url, model=model, inputs=inputs
                        ),
                        headers=_vlm_headers(),
                        timeout=remaining,
                    )
                if response.status_code not in (429, 502, 503, 504) or attempt == 2:
                    break
                delay = _retry_delay(response, 2.0 * (attempt + 1))
                if delay >= deadline - time.monotonic():
                    break
                await response.aclose()
                await asyncio.sleep(delay)
            return response
    except TimeoutError as exc:
        raise httpx.TimeoutException("Second-look model-request deadline reached") from exc


class _VlmJob:
    """One CLI read, sharing its identity and persistence across terminal paths."""

    def __init__(self, group: VlmGroup, inputs: VlmInput, ctx: Context, options: VlmOptions, model: str) -> None:
        from vss_core.memory.adapters import utc_now_iso

        from .memory_adapter import VlmAdapter

        self.job_id = _mint_job_id()
        self.created_at = utc_now_iso()
        self.adapter = VlmAdapter()
        self.inputs = inputs
        self.model = model
        self.input_data: MemoryInput | None = None
        self.model_params: dict[str, Any] = {"model": model, "timeout": inputs.timeout}
        for name in (
            *config_mod.VLM_SAMPLING_FIELDS,
            "max_tokens",
            "temperature",
            "seed",
            "enable_reasoning",
            "chunk_duration",
        ):
            value = getattr(inputs, name)
            if value is not None:
                self.model_params[name] = value
        # Resolve memory first so media failures also receive a terminal record.
        self.persist_error: str | None = None
        try:
            self.memory = group.persist_memory(ctx, no_persist=options.no_persist)
        except memory_mod.MemoryUnavailable as exc:
            self.persist_error = str(exc)
            click.echo(f"vss: unified memory is unavailable, running without it ({exc})", err=True)
            self.memory = None

    def build_input(self, start: str | None, end: str | None, media_url: str | None = None) -> MemoryInput:
        return self.adapter.build_input(
            prompt=self.inputs.prompt,
            sensor=self.inputs.sensor,
            start_time=start,
            end_time=end,
            media_url=media_url,
            intent=self.inputs.intent,
            model_params=self.model_params,
        )

    def failure(
        self,
        detail: str,
        code: Exit,
        *,
        status: str = "failed",
        diagnostic: str | None = None,
        include_error: bool = True,
    ) -> Result:
        if self.input_data is None:
            persisted = _persist_failure(
                self.memory,
                self.adapter,
                job_id=self.job_id,
                created_at=self.created_at,
                prompt=self.inputs.prompt,
                sensor=self.inputs.sensor,
                start_time=self.inputs.start_time,
                end_time=self.inputs.end_time,
                intent=self.inputs.intent,
                model_params=self.model_params,
                status=status,
                message=detail,
            )
        else:
            persisted = _write_terminal(
                self.memory,
                self.adapter,
                job_id=self.job_id,
                created_at=self.created_at,
                input_data=self.input_data,
                status=status,
                message=detail,
            )
        body = {"job_id": self.job_id, "status": status}
        if include_error:
            body["error"] = detail
        click.echo(f"vss: {diagnostic if diagnostic is not None else detail}", err=True)
        return Result(
            body=body, extra={"marker": {"status": status, "persisted": persisted}}, exit=code, job_id=self.job_id
        )

    def resolve_media(
        self, deployment: config_mod.Deployment, ctx: Context
    ) -> tuple[str, str | None, str | None] | Result:
        inputs = self.inputs
        if not inputs.sensor:
            return inputs.file or inputs.media_url, inputs.start_time, inputs.end_time  # type: ignore[return-value]
        if "vst" not in (deployment.services or {}):
            if deployment.is_direct_vlm:
                detail = (
                    f"--sensor needs a VSS deployment, but {deployment.base_url} is a bare VLM endpoint "
                    "with no VIOS. Use --media-url <url> or --media-url <path> --use-base64."
                )
            else:
                detail = (
                    "--sensor requires the `vst` service in the deployment. Re-run `vss configure --base-url <URL>`."
                )
            return self.failure(detail, Exit.CONFIGURATION)
        resolved: tuple[str, str | None, str | None] | Result
        try:
            resolved = _resolve_vios_clip(
                deployment,
                inputs.sensor,
                inputs.start_time,
                inputs.end_time,
                transcode="full" if ctx.extra.get("_second_look_exact_clip") else None,
                container=ctx.extra.get("_second_look_container"),
            )
        except Exception as exc:
            code, status = _vios_exit_for(exc)
            resolved = self.failure(str(exc), code, status=status)
        return resolved

    def download(self, media_url: str, start: str | None, end: str | None) -> str | Result:
        import httpx

        suffix = os.path.splitext(urllib.parse.urlparse(media_url).path)[1] or ".mp4"
        fd, path = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        downloaded = False
        result: str | Result
        try:
            with httpx.stream("GET", media_url, timeout=float(self.inputs.timeout)) as response:
                response.raise_for_status()
                with open(path, "wb") as stream:
                    for chunk in response.iter_bytes(chunk_size=65536):
                        stream.write(chunk)
            downloaded = True
            result = path
        except httpx.TimeoutException:
            detail = f"VIOS clip download timed out after {self.inputs.timeout}s"
            self.input_data = self.build_input(start, end)
            result = self.failure(
                detail, Exit.TIMEOUT, status="timeout", diagnostic=f"{detail} (job {self.job_id})", include_error=False
            )
        except (httpx.HTTPError, OSError) as exc:
            if isinstance(exc, httpx.HTTPStatusError):
                detail = f"VIOS clip download returned HTTP {exc.response.status_code}"
            else:
                detail = f"VIOS clip download failed ({type(exc).__name__})"
            self.input_data = self.build_input(start, end)
            result = self.failure(detail, Exit.BACKEND_UNREACHABLE)
        finally:
            if not downloaded:
                with contextlib.suppress(OSError):
                    os.unlink(path)
        return result

    def request(
        self,
        deployment: config_mod.Deployment,
        backend: str,
        media_url: str,
        inline: bool,
        second_look: bool,
        bounds: tuple[str | None, str | None],
    ) -> httpx.Response | Result:
        import httpx

        result: httpx.Response | Result
        try:
            self.input_data = self.build_input(*bounds, media_url if not self.inputs.sensor and not inline else None)
            url = deployment.endpoint("rt_vlm").rstrip("/") + _COMPLETIONS_PATH
            if inline:
                # Fail before streaming so an unreadable file remains invalid input.
                try:
                    open(media_url, "rb").close()
                except OSError as exc:
                    raise InvalidInput(f"cannot read local file {media_url!r}: {exc}") from exc
            if second_look:
                result = asyncio.run(
                    _post_second_look(
                        url,
                        backend=backend,
                        model=self.model,
                        inputs=self.inputs,
                        media_url=media_url,
                        inline=inline,
                    )
                )
            elif inline:
                result = httpx.post(
                    url,
                    content=_iter_base64_json(
                        backend=backend,
                        prompt=self.inputs.prompt,
                        file_path=media_url,
                        model=self.model,
                        inputs=self.inputs,
                    ),
                    headers=_vlm_headers(),
                    timeout=float(self.inputs.timeout),
                )
            else:
                result = httpx.post(
                    url,
                    json=_build_vlm_request(
                        backend=backend,
                        prompt=self.inputs.prompt,
                        media_url=media_url,
                        model=self.model,
                        inputs=self.inputs,
                    ),
                    headers=_vlm_headers(),
                    timeout=float(self.inputs.timeout),
                )
        except InvalidInput as exc:
            result = self.failure(str(exc), Exit.INVALID_INPUT)
        except httpx.TimeoutException:
            detail = f"VLM call timed out after {self.inputs.timeout}s"
            result = self.failure(
                detail, Exit.TIMEOUT, status="timeout", diagnostic=f"{detail} (job {self.job_id})", include_error=False
            )
        except httpx.HTTPError as exc:
            result = self.failure(str(exc), Exit.BACKEND_UNREACHABLE, diagnostic=f"VLM unreachable at {url}: {exc}")
        return result

    def completion(self, response: httpx.Response) -> tuple[dict[str, Any], str] | Result:
        if response.status_code >= 400:
            detail = f"HTTP {response.status_code}"
            code = Exit.BACKEND_UNREACHABLE if response.status_code >= 500 else Exit.INVALID_INPUT
            return self.failure(detail, code, diagnostic=f"VLM backend error {detail}: {response.text[:500]}")
        try:
            try:
                completion = response.json()
            except ValueError:
                raise ValueError("VLM response was not valid JSON") from None
            answer = _extract_answer(completion)
            if not answer.strip():
                raise ValueError("VLM returned an empty answer")
        except ValueError as exc:
            return self.failure(str(exc), Exit.BACKEND_UNREACHABLE)
        return completion, answer

    def finish(self, response: httpx.Response, media_url: str | None) -> Result:
        parsed = self.completion(response)
        if isinstance(parsed, Result):
            return parsed
        completion, answer = parsed
        model = completion.get("model") or self.model
        body: dict[str, Any] = {
            "job_id": self.job_id,
            "status": "completed",
            "answer": answer,
            "model": model,
            "intent": self.inputs.intent,
        }
        if self.memory is not None:
            assert self.input_data is not None
            output = self.adapter.build_output(
                answer=answer,
                model=model,
                media_url=media_url,
                intent=self.inputs.intent,
                completion_id=completion.get("id"),
            )
            terminal = self.adapter.terminal_record(
                job_id=self.job_id,
                created_at=self.created_at,
                status="completed",
                input_data=self.input_data,
                output=output,
            )
            try:
                self.memory.service.upsert(terminal)
            except memory_mod.write_failures() as exc:
                self.persist_error = str(exc)
                click.echo(f"vss: unified memory write failed ({exc})", err=True)
        persisted = self.memory is not None and not self.persist_error
        body["persisted"] = persisted
        if self.persist_error:
            body["persist_error"] = self.persist_error
        elif persisted:
            body["memory_index"] = self.memory.index
        return Result(
            body=body,
            extra={"marker": {"status": "completed", "persisted": persisted}},
            exit=Exit.PARTIAL if self.persist_error else Exit.SUCCESS,
            job_id=self.job_id,
        )

    def execute(self, deployment: config_mod.Deployment, options: VlmOptions, ctx: Context, backend: str) -> Result:
        prepared = self.resolve_media(deployment, ctx)
        if isinstance(prepared, Result):
            return prepared
        media_url, start, end = prepared
        temporary = None
        if self.inputs.sensor and (_is_loopback_url(media_url) or options.use_base64):
            downloaded = self.download(media_url, start, end)
            if isinstance(downloaded, Result):
                return downloaded
            media_url = temporary = downloaded
        inline = options.use_base64 or bool(self.inputs.file) or temporary is not None
        try:
            response = self.request(
                deployment, backend, media_url, inline, bool(ctx.extra.get("_second_look_exact_clip")), (start, end)
            )
        finally:
            if temporary is not None:
                with contextlib.suppress(OSError):
                    os.unlink(temporary)
        if not isinstance(response, Result):
            response = self.finish(response, None if inline else media_url)
        return response


class VlmGroup(CommandGroup):
    """Ask a visual question about video and persist the answer to memory."""

    name: ClassVar[str] = "vlm"
    summary: ClassVar[str] = "Ask a visual question about video"

    Input: ClassVar[type[BaseModel] | None] = VlmInput
    requires: ClassVar[frozenset[str]] = frozenset({"rt_vlm"})
    extra_params: ClassVar[Sequence[click.Parameter]] = tuple(params_mod.options_from_model(VlmOptions))

    def run(self, action: str, inputs: BaseModel, ctx: Context) -> Result:  # noqa: ARG002
        if not isinstance(inputs, VlmInput):  # pragma: no cover
            raise TypeError(f"expected VlmInput, got {type(inputs).__name__}")
        deployment = ctx.deployment or config_mod.load()
        policy = config_mod.effective_vlm_config(deployment.vlm)
        inputs = _apply_vlm_policy(inputs, policy)
        backend = policy.backend if policy is not None else "rt_vlm"
        options = VlmOptions(**{k: v for k, v in ctx.extra.items() if k in VlmOptions.model_fields})
        if options.relook_container is not None and inputs.relook_start_time is None:
            raise InvalidInput("--relook-container requires a relook window")
        model = inputs.model or _default_model(deployment)
        if inputs.relook_start_time is not None:
            from dataclasses import replace

            from .second_look import run_second_look

            relook_inputs: VlmInput = inputs.model_copy(update={"model": model, "chunk_duration": 0})
            relook_ctx: Context = replace(ctx, deployment=deployment)
            return run_second_look(self, relook_inputs, relook_ctx, policy=policy)
        return _VlmJob(self, inputs, ctx, options, model).execute(deployment, options, ctx, backend)


def _write_terminal(
    memory: Any,
    adapter: Any,
    *,
    job_id: str,
    created_at: str,
    input_data: Any,
    status: str,
    message: str,
) -> bool:
    """Best-effort terminal write on failure paths.

    Returns True only when the record was durably written, so the caller's
    marker can report ``persisted`` truthfully. Hardcoding ``persisted: false``
    on a write that actually succeeded tells callers the job is unretrievable
    when ``vss vlm get/list`` can in fact return it.
    """
    if memory is None:
        return False
    from vss_core.memory.models import MemoryError

    record = adapter.terminal_record(
        job_id=job_id,
        created_at=created_at,
        status=status,
        input_data=input_data,
        error=MemoryError(code=status, message=message),
    )
    try:
        memory.service.upsert(record)
    except Exception:
        return False
    return True


def _persist_failure(
    memory: Any,
    adapter: Any,
    *,
    job_id: str,
    created_at: str,
    prompt: str,
    sensor: str | None,
    start_time: str | None,
    end_time: str | None,
    intent: str,
    model_params: dict[str, Any],
    status: str,
    message: str,
) -> bool:
    """Write the terminal record for a post-mint failure; report whether it landed.

    Every post-mint outcome owes the caller a durable record: the job id is
    already public, so a failure that skips this leaves ``vss vlm get/list``
    unable to retrieve an invocation the CLI just reported.

    The requested window may be exactly what the backend rejected, in which case
    ``build_input`` parses it again and raises (``fromisoformat`` on a malformed
    ``--start-time``). Retry without the window rather than lose the record.
    """
    for _start, _end in ((start_time, end_time), (None, None)):
        try:
            input_data = adapter.build_input(
                prompt=prompt,
                sensor=sensor,
                start_time=_start,
                end_time=_end,
                media_url=None,
                intent=intent,
                model_params=model_params,
            )
        except Exception:
            continue
        return _write_terminal(
            memory,
            adapter,
            job_id=job_id,
            created_at=created_at,
            input_data=input_data,
            status=status,
            message=message,
        )
    return False


VLM = VlmGroup()

__all__ = ["VLM", "VlmGroup", "VlmInput", "VlmOptions"]
