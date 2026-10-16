#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regression tests for the pre-trial docker reset script.

The reset wipes containers, volumes and user networks while keeping images.
It also keeps the two RT-VLM model-weight cache volumes: re-downloading those
measured ~25 min on a WiFi-linked GB10 board, which does not fit the per-trial
agent budget (PR #1743 -- the DGX-SPARK base leg timed out that way).

The script is shell, so these tests run it under bash against a stub `docker`
on PATH rather than asserting on substrings. That way the grep/pipefail
interactions -- the parts that actually broke -- are exercised for real.

Run:
    python3 -m pytest .github/skill-eval/envs/tests/test_docker_reset.py -v
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
import types
import unittest
from pathlib import Path

# --- Stub the harbor.environments.base import so brev_env is importable. ---
_base = types.ModuleType("harbor.environments.base")


class _BaseEnvironment:
    def __init__(self, *a, **kw):
        pass


class _ExecResult:
    def __init__(self, stdout=None, stderr=None, return_code=0):
        self.stdout = stdout
        self.stderr = stderr
        self.return_code = return_code


_base.BaseEnvironment = _BaseEnvironment
_base.ExecResult = _ExecResult
sys.modules.setdefault("harbor", types.ModuleType("harbor"))
sys.modules.setdefault("harbor.environments", types.ModuleType("harbor.environments"))
sys.modules["harbor.environments.base"] = _base

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import envs.brev_env as brev_env  # noqa: E402


# Volumes a real box carries after a base-profile deploy: the two weight
# caches, three service-state volumes, and one anonymous volume.
_VOLUMES = [
    "vss_rtvi-hf-cache",
    "vss_rtvi-ngc-model-cache",
    "vss_vios_pg_data",
    "vss_phoenix-data",
    "vss_agent-eval",
    "f2236fece0f88db30a3b330342e31e12ddbdb77e01a95fec499eb64022ccb579",
]


class DockerResetScript(unittest.TestCase):
    def _run(self, volumes=_VOLUMES, containers=(), networks=()):
        """Run the reset script against a stub docker; return (proc, removed)."""
        tmp = Path(tempfile.mkdtemp())
        state = tmp / "volumes.txt"
        state.write_text("".join(f"{v}\n" for v in volumes))
        (tmp / "containers.txt").write_text("".join(f"{c}\n" for c in containers))
        (tmp / "networks.txt").write_text("".join(f"{n}\n" for n in networks))
        removed = tmp / "removed.txt"
        removed.touch()

        # `volume rm` deletes from the state file, so the post-reset guard
        # counts what a real daemon would report.
        stub = tmp / "docker"
        stub.write_text(textwrap.dedent(f"""\
            #!/usr/bin/env bash
            state="{state}"
            case "$1 $2" in
              "info ") exit 0 ;;
              "ps -aq") cat "{tmp}/containers.txt"; exit 0 ;;
              "images -q") echo layer1; echo layer2; exit 0 ;;
              "network prune") : > "{tmp}/networks.txt"; exit 0 ;;
              "network ls") cat "{tmp}/networks.txt"; exit 0 ;;
              "volume ls") cat "$state"; exit 0 ;;
              "volume rm")
                shift 3
                for v in "$@"; do
                  echo "$v" >> "{removed}"
                  grep -vxF "$v" "$state" > "$state.new" || true
                  mv "$state.new" "$state"
                done
                exit 0 ;;
              "rm -f") exit 0 ;;
            esac
            exit 0
            """))
        stub.chmod(0o755)

        env = dict(os.environ, PATH=f"{tmp}{os.pathsep}{os.environ['PATH']}")
        proc = subprocess.run(
            ["bash", "-c", brev_env._docker_reset_script()],
            capture_output=True, text=True, env=env, timeout=60,
        )
        return proc, removed.read_text().split()

    def test_keeps_weight_caches_and_removes_everything_else(self):
        proc, removed = self._run()
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertNotIn("vss_rtvi-hf-cache", removed)
        self.assertNotIn("vss_rtvi-ngc-model-cache", removed)
        self.assertEqual(
            sorted(removed),
            sorted([
                "f2236fece0f88db30a3b330342e31e12ddbdb77e01a95fec499eb64022ccb579",
                "vss_agent-eval",
                "vss_phoenix-data",
                "vss_vios_pg_data",
            ]),
        )

    def test_surviving_caches_do_not_fail_the_guard(self):
        # The caches are still listed after the reset; the guard must not read
        # them as an incomplete reset.
        proc, _ = self._run()
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertIn("model-weight caches kept (2)", proc.stdout)
        self.assertIn("images preserved (2 layers)", proc.stdout)

    def test_only_caches_present_is_a_no_op_not_an_error(self):
        # `grep -Ev` filters every volume out and exits 1; under `pipefail`
        # that must not abort the script or produce a bogus `volume rm`.
        proc, removed = self._run(volumes=["vss_rtvi-hf-cache", "vss_rtvi-ngc-model-cache"])
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertEqual(removed, [])

    def test_no_volumes_at_all_succeeds(self):
        proc, removed = self._run(volumes=[])
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertEqual(removed, [])
        self.assertIn("model-weight caches kept (0)", proc.stdout)

    def test_leftover_non_cache_volume_still_fails_loud(self):
        # A volume that cannot be removed must fail the reset, otherwise
        # predecessor state leaks into the trial.
        tmp = Path(tempfile.mkdtemp())
        stub = tmp / "docker"
        stub.write_text(textwrap.dedent("""\
            #!/usr/bin/env bash
            case "$1 $2" in
              "info ") exit 0 ;;
              "ps -aq") exit 0 ;;
              "images -q") exit 0 ;;
              "network ls") exit 0 ;;
              "volume ls") echo vss_vios_pg_data ;;
              "volume rm") exit 1 ;;
            esac
            exit 0
            """))
        stub.chmod(0o755)
        env = dict(os.environ, PATH=f"{tmp}{os.pathsep}{os.environ['PATH']}")
        proc = subprocess.run(
            ["bash", "-c", brev_env._docker_reset_script()],
            capture_output=True, text=True, env=env, timeout=60,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("reset incomplete", proc.stderr)
        self.assertIn("1 volumes", proc.stderr)

    def test_unreachable_daemon_fails_loud(self):
        tmp = Path(tempfile.mkdtemp())
        stub = tmp / "docker"
        stub.write_text("#!/usr/bin/env bash\nexit 1\n")
        stub.chmod(0o755)
        env = dict(os.environ, PATH=f"{tmp}{os.pathsep}{os.environ['PATH']}")
        proc = subprocess.run(
            ["bash", "-c", brev_env._docker_reset_script()],
            capture_output=True, text=True, env=env, timeout=60,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("docker daemon unreachable", proc.stderr)

    def test_pattern_scopes_to_weight_caches_only(self):
        import re
        keep = re.compile(brev_env._PRESERVED_VOLUME_RE)
        for name in ("vss_rtvi-hf-cache", "vss_rtvi-ngc-model-cache",
                     "rtvi-hf-cache", "rtvi-ngc-model-cache"):
            self.assertIsNotNone(keep.search(name), name)
        for name in ("vss_vios_pg_data", "vss_phoenix-data", "vss_agent-eval",
                     "vss_rtvi-hf-cache-old", "my-rtvi-hf-cache-backup",
                     "f2236fece0f88db30a3b330342e31e12ddbdb77e01a95fec4"):
            self.assertIsNone(keep.search(name), name)


if __name__ == "__main__":
    unittest.main()
