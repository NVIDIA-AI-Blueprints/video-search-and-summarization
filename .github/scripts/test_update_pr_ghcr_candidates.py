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

SCRIPT = Path(__file__).with_name("update_pr_ghcr_candidates.py")
SPEC = importlib.util.spec_from_file_location("update_pr_ghcr_candidates", SCRIPT)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class CandidateCommentTest(unittest.TestCase):
    def test_pr_number_only_accepts_synthetic_ref(self):
        self.assertEqual(module.pr_number("pull-request/1190"), 1190)
        self.assertIsNone(module.pr_number("develop"))
        self.assertIsNone(module.pr_number("pull-request/not-a-number"))

    def test_select_release_set_run_requires_exact_ref_sha_and_success(self):
        runs = [
            {
                "id": 1,
                "head_sha": "a" * 40,
                "head_branch": "pull-request/1190",
                "conclusion": "failure",
            },
            {
                "id": 2,
                "head_sha": "a" * 40,
                "head_branch": "pull-request/1190",
                "conclusion": "success",
            },
        ]
        self.assertEqual(
            module.select_release_set_run(
                runs, "a" * 40, "pull-request/1190"
            )["id"],
            2,
        )

    def test_comment_lists_only_immutable_ghcr_builds(self):
        release_set = {
            "release_set_id": "sha256:" + "1" * 64,
            "images": [
                {
                    "name": "vss-agent",
                    "strategy": "build",
                    "image": "ghcr.io/nvidia-ai-blueprints/vss/vss-agent",
                    "tag": "pr-1190-deadbeef",
                    "digest": "sha256:" + "2" * 64,
                },
                {
                    "name": "vss-configurator",
                    "strategy": "reuse-pinned",
                    "image": "nvcr.io/nvidia/vss-core/vss-configurator",
                    "tag": "3.2.1",
                    "digest": None,
                },
            ],
        }
        body = module.render_comment(release_set, "a" * 40)
        self.assertIn(module.MARKER, body)
        self.assertIn("ghcr.io/nvidia-ai-blueprints/vss/vss-agent", body)
        self.assertIn("pr-1190-latest", body)
        self.assertNotIn("vss-configurator", body)
        self.assertIn("does not rebuild", body)

    def test_moving_alias_derives_from_immutable_tag(self):
        self.assertEqual(module.moving_alias("develop-deadbeef"), "develop-latest")
        self.assertEqual(
            module.moving_alias("pr-1190-deadbeef"), "pr-1190-latest"
        )
        self.assertEqual(module.moving_alias("release-3.2.0"), "")

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

    def test_main_dry_run_needs_no_network(self):
        sha = "a" * 40
        release_set = {
            "release_set_id": "sha256:" + "1" * 64,
            "source": {"commit": sha},
            "images": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "release-set.json"
            path.write_text(json.dumps(release_set))
            argv = [
                "update_pr_ghcr_candidates.py",
                "--repository",
                "org/repo",
                "--sha",
                sha,
                "--ref-name",
                "pull-request/1190",
                "--release-set",
                str(path),
                "--dry-run",
            ]
            with mock.patch("sys.argv", argv), mock.patch.dict(
                os.environ, {}, clear=True
            ):
                self.assertEqual(module.main(), 0)

    def test_upsert_comment_finds_marker_after_first_page(self):
        class FakeApi:
            def __init__(self):
                self.calls = []

            def request(self, method, path, payload=None):
                self.calls.append((method, path, payload))
                if method == "GET" and path.endswith("&page=1"):
                    return [{"id": index, "body": "other"} for index in range(100)]
                if method == "GET" and path.endswith("&page=2"):
                    return [{"id": 999, "body": module.MARKER}]
                return {}

        api = FakeApi()
        module.upsert_comment(api, "org/repo", 1190, "updated")
        self.assertIn(
            (
                "PATCH",
                "/repos/org/repo/issues/comments/999",
                {"body": "updated"},
            ),
            api.calls,
        )
        self.assertFalse(any(method == "POST" for method, _, _ in api.calls))

    def test_upsert_comment_stops_repeating_full_pages(self):
        class FakeApi:
            def __init__(self):
                self.calls = []

            def request(self, method, path, payload=None):
                self.calls.append((method, path, payload))
                page = int(path.rsplit("page=", 1)[1])
                return [
                    {"id": page * 100 + index, "body": "other"}
                    for index in range(100)
                ]

        api = FakeApi()
        with mock.patch.object(module, "MAX_COMMENT_PAGES", 3):
            with self.assertRaisesRegex(RuntimeError, "exceeded 3 pages"):
                module.upsert_comment(api, "org/repo", 1190, "updated")
        self.assertEqual(
            [path for method, path, _ in api.calls if method == "GET"],
            [
                "/repos/org/repo/issues/1190/comments?per_page=100&page=1",
                "/repos/org/repo/issues/1190/comments?per_page=100&page=2",
                "/repos/org/repo/issues/1190/comments?per_page=100&page=3",
            ],
        )
        self.assertFalse(
            any(method in {"POST", "PATCH"} for method, _, _ in api.calls)
        )

    def test_upsert_comment_rejects_repeated_page(self):
        class FakeApi:
            def request(self, method, path, payload=None):
                return [{"id": index, "body": "other"} for index in range(100)]

        with self.assertRaisesRegex(RuntimeError, "repeated a page"):
            module.upsert_comment(FakeApi(), "org/repo", 1190, "updated")


if __name__ == "__main__":
    module.enforce_memory_ceiling()
    unittest.main(verbosity=2)
