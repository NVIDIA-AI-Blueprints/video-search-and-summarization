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

"""Private, bounded input for real normal-VSS and library workflow diagnostics."""

from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from benchmarks.e2e.config import Strict


class Workflow(Strict):
    schema_version: Literal["workflow.1"] = "workflow.1"
    arm: Literal["normal", "library"]
    captioning: Literal["rtvi", "cpu_vlm", "gpu_vlm", "replay"] = "rtvi"
    store_backend: Literal["memory", "file", "elasticsearch"] = "elasticsearch"
    observation: Literal["minimal", "collection", "resources", "combined"] = "resources"
    barrier_directory: Path | None = None
    barrier_member: str | None = Field(default=None, pattern=r"^[a-z0-9-]{1,40}$")
    barrier_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    caption_input: Path | None = None
    caption_input_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    caption_capture: Path | None = None
    output_capture: Path | None = None
    sampling_contract: Literal["backend_native", "shared_duration_grid.v1"] = (
        "backend_native"
    )
    caption_contract: Literal[
        "append_only_final_chunks.v1", "final_file_chunk_set.v1"
    ] = "append_only_final_chunks.v1"
    video: Path
    video_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    duration_s: float = Field(gt=0, le=600)
    chunk_duration_s: int = Field(default=30, ge=1, le=120)
    normal_url_env: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,127}$")
    rtvi_url_env: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,127}$")
    llm_url_env: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,127}$")
    es_url_env: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,127}$")
    relay_control_env: str | None = Field(
        default=None, pattern=r"^[A-Z][A-Z0-9_]{0,127}$"
    )
    frame_url_env: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{0,127}$")
    normal_key_env: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{0,127}$")
    rtvi_key_env: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{0,127}$")
    frame_key_env: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{0,127}$")
    llm_key_env: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{0,127}$")
    es_key_env: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{0,127}$")
    frames_per_chunk: int = Field(default=8, ge=1, le=64)
    concurrent_streams: int = Field(default=1, ge=1, le=8)
    caption_generation: dict | None = None
    summary_generation: dict | None = None
    vlm_model: str = Field(min_length=1, max_length=128)
    llm_model: str = Field(min_length=1, max_length=128)
    stack_manifest: Path | None = None
    stack_manifest_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    normal_config_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    caption_prompt: str = Field(
        default="Describe visible video events as JSON with events containing "
        "start_time, end_time, type and description.",
        min_length=1,
        max_length=4096,
    )
    summary_prompt: str = Field(
        default="/no_think\nSummarize observed video events factually and concisely.",
        min_length=1,
        max_length=4096,
    )
    scenario: str = Field(default="traffic observation", min_length=1, max_length=256)
    events: tuple[str, ...] = Field(
        default=("vehicle movement", "pedestrian movement"), min_length=1, max_length=16
    )
    caption_max_tokens: int = Field(default=256, ge=1, le=4096)
    summary_max_tokens: int = Field(default=512, ge=1, le=4096)
    work_timeout_s: float = Field(default=180, ge=5, le=600)
    cleanup_timeout_s: float = Field(default=15, ge=1, le=60)

    @model_validator(mode="after")
    def module_selection(self):
        if (self.stack_manifest is None) != (self.stack_manifest_sha256 is None):
            raise ValueError("stack manifest requires path and hash")
        if self.captioning in {"cpu_vlm", "gpu_vlm"} and self.frames_per_chunk != 8:
            raise ValueError("library frame backend currently fixes eight frames")
        for controls in (self.caption_generation, self.summary_generation):
            validate_generation(controls)
        if (
            len(
                {
                    self.barrier_directory is None,
                    self.barrier_member is None,
                    self.barrier_id is None,
                }
            )
            != 1
        ):
            raise ValueError("barrier requires directory, member and identity")
        if self.sampling_contract != "backend_native" and (
            self.arm != "library" or self.captioning not in {"cpu_vlm", "gpu_vlm"}
        ):
            raise ValueError("shared sampling requires local frame captioning")
        if (
            self.output_capture is not None
            and self.output_capture == self.caption_capture
        ):
            raise ValueError("private capture paths must differ")
        if self.arm == "normal" and (
            self.captioning != "rtvi"
            or self.store_backend != "elasticsearch"
            or self.caption_capture is not None
        ):
            raise ValueError("normal module selection unsupported")
        if (self.captioning == "replay") != (
            self.caption_input is not None and self.caption_input_sha256 is not None
        ):
            raise ValueError("replay requires exclusive capsule path/hash")
        if self.captioning != "replay" and (
            self.caption_input is not None or self.caption_input_sha256 is not None
        ):
            raise ValueError("capsule only applies to replay")
        if self.captioning == "replay" and self.caption_capture is not None:
            raise ValueError("replay cannot capture again")
        if (
            self.caption_capture is not None
            and self.captioning == "rtvi"
            and self.caption_contract != "final_file_chunk_set.v1"
        ):
            raise ValueError("RTVI capture requires final-file witness")
        return self

    def model_generation(self):
        return {
            "max_tokens": self.caption_max_tokens,
            **(
                {"temperature": 0, "seed": 1729, "enable_reasoning": False}
                if self.caption_generation is None
                else self.caption_generation
            ),
        }

    def caption_settings(self):
        return {
            "num_frames_per_second_or_fixed_frames_chunk": self.frames_per_chunk,
            "use_fps_for_chunking": False,
            "enable_audio": False,
            **self.model_generation(),
        }


def validate_generation(controls):
    if controls is None:
        return
    if not isinstance(controls, dict) or set(controls) - {
        "temperature",
        "top_p",
        "seed",
        "enable_reasoning",
        "reasoning_effort",
    }:
        raise ValueError("unsupported generation control")
    for name, value in controls.items():
        if name in {"temperature", "top_p"}:
            if type(value) not in {float, int} or not 0 <= value <= (
                2 if name == "temperature" else 1
            ):
                raise ValueError("generation sampling range")
        elif name == "seed" and (type(value) is not int or not 0 <= value < 2**31):
            raise ValueError("generation seed")
        elif name == "enable_reasoning" and type(value) is not bool:
            raise ValueError("generation reasoning flag")
        elif name == "reasoning_effort" and value not in {
            "none",
            "minimal",
            "low",
            "medium",
            "high",
        }:
            raise ValueError("generation reasoning effort")


def load(path: Path) -> Workflow:
    if path.stat().st_size > 65536:
        raise ValueError("workflow profile byte budget")
    profile = Workflow.model_validate_json(path.read_bytes(), strict=True)
    if any(not 0 < len(event.strip()) <= 256 for event in profile.events):
        raise ValueError("event label budget")
    return profile
