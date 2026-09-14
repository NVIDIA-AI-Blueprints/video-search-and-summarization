#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("check_agent_http_endpoints.py")
SPEC = importlib.util.spec_from_file_location("check_agent_http_endpoints", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
LINT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LINT)


class AgentHttpEndpointsTest(unittest.TestCase):
    def test_tree_has_no_docker_only_agent_http_endpoints(self) -> None:
        self.assertEqual([], LINT.scan_paths(LINT.default_paths()))

    def test_docker_only_elasticsearch_url_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "fake-agent.yml"
            config.write_text("elasticsearch_url: http://elasticsearch:9200\n")

            failures = LINT.scan_paths([config])

        self.assertEqual(1, len(failures))
        self.assertIn("Docker-only HTTP host 'elasticsearch'", failures[0])

    def test_bare_profile_env_files_are_covered(self) -> None:
        # Path(".env").suffix is "", not ".env", so a suffix-only filter skips
        # every profile .env -- the file class this lint exists to guard.
        covered = {path.name for path in LINT.default_paths()}
        self.assertIn(".env", covered)

    def test_rtvi_vlm_is_a_forbidden_host(self) -> None:
        # The gateway mounts RT-VLM at /rtvi-vlm, so the Compose name is as
        # unresolvable to an off-host agent as alert-bridge is. It was missing
        # from the host set, which hid VLM_BASE_URL=http://rtvi-vlm:8000 in
        # five profile override files the lint was already reading.
        with tempfile.TemporaryDirectory() as directory:
            env = Path(directory) / "overrides.env"
            env.write_text("VLM_BASE_URL=http://rtvi-vlm:8000\n")

            failures = LINT.scan_paths([env])

        self.assertEqual(1, len(failures))
        self.assertIn("Docker-only HTTP host 'rtvi-vlm'", failures[0])

    def test_the_gateways_own_bridge_identities_are_forbidden(self) -> None:
        # The host set was nine backends *behind* the gateway, so an endpoint
        # pointed at the gateway itself by a bridge-only name passed the lint.
        # That is the worst regression for this lint to miss: both spellings
        # work from a colocated agent and resolve nowhere from a remote one, so
        # they survive every single-host test and break only FR-35's case.
        for url, host in (
            ("http://vss-haproxy-ingress:7777/elasticsearch", "vss-haproxy-ingress"),
            ("http://vss.local:7777/elasticsearch", "vss.local"),
        ):
            with self.subTest(host=host), tempfile.TemporaryDirectory() as directory:
                config = Path(directory) / "fake-agent.yml"
                config.write_text(f"elasticsearch_url: {url}\n")

                failures = LINT.scan_paths([config])

                self.assertEqual(1, len(failures))
                self.assertIn(f"Docker-only HTTP host {host!r}", failures[0])

    def test_the_gateway_origin_expansion_is_not_flagged(self) -> None:
        # The forbidden literal is `http://vss.local`; the *default* inside
        # VSS_GATEWAY_ORIGIN spells the same alias as an expansion, and that is
        # the correct form the tree uses in ~30 places. Flagging it would make
        # the lint unusable and the previous test's host unaddable.
        with tempfile.TemporaryDirectory() as directory:
            env = Path(directory) / "overrides.env"
            env.write_text(
                "ELASTIC_SEARCH_ENDPOINT=${VSS_GATEWAY_ORIGIN:-http://"
                "${VSS_GATEWAY_HOST:-vss.local}:${VSS_GATEWAY_PORT:-7777}}"
                "/elasticsearch\n"
            )

            self.assertEqual([], LINT.scan_paths([env]))

    def test_host_patterns_are_regex_escaped(self) -> None:
        # `vss.local` carries a dot. Unescaped it is a wildcard, so the lint
        # would report `vss-local` -- a host nobody wrote -- as a violation.
        with tempfile.TemporaryDirectory() as directory:
            env = Path(directory) / "overrides.env"
            env.write_text("SOME_URL=http://vss-local:7777/elasticsearch\n")

            self.assertEqual([], LINT.scan_paths([env]))

    def test_shared_service_env_files_are_scanned(self) -> None:
        # services/compose.yml merges these into the agent's environment, so a
        # Docker-only default here is an agent default. Scanning only
        # services/agent made alert.env and rtvi.env invisible.
        covered = {path.relative_to(LINT.ROOT).as_posix() for path in LINT.default_paths()}
        self.assertIn("deploy/docker/services/alert/alert.env", covered)
        self.assertIn("deploy/docker/services/rtvi/rtvi.env", covered)
        self.assertIn("deploy/docker/services/vios/vst.env", covered)

    def test_self_address_exemption_is_scoped_to_one_variable(self) -> None:
        # vst.env is exempt for VST_INTERNAL_URL only. Any other setting in it
        # that names a Docker-only host still fails, so the exemption cannot
        # grow into a skipped file.
        vst_env = LINT.ROOT / "deploy/docker/services/vios/vst.env"
        self.assertIn(
            (vst_env.relative_to(LINT.ROOT).as_posix(), "VST_INTERNAL_URL"),
            LINT.IN_NETWORK_SELF_ADDRESSES,
        )
        self.assertEqual([], LINT.scan_paths([vst_env]))

        line = "SOME_OTHER_URL=http://vst-ingress:30888\n"
        original = vst_env.read_text()
        try:
            vst_env.write_text(original + line)
            failures = LINT.scan_paths([vst_env])
        finally:
            vst_env.write_text(original)

        self.assertEqual(1, len(failures))
        self.assertIn("Docker-only HTTP host 'vst-ingress'", failures[0])

    def test_commented_hostnames_are_not_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env = Path(directory) / "overrides.env"
            env.write_text("# was http://alert-bridge:9080 before the gateway\n")

            self.assertEqual([], LINT.scan_paths([env]))

    def test_bare_env_filename_is_recognised(self) -> None:
        self.assertTrue(LINT.is_env_file(Path("deploy/dev-profile-lvs/.env")))
        self.assertTrue(LINT.is_env_file(Path("deploy/overrides.env")))
        self.assertFalse(LINT.is_env_file(Path("deploy/config.yml")))


if __name__ == "__main__":
    unittest.main()
