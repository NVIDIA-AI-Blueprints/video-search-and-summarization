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
  VIOS resolves the sensor to a clip URL and the VLM receives that URL. No
  video bytes cross the CLI; the VLM fetches the clip from VIOS directly.
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
from dataclasses import dataclass
import json as _json_mod
import logging
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
    from collections.abc import Sequence

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
        return self


class VlmOptions(BaseModel):
    """Job and transport options. Not sent to the VLM backend."""

    model_config = ConfigDict(extra="forbid")

    no_persist: bool = Field(False, description="Skip writing the answer to unified memory.")
    use_base64: bool = Field(
        False,
        description=(
            "Read the --media-url value as a local file path and send its bytes as base64. "
            "Path A escape hatch when VIOS is not available."
        ),
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
) -> tuple[str, str | None, str | None]:
    """Resolve a VIOS sensor to a clip URL and the effective window bounds.

    Returns ``(media_url, resolved_start, resolved_end)`` where the bounds reflect
    the actual window VIOS served, which may differ from the inputs when only one
    bound was supplied or neither was supplied.
    """
    from vss_core import vios

    origin = deployment.base_url.rstrip("/")

    async def _fetch() -> tuple[str, str | None, str | None]:
        ref = await vios.resolve_sensor(origin, sensor)
        segments = await vios.recorded_segments(origin, ref.stream_id)
        start, end = vios.resolve_window(segments, start_time, end_time, ref.kind)
        url = await vios.get_video_clip_url(
            stream_id=ref.stream_id,
            start_time=start,
            end_time=end,
            vst_internal_url=origin,
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
            yield (pre + '"data:video/mp4;base64,').encode()
            while chunk := fh.read(3 * 65536):
                yield base64.b64encode(chunk)
            yield ('"' + post).encode()
    except OSError as exc:
        raise InvalidInput(f"cannot read local file {file_path!r}: {exc}") from exc


class _JobFailedError(Exception):
    """A post-mint failure: the job id is public, so it ends in a terminal record and a Result.

    ``input_data`` is the memory input to record. None means none could be
    built yet (the media never resolved), so the record is built from the
    raw inputs by ``_persist_failure``.
    """

    def __init__(
        self,
        detail: str,
        exit_code: Exit,
        *,
        status: str = "failed",
        echo: str | None = None,
        report_error: bool = True,
        input_data: Any = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.exit_code = exit_code
        self.status = status
        self.echo = echo if echo is not None else f"vss: {detail}"
        self.report_error = report_error
        self.input_data = input_data


@dataclass
class _Media:
    """Where the clip comes from, and the window VIOS actually served."""

    url: str
    start_time: str | None
    end_time: str | None
    #: Temp file holding a loopback VIOS clip, sent inline and deleted after the call.
    loopback_file: str | None = None


@dataclass
class _VlmJob:
    """The state every outcome of one `vss vlm run` call reports against."""

    job_id: str
    created_at: str
    inputs: VlmInput
    model_params: dict[str, Any]
    adapter: Any
    memory: Any

    def build_input(self, media: _Media, *, media_url: str | None) -> MemoryInput:
        return self.adapter.build_input(
            prompt=self.inputs.prompt,
            sensor=self.inputs.sensor,
            start_time=media.start_time,
            end_time=media.end_time,
            media_url=media_url,
            intent=self.inputs.intent,
            model_params=self.model_params,
        )

    def fail(self, failure: _JobFailedError) -> Result:
        if failure.input_data is None:
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
                status=failure.status,
                message=failure.detail,
            )
        else:
            persisted = _write_terminal(
                self.memory,
                self.adapter,
                job_id=self.job_id,
                created_at=self.created_at,
                input_data=failure.input_data,
                status=failure.status,
                message=failure.detail,
            )
        click.echo(failure.echo, err=True)
        body: dict[str, Any] = {"job_id": self.job_id, "status": failure.status}
        if failure.report_error:
            body["error"] = failure.detail
        return Result(
            body=body,
            extra={"marker": {"status": failure.status, "persisted": persisted}},
            exit=failure.exit_code,
            job_id=self.job_id,
        )

    def complete(
        self,
        *,
        answer: str,
        completion: dict[str, Any],
        model: str,
        input_data: MemoryInput,
        stored_media_url: str | None,
        persist_error: str | None,
    ) -> Result:
        """Point call: write the terminal record once and report how persistence went."""
        body: dict[str, Any] = {
            "job_id": self.job_id,
            "status": "completed",
            "answer": answer,
            "model": completion.get("model") or model,
            "intent": self.inputs.intent,
        }
        if self.memory is not None:
            persist_error = self._store_answer(answer, completion, model, input_data, stored_media_url)
        if self.memory is None or persist_error:
            body["persisted"] = False
            if persist_error:
                body["persist_error"] = persist_error
            return Result(
                body=body,
                extra={"marker": {"status": "completed", "persisted": False}},
                exit=Exit.PARTIAL if persist_error else Exit.SUCCESS,
                job_id=self.job_id,
            )
        body["persisted"] = True
        body["memory_index"] = self.memory.index
        return Result(
            body=body,
            extra={"marker": {"status": "completed", "persisted": True}},
            exit=Exit.SUCCESS,
            job_id=self.job_id,
        )

    def _store_answer(
        self,
        answer: str,
        completion: dict[str, Any],
        model: str,
        input_data: MemoryInput,
        stored_media_url: str | None,
    ) -> str | None:
        """Upsert the completed record; the write error, or None when it landed."""
        output = self.adapter.build_output(
            answer=answer,
            model=completion.get("model") or model,
            media_url=stored_media_url,
            intent=self.inputs.intent,
            completion_id=completion.get("id"),
        )
        terminal = self.adapter.terminal_record(
            job_id=self.job_id,
            created_at=self.created_at,
            status="completed",
            input_data=input_data,
            output=output,
        )
        try:
            self.memory.service.upsert(terminal)
        except memory_mod.write_failures() as exc:
            click.echo(f"vss: unified memory write failed ({exc})", err=True)
            return str(exc)
        return None


def _model_params(inputs: VlmInput, model: str) -> dict[str, Any]:
    """The request values recorded with the job, unset ones omitted."""
    params: dict[str, Any] = {"model": model, "timeout": inputs.timeout}
    for name in (
        *config_mod.VLM_SAMPLING_FIELDS,
        "max_tokens",
        "temperature",
        "seed",
        "enable_reasoning",
        "chunk_duration",
    ):
        if getattr(inputs, name) is not None:
            params[name] = getattr(inputs, name)
    return params


def _sensor_unavailable_detail(deployment: config_mod.Deployment) -> str:
    if deployment.is_direct_vlm:
        return (
            f"--sensor needs a VSS deployment, but {deployment.base_url} is a bare VLM endpoint "
            "with no VIOS. Use --media-url <url> or --media-url <path> --use-base64."
        )
    return "--sensor requires the `vst` service in the deployment. Re-run `vss configure --base-url <URL>`."


def _resolve_media(job: _VlmJob, deployment: config_mod.Deployment) -> _Media:
    """The clip to send: a VIOS sensor clip (Path B), a local file, or a URL (Path A)."""
    inputs = job.inputs
    if inputs.file:
        return _Media(inputs.file, inputs.start_time, inputs.end_time)
    if not inputs.sensor:
        return _Media(inputs.media_url or "", inputs.start_time, inputs.end_time)
    if "vst" not in (deployment.services or {}):
        raise _JobFailedError(_sensor_unavailable_detail(deployment), Exit.CONFIGURATION)
    try:
        url, start, end = _resolve_vios_clip(deployment, inputs.sensor, inputs.start_time, inputs.end_time)
    except Exception as exc:
        code, status = _vios_exit_for(exc)
        raise _JobFailedError(str(exc), code, status=status) from exc
    media = _Media(url, start, end)
    if _is_loopback_url(url):
        # The VIOS clip URL resolves to localhost — reachable from this CLI
        # host but blocked by rt_vlm's SSRF protection (or simply unreachable
        # from inside the VLM container in Docker deployments). Stream the
        # clip to a temp file and send it inline as base64.
        media.loopback_file = _download_clip(job, media)
        media.url = media.loopback_file
    return media


def _download_clip(job: _VlmJob, media: _Media) -> str:
    """Stream a loopback VIOS clip to a temp file; the path, or _JobFailedError with the file removed."""
    import httpx

    tmp_fd, path = tempfile.mkstemp(suffix=".mp4")
    os.close(tmp_fd)
    try:
        with httpx.stream("GET", media.url, timeout=float(job.inputs.timeout)) as clip_resp:
            clip_resp.raise_for_status()
            with open(path, "wb") as f:
                for chunk in clip_resp.iter_bytes(chunk_size=65536):
                    f.write(chunk)
    except httpx.TimeoutException as exc:
        with contextlib.suppress(OSError):
            os.unlink(path)
        detail = f"VIOS clip download timed out after {job.inputs.timeout}s"
        # VIOS resolution succeeded, so the resolved window is recorded.
        raise _JobFailedError(
            detail,
            Exit.TIMEOUT,
            status="timeout",
            echo=f"vss: {detail} (job {job.job_id})",
            report_error=False,
            input_data=job.build_input(media, media_url=None),
        ) from exc
    except Exception as exc:
        # httpx.HTTPError (network/protocol failure) or OSError during the
        # write — both signal VIOS is unreachable, not a caller mistake, so
        # BACKEND_UNREACHABLE (3) rather than invalid input (2).
        with contextlib.suppress(OSError):
            os.unlink(path)
        raise _JobFailedError(
            f"cannot fetch VIOS clip for loopback base64 fallback: {exc}",
            Exit.BACKEND_UNREACHABLE,
            input_data=job.build_input(media, media_url=None),
        ) from exc
    return path


def _post_vlm(
    vlm_url: str,
    *,
    backend: str,
    model: str,
    inputs: VlmInput,
    media_url: str,
    inline: bool,
) -> Any:
    """POST the chat completion; an inline clip streams from its file as base64."""
    import httpx

    if not inline:
        return httpx.post(
            vlm_url,
            json=_build_vlm_request(
                backend=backend, prompt=inputs.prompt, media_url=media_url, model=model, inputs=inputs
            ),
            headers=_vlm_headers(),
            timeout=float(inputs.timeout),
        )
    # Pre-validate readability before giving the file to httpx. If the open()
    # fails inside the content generator, httpx wraps the OSError as
    # httpx.WriteError (an httpx.HTTPError subclass) and the caller sees
    # BACKEND_UNREACHABLE instead of INVALID_INPUT.
    try:
        open(media_url, "rb").close()
    except OSError as exc:
        raise InvalidInput(f"cannot read local file {media_url!r}: {exc}") from exc
    # Stream the JSON body chunk-by-chunk from the file: one 192 KB raw chunk in
    # memory at a time instead of the whole encoded payload several times over.
    return httpx.post(
        vlm_url,
        content=_iter_base64_json(
            backend=backend, prompt=inputs.prompt, file_path=media_url, model=model, inputs=inputs
        ),
        headers=_vlm_headers(),
        timeout=float(inputs.timeout),
    )


def _call_vlm(job: _VlmJob, vlm_url: str, input_data: MemoryInput, **request: Any) -> Any:
    """`_post_vlm`, with each way the call can fail mapped to its terminal outcome."""
    import httpx

    try:
        return _post_vlm(vlm_url, **request)
    except InvalidInput as exc:
        # The readability check and _iter_base64_json while httpx consumes the
        # body both raise after the job id is minted, so they report through a
        # Result rather than propagating to guarded() without a marker.
        raise _JobFailedError(str(exc), Exit.INVALID_INPUT, input_data=input_data) from exc
    except httpx.TimeoutException as exc:
        detail = f"VLM call timed out after {job.inputs.timeout}s"
        raise _JobFailedError(
            detail,
            Exit.TIMEOUT,
            status="timeout",
            echo=f"vss: {detail} (job {job.job_id})",
            report_error=False,
            input_data=input_data,
        ) from exc
    except httpx.HTTPError as exc:
        raise _JobFailedError(
            str(exc),
            Exit.BACKEND_UNREACHABLE,
            echo=f"vss: VLM unreachable at {vlm_url}: {exc}",
            input_data=input_data,
        ) from exc


def _read_answer(response: Any, input_data: MemoryInput) -> tuple[str, dict[str, Any]]:
    """The answer text and the completion it came from, or _JobFailedError."""
    if response.status_code >= 400:
        detail = f"HTTP {response.status_code}"
        raise _JobFailedError(
            detail,
            Exit.BACKEND_UNREACHABLE if response.status_code >= 500 else Exit.INVALID_INPUT,
            echo=f"vss: VLM backend error {detail}: {response.text[:500]}",
            input_data=input_data,
        )
    try:
        completion = response.json()
    except ValueError as exc:
        raise _JobFailedError(
            "VLM response was not valid JSON", Exit.BACKEND_UNREACHABLE, input_data=input_data
        ) from exc
    try:
        answer = _extract_answer(completion)
    except ValueError as exc:
        raise _JobFailedError(str(exc), Exit.BACKEND_UNREACHABLE, input_data=input_data) from exc
    if not answer.strip():
        raise _JobFailedError("VLM returned an empty answer", Exit.BACKEND_UNREACHABLE, input_data=input_data)
    return answer, completion


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
        if options.use_base64 and inputs.sensor:
            raise InvalidInput("--use-base64 cannot be combined with --sensor")

        model = inputs.model or _default_model(deployment)
        job_id = _mint_job_id()

        from vss_core.memory.adapters import utc_now_iso

        from .memory_adapter import VlmAdapter

        # Initialise memory before media resolution so any failure path (including
        # the loopback clip-fetch timeout) can write a terminal record. A
        # configured store that is unavailable must not prevent the visual answer:
        # carry on unpersisted and report the persistence failure as partial.
        persist_error: str | None = None
        try:
            memory = self.persist_memory(ctx, no_persist=options.no_persist)
        except memory_mod.MemoryUnavailable as exc:
            persist_error = str(exc)
            click.echo(f"vss: unified memory is unavailable, running without it ({exc})", err=True)
            memory = None

        job = _VlmJob(
            job_id=job_id,
            created_at=utc_now_iso(),
            inputs=inputs,
            model_params=_model_params(inputs, model),
            adapter=VlmAdapter(),
            memory=memory,
        )
        try:
            media = _resolve_media(job, deployment)
        except _JobFailedError as failure:
            return job.fail(failure)

        # --file, "--media-url <path> --use-base64", or the loopback fallback's
        # temp file: machine-specific bytes, not a retrievable URL handle.
        inline = options.use_base64 or bool(inputs.file) or media.loopback_file is not None
        try:
            input_data = job.build_input(media, media_url=None if inputs.sensor or inline else media.url)
            response = _call_vlm(
                job,
                deployment.endpoint("rt_vlm").rstrip("/") + _COMPLETIONS_PATH,
                input_data,
                backend=backend,
                model=model,
                inputs=inputs,
                media_url=media.url,
                inline=inline,
            )
            answer, completion = _read_answer(response, input_data)
        except _JobFailedError as failure:
            return job.fail(failure)
        finally:
            # Deleted whether the call succeeded, failed or raised.
            if media.loopback_file is not None:
                with contextlib.suppress(OSError):
                    os.unlink(media.loopback_file)

        return job.complete(
            answer=answer,
            completion=completion,
            model=model,
            input_data=input_data,
            stored_media_url=None if inline else media.url,
            persist_error=persist_error,
        )


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
