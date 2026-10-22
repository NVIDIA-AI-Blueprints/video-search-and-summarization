# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the untrusted-data sanitizers."""

from __future__ import annotations

import pytest

from vss_core._foundation.sanitize import quote_path_segment
from vss_core._foundation.sanitize import safe_basename
from vss_core._foundation.sanitize import scrub_log


def test_scrub_log_replaces_cr_lf():
    assert scrub_log("line1\r\nline2") == "line1  line2"
    assert scrub_log("a\nb") == "a b"
    assert scrub_log("a\rb") == "a b"


def test_scrub_log_drops_control_chars_but_keeps_tab():
    assert scrub_log("a\x00b\x1fc") == "abc"
    assert scrub_log("a\tb") == "a\tb"


def test_scrub_log_coerces_non_str():
    assert scrub_log(123) == "123"
    assert scrub_log(None) == "None"
    assert scrub_log(["x", "y"]) == "['x', 'y']"


def test_quote_path_segment_encodes_reserved():
    assert quote_path_segment("a/b") == "a%2Fb"
    assert quote_path_segment("a b") == "a%20b"
    assert quote_path_segment("plain") == "plain"


def test_safe_basename_strips_directory():
    assert safe_basename("/etc/passwd") == "passwd"
    assert safe_basename("a/b/c.txt") == "c.txt"
    assert safe_basename("file.txt") == "file.txt"


@pytest.mark.parametrize("bad", ["", ".", "..", "/", "foo/"])
def test_safe_basename_rejects_traversal(bad):
    with pytest.raises(ValueError):
        safe_basename(bad)
