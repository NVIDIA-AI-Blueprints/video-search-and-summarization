#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Monorepo path constants for video-summarization CI utilities."""

from __future__ import annotations

from pathlib import Path

# Path from the git repository root to the video-summarization service tree.
LVS_SERVICE_PREFIX = "services/video-summarization/"


def service_root_from_ci_utils() -> Path:
    """Return the video-summarization service root (parent of ci/)."""
    return Path(__file__).resolve().parents[2]


def is_under_service(path: str) -> bool:
    normalized = path.lstrip("/").replace("\\", "/")
    return normalized == LVS_SERVICE_PREFIX.rstrip("/") or normalized.startswith(LVS_SERVICE_PREFIX)


def strip_service_prefix(path: str) -> str:
    normalized = path.lstrip("/").replace("\\", "/")
    if normalized == LVS_SERVICE_PREFIX.rstrip("/"):
        return ""
    if normalized.startswith(LVS_SERVICE_PREFIX):
        return normalized[len(LVS_SERVICE_PREFIX) :]
    return normalized
