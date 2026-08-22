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
"""Shared Pydantic models used across multiple primitives."""

from __future__ import annotations

from typing import Literal

# Constrains source_type to the two supported ingest kinds so an unknown value
# is rejected at the model boundary rather than deep in a primitive.
SourceType = Literal["video_file", "rtsp"]

# Fusion reranking strategies supported by the Search orchestrator. Shared so the
# runtime, the Search primitive, and the CLI's ``--fusion-method`` choices stay
# in lockstep instead of drifting across three separate literal definitions.
FusionMethod = Literal["weighted_rrf", "rrf"]
