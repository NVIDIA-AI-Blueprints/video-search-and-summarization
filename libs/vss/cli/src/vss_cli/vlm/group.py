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
import contextlib
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


def _ulid() -> str:
    value = (int(time.time() * 1000) & ((1 << 48) - 1)) << 80 | secrets.randbits(80)
    return "".join(_CROCKFORD32[(value >> shift) & 0x1F] for shift in range(125, -1, -5))


def _mint_job_id() -> str:
    return f"{_JOB_DOMAIN}-{_ulid()}"


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


def _chat_request(*, prompt: str, media_url: str, model: str, inputs: VlmInput, local: bool = False):
    """Adapt the existing video invocation to the generic typed API."""
    from pathlib import Path

    from vss_core.vlm import ChatMessage
    from vss_core.vlm import ChatRequest
    from vss_core.vlm import GenerationOptions
    from vss_core.vlm import TextPart
    from vss_core.vlm import VideoFile
    from vss_core.vlm import VideoOptions
    from vss_core.vlm import VideoPart

    source = VideoFile(Path(media_url)) if local else media_url
    return ChatRequest(
        messages=(ChatMessage("user", (VideoPart(source), TextPart(prompt))),),
        model=model,
        generation=GenerationOptions(temperature=inputs.temperature, max_tokens=inputs.max_tokens, seed=inputs.seed),
        enable_reasoning=inputs.enable_reasoning,
        video_options=VideoOptions(
            fps=inputs.fps,
            max_frames=inputs.max_frames,
            total_pixels=inputs.total_pixels,
            chunk_duration=inputs.chunk_duration,
        ),
    )


class VlmGroup(CommandGroup):
    """Ask a visual question about video and persist the answer to memory."""

    name: ClassVar[str] = "vlm"
    summary: ClassVar[str] = "Ask a visual question about video"

    Input: ClassVar[type[BaseModel] | None] = VlmInput
    requires: ClassVar[frozenset[str]] = frozenset({"rt_vlm"})
    extra_params: ClassVar[Sequence[click.Parameter]] = tuple(params_mod.options_from_model(VlmOptions))

    def run(self, action: str, inputs: BaseModel, ctx: Context) -> Result:  # noqa: ARG002
        import httpx

        if not isinstance(inputs, VlmInput):  # pragma: no cover
            raise TypeError(f"expected VlmInput, got {type(inputs).__name__}")

        deployment = ctx.deployment or config_mod.load()
        policy = config_mod.effective_vlm_config(deployment.vlm)
        inputs = _apply_vlm_policy(inputs, policy)
        options = VlmOptions(**{k: v for k, v in ctx.extra.items() if k in VlmOptions.model_fields})

        if options.use_base64 and inputs.sensor:
            raise InvalidInput("--use-base64 cannot be combined with --sensor")

        from .target import resolve_vlm_target

        target = resolve_vlm_target(deployment, inputs.model, policy)
        model = target.model
        job_id = _mint_job_id()

        from vss_core.memory.adapters import utc_now_iso

        from .memory_adapter import VlmAdapter

        adapter = VlmAdapter()
        created_at = utc_now_iso()

        model_params: dict[str, Any] = {"model": model, "timeout": inputs.timeout}
        for name in config_mod.VLM_SAMPLING_FIELDS:
            if getattr(inputs, name) is not None:
                model_params[name] = getattr(inputs, name)
        if inputs.max_tokens is not None:
            model_params["max_tokens"] = inputs.max_tokens
        if inputs.temperature is not None:
            model_params["temperature"] = inputs.temperature
        if inputs.seed is not None:
            model_params["seed"] = inputs.seed
        if inputs.enable_reasoning is not None:
            model_params["enable_reasoning"] = inputs.enable_reasoning
        if inputs.chunk_duration is not None:
            model_params["chunk_duration"] = inputs.chunk_duration

        # Initialise memory before media resolution so any failure path (including
        # the loopback clip-fetch timeout below) can write a terminal record. A
        # configured store that is unavailable must not prevent the visual answer:
        # carry on unpersisted and report the persistence failure as partial.
        persist_error: str | None = None
        try:
            memory = self.persist_memory(ctx, no_persist=options.no_persist)
        except memory_mod.MemoryUnavailable as exc:
            persist_error = str(exc)
            click.echo(f"vss: unified memory is unavailable, running without it ({exc})", err=True)
            memory = None

        # Resolve the media URL.
        media_url: str
        resolved_start: str | None = inputs.start_time
        resolved_end: str | None = inputs.end_time
        _loopback_tmp: str | None = None  # temp file path when loopback fallback fires
        if inputs.sensor:
            if "vst" not in (deployment.services or {}):
                # Post-mint: the job id is already public, so this must write its
                # terminal record and report through a Result (body + marker).
                if deployment.is_direct_vlm:
                    detail = (
                        f"--sensor needs a VSS deployment, but {deployment.base_url} is a bare VLM endpoint "
                        "with no VIOS. Use --media-url <url> or --media-url <path> --use-base64."
                    )
                else:
                    detail = (
                        "--sensor requires the `vst` service in the deployment. "
                        "Re-run `vss configure --base-url <URL>`."
                    )
                _vst_persisted = _persist_failure(
                    memory,
                    adapter,
                    job_id=job_id,
                    created_at=created_at,
                    prompt=inputs.prompt,
                    sensor=inputs.sensor,
                    start_time=inputs.start_time,
                    end_time=inputs.end_time,
                    intent=inputs.intent,
                    model_params=model_params,
                    status="failed",
                    message=detail,
                )
                click.echo(f"vss: {detail}", err=True)
                return Result(
                    body={"job_id": job_id, "status": "failed", "error": detail},
                    extra={"marker": {"status": "failed", "persisted": _vst_persisted}},
                    exit=Exit.CONFIGURATION,
                    job_id=job_id,
                )
            try:
                media_url, resolved_start, resolved_end = _resolve_vios_clip(
                    deployment, inputs.sensor, inputs.start_time, inputs.end_time
                )
            except Exception as _vios_exc:
                _vios_code, _vios_status = _vios_exit_for(_vios_exc)
                _vios_persisted = _persist_failure(
                    memory,
                    adapter,
                    job_id=job_id,
                    created_at=created_at,
                    prompt=inputs.prompt,
                    sensor=inputs.sensor,
                    start_time=inputs.start_time,
                    end_time=inputs.end_time,
                    intent=inputs.intent,
                    model_params=model_params,
                    status=_vios_status,
                    message=str(_vios_exc),
                )
                click.echo(f"vss: {_vios_exc}", err=True)
                return Result(
                    body={"job_id": job_id, "status": _vios_status, "error": str(_vios_exc)},
                    extra={"marker": {"status": _vios_status, "persisted": _vios_persisted}},
                    exit=_vios_code,
                    job_id=job_id,
                )
            if _is_loopback_url(media_url):
                # The VIOS clip URL resolves to localhost — reachable from this CLI
                # host but blocked by rt_vlm's SSRF protection (or simply unreachable
                # from inside the VLM container in Docker deployments). Stream the
                # clip to a temp file and send it inline as base64.
                tmp_fd, _loopback_tmp = tempfile.mkstemp(suffix=".mp4")
                os.close(tmp_fd)
                _download_ok = False
                try:
                    with httpx.stream("GET", media_url, timeout=float(inputs.timeout)) as clip_resp:
                        clip_resp.raise_for_status()
                        with open(_loopback_tmp, "wb") as f:
                            for chunk in clip_resp.iter_bytes(chunk_size=65536):
                                f.write(chunk)
                    media_url = _loopback_tmp
                    _download_ok = True
                except httpx.TimeoutException:
                    detail = f"VIOS clip download timed out after {inputs.timeout}s"
                    # VIOS resolution succeeded; resolved_start/resolved_end are available.
                    clip_input: MemoryInput = adapter.build_input(
                        prompt=inputs.prompt,
                        sensor=inputs.sensor,
                        start_time=resolved_start,
                        end_time=resolved_end,
                        media_url=None,
                        intent=inputs.intent,
                        model_params=model_params,
                    )
                    _persisted = _write_terminal(
                        memory,
                        adapter,
                        job_id=job_id,
                        created_at=created_at,
                        input_data=clip_input,
                        status="timeout",
                        message=detail,
                    )
                    click.echo(f"vss: {detail} (job {job_id})", err=True)
                    return Result(
                        body={"job_id": job_id, "status": "timeout"},
                        extra={"marker": {"status": "timeout", "persisted": _persisted}},
                        exit=Exit.TIMEOUT,
                        job_id=job_id,
                    )
                except Exception as exc:
                    # httpx.HTTPError (network/protocol failure) or OSError
                    # during the write — both signal VIOS is unreachable, not a
                    # caller mistake. Write a terminal record and exit as
                    # BACKEND_UNREACHABLE (3) so callers/retries treat this
                    # correctly instead of seeing an invalid-input (2) exit.
                    detail = f"cannot fetch VIOS clip for loopback base64 fallback: {exc}"
                    clip_input = adapter.build_input(
                        prompt=inputs.prompt,
                        sensor=inputs.sensor,
                        start_time=resolved_start,
                        end_time=resolved_end,
                        media_url=None,
                        intent=inputs.intent,
                        model_params=model_params,
                    )
                    _persisted = _write_terminal(
                        memory,
                        adapter,
                        job_id=job_id,
                        created_at=created_at,
                        input_data=clip_input,
                        status="failed",
                        message=detail,
                    )
                    click.echo(f"vss: {detail}", err=True)
                    return Result(
                        body={"job_id": job_id, "status": "failed", "error": detail},
                        extra={"marker": {"status": "failed", "persisted": _persisted}},
                        exit=Exit.BACKEND_UNREACHABLE,
                        job_id=job_id,
                    )
                finally:
                    # Clean up the temp file if the download failed.  On success
                    # (_download_ok=True) the file is kept for the VLM call below.
                    if not _download_ok:
                        with contextlib.suppress(OSError):
                            os.unlink(_loopback_tmp)
                        _loopback_tmp = None
        elif inputs.file:
            # Path A (file): local file path read and sent as base64-encoded bytes.
            media_url = inputs.file
        else:
            # Path A (url): pre-resolved HTTP/HTTPS handle passed directly to the VLM.
            media_url = inputs.media_url  # type: ignore[assignment]

        # True for --file, "--media-url <path> --use-base64", or the loopback
        # SSRF fallback where the clip was streamed to a temp file: the video
        # content is machine-specific bytes, not a retrievable URL handle.
        _use_base64_effective = options.use_base64 or bool(inputs.file) or _loopback_tmp is not None

        # Wrap everything that references _loopback_tmp in try/finally so the
        # temp file is deleted even if adapter.build_input or the VLM call raises.
        try:
            input_data: MemoryInput = adapter.build_input(
                prompt=inputs.prompt,
                sensor=inputs.sensor,
                start_time=resolved_start,
                end_time=resolved_end,
                media_url=media_url if (not inputs.sensor and not _use_base64_effective) else None,
                intent=inputs.intent,
                model_params=model_params,
            )

            from vss_core.vlm import ChatError
            from vss_core.vlm import VLMChatClient

            request = _chat_request(
                prompt=inputs.prompt, media_url=media_url, model=model, inputs=inputs, local=_use_base64_effective
            )

            async def complete():
                async with VLMChatClient(
                    target.endpoint, target.backend, target.api_key, inputs.timeout, attempts=1
                ) as client:
                    return await client.complete(request, allow_text_parts=False)

            completion = asyncio.run(complete())
            answer = completion.text
        except ChatError as exc:
            detail = (
                f"HTTP {exc.status_code}"
                if exc.status_code is not None
                else f"VLM call timed out after {inputs.timeout}s"
                if exc.kind == "timeout"
                else str(exc)
            )
            status = "timeout" if exc.kind == "timeout" else "failed"
            code = (
                Exit.INVALID_INPUT
                if exc.kind == "validation" or (exc.status_code is not None and 400 <= exc.status_code < 500)
                else Exit.TIMEOUT
                if exc.kind == "timeout"
                else Exit.BACKEND_UNREACHABLE
            )
            persisted = _write_terminal(
                memory,
                adapter,
                job_id=job_id,
                created_at=created_at,
                input_data=input_data,
                status=status,
                message=detail,
            )
            click.echo(f"vss: {detail}", err=True)
            failure_body = {"job_id": job_id, "status": status}
            if status != "timeout":
                failure_body["error"] = detail
            return Result(
                body=failure_body,
                extra={"marker": {"status": status, "persisted": persisted}},
                exit=code,
                job_id=job_id,
            )
        finally:
            if _loopback_tmp is not None:
                with contextlib.suppress(OSError):
                    os.unlink(_loopback_tmp)
                _loopback_tmp = None

        if not answer.strip():
            detail = "VLM returned an empty answer"
            _persisted = _write_terminal(
                memory,
                adapter,
                job_id=job_id,
                created_at=created_at,
                input_data=input_data,
                status="failed",
                message=detail,
            )
            click.echo(f"vss: {detail}", err=True)
            return Result(
                body={"job_id": job_id, "status": "failed", "error": detail},
                extra={"marker": {"status": "failed", "persisted": _persisted}},
                exit=Exit.BACKEND_UNREACHABLE,
                job_id=job_id,
            )

        completion_id: str | None = completion.completion_id
        body: dict[str, Any] = {
            "job_id": job_id,
            "status": "completed",
            "answer": answer,
            "model": completion.reported_model or model,
        }
        body["intent"] = inputs.intent

        # Point call: write the terminal record once.
        if memory is None:
            body["persisted"] = False
            if persist_error:
                body["persist_error"] = persist_error
            return Result(
                body=body,
                extra={"marker": {"status": "completed", "persisted": False}},
                exit=Exit.PARTIAL if persist_error else Exit.SUCCESS,
                job_id=job_id,
            )

        output = adapter.build_output(
            answer=answer,
            model=completion.reported_model or model,
            media_url=None if _use_base64_effective else media_url,
            intent=inputs.intent,
            completion_id=completion_id,
        )
        terminal = adapter.terminal_record(
            job_id=job_id,
            created_at=created_at,
            status="completed",
            input_data=input_data,
            output=output,
        )
        try:
            memory.service.upsert(terminal)
        except memory_mod.write_failures() as exc:
            persist_error = str(exc)
            click.echo(f"vss: unified memory write failed ({exc})", err=True)

        if persist_error:
            body["persisted"] = False
            body["persist_error"] = persist_error
            return Result(
                body=body,
                extra={"marker": {"status": "completed", "persisted": False}},
                exit=Exit.PARTIAL,
                job_id=job_id,
            )

        body["persisted"] = True
        body["memory_index"] = memory.index
        return Result(
            body=body,
            extra={"marker": {"status": "completed", "persisted": True}},
            exit=Exit.SUCCESS,
            job_id=job_id,
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
