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
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import prepare_nightly_promotion as module  # noqa: E402
from prepare_nightly_promotion import (  # noqa: E402
    promotion_variables,
    select_build_release_set,
    select_build_run,
)


def release_set() -> dict:
    return {
        "release_set_id": "sha256:" + "1" * 64,
        "images": [
            {
                "name": "vss-agent",
                "strategy": "build",
                "image": "ghcr.io/nvidia-ai-blueprints/vss/vss-agent",
                "tag": "develop-deadbeef1234",
                "digest": "sha256:" + "2" * 64,
            }
        ],
    }


class NightlyPromotionTest(unittest.TestCase):
    def test_selects_exact_green_develop_run(self):
        runs = [
            {
                "id": 1,
                "head_branch": "feature",
                "head_sha": "a" * 40,
                "conclusion": "success",
            },
            {
                "id": 2,
                "head_branch": "develop",
                "head_sha": "b" * 40,
                "conclusion": "success",
            },
        ]
        self.assertEqual(select_build_run(runs)["id"], 2)
        self.assertIsNone(select_build_run(runs, "c" * 40))

    def test_skips_reuse_only_runs_and_paginates(self):
        pages = {
            1: [
                {
                    "id": 1,
                    "head_branch": "develop",
                    "head_sha": "a" * 40,
                    "conclusion": "success",
                },
                {
                    "id": 2,
                    "head_branch": "develop",
                    "head_sha": "b" * 40,
                    "conclusion": "success",
                },
            ],
            2: [
                {
                    "id": 3,
                    "head_branch": "develop",
                    "head_sha": "c" * 40,
                    "conclusion": "success",
                }
            ],
        }

        class FakeApi:
            def request(self, method, url):
                self.method = method
                page = int(url.split("page=")[-1])
                return {"workflow_runs": pages.get(page, [])}

        reuse_only = {"images": [{"strategy": "reuse-pinned"}]}
        release_sets = {1: reuse_only, 2: reuse_only, 3: release_set()}
        with patch(
            "prepare_nightly_promotion.download_release_set_artifact",
            side_effect=lambda api, repository, run_id: release_sets[run_id],
        ):
            selected = select_build_release_set(
                FakeApi(),
                "org/repo",
                per_page=2,
            )
        self.assertIsNotNone(selected)
        run, payload = selected
        self.assertEqual(run["id"], 3)
        self.assertEqual(payload, release_set())

    def test_promotion_variables_preserve_tag_and_manifest(self):
        payload = release_set()
        tag, variables = promotion_variables(
            payload,
            requested_tag="develop-deadbeef1234",
        )
        self.assertEqual(tag, "develop-deadbeef1234")
        self.assertEqual(variables["BUILD_TYPE"], "ghcr-nightly")
        self.assertEqual(variables["VSS_ACCEPTANCE_REGISTRY"], "ghcr")
        self.assertEqual(
            json.loads(base64.b64decode(variables["VSS_RELEASE_SET_B64"])),
            payload,
        )

    def test_artifacts_promotion_config_is_not_sent_to_test_pipeline(self):
        _, variables = promotion_variables(release_set())
        self.assertNotIn(
            "AGENT_UI_ARTIFACTS_PROMOTION_CONFIG_PATH", variables
        )

    def test_main_with_release_set_file_performs_no_network(self):
        payload = release_set()
        payload["source"] = {"commit": "a" * 40}
        payload["release_set_id"] = "sha256:" + "1" * 64
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.json"
            env_path = Path(tmp) / "github.env"
            output_path = Path(tmp) / "github.output"
            selected_path = Path(tmp) / "selected.json"
            source.write_text(json.dumps(payload))
            argv = [
                "prepare_nightly_promotion.py",
                "--release-set",
                str(source),
                "--release-set-output",
                str(selected_path),
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
            ), mock.patch.object(module, "select_build_release_set") as select:
                self.assertEqual(module.main(), 0)
                select.assert_not_called()
            self.assertTrue(selected_path.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
