# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Read-only ``vss analytics`` command package."""

from __future__ import annotations

from .register import ANALYTICS
from .register import GROUP

__all__ = ["ANALYTICS", "GROUP"]
