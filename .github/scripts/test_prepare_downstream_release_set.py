#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import prepare_downstream_release_set as module  # noqa: E402
from prepare_downstream_release_set import downstream_variables  # noqa: E402


class GhcrBuildEntriesTest(unittest.TestCase):
    def test_requires_new_ghcr_build(self):
        self.assertTrue(
            module.has_ghcr_build_entries(
                {
                    "images": [
                        {
                            "strategy": "build",
                            "image": "ghcr.io/nvidia/vss-agent",
                        }
                    ]
                }
            )
        )
        self.assertFalse(
            module.has_ghcr_build_entries(
                {
                    "images": [
                        {
                            "strategy": "reuse-pinned",
                            "image": "ghcr.io/nvidia/vss-agent",
                        }
                    ]
                }
            )
        )
        self.assertFalse(
            module.has_ghcr_build_entries(
                {
                    "images": [
                        {
                            "strategy": "build",
                            "image": "nvcr.io/nvidia/vss-agent",
                        }
                    ]
                }
            )
        )


class DownstreamVariablesTest(unittest.TestCase):
    def test_encodes_exact_release_set_for_acceptance(self):
        release_set = {
            "schema_version": 1,
            "release_set_id": "sha256:" + "1" * 64,
            "source": {"commit": "a" * 40},
            "images": [{"name": "vss-agent"}],
        }
        variables = downstream_variables(release_set)
        self.assertEqual(variables["BUILD_TYPE"], "ghcr-acceptance")
        self.assertEqual(
            variables["VSS_RELEASE_SET_ID"], release_set["release_set_id"]
        )
        decoded = json.loads(base64.b64decode(variables["VSS_RELEASE_SET_B64"]))
        self.assertEqual(decoded, release_set)

    def test_main_with_release_set_file_performs_no_network(self):
        sha = "a" * 40
        release_set = {
            "source": {"commit": sha},
            "release_set_id": "sha256:" + "1" * 64,
            "images": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            release_path = Path(tmp) / "release-set.json"
            release_output_path = Path(tmp) / "handoff/release-set.json"
            env_path = Path(tmp) / "github.env"
            output_path = Path(tmp) / "github.output"
            release_path.write_text(json.dumps(release_set))
            argv = [
                "prepare_downstream_release_set.py",
                "--sha",
                sha,
                "--release-set",
                str(release_path),
                "--release-set-output",
                str(release_output_path),
            ]
            with mock.patch("sys.argv", argv), mock.patch.dict(
                os.environ,
                {
                    "GITHUB_ENV": str(env_path),
                    "GITHUB_OUTPUT": str(output_path),
                },
                clear=True,
            ), mock.patch.object(
                module, "validate_release_set", return_value=[]
            ), mock.patch.object(module, "download_release_set") as download:
                self.assertEqual(module.main(), 0)
                download.assert_not_called()
            self.assertIn("DOWNSTREAM_EXTRA_VARIABLES_JSON", env_path.read_text())
            self.assertEqual(
                output_path.read_text(),
                "has_ghcr_build_entries=false\n",
            )
            self.assertEqual(json.loads(release_output_path.read_text()), release_set)


if __name__ == "__main__":
    unittest.main(verbosity=2)
