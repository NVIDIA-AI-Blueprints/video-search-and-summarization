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

"""Validated matrix dimensions and explicit preconfigured service bindings."""

import importlib.util
import itertools
import json
import math
import shutil
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from benchmarks.e2e.config import Strict, endpoint, fingerprint, key
from benchmarks.media import file_hash
from benchmarks.workflow.config import Workflow, validate_generation

ENV = r"^[A-Z][A-Z0-9_]{0,127}$"
NAME = r"^[a-z][a-z0-9_-]{0,39}$"
PROFILES = {"normal-es": ("normal", "rtvi", "elasticsearch")} | {
    f"library-{caption}-{store}": (
        "library",
        caption + "_vlm" if caption != "rtvi" else caption,
        store,
    )
    for caption, store in itertools.product(
        ("rtvi", "cpu", "gpu"), ("memory", "file", "elasticsearch")
    )
}
PLACEMENTS = ("local-local", "local-remote", "remote-local", "remote-remote")


class Provider(Strict):
    model: str = Field(min_length=1, max_length=128)
    revision: str = Field(min_length=1, max_length=256)
    engine: str = Field(default="unknown", min_length=1, max_length=128)
    precision: str = Field(default="unknown", min_length=1, max_length=64)
    url_env: str = Field(pattern=ENV)
    key_env: str | None = Field(default=None, pattern=ENV)
    # RTVI is a service connector, not the OpenAI frame-VLM endpoint.
    rtvi_url_env: str | None = Field(default=None, pattern=ENV)
    rtvi_key_env: str | None = Field(default=None, pattern=ENV)
    generation: dict | None = None

    @model_validator(mode="after")
    def controls(self):
        validate_generation(self.generation)
        return self


class Normal(Strict):
    url_env: str = Field(pattern=ENV)
    key_env: str | None = Field(default=None, pattern=ENV)
    deployment_manifest: Path | None = None


class Media(Strict):
    name: str = Field(pattern=NAME)
    video: Path
    duration_s: float = Field(gt=0, le=600)
    sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class Matrix(Strict):
    schema_version: Literal["perf_matrix.1"] = "perf_matrix.1"
    media: tuple[Media, ...] = Field(min_length=1, max_length=16)
    vlms: dict[str, Provider]
    llms: dict[str, Provider]
    normal: dict[str, Normal] = {}
    es_url_env: str = Field(default="PERF_ES_URL", pattern=ENV)
    es_key_env: str | None = Field(default=None, pattern=ENV)
    profiles: tuple[str, ...] = (
        "normal-es",
        "library-rtvi-memory",
        "library-rtvi-file",
        "library-rtvi-elasticsearch",
    )
    placements: tuple[str, ...] = ("local-local",)
    chunk_durations: tuple[int, ...] = (30,)
    frames_per_chunk: tuple[int, ...] = (8,)
    frame_sampling: Literal["backend_native", "shared_duration_grid.v1"] = (
        "backend_native"
    )
    streams: tuple[int, ...] = (1,)
    repetitions: int = Field(default=10, ge=1, le=100)
    warmups: int = Field(default=1, ge=0, le=5)
    observation: Literal["minimal", "collection", "resources", "combined"] = "resources"
    work_timeout_s: float = Field(default=600, ge=5, le=600)
    cleanup_timeout_s: float = Field(default=15, ge=1, le=60)
    # Wall deadline stops admission, then reserves cooperative cleanup time.
    run_timeout_s: float = Field(default=7200, ge=5, le=86400)
    max_trials: int = Field(default=500, ge=1, le=5000)
    caption_max_tokens: int = Field(default=1024, ge=1, le=4096)
    summary_max_tokens: int = Field(default=1024, ge=1, le=4096)
    caption_generation: dict = {"temperature": 0, "top_p": 1, "enable_reasoning": False}
    summary_generation: dict = {"temperature": 0, "top_p": 1, "enable_reasoning": False}
    caption_prompt: str = Field(
        default=(
            "Describe visible video events as JSON with events containing "
            "start_time, end_time, type and description."
        ),
        min_length=1,
        max_length=4096,
    )
    summary_prompt: str = Field(
        default="Summarize observed video events factually and concisely.",
        min_length=1,
        max_length=4096,
    )
    scenario: str = Field(
        default="video event observation", min_length=1, max_length=256
    )
    events: tuple[str, ...] = ("visible activity",)

    @model_validator(mode="after")
    def dimensions(self):
        if len({m.name for m in self.media}) != len(self.media):
            raise ValueError("duplicate media names")
        for values, choices in (
            (self.profiles, PROFILES),
            (self.placements, PLACEMENTS),
        ):
            if (
                not values
                or len(set(values)) != len(values)
                or set(values) - set(choices)
            ):
                raise ValueError("unknown or duplicate matrix selection")
        for values, low, high in (
            (self.chunk_durations, 1, 120),
            (self.frames_per_chunk, 1, 64),
            (self.streams, 1, 8),
        ):
            if (
                not values
                or len(set(values)) != len(values)
                or any(type(v) is not int or not low <= v <= high for v in values)
            ):
                raise ValueError("matrix numeric dimension")
        if (
            set(self.vlms) - {"local", "remote"}
            or set(self.llms) - {"local", "remote"}
            or set(self.normal) - set(PLACEMENTS)
        ):
            raise ValueError("unknown model placement")
        validate_generation(self.caption_generation)
        validate_generation(self.summary_generation)
        if not 1 <= len(self.events) <= 16 or any(
            not e.strip() or len(e) > 256 for e in self.events
        ):
            raise ValueError("event labels")
        return self


def load(path: Path) -> Matrix:
    if path.stat().st_size > 65536:
        raise ValueError("recipe byte limit")
    recipe = Matrix.model_validate(tomllib.loads(path.read_text()))
    base = path.resolve().parent
    recipe = recipe.model_copy(
        update={
            "media": tuple(
                m.model_copy(update={"video": (base / m.video).resolve()})
                for m in recipe.media
            ),
            "normal": {
                k: n.model_copy(
                    update={
                        "deployment_manifest": (base / n.deployment_manifest).resolve()
                        if n.deployment_manifest
                        else None
                    }
                )
                for k, n in recipe.normal.items()
            },
        }
    )
    return recipe


def select(requested, available, excluded=()):
    selected = (
        list(available)
        if requested == "all"
        else requested.split(",")
        if requested
        else list(available)
    )
    if (
        not selected
        or len(set(selected)) != len(selected)
        or set(selected) - set(available)
        or set(excluded) - set(available)
    ):
        raise ValueError("unknown or duplicate selector")
    selected = [s for s in selected if s not in excluded]
    if not selected:
        raise ValueError("empty selector")
    return tuple(selected)


def cells(recipe):
    dimensions = (
        recipe.media,
        recipe.chunk_durations,
        recipe.frames_per_chunk,
        recipe.streams,
        recipe.placements,
        recipe.profiles,
    )
    if math.prod(len(d) for d in dimensions) > 256:
        raise ValueError("matrix exceeds 256 cells; split the recipe")
    combinations = list(itertools.product(*dimensions))
    trials = sum(row[3] * (recipe.repetitions + recipe.warmups) for row in combinations)
    if trials > recipe.max_trials:
        raise ValueError("trial budget exceeded")
    rows = []
    for media, chunk, frames, streams, placement, profile in combinations:
        row = dict(
            workload=media.name,
            profile=profile,
            placement=placement,
            chunk_duration_s=chunk,
            frames_per_chunk=frames,
            concurrent_streams=streams,
        )
        row["cell_id"] = fingerprint(row)[:16]
        row["blocked"] = []
        if math.ceil(media.duration_s / chunk) > 64:
            row["blocked"].append("exceeds_64_caption_windows")
        if PROFILES[profile][1] != "rtvi" and frames != 8:
            row["blocked"].append("native_frame_backend_fixes_eight_frames")
        vlm, llm = placement.split("-")
        if vlm not in recipe.vlms or llm not in recipe.llms:
            row["blocked"].append("model_placement_not_configured")
        elif PROFILES[profile][1] == "rtvi" and recipe.vlms[vlm].rtvi_url_env is None:
            row["blocked"].append("rtvi_connector_not_configured_for_vlm")
        if profile == "normal-es" and placement not in recipe.normal:
            row["blocked"].append("normal_deployment_not_configured_for_placement")
        rows.append(row)
    return rows


def workflow(recipe, cell, *, capture=None):
    media = next(m for m in recipe.media if m.name == cell["workload"])
    vlm, llm = (
        recipe.vlms[cell["placement"].split("-")[0]],
        recipe.llms[cell["placement"].split("-")[1]],
    )
    normal = recipe.normal.get(cell["placement"])
    arm, captioning, store = PROFILES[cell["profile"]]
    expected_sha = media.sha256 or file_hash(media.video)
    normal_sha = None
    if arm == "normal" and normal and normal.deployment_manifest is not None:
        if not 0 < normal.deployment_manifest.stat().st_size <= 65536:
            raise ValueError("normal deployment evidence size")
        declaration = json.loads(normal.deployment_manifest.read_bytes())
        if declaration != {
            "schema_version": "perf_normal_deployment.1",
            "placement": cell["placement"],
            "vlm_model": vlm.model,
            "llm_model": llm.model,
            "vlm_revision": vlm.revision,
            "llm_revision": llm.revision,
        }:
            raise ValueError(
                "normal deployment declaration does not match requested models"
            )
        normal_sha = file_hash(normal.deployment_manifest)
    return Workflow(
        normal_config_sha256=normal_sha,
        arm=arm,
        captioning=captioning,
        store_backend=store,
        video=media.video,
        video_sha256=expected_sha,
        duration_s=media.duration_s,
        chunk_duration_s=cell["chunk_duration_s"],
        frames_per_chunk=cell["frames_per_chunk"],
        concurrent_streams=cell["concurrent_streams"],
        normal_url_env=normal.url_env if normal else "PERF_UNUSED_NORMAL",
        normal_key_env=normal.key_env if normal else None,
        rtvi_url_env=vlm.rtvi_url_env or "PERF_UNUSED_RTVI",
        rtvi_key_env=vlm.rtvi_key_env,
        frame_url_env=vlm.url_env,
        frame_key_env=vlm.key_env,
        llm_url_env=llm.url_env,
        llm_key_env=llm.key_env,
        es_url_env=recipe.es_url_env,
        es_key_env=recipe.es_key_env,
        vlm_model=vlm.model,
        llm_model=llm.model,
        sampling_contract=recipe.frame_sampling
        if captioning in {"cpu_vlm", "gpu_vlm"}
        else "backend_native",
        caption_contract="final_file_chunk_set.v1",
        caption_max_tokens=recipe.caption_max_tokens,
        summary_max_tokens=recipe.summary_max_tokens,
        caption_prompt=recipe.caption_prompt,
        summary_prompt=recipe.summary_prompt,
        caption_generation=recipe.caption_generation
        if vlm.generation is None
        else vlm.generation,
        summary_generation=recipe.summary_generation
        if llm.generation is None
        else llm.generation,
        scenario=recipe.scenario,
        events=recipe.events,
        observation=recipe.observation,
        work_timeout_s=recipe.work_timeout_s,
        cleanup_timeout_s=recipe.cleanup_timeout_s,
        output_capture=capture,
    )


def admit_environment(profile):
    """Resolve only used endpoints/keys; never serialize secret values."""
    if shutil.which("ffprobe") is None:
        raise ValueError("FFprobe is required")
    decoder = {"cpu_vlm": "cv2", "gpu_vlm": "PyNvVideoCodec"}.get(profile.captioning)
    if decoder and importlib.util.find_spec(decoder) is None:
        raise ValueError("required native decoder is not installed")
    roles = ["normal"] if profile.arm == "normal" else ["llm"]
    if profile.captioning == "rtvi":
        roles.append("rtvi")
    else:
        roles.append("frame")
    if profile.arm == "library" and profile.store_backend == "elasticsearch":
        roles.append("es")
    for role in roles:
        endpoint(getattr(profile, role + "_url_env"))
        key(getattr(profile, role + "_key_env"))
