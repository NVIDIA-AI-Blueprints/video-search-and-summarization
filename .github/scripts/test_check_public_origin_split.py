#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("check_public_origin_split.py")
SPEC = importlib.util.spec_from_file_location("check_public_origin_split", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
LINT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LINT)


def _env(directory: str, body: str) -> Path:
    path = Path(directory) / "overrides.env"
    path.write_text(body)
    return path


class PublicOriginSplitTest(unittest.TestCase):
    def test_tree_keeps_the_two_origins_distinct(self) -> None:
        self.assertEqual([], LINT.scan_paths(LINT.default_paths()))

    def test_profiles_are_actually_covered(self) -> None:
        # A lint that silently matches nothing passes forever. Assert the
        # developer profiles are in scope, since they are what it guards.
        covered = {str(path) for path in LINT.default_paths()}
        self.assertTrue(
            any("dev-profile-alerts" in path for path in covered),
            f"developer profiles are not in scope: {sorted(covered)}",
        )

    def test_browser_url_from_gateway_origin_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = _env(directory, "VST_EXTERNAL_URL=${VSS_GATEWAY_ORIGIN}\n")
            failures = LINT.scan_paths([path])
        self.assertEqual(1, len(failures))
        self.assertIn("VST_EXTERNAL_URL is derived from VSS_GATEWAY_*", failures[0])

    def test_reports_base_url_from_gateway_host_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = _env(
                directory,
                "VSS_AGENT_REPORTS_BASE_URL=http://${VSS_GATEWAY_HOST}:7777/static/\n",
            )
            failures = LINT.scan_paths([path])
        self.assertEqual(1, len(failures))
        self.assertIn("VSS_AGENT_REPORTS_BASE_URL", failures[0])

    def test_gateway_origin_from_public_values_fails(self) -> None:
        # The Brev shape: VSS_PUBLIC_* is https on 443, so this hands every
        # container https://vss.local:443 -- a listener that does not exist.
        with tempfile.TemporaryDirectory() as directory:
            path = _env(
                directory,
                "VSS_GATEWAY_ORIGIN=${VSS_PUBLIC_HTTP_PROTOCOL}://"
                "${VSS_GATEWAY_HOST}:${VSS_PUBLIC_PORT}\n",
            )
            failures = LINT.scan_paths([path])
        self.assertEqual(1, len(failures))
        self.assertIn("the gateway origin is HAProxy's own listener", failures[0])

    def test_empty_gateway_origin_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = _env(directory, "VSS_GATEWAY_ORIGIN=\n")
            failures = LINT.scan_paths([path])
        self.assertEqual(1, len(failures))
        self.assertIn("is empty", failures[0])

    def test_correct_wiring_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = _env(
                directory,
                "VSS_PUBLIC_HTTP_PROTOCOL=http\n"
                "VSS_PUBLIC_HOST=${EXTERNAL_IP}\n"
                "VSS_PUBLIC_PORT=${HAPROXY_HOST_PORT}\n"
                "VSS_GATEWAY_HOST=vss.local\n"
                "VSS_GATEWAY_PORT=${HAPROXY_PORT:-7777}\n"
                "VSS_GATEWAY_ORIGIN=http://${VSS_GATEWAY_HOST}:${VSS_GATEWAY_PORT}\n"
                "VST_EXTERNAL_URL=${VSS_PUBLIC_HTTP_PROTOCOL}://"
                "${VSS_PUBLIC_HOST}:${VSS_PUBLIC_PORT}\n",
            )
            failures = LINT.scan_paths([path])
        self.assertEqual([], failures)

    def test_comments_are_not_flagged(self) -> None:
        # The profiles document the relationship between the two origins in
        # prose right next to the assignments; a lint that reads comments would
        # fail on the explanation of the rule it enforces.
        with tempfile.TemporaryDirectory() as directory:
            path = _env(
                directory,
                "# Never set VST_EXTERNAL_URL=${VSS_GATEWAY_ORIGIN} -- see brev.md\n"
                "VST_EXTERNAL_URL=${VSS_PUBLIC_HTTP_PROTOCOL}://${VSS_PUBLIC_HOST}\n",
            )
            failures = LINT.scan_paths([path])
        self.assertEqual([], failures)


if __name__ == "__main__":
    unittest.main()
