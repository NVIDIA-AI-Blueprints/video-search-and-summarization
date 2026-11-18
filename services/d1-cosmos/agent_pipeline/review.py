# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Exact VIOS event clips, independent Cosmos verification, and shared inference."""
import hashlib
import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from urllib.parse import quote
import httpx
from .clips import clip_path, save_clip, LOCK as CLIP_LOCK
from .verification import (candidate_condition, parse_verification,
                           verification_messages, verification_response_format,
                           corrective_verification_message, verification_policy_sha256,
                           VERIFICATION_POLICY_VERSION, VerificationFormatError)
from .inference_scheduler import INFERENCE_SCHEDULER, InferenceCancelled

# Compatibility for callers/tests using the old lock API. Each generation now
# gets its own slot, so queued interactive questions can precede a correction.
INFERENCE_LOCK = INFERENCE_SCHEDULER
MAX_CLIP_BYTES = 256 * 1024 * 1024


class VideoCompletionTruncated(ValueError):
    """A generation hit its output limit; retain the exact response for diagnosis."""

    def __init__(self, response, digest, model):
        super().__init__("Cosmos answer exceeded its output limit")
        self.response = response
        self.clip_sha256 = digest
        self.model = model


class ClipEvidenceChanged(ValueError):
    """The retained media no longer matches the event's established evidence."""


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def resolve_clip(event):
    sensor = event["trigger"].get("vios_sensor")
    if not sensor:
        raise ValueError("Stream needs a registered VIOS sensor before clip review")
    result = subprocess.run([os.getenv("VSS_CLI", "vss"), "vios", "clip", "--sensor", sensor,
        "--start-time", iso(event["clip_start"]), "--end-time", iso(event["clip_end"])],
        capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RuntimeError(f"VIOS clip command failed with exit {result.returncode}")
    clip = json.loads(result.stdout)
    if not isinstance(clip.get("media_url"), str):
        raise ValueError("VIOS did not return media_url")
    return clip


def _cache_event_clip(event, client, clip_resolver):
    # Once published, these bytes are the evidence for this event. Retries must
    # not replace the clip the operator may already be watching.
    with CLIP_LOCK:
        path = clip_path(event["id"])
        if path.exists():
            return {"source": "event_clip_cache", "event_id": event["id"]}
    # Do not hold the cache lock during network I/O; questions about an already
    # cached event can still open their file while the next clip is prepared.
    clip = clip_resolver(event)
    with tempfile.TemporaryFile() as media:
        with client.stream("GET", clip["media_url"]) as download:
            download.raise_for_status()
            size = 0
            for block in download.iter_bytes():
                size += len(block)
                if size > MAX_CLIP_BYTES:
                    raise ValueError("Event clip exceeds 256 MiB limit")
                media.write(block)
        if not size:
            raise ValueError("VIOS returned an empty clip")
        with CLIP_LOCK:
            if path.exists():
                return {"source": "event_clip_cache", "event_id": event["id"]}
            save_clip(event["id"], media)
    return clip


def complete_video(event, messages, *, client=None, response_format=None, max_tokens=700,
                   expected_sha256=None):
    """Upload exactly the cached event file; caller holds an inference slot."""
    base = os.environ["COSMOS_BASE_URL"].rstrip("/")
    model = os.environ["COSMOS_MODEL"]
    key = os.getenv("COSMOS_API_KEY")
    headers = {"Authorization": "Bearer " + key} if key else {}
    owns_client = client is None
    client = client or httpx.Client(timeout=180)
    uploaded_id = None
    try:
        # Opening under the cache lock prevents eviction between lookup and open.
        # The open file descriptor keeps these exact bytes alive through upload.
        with CLIP_LOCK:
            media = clip_path(event["id"]).open("rb")
        with media:
            if not 0 < os.fstat(media.fileno()).st_size <= MAX_CLIP_BYTES:
                raise ValueError("Invalid cached event clip size")
            digest = hashlib.file_digest(media, "sha256").hexdigest()
            if expected_sha256 is not None and digest != expected_sha256:
                raise ClipEvidenceChanged("Cached video no longer matches this event's reviewed evidence")
            media.seek(0)
            upload = client.post(base + "/v1/files", headers=headers,
                data={"purpose": "vision", "media_type": "video", "sensor_name": event["stream_id"],
                      "creation_time": datetime.fromtimestamp(event["clip_start"], timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")},
                files={"file": (event["id"] + ".mp4", media, "video/mp4")})
            upload.raise_for_status()
            uploaded_id = upload.json()["id"]
        body = {"model": model, "id": uploaded_id, "stream": False, "max_tokens": max_tokens,
                "enable_reasoning": False, "temperature": 0, "messages": messages}
        if response_format is not None:
            body["response_format"] = response_format
        response = client.post(base + "/v1/chat/completions", headers=headers, json=body)
        response.raise_for_status()
        answer = response.json()
        choice = response_choice(answer)
        if choice.get("finish_reason") == "length":
            raise VideoCompletionTruncated(answer, digest, model)
        return answer, digest, model
    finally:
        if uploaded_id:
            try:
                client.delete(base + "/v1/files/" + quote(str(uploaded_id), safe=""), headers=headers)
            except httpx.HTTPError:
                pass
        if owns_client:
            client.close()


def response_choice(answer):
    choices = answer.get("choices") if isinstance(answer, dict) else None
    return choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}


def _failed_generation(attempt, answer, digest, model, error):
    choice = response_choice(answer)
    message = choice.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    return {"attempt": attempt, "clip_sha256": digest, "model": model,
            "finish_reason": str(choice.get("finish_reason", ""))[:80],
            "response_sha256": hashlib.sha256(json.dumps(answer, sort_keys=True,
                ensure_ascii=False).encode()).hexdigest(),
            "content_length": len(content) if isinstance(content, str) else None,
            "validation_errors": error.validation_errors}


def review_event(event, client=None, clip_resolver=resolve_clip, *, stop=None):
    if stop is not None and stop.is_set():
        raise InferenceCancelled("Review stopped before preparing its clip")
    condition = candidate_condition(event)
    owns_client = client is None
    client = client or httpx.Client(timeout=180)
    try:
        clip = _cache_event_clip(event, client, clip_resolver)
        messages = verification_messages(event, condition)
        expected_digest = None
        failures = []
        policy_hash = verification_policy_sha256()
        for attempt in range(1, 3):
            output_limit = False
            with INFERENCE_SCHEDULER.slot(priority="background", stop=stop):
                try:
                    answer, digest, model = complete_video(event, messages,
                        client=client, response_format=verification_response_format(),
                        expected_sha256=expected_digest)
                except VideoCompletionTruncated as exc:
                    answer, digest, model = exc.response, exc.clip_sha256, exc.model
                    output_limit = True
            expected_digest = digest
            message = response_choice(answer).get("message")
            content = message.get("content") if isinstance(message, dict) else None
            try:
                if output_limit:
                    raise VerificationFormatError("Cosmos verification exceeded its output limit", errors=[{
                        "field": "$", "code": "output_limit",
                        "message": "Write shorter complete observations so the whole JSON object fits the output budget"}])
                assessment = parse_verification(content)
                break
            except VerificationFormatError as exc:
                failures.append(_failed_generation(attempt, answer, digest, model, exc))
                if attempt == 2:
                    exc.diagnostics = {"verification_policy_version": VERIFICATION_POLICY_VERSION,
                        "verification_policy_sha256": policy_hash, "clip_sha256": digest,
                        "generation_attempts": attempt, "validation_errors": exc.validation_errors,
                        "attempt_failures": failures}
                    # Available to a trusted diagnostics caller; never interpolate
                    # raw model text into logs, API errors or a substitute verdict.
                    exc.last_response = answer
                    raise
                # Exactly one corrective generation, using the same bytes. A
                # waiting operator question can run before that second attempt.
                messages += [{"role": "assistant", "content": str(content or "")[:5000]},
                    {"role": "user", "content": corrective_verification_message(exc)}]
        context = assessment["summary"] + "\n\n" + "\n".join("• " + point for point in assessment["evidence"])
        return {"schema_version": 1, "kind": "d1.review", "id": event["id"] + "-review",
            "event_id": event["id"], "stream_id": event["stream_id"], "captured_at": event["captured_at"],
            "decision_ids": event["decision_ids"], "clip_start": event["clip_start"],
            "clip_end": event["clip_end"], "clip": clip, "clip_sha256": digest, "model": model,
            "review_prompt": event["rule"]["review_prompt"], "condition": condition,
            "verification_version": 1, "verification_attempts": attempt,
            "verification_policy_version": VERIFICATION_POLICY_VERSION,
            "verification_policy_sha256": policy_hash, "verification_format_failures": failures,
            **assessment, "context": context, "response": answer}
    finally:
        if owns_client:
            client.close()
