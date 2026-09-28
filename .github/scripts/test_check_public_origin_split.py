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


def _compose(directory: str, body: str) -> Path:
    path = Path(directory) / "compose.yml"
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

    # --- NEXT_PUBLIC_* in service compose files ---

    def test_service_compose_files_are_actually_covered(self) -> None:
        # This lint reported the class as covered while scanning only profile env
        # files, so a private address reached the browser from ui/compose.yml.
        # Assert the compose files are in scope, since that is the gap.
        covered = {str(path) for path in LINT.default_paths()}
        self.assertTrue(
            any(path.endswith("services/ui/compose.yml") for path in covered),
            f"service compose files are not in scope: {sorted(covered)}",
        )

    def test_browser_url_from_external_ip_fails(self) -> None:
        # The live defect: the UI advertised http://172.31.9.164:3002 to a
        # browser reaching the deployment over a Brev secure link.
        with tempfile.TemporaryDirectory() as directory:
            path = _compose(
                directory,
                "      NEXT_PUBLIC_MAP_URL: ${NEXT_PUBLIC_MAP_URL:-http://${EXTERNAL_IP}:3002}\n",
            )
            failures = LINT.scan_paths([path])
        self.assertEqual(2, len(failures))
        self.assertTrue(any("host's own address" in f for f in failures))
        self.assertTrue(any("hard-codes http://" in f for f in failures))

    def test_browser_url_from_host_ip_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = _compose(
                directory,
                "      NEXT_PUBLIC_HTTP_CHAT_COMPLETION_URL: "
                "${VSS_PUBLIC_HTTP_PROTOCOL}://${HOST_IP}:8000/chat/stream\n",
            )
            failures = LINT.scan_paths([path])
        self.assertEqual(1, len(failures))
        self.assertIn("host's own address", failures[0])

    def test_the_rule_is_the_prefix_not_a_name_list(self) -> None:
        # A variable nobody has written yet must be covered the moment it is
        # added. Matching a list of names is how MAP_URL stayed outside the lint.
        with tempfile.TemporaryDirectory() as directory:
            path = _compose(
                directory,
                "      NEXT_PUBLIC_SOME_FUTURE_TAB_URL: http://${EXTERNAL_IP}:9999\n",
            )
            failures = LINT.scan_paths([path])
        self.assertTrue(any("NEXT_PUBLIC_SOME_FUTURE_TAB_URL" in f for f in failures))

    def test_public_origin_browser_url_passes(self) -> None:
        # The shape every other NEXT_PUBLIC_* URL in ui/compose.yml already uses.
        with tempfile.TemporaryDirectory() as directory:
            path = _compose(
                directory,
                "      NEXT_PUBLIC_VST_API_URL: "
                "${VSS_PUBLIC_HTTP_PROTOCOL}://${VSS_PUBLIC_HOST}:${VSS_PUBLIC_PORT}/vst/api\n",
            )
            failures = LINT.scan_paths([path])
        self.assertEqual([], failures)

    def test_non_gateway_port_on_the_public_host_passes(self) -> None:
        # The map app is a second origin the gateway does not route, so it keeps
        # its own port while sharing the public host and scheme. That is not the
        # defect: no private address and no fixed scheme.
        with tempfile.TemporaryDirectory() as directory:
            path = _compose(
                directory,
                "      NEXT_PUBLIC_MAP_URL: ${NEXT_PUBLIC_MAP_URL:-"
                "${VSS_PUBLIC_HTTP_PROTOCOL}://${VSS_PUBLIC_HOST}:"
                "${VIDEO_ANALYTICS_UI_HOST_PORT:-3002}}\n",
            )
            failures = LINT.scan_paths([path])
        self.assertEqual([], failures)

    def test_non_browser_vars_may_use_the_host_address(self) -> None:
        # A container reaching the host legitimately uses HOST_IP. Only the
        # NEXT_PUBLIC_ prefix means "the browser downloads this".
        with tempfile.TemporaryDirectory() as directory:
            path = _compose(
                directory,
                "      VST_INTERNAL_URL: http://${HOST_IP}:30888\n",
            )
            failures = LINT.scan_paths([path])
        self.assertEqual([], failures)

    def test_compose_comments_are_not_flagged(self) -> None:
        # The fix carries a comment saying not to reintroduce EXTERNAL_IP or a
        # literal http:// -- the lint must not fail on its own rationale.
        with tempfile.TemporaryDirectory() as directory:
            path = _compose(
                directory,
                "      # Do not reintroduce EXTERNAL_IP or a literal http:// here.\n"
                "      NEXT_PUBLIC_MAP_URL: ${VSS_PUBLIC_HTTP_PROTOCOL}://${VSS_PUBLIC_HOST}:3002\n",
            )
            failures = LINT.scan_paths([path])
        self.assertEqual([], failures)


if __name__ == "__main__":
    unittest.main()
