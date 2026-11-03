# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Incident extraction for the incident-console ``/analyze`` path, on the shared incident contract.

Runs the same calls as ``incident-console-v2`` (gateway mode, P1 only) and the ``eval/`` benchmark
(see ``.docs/analysis-schema.md`` in the incident profile):

0. **Caption pass** (``INCIDENT_CHUNKING_ENABLED=1`` for a production request) - the clip is cut into
   overlapping windows, each captioned by the same VLM at its full frame rate, and the timed captions are
   appended to the P1 request as one more user message (``utils/chunk_captions.py``). The prompt file,
   ``inference.json`` and the schema are untouched. The captions are context for P1 only: the response
   does not return them and nothing stores them (their telemetry is logged). A failure of this pass never
   fails the analysis: the reason is logged and P1 runs without context. An experimental request runs it only
   when it sets ``experiment_captions`` (whatever ``INCIDENT_CHUNKING_ENABLED`` says), with its own scratch store
   (``inline``: base64 data URLs, nothing stored; ``r2``: the owner-approved ``INCIDENT_EXPERIMENT_SCRATCH_PREFIX``,
   each window deleted after its call) and its own cap (``max_caption_calls``); ``experiment.caption`` reports it.
1. **P1** - the video as a signed R2 URL first, then the contract extraction prompt (plus the
   reviewer prompt of a re-generation, framed exactly as the console frames its additional
   instruction), with the contract JSON Schema enforced through ``response_format`` and the fixed
   settings from ``inference.json``. The response is validated under ``core-scored-v1`` with the
   ``id-normalization-v1`` recovery (``incident_contract.parse_report``), like the console;
   a core or schema violation raises :class:`IncidentExtractionError` carrying the raw output,
   and nothing is persisted. An output cut off by length (``finish_reason=length``) or over
   ``MAX_P1_OUTPUT_CHARS`` is retried once with the identical request (never repaired); the response
   and the error list every call in ``p1_attempts``. The video-bounds rules (``WINDOW_BEYOND_VIDEO``,
   ``TIMELINE_BEYOND_VIDEO``) apply whenever the video length is known: the caller's
   ``video_duration_seconds``, else the MP4 header read from the signed URL by byte ranges
   (``utils/video_duration.py``). When neither gives a length the rules cannot apply, as in the
   console and eval; the response says so (``video_bounds_checked`` false with a reason) and a
   warning is logged, so an unchecked report is never silent.
2. **RP1** - a text-only prose report from the validated P1 JSON. A
   failed RP1 never discards a valid P1 report; the error is returned instead. ``skip_rp1``
   leaves it out (``incident-console-v2`` agent mode, which, like gateway mode, uses P1 only).
3. **Evidence** - each entity, instrument and asset is cropped from the video at its
   ``timestamp_seconds`` and uploaded to R2 under ``evidence/<video key>/run-<n>/<itemId>.jpg``
   (the video's own R2 key and the next unused run number) (``crop_evidence``). An item without an image keeps a plain ``image_gap`` reason.
   With ``INCIDENT_TRACKING_ENABLED`` set, a production request also pushes the clip through the stock VSS
   perception stack (``track_evidence``) and re-cuts each entity it matches to a person track at the track's first-seen
   moment; the response carries a ``tracking`` summary and a tracking failure never fails the analysis.
   With ``INCIDENT_SOM_ENABLED`` set as well (set-of-marks prompting, ``utils/set_of_marks.py``), tracking runs
   before P1 instead: P1 sees a scratch copy of the clip with a numbered box on each person track and one more
   context message asking for the number in each person entity's label, and the entities are matched to tracks by
   that number first (box overlap for the rest). The number is stripped from the stored labels, the evidence is
   cut from the original clip, and a failure to render or upload the copy runs P1 on the original clip.
   With ``INCIDENT_OBJECT_TRACKING_ENABLED`` set, a production request also follows each located instrument and
   asset through the clip (``object_tracking``: a seeded OpenCV tracker corrected by the open-vocabulary detector),
   stores its track like a person's with ``track_source`` ``object-tracker``, and re-cuts its frame at the track's
   first-seen second; the response carries an ``object_tracking`` summary and a failure never fails the analysis.
4. **Revision** - the validated report is appended best-effort as a new ``kind='ai'``
   ``report_revisions`` row (``payload`` = report, ``evidence`` = step 3, facet columns,
   ``parent_id`` NULL). A re-generation is a new branch and copies nothing from earlier revisions.

The revision needs its ``model_runs`` row (foreign key). With a caller ``model_run_id``
(``incident-console-v2`` agent mode) the console owns that row and writes its ``notes`` record
after this call returns, so the agent only creates a placeholder row when none exists yet. A
standalone call (no ``model_run_id``) registers ``config.model_run_id`` itself. The ``videos`` row
belongs to the console, which owns the durable R2 key, so this tool never writes it.
"""

import asyncio
from collections.abc import AsyncGenerator
import copy
import hashlib
import logging
import os
import re
import time
from typing import Annotated
from typing import Any
from typing import Literal
from urllib.parse import urlsplit
import uuid

import httpx
from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.function import FunctionBaseConfig
from pydantic import AfterValidator
from pydantic import BaseModel
from pydantic import Field

from vss_agents import incident_contract
from vss_agents.incident_contract import IncidentExtractionError
from vss_agents.tools import incident_experiments
from vss_agents.utils import chunk_captions
from vss_agents.utils import crop_evidence
from vss_agents.utils import incident_db
from vss_agents.utils import object_tracking
from vss_agents.utils import run_provenance
from vss_agents.utils import set_of_marks
from vss_agents.utils import track_evidence
from vss_agents.utils import video_duration
from vss_agents.utils.embeddings import RemoteTextEmbedder
from vss_agents.utils.r2 import is_r2_configured

logger = logging.getLogger(__name__)

# P1 with 64 frames or a reasoning model can take minutes; matches the console and gateway.
DEFAULT_REQUEST_TIMEOUT_SECONDS = 300.0
# Reading the MP4 header takes a few small ranged reads of the signed URL.
VIDEO_HEADER_TIMEOUT_SECONDS = 30.0

# Same limit and framing as the console's additional instruction (incident-console-v2
# modules/analysis/application/build-request.ts): both read the preamble from contracts/prompts/
# (p1/reviewer-preamble), so a reviewer prompt reads the same in both analysis modes.
MAX_REVIEWER_PROMPT_LENGTH = 1000
REVIEWER_PROMPT_PREAMBLE = incident_contract.prompt("p1/reviewer-preamble")
CAPTION_PROMPT_IDS = ("caption/system", "caption/window", "caption/time-range")
ZOOM_PROMPT_IDS = (
    "zoom/look-entity-frame",
    "zoom/look-entity-close-up",
    "zoom/look-object-frame",
    "zoom/look-object-close-up",
    "zoom/merge-entity",
    "zoom/merge-object",
)
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class VlmNoAnswerError(ValueError):
    """The video model answered HTTP 200 with a null body or no choices (``/analyze`` returns 502)."""


def redact_url(url: str) -> str:
    """``url`` without credentials, query or fragment, so a signed URL never reaches a log (the path keeps the object key)."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return f"{parts.scheme}://{host}{parts.path}" if parts.scheme and host else "<redacted url>"


class SignedUrl(str):
    """A ``str`` whose ``repr`` is the redacted URL, so a signed URL never reaches a log through a container.

    NAT logs a failed tool call's input as the plain kwargs dict LangChain hands over, which has no model
    ``__repr__`` to hook; the value itself must print safely. The string content is unchanged.
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return repr(redact_url(str(self)))


class IncidentReportGenConfig(FunctionBaseConfig, name="incident_report_gen"):
    """Endpoints and identity for the contract P1/RP1 calls.

    The P1/RP1 prompts, schema and request settings are not configured here: they come
    from the vendored incident contract, identical to eval and the console.
    """

    vlm_base_url: str = Field(..., description="OpenAI-compatible base URL for P1 (no trailing /v1).")
    vlm_model_name: str = Field(..., description="VLM that runs P1 on the video when the caller names none.")
    rp1_base_url: str = Field(..., description="OpenAI-compatible base URL for RP1 (no trailing /v1).")
    api_key_env: str = Field(
        default="OPENAI_API_KEY", description="Environment variable holding the bearer key for both endpoints."
    )
    request_timeout_seconds: float = Field(default=DEFAULT_REQUEST_TIMEOUT_SECONDS, gt=0)
    model_run_id: str = Field(
        default="incident_report_gen",
        description="model_runs.id a standalone call (no caller model_run_id) persists under.",
    )


# The highest per-request crop-call cap an experimental request may ask for.
MAX_CROP_CALLS_LIMIT = 200


class IncidentReportGenInput(BaseModel):
    incident_id: str = Field(..., min_length=1, max_length=20, description="Console videos.id.")
    video_url: Annotated[str, AfterValidator(SignedUrl)] = Field(
        ..., min_length=1, description="Signed R2 URL of the uploaded video, created by the console."
    )
    video_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=900,
        description="The video's R2 key; evidence crops go under evidence/<video_key>/. Falls back to incident_id.",
    )
    model_run_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=20,
        description="The caller's model_runs.id; when given, the caller owns the model_runs row and its notes.",
    )
    skip_rp1: bool = Field(default=False, description="Run P1 only; report_text stays null.")
    model: str | None = Field(
        default=None, min_length=1, description="P1 VLM from the contract allowlist; default is the configured one."
    )
    reviewer_prompt: str | None = Field(
        default=None, description="Extra reviewer instruction for a re-generation, added after the P1 prompt."
    )
    video_duration_seconds: float | None = Field(
        default=None,
        gt=0,
        allow_inf_nan=False,
        strict=True,
        description="The video length the caller read; when absent the agent reads the MP4 header itself.",
    )
    experiment_prompt_id: str | None = Field(
        default=None,
        description="Experimental: a registry P1 prompt id (needs INCIDENT_EXPERIMENTS_ENABLED=1; never persisted).",
    )
    skip_crops: bool = Field(
        default=False,
        description="Experimental: skip post-P1 evidence cropping (needs INCIDENT_EXPERIMENTS_ENABLED=1).",
    )
    max_crop_calls: int | None = Field(
        default=None,
        ge=1,
        le=MAX_CROP_CALLS_LIMIT,
        description=(
            "Experimental: the most grounding HTTP attempts (retries included) this request's cropping may make. "
            "Required for an experimental crops-on request; items beyond it keep an image_gap."
        ),
    )
    max_zoom_calls: int | None = Field(
        default=None,
        ge=1,
        le=MAX_CROP_CALLS_LIMIT,
        description=(
            "Experimental: the most zoomed-second-look HTTP attempts (retries included) this request may make; items "
            "beyond it keep a zoom_gap. Without it the second look runs uncapped."
        ),
    )
    experiment_object_tracking: bool = Field(
        default=False,
        description="Experimental: run the object tracker in a crops-on request, under its own detector-call budget.",
    )
    experiment_captions: Literal["inline", "r2"] | None = Field(
        default=None,
        description=(
            "Experimental: run the caption pass before P1, as production does, with its windows in this scratch "
            "store: 'inline' (each window sent as a base64 data URL, nothing stored) or 'r2' (the owner-approved "
            "INCIDENT_EXPERIMENT_SCRATCH_PREFIX, each window deleted after its call). Needs max_caption_calls."
        ),
    )
    max_caption_calls: int | None = Field(
        default=None,
        ge=1,
        le=MAX_CROP_CALLS_LIMIT,
        description=(
            "Experimental: the most caption HTTP attempts (retries included) this request's caption pass may make. "
            "Required with experiment_captions; a clip with more windows than the cap gets no caption pass."
        ),
    )


class ExperimentRefusedError(RuntimeError):
    """An experimental request would write to shared storage (HTTP 409 on ``/analyze``)."""


EXPERIMENT_EVIDENCE_STORE = "temporary in-memory store, discarded after the request (no disk, no R2)"
# The owner-approved R2 root of experimental caption windows (blocker X16): the one shared-storage write an
# experiment may make, and only when this is set. Never production's scratch/analyze.
EXPERIMENT_SCRATCH_PREFIX_ENV = "INCIDENT_EXPERIMENT_SCRATCH_PREFIX"
# Fields only an experimental request returns; production responses never carry them.
EXPERIMENT_OUTPUT_FIELDS = ("experiment", "p1_elapsed_ms", "usage")


class IncidentReportGenOutput(BaseModel):
    """What ``POST /api/v1/incidents/{id}/analyze`` returns to the console."""

    report: dict[str, Any] = Field(description="Validated contract report with derived fields (incident.duration).")
    report_text: str | None = None
    report_text_error: str | None = None
    model: str
    contract_version: str
    raw_output: str
    finish_reason: str | None = None
    p1_attempts: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Every P1 call this request made, in order: attempt, outcome, finish_reason, output_chars.",
    )
    revision_id: int | None = Field(
        default=None, description="report_revisions.id of the AI revision written, or null when none was written."
    )
    evidence: list[dict[str, Any]] = Field(
        default_factory=list, description="The revision's evidence entries (R2 image keys or image_gap reasons)."
    )
    video_duration_seconds: float | None = Field(
        default=None, description="The video length the bounds rules used; null when it could not be read."
    )
    video_duration_source: Literal["request", "mp4_header"] | None = Field(
        default=None, description="Where the length came from: the caller's request or the MP4 header."
    )
    video_bounds_checked: bool = Field(
        default=False, description="Whether WINDOW_BEYOND_VIDEO and TIMELINE_BEYOND_VIDEO were applied."
    )
    video_bounds_unchecked_reason: str | None = Field(
        default=None, description="Why the video-bounds rules were not applied; null when they were."
    )
    experiment: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Experimental requests only: the prompt id and SHA-256 used, skip_crops, the caps, captions, persisted, "
            "and what the crop stage and the caption pass cost (crop, zoom, caption)."
        ),
    )
    p1_elapsed_ms: float | None = Field(default=None, description="Experimental requests only: the P1 round trip.")
    usage: dict[str, Any] | None = Field(default=None, description="Experimental requests only: P1 token usage.")
    tracking: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Person tracking (INCIDENT_TRACKING_ENABLED, production requests only): {enabled, matched, tracks, "
            "elapsed_ms, skipped}, plus marks {rendered, parsed, fallback, invalid, skipped, elapsed_ms} when "
            "INCIDENT_SOM_ENABLED is on. Null when tracking is off or the request is experimental."
        ),
    )
    object_tracking: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Object tracking (INCIDENT_OBJECT_TRACKING_ENABLED, production requests only): {enabled, seeds, tracked, "
            "tracker, elapsed_ms, time_cap_reached, skipped, detector: {cap, calls, ...}, items: {<item id>: {samples, "
            "forward_samples, backward_samples, detector_calls, detector_errors, reanchors, lost, capped, elapsed_ms, "
            "first_seen_seconds, last_seen_seconds}}}. Null when object tracking is off, the request is "
            "experimental or skip_crops was asked for."
        ),
    )
    provenance: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Run provenance (utils/run_provenance.py, incident-run-provenance-v1): {schema, agent: {git_sha, source, "
            "dirty, package_version}, contract_version, flags, stages: {<stage>: {model, prompts: {<id>: sha256}, "
            "calls, failed, refused, prompt_tokens, completion_tokens, total_tokens, calls_without_usage, "
            "elapsed_ms}}}; every HTTP attempt of every stage is counted, failed and refused ones included."
        ),
    )


class VideoLength(BaseModel):
    """The video length the bounds rules use, or why there is none."""

    seconds: float | None
    source: Literal["request", "mp4_header"] | None
    unchecked_reason: str | None


def normalize_reviewer_prompt(value: str | None) -> str | None:
    """Strip control characters, normalise newlines and trim; ``None`` when nothing is left.

    Raises ``ValueError`` above :data:`MAX_REVIEWER_PROMPT_LENGTH` characters (the console's limit).
    """
    if value is None:
        return None
    cleaned = _CONTROL_CHARACTERS.sub("", value).replace("\r\n", "\n").replace("\r", "\n").strip()
    if not cleaned:
        return None
    if len(cleaned) > MAX_REVIEWER_PROMPT_LENGTH:
        raise ValueError(f"The reviewer prompt must be at most {MAX_REVIEWER_PROMPT_LENGTH} characters")
    return cleaned


def _chat_completions_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/v1/chat/completions"


def p1_request_body(
    model: str,
    video_url: str,
    reviewer_prompt: str | None = None,
    prompt_text: str | None = None,
    context_block: str | None = None,
    marks_block: str | None = None,
) -> dict[str, Any]:
    """The P1 request. ``prompt_text`` (a verified registry prompt) replaces only the prompt text.

    ``context_block`` (the caption pass's timed captions) is one more user message after the video message,
    so the stored prompt text and the request settings stay exactly the contract's. ``marks_block`` (set-of-marks
    prompting) is added the same way, after it.
    """
    content: list[dict[str, Any]] = [
        {"type": "video_url", "video_url": {"url": video_url}},
        {"type": "text", "text": incident_contract.extraction_prompt() if prompt_text is None else prompt_text},
    ]
    if reviewer_prompt:
        content.append({"type": "text", "text": f"{REVIEWER_PROMPT_PREAMBLE}\n<<<\n{reviewer_prompt}\n>>>"})
    messages: list[dict[str, Any]] = [{"role": "user", "content": content}]
    if context_block:
        messages.append({"role": "user", "content": context_block})
    if marks_block:
        messages.append({"role": "user", "content": marks_block})
    return {
        "model": model,
        "messages": messages,
        "stream": False,
        **incident_contract.p1_request(),
        "response_format": incident_contract.response_format(),
    }


def rp1_request_body(report: dict[str, Any]) -> dict[str, Any]:
    settings = incident_contract.rp1_request()
    model = settings.pop("model")
    return {
        "model": model,
        "messages": [{"role": "user", "content": incident_contract.report_prompt(report)}],
        "stream": False,
        **settings,
    }


def _message_content(body: Any) -> tuple[str, str | None]:
    """``(content, finish_reason)`` of the first choice; a non-completion body is an error."""
    if not isinstance(body, dict) or not body.get("choices"):
        raise ValueError(f"no completion in response body: {str(body)[:300]}")
    choice = body["choices"][0]
    return (choice.get("message") or {}).get("content") or "", choice.get("finish_reason")


async def resolve_video_length(video_url: str, supplied_seconds: float | None) -> VideoLength:
    """The caller's length when given, else the MP4 header's. Never raises: no length carries its reason."""
    if supplied_seconds is not None:
        if not video_duration.valid_duration_seconds(supplied_seconds):
            # The input model already rejects these; this guards a direct call.
            raise ValueError(f"video_duration_seconds must be a finite number above 0, got {supplied_seconds!r}")
        return VideoLength(seconds=supplied_seconds, source="request", unchecked_reason=None)
    try:
        # A client of its own: the model endpoint's bearer key must never reach the object store.
        async with httpx.AsyncClient(timeout=VIDEO_HEADER_TIMEOUT_SECONDS, follow_redirects=True) as client:
            seconds = await video_duration.url_duration_seconds(client, video_url)
        return VideoLength(seconds=seconds, source="mp4_header", unchecked_reason=None)
    except Exception as exc:  # Any failure means "length unknown", never a guess.
        reason = str(exc) if isinstance(exc, video_duration.VideoDurationError) else f"{type(exc).__name__}: {exc}"
        return VideoLength(seconds=None, source=None, unchecked_reason=f"video length unknown: {reason}")


async def run_p1(
    client: httpx.AsyncClient,
    config: IncidentReportGenConfig,
    video_url: str,
    *,
    model: str | None = None,
    reviewer_prompt: str | None = None,
    video_duration_seconds: float | None = None,
    prompt_text: str | None = None,
    telemetry: dict[str, Any] | None = None,
    context_block: str | None = None,
    marks_block: str | None = None,
) -> tuple[dict[str, Any], str, str | None]:
    """One P1 call parsed strictly. Returns ``(report, raw_content, finish_reason)``.

    With ``video_duration_seconds`` the video-bounds rules apply; without it they cannot.
    ``telemetry``, when given, receives ``p1_elapsed_ms`` (the HTTP round trip) and ``usage``.
    ``context_block`` is the caption pass's context message and ``marks_block`` the set-of-marks one (see
    :func:`p1_request_body`).
    """
    model_name = model or config.vlm_model_name
    started = time.perf_counter()

    def count(ok: bool, usage: Any = None) -> None:
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        run_provenance.record_call("p1", ok=ok, usage=usage, elapsed_ms=elapsed_ms, model=model_name)

    try:
        response = await client.post(
            _chat_completions_url(config.vlm_base_url),
            json=p1_request_body(model_name, video_url, reviewer_prompt, prompt_text, context_block, marks_block),
        )
    except Exception:
        count(False)
        raise
    if telemetry is not None:
        telemetry["p1_elapsed_ms"] = round((time.perf_counter() - started) * 1000.0, 1)
    if not 200 <= response.status_code < 300:
        count(False, run_provenance.usage_of(response))
        raise RuntimeError(f"P1 call returned HTTP {response.status_code}: {response.text[:500]}")
    # The attempt counts as successful only once its report passes the strict parse: a 200 with no answer, a
    # runaway (finish_reason=length) or a reply the contract rejects is a failed attempt in the run provenance.
    ok, usage = False, None
    try:
        body = response.json()
        usage = body.get("usage") if isinstance(body, dict) else None
        if not isinstance(body, dict) or not body.get("choices"):
            # HTTP 200 with a null body is how the video host answers a video it cannot decode.
            raise VlmNoAnswerError(
                f"no completion in response body: {str(body)[:300]}. The video model returned no answer; the video "
                "is probably in an unsupported format (for example 10-bit H.264) or unreadable."
            )
        if telemetry is not None:
            telemetry["usage"] = usage if isinstance(usage, dict) else {}
        content, finish_reason = _message_content(body)
        try:
            if not content and finish_reason == "length":
                raise IncidentExtractionError(
                    "the VLM ran out of tokens before returning the report (finish_reason=length)"
                )
            report = incident_contract.parse_report(content, video_duration_seconds=video_duration_seconds)
        except IncidentExtractionError as exc:
            raise IncidentExtractionError(
                str(exc), raw_output=content, finish_reason=finish_reason, model=model_name
            ) from exc
        ok = True
        return report, content, finish_reason
    finally:
        count(ok, usage)


# A valid report is a few KB; the P1 token cap (16384) is about 60k characters, which is what a runaway reaches.
MAX_P1_OUTPUT_CHARS = 30_000
P1_MAX_ATTEMPTS = 2


def _runaway(exc: IncidentExtractionError) -> bool:
    """Whether a rejected P1 output was cut off by length or is over the size limit, so one retry may help."""
    return exc.finish_reason == "length" or len(exc.raw_output) > MAX_P1_OUTPUT_CHARS


async def run_p1_with_retry(
    client: httpx.AsyncClient, config: IncidentReportGenConfig, video_url: str, **kwargs: Any
) -> tuple[dict[str, Any], str, str | None, list[dict[str, Any]]]:
    """``run_p1`` with one automatic retry after a runaway output; returns ``run_p1``'s result plus every attempt.

    The retry repeats the identical request: nothing in the rejected output is repaired or reused, so the
    stored report is always a fresh, strictly validated answer. Any other failure is raised at once. On final
    failure the error carries ``attempts`` and the last rejected output.
    """
    attempts: list[dict[str, Any]] = []
    for attempt in range(1, P1_MAX_ATTEMPTS + 1):
        try:
            report, raw_output, finish_reason = await run_p1(client, config, video_url, **kwargs)
        except IncidentExtractionError as exc:
            retry = attempt < P1_MAX_ATTEMPTS and _runaway(exc)
            attempts.append(
                {
                    "attempt": attempt,
                    "outcome": "retried" if retry else "failed",
                    "finish_reason": exc.finish_reason,
                    "output_chars": len(exc.raw_output),
                }
            )
            if not retry:
                exc.attempts = attempts  # type: ignore[attr-defined]
                raise
            logger.warning(
                "incident_report_gen: P1 output rejected (finish_reason=%s, %d chars); retrying once",
                exc.finish_reason,
                len(exc.raw_output),
            )
            continue
        attempts.append(
            {"attempt": attempt, "outcome": "valid", "finish_reason": finish_reason, "output_chars": len(raw_output)}
        )
        return report, raw_output, finish_reason, attempts
    raise AssertionError("unreachable: the last attempt returns or raises")  # pragma: no cover


async def run_rp1(
    client: httpx.AsyncClient, config: IncidentReportGenConfig, report: dict[str, Any]
) -> tuple[str | None, str | None]:
    """One RP1 call. Never raises: returns ``(report_text, error)``."""
    started = time.perf_counter()
    try:
        body = rp1_request_body(report)
        try:
            response = await client.post(_chat_completions_url(config.rp1_base_url), json=body)
        except Exception:
            run_provenance.record_call(
                "rp1", ok=False, usage=None, elapsed_ms=(time.perf_counter() - started) * 1000.0, model=body["model"]
            )
            raise
        succeeded = 200 <= response.status_code < 300
        run_provenance.record_call(
            "rp1",
            ok=succeeded,
            usage=run_provenance.usage_of(response),
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            model=body["model"],
        )
        if not succeeded:
            return None, f"RP1 call returned HTTP {response.status_code}: {response.text[:300]}"
        content, _ = _message_content(response.json())
        text = content.strip()
        return (text, None) if text else (None, "RP1 returned no final content")
    except Exception as exc:  # A prose-report failure must not discard a valid P1 report.
        return None, f"{type(exc).__name__}: {exc}"


async def collect_evidence(
    *,
    video_url: str,
    incident_id: str,
    report: dict[str, Any],
    store: crop_evidence.EvidenceStore | None = None,
    video_key: str | None = None,
    detector: Any | None = None,
    namer: Any | None = None,
    tracking: track_evidence.TrackingRequest | None = None,
    object_tracking: track_evidence.ObjectTrackingRequest | None = None,
) -> list[dict[str, Any]]:
    """Crop and upload every evidence item. Never raises: a failure becomes an ``image_gap`` reason.

    ``store`` and ``detector`` override the service's own (R2 and the shared detector in production) for this
    call only. ``object_tracking`` receives the summary of the instrument and asset tracks.
    """
    try:
        return await crop_evidence.get_crop_evidence_service().process_incident_report(
            video_ref=video_url,
            video_id=incident_id,
            report=report,
            store=store,
            video_key=video_key,
            detector=detector,
            namer=namer,
            tracking=tracking,
            object_tracking=object_tracking,
        )
    except Exception as exc:
        logger.warning("incident_report_gen: evidence cropping failed for %s: %s", incident_id, exc)
        if tracking is not None:
            tracking.summary.skipped = "evidence cropping failed"
        if object_tracking is not None:
            object_tracking.summary.skipped = "evidence cropping failed"
        reason = f"evidence cropping failed: {type(exc).__name__}: {exc}"
        return crop_evidence.blank_evidence_entries(report, image_gap=reason, zoom_gap=reason)


def tracking_request(tool_input: IncidentReportGenInput) -> track_evidence.TrackingRequest:
    """The tracking run of one production request: its own sensor id on the perception stack."""
    run_id = tool_input.model_run_id or f"{tool_input.incident_id}-{int(time.time())}"
    return track_evidence.TrackingRequest(
        sensor_id=track_evidence.sensor_id_for(tool_input.video_key or tool_input.incident_id, run_id)
    )


def object_tracking_request() -> track_evidence.ObjectTrackingRequest:
    """The object tracking run of one production request: its settings and its own detector-call budget."""
    settings = object_tracking.ObjectTrackingSettings.from_env()
    return track_evidence.ObjectTrackingRequest(
        budget=crop_evidence.CropCallBudget(settings.max_detector_calls),
        runner=object_tracking.run_object_tracking,
        options={"settings": settings},
    )


def crop_telemetry(evidence: list[dict[str, Any]], budget: crop_evidence.CropCallBudget, elapsed_s: float) -> dict:
    """What an experimental crop stage cost: wall time, grounding calls against the cap, images and gaps."""
    gaps: dict[str, int] = {}
    for entry in evidence:
        if entry.get("image") is None:
            reason = str(entry.get("image_gap") or "unknown").split(":")[0]
            gaps[reason] = gaps.get(reason, 0) + 1
    return {
        "elapsed_ms": round(elapsed_s * 1000.0, 1),
        "items": len(evidence),
        "images": sum(1 for entry in evidence if entry.get("image") is not None),
        "gaps": gaps,
        **budget.summary(),
    }


async def revision_embedding(report: dict[str, Any]) -> list[float] | None:
    """The summary vector stored with the AI revision. Never raises: None means the row is saved without one.

    Uses the passage embedder that the evidence embeddings use, so a revision and a search query share
    one model. Unconfigured or failed embedding is logged and leaves the revision searchable only by
    its other data, which is what a NULL vector gives.
    """
    try:
        embedder = RemoteTextEmbedder()
        text = incident_db.revision_summary_text(report)
        if not text or not embedder.is_configured():
            return None
        return await embedder.embed_passage(text)
    except Exception as exc:
        logger.warning("incident_report_gen: revision embedding failed, saving without a vector: %s", exc)
        return None


async def persist_revision(
    *,
    incident_id: str,
    model_run_id: str,
    model_name: str,
    report: dict[str, Any],
    evidence: list[dict[str, Any]],
    caller_owns_run: bool,
    note: str | None = None,
) -> int | None:
    """Best-effort: write the AI revision and return its id. Never raises - a DB outage must not break analysis.

    ``caller_owns_run`` (console agent mode) only creates a placeholder ``model_runs`` row when
    none exists, so the console's own record is never overwritten; otherwise the run is
    registered with :meth:`IncidentDB.insert_model_run` as before.
    """
    try:
        if not incident_db.is_configured():
            return None
        db = await incident_db.get_db()
        if db is None:
            return None
        if caller_owns_run:
            await db.insert_model_run_if_absent(
                model_run_id, model_name=model_name, prompt_version=incident_contract.contract_version()
            )
        else:
            await db.insert_model_run(
                model_run_id, model_name=model_name, prompt_version=incident_contract.contract_version()
            )
        row = await db.insert_ai_revision(
            incident_id,
            model_run_id=model_run_id,
            payload=report,
            evidence=evidence,
            note=note,
            embedding=await revision_embedding(report),
        )
        return int(row["id"])
    except Exception as exc:
        logger.warning("incident_report_gen: failed to persist the AI revision for %s: %s", incident_id, exc)
        return None


def _skipped_captions(reason: str) -> chunk_captions.ChunkCaptions:
    """A caption pass that did not run: no timeline, and why in ``telemetry["skipped"]``."""
    return chunk_captions.ChunkCaptions(timeline=[], telemetry={"skipped": reason})


def _caption_settings() -> chunk_captions.ChunkSettings | chunk_captions.ChunkCaptions:
    """The caption settings, or the skipped pass a rejected setting gives (logged)."""
    try:
        return chunk_captions.chunk_settings()
    except ValueError as exc:
        logger.warning("incident_report_gen: chunking settings rejected, caption pass skipped: %s", exc)
        return _skipped_captions(f"bad chunking settings: {exc}")


async def run_caption_pass(
    client: httpx.AsyncClient,
    config: IncidentReportGenConfig,
    tool_input: IncidentReportGenInput,
    model_name: str,
    video_duration_seconds: float | None,
) -> chunk_captions.ChunkCaptions | None:
    """The caption pass of a production request. Never raises.

    ``None`` when ``INCIDENT_CHUNKING_ENABLED`` is not ``1``.
    Otherwise the captions and their telemetry, or an empty timeline with ``telemetry = {"skipped": reason}``
    when the pass did not run (short clip, unknown length, ffmpeg missing, a failed window call, ...): the
    analysis goes on without context.
    """
    settings = _caption_settings()
    if isinstance(settings, chunk_captions.ChunkCaptions):
        return settings
    if not settings.enabled:
        return None
    return await _caption_pass(
        client,
        config,
        tool_input,
        model_name,
        video_duration_seconds,
        settings,
        run_id=tool_input.model_run_id or uuid.uuid4().hex[:12],
    )


async def _caption_pass(
    client: httpx.AsyncClient,
    config: IncidentReportGenConfig,
    tool_input: IncidentReportGenInput,
    model_name: str,
    video_duration_seconds: float | None,
    settings: chunk_captions.ChunkSettings,
    *,
    run_id: str,
    store: chunk_captions.ScratchStorage | None = None,
    budget: crop_evidence.CropCallBudget | None = None,
) -> chunk_captions.ChunkCaptions:
    """One caption pass with ``settings`` (``store`` None is production's R2 store). Never raises: a pass that
    could not run or finish is an empty timeline with its reason, logged."""
    if video_duration_seconds is None:
        reason = "video length unknown"
        logger.warning("incident_report_gen: caption pass skipped for %s: %s", tool_input.incident_id, reason)
        return _skipped_captions(reason)
    try:
        captions = await chunk_captions.caption_video(
            video_url=tool_input.video_url,
            video_key=tool_input.video_key or tool_input.incident_id,
            run_id=run_id,
            duration_seconds=video_duration_seconds,
            client=client,
            vlm_base_url=config.vlm_base_url,
            model=model_name,
            settings=settings,
            store=store,
            budget=budget,
        )
    except Exception as exc:
        reason = str(exc) if isinstance(exc, chunk_captions.ChunkCaptionError) else f"{type(exc).__name__}: {exc}"
        logger.warning("incident_report_gen: caption pass skipped for %s: %s", tool_input.incident_id, reason)
        return _skipped_captions(reason)
    logger.info(
        "incident_report_gen: caption pass for %s: %d windows, %d calls, %d retries, %.0f ms",
        tool_input.incident_id,
        len(captions.timeline),
        captions.telemetry["calls"],
        captions.telemetry["retries"],
        captions.telemetry["elapsed_ms"],
    )
    return captions


def experiment_scratch_root() -> str:
    """The owner-approved R2 root of an experiment's caption windows, from ``INCIDENT_EXPERIMENT_SCRATCH_PREFIX``.

    ``experiment_captions: "r2"`` is the one experimental setting that writes to shared storage, so it needs this
    explicit setting (blocker X16), a dedicated folder under ``scratch/`` other than production's ``scratch/analyze``
    (a root inside or above it is refused, as is any other top-level folder), and R2 settings on the agent.
    Raises ``ExperimentRefusedError``.
    """
    root = (os.environ.get(EXPERIMENT_SCRATCH_PREFIX_ENV) or "").strip().strip("/")
    if not root:
        raise ExperimentRefusedError(
            f"experiment_captions 'r2' needs the owner-approved scratch prefix in {EXPERIMENT_SCRATCH_PREFIX_ENV} "
            "(blocker X16); without it use experiment_captions 'inline'"
        )
    parts = root.split("/")
    production = chunk_captions.SCRATCH_PREFIX.split("/")
    if (
        parts[0] != "scratch"
        or len(parts) < 2
        or any(part in ("", ".", "..") for part in parts)
        or parts[: len(production)] == production
    ):
        raise ExperimentRefusedError(
            f"{EXPERIMENT_SCRATCH_PREFIX_ENV}={root!r} must be a dedicated folder under scratch/ other than "
            f"{chunk_captions.SCRATCH_PREFIX}/ (production's caption windows), for example scratch/eval"
        )
    if not is_r2_configured():
        raise ExperimentRefusedError("experiment_captions 'r2' needs R2 settings (R2_*) on the experiment agent")
    return root


def experiment_scratch_store(captions: str, scratch_root: str | None) -> chunk_captions.ScratchStorage:
    """The scratch store of an experimental caption pass: inline (nothing stored), or R2 confined to the approved
    root with each window deleted after its call. Never production's store."""
    if captions == "inline":
        return chunk_captions.InlineScratchStore()
    if captions != "r2" or not scratch_root:  # experiment_settings refuses both; this guards a direct call
        raise ExperimentRefusedError(f"experiment_captions {captions!r} needs an approved scratch root")
    return chunk_captions.ScratchStore(root=scratch_root, delete_after_use=True)


def caption_telemetry(
    captions: chunk_captions.ChunkCaptions,
    store: chunk_captions.ScratchStorage,
    budget: crop_evidence.CropCallBudget,
    elapsed_s: float,
) -> dict[str, Any]:
    """What an experimental caption pass cost and gave P1: its store, wall time, windows captioned, whether P1 got
    the context message, why the pass was skipped (``None`` when it ran) and its caption calls against the cap."""
    return {
        "store": store.name,
        "scratch_root": store.root if store.name == "r2" else None,
        "elapsed_ms": round(elapsed_s * 1000.0, 1),
        "windows": len(captions.timeline),
        "context_sent": bool(captions.context),
        "skipped": captions.telemetry.get("skipped"),
        **budget.summary(),
    }


async def run_experiment_caption_pass(
    client: httpx.AsyncClient,
    config: IncidentReportGenConfig,
    tool_input: IncidentReportGenInput,
    model_name: str,
    video_duration_seconds: float | None,
    experiment: dict[str, Any],
    scratch_root: str | None,
) -> chunk_captions.ChunkCaptions:
    """The caption pass an experiment asked for (``experiment_captions``). Never raises for a request that
    :func:`experiment_settings` accepted.

    It runs as production's does (the same settings, prompts, model, retries and context message) whatever
    ``INCIDENT_CHUNKING_ENABLED`` says, with the request's scratch store, its caption-call cap, and a run id of its
    own, so its scratch keys never meet another request's. A pass that could not run or finish (a short clip, a
    window over the inline limit, the cap, ...) is skipped as in production: P1 runs without context.
    ``experiment["caption"]`` receives :func:`caption_telemetry`.
    """
    store = experiment_scratch_store(experiment["captions"], scratch_root)
    budget = crop_evidence.CropCallBudget(experiment["max_caption_calls"])
    started = time.perf_counter()
    settings = _caption_settings()
    captions = (
        settings
        if isinstance(settings, chunk_captions.ChunkCaptions)
        else await _caption_pass(
            client,
            config,
            tool_input,
            model_name,
            video_duration_seconds,
            settings,
            run_id=f"exp-{uuid.uuid4().hex[:12]}",
            store=store,
            budget=budget,
        )
    )
    experiment["caption"] = caption_telemetry(captions, store, budget, time.perf_counter() - started)
    return captions


async def run_marks_pass(
    tool_input: IncidentReportGenInput, tracking: track_evidence.TrackingRequest | None
) -> set_of_marks.MarkedClip | None:
    """Set-of-marks preparation of a production request. Never raises.

    ``None`` when tracking is off or ``INCIDENT_SOM_ENABLED`` is not set (P1 then runs exactly as before).
    Otherwise the marked copy, or why there is none; the tracking run it made is reused by the evidence step, and
    ``tracking.summary.marks`` starts its record.
    """
    if tracking is None or not set_of_marks.som_enabled():
        return None
    marked = await set_of_marks.prepare_marks(
        tool_input.video_url,
        video_key=tool_input.video_key or tool_input.incident_id,
        run_id=tool_input.model_run_id or uuid.uuid4().hex[:12],
        tracking=tracking,
    )
    if marked.outcome is not None:
        tracking.reuse(marked.outcome)
    tracking.summary.marks = marked.telemetry()
    return marked


def experiment_settings(tool_input: IncidentReportGenInput, reviewer_prompt: str | None) -> dict[str, Any] | None:
    """``None`` for a production request; for an experimental one, what it uses, after every gate.

    Raises ``ExperimentsDisabledError`` unless the agent runs with ``INCIDENT_EXPERIMENTS_ENABLED=1``,
    ``ExperimentPromptError`` for an unknown or edited prompt, ``ValueError`` when combined with a
    reviewer prompt or for a cap without its setting (or the reverse), and ``ExperimentRefusedError`` when
    cropping would upload images to R2 or R2 caption windows have no approved scratch root.
    """
    captions = tool_input.experiment_captions
    if (
        tool_input.experiment_prompt_id is None
        and not tool_input.skip_crops
        and tool_input.max_crop_calls is None
        and tool_input.max_zoom_calls is None
        and not tool_input.experiment_object_tracking
        and captions is None
        and tool_input.max_caption_calls is None
    ):
        return None
    incident_experiments.require_enabled()
    if reviewer_prompt:
        raise ValueError(
            "an experimental request cannot carry a reviewer prompt (the P1 request must be the prompt alone)"
        )
    if tool_input.skip_crops and tool_input.max_crop_calls is not None:
        raise ValueError("max_crop_calls applies only when cropping runs; it cannot be combined with skip_crops")
    if tool_input.skip_crops and (tool_input.max_zoom_calls is not None or tool_input.experiment_object_tracking):
        raise ValueError("max_zoom_calls and experiment_object_tracking need cropping; drop skip_crops")
    if not tool_input.skip_crops and tool_input.max_crop_calls is None:
        raise ValueError("an experimental request with cropping on needs max_crop_calls (its crop-call cap)")
    if tool_input.max_caption_calls is not None and captions is None:
        raise ValueError("max_caption_calls applies only with experiment_captions")
    if captions is not None and tool_input.max_caption_calls is None:
        raise ValueError("experiment_captions needs max_caption_calls (its caption-call cap)")
    if captions == "r2" and not tool_input.skip_crops:
        raise ExperimentRefusedError(
            "experiment_captions 'r2' needs R2 settings, and experimental cropping refuses to run on an agent with "
            "R2 settings: set skip_crops, or use experiment_captions 'inline' on an agent without R2 settings"
        )
    if not tool_input.skip_crops and is_r2_configured():
        # Defence in depth: experimental crops go to a temporary in-memory store, and an experiment agent
        # must not hold shared-storage settings at all.
        raise ExperimentRefusedError(
            "experimental cropping refuses to run on an agent with R2 settings; run the experiment agent without "
            "R2 settings (its crops go to a temporary in-memory store) or set skip_crops"
        )
    # The single shared-storage write an experiment may make: R2 caption windows under the approved root.
    scratch_root = experiment_scratch_root() if captions == "r2" else None
    settings: dict[str, Any] = {"prompt_id": None, "prompt_sha256": incident_experiments.production_prompt_sha256()}
    if tool_input.experiment_prompt_id is not None:
        text, digest = incident_experiments.resolve_prompt(tool_input.experiment_prompt_id)
        settings = {"prompt_id": tool_input.experiment_prompt_id, "prompt_sha256": digest, "_prompt_text": text}
    return {
        **settings,
        "skip_crops": tool_input.skip_crops,
        "max_crop_calls": tool_input.max_crop_calls,
        "max_zoom_calls": tool_input.max_zoom_calls,
        "object_tracking": tool_input.experiment_object_tracking,
        "captions": captions,
        "max_caption_calls": tool_input.max_caption_calls,
        "persisted": False,
        **({"_scratch_root": scratch_root} if scratch_root else {}),
    }


@register_function(config_type=IncidentReportGenConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def incident_report_gen(config: IncidentReportGenConfig, builder: Builder) -> AsyncGenerator[FunctionInfo]:  # noqa: ARG001
    """Contract P1 -> RP1 over one uploaded video, then evidence crops and a new AI revision."""

    def _registered(*prompt_ids: str) -> dict[str, str]:
        return {prompt_id: incident_contract.prompt_sha256(prompt_id) for prompt_id in prompt_ids}

    async def _analyze(
        tool_input: IncidentReportGenInput, provenance: run_provenance.RunProvenance
    ) -> IncidentReportGenOutput:
        model_name = tool_input.model or config.vlm_model_name
        if tool_input.model is not None and tool_input.model not in incident_contract.allowed_models():
            raise ValueError(f"Model {tool_input.model!r} is not in the incident contract allowlist")
        reviewer_prompt = normalize_reviewer_prompt(tool_input.reviewer_prompt)
        experiment = experiment_settings(tool_input, reviewer_prompt)
        prompt_text = experiment.pop("_prompt_text", None) if experiment else None
        scratch_root = experiment.pop("_scratch_root", None) if experiment else None
        telemetry: dict[str, Any] | None = {} if experiment else None
        p1_prompt_sha256 = (experiment or {}).get("prompt_sha256") or incident_experiments.production_prompt_sha256()
        provenance.use("p1", model=model_name, prompts={"contract/incident_extraction_prompt": p1_prompt_sha256})
        if reviewer_prompt:
            provenance.use("p1", prompts=_registered("p1/reviewer-preamble"))
        if not tool_input.skip_rp1:
            rp1_prompt_sha256 = hashlib.sha256(incident_contract.report_prompt_template().encode("utf-8")).hexdigest()
            provenance.use(
                "rp1",
                model=str(incident_contract.rp1_request()["model"]),
                prompts={"contract/report_generation_prompt": rp1_prompt_sha256},
            )
        video_length = await resolve_video_length(tool_input.video_url, tool_input.video_duration_seconds)
        if video_length.seconds is None:
            logger.warning(
                "incident_report_gen: video-bounds rules not applied for %s (%s)",
                tool_input.incident_id,
                video_length.unchecked_reason,
            )
        api_key = os.environ.get(config.api_key_env, "")
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        # Only production requests track; experiments (skip_crops included) never do.
        tracking = tracking_request(tool_input) if experiment is None and track_evidence.tracking_enabled() else None
        # Object tracks (instruments and assets) are a production request's too, and need the crops.
        objects = (
            object_tracking_request()
            if not tool_input.skip_crops
            and (
                (experiment is None and object_tracking.object_tracking_enabled())
                or (experiment is not None and experiment["object_tracking"])
            )
            else None
        )
        async with httpx.AsyncClient(timeout=config.request_timeout_seconds, headers=headers) as client:
            captions: chunk_captions.ChunkCaptions | None = None
            marked: set_of_marks.MarkedClip | None = None
            if experiment is None:
                captions, marked = await asyncio.gather(
                    run_caption_pass(client, config, tool_input, model_name, video_length.seconds),
                    run_marks_pass(tool_input, tracking),
                )
            elif experiment["captions"] is not None:
                captions = await run_experiment_caption_pass(
                    client, config, tool_input, model_name, video_length.seconds, experiment, scratch_root
                )
            marked_url = marked.url if marked is not None else None
            # What this request runs, whatever the agent's settings say (an experiment runs the caption pass only
            # when it asks for it, and never tracking or marks): the provenance flags must describe the run.
            provenance.ran(
                experiment=experiment is not None,
                chunking=captions is not None and not captions.telemetry.get("skipped"),
                chunking_skipped=captions.telemetry.get("skipped") if captions is not None else None,
                tracking=tracking is not None,
                set_of_marks=marked is not None and bool(marked_url),
                object_tracking=objects is not None,
                zoom_names=crop_evidence.zoom_names_enabled() and not tool_input.skip_crops,
            )
            if captions is not None and not captions.telemetry.get("skipped"):
                provenance.use("caption", model=model_name, prompts=_registered(*CAPTION_PROMPT_IDS))
                if captions.context:
                    provenance.use("p1", prompts=_registered("caption/context-preamble"))
            if marked is not None and marked_url:
                provenance.use("p1", prompts=_registered("p1/marks-preamble"))
            try:
                report, raw_output, finish_reason, p1_attempts = await run_p1_with_retry(
                    client,
                    config,
                    SignedUrl(marked_url) if marked_url else tool_input.video_url,
                    model=model_name,
                    reviewer_prompt=reviewer_prompt,
                    video_duration_seconds=video_length.seconds,
                    prompt_text=prompt_text,
                    telemetry=telemetry,
                    context_block=captions.context if captions else None,
                    marks_block=set_of_marks.marks_context(marked.marks) if marked is not None and marked_url else None,
                )
            except IncidentExtractionError as exc:
                if experiment is not None:  # the caller must still see which prompt produced the rejected output
                    exc.experiment = experiment  # type: ignore[attr-defined]
                    exc.telemetry = telemetry  # type: ignore[attr-defined]
                exc.provenance = provenance.to_dict(  # type: ignore[attr-defined]
                    contract_version=incident_contract.contract_version()
                )
                raise
            except httpx.TimeoutException as exc:
                raise TimeoutError(f"P1 call timed out after {config.request_timeout_seconds}s") from exc
            finally:
                await set_of_marks.cleanup_marks(marked)
            if marked is not None and marked_url and tracking is not None and tracking.summary.marks is not None:
                # Before RP1 and the evidence step, so neither the prose nor the stored labels carry the number.
                tracking.marked, tracking.summary.marks["invalid"] = set_of_marks.apply_marks(report, marked.marks)
            report_text, report_text_error = (
                (None, None) if tool_input.skip_rp1 else await run_rp1(client, config, report)
            )
        if experiment is not None:
            # The validated P1 report before the zoomed second look rewrites names and descriptions in place.
            experiment["p1_report"] = copy.deepcopy(report)

        if not tool_input.skip_crops:
            provenance.use("grounding", prompts=_registered("grounding/best-match"))
            if crop_evidence.zoom_names_enabled():
                provenance.use("zoom", prompts=_registered(*ZOOM_PROMPT_IDS))
            if objects is not None:
                provenance.use("object_tracking", prompts=_registered("grounding/best-match"))
        if tool_input.skip_crops:
            # No crop means no located item, so the zoomed second look is off as well.
            evidence = crop_evidence.blank_evidence_entries(
                report,
                image_gap="evidence cropping skipped (skip_crops)",
                zoom_gap="zoomed second look skipped (skip_crops)",
            )
        elif experiment is not None:
            # An experimental crop run uses its own temporary in-memory store: isolated from every other
            # request, holding no production data, never written to disk or R2, and discarded on return.
            # Its grounding calls draw on the request's own cap, checked before every HTTP attempt.
            budget = crop_evidence.CropCallBudget(experiment["max_crop_calls"])
            service = crop_evidence.get_crop_evidence_service()
            detector = crop_evidence.with_call_budget(service.detector, budget)
            zoom_budget = (
                crop_evidence.CropCallBudget(experiment["max_zoom_calls"]) if experiment["max_zoom_calls"] else None
            )
            namer = crop_evidence.with_zoom_budget(service.namer, zoom_budget) if zoom_budget is not None else None
            crop_started = time.perf_counter()
            evidence = await collect_evidence(
                video_url=tool_input.video_url,
                incident_id=tool_input.incident_id,
                report=report,
                store=crop_evidence.InMemoryEvidenceStore(),
                video_key=tool_input.video_key,
                detector=detector,
                namer=namer,
                object_tracking=objects,
            )
            experiment["evidence_store"] = EXPERIMENT_EVIDENCE_STORE
            experiment["crop"] = crop_telemetry(evidence, budget, time.perf_counter() - crop_started)
            if zoom_budget is not None:
                experiment["zoom"] = zoom_budget.summary()
            # experiment["object_tracking"] stays the request's setting (a bool); the tracker's summary is the
            # response's own object_tracking field, as for a production request.
        else:
            evidence = await collect_evidence(
                video_url=tool_input.video_url,
                incident_id=tool_input.incident_id,
                report=report,
                video_key=tool_input.video_key,
                tracking=tracking,
                object_tracking=objects,
            )
        if objects is not None:
            logger.info(
                "incident_report_gen: object tracking for %s: %s", tool_input.incident_id, objects.summary.to_dict()
            )
        if tracking is not None:
            logger.info("incident_report_gen: tracking for %s: %s", tool_input.incident_id, tracking.summary.to_dict())
            for entry in evidence:
                if entry.get("track"):
                    logger.info(
                        "incident_report_gen: %s matched track %s (%s s to %s s), frame %s at %s s",
                        entry["item_id"],
                        entry["track"],
                        entry.get("track_first_seen_seconds"),
                        entry.get("track_last_seen_seconds"),
                        entry.get("frame"),
                        entry.get("box_seconds"),
                    )
        # An experimental request never writes a report revision: its output is an experiment result.
        revision_id = (
            None
            if experiment is not None
            else await persist_revision(
                incident_id=tool_input.incident_id,
                model_run_id=tool_input.model_run_id or config.model_run_id,
                model_name=model_name,
                report=report,
                evidence=evidence,
                caller_owns_run=tool_input.model_run_id is not None,
                note=f"Reviewer prompt: {reviewer_prompt}" if reviewer_prompt else None,
            )
        )

        return IncidentReportGenOutput(
            report=report,
            report_text=report_text,
            report_text_error=report_text_error,
            model=model_name,
            contract_version=incident_contract.contract_version(),
            raw_output=raw_output,
            finish_reason=finish_reason,
            p1_attempts=p1_attempts,
            revision_id=revision_id,
            evidence=evidence,
            video_duration_seconds=video_length.seconds,
            video_duration_source=video_length.source,
            video_bounds_checked=video_length.seconds is not None,
            video_bounds_unchecked_reason=video_length.unchecked_reason,
            experiment=experiment,
            p1_elapsed_ms=(telemetry or {}).get("p1_elapsed_ms"),
            usage=(telemetry or {}).get("usage") if telemetry is not None else None,
            tracking=tracking.summary.to_dict() if tracking is not None else None,
            object_tracking=objects.summary.to_dict() if objects is not None else None,
            provenance=provenance.to_dict(contract_version=incident_contract.contract_version()),
        )

    async def _incident_report_gen(tool_input: IncidentReportGenInput) -> IncidentReportGenOutput:
        with run_provenance.collecting() as provenance:
            return await _analyze(tool_input, provenance)

    yield FunctionInfo.create(
        single_fn=_incident_report_gen,
        description=(
            "Extract a structured incident report (incident-contract) from an uploaded video given its signed URL, "
            "write a prose report, crop its evidence to R2 and append it as a new AI report revision when the "
            "incident DB is configured (best-effort)."
        ),
        input_schema=IncidentReportGenInput,
        single_output_schema=IncidentReportGenOutput,
    )
