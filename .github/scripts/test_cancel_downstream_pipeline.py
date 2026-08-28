#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError

SCRIPT = Path(__file__).with_name("cancel_downstream_pipeline.py")
SPEC = importlib.util.spec_from_file_location("cancel_downstream_pipeline", SCRIPT)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)

TRIGGER = Path(__file__).with_name("trigger_downstream_pipeline.py")
TSPEC = importlib.util.spec_from_file_location("trigger_downstream_pipeline", TRIGGER)
assert TSPEC and TSPEC.loader
trigger = importlib.util.module_from_spec(TSPEC)
TSPEC.loader.exec_module(trigger)


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
                99,
                project_id=1,
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
                99,
                project_id=1,
                open_func=open_func,
            ),
            "already finished (409)",
        )


class HandoffTest(unittest.TestCase):
    def test_handoff_survives_missing_step_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "downstream-pipeline.json"
            with mock.patch.dict(
                os.environ, {"DOWNSTREAM_HANDOFF_PATH": str(path)}, clear=False
            ):
                trigger.persist_handoff(project_id=11)
                trigger.persist_handoff(pipeline_id=99)
                with mock.patch.dict(
                    os.environ,
                    {
                        "DOWNSTREAM_HANDOFF_PATH": str(path),
                        "DOWNSTREAM_PROJECT_ID": "",
                        "DOWNSTREAM_PIPELINE_ID": "",
                    },
                    clear=False,
                ):
                    self.assertEqual(module.resolve_pipeline_ids(), ("11", "99"))
            self.assertEqual(
                json.loads(path.read_text()),
                {"project_id": "11", "pipeline_id": "99"},
            )

    def test_env_ids_win_over_handoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "downstream-pipeline.json"
            path.write_text('{"project_id":"1","pipeline_id":"2"}', encoding="utf-8")
            with mock.patch.dict(
                os.environ,
                {
                    "DOWNSTREAM_HANDOFF_PATH": str(path),
                    "DOWNSTREAM_PROJECT_ID": "8",
                    "DOWNSTREAM_PIPELINE_ID": "9",
                },
                clear=False,
            ):
                self.assertEqual(module.resolve_pipeline_ids(), ("8", "9"))


class SearchFallbackTest(unittest.TestCase):
    def test_matches_recent_pipeline_with_the_recorded_sha(self):
        pipelines = [
            {"id": 10, "created_at": "2026-08-28T08:12:30Z"},
            {"id": 11, "created_at": "2026-08-28T07:00:00Z"},
        ]
        variables = {
            10: [{"key": "VSS_SUBMODULE_HASH", "value": "abc"}],
            11: [{"key": "VSS_SUBMODULE_HASH", "value": "abc"}],
        }
        self.assertEqual(
            module.matching_pipeline_ids(
                pipelines,
                variables,
                variable_name="VSS_SUBMODULE_HASH",
                commit_sha="abc",
                started_at="2026-08-28T08:12:00Z",
            ),
            [10],
        )

    def test_ignores_pipelines_for_a_different_sha(self):
        self.assertEqual(
            module.matching_pipeline_ids(
                [{"id": 10, "created_at": "2026-08-28T08:12:30Z"}],
                {10: [{"key": "VSS_SUBMODULE_HASH", "value": "other"}]},
                variable_name="VSS_SUBMODULE_HASH",
                commit_sha="abc",
                started_at="2026-08-28T08:12:00Z",
            ),
            [],
        )


class WorkflowWiringTest(unittest.TestCase):
    def test_ci_cancels_downstream_when_the_github_job_is_cancelled(self):
        ci = (
            Path(__file__).resolve().parent.parent / "workflows" / "ci.yml"
        ).read_text()
        self.assertIn("cancel_downstream_pipeline.py", ci)
        self.assertIn("if: cancelled()", ci)
        self.assertNotIn(
            "steps.trigger.outputs.pipeline_id != ''",
            ci.split("Cancel downstream if this GitHub job was cancelled", 1)[1].split(
                "- name:", 1
            )[0],
        )

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
