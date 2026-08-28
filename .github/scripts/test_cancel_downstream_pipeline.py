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
from urllib.error import URLError

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
    def test_matches_this_runs_correlation_token(self):
        pipelines = [
            {"id": 10, "created_at": "2026-08-28T08:12:30Z"},
            {"id": 11, "created_at": "2026-08-28T07:00:00Z"},
        ]
        variables = {
            10: [{"key": module.CORRELATION_VARIABLE, "value": "gh-1-1-aaa"}],
            11: [{"key": module.CORRELATION_VARIABLE, "value": "gh-1-1-aaa"}],
        }
        self.assertEqual(
            module.matching_pipeline_ids(
                pipelines,
                variables,
                correlation_id="gh-1-1-aaa",
                started_at="2026-08-28T08:12:00Z",
            ),
            [10],
        )

    def test_leaves_a_concurrent_run_on_the_same_sha_alone(self):
        """Two runs share ref + VSS_SUBMODULE_HASH; only ours may be cancelled."""
        pipelines = [
            {"id": 20, "created_at": "2026-08-28T08:12:30Z"},
            {"id": 21, "created_at": "2026-08-28T08:12:31Z"},
        ]
        variables = {
            20: [
                {"key": "VSS_SUBMODULE_HASH", "value": "abc"},
                {"key": module.CORRELATION_VARIABLE, "value": "gh-1-1-mine"},
            ],
            21: [
                {"key": "VSS_SUBMODULE_HASH", "value": "abc"},
                {"key": module.CORRELATION_VARIABLE, "value": "gh-2-1-theirs"},
            ],
        }
        self.assertEqual(
            module.matching_pipeline_ids(
                pipelines,
                variables,
                correlation_id="gh-1-1-mine",
                started_at="2026-08-28T08:12:00Z",
            ),
            [20],
        )

    def test_no_correlation_token_cancels_nothing(self):
        self.assertEqual(
            module.matching_pipeline_ids(
                [{"id": 10, "created_at": "2026-08-28T08:12:30Z"}],
                {10: [{"key": "VSS_SUBMODULE_HASH", "value": "abc"}]},
                correlation_id="",
                started_at="2026-08-28T08:12:00Z",
            ),
            [],
        )

    def test_skips_a_gone_candidate_and_still_finds_ours(self):
        class JsonResponse:
            def __init__(self, payload: object) -> None:
                self._body = json.dumps(payload).encode("utf-8")

            def read(self) -> bytes:
                return self._body

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        def open_func(req):
            url = req.full_url
            if "/pipelines?" in url:
                if "status=running" in url:
                    return JsonResponse(
                        [
                            {"id": 10, "created_at": "2026-08-28T08:12:30Z"},
                            {"id": 11, "created_at": "2026-08-28T08:12:31Z"},
                        ]
                    )
                return JsonResponse([])
            if url.endswith("/pipelines/10/variables"):
                raise HTTPError(
                    url,
                    404,
                    "Not Found",
                    hdrs=None,  # type: ignore[arg-type]
                    fp=io.BytesIO(b"{}"),
                )
            if url.endswith("/pipelines/11/variables"):
                return JsonResponse(
                    [{"key": module.CORRELATION_VARIABLE, "value": "gh-1-1-mine"}]
                )
            raise AssertionError(url)

        self.assertEqual(
            module.discover_matching_pipeline_ids(
                "https://gitlab.example/api/v4",
                "token",
                project="1",
                ref="main",
                correlation_id="gh-1-1-mine",
                started_at="2026-08-28T08:12:00Z",
                open_func=open_func,
            ),
            [11],
        )

    def test_unauthorized_still_aborts_discovery(self):
        def open_func(req):
            raise HTTPError(
                req.full_url,
                401,
                "Unauthorized",
                hdrs=None,  # type: ignore[arg-type]
                fp=io.BytesIO(b"{}"),
            )

        with self.assertRaises(SystemExit):
            module.fetch_pipeline_variables(
                "https://gitlab.example/api/v4",
                "token",
                "1",
                10,
                open_func=open_func,
            )

    def test_forbidden_variables_read_still_aborts_discovery(self):
        def open_func(req):
            raise HTTPError(
                req.full_url,
                403,
                "Forbidden",
                hdrs=None,  # type: ignore[arg-type]
                fp=io.BytesIO(b"{}"),
            )

        with self.assertRaises(SystemExit):
            module.fetch_pipeline_variables(
                "https://gitlab.example/api/v4",
                "token",
                "1",
                10,
                open_func=open_func,
            )

    def test_listing_503_is_retried_then_finds_ours(self):
        class JsonResponse:
            def __init__(self, payload: object) -> None:
                self._body = json.dumps(payload).encode("utf-8")

            def read(self) -> bytes:
                return self._body

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        waves = {"n": 0}

        def open_func(req):
            url = req.full_url
            if "/pipelines?" in url:
                if "status=created" in url:
                    waves["n"] += 1
                    if waves["n"] == 1:
                        raise HTTPError(
                            url,
                            503,
                            "Service Unavailable",
                            hdrs=None,  # type: ignore[arg-type]
                            fp=io.BytesIO(b"{}"),
                        )
                    return JsonResponse([])
                if "status=running" in url:
                    return JsonResponse(
                        [{"id": 11, "created_at": "2026-08-28T08:12:31Z"}]
                    )
                return JsonResponse([])
            if url.endswith("/pipelines/11/variables"):
                return JsonResponse(
                    [{"key": module.CORRELATION_VARIABLE, "value": "gh-1-1-mine"}]
                )
            raise AssertionError(url)

        self.assertEqual(
            module.search_matching_pipeline_ids(
                "https://gitlab.example/api/v4",
                "token",
                project="1",
                ref="main",
                correlation_id="gh-1-1-mine",
                started_at="2026-08-28T08:12:00Z",
                attempts=3,
                delay=0,
                open_func=open_func,
            ),
            [11],
        )
        self.assertEqual(waves["n"], 2)

    def test_listing_5xx_exhausted_fails_cleanup(self):
        def open_func(req):
            raise HTTPError(
                req.full_url,
                502,
                "Bad Gateway",
                hdrs=None,  # type: ignore[arg-type]
                fp=io.BytesIO(b"{}"),
            )

        with self.assertRaises(SystemExit):
            module.search_matching_pipeline_ids(
                "https://gitlab.example/api/v4",
                "token",
                project="1",
                ref="main",
                correlation_id="gh-1-1-mine",
                started_at="2026-08-28T08:12:00Z",
                attempts=2,
                delay=0,
                open_func=open_func,
            )

    def test_listing_connection_error_is_retryable(self):
        def open_func(_req):
            raise URLError("temporary failure")

        with self.assertRaises(module.GitLabTransientError):
            module.list_ref_pipelines(
                "https://gitlab.example/api/v4",
                "token",
                "1",
                "main",
                open_func=open_func,
            )

    def test_variables_503_is_retried_then_finds_ours(self):
        class JsonResponse:
            def __init__(self, payload: object) -> None:
                self._body = json.dumps(payload).encode("utf-8")

            def read(self) -> bytes:
                return self._body

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        reads = {"n": 0}

        def open_func(req):
            url = req.full_url
            if "/pipelines?" in url:
                if "status=running" in url:
                    return JsonResponse(
                        [{"id": 11, "created_at": "2026-08-28T08:12:31Z"}]
                    )
                return JsonResponse([])
            if url.endswith("/pipelines/11/variables"):
                reads["n"] += 1
                if reads["n"] == 1:
                    raise HTTPError(
                        url,
                        503,
                        "Service Unavailable",
                        hdrs=None,  # type: ignore[arg-type]
                        fp=io.BytesIO(b"{}"),
                    )
                return JsonResponse(
                    [{"key": module.CORRELATION_VARIABLE, "value": "gh-1-1-mine"}]
                )
            raise AssertionError(url)

        self.assertEqual(
            module.search_matching_pipeline_ids(
                "https://gitlab.example/api/v4",
                "token",
                project="1",
                ref="main",
                correlation_id="gh-1-1-mine",
                started_at="2026-08-28T08:12:00Z",
                attempts=3,
                delay=0,
                open_func=open_func,
            ),
            [11],
        )
        self.assertEqual(reads["n"], 2)

    def test_variables_5xx_exhausted_fails_cleanup(self):
        class JsonResponse:
            def __init__(self, payload: object) -> None:
                self._body = json.dumps(payload).encode("utf-8")

            def read(self) -> bytes:
                return self._body

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        def open_func(req):
            url = req.full_url
            if "/pipelines?" in url:
                if "status=running" in url:
                    return JsonResponse(
                        [{"id": 11, "created_at": "2026-08-28T08:12:31Z"}]
                    )
                return JsonResponse([])
            if url.endswith("/pipelines/11/variables"):
                raise HTTPError(
                    url,
                    502,
                    "Bad Gateway",
                    hdrs=None,  # type: ignore[arg-type]
                    fp=io.BytesIO(b"{}"),
                )
            raise AssertionError(url)

        with self.assertRaises(SystemExit):
            module.search_matching_pipeline_ids(
                "https://gitlab.example/api/v4",
                "token",
                project="1",
                ref="main",
                correlation_id="gh-1-1-mine",
                started_at="2026-08-28T08:12:00Z",
                attempts=2,
                delay=0,
                open_func=open_func,
            )

    def test_sibling_variables_5xx_does_not_hide_ours(self):
        class JsonResponse:
            def __init__(self, payload: object) -> None:
                self._body = json.dumps(payload).encode("utf-8")

            def read(self) -> bytes:
                return self._body

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        def open_func(req):
            url = req.full_url
            if "/pipelines?" in url:
                if "status=running" in url:
                    return JsonResponse(
                        [
                            {"id": 10, "created_at": "2026-08-28T08:12:30Z"},
                            {"id": 11, "created_at": "2026-08-28T08:12:31Z"},
                        ]
                    )
                return JsonResponse([])
            if url.endswith("/pipelines/10/variables"):
                raise HTTPError(
                    url,
                    503,
                    "Service Unavailable",
                    hdrs=None,  # type: ignore[arg-type]
                    fp=io.BytesIO(b"{}"),
                )
            if url.endswith("/pipelines/11/variables"):
                return JsonResponse(
                    [{"key": module.CORRELATION_VARIABLE, "value": "gh-1-1-mine"}]
                )
            raise AssertionError(url)

        self.assertEqual(
            module.discover_matching_pipeline_ids(
                "https://gitlab.example/api/v4",
                "token",
                project="1",
                ref="main",
                correlation_id="gh-1-1-mine",
                started_at="2026-08-28T08:12:00Z",
                open_func=open_func,
            ),
            [11],
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
