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

"""Private configuration. Published evidence contains only hashes and labels."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, allow_inf_nan=False, hide_input_in_errors=True
    )


class Provider(Strict):
    model: str = Field(min_length=1, max_length=128)
    revision: str = Field(min_length=1, max_length=256)
    base_url_env: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,127}$")
    api_key_env: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{0,127}$")


class Arm(Strict):
    label: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    captioning: Literal["cpu_vlm", "gpu_vlm", "rtvi"] = "cpu_vlm"
    store: Literal["memory", "file", "elasticsearch"] = "memory"
    vlm: Provider
    llm: Provider
    es_url_env: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{0,127}$")
    es_api_key_env: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{0,127}$")
    es_index: str = Field(default="m8-q0", pattern=r"^[a-z][a-z0-9_-]{0,63}$")

    @model_validator(mode="after")
    def storage_selection(self):
        if (self.store == "elasticsearch") != (self.es_url_env is not None):
            raise ValueError("ES endpoint must be explicit and exclusive to ES")
        if self.store != "elasticsearch" and self.es_api_key_env is not None:
            raise ValueError("ES credentials require ES")
        return self


class Recipe(Strict):
    schema_version: Literal["q0.1"] = "q0.1"
    evidence_kind: Literal["controlled_wiring", "live_provider"]
    swap: Literal["captioning", "storage", "vlm", "llm"]
    arms: tuple[Arm, Arm]
    video: Path
    video_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    duration_s: float = Field(gt=0, le=1800)
    chunk_duration_s: int = Field(default=30, ge=1, le=120)
    frames_per_chunk: Literal[8] = 8
    overlap_s: Literal[0] = 0
    caption_max_tokens: int = Field(default=256, ge=1, le=4096)
    summary_max_tokens: int = Field(default=256, ge=1, le=4096)
    caption_prompt: str = Field(
        default="Describe visible events with timestamps.",
        min_length=1,
        max_length=4096,
    )
    summary_prompt: str = Field(
        default="Summarize the visible events factually.", min_length=1, max_length=4096
    )
    required_summary_terms: tuple[str, ...] = Field(min_length=1, max_length=16)
    pairs: int = Field(default=2, ge=2, le=20)
    warmups: int = Field(default=1, ge=0, le=3)
    request_timeout_s: float = Field(default=60, gt=0, le=600)
    work_timeout_s: float = Field(default=240, gt=0, le=1800)
    cleanup_timeout_s: float = Field(default=15, gt=0, le=120)
    worker_timeout_s: float = Field(default=300, gt=0, le=2000)
    run_timeout_s: float = Field(default=3600, gt=0, le=86400)

    @model_validator(mode="after")
    def comparable(self):
        if math.ceil(self.duration_s / self.chunk_duration_s) > 64:
            raise ValueError("profile exceeds the 64-window bound")
        a, b = self.arms
        if a.label == b.label:
            raise ValueError("arm labels must differ")
        fields = {
            "captioning": {"captioning"},
            "storage": {"store", "es_url_env", "es_api_key_env", "es_index"},
            "vlm": {"vlm"},
            "llm": {"llm"},
        }[self.swap]
        left = a.model_dump(exclude={"label"} | fields)
        right = b.model_dump(exclude={"label"} | fields)
        if left != right:
            raise ValueError("comparison changes more than the named slot")
        if a.model_dump(exclude={"label"}) == b.model_dump(exclude={"label"}):
            raise ValueError("comparison must change the named slot")
        if self.work_timeout_s + self.cleanup_timeout_s + 5 >= self.worker_timeout_s:
            raise ValueError("worker deadline must allow work, cleanup and setup")
        if self.run_timeout_s < self.worker_timeout_s:
            raise ValueError("run deadline must allow one worker")
        if any(
            not term.strip() or len(term) > 128 for term in self.required_summary_terms
        ):
            raise ValueError("adequacy terms must be bounded and nonempty")
        if self.evidence_kind == "controlled_wiring" and any(
            arm.captioning != "cpu_vlm" or arm.store == "elasticsearch"
            for arm in self.arms
        ):
            raise ValueError("controlled fixture supports CPU and memory/file only")
        return self


def load(path: Path) -> Recipe:
    if path.stat().st_size > 65536:
        raise ValueError("configuration byte limit")
    return Recipe.model_validate_json(path.read_bytes(), strict=True)


def fingerprint(value) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False
        ).encode()
    ).hexdigest()


def endpoint(name: str) -> str:
    value = os.environ[name]
    parts = urlsplit(value)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or any(c.isspace() for c in value)
    ):
        raise ValueError("endpoint must be HTTP(S), without URL credentials/query")
    return value.rstrip("/")


def key(name: str | None) -> str:
    if name is None:
        return "not-set"
    value = os.environ[name]
    if not value or any(c in value for c in "\r\n"):
        raise ValueError("missing or invalid credential")
    return value
