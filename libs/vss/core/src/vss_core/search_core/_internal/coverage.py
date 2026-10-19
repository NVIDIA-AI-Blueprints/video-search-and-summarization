# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Request-local exact-scan diagnostics shared by concurrent retrieval legs."""

from contextvars import ContextVar
from typing import Any

candidate_coverage: ContextVar[list[dict[str, Any]] | None] = ContextVar("candidate_coverage", default=None)
