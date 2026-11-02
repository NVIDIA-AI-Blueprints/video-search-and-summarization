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
"""``python -m evalv2.probe``: live capability probes for Track A's video models and the judges. Dry run by default.

RB1 (read-only listings) left four things only a model call can settle. Each probe is one minimal call:

- ``schema_inline`` (per video model): a short **dev** clip sent inline as a base64 data URL, with a strict
  ``response_format`` whose keys the prompt never names. A 2xx with a completion means inline video is accepted;
  a reply that is exactly a JSON object valid against that schema means the server enforces it.
- ``frames_many`` (per video model): the same call with ``num_frames`` 64 instead of 8. The methodology records
  effective sampling from provider-reported usage (the method behind ``analysis-schema.md`` section 7): equal prompt
  tokens mean ``num_frames`` is ignored; more mean it is honoured, and the difference per frame is the frame cost.
- ``long_clip`` (per video model): a longer dev clip at 64 frames. More prompt tokens than the short clip mean the
  sampling depends on clip length (a fixed frame rate); equal ones mean a fixed frame count. The schema also asks
  the model how many frames it saw, a weak signal recorded beside the token evidence.
- ``judge`` (per judge): the production judge request (``judge-prompt-v1`` on a paraphrase pair). It checks that the
  judge answers and that the reply is the expected ``{"score", "reasoning"}`` JSON.

**Without ``--live``** the probe prints every request exactly as it would be stored (an inline clip appears as its
size and SHA-256, the bytes the manifest pins) and its worst-case tokens, under the network block. **With
``--live``** it needs ``--max-calls`` (at most 12) and ``--max-tokens``, and an approval: a marker file
(``--approval``) or ``EVALV2_ALLOW_LIVE_PROBE=1``. It refuses before anything is sent when the plan's worst case
passes either cap or a credential is missing. Every attempt is reserved on a budget ledger before it is sent, with
no retries (the ``none`` policy), so a call is one HTTP attempt. A model's later probes are skipped once one of its
probes fails, and an endpoint's once it answers 401 or 403. Results are appended to ``probes.jsonl`` under a private
output directory, with request and response artifacts beside it.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import copy
import hashlib
import json
import os
import re
import sys
import time
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote_to_bytes

import jsonschema

from evalv2.adapters.base import AdapterConfigError, CallContext, CallResult, ModelAdapter
from evalv2.adapters.openai_compat import ENDPOINTS
from evalv2.adapters.registry import build_adapter
from evalv2.core.artifacts import ArtifactRef, ArtifactStore
from evalv2.core.budget import BudgetLedger, Cap
from evalv2.core.hashing import canonical_json, sha256_file, sha256_json
from evalv2.core.netguard import no_network
from evalv2.core.private import ensure_private
from evalv2.core.retry import INFRASTRUCTURE_FAILURES, POLICIES
from evalv2.datasets import registry as dataset_registry
from evalv2.requests.judge import tokens_bound as judge_tokens_bound
from evalv2.requests.p1 import p1_settings
from evalv2.tasks import judges as judge_registry

PROBE_VERSION = "evalv2-probe-v1"
APPROVAL_VERSION = "evalv2-probe-approval-v1"
APPROVAL_ENV = "EVALV2_ALLOW_LIVE_PROBE"
MAX_CALLS_LIMIT = 12
TASK = "probe"
VIDEO_MODELS = ("nvidia/cosmos-3-nano-reasoner", "nvidia/cosmos-3-super-reasoner",
                "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning")  # Track A's video subjects (Switchyard ids, RB1)
VIDEO_ENDPOINT = "switchyard"
# External open-weight candidates (independent families) on the NVIDIA API catalog, probed only when named with
# --models; their endpoint is the catalog's. Documented inputs are in adapters/capabilities.json (unverified).
EXTERNAL_VIDEO_MODELS = {"google/gemma-4-31b-it": "nvidia-api-catalog", "moonshotai/kimi-k2.6": "nvidia-api-catalog",
                         "qwen/qwen3.5-9b": "openrouter"}
EXTERNAL_ENDPOINTS = ("nvidia-api-catalog", "openrouter")  # --endpoint may move an external candidate between these
# OpenRouter routing per model (OpenRouter's provider block): pinned full-precision or fp8 providers that list
# structured outputs, no fallback to other providers, no data collection, every request parameter required.
OPENROUTER_ROUTING = {
    "google/gemma-4-31b-it": {"provider": {"order": ["Crusoe", "Parasail", "DeepInfra"], "allow_fallbacks": False,
                                           "data_collection": "deny", "require_parameters": True}},
    "qwen/qwen3.5-9b": {"provider": {"order": ["DeepInfra", "Parasail"], "allow_fallbacks": False,
                                     "data_collection": "deny", "require_parameters": True}},
}
JUDGE_IDS = ("catalog-gpt-oss-20b-v1", "catalog-mistral-large-4-v1")
CONTINUITY_JUDGE_ID = "switchyard-nemotron-3-nano-v1"  # opt-in (--with-continuity-judge); its budget is not approved
KINDS = ("schema_inline", "frames_many", "long_clip", "judge", "over_60s", "p1_short")
DEFAULT_KINDS = ("schema_inline", "frames_many", "long_clip", "judge")  # over_60s and p1_short are opt-in (--only)
# p1_short: the unchanged production v4 P1 request (prompt, strict schema, settings) on the short clip, so a candidate's
# incident extraction is checked against the contract; its worst case is the P1 prompt bound plus P1's max_tokens.
P1_PROMPT_TOKENS_BOUND = 18000  # the P1 prompt is about 4 k tokens; an 8 s clip leaves wide room (C1a: <= 12.8 k)
# Per-model probe limits (max output tokens, prompt bound) where an approved probe budget is tight: Kimi's two probes
# must fit 0.05 M tokens (schema_inline 14,048 + p1_short 34,384 = 48,432).
PROBE_LIMITS = {"moonshotai/kimi-k2.6": {"max_tokens": 2048, "prompt_bound": 12000}}
SHORT_CLIP = "RoadAccidents017"  # dev, 8.1 s, 1.68 MB: the smallest dev file
LONG_CLIP = "Burglary093"  # dev, 25.4 s, 4.94 MB: about three times the short clip at a fixed 2 fps
OVER_60S_CLIP = "Explosion029"  # dev, 80.3 s, 6.9 MB: past Gemma 4's documented 60 s video limit; under the inline cap
FEW_FRAMES, MANY_FRAMES = 8, 64  # 64 is the P1 setting (contracts/inference.json)
VIDEO_MAX_TOKENS = 8192  # enough for a reasoning model's thinking on a one-line answer
# The most video and text a probe prompt can carry: 64 frames at up to ~300 tokens each with room to spare. The
# largest V1 P1 prompt was 17,970 tokens with an 18 KB prompt text; the probe's text is about 50 tokens.
VIDEO_PROMPT_TOKENS_BOUND = 24000
MAX_INLINE_BYTES = 8 * 1024 * 1024  # raw clip bytes (base64 adds a third); far below a gateway's 100 MB body limit
PROBE_PROMPT = ("Watch this video. How many separate still frames of the video were you given? Describe the scene in "
                "one short sentence, and say how much movement there is.")
PROBE_SCHEMA: dict[str, Any] = {  # only keywords the P1 contract schema uses
    "type": "object",
    "properties": {
        "probe_frame_count": {"type": "integer", "minimum": 0, "maximum": 4096},
        "probe_scene": {"type": "string"},
        "probe_motion": {"type": "string", "enum": ["none", "some", "much"]},
    },
    "required": ["probe_frame_count", "probe_scene", "probe_motion"],
    "additionalProperties": False,
}
PROBE_RESPONSE_FORMAT = {"type": "json_schema", "json_schema": {"name": "evalv2_probe", "schema": PROBE_SCHEMA,
                                                                 "strict": True}}
JUDGE_PROBE_TEXTS = ("A man in a red jacket punches another man outside a shop at night.",
                     "At night, outside a store, a person wearing a red coat hits another man.")
HONOURED_RATIO, SAME_RATIO = 0.10, 0.02  # descriptive thresholds for the token comparisons, not gates


class ProbeRefused(RuntimeError):
    """The probe will not start (or send anything): approval, caps, credentials or the clips are not in order."""


@dataclass(frozen=True)
class ProbeCall:
    id: str
    kind: str
    model: str
    endpoint: str
    ledger: str
    tokens_bound: int
    clip: str | None = None
    num_frames: int | None = None
    judge_id: str | None = None
    requires: str | None = None  # the probe whose failure skips this one (same model, same request path)


@dataclass(frozen=True)
class _Subject:
    """What ``adapters.registry.build_adapter`` reads from a subject config."""

    id: str
    adapter: str
    model: str
    endpoint: str
    request: dict[str, Any] | None = None


# ---------------------------------------------------------------------------------------------------------------
# Clips: dev only, by id, checked against the dataset manifest
# ---------------------------------------------------------------------------------------------------------------


def _clip_index() -> tuple[dict[str, dict[str, Any]], set[str]]:
    manifest = dataset_registry.resolve("dataset-manifest-v1")
    split = dataset_registry.resolve("split-v2")
    clips = json.loads(manifest.path.read_text(encoding="utf-8"))["clips"]
    dev = set(json.loads(split.path.read_text(encoding="utf-8"))["dev"])
    return {clip["incident_id"]: clip for clip in clips}, dev


def clip_meta(clip: str) -> dict[str, Any]:
    """The manifest entry of a dev clip; a test-half clip (or an unknown one) is refused."""
    index, dev = _clip_index()
    if clip not in index:
        raise ProbeRefused(f"{clip} is not in dataset-manifest-v1")
    if clip not in dev:
        raise ProbeRefused(f"{clip} is not a dev clip of split-v2: probes never touch the test half")
    meta = index[clip]
    if meta["size_bytes"] > MAX_INLINE_BYTES:
        raise ProbeRefused(f"{clip} is {meta['size_bytes']} bytes, over the inline limit of {MAX_INLINE_BYTES}")
    return meta


def clip_path(meta: Mapping[str, Any], data_root: Path) -> Path:
    """Where the verified local copy lives: ``eval_data/videos/...`` in the manifest, under the data root here."""
    relative = Path(meta["local_path"])
    parts = relative.parts[1:] if relative.parts and relative.parts[0] == "eval_data" else relative.parts
    return Path(data_root).joinpath(*parts)


def load_clip(meta: Mapping[str, Any], data_root: Path) -> bytes:
    """The clip's bytes, refused unless their size and SHA-256 are the manifest's."""
    path = clip_path(meta, data_root)
    if not path.is_file():
        raise ProbeRefused(f"{meta['incident_id']}: {path} not found (fetch it with eval_fetch_videos.py)")
    if path.stat().st_size != meta["size_bytes"] or sha256_file(path) != meta["sha256"]:
        raise ProbeRefused(f"{meta['incident_id']}: {path} differs from the manifest (size or sha256)")
    return path.read_bytes()


def stored_data_url(meta: Mapping[str, Any]) -> str:
    """The inline video as the probe stores and prints it: what :func:`compact_data_url` makes of the real one."""
    return f"data:video/mp4;base64,[omitted: {meta['size_bytes']} bytes, sha256 {meta['sha256']}]"


def data_url(data: bytes) -> str:
    return "data:video/mp4;base64," + base64.b64encode(data).decode("ascii")


def compact_data_url(url: str) -> str:
    """A data URL reduced to its media type and the size and SHA-256 of its bytes (other URLs are unchanged)."""
    header, comma, payload = url.partition(",")
    if not comma or not header.lower().startswith("data:"):
        return url
    if header.lower().endswith(";base64"):
        try:
            data = base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError):
            return f"{header},[omitted: {len(payload)} characters that are not valid base64]"
    else:
        data = unquote_to_bytes(payload)
    return f"{header},[omitted: {len(data)} bytes, sha256 {hashlib.sha256(data).hexdigest()}]"


def compact_request(request: Mapping[str, Any]) -> dict[str, Any]:
    """The request with every content part's data URL compacted (the clip is private and megabytes long)."""
    stored = copy.deepcopy(dict(request))
    for message in stored.get("messages") or []:
        content = message.get("content") if isinstance(message, dict) else None
        for part in content if isinstance(content, list) else []:
            for value in part.values() if isinstance(part, dict) else []:
                if isinstance(value, dict) and isinstance(value.get("url"), str):
                    value["url"] = compact_data_url(value["url"])
    return stored


class CompactingStore(ArtifactStore):
    """The probe's artifact store: a stored request keeps an inline clip's size and SHA-256, never its bytes.

    The adapter stores ``stored_request(request)``, which keeps data URLs whole by contract; the probe compacts them
    on the way in, so its store holds no copy of a dataset clip.
    """

    def put(self, kind: str, payload: Any) -> ArtifactRef:
        return super().put(kind, compact_request(payload) if kind == "request" else payload)


# ---------------------------------------------------------------------------------------------------------------
# The plan and its requests
# ---------------------------------------------------------------------------------------------------------------


def video_request(model: str, video_url: str, num_frames: int) -> dict[str, Any]:
    """P1's request shape and settings (``contracts/inference.json``) with the probe's prompt, schema and limits."""
    settings = p1_settings()
    max_tokens = PROBE_LIMITS.get(model, {}).get("max_tokens", VIDEO_MAX_TOKENS)
    return {"model": model,
            "messages": [{"role": "user", "content": [{"type": "video_url", "video_url": {"url": video_url}},
                                                      {"type": "text", "text": PROBE_PROMPT}]}],
            "temperature": settings["temperature"], "max_tokens": max_tokens,
            "media_io_kwargs": {"video": {"num_frames": num_frames}}, "response_format": PROBE_RESPONSE_FORMAT}


def judge_request(spec: judge_registry.JudgeSpec) -> dict[str, Any]:
    """The production judge request on the probe's paraphrase pair (the same builder a judge stage uses)."""
    return judge_registry.build_request(spec, "incident", *JUDGE_PROBE_TEXTS)


def build_plan(*, kinds: Sequence[str] = DEFAULT_KINDS, models: Sequence[str] = VIDEO_MODELS,
               endpoint: str | None = None,
               judges: Sequence[str] = JUDGE_IDS) -> list[ProbeCall]:
    """Every call, in the order it would be sent: each video model's three probes, then each judge."""
    unknown = sorted(set(kinds) - set(KINDS))
    if unknown:
        raise ProbeRefused(f"unknown probe kinds {unknown}; known: {list(KINDS)}")
    known = [*VIDEO_MODELS, *EXTERNAL_VIDEO_MODELS]
    bad = sorted(set(models) - set(known))
    if bad:
        raise ProbeRefused(f"{bad} are not Track A video subjects or external candidates {known}")
    registry = judge_registry.load_judges()
    plan: list[ProbeCall] = []
    for model in models:
        limits = PROBE_LIMITS.get(model, {})
        video_bound = limits.get("prompt_bound", VIDEO_PROMPT_TOKENS_BOUND) + limits.get("max_tokens", VIDEO_MAX_TOKENS)
        first = f"schema_inline:{model}"
        steps = (("schema_inline", SHORT_CLIP, FEW_FRAMES), ("frames_many", SHORT_CLIP, MANY_FRAMES),
                 ("long_clip", LONG_CLIP, MANY_FRAMES), ("over_60s", OVER_60S_CLIP, MANY_FRAMES),
                 ("p1_short", SHORT_CLIP, None))
        for kind, clip, frames in steps:
            if kind in kinds:
                requires = first if kind != "schema_inline" and "schema_inline" in kinds else None
                if model in EXTERNAL_VIDEO_MODELS:
                    call_endpoint = endpoint or EXTERNAL_VIDEO_MODELS[model]
                    if call_endpoint not in EXTERNAL_ENDPOINTS:
                        raise ProbeRefused(f"--endpoint {call_endpoint!r} is not one of {list(EXTERNAL_ENDPOINTS)}")
                else:
                    call_endpoint = VIDEO_ENDPOINT
                bound = (P1_PROMPT_TOKENS_BOUND + p1_settings()["max_tokens"]) if kind == "p1_short" else video_bound
                plan.append(ProbeCall(f"{kind}:{model}", kind, model, call_endpoint, "inference", bound,
                                      clip=clip, num_frames=frames, requires=requires))
    if "judge" in kinds:
        for judge_id in judges:
            spec = registry.get(judge_id)
            plan.append(ProbeCall(f"judge:{judge_id}", "judge", spec.model, spec.endpoint, "judge",
                                  judge_tokens_bound(judge_request(spec)), judge_id=judge_id))
    return plan


def request_for(call: ProbeCall, video_urls: Mapping[str, str]) -> dict[str, Any]:
    """The body of one call; ``video_urls`` maps a clip to the URL to put in it (stored form or real data URL)."""
    if call.kind == "judge":
        return judge_request(judge_registry.load_judges().get(call.judge_id or ""))
    if call.kind == "p1_short":
        from evalv2.requests.p1 import build_p1_request

        return build_p1_request(call.model, video_urls[call.clip or ""])
    return video_request(call.model, video_urls[call.clip or ""], call.num_frames or 0)


def credential_names(endpoints: Iterable[str]) -> dict[str, list[str]]:
    """The environment variables each endpoint reads (names only; values are never printed or stored)."""
    out: dict[str, list[str]] = {}
    for name in sorted(set(endpoints)):
        endpoint = ENDPOINTS[name]
        out[name] = [endpoint.api_key_env] + ([] if endpoint.default_base_url else [endpoint.base_url_env])
    return out


def missing_credentials(endpoints: Iterable[str], environ: Mapping[str, str]) -> list[str]:
    return sorted({variable for names in credential_names(endpoints).values() for variable in names
                   if not environ.get(variable)})


def describe(plan: Sequence[ProbeCall]) -> dict[str, Any]:
    """The dry run's statement: each request exactly as stored, its hash and worst case, and the totals."""
    clips = {call.clip: clip_meta(call.clip) for call in plan if call.clip}
    stored_urls = {clip: stored_data_url(meta) for clip, meta in clips.items()}
    calls = []
    for call in plan:
        request = request_for(call, stored_urls)
        calls.append({"id": call.id, "kind": call.kind, "model": call.model, "endpoint": call.endpoint,
                      "ledger": call.ledger, "clip": call.clip, "num_frames": call.num_frames,
                      "judge": call.judge_id, "skipped_if_failed": call.requires,
                      "worst_case_tokens": call.tokens_bound, "request_sha256": sha256_json(request),
                      "request": request})
    by_ledger: dict[str, dict[str, int]] = {}
    for call in plan:
        row = by_ledger.setdefault(call.ledger, {"calls": 0, "worst_case_tokens": 0})
        row["calls"] += 1
        row["worst_case_tokens"] += call.tokens_bound
    return {"probe": PROBE_VERSION, "mode": "dry run: nothing was sent",
            "clips": {clip: {"split": "dev", "bytes": meta["size_bytes"], "sha256": meta["sha256"],
                             "duration_s": meta["duration_seconds"]} for clip, meta in clips.items()},
            "note": ("A video request's data URL is shown as stored: at send time it carries the clip file named "
                     "by that size and SHA-256, base64-encoded. Retries: none, so each call is one HTTP attempt."),
            "calls": calls,
            "totals": {"calls": len(plan), "worst_case_tokens": sum(c.tokens_bound for c in plan),
                       "by_ledger": by_ledger},
            "credentials": credential_names(call.endpoint for call in plan),
            "live": ("python -m evalv2.probe --live --max-calls N --max-tokens T --approval FILE "
                     f"(or {APPROVAL_ENV}=1), N <= {MAX_CALLS_LIMIT}, only with the owner's approval")}


# ---------------------------------------------------------------------------------------------------------------
# Approval and limits
# ---------------------------------------------------------------------------------------------------------------


def read_approval(path: Path) -> dict[str, Any]:
    """A probe approval marker, refused unless it names the approver, the date and caps within the hard limit."""
    try:
        data = Path(path).read_bytes()
        marker = json.loads(data)
    except (OSError, json.JSONDecodeError) as exc:
        raise ProbeRefused(f"approval marker {path} is unreadable: {exc}") from exc
    if not isinstance(marker, dict) or marker.get("approval") != APPROVAL_VERSION:
        raise ProbeRefused(f"approval marker {path} is not an {APPROVAL_VERSION} marker")
    if not str(marker.get("approved_by") or "").strip() or not str(marker.get("approved_at") or "").strip():
        raise ProbeRefused("the approval marker must name approved_by and approved_at")
    calls, tokens = marker.get("max_calls"), marker.get("max_tokens")
    if not isinstance(calls, int) or isinstance(calls, bool) or not 1 <= calls <= MAX_CALLS_LIMIT:
        raise ProbeRefused(f"the approval marker's max_calls must be an integer from 1 to {MAX_CALLS_LIMIT}")
    if not isinstance(tokens, int) or isinstance(tokens, bool) or tokens <= 0:
        raise ProbeRefused("the approval marker's max_tokens must be a positive integer")
    return {"source": "marker", "path": str(path), "sha256": hashlib.sha256(data).hexdigest(), **marker}


def check_live(plan: Sequence[ProbeCall], *, max_calls: int | None, max_tokens: int | None,
               approval_path: Path | None, environ: Mapping[str, str]) -> dict[str, Any]:
    """The approval record of a live session; :class:`ProbeRefused` names every reason not to start."""
    problems: list[str] = []
    approval: dict[str, Any] | None = None
    if approval_path is not None:
        try:
            approval = read_approval(approval_path)
        except ProbeRefused as exc:
            problems.append(str(exc))
    elif environ.get(APPROVAL_ENV) == "1":
        approval = {"source": "env", "variable": APPROVAL_ENV}
    else:
        problems.append(f"no approval: pass --approval FILE or set {APPROVAL_ENV}=1, only with the owner's approval")
    if max_calls is None or max_tokens is None:
        problems.append("--live needs an explicit --max-calls and --max-tokens")
    else:
        if not 1 <= max_calls <= MAX_CALLS_LIMIT:
            problems.append(f"--max-calls must be from 1 to {MAX_CALLS_LIMIT}")
        if max_tokens <= 0:
            problems.append("--max-tokens must be positive")
        if len(plan) > max_calls:
            problems.append(f"the plan has {len(plan)} calls, more than --max-calls {max_calls}")
        worst = sum(call.tokens_bound for call in plan)
        if worst > max_tokens:
            problems.append(f"the plan's worst case is {worst} tokens, more than --max-tokens {max_tokens}")
        if approval and approval["source"] == "marker":
            if max_calls > approval["max_calls"] or max_tokens > approval["max_tokens"]:
                problems.append(f"--max-calls/--max-tokens exceed the approved {approval['max_calls']} calls and "
                                f"{approval['max_tokens']} tokens")
            for field, values in (("kinds", {c.kind for c in plan}),
                                  ("models", {c.model for c in plan if c.kind != "judge"}),
                                  ("judges", {c.judge_id for c in plan if c.judge_id})):
                allowed = approval.get(field)
                if allowed is not None and not values <= set(allowed):
                    problems.append(f"the approval does not cover {field} {sorted(values - set(allowed))}")
    if not plan:
        problems.append("the plan is empty")
    missing = missing_credentials((call.endpoint for call in plan), environ)
    if missing:
        problems.append(f"missing credentials (set, never print): {missing}")
    if problems:
        raise ProbeRefused("; ".join(problems))
    return approval or {}


# ---------------------------------------------------------------------------------------------------------------
# Reading the replies
# ---------------------------------------------------------------------------------------------------------------

_THINK = re.compile(r"^\s*<think>.*?</think>\s*", re.DOTALL)
_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)
_DATA_URL_TEXT = re.compile(r"(data:[\w.+-]+/[\w.+-]+;base64,)[A-Za-z0-9+/=]{16,}", re.IGNORECASE)


def scrub(text: str | None) -> str | None:
    """Error text with any echoed base64 payload of an inline clip replaced (a provider's 400 can quote the URL)."""
    return None if text is None else _DATA_URL_TEXT.sub(lambda m: m.group(1) + "[omitted]", text)


def parse_reply(content: str | None) -> tuple[Any, str]:
    """``(value, form)``: ``json`` when the whole reply is one JSON value; ``json_after_reasoning`` after a leading
    ``<think>`` block; ``fenced`` inside a code fence; ``prose`` when it is not JSON (value ``None``)."""
    text = content or ""
    candidates = [("json", text)]
    if _THINK.match(text):
        candidates.append(("json_after_reasoning", _THINK.sub("", text, count=1)))
    fenced = _FENCE.match(text)
    if fenced:
        candidates.append(("fenced", fenced.group(1)))
    for form, body in candidates:
        try:
            return json.loads(body), form
        except json.JSONDecodeError:
            continue
    return None, "prose"


def schema_errors(value: Any) -> list[str]:
    validator = jsonschema.Draft202012Validator(PROBE_SCHEMA)
    return sorted(error.message for error in validator.iter_errors(value))


def _transport(result: CallResult) -> dict[str, Any]:
    last = result.attempts[-1] if result.attempts else {}
    failure = result.failure
    usage = result.usage
    return {"accepted": result.ok, "http_status": last.get("status"),
            "failure": failure.kind if failure else None, "detail": scrub(failure.detail[:300]) if failure else None,
            "finish_reason": result.finish_reason, "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens, "reasoning_tokens": usage.reasoning_tokens,
            "total_tokens": usage.total_tokens, "wall_ms": result.timing.wall_ms if result.timing else None}


def analyse_video(result: CallResult) -> dict[str, Any]:
    """What one video probe shows: inline acceptance, strict-schema enforcement, the model's own frame count."""
    findings = _transport(result)
    if not result.ok:
        return {**findings, "strict_json_schema": None}
    value, form = parse_reply(result.text)
    errors = schema_errors(value) if value is not None else ["the reply is not JSON"]
    truncated = result.finish_reason == "length"
    enforced = None if truncated else (form in ("json", "json_after_reasoning") and not errors)
    frames = value.get("probe_frame_count") if isinstance(value, dict) else None
    return {**findings, "reply_form": form, "schema_valid": not errors, "schema_errors": errors[:5],
            "strict_json_schema": enforced, "frames_self_report": frames if isinstance(frames, int) else None}


def analyse_p1(result: CallResult, duration_s: float | None) -> dict[str, Any]:
    """Whether the production P1 request yields a contract-v4 report: V1's policy (strict validation, ID-only
    normalisation, no repair), the same check Track A's runner applies, and the outcome it would record."""
    from evalv2.tasks.incident_objects import v1

    findings = _transport(result)
    if not result.ok:
        return {**findings, "contract_ok": None, "core_ok": None, "outcome": None}
    policy = v1.evaluate_policy(result.text, duration_s)
    validation = {"core_violations": policy["core"], "enrichment_violations": policy["enrichment"],
                  "full_contract_ok": policy["full_contract_ok"], "core_ok": policy["core_ok"]}
    return {**findings, "contract_ok": bool(policy["full_contract_ok"]), "core_ok": bool(policy["core_ok"]),
            "outcome": v1.outcome({"ok": True, "core_ok": policy["core_ok"]}, validation),
            "violations": [str(v)[:160] for v in [*policy["core"], *policy["enrichment"]]][:8]}


def analyse_judge(result: CallResult) -> dict[str, Any]:
    """Whether the judge answered with the expected JSON and a usable score (the pair is a paraphrase)."""
    findings = _transport(result)
    if not result.ok:
        return {**findings, "expected_json": None, "score": None}
    check = judge_registry.expected_json(result.text)
    score = None if result.finish_reason == "length" else judge_registry.parse_score(result.text)
    exact = check["json_object"] and check["score_valid"] and check.get("reasoning_is_text", False)
    return {**findings, "json_check": check, "expected_json": bool(exact and not check["fenced"]),
            "score": score, "scored": score is not None, "paraphrase_passes": None if score is None else score >= 0.5}


def _ratio(base: int | None, other: int | None) -> float | None:
    return None if not base or other is None else (other - base) / base


def frame_sampling(few: int | None, many: int | None, long: int | None) -> dict[str, Any]:
    """The token evidence on frame sampling (prompt tokens of the three video probes), read descriptively."""
    by_frames, by_length = _ratio(few, many), _ratio(many, long)

    def read(ratio: float | None, more: str, same: str) -> str:
        if ratio is None:
            return "unknown"
        return more if ratio > HONOURED_RATIO else same if abs(ratio) <= SAME_RATIO else "unclear"

    num_frames = read(by_frames, "honoured", "ignored")
    per_frame = (round((many - few) / (MANY_FRAMES - FEW_FRAMES), 1)
                 if num_frames == "honoured" and few is not None and many is not None else None)
    return {"prompt_tokens": {f"short_{FEW_FRAMES}": few, f"short_{MANY_FRAMES}": many, f"long_{MANY_FRAMES}": long},
            "num_frames": num_frames, "clip_length": read(by_length, "depends on clip length",
                                                         "independent of clip length"),
            "tokens_per_frame": per_frame, "ratios": {"frames": by_frames, "length": by_length}}


def summarise(plan: Sequence[ProbeCall], lines: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Per video model and per judge, the findings a reviewer copies into ``capabilities.json`` (never automatic)."""
    by_id = {line["probe"]: line for line in lines if line.get("kind") == "probe"}

    def finding(probe_id: str, key: str) -> Any:
        line = by_id.get(probe_id)
        return None if line is None or line["status"] != "answered" else line["findings"].get(key)

    def inline_accepted(model: str) -> bool | None:
        """Answered: accepted. Refused (an HTTP error or a non-completion): not accepted. A rate limit, timeout or
        transport error, a skip or no probe: unknown, since nothing about the inline path was learnt."""
        first = by_id.get(f"schema_inline:{model}")
        if first is None or first["status"] == "skipped":
            return None
        if first["status"] == "answered":
            return True
        return None if first["findings"]["failure"] in INFRASTRUCTURE_FAILURES | {"budget_exceeded"} else False

    models: dict[str, Any] = {}
    for model in dict.fromkeys(call.model for call in plan if call.kind != "judge"):
        inline = inline_accepted(model)
        models[model] = {
            "inline_video_accepted": inline,
            "strict_json_schema": finding(f"schema_inline:{model}", "strict_json_schema"),
            "frame_sampling": frame_sampling(finding(f"schema_inline:{model}", "prompt_tokens"),
                                             finding(f"frames_many:{model}", "prompt_tokens"),
                                             finding(f"long_clip:{model}", "prompt_tokens")),
            "frames_self_report": {kind: finding(f"{kind}:{model}", "frames_self_report")
                                   for kind in ("schema_inline", "frames_many", "long_clip", "over_60s")},
            "p1_short": {"answered": finding(f"p1_short:{model}", "accepted"),
                         "contract_ok": finding(f"p1_short:{model}", "contract_ok"),
                         "outcome": finding(f"p1_short:{model}", "outcome")},
            "over_60s": {"answered": finding(f"over_60s:{model}", "accepted"),
                         "prompt_tokens": finding(f"over_60s:{model}", "prompt_tokens")}}
    judges = {call.judge_id: {"answered": finding(call.id, "accepted"),
                              "expected_json": finding(call.id, "expected_json"),
                              "score": finding(call.id, "score"),
                              "reasoning_tokens": finding(call.id, "reasoning_tokens"),
                              "finish_reason": finding(call.id, "finish_reason")}
              for call in plan if call.kind == "judge"}
    statuses = [line["status"] for line in by_id.values()]
    return {"models": models, "judges": judges,
            "calls": {"answered": statuses.count("answered"), "failed": statuses.count("failed"),
                      "skipped": statuses.count("skipped")}}


# ---------------------------------------------------------------------------------------------------------------
# A live session (approval-gated)
# ---------------------------------------------------------------------------------------------------------------


class ResultsLog:
    """``probes.jsonl``: one canonical JSON line per event, appended and synced, never rewritten."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def append(self, record: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("ab") as handle:
            handle.write(canonical_json(dict(record)) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())

    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]


AdapterFactory = Callable[[ProbeCall], ModelAdapter]


def live_adapter(call: ProbeCall) -> ModelAdapter:
    """A real adapter for one call: a judge from its registry entry, a video model on Switchyard."""
    if call.judge_id:
        return build_adapter(judge_registry.load_judges().get(call.judge_id))
    routing = OPENROUTER_ROUTING.get(call.model) if call.endpoint == "openrouter" else None
    return build_adapter(_Subject(f"probe-{call.model.split('/')[-1]}", "openai_compat", call.model, call.endpoint,
                                  {"extra_body": routing} if routing else None))


def ledger_caps(plan: Sequence[ProbeCall]) -> list[Cap]:
    """Per ledger, exactly the plan's worst case: no ledger can take more than its share of the approved caps."""
    caps: dict[str, list[int]] = {}
    for call in plan:
        row = caps.setdefault(call.ledger, [0, 0])
        row[0] += 1
        row[1] += call.tokens_bound
    return [Cap(ledger, max_attempts=attempts, max_tokens=tokens) for ledger, (attempts, tokens) in caps.items()]


def run_live(plan: Sequence[ProbeCall], *, approval: Mapping[str, Any], max_calls: int, max_tokens: int,
             out_dir: Path, data_root: Path, adapter_factory: AdapterFactory = live_adapter,
             sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Send the plan once, in order, and append every outcome. Call only after :func:`check_live`."""
    out_dir = Path(out_dir)
    ensure_private(out_dir)  # replies describe dataset clips
    clips = {call.clip: clip_meta(call.clip) for call in plan if call.clip}
    urls = {clip: data_url(load_clip(meta, data_root)) for clip, meta in clips.items()}  # verified before any call
    session = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    store = CompactingStore(out_dir / "store")
    ledger = BudgetLedger(ledger_caps(plan), out_dir / "budget" / f"{session}.jsonl")
    log = ResultsLog(out_dir / "probes.jsonl")
    log.append({"kind": "session", "session": session, "probe": PROBE_VERSION,
                "probe_sha256": sha256_file(Path(__file__)), "started_at": datetime.now(UTC).isoformat(),
                "approval": dict(approval), "limits": {"max_calls": max_calls, "max_tokens": max_tokens},
                "plan": [{"id": c.id, "worst_case_tokens": c.tokens_bound} for c in plan],
                "clips": {clip: {"bytes": m["size_bytes"], "sha256": m["sha256"]} for clip, m in clips.items()}})
    failed: set[str] = set()
    blocked: set[str] = set()  # endpoints that refused the credentials
    adapters: dict[str, ModelAdapter] = {}
    sent = 0
    lines: list[dict[str, Any]] = []
    for call in plan:
        base = {"kind": "probe", "session": session, "probe": call.id, "probe_kind": call.kind, "model": call.model,
                "endpoint": call.endpoint, "clip": call.clip, "num_frames": call.num_frames, "judge": call.judge_id}
        reason = ("an earlier probe of this model failed" if call.requires in failed or call.model in failed
                  else "the endpoint refused the credentials" if call.endpoint in blocked
                  else "max calls reached" if sent >= max_calls else None)
        if reason:
            line = {**base, "status": "skipped", "reason": reason, "at": datetime.now(UTC).isoformat()}
            log.append(line)
            lines.append(line)
            continue
        key = call.judge_id or call.model
        if key not in adapters:
            adapters[key] = adapter_factory(call)
        adapter = adapters[key]
        request = request_for(call, urls)
        ctx = CallContext(store=store, ledger=ledger, policy=POLICIES["none"], subject=call.model, task=TASK,
                          budget_ledger=call.ledger, tokens_bound=call.tokens_bound, sleep=sleep)
        result = adapter.complete(request, ctx)
        sent += sum(1 for attempt in result.attempts if attempt.get("failure") != "budget_exceeded")
        if call.kind == "judge":
            findings = analyse_judge(result)
        elif call.kind == "p1_short":
            findings = analyse_p1(result, clip_meta(call.clip or "").get("duration_seconds"))
        else:
            findings = analyse_video(result)
        line = {**base, "status": "answered" if result.ok else "failed",
                "request_sha256": sha256_json(compact_request(request)), "request_ref": str(result.request_ref),
                "response_ref": str(result.response_ref) if result.response_ref else None,
                "failure": ({**result.failure.to_dict(), "detail": scrub(result.failure.detail)}
                            if result.failure else None), "usage": result.usage.to_dict(),
                "timing": result.timing.to_dict() if result.timing else None, "findings": findings,
                "at": datetime.now(UTC).isoformat()}
        log.append(line)
        lines.append(line)
        if not result.ok:
            failed.add(call.id)
            if call.kind != "judge":
                failed.add(call.model)
            if findings.get("http_status") in (401, 403):
                blocked.add(call.endpoint)
    summary = {"kind": "summary", "session": session, **summarise(plan, lines), "sent": sent,
               "budget": ledger.snapshot(), "finished_at": datetime.now(UTC).isoformat()}
    log.append(summary)
    return summary


# ---------------------------------------------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m evalv2.probe", description=__doc__.splitlines()[0])
    parser.add_argument("--only", help=f"comma-separated probe kinds from {','.join(KINDS)} "
                                       f"(default: {','.join(DEFAULT_KINDS)})")
    parser.add_argument("--models", help="comma-separated video models (default: every Track A video subject)")
    parser.add_argument("--endpoint", help=f"endpoint for external candidates ({', '.join(EXTERNAL_ENDPOINTS)}); "
                                           "default: each candidate's own")
    parser.add_argument("--judges", help=f"comma-separated judge ids (default: {','.join(JUDGE_IDS)})")
    parser.add_argument("--with-continuity-judge", action="store_true",
                        help=f"also probe {CONTINUITY_JUDGE_ID} (its budget needs its own approval)")
    parser.add_argument("--live", action="store_true", help="send the calls (approval-gated)")
    parser.add_argument("--max-calls", type=int, help=f"hard cap on HTTP attempts, at most {MAX_CALLS_LIMIT}")
    parser.add_argument("--max-tokens", type=int, help="hard cap on worst-case tokens")
    parser.add_argument("--approval", type=Path, help=f"an {APPROVAL_VERSION} marker file")
    parser.add_argument("--data-root", type=Path, default=None, help="private data root (default EVALV2_DATA_ROOT, "
                                                                    "else eval/eval_data)")
    parser.add_argument("--out", type=Path, default=None, help="output directory (default <data root>/v2/probes)")
    return parser


def _split(value: str | None, default: Sequence[str]) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()] if value else list(default)


def main(argv: Sequence[str] | None = None, *, environ: Mapping[str, str] | None = None,
         adapter_factory: AdapterFactory = live_adapter) -> int:
    args = _parser().parse_args(argv)
    environ = os.environ if environ is None else environ
    judges = _split(args.judges, JUDGE_IDS) + ([CONTINUITY_JUDGE_ID] if args.with_continuity_judge else [])
    selection = {"kinds": _split(args.only, DEFAULT_KINDS), "models": _split(args.models, VIDEO_MODELS),
                 "judges": judges, "endpoint": args.endpoint}
    try:
        if not args.live:
            with no_network():  # the dry run proves itself offline: any connection attempt raises
                print(json.dumps(describe(build_plan(**selection)), indent=2))
            return 0
        plan = build_plan(**selection)
        approval = check_live(plan, max_calls=args.max_calls, max_tokens=args.max_tokens,
                              approval_path=args.approval, environ=environ)
        data_root = dataset_registry.data_root(args.data_root)
        summary = run_live(plan, approval=approval, max_calls=args.max_calls, max_tokens=args.max_tokens,
                           out_dir=args.out or data_root / "v2" / "probes", data_root=data_root,
                           adapter_factory=adapter_factory)
    except (ProbeRefused, AdapterConfigError, judge_registry.JudgeConfigError, dataset_registry.DatasetError,
            PermissionError) as exc:
        print(json.dumps({"ok": False, "refused": str(exc)}, indent=2), file=sys.stderr)
        return 2
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
