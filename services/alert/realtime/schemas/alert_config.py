#!/usr/bin/env python3
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

"""
Typed configuration dataclass for real-time VLM alert rules.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


# Single source of truth for the optional RTVI VLM fields that are omitted
# from the ES document and the generate_captions payload when None.
# Reference this constant instead of duplicating the tuple at every call site
# so that adding a new field only requires one change here.
EXTENDED_OPTIONAL_FIELDS: Tuple[str, ...] = (
    "api_type",
    "response_format",
    "stream_options",
    "max_tokens",
    "temperature",
    "top_p",
    "top_k",
    "ignore_eos",
    "seed",
    "media_info",
    "enable_audio",
    "mm_processor_kwargs",
)


# Optional stream-identity / location metadata fields that operators can set
# via POST. Persisted in Elasticsearch (so replay can rebuild the full
# /streams/add payload after a restart) and forwarded to RTVI when not None.
# Kept separate from EXTENDED_OPTIONAL_FIELDS because these target
# /streams/add, not /generate_captions, and their values are stream identity
# rather than VLM tuning knobs.
STREAM_IDENTITY_OPTIONAL_FIELDS: Tuple[str, ...] = (
    "description",
    "username",
    "password",
    "place_name",
    "place_type",
    "place_lat",
    "place_lon",
    "place_alt",
    "place_coordinate_x",
    "place_coordinate_y",
)


@dataclass(frozen=True, slots=True)
class AlertRuleConfig:
    """
    Typed configuration for creating a real-time VLM alert rule.

    This is the service-layer contract. REST handlers, CLI tools, agent flows,
    and tests should construct this object instead of hand-building a dict.
    Validation and defaults live here, making callers simpler and safer.

    Required fields: live_stream_url, alert_type, prompt.
    For the always-on YAML config, system_prompt and model are also required
    (enforced by AlwaysOnRuleParams validation).

    ``sensor_id`` and ``sensor_name`` are intentionally optional. Callers
    that don't have a VIOS-assigned sensor id (manual POSTs, ad-hoc tests,
    legacy ES documents written before the field existed) can omit them;
    the RTVI VLM client forwards ``None`` as ``null`` and lets RTVI apply
    its own defaults.

    Optional RTVI VLM fields (omitted from the generate_captions payload when
    set to None, letting RTVI use its own server-side defaults):
      api_type, response_format, stream_options, max_tokens, temperature,
      top_p, top_k, ignore_eos, seed, media_info, enable_audio,
      mm_processor_kwargs.
    """

    live_stream_url: str
    alert_type: str
    prompt: str

    # Stream identity & metadata — all optional. ``None`` flows all the
    # way through to RTVI as JSON ``null``; RTVI either applies its own
    # default or generates a new identifier.
    sensor_id: Optional[str] = None
    sensor_name: Optional[str] = None
    description: Optional[str] = None

    # RTSP authentication
    username: Optional[str] = None
    password: Optional[str] = None

    # Place / location metadata
    place_name: Optional[str] = None
    place_type: Optional[str] = None
    place_lat: Optional[str] = None
    place_lon: Optional[str] = None
    place_alt: Optional[str] = None
    place_coordinate_x: Optional[str] = None
    place_coordinate_y: Optional[str] = None

    # VLM parameters
    system_prompt: str = ""
    model: str = ""
    chunk_duration: int = 30
    chunk_overlap_duration: int = 5
    num_frames_per_second_or_fixed_frames_chunk: int = 10
    use_fps_for_chunking: bool = True
    vlm_input_width: int = 256
    vlm_input_height: int = 256
    enable_reasoning: bool = True
    # Extended RTVI VLM generate_captions options — all optional.
    # When None the field is omitted from the RTVI request payload so
    # RTVI applies its server-side defaults.
    api_type: Optional[str] = None
    response_format: Optional[Dict[str, Any]] = None
    stream_options: Optional[Dict[str, Any]] = None
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    top_k: Optional[int] = None
    ignore_eos: Optional[bool] = None
    seed: Optional[int] = None
    media_info: Optional[Dict[str, Any]] = None
    enable_audio: Optional[bool] = None
    mm_processor_kwargs: Optional[Dict[str, Any]] = None
