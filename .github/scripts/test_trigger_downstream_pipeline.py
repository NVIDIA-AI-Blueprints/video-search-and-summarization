#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from urllib.parse import parse_qs
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).with_name("trigger_downstream_pipeline.py")
SPEC = importlib.util.spec_from_file_location("trigger_downstream_pipeline", SCRIPT)
assert SPEC
module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(module)


class ExtraPipelineVariablesTest(unittest.TestCase):
    def test_accepts_string_map(self):
        with mock.patch.dict(
            os.environ,
            {
                "DOWNSTREAM_EXTRA_VARIABLES_JSON": (
                    '{"BUILD_TYPE":"ghcr-promotion","VSS_PROMOTION_TAG":"develop-abc"}'
                )
            },
            clear=True,
        ):
            self.assertEqual(
                module.extra_pipeline_variables(),
                {
                    "BUILD_TYPE": "ghcr-promotion",
                    "VSS_PROMOTION_TAG": "develop-abc",
                },
            )

    def test_rejects_reserved_variable_override(self):
        with mock.patch.dict(
            os.environ,
            {"DOWNSTREAM_EXTRA_VARIABLES_JSON": '{"VSS_SUBMODULE_HASH":"wrong"}'},
            clear=True,
        ):
            with self.assertRaises(SystemExit):
                module.extra_pipeline_variables()

    def test_empty_value_is_noop(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(module.extra_pipeline_variables(), {})

    def test_pipeline_payload_is_pure_and_contains_all_variables(self):
        payload = module.pipeline_request_data(
            ref="main",
            variable_name="VSS_SUBMODULE_HASH",
            commit_sha="a" * 40,
            target_branch="develop",
            compare_branch="pull-request/1190",
            extra_variables={"BUILD_TYPE": "ghcr-nightly"},
        )
        parsed = parse_qs(payload.decode())
        self.assertEqual(parsed["ref"], ["main"])
        self.assertEqual(
            parsed["variables[][key]"],
            [
                "VSS_SUBMODULE_HASH",
                "VSS_TARGET_BRANCH",
                "VSS_COMPARE_BRANCH",
                "BUILD_TYPE",
            ],
        )
        self.assertEqual(parsed["variables[][value]"][-1], "ghcr-nightly")

    def test_main_dry_run_performs_no_network(self):
        env = {
            "DOWNSTREAM_DRY_RUN": "true",
            "DOWNSTREAM_PROJECT_PATH": "metromind/ci-vss-oss",
            "GITHUB_SHA": "a" * 40,
            "GITHUB_REF_NAME": "develop",
            "DOWNSTREAM_REF": "main",
        }
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(
            module, "fetch_project_id"
        ) as fetch:
            self.assertEqual(module.main(), 0)
            fetch.assert_not_called()

    def test_explicit_downstream_branches_support_recovery_workflows(self):
        with mock.patch.dict(
            os.environ,
            {
                "GITHUB_REF_NAME": "main",
                "DOWNSTREAM_TARGET_BRANCH": "develop",
                "DOWNSTREAM_COMPARE_BRANCH": "develop",
            },
            clear=True,
        ):
            self.assertEqual(module.resolve_branches(), ("develop", "develop"))

    def test_explicit_downstream_branches_must_be_a_pair(self):
        with mock.patch.dict(
            os.environ,
            {"DOWNSTREAM_TARGET_BRANCH": "develop"},
            clear=True,
        ):
            with self.assertRaises(SystemExit):
                module.resolve_branches()

    def test_correlation_id_is_unique_per_trigger_attempt(self):
        with mock.patch.dict(
            os.environ,
            {"GITHUB_RUN_ID": "42", "GITHUB_RUN_ATTEMPT": "2"},
            clear=False,
        ):
            first = module.new_correlation_id()
            second = module.new_correlation_id()
        self.assertTrue(first.startswith("gh-42-2-"))
        self.assertNotEqual(first, second)

    def test_correlation_id_is_sent_as_a_pipeline_variable(self):
        payload = module.pipeline_request_data(
            ref="main",
            variable_name="VSS_SUBMODULE_HASH",
            commit_sha="a" * 40,
            target_branch="develop",
            compare_branch="pull-request/1906",
            correlation_id="gh-42-2-abc",
        )
        parsed = parse_qs(payload.decode())
        self.assertIn(module.CORRELATION_VARIABLE, parsed["variables[][key]"])
        self.assertIn("gh-42-2-abc", parsed["variables[][value]"])

    def test_extra_variables_cannot_forge_the_correlation_token(self):
        with mock.patch.dict(
            os.environ,
            {
                "DOWNSTREAM_EXTRA_VARIABLES_JSON": json.dumps(
                    {module.CORRELATION_VARIABLE: "forged"}
                )
            },
            clear=True,
        ):
            with self.assertRaises(SystemExit):
                module.extra_pipeline_variables()

    def test_persist_handoff_writes_ids_before_step_outputs_would_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "handoff.json"
            with mock.patch.dict(
                os.environ, {"DOWNSTREAM_HANDOFF_PATH": str(path)}, clear=False
            ):
                module.persist_handoff(project_id=4, pipeline_id=88)
            self.assertEqual(
                json.loads(path.read_text()),
                {"project_id": "4", "pipeline_id": "88"},
            )

    def test_handoff_path_keys_by_github_run_so_runners_do_not_reuse_a_file(self):
        with mock.patch.dict(
            os.environ,
            {
                "GITHUB_RUN_ID": "77",
                "GITHUB_RUN_ATTEMPT": "2",
                "DOWNSTREAM_HANDOFF_PATH": "",
            },
            clear=False,
        ):
            self.assertEqual(
                module.handoff_path(),
                ".ci/downstream-pipeline-77-2.json",
            )

    def test_handoff_path_override_still_wins(self):
        with mock.patch.dict(
            os.environ,
            {
                "DOWNSTREAM_HANDOFF_PATH": "/tmp/explicit.json",
                "GITHUB_RUN_ID": "77",
            },
            clear=False,
        ):
            self.assertEqual(module.handoff_path(), "/tmp/explicit.json")


if __name__ == "__main__":
    unittest.main(verbosity=2)
