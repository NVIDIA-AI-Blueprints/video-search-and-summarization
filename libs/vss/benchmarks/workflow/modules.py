# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Private frozen caption inputs and observations for diagnostic module swaps."""

import hashlib
import json
import os
import stat

from benchmarks.e2e.config import fingerprint, key
from benchmarks.e2e.transport import ProviderObserver
from benchmarks.e2e.worker import caption_identity, validate_windows
from benchmarks.ingestion import caption_result
from vss.ingestion import RequestSource, ingest
from vss.ingestion.contracts import VlmChunkResponse
from vss.ingestion.vlm import OpenAICompatibleVLMBackend, register_vlm_backend

CAPSULE_LIMIT = 1024**2


def task_identity(profile):
    return {
        "source_sha256": profile.video_sha256,
        "duration_s": profile.duration_s,
        "chunk_duration_s": profile.chunk_duration_s,
        "vlm_model": profile.vlm_model,
        "caption_prompt_sha256": fingerprint(profile.caption_prompt),
        "caption_max_tokens": profile.caption_max_tokens,
    }


def bounded_captions(chunks, profile, expected):
    validate_windows(chunks, profile, expected)
    if len(chunks) > 64 or any(len(c.vlm_response.encode()) > 8192 for c in chunks):
        raise ValueError("caption_budget")
    raw = json.dumps(
        [c.model_dump(mode="json") for c in chunks], allow_nan=False
    ).encode()
    if len(raw) > CAPSULE_LIMIT:
        raise ValueError("caption_budget")


def storage_identity(chunks):
    return {
        "captions": caption_identity(chunks),
        "source_ids": [c.chunk.sourceId for c in chunks],
        "frames": [c.rtvi_frame_count for c in chunks],
        "usage": [c.vlm_stats for c in chunks],
    }


def capture(chunks, profile, expected):
    bounded_captions(chunks, profile, expected)
    raw = json.dumps(
        {
            "schema_version": "private_caption_capsule.1",
            "task": task_identity(profile),
            "caption_sha256": fingerprint(caption_identity(chunks)),
            "native_completion": "unqualified",
            "chunks": [c.model_dump(mode="json") for c in chunks],
        },
        allow_nan=False,
    ).encode()
    if len(raw) > CAPSULE_LIMIT:
        raise ValueError("capsule_budget")
    fd = os.open(
        profile.caption_capture,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
    )
    with os.fdopen(fd, "wb") as out:
        out.write(raw)
    return {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "scope": "private_text_never_published",
    }


def replay(profile, expected):
    fd = os.open(profile.caption_input, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise ValueError("capsule_permissions")
        raw = stream.read(CAPSULE_LIMIT + 1)
    if (
        len(raw) > CAPSULE_LIMIT
        or hashlib.sha256(raw).hexdigest() != profile.caption_input_sha256
    ):
        raise ValueError("capsule_identity_or_budget")
    body = json.loads(raw)
    if (
        set(body)
        != {"schema_version", "task", "caption_sha256", "native_completion", "chunks"}
        or body["schema_version"] != "private_caption_capsule.1"
        or body["task"] != task_identity(profile)
        or body["native_completion"] != "unqualified"
        or not isinstance(body["chunks"], list)
        or not 1 <= len(body["chunks"]) <= 64
    ):
        raise ValueError("capsule_contract")
    chunks = [VlmChunkResponse.model_validate(c) for c in body["chunks"]]
    bounded_captions(chunks, profile, expected)
    if fingerprint(caption_identity(chunks)) != body["caption_sha256"]:
        raise ValueError("capsule_caption_identity")
    return chunks


class FrameObserver(ProviderObserver):
    def __init__(self, profile):
        super().__init__(profile.vlm_model)
        self.profile = profile
        self.selected = []

    def request(self, request):
        super().request(request)
        body = json.loads(request.content)
        settings = self.profile.model_generation()
        if body.get("model") != self.model or any(
            type(body.get(k)) is not type(v) or body[k] != v
            for k, v in settings.items()
        ):
            raise ValueError("frame_request_settings")
        images = [
            p["image_url"]["url"]
            for m in body["messages"]
            if isinstance(m.get("content"), list)
            for p in m["content"]
            if p.get("type") == "image_url"
        ]
        if not 1 <= len(images) <= 8 or any(
            not isinstance(v, str) or not v.startswith("data:image/jpeg;base64,")
            for v in images
        ):
            raise ValueError("frame_request_images")
        self.inputs[-1].update(
            generation=settings,
            image_sha256=[hashlib.sha256(v.encode()).hexdigest() for v in images],
            encoded_bytes=sum(len(v.encode()) for v in images),
        )

    def validate(self, expected):
        if (
            not len(self.completions)
            == len(self.inputs)
            == len(self.selected)
            == expected
        ):
            raise ValueError("frame_request_multiplicity")
        for request, selection, completion in zip(
            self.inputs, self.selected, self.completions, strict=True
        ):
            if (
                request["image_count"] != len(selection["pts_ns"])
                or completion["http_status"] != 200
                or not completion["model_matches"]
                or completion["finish_reason"] != "stop"
                or not completion["usage_available"]
                or completion["output_tokens"] >= self.profile.caption_max_tokens
            ):
                raise ValueError("frame_completion_or_input")


async def local_captions(profile, asset, http, observer, state):
    class ConfiguredVLM(OpenAICompatibleVLMBackend):
        def __init__(self):
            super().__init__(
                base_url=str(http.base_url).rstrip("/")
                + ("" if str(http.base_url).rstrip("/").endswith("/v1") else "/v1"),
                model=profile.vlm_model,
                api_key=key(profile.frame_key_env),
                client=http,
            )

        async def infer(self, chunk, **kwargs):
            if len(observer.selected) >= 64:
                raise ValueError("frame_selection_budget")
            observer.selected.append(
                {
                    "index": chunk.chunk_idx,
                    "start_ns": round(chunk.start_s * 1e9),
                    "end_ns": round(chunk.end_s * 1e9),
                    "pts_ns": [f.timestamp_ns for f in chunk.frames],
                }
            )
            return await super().infer(chunk, **kwargs)

    register_vlm_backend("vlm", ConfiguredVLM)
    selected_backend = state.get("local_pipeline", profile.captioning)
    state["job"] = ingest(
        RequestSource(source_id=asset, url=str(profile.video)),
        backends=[selected_backend],
        model=profile.vlm_model,
        prompt=profile.caption_prompt,
        chunk_duration=profile.chunk_duration_s,
        generation_config=profile.model_generation(),
    )
    chunks = caption_result(await state["job"], selected_backend)
    await state["job"].wait_closed()
    return chunks
