# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Lightweight command grammar for the ``vss search`` domain."""

from __future__ import annotations

# Kept apart from ``search.py`` so the root command can render its help and
# reject invalid operations without importing the search runtime and clients.
SEARCH_OPERATIONS = {
    "run": "search",
    "embed": "embed_search",
    "attribute": "attribute_search",
}
