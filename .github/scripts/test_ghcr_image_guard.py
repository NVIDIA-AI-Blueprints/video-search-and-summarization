#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for ghcr_image_guard.py (pure decision logic; the registry I/O
lives in check_container_tag_source.read_image_manifest_labels, tested via its
own gate). Run directly:

    python3 .github/scripts/test_ghcr_image_guard.py
"""

from __future__ import annotations

import base64
import os
import sys
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_container_tag_source import (  # noqa: E402
    ImageManifestLabels,
    _fetch_bearer_token,
)
from ghcr_image_guard import (  # noqa: E402
    bakes_release_line,
    preflight_decision,
    reuse_decision,
    verify_decision,
)

TREE = "a" * 40
OTHER_TREE = "b" * 40


def labels(tree=TREE, path="services/agent", name="vss-agent", line=None):
    return ImageManifestLabels(source_tree_sha=tree, source_path=path, image_name=name, release_line=line)


class PreflightTest(unittest.TestCase):
    def test_missing_tag_404_builds(self):
        action, _ = preflight_decision(
            None, "index fetch failed: GET https://x returned 404: Not Found", TREE
        )
        self.assertEqual(action, "build")

    def test_network_error_fails_closed(self):
        action, message = preflight_decision(
            None, "index fetch failed: network error fetching https://x", TREE
        )
        self.assertEqual(action, "fail")
        self.assertIn("cannot prove", message)

    def test_auth_error_is_not_reported_as_collision(self):
        action, message = preflight_decision(
            None, "token endpoint returned 403: Forbidden", TREE
        )
        self.assertEqual(action, "fail")
        self.assertIn("cannot prove", message)
        self.assertNotIn("DIFFERENT content", message)

    def test_same_content_rerun_skips(self):
        action, _ = preflight_decision(labels(), None, TREE)
        self.assertEqual(action, "skip")


class ReuseTest(unittest.TestCase):
    def test_same_content_reuses(self):
        reuse, _ = reuse_decision(labels(), None, TREE)
        self.assertTrue(reuse)

    def test_missing_content_builds(self):
        reuse, _ = reuse_decision(
            None, "index fetch failed: GET https://x returned 404: Not Found", TREE
        )
        self.assertFalse(reuse)

    def test_different_content_builds(self):
        reuse, _ = reuse_decision(labels(tree=OTHER_TREE), None, TREE)
        self.assertFalse(reuse)

    def test_error_fails_open_to_build(self):
        # Unlike preflight, an error never fails the job — it just builds.
        reuse, _ = reuse_decision(None, "network error fetching https://x", TREE)
        self.assertFalse(reuse)

    def test_same_content_same_release_line_reuses(self):
        reuse, _ = reuse_decision(labels(line="3.3.0-rc0"), None, TREE, "3.3.0-rc0")
        self.assertTrue(reuse)

    def test_same_content_new_release_line_rebuilds(self):
        """After v3.3.0 lands on an unchanged tree, the rc image must not be re-tagged."""
        reuse, message = reuse_decision(labels(line="3.3.0-rc0"), None, TREE, "3.3.0")
        self.assertFalse(reuse)
        self.assertIn("3.3.0-rc0", message)

    def test_unlabelled_release_line_rebuilds_when_one_is_expected(self):
        reuse, _ = reuse_decision(labels(), None, TREE, "3.3.0")
        self.assertFalse(reuse)

    def test_release_line_ignored_when_not_expected(self):
        # Images that bake no version keep reusing on tree alone.
        reuse, _ = reuse_decision(labels(line="3.2.1"), None, TREE, None)
        self.assertTrue(reuse)


class BakesReleaseLineTest(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[2]

    def test_version_carrying_dockerfiles(self):
        for dockerfile in ("services/agent/docker/Dockerfile", ".openclaw/Dockerfile", ".hermes/Dockerfile"):
            with self.subTest(dockerfile=dockerfile):
                self.assertTrue(bakes_release_line(str(self.ROOT / dockerfile)))

    def test_other_dockerfiles_and_missing_paths(self):
        self.assertFalse(bakes_release_line(None))
        self.assertFalse(bakes_release_line(str(self.ROOT / "does/not/exist/Dockerfile")))
        with tempfile.NamedTemporaryFile("w", suffix="Dockerfile", delete=False) as handle:
            handle.write("FROM scratch\nARG SOMETHING_ELSE=1\n")
        self.assertFalse(bakes_release_line(handle.name))
        os.unlink(handle.name)

    def test_different_content_fails(self):
        action, message = preflight_decision(labels(tree=OTHER_TREE), None, TREE)
        self.assertEqual(action, "fail")
        self.assertIn("immutable", message)

    def test_existing_unlabelled_tag_fails(self):
        action, _ = preflight_decision(
            None,
            "image config has no com.nvidia.vss.source_tree_sha label",
            TREE,
            can_fallback=True,
        )
        self.assertEqual(action, "fail")


class BearerTokenTest(unittest.TestCase):
    def test_ghcr_credentials_authenticate_token_request(self):
        headers = Message()
        headers["WWW-Authenticate"] = (
            'Bearer realm="https://ghcr.io/token",service="ghcr.io"'
        )
        challenge = HTTPError(
            "https://ghcr.io/v2/",
            401,
            "Unauthorized",
            headers,
            None,
        )
        token_response = MagicMock()
        token_response.__enter__.return_value = token_response
        token_response.read.return_value = b'{"token":"registry-token"}'

        with patch(
            "urllib.request.urlopen",
            side_effect=[challenge, token_response],
        ) as urlopen:
            token, error = _fetch_bearer_token(
                "ghcr.io",
                "nvidia-ai-blueprints/vss-agent",
                None,
                registry_username="workflow-actor",
                registry_password="github-token",
            )

        self.assertEqual(token, "registry-token")
        self.assertIsNone(error)
        token_request = urlopen.call_args_list[1].args[0]
        expected = base64.b64encode(b"workflow-actor:github-token").decode()
        self.assertEqual(
            token_request.get_header("Authorization"),
            f"Basic {expected}",
        )


class VerifyTest(unittest.TestCase):
    KWARGS = dict(
        expected_tree_sha=TREE,
        expected_source_path="services/agent",
        expected_image_name="vss-agent",
    )

    def test_matching_labels_pass(self):
        ok, message = verify_decision(labels(), None, **self.KWARGS)
        self.assertTrue(ok)
        self.assertIn("gate will accept", message)

    def test_commit_sha_instead_of_tree_sha_fails(self):
        # The historical P0: stamping github.sha (a commit SHA) instead of the
        # tree hash. Both are 40 hex chars, so only value comparison catches it.
        ok, message = verify_decision(labels(tree=OTHER_TREE), None, **self.KWARGS)
        self.assertFalse(ok)
        self.assertIn("TREE hash", message)

    def test_wrong_source_path_fails(self):
        ok, message = verify_decision(labels(path="services/ui"), None, **self.KWARGS)
        self.assertFalse(ok)
        self.assertIn("source_path", message)

    def test_wrong_image_name_fails(self):
        ok, message = verify_decision(labels(name="vss-agent-ui"), None, **self.KWARGS)
        self.assertFalse(ok)
        self.assertIn("image_name", message)

    def test_unreadable_labels_fail(self):
        ok, message = verify_decision(None, "boom", **self.KWARGS)
        self.assertFalse(ok)
        self.assertIn("boom", message)


if __name__ == "__main__":
    unittest.main(verbosity=2)
