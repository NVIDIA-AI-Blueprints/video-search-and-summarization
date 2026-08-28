#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import importlib.util
import io
import unittest
from pathlib import Path
from urllib.error import HTTPError

SCRIPT = Path(__file__).with_name("cancel_downstream_pipeline.py")
SPEC = importlib.util.spec_from_file_location("cancel_downstream_pipeline", SCRIPT)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class FakeResponse:
    def read(self) -> bytes:
        return b"{}"

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class CancelPipelineTest(unittest.TestCase):
    def test_success(self):
        self.assertEqual(
            module.cancel_pipeline(
                "https://gitlab.example/api/v4",
                "token",
                1,
                99,
                open_func=lambda _req: FakeResponse(),
            ),
            "cancelled",
        )

    def test_already_finished_is_ok(self):
        def open_func(_req):
            raise HTTPError(
                "https://gitlab.example/api/v4/projects/1/pipelines/99/cancel",
                409,
                "Conflict",
                hdrs=None,  # type: ignore[arg-type]
                fp=io.BytesIO(b"{}"),
            )

        self.assertEqual(
            module.cancel_pipeline(
                "https://gitlab.example/api/v4",
                "token",
                1,
                99,
                open_func=open_func,
            ),
            "already finished (409)",
        )


class WorkflowWiringTest(unittest.TestCase):
    def test_ci_cancels_downstream_when_the_github_job_is_cancelled(self):
        ci = (
            Path(__file__).resolve().parent.parent / "workflows" / "ci.yml"
        ).read_text()
        self.assertIn("cancel_downstream_pipeline.py", ci)
        self.assertIn("if: cancelled()", ci)

    def test_ci_runs_these_tests(self):
        ci = (
            Path(__file__).resolve().parent.parent / "workflows" / "ci.yml"
        ).read_text()
        self.assertIn(
            "python3 .github/scripts/test_cancel_downstream_pipeline.py", ci
        )

    def test_downstream_trigger_workflows_cancel_gitlab_on_github_cancel(self):
        workflows = Path(__file__).resolve().parent.parent / "workflows"
        for name in (
            "ci.yml",
            "spatialai-data-utils.yml",
            "osrb-review.yml",
        ):
            text = (workflows / name).read_text()
            self.assertIn(
                "cancel_downstream_pipeline.py",
                text,
                f"{name} must cancel GitLab when the GitHub job is cancelled",
            )


if __name__ == "__main__":
    unittest.main()
