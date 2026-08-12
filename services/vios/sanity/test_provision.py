# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

import provision


class ProvisioningTest(unittest.TestCase):
    @mock.patch("subprocess.run")
    def test_nvenc_requires_working_h264_and_hevc_encoders(self, run):
        run.return_value = SimpleNamespace(returncode=0, stderr="")

        self.assertTrue(provision._nvenc_available())

        self.assertEqual(run.call_count, 2)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertIn("h264_nvenc", commands[0])
        self.assertIn("hevc_nvenc", commands[1])
        self.assertIn("color=c=black:s=640x480:r=1", commands[0])
        self.assertNotIn("-encoders", commands[0])

    @mock.patch("subprocess.run")
    def test_nvenc_is_unavailable_when_hardware_probe_fails(self, run):
        run.return_value = SimpleNamespace(
            returncode=1,
            stderr="Cannot load libcuda",
        )

        self.assertFalse(provision._nvenc_available())
        run.assert_called_once()

    @mock.patch("subprocess.run", side_effect=FileNotFoundError("ffmpeg"))
    def test_nvenc_is_unavailable_when_ffmpeg_cannot_run(self, run):
        self.assertFalse(provision._nvenc_available())
        run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
