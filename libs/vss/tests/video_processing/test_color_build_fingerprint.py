# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""A known native color regression applies only to its observed build stack."""

from __future__ import annotations

import pytest

from . import test_native_inputs as inputs

BROKEN_BUILD = """
Video I/O:
    FFMPEG: YES
      avcodec: YES (61.19.101)
      avformat: YES (61.7.100)
      avutil: YES (59.39.100)
      swscale: YES (8.3.100)
"""


@pytest.mark.parametrize(
    "version,system,build,expected",
    [
        ("4.13.0", "Darwin", BROKEN_BUILD, True),
        ("4.13.0", "Linux", BROKEN_BUILD, False),
        ("4.13.0", "Darwin", BROKEN_BUILD.replace("8.3.100", "9.1.100"), False),
        ("4.13.0", "Darwin", "no linked-library information", False),
        ("5.0.0", "Darwin", BROKEN_BUILD, False),
    ],
)
def test_color_failure_fingerprint_is_exact(
    version: str,
    system: str,
    build: str,
    expected: bool,
) -> None:
    assert inputs._known_broken_color_build(version, system, build) is expected
