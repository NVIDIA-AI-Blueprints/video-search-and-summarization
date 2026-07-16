# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Deterministic identifiers for retry-safe memory upserts."""

from uuid import UUID


def summary_id_from_completion_id(completion_id: UUID | str) -> str:
    return f"summary:{UUID(str(completion_id))}"


def event_id_from_summary_id(summary_id: str, ordinal: int) -> str:
    if not summary_id.startswith("summary:"):
        raise ValueError("summary_id must start with 'summary:'")
    if ordinal < 1:
        raise ValueError("ordinal must be one-based")
    return f"event:{summary_id.removeprefix('summary:')}:{ordinal:04d}"
