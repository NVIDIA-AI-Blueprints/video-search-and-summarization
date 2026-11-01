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

"""Chunked captioning for the incident ``/analyze`` path (a first pass before P1).

Through the Switchyard gateway the Nano video model samples a fixed 2 frames per second and caps at about
60 frames, ignoring ``num_frames``, so a clip longer than 30 s is seen ever more sparsely and a short action
(a fall lasting under a second) is missed. This module cuts the clip into overlapping windows with ffmpeg,
uploads each window under ``scratch/analyze/<video key>/<run id>/<index>.mp4``, captions every window with
NVIDIA's stock caption prompt (one VLM call per window, at most ``concurrency`` in flight, 429 and transient
errors retried), offsets the ``[Xs-Ys]`` stamps to whole-video seconds, and builds the context block that
``incident_report_gen`` appends to the P1 request as one more user message. The scratch prefix is deleted in
a ``finally`` block whether or not the pass succeeded.

The prompts and the stamp helpers are copies of ``tools/video_report_gen.py`` and the base profile's
``config.yml`` (``video_understanding.system_prompt``, ``video_report_gen.vlm_prompt``); those tools are
bound to VST, the NAT object store and a LangChain VLM, none of which the incident profile has.

Everything here is best-effort for the caller: :func:`caption_video` raises :class:`ChunkCaptionError` with a
plain reason (ffmpeg missing, a window call that failed after retries, ...) and ``incident_report_gen`` turns
that into a logged skip reason and runs P1 without context.
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
import logging
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
from typing import Any
from typing import Protocol

import httpx

from vss_agents import incident_contract
from vss_agents.utils import run_provenance
from vss_agents.utils.r2 import get_r2_client
from vss_agents.utils.r2 import get_r2_presigned_url
from vss_agents.utils.r2 import is_r2_configured
from vss_agents.utils.retry import create_retry_strategy

logger = logging.getLogger(__name__)

# Settings: read from the environment like the other incident settings (INCIDENT_EXPERIMENTS_ENABLED).
CHUNKING_FLAG = "INCIDENT_CHUNKING_ENABLED"
WINDOW_ENV = "INCIDENT_CHUNK_WINDOW_SECONDS"
OVERLAP_ENV = "INCIDENT_CHUNK_OVERLAP_SECONDS"
MIN_CLIP_ENV = "INCIDENT_CHUNK_MIN_CLIP_SECONDS"
NUM_FRAMES_ENV = "INCIDENT_CHUNK_NUM_FRAMES"
CONCURRENCY_ENV = "INCIDENT_CHUNK_CONCURRENCY"

DEFAULT_WINDOW_SECONDS = 20.0
DEFAULT_OVERLAP_SECONDS = 2.0
DEFAULT_MIN_CLIP_SECONDS = 30.0
DEFAULT_NUM_FRAMES = 32
DEFAULT_CONCURRENCY = 4
# A trailing window shorter than this is merged into the previous one (the model answers a 0.2 s clip with
# the prompt's own placeholder text).
MIN_REMAINDER_SECONDS = 3.0
# NVIDIA's summariser drops caption lines under 2 s; a fall lasts well under that, so only stamp noise
# (zero-length or shorter than half a second) is dropped here.
MIN_EVENT_SECONDS = 0.5
# One caption call: the stock video_understanding settings (temperature 0, 4096 tokens) and the presign
# lifetime of a scratch window (a pass takes seconds; an hour leaves nothing usable behind for long).
CAPTION_MAX_TOKENS = 4096
CAPTION_RETRIES = 3
SCRATCH_URL_SECONDS = 3600
DOWNLOAD_TIMEOUT_SECONDS = 120.0
FFMPEG_TIMEOUT_SECONDS = 120.0
SCRATCH_PREFIX = "scratch/analyze"

# The base profile's video_understanding.system_prompt (dev-profile-base/vss-agent/configs/config.yml).
CAPTION_SYSTEM_PROMPT = incident_contract.prompt("caption/system")

# The base profile's video_report_gen.vlm_prompt, registered in contracts/prompts/ (caption/window).
CAPTION_PROMPT = incident_contract.prompt("caption/window")

# tools/video_report_gen.py CHUNK_TIMESTAMP_PROMPT, verbatim, registered with {{start_time}} and {{end_time}}.
CHUNK_TIMESTAMP_PROMPT_ID = "caption/time-range"

CONTEXT_PREAMBLE = incident_contract.prompt("caption/context-preamble")

# [Xs-Ys] stamps, as tools/video_report_gen.py matches them (optional ** around the brackets).
_STAMP_PATTERN = re.compile(
    r"(?:\*\*\s*)?"  # optional leading ** and spaces
    r"\[\s*"  # literal [
    r"(\d+(?:\.\d+)?)(?:s)?"  # group 1: start time
    r"\s*-\s*"
    r"(\d+(?:\.\d+)?)(?:s)?"  # group 2: end time
    r"\s*\]"  # literal ]
    r"(?:\s*\*\*)?"  # optional trailing ** and spaces
)
_TIMESTAMP_RANGE_PATTERN = re.compile(r"\[\s*(\d+(?:\.\d+)?)(?:s)?\s*-\s*(\d+(?:\.\d+)?)(?:s)?\s*\]")
# A window the model described as nothing happening; every event of the window must match for it to be dropped.
_EMPTY_WINDOW_PATTERN = re.compile(
    r"\b(static|no (?:significant |notable |visible |apparent )?(?:activity|activities|movement|motion|action|"
    r"people|persons|individuals|changes?|events?)|nothing (?:happens|occurs|is happening)|"
    r"remains? (?:unchanged|empty|still|the same)|unchanged|uneventful|empty hallway|no one (?:is )?(?:present|visible))\b",
    re.IGNORECASE,
)


class ChunkCaptionError(RuntimeError):
    """The caption pass could not run or finish; the message is the plain skip reason the agent logs."""


@dataclass(frozen=True)
class ChunkSettings:
    """The caption-pass settings read from the environment."""

    enabled: bool = False
    window_seconds: float = DEFAULT_WINDOW_SECONDS
    overlap_seconds: float = DEFAULT_OVERLAP_SECONDS
    min_clip_seconds: float = DEFAULT_MIN_CLIP_SECONDS
    num_frames: int = DEFAULT_NUM_FRAMES
    concurrency: int = DEFAULT_CONCURRENCY


def _env_number(name: str, default: float, *, minimum: float, integer: bool = False) -> float | int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return int(default) if integer else default
    try:
        value: float | int = int(raw) if integer else float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}, got {value}")
    return value


def chunk_settings() -> ChunkSettings:
    """The settings from ``INCIDENT_CHUNKING_ENABLED`` and ``INCIDENT_CHUNK_*``; ``ValueError`` on a bad value."""
    window = float(_env_number(WINDOW_ENV, DEFAULT_WINDOW_SECONDS, minimum=1.0))
    overlap = float(_env_number(OVERLAP_ENV, DEFAULT_OVERLAP_SECONDS, minimum=0.0))
    if overlap >= window:
        raise ValueError(f"{OVERLAP_ENV} ({overlap}) must be smaller than {WINDOW_ENV} ({window})")
    return ChunkSettings(
        enabled=os.environ.get(CHUNKING_FLAG, "") == "1",
        window_seconds=window,
        overlap_seconds=overlap,
        min_clip_seconds=float(_env_number(MIN_CLIP_ENV, DEFAULT_MIN_CLIP_SECONDS, minimum=0.0)),
        num_frames=int(_env_number(NUM_FRAMES_ENV, DEFAULT_NUM_FRAMES, minimum=1, integer=True)),
        concurrency=int(_env_number(CONCURRENCY_ENV, DEFAULT_CONCURRENCY, minimum=1, integer=True)),
    )


@dataclass(frozen=True)
class Window:
    index: int
    start: float
    end: float

    @property
    def length(self) -> float:
        return self.end - self.start


def plan_windows(
    duration_seconds: float,
    *,
    window_seconds: float = DEFAULT_WINDOW_SECONDS,
    overlap_seconds: float = DEFAULT_OVERLAP_SECONDS,
    min_clip_seconds: float = DEFAULT_MIN_CLIP_SECONDS,
) -> list[Window]:
    """The windows to caption: ``[0, w), [w - o, 2w - o), ...`` up to the end of the clip.

    A clip of ``min_clip_seconds`` or shorter gets no windows (one whole-clip call already sees it at the
    full frame rate). A trailing window shorter than :data:`MIN_REMAINDER_SECONDS` is merged into the previous
    one, so the model is never asked to caption a fraction of a second.
    """
    if duration_seconds <= 0 or duration_seconds <= min_clip_seconds:
        return []
    if overlap_seconds >= window_seconds:
        raise ValueError(f"overlap ({overlap_seconds}) must be smaller than the window ({window_seconds})")
    step = window_seconds - overlap_seconds
    windows: list[Window] = []
    start = 0.0
    while True:
        end = min(start + window_seconds, duration_seconds)
        windows.append(Window(index=len(windows), start=round(start, 3), end=round(end, 3)))
        if end >= duration_seconds:
            break
        start += step
    if len(windows) > 1 and windows[-1].length < MIN_REMAINDER_SECONDS:
        last = windows.pop()
        previous = windows.pop()
        windows.append(Window(index=previous.index, start=previous.start, end=last.end))
    return windows


def clean_caption(content: str) -> str:
    """The caption without ``<think>`` blocks, ``<answer>`` tags or code fences (``_clean_vlm_response``)."""
    cleaned = content.strip()
    cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
    if cleaned.strip().lower().startswith("<think>"):
        cleaned = re.sub(r"^<think>\s*", "", cleaned, flags=re.IGNORECASE)
    think_end = re.search(r"</think>", cleaned, flags=re.IGNORECASE)
    if think_end:
        cleaned = cleaned[think_end.end() :].strip()
    answer = re.search(r"<answer>(.*?)</answer>", cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = answer.group(1).strip() if answer else re.sub(r"</?answer>", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.strip()
    for prefix in ("```markdown", "```plaintext", "```text", "```"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :].strip()
            break
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3].strip()
    return cleaned


def offset_chunk_timestamps(content: str, chunk_start: float, chunk_end: float) -> str:
    """``[Xs-Ys]`` stamps rewritten to whole-video seconds (``_normalize_chunk_timestamps``, verbatim logic).

    The model stamps a window from 0 s. When its largest end stamp runs past the window length every stamp
    is scaled down by that ratio first; then the window start is added to each stamp.
    """
    matches_data: list[tuple[re.Match[str], float, float]] = []
    max_end_sec = 0.0
    for match in re.finditer(_STAMP_PATTERN, content):
        start_sec = float(match.group(1))
        end_sec = float(match.group(2))
        matches_data.append((match, start_sec, end_sec))
        max_end_sec = max(max_end_sec, end_sec)
    chunk_duration = chunk_end - chunk_start
    if not matches_data or chunk_duration <= 0:
        return content
    ratio = max_end_sec / chunk_duration
    should_normalize = ratio > 1.0
    if should_normalize:
        logger.info(
            "chunk_captions: scaling stamps of window %.1f-%.1f s (max end %.1f s, ratio %.2f)",
            chunk_start,
            chunk_end,
            max_end_sec,
            ratio,
        )
    result = content
    for match, start_sec, end_sec in matches_data:
        if should_normalize:
            start_sec /= ratio
            end_sec /= ratio
        replacement = f"[{chunk_start + start_sec:.1f}s-{chunk_start + end_sec:.1f}s]"
        result = result.replace(match.group(0), replacement, 1)
    return result


def filter_short_duration_lines(content: str, min_duration_seconds: float = MIN_EVENT_SECONDS) -> str:
    """Lines whose every ``[Xs-Ys]`` span is shorter than ``min_duration_seconds`` are dropped.

    ``_filter_short_duration_from_markdown`` with a lower default: a fall lasts under NVIDIA's 2 s cut.
    """
    if not content:
        return content
    kept: list[str] = []
    for line in content.split("\n"):
        matches = list(_TIMESTAMP_RANGE_PATTERN.finditer(line))
        if not matches or any(float(m.group(2)) - float(m.group(1)) >= min_duration_seconds for m in matches):
            kept.append(line)
        else:
            logger.info("chunk_captions: dropped a short-duration line: %s", line.strip()[:100])
    return "\n".join(kept)


def parse_caption_events(caption: str) -> list[dict[str, Any]]:
    """``[{start, end, text}]`` for every ``[Xs-Ys]`` stamp (``_parse_timestamps``, keeping both ends and the text).

    The text of an event runs from its stamp to the next stamp, so prose between stamps belongs to the
    event before it. Prose before the first stamp belongs to no event.
    """
    matches = list(_STAMP_PATTERN.finditer(caption))
    events: list[dict[str, Any]] = []
    for position, match in enumerate(matches):
        text_end = matches[position + 1].start() if position + 1 < len(matches) else len(caption)
        text = " ".join(caption[match.end() : text_end].split())
        events.append({"start": float(match.group(1)), "end": float(match.group(2)), "text": text})
    return events


def window_is_empty(events: list[dict[str, Any]]) -> bool:
    """Whether the model saw nothing happen in the window (no events, or every event says so)."""
    if not events:
        return True
    return all(_EMPTY_WINDOW_PATTERN.search(event["text"]) for event in events)


def context_block(timeline: list[dict[str, Any]]) -> str | None:
    """The context message for P1, or ``None`` when every window was empty.

    One paragraph per non-empty window: its bounds on one line, then its caption with absolute stamps.
    """
    paragraphs = [
        f"[{entry['start']:.1f}s-{entry['end']:.1f}s]\n{entry['caption']}"
        for entry in timeline
        if not window_is_empty(entry["events"])
    ]
    if not paragraphs:
        return None
    return CONTEXT_PREAMBLE + "\n\n" + "\n\n".join(paragraphs)


def caption_request_body(model: str, video_url: str, window_length: float, num_frames: int) -> dict[str, Any]:
    """One caption call: the stock system prompt, the window as a signed URL, then the stock caption prompt."""
    time_range = incident_contract.render_prompt(
        CHUNK_TIMESTAMP_PROMPT_ID, start_time=0, end_time=round(window_length, 1)
    )
    prompt = CAPTION_PROMPT + time_range
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": CAPTION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "video_url", "video_url": {"url": video_url}},
                    {"type": "text", "text": prompt},
                ],
            },
        ],
        "stream": False,
        "temperature": 0.0,
        "max_tokens": CAPTION_MAX_TOKENS,
        "media_io_kwargs": {"video": {"num_frames": num_frames}},
    }


class CaptionCallRetryableError(RuntimeError):
    """A caption call answer worth retrying: HTTP 429 or a 5xx."""


class ScratchStorage(Protocol):
    """Where a caption pass keeps its window clips while the model fetches them (blocker X16 for experiments).

    ``put_video`` stores one window and returns its key, ``signed_url`` gives the model a URL it can fetch, and
    ``delete_prefix`` removes a pass's windows (it runs in a ``finally`` block, whether or not the pass succeeded).
    """

    name: str

    async def put_video(self, key: str, path: Path) -> str: ...

    async def signed_url(self, key: str) -> str: ...

    async def delete_prefix(self, prefix: str) -> int: ...


class ScratchStoreError(ChunkCaptionError):
    """A scratch store refused a key, a prefix or a window (outside its root, or too large to inline)."""


class ScratchStore:
    """The R2 scratch prefix of one caption pass: upload, presign and delete through ``utils/r2.py``'s client.

    The store is confined to ``root`` (default ``scratch/analyze``, the production prefix). It refuses a key or a
    delete prefix outside ``root``, and a delete of ``root`` itself or of one level under it, so a pass can only
    ever touch ``<root>/<video key>/<run id>/``. An evaluation run would use its own approved root (for example
    ``scratch/eval``, blocker X16) with the same guard.
    """

    name = "r2"

    def __init__(self, bucket: str | None = None, client: Any = None, root: str = SCRATCH_PREFIX) -> None:
        self.bucket = bucket or (os.getenv("R2_BUCKET") or "").strip()
        self._client = client
        self.root = root.strip("/")
        if not self.root:
            raise ScratchStoreError("a scratch store needs a non-empty root prefix")

    def _get_client(self) -> Any:
        return self._client if self._client is not None else get_r2_client()

    def _check(self, key_or_prefix: str, *, min_depth: int) -> None:
        inside = key_or_prefix.startswith(self.root + "/")
        depth = len([part for part in key_or_prefix[len(self.root) + 1 :].split("/") if part]) if inside else 0
        if not inside or depth < min_depth:
            raise ScratchStoreError(f"{key_or_prefix!r} is outside this scratch store's root {self.root + '/'!r}")

    async def put_video(self, key: str, path: Path) -> str:
        self._check(key, min_depth=1)
        await asyncio.to_thread(
            self._get_client().put_object, Bucket=self.bucket, Key=key, Body=path.read_bytes(), ContentType="video/mp4"
        )
        return key

    async def signed_url(self, key: str) -> str:
        if self._client is None:
            return await asyncio.to_thread(get_r2_presigned_url, key, SCRATCH_URL_SECONDS)
        return str(
            self._client.generate_presigned_url(
                "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=SCRATCH_URL_SECONDS
            )
        )

    async def delete_prefix(self, prefix: str) -> int:
        """Delete every object under ``prefix`` (at least two levels under the root) and return how many."""
        self._check(prefix, min_depth=2)

        def _delete() -> int:
            client = self._get_client()
            deleted = 0
            for page in client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix):
                keys = [{"Key": obj["Key"]} for obj in page.get("Contents") or []]
                if keys:
                    client.delete_objects(Bucket=self.bucket, Delete={"Objects": keys, "Quiet": True})
                    deleted += len(keys)
            return deleted

        return await asyncio.to_thread(_delete)


class InlineScratchStore:
    """A scratch store that keeps nothing: each window goes to the model inline, as a ``data:video/mp4;base64`` URL.

    It needs no storage at all, so an experiment agent without R2 could run the caption pass (blocker X16). Whether
    an endpoint accepts a base64 video URL, and up to what size, is unverified (it needs a model call), so a window
    larger than ``max_bytes`` is refused rather than sent.
    """

    name = "inline"

    def __init__(self, max_bytes: int = 8 * 1024 * 1024) -> None:
        self.max_bytes = max_bytes
        self._windows: dict[str, bytes] = {}

    async def put_video(self, key: str, path: Path) -> str:
        data = path.read_bytes()
        if len(data) > self.max_bytes:
            raise ScratchStoreError(f"window {key!r} is {len(data)} bytes, over the inline limit of {self.max_bytes}")
        self._windows[key] = data
        return key

    async def signed_url(self, key: str) -> str:
        return "data:video/mp4;base64," + base64.b64encode(self._windows[key]).decode("ascii")

    async def delete_prefix(self, prefix: str) -> int:
        keys = [key for key in self._windows if key.startswith(prefix)]
        for key in keys:
            del self._windows[key]
        return len(keys)


@dataclass
class ChunkCaptions:
    """What a caption pass produced: the timeline for the response and notes, and its telemetry."""

    timeline: list[dict[str, Any]]
    telemetry: dict[str, Any]
    context: str | None = None


def scratch_prefix(video_key: str, run_id: str) -> str:
    return f"{SCRATCH_PREFIX}/{video_key.strip('/')}/{run_id}/"


def ffmpeg_path() -> str | None:
    return shutil.which("ffmpeg")


async def download_video(video_url: str, target: Path) -> None:
    """One download of the signed URL, with a client of its own (no model key on the object store)."""
    async with (
        httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT_SECONDS, follow_redirects=True) as client,
        client.stream("GET", video_url) as response,
    ):
        if response.status_code >= 400:
            raise ChunkCaptionError(f"downloading the video returned HTTP {response.status_code}")
        with target.open("wb") as handle:
            async for chunk in response.aiter_bytes():
                handle.write(chunk)


async def cut_window(ffmpeg: str, source: Path, window: Window, target: Path) -> None:
    """Re-encode ``window`` of ``source`` into ``target`` (precise cut, no audio)."""
    args = [
        ffmpeg,
        "-y",
        "-loglevel",
        "error",
        "-ss",
        f"{window.start:.3f}",
        "-i",
        str(source),
        "-t",
        f"{window.length:.3f}",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-movflags",
        "+faststart",
        str(target),
    ]
    process = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    try:
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=FFMPEG_TIMEOUT_SECONDS)
    except TimeoutError:
        process.kill()
        raise ChunkCaptionError(f"ffmpeg timed out cutting window {window.index}") from None
    if process.returncode != 0:
        detail = stderr.decode(errors="replace").strip().splitlines()[-1:] or ["no output"]
        raise ChunkCaptionError(f"ffmpeg failed on window {window.index}: {detail[0][:200]}")


async def caption_window(
    client: httpx.AsyncClient,
    chat_url: str,
    model: str,
    window: Window,
    signed_url: str,
    num_frames: int,
    semaphore: asyncio.Semaphore,
    telemetry: dict[str, Any],
) -> dict[str, Any]:
    """Caption one window with retries on 429, 5xx and transport errors; returns the timeline entry."""
    body = caption_request_body(model, signed_url, window.length, num_frames)
    attempts = 0
    content = ""
    async with semaphore:
        async for attempt in create_retry_strategy(
            retries=CAPTION_RETRIES, exceptions=(CaptionCallRetryableError, httpx.TransportError)
        ):
            with attempt:
                attempts += 1
                started = time.perf_counter()
                try:
                    response = await client.post(chat_url, json=body)
                except Exception:
                    run_provenance.record_call(
                        "caption",
                        ok=False,
                        usage=None,
                        model=model,
                        elapsed_ms=(time.perf_counter() - started) * 1000.0,
                    )
                    raise
                answered = response.status_code < 400
                run_provenance.record_call(
                    "caption",
                    ok=answered,
                    usage=run_provenance.usage_of(response),
                    model=model,
                    elapsed_ms=(time.perf_counter() - started) * 1000.0,
                )
                if response.status_code == 429 or response.status_code >= 500:
                    raise CaptionCallRetryableError(
                        f"window {window.index} caption call returned HTTP {response.status_code}: {response.text[:200]}"
                    )
                if response.status_code >= 400:
                    raise ChunkCaptionError(
                        f"window {window.index} caption call returned HTTP {response.status_code}: {response.text[:200]}"
                    )
                payload = response.json()
                if not isinstance(payload, dict) or not payload.get("choices"):
                    raise ChunkCaptionError(f"window {window.index} caption call returned no completion")
                usage = payload.get("usage") or {}
                telemetry["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
                telemetry["completion_tokens"] += int(usage.get("completion_tokens") or 0)
                content = (payload["choices"][0].get("message") or {}).get("content") or ""
    telemetry["calls"] += attempts
    telemetry["retries"] += attempts - 1
    caption = filter_short_duration_lines(offset_chunk_timestamps(clean_caption(content), window.start, window.end))
    return {"start": window.start, "end": window.end, "caption": caption, "events": parse_caption_events(caption)}


async def caption_video(
    *,
    video_url: str,
    video_key: str,
    run_id: str,
    duration_seconds: float,
    client: httpx.AsyncClient,
    vlm_base_url: str,
    model: str,
    settings: ChunkSettings,
    store: ScratchStorage | None = None,
) -> ChunkCaptions:
    """The whole caption pass: plan, cut, upload, caption, then delete the scratch prefix.

    ``client`` is the model client (bearer key set) ``run_p1`` uses; the video download uses a client of its
    own. Raises :class:`ChunkCaptionError` with a plain reason when the pass cannot run or a window failed
    after its retries; the scratch prefix is deleted either way.
    """
    windows = plan_windows(
        duration_seconds,
        window_seconds=settings.window_seconds,
        overlap_seconds=settings.overlap_seconds,
        min_clip_seconds=settings.min_clip_seconds,
    )
    if not windows:
        raise ChunkCaptionError(
            f"clip is {duration_seconds:.1f} s, at or under {MIN_CLIP_ENV} ({settings.min_clip_seconds:g} s)"
        )
    ffmpeg = ffmpeg_path()
    if ffmpeg is None:
        raise ChunkCaptionError("ffmpeg is not installed on the agent host")
    if store is None and not is_r2_configured():
        raise ChunkCaptionError("R2 is not configured on the agent (scratch windows need R2_* settings)")
    if store is None:
        store = ScratchStore()
    started = time.perf_counter()
    telemetry: dict[str, Any] = {
        "window_seconds": settings.window_seconds,
        "overlap_seconds": settings.overlap_seconds,
        "num_frames": settings.num_frames,
        "model": model,
        "calls": 0,
        "retries": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "elapsed_ms": 0.0,
    }
    prefix = scratch_prefix(video_key, run_id)
    chat_url = f"{vlm_base_url.rstrip('/')}/v1/chat/completions"
    semaphore = asyncio.Semaphore(settings.concurrency)
    try:
        with tempfile.TemporaryDirectory(prefix="chunk-captions-") as workdir:
            source = Path(workdir) / "source.mp4"
            await download_video(video_url, source)

            async def cut_and_upload(window: Window) -> str:
                target = Path(workdir) / f"{window.index}.mp4"
                async with semaphore:
                    await cut_window(ffmpeg, source, window, target)
                    key = await store.put_video(f"{prefix}{window.index}.mp4", target)
                return await store.signed_url(key)

            signed_urls = await asyncio.gather(*(cut_and_upload(window) for window in windows))
            timeline = await asyncio.gather(
                *(
                    caption_window(client, chat_url, model, window, url, settings.num_frames, semaphore, telemetry)
                    for window, url in zip(windows, signed_urls, strict=True)
                )
            )
    except ChunkCaptionError:
        raise
    except Exception as exc:
        raise ChunkCaptionError(f"{type(exc).__name__}: {exc}") from exc
    finally:
        try:
            deleted = await store.delete_prefix(prefix)
            logger.info("chunk_captions: deleted %d scratch objects under %s", deleted, prefix)
        except Exception as exc:
            logger.warning("chunk_captions: could not delete the scratch prefix %s: %s", prefix, exc)
    telemetry["elapsed_ms"] = round((time.perf_counter() - started) * 1000.0, 1)
    entries = list(timeline)
    return ChunkCaptions(timeline=entries, telemetry=telemetry, context=context_block(entries))
