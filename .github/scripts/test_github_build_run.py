#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).with_name("github_build_run.py")
SPEC = importlib.util.spec_from_file_location("github_build_run", SCRIPT)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class GitHubApiTest(unittest.TestCase):
    def test_select_release_set_run_requires_exact_ref_and_sha(self):
        """Exact match only; conclusion is the caller's business, not the selector's."""
        runs = [
            {"id": 1, "head_sha": "b" * 40, "head_branch": "pull-request/1190",
             "conclusion": "success", "created_at": "2026-07-01T00:00:00Z"},
            {"id": 2, "head_sha": "a" * 40, "head_branch": "develop",
             "conclusion": "success", "created_at": "2026-07-01T00:00:00Z"},
        ]
        self.assertIsNone(
            module.select_release_set_run(runs, "a" * 40, "pull-request/1190")
        )

    def test_select_release_set_run_returns_a_failed_run(self):
        """The whole point: a failed companion run must be visible to the caller.

        Filtering it out here is what made "finished unsuccessfully" look
        identical to "not created yet", so the poller waited out its budget.
        """
        runs = [
            {"id": 1, "head_sha": "a" * 40, "head_branch": "pull-request/1190",
             "conclusion": "failure", "created_at": "2026-07-01T00:00:00Z"},
        ]
        selected = module.select_release_set_run(runs, "a" * 40, "pull-request/1190")
        self.assertEqual(selected["id"], 1)
        self.assertEqual(selected["conclusion"], "failure")

    def test_select_release_set_run_prefers_the_newest_execution(self):
        """A newer workflow execution supersedes an older one, so a stale
        failure must not abort a build that is currently running.

        Note this is a distinct execution (new id), not a GitHub re-run.
        A re-run reuses the id; that case is covered in BuildWaitTest."""
        runs = [
            {"id": 1, "head_sha": "a" * 40, "head_branch": "pull-request/1190",
             "conclusion": "failure", "created_at": "2026-07-01T00:00:00Z"},
            {"id": 2, "head_sha": "a" * 40, "head_branch": "pull-request/1190",
             "conclusion": None, "status": "in_progress",
             "created_at": "2026-07-02T00:00:00Z"},
        ]
        self.assertEqual(
            module.select_release_set_run(runs, "a" * 40, "pull-request/1190")["id"], 2
        )


    def test_github_network_adapter_is_injected(self):
        requests = []

        class Headers:
            @staticmethod
            def get_content_type():
                return "application/json"

        class Response:
            headers = Headers()

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            @staticmethod
            def read(_size=-1):
                return json.dumps({"ok": True}).encode()

        def open_func(request, timeout):
            requests.append((request.full_url, timeout))
            return Response()

        api = module.GitHubApi("redacted", open_func=open_func)
        self.assertEqual(api.request("GET", "/example"), {"ok": True})
        self.assertEqual(requests, [("https://api.github.com/example", 60)])

    def test_github_network_adapter_omits_credentials_for_external_url(self):
        requests = []

        class Headers:
            @staticmethod
            def get_content_type():
                return "application/octet-stream"

        class Response:
            headers = Headers()

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            @staticmethod
            def read(_size=-1):
                return b"artifact"

        def open_func(request, timeout):
            requests.append((dict(request.header_items()), timeout))
            return Response()

        api = module.GitHubApi("secret-token", open_func=open_func)
        self.assertEqual(
            api.request("GET", "https://artifact.example/release-set.zip"),
            b"artifact",
        )
        headers, timeout = requests[0]
        self.assertNotIn("Authorization", headers)
        self.assertEqual(timeout, 60)

    def test_cross_origin_redirect_drops_github_authorization(self):
        request = module.urllib.request.Request(
            "https://api.github.com/repos/org/repo/actions/artifacts/1/zip",
            headers={"Authorization": "Bearer secret-token"},
        )
        redirected = module.SafeRedirectHandler().redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "https://results.example/release-set.zip?signature=redacted",
        )
        self.assertIsNotNone(redirected)
        assert redirected is not None
        self.assertIsNone(redirected.get_header("Authorization"))

    def test_same_origin_redirect_keeps_github_authorization(self):
        request = module.urllib.request.Request(
            "https://api.github.com/repos/org/repo/actions/artifacts/1/zip",
            headers={"Authorization": "Bearer secret-token"},
        )
        redirected = module.SafeRedirectHandler().redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "https://api.github.com/repos/org/repo/actions/artifacts/2/zip",
        )
        self.assertIsNotNone(redirected)
        assert redirected is not None
        self.assertEqual(
            redirected.get_header("Authorization"), "Bearer secret-token"
        )

    def test_github_network_adapter_rejects_oversized_response(self):
        class Headers:
            @staticmethod
            def get_content_type():
                return "application/octet-stream"

        class Response:
            headers = Headers()

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            @staticmethod
            def read(size=-1):
                return b"x" * size

        api = module.GitHubApi("redacted", open_func=lambda *_args, **_kwargs: Response())
        with mock.patch.object(module, "MAX_API_RESPONSE_BYTES", 4):
            with self.assertRaisesRegex(RuntimeError, "exceeded 4 bytes"):
                api.request("GET", "/example")

class BuildWaitTest(unittest.TestCase):
    """The defect: a failed companion build was invisible, so the poller
    retried 520 times over ~130 minutes for a run that would never appear."""

    SHA = "f" * 40
    REF = "pull-request/1190"

    def _api(self, *pages):
        api = mock.Mock()
        api.request.side_effect = [{"workflow_runs": p} for p in pages]
        return api

    def _run(self, **kw):
        base = {"id": 7, "run_number": 7, "run_attempt": 1,
                "head_sha": self.SHA, "head_branch": self.REF,
                "created_at": "2026-07-01T00:00:00Z", "html_url": "https://x/7"}
        base.update(kw)
        return base

    def test_query_does_not_filter_on_success(self):
        """Guards the fix itself. Asking GitHub only for successful runs is
        what made a failed companion build indistinguishable from an absent
        one, so the query must stay unfiltered by status."""
        api = self._api([self._run(status="completed", conclusion="success")])
        module.await_build_run(api, "o/r", self.SHA, self.REF, 520, 15)
        url = api.request.call_args[0][1]
        self.assertIn("head_sha=", url)
        self.assertNotIn("status=", url)
        self.assertIn("branch=", url)

    def test_failed_companion_run_raises_immediately_without_sleeping(self):
        api = self._api([self._run(status="completed", conclusion="failure")])
        with mock.patch.object(module.time, "sleep") as slept:
            with self.assertRaises(RuntimeError) as ctx:
                module.await_build_run(api, "o/r", self.SHA, self.REF, 520, 15)
        slept.assert_not_called()
        self.assertIn("failure", str(ctx.exception))
        self.assertIn("7", str(ctx.exception))
        self.assertEqual(api.request.call_count, 1)

    def test_every_terminal_non_success_conclusion_fails_immediately(self):
        for conclusion in ("cancelled", "timed_out", "startup_failure",
                           "stale", "neutral", "skipped"):
            with self.subTest(conclusion=conclusion):
                api = self._api([self._run(status="completed",
                                           conclusion=conclusion)])
                with mock.patch.object(module.time, "sleep") as slept:
                    with self.assertRaises(RuntimeError) as ctx:
                        module.await_build_run(
                            api, "o/r", self.SHA, self.REF, 520, 15)
                slept.assert_not_called()
                self.assertIn(conclusion, str(ctx.exception))

    def test_action_required_keeps_polling(self):
        """Approval can resume the same run, so this is not terminal."""
        api = self._api(
            [self._run(status="completed", conclusion="action_required")],
            [self._run(status="completed", conclusion="success")],
        )
        with mock.patch.object(module.time, "sleep"):
            run = module.await_build_run(api, "o/r", self.SHA, self.REF, 520, 15)
        self.assertEqual(run["conclusion"], "success")

    def test_in_flight_github_rerun_is_not_aborted(self):
        """A GitHub re-run reuses the run id and resets status to in_progress
        while `conclusion` may still read stale. Interpreting a conclusion on a
        non-completed run would abort somebody's retry."""
        api = self._api(
            [self._run(run_attempt=2, status="in_progress", conclusion="failure")],
            [self._run(run_attempt=2, status="completed", conclusion="success")],
        )
        with mock.patch.object(module.time, "sleep") as slept:
            out = module.await_build_run(api, "o/r", self.SHA, self.REF, 520, 15)
        self.assertEqual(out["conclusion"], "success")
        self.assertEqual(slept.call_count, 1)

    def test_in_progress_keeps_waiting_then_succeeds(self):
        api = self._api(
            [self._run(status="in_progress", conclusion=None)],
            [self._run(status="completed", conclusion="success")],
        )
        with mock.patch.object(module.time, "sleep") as slept:
            out = module.await_build_run(api, "o/r", self.SHA, self.REF, 520, 15)
        self.assertEqual(out["conclusion"], "success")
        self.assertEqual(slept.call_count, 1)

    def test_still_in_progress_at_the_final_attempt_times_out(self):
        """Guards the reset at the end of the loop: a non-terminal run on the
        last attempt must not be mistaken for a usable one."""
        api = mock.Mock()
        api.request.return_value = {
            "workflow_runs": [self._run(status="in_progress", conclusion=None)]
        }
        with mock.patch.object(module.time, "sleep") as slept:
            with self.assertRaises(RuntimeError) as ctx:
                module.await_build_run(api, "o/r", self.SHA, self.REF, 3, 15)
        self.assertEqual(api.request.call_count, 3)
        self.assertEqual(slept.call_count, 2)
        self.assertIn("3 polling attempts", str(ctx.exception))

    def test_absent_run_still_exhausts_its_budget(self):
        """Waiting is correct while the run does not exist yet. Note CI also
        triggers on `main`, where Build Dev Images never runs at all, so an
        absent run is not always transient."""
        api = mock.Mock()
        api.request.return_value = {"workflow_runs": []}
        with mock.patch.object(module.time, "sleep") as slept:
            with self.assertRaises(RuntimeError):
                module.await_build_run(api, "o/r", self.SHA, self.REF, 3, 15)
        self.assertEqual(api.request.call_count, 3)
        self.assertEqual(slept.call_count, 2)

    def test_older_failure_does_not_abort_a_newer_running_execution(self):
        api = self._api([
            self._run(id=1, run_number=1, status="completed", conclusion="failure",
                      created_at="2026-07-01T00:00:00Z"),
            self._run(id=2, run_number=2, status="in_progress", conclusion=None,
                      created_at="2026-07-02T00:00:00Z"),
        ], [
            self._run(id=2, run_number=2, status="completed", conclusion="success",
                      created_at="2026-07-02T00:00:00Z"),
        ])
        with mock.patch.object(module.time, "sleep"):
            out = module.await_build_run(api, "o/r", self.SHA, self.REF, 520, 15)
        self.assertEqual(out["conclusion"], "success")

if __name__ == "__main__":
    module.enforce_memory_ceiling()
    unittest.main(verbosity=2)
