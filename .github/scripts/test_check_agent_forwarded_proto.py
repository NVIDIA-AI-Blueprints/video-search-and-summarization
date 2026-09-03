#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("check_agent_forwarded_proto.py")
SPEC = importlib.util.spec_from_file_location("check_agent_forwarded_proto", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
LINT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LINT)

DEFAULT = "127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"


def _compose(directory: str, environment: str, *, image: bool = True) -> Path:
    path = Path(directory) / "compose.yml"
    image_line = "    image: ghcr.io/example/vss-agent:latest\n" if image else ""
    path.write_text(
        "services:\n"
        "  vss-agent:\n"
        f"{image_line}"
        "    container_name: vss-agent\n"
        "    environment:\n"
        f"{environment}"
        "    restart: unless-stopped\n"
        "\n"
        "  other:\n"
        "    image: busybox\n"
    )
    return path


def _values(directory: str, entry: str) -> Path:
    path = Path(directory) / "values.yaml"
    path.write_text(
        "env:\n"
        "- name: VSS_AGENT_HOST\n"
        "  value: '0.0.0.0'\n"
        "- name: VSS_AGENT_PORT\n"
        "  value: '8000'\n"
        f"{entry}"
    )
    return path


class TreeTest(unittest.TestCase):
    def test_tree_lets_the_agent_learn_the_scheme(self) -> None:
        self.assertEqual([], LINT.scan_paths(LINT.default_paths()))

    def test_the_agent_definition_is_actually_in_scope(self) -> None:
        # A lint that silently matches nothing passes forever. Assert both the
        # Compose definition and the Helm chart are discovered, since those are
        # the two places the variable has to exist.
        covered = {str(path) for path in LINT.default_paths()}
        self.assertTrue(
            any("deploy/docker/services/agent/compose.yml" in p for p in covered),
            f"the agent's Compose definition is not in scope: {sorted(covered)}",
        )
        self.assertTrue(
            any("charts/agent/values.yaml" in p for p in covered),
            f"the agent Helm chart is not in scope: {sorted(covered)}",
        )

    def test_the_remote_agent_overlay_is_not_required_to_repeat_it(self) -> None:
        overlay = LINT.ROOT / "deploy/docker/compose.remote-agent.yml"
        if not overlay.is_file():
            self.skipTest("no remote-agent overlay in this tree")
        self.assertFalse(LINT.defines_the_agent(overlay.read_text()))
        self.assertNotIn(overlay, LINT.default_paths())


class TrustParsingTest(unittest.TestCase):
    def test_the_shipped_default_trusts_a_bridge_peer(self) -> None:
        self.assertTrue(LINT.trusts_a_gateway_peer(DEFAULT))

    def test_a_wildcard_trusts_everything(self) -> None:
        self.assertTrue(LINT.trusts_a_gateway_peer("*"))

    def test_uvicorns_own_default_is_not_enough(self) -> None:
        # This is the pre-fix state: present, plausible, and unable to trust
        # the gateway.
        self.assertFalse(LINT.trusts_a_gateway_peer("127.0.0.1"))

    def test_a_container_name_never_matches(self) -> None:
        # uvicorn keeps a non-address as a literal and compares it against the
        # peer's numeric address, so this reads tight and does nothing.
        self.assertFalse(LINT.trusts_a_gateway_peer("vss-haproxy-ingress"))

    def test_a_public_range_does_not_cover_a_bridge_peer(self) -> None:
        self.assertFalse(LINT.trusts_a_gateway_peer("203.0.113.0/24"))

    def test_an_empty_value_is_not_trust(self) -> None:
        self.assertFalse(LINT.trusts_a_gateway_peer(""))

    def test_one_usable_entry_among_literals_is_enough(self) -> None:
        self.assertTrue(LINT.trusts_a_gateway_peer("vss.local, 172.16.0.0/12"))


class ComposeTest(unittest.TestCase):
    def test_the_shipped_shape_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = _compose(
                directory,
                f"      FORWARDED_ALLOW_IPS: ${{VSS_AGENT_FORWARDED_ALLOW_IPS:-{DEFAULT}}}\n",
            )
            self.assertEqual([], LINT.scan_paths([path]))

    def test_a_missing_variable_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = _compose(directory, "      VSS_AGENT_PORT: ${VSS_AGENT_PORT}\n")
            failures = LINT.scan_paths([path])
        self.assertEqual(1, len(failures))
        self.assertIn("does not set FORWARDED_ALLOW_IPS", failures[0])

    def test_a_container_name_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = _compose(
                directory, "      FORWARDED_ALLOW_IPS: vss-haproxy-ingress\n"
            )
            failures = LINT.scan_paths([path])
        self.assertEqual(1, len(failures))
        self.assertIn("trusts no address", failures[0])

    def test_a_loopback_only_default_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = _compose(
                directory,
                "      FORWARDED_ALLOW_IPS: ${VSS_AGENT_FORWARDED_ALLOW_IPS:-127.0.0.1}\n",
            )
            failures = LINT.scan_paths([path])
        self.assertEqual(1, len(failures))
        self.assertIn("127.0.0.1", failures[0])

    def test_a_quoted_value_is_read_through_the_quotes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = _compose(
                directory, f'      FORWARDED_ALLOW_IPS: "{DEFAULT}"\n'
            )
            self.assertEqual([], LINT.scan_paths([path]))

    def test_an_overlay_without_an_image_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = _compose(directory, "      LLM_MODE: remote\n", image=False)
            self.assertEqual([], LINT.scan_paths([path]))


class HelmTest(unittest.TestCase):
    def test_the_shipped_shape_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = _values(
                directory,
                "- name: FORWARDED_ALLOW_IPS\n"
                f"  value: '{{{{ .Values.forwardedAllowIps | default \"{DEFAULT}\" }}}}'\n",
            )
            self.assertEqual([], LINT.scan_paths([path]))

    def test_a_missing_entry_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = _values(directory, "- name: LLM_MODE\n  value: remote\n")
            failures = LINT.scan_paths([path])
        self.assertEqual(1, len(failures))
        self.assertIn("does not set FORWARDED_ALLOW_IPS", failures[0])

    def test_a_loopback_only_template_default_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = _values(
                directory,
                "- name: FORWARDED_ALLOW_IPS\n"
                "  value: '{{ .Values.forwardedAllowIps | default \"127.0.0.1\" }}'\n",
            )
            failures = LINT.scan_paths([path])
        self.assertEqual(1, len(failures))
        self.assertIn("trusts no address", failures[0])

    def test_a_plain_value_is_judged_directly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = _values(
                directory,
                "- name: FORWARDED_ALLOW_IPS\n  value: 'vss-agent'\n",
            )
            failures = LINT.scan_paths([path])
        self.assertEqual(1, len(failures))


if __name__ == "__main__":
    unittest.main()
