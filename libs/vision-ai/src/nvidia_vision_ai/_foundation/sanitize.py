# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Sanitizers for untrusted (backend- or user-controlled) data."""

from __future__ import annotations


def scrub_log(value: object) -> str:
    """Return ``value`` as a single-line string safe to log (CWE-117).

    Replaces CR/LF with a space and drops C0 control characters except tab.
    """
    text = str(value).replace("\r", " ").replace("\n", " ")
    return "".join(ch for ch in text if ch == "\t" or ord(ch) >= 0x20)
