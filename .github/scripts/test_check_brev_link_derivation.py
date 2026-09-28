#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("check_brev_link_derivation.py")
SPEC = importlib.util.spec_from_file_location("check_brev_link_derivation", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
LINT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LINT)


def _script(directory: str, body: str) -> Path:
    path = Path(directory) / "deploy.sh"
    path.write_text(body)
    return path


def _env(directory: str, body: str) -> Path:
    path = Path(directory) / "overrides.env"
    path.write_text(body)
    return path


class BrevLinkDerivationTest(unittest.TestCase):
    def test_tree_is_clean(self) -> None:
        self.assertEqual([], LINT.scan_paths(LINT.default_paths()))

    def test_scope_actually_covers_the_deploy_script(self) -> None:
        # A lint that silently matches nothing passes forever. dev-profile.sh is
        # the file that derives the link, so it must be in scope.
        covered = {str(path) for path in LINT.default_paths()}
        self.assertTrue(
            any(path.endswith("dev-profile.sh") for path in covered),
            f"dev-profile.sh is not in scope: {sorted(covered)}",
        )

    def test_scope_covers_profile_env_files(self) -> None:
        covered = {str(path) for path in LINT.default_paths()}
        self.assertTrue(
            any("dev-profile-alerts" in path for path in covered),
            f"developer profiles are not in scope: {sorted(covered)}",
        )

    def test_hardcoded_brevlab_domain_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = _script(directory, '_link_domain="brevlab.com"\n')
            failures = LINT.scan_paths([path])
        self.assertEqual(1, len(failures))
        self.assertIn("hardcodes the Brev link domain", failures[0])

    def test_hardcoded_skybridge_domain_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = _script(directory, '_link_domain="apps.run.brev.nvidia.com"\n')
            failures = LINT.scan_paths([path])
        self.assertEqual(1, len(failures))
        self.assertIn("apps.run.brev.nvidia.com", failures[0])

    def test_hardcoding_the_newly_found_domain_also_fails(self) -> None:
        # The domain this defect was found on is not a safer default than the two
        # it replaced. Pinning it would repeat the defect with fresher data.
        with tempfile.TemporaryDirectory() as directory:
            path = _script(directory, '_link_domain="gobrev.dev"\n')
            failures = LINT.scan_paths([path])
        self.assertEqual(1, len(failures))
        self.assertIn("gobrev.dev", failures[0])

    def test_domain_in_a_profile_env_file_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = _env(directory, "VSS_PUBLIC_HOST=7777-${BREV_ENV_ID}.brevlab.com\n")
            failures = LINT.scan_paths([path])
        self.assertTrue(any("hardcodes the Brev link domain" in f for f in failures))

    def test_netbird_domain_probe_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = _script(
                directory,
                'if netbird status -d | grep -qi skybridge; then\n'
                '  _link_domain="apps.run.brev.nvidia.com"\n'
                "fi\n",
            )
            failures = LINT.scan_paths([path])
        self.assertTrue(any("probing" in f and "netbird" in f for f in failures))

    def test_deriving_public_host_without_the_context_fails(self) -> None:
        # The shape of the original defect with the literals removed: still a
        # composed hostname, still lands on the Host allowlist as a guess.
        with tempfile.TemporaryDirectory() as directory:
            path = _script(
                directory,
                'if [[ -n "${BREV_ENV_ID:-}" ]]; then\n'
                '  set_env_var VSS_PUBLIC_HOST "${_prefix}-${BREV_ENV_ID}.${_domain}"\n'
                "fi\n",
            )
            failures = LINT.scan_paths([path])
        self.assertEqual(1, len(failures))
        self.assertIn("without consulting the Brev environment context", failures[0])

    def test_reading_the_context_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = _script(
                directory,
                'if [[ -n "${BREV_ENV_ID:-}" ]]; then\n'
                '  _ctx="${BREV_ENVIRONMENT_CONTEXT_PATH:-/etc/brev/environment-context.json}"\n'
                '  read -r _port _host_port _fqdn <<< "$(jq -r ... "${_ctx}")"\n'
                '  set_env_var VSS_PUBLIC_HOST "${_fqdn}"\n'
                "fi\n",
            )
            failures = LINT.scan_paths([path])
        self.assertEqual([], failures)

    def test_public_host_outside_a_brev_block_is_not_flagged(self) -> None:
        # A non-Brev deployment sets VSS_PUBLIC_HOST from the host address and has
        # no link to look up.
        with tempfile.TemporaryDirectory() as directory:
            path = _env(directory, "VSS_PUBLIC_HOST=${EXTERNAL_IP}\n")
            failures = LINT.scan_paths([path])
        self.assertEqual([], failures)

    def test_comments_are_not_flagged(self) -> None:
        # The script explains why these domains must not be pinned, right beside
        # the code that avoids pinning them.
        with tempfile.TemporaryDirectory() as directory:
            path = _script(
                directory,
                "# Not brevlab.com and not apps.run.brev.nvidia.com: a real box\n"
                "# serves gobrev.dev, so the domain has to be read, not guessed.\n"
                '_ctx="${BREV_ENVIRONMENT_CONTEXT_PATH}"\n',
            )
            failures = LINT.scan_paths([path])
        self.assertEqual([], failures)


if __name__ == "__main__":
    unittest.main()
