# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Plugin registration for ``vss analytics``."""

from __future__ import annotations

from .group import ANALYTICS

GROUP = ANALYTICS

__all__ = ["ANALYTICS", "GROUP"]
