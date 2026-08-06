#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[4]


def _load_smoke_runner():
    path = REPO_ROOT / ".github" / "skill-eval" / "nemoclaw" / "smoke_runner.py"
    spec = importlib.util.spec_from_file_location("nemoclaw_blocked_matrix_runner", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


smoke_runner = _load_smoke_runner()


class BlockedMatrixTest(unittest.TestCase):
    def _print_matrix(self, *args: str) -> tuple[list[dict[str, str]], str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.dict(
            os.environ,
            {
                "MANUAL_SKILLS_FILTER": "",
                "NEMOCLAW_ALL_SPECS": "",
                "NEMOCLAW_EVAL_PLATFORM": "",
                "NEMOCLAW_EVAL_PROFILE": "",
                "NEMOCLAW_EVAL_SPEC": "",
            },
        ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = smoke_runner.main(["--print-matrix", *args])
        self.assertEqual(rc, 0)
        return json.loads(stdout.getvalue())["include"], stderr.getvalue()

    def test_representative_all_skills_matrix_covers_canonical_inventory(self):
        rows, _stderr = self._print_matrix("--skills", "*")

        expected_skills = {
            "vss-ask-video",
            "vss-build-vision-agent",
            "vss-deploy-dense-captioning",
            "vss-deploy-detection-tracking-2d",
            "vss-deploy-detection-tracking-3d",
            "vss-deploy-profile",
            "vss-deploy-video-embedding",
            "vss-generate-video-calibration",
            "vss-generate-video-report",
            "vss-generate-video-report-rag",
            "vss-manage-alerts",
            "vss-manage-video-io-storage",
            "vss-query-analytics",
            "vss-search-archive",
            "vss-setup-behavior-analytics",
            "vss-setup-video-analytics-api",
            "vss-summarize-video",
        }
        blocked_skills = {
            "vss-build-vision-agent",
            "vss-deploy-detection-tracking-2d",
            "vss-deploy-detection-tracking-3d",
            "vss-deploy-video-embedding",
            "vss-generate-video-calibration",
            "vss-generate-video-report-rag",
            "vss-manage-video-io-storage",
            "vss-search-archive",
            "vss-setup-video-analytics-api",
        }

        self.assertEqual(len(rows), 17)
        self.assertEqual({row["skill"] for row in rows}, expected_skills)
        self.assertEqual(len({row["skill"] for row in rows}), len(rows))
        self.assertEqual(
            {row["skill"] for row in rows if row["kind"] == "blocked"},
            blocked_skills,
        )
        self.assertEqual(
            {row["skill"] for row in rows if row["kind"] == "eval"},
            expected_skills - blocked_skills,
        )

        slugs = [row["slug"] for row in rows]
        self.assertEqual(len(slugs), len(set(slugs)))
        self.assertTrue(all(re.fullmatch(r"[a-z0-9_-]+", slug) for slug in slugs))
        for row in rows:
            with self.subTest(skill=row["skill"]):
                if row["kind"] == "blocked":
                    self.assertTrue(row["reason"])
                    self.assertEqual(row["spec_stem"], "blocked")
                    self.assertEqual(row["task_limit"], "0")
                else:
                    self.assertNotIn("reason", row)
                    self.assertTrue(row["spec_path"].startswith("skills/"))

    def test_targeted_blocked_skill_gets_one_deterministic_blocked_row(self):
        rows, _stderr = self._print_matrix(
            "--skills",
            "vss-deploy-detection-tracking-2d",
        )

        self.assertEqual(
            rows,
            [
                {
                    "kind": "blocked",
                    "name": "vss-deploy-detection-tracking-2d/blocked",
                    "skill": "vss-deploy-detection-tracking-2d",
                    "spec_stem": "blocked",
                    "spec_path": "",
                    "platform": "",
                    "slug": "vss-deploy-detection-tracking-2d__blocked",
                    "task_limit": "0",
                    "reason": (
                        "vss-deploy-detection-tracking-2d/deploy-evals.json: "
                        "standalone host-Docker eval is not supported by the "
                        "NemoClaw MCP-only runner yet"
                    ),
                }
            ],
        )

    def test_all_specs_discovers_legacy_eval_directory_without_blocked_rows(self):
        rows, _stderr = self._print_matrix(
            "--skills",
            "vss-build-vision-agent",
            "--all-specs",
        )

        self.assertEqual(
            {row["spec_stem"] for row in rows},
            {
                "profile_an_1_stored_video_summarization_api",
                "profile_an_1_stored_video_summarization_runtime_harbor",
                "profile_at_1_alert_verification",
                "profile_combined_alerts_search_harbor",
                "profile_in_1_streaming_dense_captions",
                "profile_in_2_rt_cv_person_detection_harbor",
                "profile_in_3_ingestion_detection_embeddings_harbor",
                "profile_sop_1_compliance_monitoring",
            },
        )
        self.assertTrue(rows)
        self.assertTrue(all(row["kind"] == "eval" for row in rows))
        self.assertTrue(all("/eval/" in row["spec_path"] for row in rows))

    def test_unknown_skill_does_not_produce_a_successful_empty_matrix(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = smoke_runner.main(
                ["--print-matrix", "--skills", "definitely-not-a-skill"]
            )

        self.assertNotEqual(rc, 0)
        self.assertEqual(json.loads(stdout.getvalue()), {"include": []})


if __name__ == "__main__":
    unittest.main()
