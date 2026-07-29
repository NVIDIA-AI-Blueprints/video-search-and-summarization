#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for registered-node SSH fallback in brev_env.py.

These tests don't need an actual Brev instance — they monkeypatch the
module-level `_registered_nodes_cache` and stub asyncio subprocess calls.

Run manually:
    python3 -m pytest .github/skill-eval/envs/tests/test_registered_node.py -v
Or directly:
    python3 .github/skill-eval/envs/tests/test_registered_node.py
"""
from __future__ import annotations

import asyncio
import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import types
import unittest
from pathlib import Path
from unittest import mock

# Stub the harbor.environments.base import so brev_env is importable.
_base = types.ModuleType("harbor.environments.base")

class _BaseEnvironment:
    def __init__(self, *a, **kw): pass

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

ENVS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENVS_DIR))

import brev_env  # noqa: E402


class RtspSampleUrlResolution(unittest.TestCase):
    def test_uses_public_default_when_unset(self):
        with mock.patch.dict(os.environ, {"RTSP_SAMPLE_URL": ""}):
            self.assertEqual(
                brev_env._resolve_rtsp_sample_url(),
                "rtsp://global.stg.ga.launchpad.nvidia.com:11333/camera03",
            )

    def test_preserves_operator_override(self):
        custom_url = "rtsp://stream.example.test:8554/eval"
        with mock.patch.dict(os.environ, {"RTSP_SAMPLE_URL": custom_url}):
            self.assertEqual(brev_env._resolve_rtsp_sample_url(), custom_url)


class RegisteredNodeDetection(unittest.TestCase):

    def setUp(self):
        # Force cache population from a fake node list.
        brev_env._registered_nodes_cache = {
            "spark": {"name": "SPARK", "status": "Connected", "external_node_id": "extnode-x"},
            "h100-vlm": {"name": "H100-VLM", "status": "Connected", "external_node_id": "extnode-y"},
        }

    def tearDown(self):
        brev_env._registered_nodes_cache = None

    def test_is_registered_node_case_insensitive(self):
        self.assertTrue(asyncio.run(brev_env._is_registered_node("SPARK")))
        self.assertTrue(asyncio.run(brev_env._is_registered_node("spark")))
        self.assertTrue(asyncio.run(brev_env._is_registered_node("Spark")))
        self.assertTrue(asyncio.run(brev_env._is_registered_node("H100-VLM")))
        self.assertTrue(asyncio.run(brev_env._is_registered_node("h100-vlm")))

    def test_is_not_registered(self):
        self.assertFalse(asyncio.run(brev_env._is_registered_node("vss-eval-rtx")))
        self.assertFalse(asyncio.run(brev_env._is_registered_node("unknown")))
        self.assertFalse(asyncio.run(brev_env._is_registered_node("")))

    def test_ssh_alias(self):
        self.assertEqual(brev_env._ssh_alias_for("SPARK"), "spark")
        self.assertEqual(brev_env._ssh_alias_for("H100-VLM"), "h100-vlm")
        self.assertEqual(brev_env._ssh_alias_for("spark"), "spark")


class FindBrevInstanceFallback(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        brev_env._registered_nodes_cache = {
            "spark": {"name": "SPARK", "status": "Connected"},
        }

    async def asyncTearDown(self):
        brev_env._registered_nodes_cache = None

    async def test_registered_node_returns_synthetic_entry(self):
        """If `brev ls` has no match but `brev ls nodes` does, return a
        synthetic dict with _registered=True."""
        async def fake_run_brev(*args, **kw):
            # brev ls --json returns empty cloud list
            return brev_env.ExecResult(stdout="[]", stderr=None, return_code=0)

        original = brev_env._run_brev
        brev_env._run_brev = fake_run_brev
        try:
            result = await brev_env._find_brev_instance("SPARK")
            self.assertIsNotNone(result)
            self.assertTrue(result.get("_registered"))
            self.assertEqual(result["name"], "SPARK")
            self.assertEqual(result["type"], "registered")
        finally:
            brev_env._run_brev = original

    async def test_brev_instance_can_be_found_by_id(self):
        async def fake_run_brev(*args, **kw):
            return brev_env.ExecResult(
                stdout='[{"id":"instance-123","name":"vss-eval-rtx-1g-2"}]',
                stderr=None,
                return_code=0,
            )

        original = brev_env._run_brev
        brev_env._run_brev = fake_run_brev
        try:
            result = await brev_env._find_brev_instance("instance-123")
            self.assertIsNotNone(result)
            self.assertEqual(result["name"], "vss-eval-rtx-1g-2")
        finally:
            brev_env._run_brev = original

    async def test_unknown_instance_returns_none(self):
        async def fake_run_brev(*args, **kw):
            return brev_env.ExecResult(stdout="[]", stderr=None, return_code=0)

        original = brev_env._run_brev
        brev_env._run_brev = fake_run_brev
        try:
            result = await brev_env._find_brev_instance("does-not-exist")
            self.assertIsNone(result)
        finally:
            brev_env._run_brev = original


class CheckInstanceMatchesForRegistered(unittest.TestCase):

    def test_registered_instance_bypasses_gpu_name_check(self):
        """Registered nodes often have empty `gpu` field — shouldn't fail."""
        inst = {"name": "SPARK", "_registered": True, "gpu": ""}
        # Should not raise
        asyncio.run(brev_env._check_instance_matches(inst, {"gpu_type": "GB10"}))

    def test_brev_managed_still_checks_gpu(self):
        """Non-registered instances still enforce GPU-name match."""
        inst = {"name": "test", "gpu": "L40S", "instance_type": "test-l40s"}

        async def fake_catalog_count(instance_type):
            return 1

        original = brev_env._get_instance_gpu_count_from_catalog
        brev_env._get_instance_gpu_count_from_catalog = fake_catalog_count
        try:
            with self.assertRaises(RuntimeError):
                asyncio.run(brev_env._check_instance_matches(inst, {"gpu_type": "H100"}))
        finally:
            brev_env._get_instance_gpu_count_from_catalog = original

    def test_larger_gpu_partition_satisfies_smaller_requirement(self):
        inst = {"name": "test", "gpu": "RTX PRO SERVER 6000", "instance_type": "rtx-2g"}

        async def fake_catalog_count(instance_type):
            return 2

        original = brev_env._get_instance_gpu_count_from_catalog
        brev_env._get_instance_gpu_count_from_catalog = fake_catalog_count
        try:
            asyncio.run(
                brev_env._check_instance_matches(
                    inst,
                    {"gpu_type": "RTX PRO 6000", "gpu_count": 1},
                )
            )
        finally:
            brev_env._get_instance_gpu_count_from_catalog = original

    def test_smaller_gpu_partition_fails_larger_requirement(self):
        inst = {"name": "test", "gpu": "RTX PRO SERVER 6000", "instance_type": "rtx-1g"}

        async def fake_catalog_count(instance_type):
            return 1

        original = brev_env._get_instance_gpu_count_from_catalog
        brev_env._get_instance_gpu_count_from_catalog = fake_catalog_count
        try:
            with self.assertRaisesRegex(RuntimeError, "want at least 2"):
                asyncio.run(
                    brev_env._check_instance_matches(
                        inst,
                        {"gpu_type": "RTX PRO 6000", "gpu_count": 2},
                    )
                )
        finally:
            brev_env._get_instance_gpu_count_from_catalog = original


class LiveResourceChecks(unittest.IsolatedAsyncioTestCase):
    async def test_root_disk_parser_ignores_brev_workspace_suffix(self):
        async def fake_run_brev_exec(instance, command, timeout=30):
            return brev_env.ExecResult(
                stdout="200G\nvss-eval-rtx-1g-2\n",
                stderr=None,
                return_code=0,
            )

        with mock.patch.object(
            brev_env,
            "_run_brev_exec",
            side_effect=fake_run_brev_exec,
        ):
            await brev_env._check_live_resources(
                "vss-eval-rtx-1g-2",
                {"min_root_disk_gb": 160},
            )

    async def test_root_disk_floor_rejects_decorated_small_disk_output(self):
        async def fake_run_brev_exec(instance, command, timeout=30):
            return brev_env.ExecResult(
                stdout="117G\nvss-eval-rtx-1g-2\n",
                stderr=None,
                return_code=0,
            )

        with (
            mock.patch.object(
                brev_env,
                "_run_brev_exec",
                side_effect=fake_run_brev_exec,
            ),
            self.assertRaisesRegex(
                RuntimeError,
                "root disk is 117 GB; task requires at least 160 GB",
            ),
        ):
            await brev_env._check_live_resources(
                "vss-eval-rtx-1g-2",
                {"min_root_disk_gb": 160},
            )

    async def test_required_root_disk_check_fails_closed_on_malformed_output(self):
        async def fake_run_brev_exec(instance, command, timeout=30):
            return brev_env.ExecResult(
                stdout="workspace status unavailable\nvss-eval-rtx-1g-2\n",
                stderr=None,
                return_code=0,
            )

        with (
            mock.patch.object(
                brev_env,
                "_run_brev_exec",
                side_effect=fake_run_brev_exec,
            ),
            self.assertRaisesRegex(
                RuntimeError,
                "root disk could not be determined",
            ),
        ):
            await brev_env._check_live_resources(
                "vss-eval-rtx-1g-2",
                {"min_root_disk_gb": 160},
            )


class NemoClawBrevCommands(unittest.IsolatedAsyncioTestCase):

    def test_attempt_owner_marker_is_optional_and_ci_scoped(self):
        with mock.patch.dict(
            os.environ,
            {"NEMOCLAW_ATTEMPT_OWNER_TOKEN": "not-a-token"},
        ):
            os.environ.pop("SKILLS_EVAL_RUNNER", None)
            self.assertEqual(
                brev_env._nemoclaw_attempt_owner_setup_command(),
                "",
            )

        with mock.patch.dict(
            os.environ,
            {"SKILLS_EVAL_RUNNER": "nemoclaw"},
        ):
            os.environ.pop("NEMOCLAW_ATTEMPT_OWNER_TOKEN", None)
            self.assertEqual(
                brev_env._nemoclaw_attempt_owner_setup_command(),
                "",
            )

    def test_attempt_owner_marker_rejects_invalid_ci_tokens(self):
        invalid_tokens = (
            "a" * 31,
            "A" * 32,
            "g" * 32,
            ("a" * 31) + "\n",
        )
        for token in invalid_tokens:
            with (
                self.subTest(token=repr(token)),
                mock.patch.dict(
                    os.environ,
                    {
                        "SKILLS_EVAL_RUNNER": "nemoclaw",
                        "NEMOCLAW_ATTEMPT_OWNER_TOKEN": token,
                    },
                ),
                self.assertRaisesRegex(
                    RuntimeError,
                    "must be exactly 32 lowercase hexadecimal characters",
                ),
            ):
                brev_env._nemoclaw_attempt_owner_setup_command()

    def test_launcher_guard_runs_payload_once_across_concurrent_replays(self):
        token = "a" * 32
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            owner = root / "owner"
            state_root = root / "launch-state"
            stdout = root / "headless.stdout"
            count = root / "payload-count"
            owner.write_text(token + "\n", encoding="utf-8")
            owner.chmod(0o600)
            payload = textwrap.dedent(
                f"""
                set -euo pipefail
                printf 'run\\n' >> {shlex.quote(str(count))}
                sleep 0.2
                printf 'payload output\\n' > {shlex.quote(str(stdout))}
                cat {shlex.quote(str(stdout))}
                exit 7
                """
            ).strip()
            guarded = brev_env._guard_nemoclaw_launcher_once(
                payload,
                token,
                owner_path=str(owner),
                state_root=str(state_root),
            )

            first = subprocess.Popen(
                ["bash", "-c", guarded],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _ in range(100):
                if count.exists():
                    break
                if first.poll() is not None:
                    self.fail("first guarded launcher exited before its payload ran")
                time.sleep(0.01)
            else:
                self.fail("first guarded launcher did not start its payload")
            second = subprocess.Popen(
                ["bash", "-c", guarded],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            first_stdout, first_stderr = first.communicate(timeout=5)
            second_stdout, second_stderr = second.communicate(timeout=5)
            replay = subprocess.run(
                ["bash", "-c", guarded],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )

            self.assertEqual(first.returncode, 7, first_stderr)
            self.assertEqual(second.returncode, 7, second_stderr)
            self.assertEqual(replay.returncode, 7, replay.stderr)
            self.assertEqual(first_stdout, "payload output\n")
            self.assertEqual(second_stdout, first_stdout)
            self.assertEqual(replay.stdout, first_stdout)
            self.assertEqual(count.read_text(encoding="utf-8"), "run\n")

    def test_launcher_guard_fails_closed_after_unfinished_claim(self):
        token = "b" * 32
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            owner = root / "owner"
            state_root = root / "launch-state"
            state = state_root / token
            count = root / "payload-count"
            owner.write_text(token + "\n", encoding="utf-8")
            owner.chmod(0o600)
            state.mkdir(parents=True)
            (state / "started").write_text("prior\n", encoding="utf-8")
            guarded = brev_env._guard_nemoclaw_launcher_once(
                f"touch {shlex.quote(str(count))}",
                token,
                owner_path=str(owner),
                state_root=str(state_root),
            )

            result = subprocess.run(
                ["bash", "-c", guarded],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )

            self.assertEqual(
                result.returncode,
                brev_env.NEMOCLAW_LAUNCH_GUARD_FAILURE_RC,
            )
            self.assertIn(
                "prior launcher stopped without a terminal result",
                result.stderr,
            )
            self.assertFalse(count.exists())

    def test_launcher_guard_rejects_a_stale_attempt_owner(self):
        current_token = "c" * 32
        stale_token = "d" * 32
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            owner = root / "owner"
            state_root = root / "launch-state"
            count = root / "payload-count"
            owner.write_text(current_token + "\n", encoding="utf-8")
            owner.chmod(0o600)
            guarded = brev_env._guard_nemoclaw_launcher_once(
                f"touch {shlex.quote(str(count))}",
                stale_token,
                owner_path=str(owner),
                state_root=str(state_root),
            )

            result = subprocess.run(
                ["bash", "-c", guarded],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )

            self.assertEqual(
                result.returncode,
                brev_env.NEMOCLAW_LAUNCH_GUARD_FAILURE_RC,
            )
            self.assertIn("does not match this launcher", result.stderr)
            self.assertFalse(state_root.exists())
            self.assertFalse(count.exists())

    async def test_stray_reap_covers_direct_nemoclaw_launcher_exactly(self):
        command = brev_env._stray_agent_reap_command()
        syntax = subprocess.run(
            ["bash", "-n"],
            input=command,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(syntax.returncode, 0, syntax.stderr)
        self.assertIn(
            "\\.github/skill-eval/nemoclaw/headless_runne[r]\\.py",
            command,
        )
        self.assertIn(
            "claude --verbose --output-format=stream-jso[n]",
            command,
        )
        self.assertIn('sort -u', command)
        self.assertIn('kill -9 -- "-$PGID"', command)
        self.assertIn("pid_is_live()", command)
        self.assertIn("group_has_live_members()", command)
        self.assertIn('FAILED="$FAILED $pid"', command)
        self.assertIn("exit 1", command)

    async def test_stray_reap_kills_full_direct_launcher_process_group(self):
        with tempfile.TemporaryDirectory() as td:
            child_file = Path(td) / "child.pid"
            worker = (
                "sleep 30 & child=$!; "
                f"printf '%s\\n' \"$child\" > {shlex.quote(str(child_file))}; "
                "exec -a "
                "'python3 .github/skill-eval/nemoclaw/headless_runner.py' "
                "sleep 30"
            )
            process = subprocess.Popen(
                ["setsid", "bash", "-c", worker],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            child_pid = 0
            try:
                for _ in range(100):
                    if process.poll() is not None:
                        self.fail("test direct launcher exited before reap")
                    if (
                        child_file.exists()
                        and os.getpgid(process.pid) == process.pid
                    ):
                        child_pid = int(child_file.read_text().strip())
                        break
                    time.sleep(0.01)
                else:
                    self.fail("test direct launcher child was not started")

                result = subprocess.run(
                    ["bash", "-lc", brev_env._stray_agent_reap_command()],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=15,
                )
                process.wait(timeout=5)
                members = subprocess.run(
                    ["ps", "-eo", "pid=,pgid=,stat="],
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout.splitlines()
            finally:
                try:
                    os.killpg(process.pid, 9)
                except ProcessLookupError:
                    pass
                if process.poll() is None:
                    process.wait(timeout=5)

        self.assertGreater(child_pid, 0)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            f"[stray-agent-reap] killed pids: {process.pid}",
            result.stdout,
        )
        self.assertFalse(
            any(
                int(fields[1]) == process.pid
                and not fields[2].startswith("Z")
                for line in members
                if len(fields := line.split()) >= 3
            )
        )

    async def test_docker_reset_preserves_only_openshell_bridge(self):
        calls = []

        async def fake_run_brev_exec(instance, command, timeout=brev_env.BREV_EXEC_TIMEOUT):
            calls.append((instance, command, timeout))
            return brev_env.ExecResult(
                stdout="docker runtime reset OK\n",
                stderr=None,
                return_code=0,
            )

        original_exec = brev_env._run_brev_exec
        brev_env._run_brev_exec = fake_run_brev_exec
        try:
            env = brev_env.BrevEnvironment()
            env._instance_name = "vss-eval-test"
            await env._reset_docker_runtime(preserve_openshell_gateway=True)
            await env._reset_docker_runtime()
        finally:
            brev_env._run_brev_exec = original_exec

        self.assertEqual(len(calls), 2)
        command = calls[0][1]
        non_nemoclaw_command = calls[1][1]
        self.assertNotIn("docker network prune", command)
        self.assertIn("preserve_openshell_gateway=1", command)
        self.assertIn("preserve_openshell_gateway=0", non_nemoclaw_command)
        self.assertIn("openshell_network=openshell-docker", command)
        self.assertIn("docker network rm", command)
        self.assertIn("refusing to preserve OpenShell network with driver", command)
        self.assertIn("openshell.ai/managed-by", command)
        self.assertIn("without an IPv4 IPAM gateway", command)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            networks = root / "networks"
            networks.mkdir()
            preserved = networks / "keep-id"
            stale = networks / "stale-id"
            preserved.write_text(
                "openshell-docker bridge openshell 172.29.0.1\n",
                encoding="utf-8",
            )
            stale.write_text(
                "stale-vss bridge unrelated 172.30.0.1\n",
                encoding="utf-8",
            )
            removals = root / "removals.log"
            fake_docker = fake_bin / "docker"
            fake_docker.write_text(
                textwrap.dedent(
                    """\
                    #!/bin/sh
                    set -eu
                    case "$1" in
                      info|ps|volume)
                        exit 0
                        ;;
                      images)
                        printf 'image-id\n'
                        exit 0
                        ;;
                      network)
                        case "$2" in
                          ls)
                            if [ "${FAKE_DOCKER_FAIL:-}" = "network-ls" ]; then
                              exit 3
                            fi
                            for network_file in "$FAKE_DOCKER_NETWORKS"/*; do
                              [ -f "$network_file" ] || continue
                              basename "$network_file"
                            done
                            exit 0
                            ;;
                          inspect)
                            if [ "${FAKE_DOCKER_FAIL:-}" = "network-inspect" ]; then
                              exit 4
                            fi
                            template="$4"
                            network_id="$5"
                            read -r network_name network_driver network_owner network_gateway < \
                              "$FAKE_DOCKER_NETWORKS/$network_id"
                            if [ "$template" = "{{.Name}}" ]; then
                              printf '%s\n' "$network_name"
                            elif [ "$template" = "{{.Driver}}" ]; then
                              printf '%s\n' "$network_driver"
                            elif [ "$template" = '{{index .Labels "openshell.ai/managed-by"}}' ]; then
                              printf '%s\n' "$network_owner"
                            elif [ "$template" = "{{range .IPAM.Config}}{{.Gateway}} {{end}}" ]; then
                              printf '%s\n' "$network_gateway"
                            else
                              exit 2
                            fi
                            exit 0
                            ;;
                          rm)
                            if [ "${FAKE_DOCKER_FAIL:-}" = "network-rm" ]; then
                              exit 5
                            fi
                            network_id="$3"
                            printf '%s\n' "$network_id" >> "$FAKE_DOCKER_REMOVALS"
                            rm "$FAKE_DOCKER_NETWORKS/$network_id"
                            exit 0
                            ;;
                        esac
                        ;;
                    esac
                    exit 2
                    """
                ),
                encoding="utf-8",
            )
            fake_docker.chmod(0o755)
            reset_env = {
                **os.environ,
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "FAKE_DOCKER_NETWORKS": str(networks),
                "FAKE_DOCKER_REMOVALS": str(removals),
            }

            reset = subprocess.run(
                ["bash", "-c", command],
                env=reset_env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(reset.returncode, 0, reset.stderr)
            self.assertIn("images and valid OpenShell bridge preserved", reset.stdout)
            self.assertTrue(preserved.is_file())
            self.assertFalse(stale.exists())
            self.assertEqual(removals.read_text(encoding="utf-8").strip(), "stale-id")

            preserved.write_text(
                "openshell-docker overlay openshell 172.29.0.1\n",
                encoding="utf-8",
            )
            rejected = subprocess.run(
                ["bash", "-c", command],
                env=reset_env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn(
                "refusing to preserve OpenShell network with driver overlay",
                rejected.stderr,
            )

            preserved.write_text(
                "openshell-docker bridge unrelated 172.29.0.1\n",
                encoding="utf-8",
            )
            rejected_owner = subprocess.run(
                ["bash", "-c", command],
                env=reset_env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(rejected_owner.returncode, 0)
            self.assertIn("without managed ownership label", rejected_owner.stderr)

            preserved.write_text(
                "openshell-docker bridge openshell 2001:db8::1\n",
                encoding="utf-8",
            )
            rejected_ipam = subprocess.run(
                ["bash", "-c", command],
                env=reset_env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(rejected_ipam.returncode, 0)
            self.assertIn("without an IPv4 IPAM gateway", rejected_ipam.stderr)

            failed_list = subprocess.run(
                ["bash", "-c", command],
                env={**reset_env, "FAKE_DOCKER_FAIL": "network-ls"},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(failed_list.returncode, 0)
            self.assertIn("failed to enumerate docker networks", failed_list.stderr)

            preserved.write_text(
                "openshell-docker bridge openshell 172.29.0.1\n",
                encoding="utf-8",
            )
            failed_inspect = subprocess.run(
                ["bash", "-c", command],
                env={**reset_env, "FAKE_DOCKER_FAIL": "network-inspect"},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(failed_inspect.returncode, 0)
            self.assertIn("failed to inspect docker network", failed_inspect.stderr)

            stale.write_text(
                "stale-vss bridge unrelated 172.30.0.1\n",
                encoding="utf-8",
            )
            failed_remove = subprocess.run(
                ["bash", "-c", command],
                env={**reset_env, "FAKE_DOCKER_FAIL": "network-rm"},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(failed_remove.returncode, 0)
            self.assertIn("failed to remove docker network", failed_remove.stderr)
            self.assertTrue(stale.exists())
            stale.unlink()

            ordinary_reset = subprocess.run(
                ["bash", "-c", non_nemoclaw_command],
                env=reset_env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(ordinary_reset.returncode, 0, ordinary_reset.stderr)
            self.assertFalse(preserved.exists())
            self.assertIn("images preserved", ordinary_reset.stdout)

    async def test_start_wipes_stale_artifacts_without_deleting_trial_inputs(self):
        calls = []
        reset_kwargs = []
        attempt_owner_token = "0123456789abcdef0123456789abcdef"

        async def fake_find_brev_instance(name):
            return {"name": name, "gpu": "RTX PRO SERVER 6000", "instance_type": "rtx-1g"}

        async def fake_check_instance_matches(instance, requirements):
            return None

        async def fake_check_live_resources(instance, requirements):
            return None

        async def fake_run_brev_exec(instance, command, timeout=brev_env.BREV_EXEC_TIMEOUT):
            calls.append((instance, command, timeout))
            if "echo harbor-ready" in command:
                return brev_env.ExecResult(stdout="harbor-ready\n", stderr=None, return_code=0)
            return brev_env.ExecResult(stdout="ok", stderr=None, return_code=0)

        original_find = brev_env._find_brev_instance
        original_match = brev_env._check_instance_matches
        original_live = brev_env._check_live_resources
        original_exec = brev_env._run_brev_exec
        original_default = brev_env.DEFAULT_INSTANCE
        brev_env._find_brev_instance = fake_find_brev_instance
        brev_env._check_instance_matches = fake_check_instance_matches
        brev_env._check_live_resources = fake_check_live_resources
        brev_env._run_brev_exec = fake_run_brev_exec
        brev_env.DEFAULT_INSTANCE = "vss-eval-test"
        try:
            with tempfile.TemporaryDirectory() as td:
                task_dir = Path(td) / "task"
                env_dir = task_dir / "environment"
                env_dir.mkdir(parents=True)
                (task_dir / "task.toml").write_text(
                    "[metadata]\nrunner = \"nemoclaw\"\n",
                    encoding="utf-8",
                )

                async def noop(self, *args, **kwargs):
                    return None

                async def reset_noop(self, *args, **kwargs):
                    reset_kwargs.append(kwargs)

                env = brev_env.BrevEnvironment()
                env.environment_dir = env_dir
                env._reset_docker_runtime = types.MethodType(reset_noop, env)
                env._sync_repo_to_pr_head = types.MethodType(noop, env)
                env._ensure_nemoclaw_ready = types.MethodType(noop, env)
                with mock.patch.dict(
                    os.environ,
                    {
                        "SKILLS_EVAL_RUNNER": "nemoclaw",
                        "NEMOCLAW_ATTEMPT_OWNER_TOKEN": attempt_owner_token,
                    },
                ):
                    await env.start(force_build=False)
        finally:
            brev_env._find_brev_instance = original_find
            brev_env._check_instance_matches = original_match
            brev_env._check_live_resources = original_live
            brev_env._run_brev_exec = original_exec
            brev_env.DEFAULT_INSTANCE = original_default

        self.assertEqual(
            reset_kwargs,
            [{"preserve_openshell_gateway": True}],
        )
        reset_commands = [command for _, command, _ in calls if "sudo rm -rf" in command]
        self.assertEqual(len(reset_commands), 1)
        reset = reset_commands[0]
        self.assertIn("/logs/artifacts", reset)
        self.assertIn("/logs/verifier", reset)
        for stale_agent_output in (
            "/logs/agent/trajectory.json",
            "/logs/agent/trajectory.jsonl",
            "/logs/agent/openclaw.session.jsonl",
            "/logs/agent/openclaw.txt",
            "/logs/agent/agent.log",
            "/logs/agent/claude-code.txt",
            "/logs/agent/nemoclaw-headless-runner.stdout",
        ):
            self.assertIn(stale_agent_output, reset)
        self.assertIn("Refusing to reset symlinked /logs/agent", reset)
        self.assertNotIn("rm -rf /logs/agent", reset)
        self.assertNotIn("rm -rf /logs/agent/sessions", reset)
        self.assertNotIn("rm -rf /tests", reset)
        self.assertNotIn("rm -rf /solution", reset)
        self.assertNotIn("rm -rf /skills", reset)
        self.assertIn("mkdir -p /logs/agent /logs/verifier /logs/artifacts /tests /solution /skills", reset)
        self.assertLess(reset.index("sudo rm -rf"), reset.index("sudo mkdir -p"))
        self.assertIn(
            "attempt_owner_tmp=/logs/artifacts/.nemoclaw-attempt-owner.$$",
            reset,
        )
        self.assertIn("umask 077", reset)
        self.assertIn(
            f"printf '%s\\n' {attempt_owner_token} > \"$attempt_owner_tmp\"",
            reset,
        )
        self.assertIn('chmod 600 "$attempt_owner_tmp"', reset)
        self.assertIn(
            'mv -f "$attempt_owner_tmp" /logs/artifacts/nemoclaw-attempt-owner',
            reset,
        )
        self.assertLess(
            reset.index("sudo chown -R"),
            reset.index("attempt_owner_tmp="),
        )
        self.assertLess(
            reset.index("sudo chown -R"),
            reset.index("umask 077"),
        )
        self.assertLess(
            reset.index("umask 077"),
            reset.index("attempt_owner_tmp="),
        )
        self.assertLess(
            reset.index("attempt_owner_tmp="),
            reset.index('chmod 600 "$attempt_owner_tmp"'),
        )
        self.assertLess(
            reset.index('chmod 600 "$attempt_owner_tmp"'),
            reset.index('mv -f "$attempt_owner_tmp"'),
        )
        archive_commands = [
            command
            for _, command, _ in calls
            if 'PROJ=/logs/agent/sessions/projects' in command
        ]
        self.assertEqual(len(archive_commands), 1)
        self.assertIn(
            'mv "$PROJ"/* "$ARCHIVE/"',
            archive_commands[0],
        )

    async def test_upload_dir_replaces_trial_input_dirs(self):
        calls = []

        async def fake_run_brev_exec(instance, command, timeout=brev_env.BREV_EXEC_TIMEOUT):
            calls.append((instance, command, timeout))
            return brev_env.ExecResult(stdout="ok", stderr=None, return_code=0)

        async def fake_run_brev_copy(src, dst, timeout=brev_env.BREV_COPY_TIMEOUT):
            calls.append(("copy", f"{src}->{dst}", timeout))
            return brev_env.ExecResult(stdout="ok", stderr=None, return_code=0)

        original_exec = brev_env._run_brev_exec
        original_copy = brev_env._run_brev_copy
        brev_env._run_brev_exec = fake_run_brev_exec
        brev_env._run_brev_copy = fake_run_brev_copy
        try:
            with tempfile.TemporaryDirectory() as td:
                source = Path(td) / "tests"
                source.mkdir()
                (source / "nemoclaw_prompt.md").write_text("fresh prompt\n", encoding="utf-8")

                env = brev_env.BrevEnvironment()
                env._instance_name = "vss-eval-test"
                await env.upload_dir(source, "/tests")
        finally:
            brev_env._run_brev_exec = original_exec
            brev_env._run_brev_copy = original_copy

        extract_commands = [
            command for _, command, _ in calls
            if isinstance(command, str) and "tar -xzf" in command
        ]
        self.assertEqual(len(extract_commands), 1)
        command = extract_commands[0]
        self.assertIn("sudo rm -rf /tests", command)
        self.assertLess(command.index("sudo rm -rf /tests"), command.index("tar -xzf"))

    async def test_upload_dir_does_not_replace_non_trial_dirs(self):
        calls = []

        async def fake_run_brev_exec(instance, command, timeout=brev_env.BREV_EXEC_TIMEOUT):
            calls.append((instance, command, timeout))
            return brev_env.ExecResult(stdout="ok", stderr=None, return_code=0)

        async def fake_run_brev_copy(src, dst, timeout=brev_env.BREV_COPY_TIMEOUT):
            calls.append(("copy", f"{src}->{dst}", timeout))
            return brev_env.ExecResult(stdout="ok", stderr=None, return_code=0)

        original_exec = brev_env._run_brev_exec
        original_copy = brev_env._run_brev_copy
        brev_env._run_brev_exec = fake_run_brev_exec
        brev_env._run_brev_copy = fake_run_brev_copy
        try:
            with tempfile.TemporaryDirectory() as td:
                source = Path(td) / "payload"
                source.mkdir()
                (source / "file.txt").write_text("data\n", encoding="utf-8")

                env = brev_env.BrevEnvironment()
                env._instance_name = "vss-eval-test"
                await env.upload_dir(source, "/tmp/payload")
        finally:
            brev_env._run_brev_exec = original_exec
            brev_env._run_brev_copy = original_copy

        extract_commands = [
            command for _, command, _ in calls
            if isinstance(command, str) and "tar -xzf" in command
        ]
        self.assertEqual(len(extract_commands), 1)
        self.assertNotIn("sudo rm -rf /tmp/payload", extract_commands[0])

    async def test_nemoclaw_setup_sources_profile_without_nounset(self):
        calls = []

        async def fake_run_brev_exec(instance, command, timeout=brev_env.BREV_EXEC_TIMEOUT):
            calls.append((instance, command, timeout))
            return brev_env.ExecResult(stdout="ok", stderr=None, return_code=0)

        original = brev_env._run_brev_exec
        brev_env._run_brev_exec = fake_run_brev_exec
        try:
            env = brev_env.BrevEnvironment()
            env._instance_name = "vss-eval-test"
            await env._ensure_nemoclaw_ready({"required_mcp_tools": ["vss_orchestrator__docker_up"]})
        finally:
            brev_env._run_brev_exec = original

        self.assertEqual(len(calls), 1)
        command = calls[0][1]
        self.assertIn("set -eo pipefail\nset +u\nsource ~/.profile", command)
        self.assertIn("source ~/.profile 2>/dev/null || true\nset -u\nexport PATH", command)
        self.assertNotIn("set -euo pipefail\nsource ~/.profile", command)
        self.assertIn("--required-tools vss_orchestrator__docker_up", command)
        self.assertIn("apt-get install -y -qq libcairo2-dev pkg-config", command)
        self.assertIn("NEMOCLAW_PRESTAGE_ALERTS_MODELS", command)
        self.assertIn("models/gdino/mgdino_mask_head_pruned_dynamic_batch.onnx", command)
        self.assertIn("models/rtdetr-its/model_epoch_035.fp16.onnx", command)
        self.assertIn(
            "rm -rf /tmp/skill-eval/nemoclaw "
            "/logs/artifacts/nemoclaw",
            command,
        )
        self.assertNotRegex(
            command,
            r"rm -rf[^\n]*(?:^|\s)/logs/artifacts(?:\s|$)",
        )
        repair_index = command.index(
            'default_gateway_state_dir="$HOME/.local/state/nemoclaw/'
        )
        adapter_index = command.index(
            "python3 .github/skill-eval/nemoclaw/notebook_setup_adapter.py"
        )
        self.assertLess(repair_index, adapter_index)
        self.assertIn(
            "for db_name in openshell.db openshell.db-wal openshell.db-shm",
            command,
        )
        self.assertIn('if [ "$db_uid" != "0" ]', command)
        self.assertIn(
            'chown --no-dereference "$current_uid:$current_gid" -- "$db_path"',
            command,
        )
        self.assertNotIn("chown -R $gateway_state_dir", command)
        self.assertIn(
            'gateway_release_module="$candidate_root/dist/lib/tunnel/'
            'gateway-port-release.js"',
            command,
        )
        self.assertIn('expected_nemoclaw_version="0.0.80"', command)
        self.assertIn("resolve_nemoclaw_cli_root", command)
        self.assertIn("type -P nemoclaw", command)
        self.assertNotIn("npm root -g", command)
        self.assertNotIn("$HOME/NemoClaw", command)
        self.assertIn('package.get("name") != "nemoclaw"', command)
        self.assertIn("releaseManagedGatewayPort({", command)
        self.assertIn("confirmTimeoutMs: 5000", command)
        self.assertIn(
            ".github/skill-eval/nemoclaw/release_gateway_port.py",
            command,
        )
        self.assertIn(
            "using fail-closed standalone gateway release",
            command,
        )
        self.assertIn(
            '/usr/bin/python3 "$gateway_release_fallback" --port "$gateway_port"',
            command,
        )
        self.assertIn("gateway_port_is_free", command)
        self.assertNotIn("docker network create", command)
        self.assertNotIn("pkill", command)

        reconcile_start = command.index(
            'gateway_port="${NEMOCLAW_GATEWAY_PORT:-8080}"'
        )
        state_init_end = command.index("gateway_port_is_free()", reconcile_start)
        state_init_script = (
            "set -e\n"
            + command[reconcile_start:state_init_end]
            + 'printf "%s|%s|%s\\n" "$NEMOCLAW_GATEWAY_PORT" '
            '"$default_gateway_state_dir" "$gateway_state_dir"\n'
        )
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            base_env = {
                **os.environ,
                "HOME": str(home),
            }
            default_state = subprocess.run(
                ["bash", "-c", state_init_script],
                env={
                    key: value
                    for key, value in base_env.items()
                    if key
                    not in {
                        "NEMOCLAW_GATEWAY_PORT",
                        "NEMOCLAW_OPENSHELL_GATEWAY_STATE_DIR",
                    }
                },
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(default_state.returncode, 0, default_state.stderr)
            self.assertEqual(
                default_state.stdout.strip(),
                f"8080|{home}/.local/state/nemoclaw/openshell-docker-gateway|"
                f"{home}/.local/state/nemoclaw/openshell-docker-gateway",
            )

            custom_state = subprocess.run(
                ["bash", "-c", state_init_script],
                env={
                    **base_env,
                    "NEMOCLAW_GATEWAY_PORT": "19080",
                },
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(custom_state.returncode, 0, custom_state.stderr)
            self.assertEqual(
                custom_state.stdout.strip(),
                f"19080|{home}/.local/state/nemoclaw/"
                "openshell-docker-gateway-19080|"
                f"{home}/.local/state/nemoclaw/openshell-docker-gateway-19080",
            )

            override_dir = Path(td) / "explicit-state"
            overridden_state = subprocess.run(
                ["bash", "-c", state_init_script],
                env={
                    **base_env,
                    "NEMOCLAW_GATEWAY_PORT": "19080",
                    "NEMOCLAW_OPENSHELL_GATEWAY_STATE_DIR": str(override_dir),
                },
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(overridden_state.returncode, 0, overridden_state.stderr)
            self.assertEqual(
                overridden_state.stdout.strip(),
                f"19080|{home}/.local/state/nemoclaw/"
                f"openshell-docker-gateway-19080|{override_dir}",
            )

        reconcile_end = command.index('recreate_value="', reconcile_start)
        reconcile_script = (
            "set -eo pipefail\n"
            "stage() { printf '%s\\n' \"$*\"; }\n"
            f"REPO={shlex.quote(str(Path(__file__).resolve().parents[4]))}\n"
            + command[reconcile_start:reconcile_end]
        )

        def unused_port() -> int:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                return int(sock.getsockname()[1])

        def wait_until_listening(port: int) -> None:
            for _ in range(100):
                with socket.socket() as sock:
                    if sock.connect_ex(("127.0.0.1", port)) == 0:
                        return
                time.sleep(0.02)
            self.fail(f"test listener did not bind port {port}")

        listener_source = (
            "import socket,sys,time;"
            "s=socket.socket();"
            "s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);"
            "s.bind(('127.0.0.1',int(sys.argv[1])));"
            "s.listen();"
            "time.sleep(30)"
        )
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            npm_root = Path(td) / "npm-root"
            module = (
                npm_root
                / "nemoclaw"
                / "dist"
                / "lib"
                / "tunnel"
                / "gateway-port-release.js"
            )
            module.parent.mkdir(parents=True)
            module.write_text("// fake pinned module\n", encoding="utf-8")
            package_root = npm_root / "nemoclaw"
            (package_root / "bin").mkdir()
            (package_root / "bin" / "nemoclaw.js").write_text(
                "#!/bin/sh\nprintf 'nemoclaw v0.0.80\\n'\n",
                encoding="utf-8",
            )
            (package_root / "bin" / "nemoclaw.js").chmod(0o755)
            (package_root / "package.json").write_text(
                json.dumps(
                    {
                        "name": "nemoclaw",
                        "bin": {"nemoclaw": "./bin/nemoclaw.js"},
                    }
                ),
                encoding="utf-8",
            )
            fake_bin = Path(td) / "bin"
            fake_bin.mkdir()
            node_log = Path(td) / "node.log"

            fake_docker = fake_bin / "docker"
            fake_docker.write_text(
                "#!/bin/sh\n"
                "if [ \"${FAKE_NETWORK_PRESENT:-0}\" = 1 ]; then "
                "printf 'openshell-docker\\n'; fi\n",
                encoding="utf-8",
            )
            fake_docker.chmod(0o755)
            fake_nemoclaw = fake_bin / "nemoclaw"
            fake_nemoclaw.symlink_to(package_root / "bin" / "nemoclaw.js")
            fake_node = fake_bin / "node"
            fake_node.write_text(
                "#!/bin/sh\n"
                "if [ \"$#\" -gt 0 ]; then "
                "printf 'nemoclaw v0.0.80\\n'; exit 0; fi\n"
                "if [ -z \"${GATEWAY_RELEASE_PORT:-}\" ]; then exit 0; fi\n"
                "printf '%s|%s\\n' \"$GATEWAY_RELEASE_MODULE\" "
                "\"$GATEWAY_RELEASE_PORT\" >> \"$FAKE_NODE_LOG\"\n"
                "if [ \"${FAKE_NODE_NOOP:-0}\" != 1 ]; then "
                "kill \"$FAKE_GATEWAY_PID\"; fi\n",
                encoding="utf-8",
            )
            fake_node.chmod(0o755)
            base_env = {
                **os.environ,
                "HOME": str(home),
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "FAKE_NODE_LOG": str(node_log),
            }

            port = unused_port()
            listener = subprocess.Popen(
                [sys.executable, "-c", listener_source, str(port)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                wait_until_listening(port)
                reconciled = subprocess.run(
                    ["bash", "-c", reconcile_script],
                    env={
                        **base_env,
                        "NEMOCLAW_GATEWAY_PORT": str(port),
                        "FAKE_GATEWAY_PID": str(listener.pid),
                    },
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(reconciled.returncode, 0, reconciled.stderr)
                listener.wait(timeout=5)
                self.assertIn(
                    f"{module}|{port}",
                    node_log.read_text(encoding="utf-8"),
                )
                self.assertIn("released stale host gateway", reconciled.stdout)
            finally:
                if listener.poll() is None:
                    listener.terminate()
                    listener.wait(timeout=5)

            fake_nemoclaw.unlink()
            installer_prefix = Path(td) / "installer-prefix"
            installer_bin = installer_prefix / "bin"
            installer_bin.mkdir(parents=True)
            installer_target = installer_bin / "nemoclaw"
            installer_target.symlink_to(package_root / "bin" / "nemoclaw.js")
            fake_nemoclaw.write_text(
                "#!/usr/bin/env bash\n"
                f'export PATH="{fake_bin}:$PATH"\n'
                f'exec "{installer_target}" "$@"\n',
                encoding="utf-8",
            )
            fake_nemoclaw.chmod(0o755)
            installer_port = unused_port()
            installer_listener = subprocess.Popen(
                [sys.executable, "-c", listener_source, str(installer_port)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                wait_until_listening(installer_port)
                installer_result = subprocess.run(
                    ["bash", "-c", reconcile_script],
                    env={
                        **base_env,
                        "NEMOCLAW_GATEWAY_PORT": str(installer_port),
                        "FAKE_GATEWAY_PID": str(installer_listener.pid),
                    },
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(
                    installer_result.returncode,
                    0,
                    installer_result.stderr,
                )
                installer_listener.wait(timeout=5)
                self.assertEqual(
                    node_log.read_text(encoding="utf-8").splitlines()[-1],
                    f"{module}|{installer_port}",
                )
            finally:
                if installer_listener.poll() is None:
                    installer_listener.terminate()
                    installer_listener.wait(timeout=5)

            cli_package_root = Path(td) / "cli-source"
            cli_module = (
                cli_package_root
                / "dist"
                / "lib"
                / "tunnel"
                / "gateway-port-release.js"
            )
            cli_module.parent.mkdir(parents=True)
            cli_module.write_text("// fake CLI-derived module\n", encoding="utf-8")
            (cli_package_root / "bin").mkdir()
            cli_target = cli_package_root / "bin" / "nemoclaw.js"
            cli_target.write_text(
                "#!/bin/sh\nprintf 'nemoclaw v0.0.80\\n'\n",
                encoding="utf-8",
            )
            cli_target.chmod(0o755)
            (cli_package_root / "package.json").write_text(
                json.dumps(
                    {
                        "name": "nemoclaw",
                        "bin": {"nemoclaw": "./bin/nemoclaw.js"},
                    }
                ),
                encoding="utf-8",
            )
            fake_nemoclaw.unlink()
            dev_shim_contents = (
                "#!/usr/bin/env bash\n"
                "# NemoClaw dev-shim - managed by scripts/npm-link-or-shim.sh\n"
                f'export PATH="{fake_bin}:$PATH"\n'
                f'exec "{cli_target}" "$@"\n'
            )
            fake_nemoclaw.write_text(dev_shim_contents, encoding="utf-8")
            fake_nemoclaw.chmod(0o755)
            cli_port = unused_port()
            cli_listener = subprocess.Popen(
                [sys.executable, "-c", listener_source, str(cli_port)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                wait_until_listening(cli_port)
                cli_result = subprocess.run(
                    ["bash", "-c", reconcile_script],
                    env={
                        **base_env,
                        "NEMOCLAW_GATEWAY_PORT": str(cli_port),
                        "FAKE_GATEWAY_PID": str(cli_listener.pid),
                    },
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(cli_result.returncode, 0, cli_result.stderr)
                cli_listener.wait(timeout=5)
                self.assertEqual(
                    node_log.read_text(encoding="utf-8").splitlines()[-1],
                    f"{cli_module}|{cli_port}",
                )
            finally:
                if cli_listener.poll() is None:
                    cli_listener.terminate()
                    cli_listener.wait(timeout=5)

            hidden_cli_module = cli_module.with_suffix(".disabled")
            cli_module.rename(hidden_cli_module)
            incomplete_port = unused_port()
            gateway_executable = Path(td) / "gateway-runtime" / "openshell-gateway"
            gateway_executable.parent.mkdir()
            shutil.copy2(sys.executable, gateway_executable)
            incomplete_listener = subprocess.Popen(
                [
                    str(gateway_executable),
                    "-c",
                    listener_source,
                    str(incomplete_port),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                wait_until_listening(incomplete_port)
                incomplete = subprocess.run(
                    ["bash", "-c", reconcile_script],
                    env={
                        **base_env,
                        "NEMOCLAW_GATEWAY_PORT": str(incomplete_port),
                        "FAKE_GATEWAY_PID": str(incomplete_listener.pid),
                    },
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(incomplete.returncode, 0, incomplete.stderr)
                self.assertIn(
                    "Active NemoClaw gateway release unavailable: "
                    "package is incomplete",
                    incomplete.stderr,
                )
                self.assertIn(
                    "using fail-closed standalone gateway release",
                    incomplete.stdout,
                )
                incomplete_listener.wait(timeout=5)
            finally:
                if incomplete_listener.poll() is None:
                    incomplete_listener.terminate()
                    incomplete_listener.wait(timeout=5)
                hidden_cli_module.rename(cli_module)

            fake_nemoclaw.write_text(
                dev_shim_contents + "echo unexpected-command\n",
                encoding="utf-8",
            )
            unexpected_port = unused_port()
            unexpected_listener = subprocess.Popen(
                [sys.executable, "-c", listener_source, str(unexpected_port)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                wait_until_listening(unexpected_port)
                unexpected = subprocess.run(
                    ["bash", "-c", reconcile_script],
                    env={
                        **base_env,
                        "NEMOCLAW_GATEWAY_PORT": str(unexpected_port),
                        "FAKE_GATEWAY_PID": str(unexpected_listener.pid),
                    },
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertNotEqual(unexpected.returncode, 0)
                self.assertIn(
                    "Active NemoClaw gateway release unavailable: "
                    "launcher is not recognized",
                    unexpected.stderr,
                )
                self.assertIsNone(unexpected_listener.poll())
            finally:
                unexpected_listener.terminate()
                unexpected_listener.wait(timeout=5)
                fake_nemoclaw.write_text(dev_shim_contents, encoding="utf-8")

            node_calls_before = node_log.read_text(encoding="utf-8")
            preserved_port = unused_port()
            preserved_listener = subprocess.Popen(
                [sys.executable, "-c", listener_source, str(preserved_port)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                wait_until_listening(preserved_port)
                preserved_result = subprocess.run(
                    ["bash", "-c", reconcile_script],
                    env={
                        **base_env,
                        "NEMOCLAW_GATEWAY_PORT": str(preserved_port),
                        "FAKE_GATEWAY_PID": str(preserved_listener.pid),
                        "FAKE_NETWORK_PRESENT": "1",
                    },
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(
                    preserved_result.returncode,
                    0,
                    preserved_result.stderr,
                )
                self.assertIsNone(preserved_listener.poll())
                self.assertEqual(
                    node_log.read_text(encoding="utf-8"),
                    node_calls_before,
                )
            finally:
                preserved_listener.terminate()
                preserved_listener.wait(timeout=5)

            blocked_port = unused_port()
            blocked_listener = subprocess.Popen(
                [sys.executable, "-c", listener_source, str(blocked_port)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                wait_until_listening(blocked_port)
                blocked = subprocess.run(
                    ["bash", "-c", reconcile_script],
                    env={
                        **base_env,
                        "NEMOCLAW_GATEWAY_PORT": str(blocked_port),
                        "FAKE_GATEWAY_PID": str(blocked_listener.pid),
                        "FAKE_NODE_NOOP": "1",
                    },
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertNotEqual(blocked.returncode, 0)
                self.assertIn("still busy after scoped release", blocked.stderr)
                self.assertIsNone(blocked_listener.poll())
            finally:
                blocked_listener.terminate()
                blocked_listener.wait(timeout=5)

            fake_nemoclaw.write_text(
                "#!/bin/sh\nprintf 'nemoclaw v0.0.80\\n'\n",
                encoding="utf-8",
            )
            cli_module.unlink()
            module.unlink()
            missing_port = unused_port()
            missing_listener = subprocess.Popen(
                [sys.executable, "-c", listener_source, str(missing_port)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                wait_until_listening(missing_port)
                missing = subprocess.run(
                    ["bash", "-c", reconcile_script],
                    env={
                        **base_env,
                        "NEMOCLAW_GATEWAY_PORT": str(missing_port),
                        "FAKE_GATEWAY_PID": str(missing_listener.pid),
                    },
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertNotEqual(missing.returncode, 0)
                self.assertIn(
                    "Active NemoClaw gateway release unavailable: "
                    "launcher is not recognized",
                    missing.stderr,
                )
                self.assertIsNone(missing_listener.poll())
            finally:
                missing_listener.terminate()
                missing_listener.wait(timeout=5)

        repair_end = command.index("if command -v apt-get", repair_index)
        repair_start = command.index('recreate_value="', repair_index)
        repair_script = (
            "set -e\n"
            "stage() { :; }\n"
            'default_gateway_state_dir="$HOME/.local/state/nemoclaw/'
            'openshell-docker-gateway"\n'
            'gateway_state_dir="${NEMOCLAW_OPENSHELL_GATEWAY_STATE_DIR:-'
            '$default_gateway_state_dir}"\n'
            + command[repair_start:repair_end]
        )
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            state_dir = (
                home
                / ".local"
                / "state"
                / "nemoclaw"
                / "openshell-docker-gateway"
            )
            state_dir.mkdir(parents=True)
            for db_name in ("openshell.db", "openshell.db-wal", "openshell.db-shm"):
                (state_dir / db_name).write_text("test\n", encoding="utf-8")

            fake_bin = Path(td) / "bin"
            fake_bin.mkdir()
            sudo_log = Path(td) / "sudo.log"
            fake_stat = fake_bin / "stat"
            fake_stat.write_text("#!/bin/sh\nprintf '0\\n'\n", encoding="utf-8")
            fake_stat.chmod(0o755)
            fake_sudo = fake_bin / "sudo"
            fake_sudo.write_text(
                "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$SUDO_LOG\"\n",
                encoding="utf-8",
            )
            fake_sudo.chmod(0o755)
            repair_env = {
                **os.environ,
                "HOME": str(home),
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "SUDO_LOG": str(sudo_log),
                "NEMOCLAW_RECREATE_SANDBOX": " Yes ",
            }
            repaired = subprocess.run(
                ["bash", "-c", repair_script],
                env=repair_env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(repaired.returncode, 0, repaired.stderr)
            chown_calls = [
                line
                for line in sudo_log.read_text(encoding="utf-8").splitlines()
                if "chown --no-dereference" in line
            ]
            self.assertEqual(len(chown_calls), 3)
            for db_name in ("openshell.db", "openshell.db-wal", "openshell.db-shm"):
                self.assertTrue(any(db_name in line for line in chown_calls))

            (state_dir / "openshell.db-wal").unlink()
            (state_dir / "openshell.db-wal").symlink_to(state_dir / "openshell.db")
            rejected = subprocess.run(
                ["bash", "-c", repair_script],
                env=repair_env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("Refusing to repair symlinked", rejected.stderr)

            calls_before_override = sudo_log.read_text(encoding="utf-8")
            override_env = {
                **repair_env,
                "NEMOCLAW_OPENSHELL_GATEWAY_STATE_DIR": str(Path(td) / "outside"),
            }
            skipped = subprocess.run(
                ["bash", "-c", repair_script],
                env=override_env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(skipped.returncode, 0, skipped.stderr)
            self.assertEqual(
                sudo_log.read_text(encoding="utf-8"),
                calls_before_override,
            )

            nemoclaw_dir = state_dir.parent
            real_nemoclaw_dir = nemoclaw_dir.with_name("nemoclaw-real")
            nemoclaw_dir.rename(real_nemoclaw_dir)
            nemoclaw_dir.symlink_to(real_nemoclaw_dir, target_is_directory=True)
            rejected_parent = subprocess.run(
                ["bash", "-c", repair_script],
                env=repair_env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(rejected_parent.returncode, 0)
            self.assertIn(
                "Refusing to repair through symlinked",
                rejected_parent.stderr,
            )

    async def test_nemoclaw_launcher_bypasses_outer_claude(self):
        calls = []

        async def fake_run_brev_exec(instance, command, timeout=brev_env.BREV_EXEC_TIMEOUT):
            calls.append((instance, command, timeout))
            return brev_env.ExecResult(stdout="ok", stderr=None, return_code=0)

        original = brev_env._run_brev_exec
        brev_env._run_brev_exec = fake_run_brev_exec
        previous_fast = os.environ.pop("NEMOCLAW_FAST_READINESS_MODE", None)
        previous_timeout = os.environ.get("NEMOCLAW_AGENT_TIMEOUT_SEC")
        previous_runner = os.environ.get("SKILLS_EVAL_RUNNER")
        previous_owner = os.environ.get("NEMOCLAW_ATTEMPT_OWNER_TOKEN")
        attempt_owner_token = "e" * 32
        os.environ["NEMOCLAW_AGENT_TIMEOUT_SEC"] = "3300"
        os.environ["SKILLS_EVAL_RUNNER"] = "nemoclaw"
        os.environ["NEMOCLAW_ATTEMPT_OWNER_TOKEN"] = attempt_owner_token
        try:
            with tempfile.TemporaryDirectory() as tmp:
                task_dir = Path(tmp) / "rtxpro6000bw"
                tests_dir = task_dir / "tests"
                env_dir = task_dir / "environment"
                tests_dir.mkdir(parents=True)
                env_dir.mkdir()
                (tests_dir / "nemoclaw_prompt.md").write_text(
                    "Use /vss-ask-video for this exact trial.\n",
                    encoding="utf-8",
                )

                env = brev_env.BrevEnvironment()
                env._instance_name = "vss-eval-test"
                env.environment_dir = env_dir
                env._task_metadata = {
                    "runner": "nemoclaw",
                    "deployment_profile": "base",
                    "expected_skill": "vss-ask-video",
                }
                await env.exec(
                    "printf %s \"$HARBOR_CLAUDE_CODE_INSTRUCTION_123\" "
                    "| claude --verbose --print",
                    env={
                        "HARBOR_CLAUDE_CODE_INSTRUCTION_123": (
                            "Run python3 .github/skill-eval/nemoclaw/"
                            "headless_runner.py --prompt-file "
                            "/tests/nemoclaw_prompt.md with the generated prompt."
                        )
                    },
                    timeout_sec=123,
                )
        finally:
            brev_env._run_brev_exec = original
            if previous_fast is not None:
                os.environ["NEMOCLAW_FAST_READINESS_MODE"] = previous_fast
            if previous_timeout is None:
                os.environ.pop("NEMOCLAW_AGENT_TIMEOUT_SEC", None)
            else:
                os.environ["NEMOCLAW_AGENT_TIMEOUT_SEC"] = previous_timeout
            if previous_runner is None:
                os.environ.pop("SKILLS_EVAL_RUNNER", None)
            else:
                os.environ["SKILLS_EVAL_RUNNER"] = previous_runner
            if previous_owner is None:
                os.environ.pop("NEMOCLAW_ATTEMPT_OWNER_TOKEN", None)
            else:
                os.environ["NEMOCLAW_ATTEMPT_OWNER_TOKEN"] = previous_owner

        self.assertEqual(len(calls), 1)
        command = calls[0][1]
        self.assertIn("NemoClaw direct Harbor launcher", command)
        self.assertIn("python3 .github/skill-eval/nemoclaw/headless_runner.py", command)
        self.assertIn("--launch-mode cli", command)
        self.assertIn("--agent-log-dir /logs/agent", command)
        self.assertIn("--wait-profile base", command)
        self.assertIn("--expected-skill vss-ask-video", command)
        self.assertIn(
            "--prompt-file /tmp/skill-eval/nemoclaw/current_prompt.md",
            command,
        )
        self.assertIn(
            "base64 -d > /tmp/skill-eval/nemoclaw/current_prompt.md",
            command,
        )
        self.assertIn(f"attempt_token={attempt_owner_token}", command)
        self.assertIn("flock -x 9", command)
        self.assertLess(
            command.index("flock -x 9"),
            command.index("NemoClaw direct Harbor launcher"),
        )
        self.assertLess(
            command.index("flock -x 9"),
            command.index("base64 -d > /tmp/skill-eval/nemoclaw/current_prompt.md"),
        )
        self.assertLess(
            command.index("flock -x 9"),
            command.index(
                "python3 .github/skill-eval/nemoclaw/headless_runner.py"
            ),
        )
        self.assertNotIn("| claude --verbose --print", command)
        self.assertNotIn("--prompt-file /tests/nemoclaw_prompt.md", command)
        self.assertEqual(calls[0][2], 3420)

    async def test_nemoclaw_launcher_can_opt_into_fast_readiness_mode(self):
        calls = []

        async def fake_run_brev_exec(instance, command, timeout=brev_env.BREV_EXEC_TIMEOUT):
            calls.append((instance, command, timeout))
            return brev_env.ExecResult(stdout="ok", stderr=None, return_code=0)

        original = brev_env._run_brev_exec
        previous_fast = os.environ.get("NEMOCLAW_FAST_READINESS_MODE")
        brev_env._run_brev_exec = fake_run_brev_exec
        os.environ["NEMOCLAW_FAST_READINESS_MODE"] = "1"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                task_dir = Path(tmp) / "rtxpro6000bw"
                tests_dir = task_dir / "tests"
                env_dir = task_dir / "environment"
                tests_dir.mkdir(parents=True)
                env_dir.mkdir()
                (tests_dir / "nemoclaw_prompt.md").write_text(
                    "Use /vss-ask-video for this exact trial.\n",
                    encoding="utf-8",
                )

                env = brev_env.BrevEnvironment()
                env._instance_name = "vss-eval-test"
                env.environment_dir = env_dir
                env._task_metadata = {
                    "runner": "nemoclaw",
                    "deployment_profile": "base",
                    "expected_skill": "vss-ask-video",
                }
                await env.exec(
                    "claude --print 'run python3 "
                    ".github/skill-eval/nemoclaw/headless_runner.py'",
                    timeout_sec=123,
                )
        finally:
            brev_env._run_brev_exec = original
            if previous_fast is None:
                os.environ.pop("NEMOCLAW_FAST_READINESS_MODE", None)
            else:
                os.environ["NEMOCLAW_FAST_READINESS_MODE"] = previous_fast

        self.assertIn("--wait-profile base", calls[0][1])

    async def test_nemoclaw_launcher_embeds_current_prompt_when_task_dir_exists(self):
        calls = []

        async def fake_run_brev_exec(instance, command, timeout=brev_env.BREV_EXEC_TIMEOUT):
            calls.append((instance, command, timeout))
            return brev_env.ExecResult(stdout="ok", stderr=None, return_code=0)

        original = brev_env._run_brev_exec
        brev_env._run_brev_exec = fake_run_brev_exec
        try:
            with tempfile.TemporaryDirectory() as tmp:
                task_dir = Path(tmp) / "rtxpro6000bw"
                tests_dir = task_dir / "tests"
                env_dir = task_dir / "environment"
                tests_dir.mkdir(parents=True)
                env_dir.mkdir()
                (tests_dir / "nemoclaw_prompt.md").write_text(
                    "Use /vss-deploy-profile for this exact trial.\n",
                    encoding="utf-8",
                )

                env = brev_env.BrevEnvironment()
                env._instance_name = "vss-eval-test"
                env.environment_dir = env_dir
                env._task_metadata = {
                    "runner": "nemoclaw",
                    "expected_skill": "vss-deploy-profile",
                }
                await env.exec(
                    "claude --print 'run python3 .github/skill-eval/nemoclaw/headless_runner.py'",
                    timeout_sec=123,
                )
        finally:
            brev_env._run_brev_exec = original

        self.assertEqual(len(calls), 1)
        command = calls[0][1]
        self.assertIn("/tmp/skill-eval/nemoclaw/current_prompt.md", command)
        self.assertIn("base64 -d > /tmp/skill-eval/nemoclaw/current_prompt.md", command)
        self.assertIn("--expected-skill vss-deploy-profile", command)
        self.assertNotIn("--prompt-file /tests/nemoclaw_prompt.md", command)

    async def test_nemoclaw_launcher_bypasses_outer_claude_without_metadata(self):
        calls = []

        async def fake_run_brev_exec(instance, command, timeout=brev_env.BREV_EXEC_TIMEOUT):
            calls.append((instance, command, timeout))
            return brev_env.ExecResult(stdout="ok", stderr=None, return_code=0)

        original = brev_env._run_brev_exec
        brev_env._run_brev_exec = fake_run_brev_exec
        try:
            with tempfile.TemporaryDirectory() as tmp:
                task_dir = Path(tmp) / "rtxpro6000bw"
                tests_dir = task_dir / "tests"
                env_dir = task_dir / "environment"
                tests_dir.mkdir(parents=True)
                env_dir.mkdir()
                (tests_dir / "nemoclaw_prompt.md").write_text(
                    "Use /vss-deploy-profile for this exact trial.\n",
                    encoding="utf-8",
                )

                env = brev_env.BrevEnvironment()
                env._instance_name = "vss-eval-test"
                env.environment_dir = env_dir
                env._task_metadata = {}
                await env.exec(
                    "claude --verbose --print",
                    env={
                        "HARBOR_CLAUDE_CODE_INSTRUCTION_456": (
                            "Run python3 .github/skill-eval/nemoclaw/"
                            "headless_runner.py with the generated prompt."
                        )
                    },
                    timeout_sec=123,
                )
        finally:
            brev_env._run_brev_exec = original

        self.assertEqual(len(calls), 1)
        command = calls[0][1]
        self.assertIn("NemoClaw direct Harbor launcher", command)
        self.assertIn("python3 .github/skill-eval/nemoclaw/headless_runner.py", command)
        self.assertNotIn("claude --verbose --print", command)

    async def test_nemoclaw_launcher_fails_closed_without_current_prompt(self):
        calls = []

        async def fake_run_brev_exec(instance, command, timeout=brev_env.BREV_EXEC_TIMEOUT):
            calls.append((instance, command, timeout))
            return brev_env.ExecResult(stdout="ok", stderr=None, return_code=0)

        original = brev_env._run_brev_exec
        brev_env._run_brev_exec = fake_run_brev_exec
        try:
            with tempfile.TemporaryDirectory() as tmp:
                task_dir = Path(tmp) / "rtxpro6000bw"
                env_dir = task_dir / "environment"
                env_dir.mkdir(parents=True)

                env = brev_env.BrevEnvironment()
                env._instance_name = "vss-eval-test"
                env.environment_dir = env_dir
                env._task_metadata = {"runner": "nemoclaw"}
                with self.assertRaisesRegex(
                    RuntimeError,
                    "refusing to fall back",
                ):
                    await env.exec(
                        "claude --verbose --print",
                        env={
                            "HARBOR_CLAUDE_CODE_INSTRUCTION_789": (
                                "Run python3 .github/skill-eval/nemoclaw/"
                                "headless_runner.py with the generated prompt."
                            )
                        },
                        timeout_sec=123,
                    )
        finally:
            brev_env._run_brev_exec = original

        self.assertEqual(calls, [])

    async def test_nemoclaw_intercept_only_matches_launcher(self):
        calls = []

        async def fake_run_brev_exec(instance, command, timeout=brev_env.BREV_EXEC_TIMEOUT):
            calls.append((instance, command, timeout))
            return brev_env.ExecResult(stdout="ok", stderr=None, return_code=0)

        original = brev_env._run_brev_exec
        brev_env._run_brev_exec = fake_run_brev_exec
        try:
            env = brev_env.BrevEnvironment()
            env._instance_name = "vss-eval-test"
            env._task_metadata = {"runner": "nemoclaw"}
            await env.exec("echo no launcher here", timeout_sec=123)
        finally:
            brev_env._run_brev_exec = original

        self.assertEqual(len(calls), 1)
        self.assertIn("echo no launcher here", calls[0][1])
        self.assertEqual(calls[0][2], 123)

    async def test_repo_sync_injects_pr_head_from_coordinator_env(self):
        calls = []

        async def fake_run_brev_exec(instance, command, timeout=brev_env.BREV_EXEC_TIMEOUT):
            calls.append((instance, command, timeout))
            return brev_env.ExecResult(stdout="synced repo to abc1234\n", stderr=None, return_code=0)

        original = brev_env._run_brev_exec
        old_head = os.environ.get("PR_HEAD_SHA")
        old_repo = os.environ.get("PR_REPO")
        brev_env._run_brev_exec = fake_run_brev_exec
        os.environ["PR_HEAD_SHA"] = "abc1234"
        os.environ["PR_REPO"] = "NVIDIA-AI-Blueprints/video-search-and-summarization"
        try:
            env = brev_env.BrevEnvironment()
            env._instance_name = "vss-eval-test"
            await env._sync_repo_to_pr_head()
        finally:
            brev_env._run_brev_exec = original
            if old_head is None:
                os.environ.pop("PR_HEAD_SHA", None)
            else:
                os.environ["PR_HEAD_SHA"] = old_head
            if old_repo is None:
                os.environ.pop("PR_REPO", None)
            else:
                os.environ["PR_REPO"] = old_repo

        self.assertEqual(len(calls), 1)
        command = calls[0][1]
        self.assertIn("PR_HEAD_SHA=abc1234", command)
        self.assertIn("PR_REPO=NVIDIA-AI-Blueprints/video-search-and-summarization", command)
        self.assertNotIn('PR_HEAD_SHA="${PR_HEAD_SHA:-}"', command)
        self.assertIn('"$REPO/deployments" "$REPO/deploy/docker/data-dir"', command)
        self.assertIn("sudo rm -rf \"$stale_path\"", command)
        self.assertIn("git clean failed; repairing checkout ownership", command)
        self.assertIn("-path \"$REPO/data\" -prune", command)
        self.assertEqual(command.count("-e /.env"), 3)
        self.assertNotIn("-e .env", command)
        self.assertIn("Nested dotenv files must be removed", command)


class UploadDirTarballCopy(unittest.IsolatedAsyncioTestCase):

    async def test_upload_dir_copies_tarball_and_extracts_with_short_command(self):
        exec_calls = []
        copy_calls = []

        async def fake_run_brev_exec(instance, command, timeout=brev_env.BREV_EXEC_TIMEOUT):
            exec_calls.append((instance, command, timeout))
            return brev_env.ExecResult(stdout="", stderr=None, return_code=0)

        async def fake_run_brev_copy(src, dst, timeout=brev_env.BREV_COPY_TIMEOUT):
            copy_calls.append((src, dst, timeout))
            self.assertTrue(Path(src).is_file())
            return brev_env.ExecResult(stdout="", stderr=None, return_code=0)

        original_exec = brev_env._run_brev_exec
        original_copy = brev_env._run_brev_copy
        brev_env._run_brev_exec = fake_run_brev_exec
        brev_env._run_brev_copy = fake_run_brev_copy
        try:
            with tempfile.TemporaryDirectory() as td:
                src_dir = Path(td) / "skills"
                src_dir.mkdir()
                (src_dir / "SKILL.md").write_text("test skill\n")

                env = brev_env.BrevEnvironment()
                env._instance_name = "vss-eval-test"
                await env.upload_dir(src_dir, "/skills")
        finally:
            brev_env._run_brev_exec = original_exec
            brev_env._run_brev_copy = original_copy

        self.assertEqual(len(copy_calls), 1)
        copied_src, copied_dst, _ = copy_calls[0]
        self.assertTrue(copied_src.endswith(".tar.gz"))
        self.assertFalse(Path(copied_src).exists())
        self.assertRegex(
            copied_dst,
            r"^vss-eval-test:/tmp/skill-eval/uploads/[0-9a-f]+/archive\.tar\.gz$",
        )

        commands = [call[1] for call in exec_calls]
        self.assertEqual(len(commands), 2)
        self.assertIn("mkdir -p /tmp/skill-eval/uploads/", commands[0])
        extract_cmd = commands[1]
        self.assertIn("tar -xzf", extract_cmd)
        self.assertIn("-C /skills", extract_cmd)
        self.assertIn("rm -f /tmp/skill-eval/uploads/", extract_cmd)
        self.assertIn("rmdir /tmp/skill-eval/uploads/", extract_cmd)
        self.assertLess(max(len(command) for command in commands), 1000)
        self.assertNotIn("base64", "\n".join(commands))
        self.assertNotIn("echo '", "\n".join(commands))

    async def test_upload_dir_raises_when_tarball_copy_fails(self):
        exec_calls = []
        copy_calls = []

        async def fake_run_brev_exec(instance, command, timeout=brev_env.BREV_EXEC_TIMEOUT):
            exec_calls.append((instance, command, timeout))
            return brev_env.ExecResult(stdout="", stderr=None, return_code=0)

        async def fake_run_brev_copy(src, dst, timeout=brev_env.BREV_COPY_TIMEOUT):
            copy_calls.append((src, dst, timeout))
            return brev_env.ExecResult(stdout="", stderr="copy failed", return_code=1)

        original_exec = brev_env._run_brev_exec
        original_copy = brev_env._run_brev_copy
        brev_env._run_brev_exec = fake_run_brev_exec
        brev_env._run_brev_copy = fake_run_brev_copy
        try:
            with tempfile.TemporaryDirectory() as td:
                src_dir = Path(td) / "skills"
                src_dir.mkdir()
                (src_dir / "SKILL.md").write_text("test skill\n")

                env = brev_env.BrevEnvironment()
                env._instance_name = "vss-eval-test"
                with self.assertRaisesRegex(
                    RuntimeError,
                    "Upload dir failed on vss-eval-test: copy failed",
                ):
                    await env.upload_dir(src_dir, "/skills")
        finally:
            brev_env._run_brev_exec = original_exec
            brev_env._run_brev_copy = original_copy

        self.assertEqual(len(copy_calls), 1)
        copied_src, _, _ = copy_calls[0]
        self.assertFalse(Path(copied_src).exists())
        self.assertEqual(len(exec_calls), 1)
        self.assertIn("mkdir -p /tmp/skill-eval/uploads/", exec_calls[0][1])

    async def test_upload_dir_binds_remote_mkdir_failure_to_instance(self):
        async def fake_run_brev_exec(
            instance,
            command,
            timeout=brev_env.BREV_EXEC_TIMEOUT,
        ):
            return brev_env.ExecResult(
                stdout="",
                stderr="No space left on device",
                return_code=1,
            )

        original_exec = brev_env._run_brev_exec
        brev_env._run_brev_exec = fake_run_brev_exec
        try:
            with tempfile.TemporaryDirectory() as td:
                src_dir = Path(td) / "skills"
                src_dir.mkdir()
                (src_dir / "SKILL.md").write_text("test skill\n")

                env = brev_env.BrevEnvironment()
                env._instance_name = "vss-eval-test"
                with self.assertRaisesRegex(
                    RuntimeError,
                    (
                        "Upload dir failed on vss-eval-test: "
                        "No space left on device"
                    ),
                ):
                    await env.upload_dir(src_dir, "/skills")
        finally:
            brev_env._run_brev_exec = original_exec

    async def test_upload_dir_retries_transient_brev_copy_failure(self):
        exec_calls = []
        copy_calls = []

        async def fake_run_brev_exec(instance, command, timeout=brev_env.BREV_EXEC_TIMEOUT):
            exec_calls.append((instance, command, timeout))
            return brev_env.ExecResult(stdout="", stderr=None, return_code=0)

        async def fake_run_brev_copy(src, dst, timeout=brev_env.BREV_COPY_TIMEOUT):
            copy_calls.append((src, dst, timeout))
            if len(copy_calls) == 1:
                return brev_env.ExecResult(
                    stdout="",
                    stderr=(
                        "waiting for instance to be ready... "
                        "rpc error: code = Unavailable desc = error reading from server: EOF"
                    ),
                    return_code=1,
                )
            return brev_env.ExecResult(stdout="", stderr=None, return_code=0)

        original_exec = brev_env._run_brev_exec
        original_copy = brev_env._run_brev_copy
        original_backoff = brev_env.BREV_UPLOAD_BACKOFF_SEC
        brev_env._run_brev_exec = fake_run_brev_exec
        brev_env._run_brev_copy = fake_run_brev_copy
        brev_env.BREV_UPLOAD_BACKOFF_SEC = 0
        try:
            with tempfile.TemporaryDirectory() as td:
                src_dir = Path(td) / "skills"
                src_dir.mkdir()
                (src_dir / "SKILL.md").write_text("test skill\n")

                env = brev_env.BrevEnvironment()
                env._instance_name = "vss-eval-test"
                await env.upload_dir(src_dir, "/skills")
        finally:
            brev_env._run_brev_exec = original_exec
            brev_env._run_brev_copy = original_copy
            brev_env.BREV_UPLOAD_BACKOFF_SEC = original_backoff

        self.assertEqual(len(copy_calls), 2)
        commands = [call[1] for call in exec_calls]
        self.assertEqual(sum("mkdir -p /tmp/skill-eval/uploads/" in command for command in commands), 2)
        self.assertEqual(sum("tar -xzf" in command for command in commands), 1)


class VersionCompareSanity(unittest.TestCase):
    """Extra coverage for _version_lt beyond the generate.py tests."""

    def test_driver_version_ordering(self):
        self.assertTrue(brev_env._version_lt("570.195.03", "580.95"))
        self.assertTrue(brev_env._version_lt("565.57.01", "580.95"))
        self.assertFalse(brev_env._version_lt("580.105.08", "580.95"))
        self.assertFalse(brev_env._version_lt("580.95", "580.95"))


class ClaudeTaskScratchCleanup(unittest.TestCase):
    def test_cleanup_command_targets_current_user_task_dirs(self):
        cmd = brev_env._claude_task_scratch_cleanup_command()

        self.assertIn('BASE="/tmp/claude-${UID_NUM}"', cmd)
        self.assertIn("-name tasks", cmd)
        self.assertIn("-exec rm -rf {} +", cmd)
        self.assertIn("[claude-task-scratch]", cmd)
        self.assertNotIn("sudo rm -rf /tmp/claude-", cmd)
        # The rm step must not swallow stderr — a real cleanup failure has to
        # surface its error to the caller, not raise an empty-tail RuntimeError.
        self.assertNotIn("rm -rf {} + 2>/dev/null", cmd)


if __name__ == "__main__":
    unittest.main(verbosity=2)
