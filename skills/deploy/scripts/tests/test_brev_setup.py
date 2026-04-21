#!/usr/bin/env python3
"""Unit tests for skills/deploy/scripts/brev_setup.sh.

Runs the script in a subshell with a custom BREV_ENV_FILE, then captures
the resulting environment. No live Brev dependency.

Run manually:
    python3 -m pytest skills/deploy/scripts/tests/test_brev_setup.py -v
Or directly (stdlib unittest fallback):
    python3 skills/deploy/scripts/tests/test_brev_setup.py
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "brev_setup.sh"


def _run(env_file_content: str | None, extra_env: dict[str, str] | None = None,
         args: str = "--quiet") -> dict[str, str]:
    """Source brev_setup.sh in a subshell and return the resulting env."""
    assert SCRIPT.exists(), f"missing: {SCRIPT}"
    with tempfile.TemporaryDirectory() as tmp:
        env_file = Path(tmp) / "environment"
        if env_file_content is not None:
            env_file.write_text(env_file_content)
        elif env_file_content is None:
            # Leave the file absent
            pass

        env = os.environ.copy()
        # Strip any inherited Brev vars so we only see what the script exports.
        for k in ("BREV_ENV_ID", "PROXY_PORT", "BREV_LINK_PREFIX"):
            env.pop(k, None)
        env["BREV_ENV_FILE"] = str(env_file) if env_file_content is not None else str(env_file)
        if extra_env:
            env.update(extra_env)

        cmd = f'source {SCRIPT} {args} >/dev/null 2>&1; env'
        out = subprocess.run(
            ["bash", "-c", cmd],
            env=env, capture_output=True, text=True, check=True,
        )
        result: dict[str, str] = {}
        for line in out.stdout.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                result[k] = v
        return result


class BrevSetup(unittest.TestCase):

    def test_no_env_file(self):
        """Missing /etc/environment → no exports, no crash."""
        res = _run(env_file_content=None)
        self.assertNotIn("BREV_ENV_ID", res)
        self.assertNotIn("BREV_LINK_PREFIX", res)

    def test_env_file_without_brev_env_id(self):
        """File exists but no BREV_ENV_ID key → no exports."""
        res = _run(env_file_content='PATH="/usr/bin"\nUSER="ubuntu"\n')
        self.assertNotIn("BREV_ENV_ID", res)
        self.assertNotIn("BREV_LINK_PREFIX", res)

    def test_happy_path_defaults(self):
        """BREV_ENV_ID present → defaults: PROXY_PORT=7777, prefix=77770."""
        res = _run(env_file_content='BREV_ENV_ID=abc123\n')
        self.assertEqual(res.get("BREV_ENV_ID"), "abc123")
        self.assertEqual(res.get("PROXY_PORT"), "7777")
        self.assertEqual(res.get("BREV_LINK_PREFIX"), "77770")

    def test_quoted_env_id(self):
        """BREV_ENV_ID value may be quoted in /etc/environment."""
        res = _run(env_file_content='BREV_ENV_ID="def456"\n')
        self.assertEqual(res.get("BREV_ENV_ID"), "def456")

    def test_proxy_port_override(self):
        """If PROXY_PORT is pre-set, reuse it and compute prefix from it."""
        res = _run(
            env_file_content='BREV_ENV_ID=abc\n',
            extra_env={"PROXY_PORT": "9999"},
        )
        self.assertEqual(res.get("PROXY_PORT"), "9999")
        self.assertEqual(res.get("BREV_LINK_PREFIX"), "99990")

    def test_brev_link_prefix_override(self):
        """Explicit BREV_LINK_PREFIX wins over the computed default
        (covers manual secure-link setups without the launchable `0` suffix)."""
        res = _run(
            env_file_content='BREV_ENV_ID=abc\n',
            extra_env={"BREV_LINK_PREFIX": "7777"},  # no `0` suffix
        )
        self.assertEqual(res.get("PROXY_PORT"), "7777")
        self.assertEqual(res.get("BREV_LINK_PREFIX"), "7777")

    def test_first_match_wins(self):
        """If /etc/environment has multiple BREV_ENV_ID lines, take the first."""
        res = _run(env_file_content='BREV_ENV_ID=first\nBREV_ENV_ID=second\n')
        self.assertEqual(res.get("BREV_ENV_ID"), "first")

    def test_stdout_printed_without_quiet(self):
        """Without --quiet, the script emits a human summary to stdout."""
        assert SCRIPT.exists()
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "environment"
            env_file.write_text('BREV_ENV_ID=xyz789\n')
            env = os.environ.copy()
            for k in ("BREV_ENV_ID", "PROXY_PORT", "BREV_LINK_PREFIX"):
                env.pop(k, None)
            env["BREV_ENV_FILE"] = str(env_file)
            out = subprocess.run(
                ["bash", "-c", f"source {SCRIPT}"],
                env=env, capture_output=True, text=True, check=True,
            )
            self.assertIn("Brev detected", out.stdout)
            self.assertIn("xyz789", out.stdout)
            self.assertIn("77770-xyz789.brevlab.com", out.stdout)

    def test_quiet_suppresses_stdout(self):
        """--quiet means no stdout output."""
        assert SCRIPT.exists()
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "environment"
            env_file.write_text('BREV_ENV_ID=xyz789\n')
            env = os.environ.copy()
            for k in ("BREV_ENV_ID", "PROXY_PORT", "BREV_LINK_PREFIX"):
                env.pop(k, None)
            env["BREV_ENV_FILE"] = str(env_file)
            out = subprocess.run(
                ["bash", "-c", f"source {SCRIPT} --quiet"],
                env=env, capture_output=True, text=True, check=True,
            )
            self.assertEqual(out.stdout, "")


class BrevUrlConstruction(unittest.TestCase):
    """Profile-level secure-link URL construction — computed client-side by
    the agent (or by the user) using the exported env vars."""

    @staticmethod
    def _url(port: int | str, env_id: str, launchable: bool = True) -> str:
        prefix = f"{port}0" if launchable else str(port)
        return f"https://{prefix}-{env_id}.brevlab.com"

    def test_base_profile_url(self):
        self.assertEqual(
            self._url(7777, "abc"),
            "https://77770-abc.brevlab.com",
        )

    def test_kibana_url(self):
        self.assertEqual(
            self._url(5601, "abc"),
            "https://56010-abc.brevlab.com",
        )

    def test_nvstreamer_url(self):
        self.assertEqual(
            self._url(31000, "abc"),
            "https://310000-abc.brevlab.com",
        )

    def test_phoenix_url(self):
        self.assertEqual(
            self._url(6006, "abc"),
            "https://60060-abc.brevlab.com",
        )

    def test_manual_link_no_zero_suffix(self):
        """If the user manually created the secure link, no `0` suffix."""
        self.assertEqual(
            self._url(7777, "abc", launchable=False),
            "https://7777-abc.brevlab.com",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
