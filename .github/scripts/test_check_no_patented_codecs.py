#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the patent-encumbered codec scanner.

Run directly (no pytest dependency):
``python3 .github/scripts/test_check_no_patented_codecs.py``.
The repo's pytest job runs from services/agent so it won't collect this; the CI
codec-scan job runs it as a step so a regression in the gate fails loudly.
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_no_patented_codecs as chk  # noqa: E402


class TestIsForbidden(unittest.TestCase):
    def test_flags_ffmpeg_libs_plain_and_mangled(self):
        # Plain soname form (system packages / Dockerfile guard).
        self.assertTrue(chk.is_forbidden("libavcodec.so.62"))
        self.assertTrue(chk.is_forbidden("libswscale.so.9"))
        self.assertTrue(chk.is_forbidden("libswresample.so.6"))
        # Wheel-mangled form (opencv-python-headless bundles these).
        self.assertTrue(chk.is_forbidden("libavcodec-156beeea.so.62.11.100"))
        self.assertTrue(chk.is_forbidden("libavformat-8c8a026e.so.62.3.100"))
        self.assertTrue(chk.is_forbidden("libavutil-ec54c519.so.60.8.100"))
        self.assertTrue(chk.is_forbidden("libavif-cbf1e83c.so.16.3.0"))

    def test_flags_standalone_codecs(self):
        self.assertTrue(chk.is_forbidden("libx264.so.164"))
        self.assertTrue(chk.is_forbidden("libx265.so.199"))
        self.assertTrue(chk.is_forbidden("libde265.so.0"))
        self.assertTrue(chk.is_forbidden("libopenh264.so.7"))

    def test_does_not_flag_unrelated_libs(self):
        # The classic false-positive risk: a broad `libav` prefix would wrongly
        # flag Avahi. Also sanity-check common system libs.
        for name in (
            "libavahi-common.so.3",
            "libavahi-client.so.3",
            "libssl.so.3",
            "libcrypto.so.3",
            "libc.so.6",
            "libpng16-1b998e9d.so.16.53.0",
            "libopenblasp-r0.so",
            "cv2.abi3.so",  # the extension module itself is not a codec lib
        ):
            self.assertFalse(chk.is_forbidden(name), name)


class TestScanPaths(unittest.TestCase):
    def test_clean_tree_has_no_hits(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "usr/lib").mkdir(parents=True)
            (Path(d) / "usr/lib/libssl.so.3").touch()
            (Path(d) / "usr/lib/libc.so.6").touch()
            self.assertEqual(chk.scan_paths([d]), [])

    def test_dirty_tree_reports_all_codec_libs(self):
        with tempfile.TemporaryDirectory() as d:
            libdir = Path(d) / "vss-agent/.venv/lib"
            libdir.mkdir(parents=True)
            (libdir / "libavcodec-156beeea.so.62.11.100").touch()
            (libdir / "libswscale.so.9").touch()
            (libdir / "libssl.so.3").touch()  # must be ignored
            hits = chk.scan_paths([d])
            self.assertEqual(len(hits), 2)
            self.assertTrue(all("libav" in h or "libsw" in h for h in hits))

    def test_main_returns_nonzero_on_hit(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "libx265.so.199").touch()
            self.assertEqual(chk.main(["--path", d]), 1)

    def test_main_returns_zero_when_clean(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "libc.so.6").touch()
            self.assertEqual(chk.main(["--path", d]), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
