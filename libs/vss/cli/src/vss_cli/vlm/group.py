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
API. The model defaults to whatever ``vss configure`` recorded for ``rt_vlm``.

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
from datetime import datetime
import json as _json_mod
import os
import secrets
import tempfile
from typing import TYPE_CHECKING
from typing import Any
from typing import ClassVar
from typing import Literal
import urllib.parse

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator

from vss_cli import config as config_mod
from vss_cli import params as params_mod
from vss_cli.exits import Exit
from vss_cli.group import CommandGroup
from vss_cli.group import Context
from vss_cli.group import InvalidInput

if TYPE_CHECKING:
    from collections.abc import Sequence

    import click

    from vss_cli.lifecycle import Job
    from vss_core.memory import RecordBundle

    from .memory_adapter import VlmAdapter

_JOB_DOMAIN = "vlm"
_CROCKFORD32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_COMPLETIONS_PATH = "/v1/chat/completions"
_DEFAULT_FIXED_FRAME_BUDGET = 8
_MAX_SAMPLED_FRAMES = 60


def _default_model(deployment: config_mod.Deployment) -> str:
    """The model the deployment's RT-VLM reports serving, or a ConfigError."""
    service = deployment.services.get("rt_vlm")
    if service and service.models:
        return service.models[0]
    raise config_mod.ConfigError(
        f"deployment at {deployment.base_url} reports no RT-VLM model, so --model cannot be defaulted. "
        f"Pass --model explicitly, or re-run `vss configure --base-url {deployment.base_url}`."
    )


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
        description="VLM model name. Defaults to whatever the deployment's RT-VLM reports.",
    )
    timeout: int = Field(
        30,
        ge=1,
        le=3600,
        description="HTTP timeout for the VLM call, in seconds.",
    )
    max_tokens: int | None = Field(None, ge=1, le=1_000_000, description="Maximum tokens to generate.")
    temperature: float | None = Field(None, ge=0.0, le=1.0, description="Sampling temperature.")
    num_frames: int | None = Field(
        None,
        ge=1,
        le=256,
        description=(
            "Fixed frame count sampled across the clip. Mutually exclusive with --fps. "
            "Defaults to 8 when neither sampling option is supplied."
        ),
    )
    fps: float | None = Field(
        None,
        gt=0,
        le=256,
        description="Frames sampled per second across the clip. Mutually exclusive with --num-frames.",
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
        if self.num_frames is not None and self.fps is not None:
            raise ValueError("--num-frames and --fps are mutually exclusive")
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


def _clip_duration_seconds(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    try:
        begin = datetime.fromisoformat(start.replace("Z", "+00:00"))
        finish = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max((finish - begin).total_seconds(), 0.0)


def _rt_vlm_sampling(
    fps: float | None,
    num_frames: int | None,
    duration_seconds: float | None,
) -> tuple[float | int, bool]:
    if fps is not None:
        from vss_core.vlm import bound_rt_vlm_fps_sampling

        return bound_rt_vlm_fps_sampling(fps, duration_seconds, max_frames=_MAX_SAMPLED_FRAMES)
    return num_frames or _DEFAULT_FIXED_FRAME_BUDGET, False


def _build_vlm_request(
    *,
    prompt: str,
    media_url: str,
    model: str,
    max_tokens: int | None,
    temperature: float | None,
    num_frames: int | None,
    fps: float | None,
    duration_seconds: float | None = None,
) -> dict[str, Any]:
    """Build an OpenAI-compatible /v1/chat/completions payload for a URL source."""
    budget, use_fps = _rt_vlm_sampling(fps, num_frames, duration_seconds)
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
        "num_frames_per_second_or_fixed_frames_chunk": budget,
        "use_fps_for_chunking": use_fps,
    }
    if max_tokens is not None:
        request["max_tokens"] = max_tokens
    if temperature is not None:
        request["temperature"] = temperature
    return request


def _iter_base64_json(
    *,
    prompt: str,
    file_path: str,
    model: str,
    max_tokens: int | None,
    temperature: float | None,
    num_frames: int | None,
    fps: float | None,
    duration_seconds: float | None = None,
) -> Any:
    """Yield the VLM request body as a JSON byte stream, reading the file in 192 KB chunks.

    At most one raw chunk (~192 KB) and its base64 encoding (~256 KB) live in memory
    at a time.  The previous list-then-join approach kept the entire encoded payload in
    memory simultaneously with the joined string, the data-URI f-string, and the
    json.dumps output -- typically 4-5x the encoded file size.
    """
    budget, use_fps = _rt_vlm_sampling(fps, num_frames, duration_seconds)
    sentinel = f"__b64_{secrets.token_hex(8)}__"
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "video_url", "video_url": {"url": sentinel}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        "num_frames_per_second_or_fixed_frames_chunk": budget,
        "use_fps_for_chunking": use_fps,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if temperature is not None:
        payload["temperature"] = temperature

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


class VlmGroup(CommandGroup):
    """Ask a visual question about video and persist the answer to memory."""

    name: ClassVar[str] = "vlm"
    summary: ClassVar[str] = "Ask a visual question about video"

    Input: ClassVar[type[BaseModel] | None] = VlmInput
    requires: ClassVar[frozenset[str]] = frozenset({"rt_vlm"})
    extra_params: ClassVar[Sequence[click.Parameter]] = tuple(params_mod.options_from_model(VlmOptions))

    #: A seconds-long visual answer is not worth losing to a store outage:
    #: carry on unpersisted and report the persistence failure as partial.
    require_memory: ClassVar[bool] = False

    @classmethod
    def adapter(cls) -> type[VlmAdapter]:
        from .memory_adapter import VlmAdapter

        return VlmAdapter

    def prepare(self, action: str, inputs: BaseModel, ctx: Context, job: Job, *, persist: bool) -> None:  # noqa: ARG002 - fixed hook signature
        if not isinstance(inputs, VlmInput):  # pragma: no cover - the framework builds this
            raise TypeError(f"expected VlmInput, got {type(inputs).__name__}")

        deployment = ctx.deployment or config_mod.load()
        options = VlmOptions(**{k: v for k, v in ctx.extra.items() if k in VlmOptions.model_fields})

        if options.use_base64 and inputs.sensor:
            raise InvalidInput("--use-base64 cannot be combined with --sensor")

        model = inputs.model or _default_model(deployment)
        model_params: dict[str, Any] = {"model": model, "timeout": inputs.timeout}
        if inputs.fps is not None:
            model_params["fps"] = inputs.fps
        else:
            model_params["num_frames"] = inputs.num_frames or _DEFAULT_FIXED_FRAME_BUDGET
        if inputs.max_tokens is not None:
            model_params["max_tokens"] = inputs.max_tokens
        if inputs.temperature is not None:
            model_params["temperature"] = inputs.temperature

        job.asset_id = inputs.sensor
        job.state = _VlmRun(
            deployment=deployment,
            options=options,
            model=model,
            model_params=model_params,
            intent=inputs.intent,
            resolved_start=inputs.start_time,
            resolved_end=inputs.end_time,
        )
        # The requested window may be exactly what the backend will reject, in
        # which case build_input parses it again and raises (fromisoformat on a
        # malformed --start-time). Record the request without the window rather
        # than mint a job no record can describe.
        adapter = self.adapter()
        for start, end in ((inputs.start_time, inputs.end_time), (None, None)):
            try:
                job.input_data = adapter.build_input(
                    prompt=inputs.prompt,
                    sensor=inputs.sensor,
                    start_time=start,
                    end_time=end,
                    media_url=None,
                    intent=inputs.intent,
                    model_params=model_params,
                )
            except Exception:
                continue
            break

    def execute(self, action: str, inputs: BaseModel, ctx: Context, job: Job) -> Any:  # noqa: ARG002 - fixed hook signature
        import httpx

        from vss_cli.lifecycle import JobError

        assert isinstance(inputs, VlmInput)
        state: _VlmRun = job.state
        deployment = state.deployment
        adapter = self.adapter()

        def describe(media_url: str | None) -> None:
            """Refine the record's request side as media resolution progresses."""
            job.input_data = adapter.build_input(
                prompt=inputs.prompt,
                sensor=inputs.sensor,
                start_time=state.resolved_start,
                end_time=state.resolved_end,
                media_url=media_url,
                intent=inputs.intent,
                model_params=state.model_params,
            )

        media_url: str
        if inputs.sensor:
            if "vst" not in (deployment.services or {}):
                raise JobError(
                    "--sensor requires the `vst` service in the deployment. Re-run `vss configure --base-url <URL>`.",
                    exit=Exit.CONFIGURATION,
                )
            try:
                media_url, state.resolved_start, state.resolved_end = _resolve_vios_clip(
                    deployment, inputs.sensor, inputs.start_time, inputs.end_time
                )
            except Exception as vios_exc:
                vios_code, vios_status = _vios_exit_for(vios_exc)
                raise JobError(str(vios_exc), exit=vios_code, status=vios_status) from vios_exc  # type: ignore[arg-type]
            describe(None)
            if _is_loopback_url(media_url):
                # The VIOS clip URL resolves to localhost -- reachable from this CLI
                # host but blocked by rt_vlm's SSRF protection (or simply unreachable
                # from inside the VLM container in Docker deployments). Stream the
                # clip to a temp file and send it inline as base64.
                tmp_fd, state.loopback_tmp = tempfile.mkstemp(suffix=".mp4")
                os.close(tmp_fd)
                try:
                    with httpx.stream("GET", media_url, timeout=float(inputs.timeout)) as clip_resp:
                        clip_resp.raise_for_status()
                        with open(state.loopback_tmp, "wb") as handle:
                            for chunk in clip_resp.iter_bytes(chunk_size=65536):
                                handle.write(chunk)
                except httpx.TimeoutException as exc:
                    state.discard_tmp()
                    detail = f"VIOS clip download timed out after {inputs.timeout}s"
                    raise JobError(
                        detail, exit=Exit.TIMEOUT, status="timeout", diagnostic=f"vss: {detail} (job {job.job_id})"
                    ) from exc
                except Exception as exc:
                    # httpx.HTTPError (network/protocol failure) or OSError during
                    # the write -- both signal VIOS is unreachable, not a caller
                    # mistake, so exit 3 rather than 2.
                    state.discard_tmp()
                    raise JobError(
                        f"cannot fetch VIOS clip for loopback base64 fallback: {exc}", exit=Exit.BACKEND_UNREACHABLE
                    ) from exc
                media_url = state.loopback_tmp
        elif inputs.file:
            # Path A (file): local file path read and sent as base64-encoded bytes.
            media_url = inputs.file
        else:
            # Path A (url): pre-resolved HTTP/HTTPS handle passed directly to the VLM.
            media_url = inputs.media_url  # type: ignore[assignment]

        # True for --file, "--media-url <path> --use-base64", or the loopback
        # SSRF fallback where the clip was streamed to a temp file: the video
        # content is machine-specific bytes, not a retrievable URL handle.
        state.use_base64 = state.options.use_base64 or bool(inputs.file) or state.loopback_tmp is not None
        state.media_url = media_url
        describe(media_url if (not inputs.sensor and not state.use_base64) else None)

        vlm_url = deployment.endpoint("rt_vlm").rstrip("/") + _COMPLETIONS_PATH
        try:
            if state.use_base64:
                # Stream the JSON body chunk-by-chunk from the file, so only one
                # 192 KB raw chunk is in memory at a time.
                file_to_read = state.loopback_tmp if state.loopback_tmp is not None else media_url
                # Pre-validate readability before giving the file to httpx: if the
                # open() fails inside the content generator, httpx wraps the OSError
                # as httpx.WriteError (an HTTPError subclass) and the caller sees
                # BACKEND_UNREACHABLE instead of INVALID_INPUT.
                try:
                    open(file_to_read, "rb").close()
                except OSError as exc:
                    raise InvalidInput(f"cannot read local file {file_to_read!r}: {exc}") from exc
                response = httpx.post(
                    vlm_url,
                    content=_iter_base64_json(
                        prompt=inputs.prompt,
                        file_path=file_to_read,
                        model=state.model,
                        max_tokens=inputs.max_tokens,
                        temperature=inputs.temperature,
                        num_frames=inputs.num_frames,
                        fps=inputs.fps,
                        duration_seconds=_clip_duration_seconds(state.resolved_start, state.resolved_end),
                    ),
                    headers={"Content-Type": "application/json"},
                    timeout=float(inputs.timeout),
                )
            else:
                response = httpx.post(
                    vlm_url,
                    json=_build_vlm_request(
                        prompt=inputs.prompt,
                        media_url=media_url,
                        model=state.model,
                        max_tokens=inputs.max_tokens,
                        temperature=inputs.temperature,
                        num_frames=inputs.num_frames,
                        fps=inputs.fps,
                        duration_seconds=_clip_duration_seconds(state.resolved_start, state.resolved_end),
                    ),
                    timeout=float(inputs.timeout),
                )
        except httpx.TimeoutException as exc:
            detail = f"VLM call timed out after {inputs.timeout}s"
            raise JobError(
                detail, exit=Exit.TIMEOUT, status="timeout", diagnostic=f"vss: {detail} (job {job.job_id})"
            ) from exc
        except httpx.HTTPError as exc:
            raise JobError(
                str(exc), exit=Exit.BACKEND_UNREACHABLE, diagnostic=f"vss: RT-VLM unreachable at {vlm_url}: {exc}"
            ) from exc
        finally:
            # Delete the temp file whether the VLM call succeeded, failed, or raised.
            state.discard_tmp()

        if response.status_code >= 400:
            detail = f"HTTP {response.status_code}"
            code = Exit.BACKEND_UNREACHABLE if response.status_code >= 500 else Exit.INVALID_INPUT
            raise JobError(detail, exit=code, diagnostic=f"vss: VLM backend error {detail}: {response.text[:500]}")

        try:
            completion = response.json()
        except ValueError as exc:
            raise JobError("VLM response was not valid JSON", exit=Exit.BACKEND_UNREACHABLE) from exc
        try:
            answer = _extract_answer(completion)
        except ValueError as exc:
            raise JobError(str(exc), exit=Exit.BACKEND_UNREACHABLE) from exc
        if not answer.strip():
            raise JobError("VLM returned an empty answer", exit=Exit.BACKEND_UNREACHABLE)

        state.answer = answer
        return completion

    def build_bundle(self, job: Job, output: Any) -> RecordBundle:
        """One completed parent; a point-call visual answer has no result rows."""
        from vss_core.memory import RecordBundle

        state: _VlmRun = job.state
        adapter = self.adapter()
        record_output = adapter.build_output(
            answer=state.answer or "",
            model=output.get("model") or state.model,
            media_url=None if state.use_base64 else state.media_url,
            intent=state.intent,
            completion_id=output.get("id"),
        )
        parent = adapter().terminal_record(
            job_id=job.job_id,
            created_at=job.created_at,
            status="completed",
            input_data=job.input_data,
            output=record_output,
        )
        return RecordBundle(parent=parent)

    def render(self, job: Job, output: Any, persist: dict[str, Any] | None) -> dict[str, Any]:  # noqa: ARG002 - fixed hook signature
        state: _VlmRun = job.state
        return {"answer": state.answer, "model": output.get("model") or state.model, "intent": state.intent}


@dataclass
class _VlmRun:
    """What ``prepare`` resolved, refined by ``execute`` as media is resolved."""

    deployment: config_mod.Deployment
    options: VlmOptions
    model: str
    model_params: dict[str, Any]
    intent: str
    resolved_start: str | None
    resolved_end: str | None
    media_url: str | None = None
    use_base64: bool = False
    #: Temp file path when the loopback fallback fired; deleted after the call.
    loopback_tmp: str | None = None
    answer: str | None = None

    def discard_tmp(self) -> None:
        if self.loopback_tmp is not None:
            with contextlib.suppress(OSError):
                os.unlink(self.loopback_tmp)
            self.loopback_tmp = None


VLM = VlmGroup()

__all__ = ["VLM", "VlmGroup", "VlmInput", "VlmOptions"]
