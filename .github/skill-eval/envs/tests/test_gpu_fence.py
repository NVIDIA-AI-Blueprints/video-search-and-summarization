# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Harbor/Brev integration tests for GPU-worker generation fencing."""

from __future__ import annotations

import os
import sys
import tempfile
import types
import unittest
import uuid
from pathlib import Path
from unittest import mock

_base = types.ModuleType("harbor.environments.base")


class _BaseEnvironment:
    def __init__(self, *args, **kwargs):
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
from envs import brev_env


class GpuFenceHarborIntegration(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.token = str(uuid.uuid4())
        self.fence_env = {
            "GPU_WORKER_FENCE_REQUIRED": "1",
            "GPU_LEASE_GPU_ID": "vss-eval-test",
            "GPU_LEASE_TOKEN": self.token,
            "GPU_LEASE_GENERATION": "7",
        }

    async def test_claim_precedes_every_mutating_start_command(self):
        commands = []

        async def fake_exec(_instance, command, timeout=brev_env.BREV_EXEC_TIMEOUT):
            commands.append((command, timeout))
            if command == "echo harbor-ready":
                return brev_env.ExecResult(
                    stdout="harbor-ready",
                    stderr="",
                    return_code=0,
                )
            if "vss-gpu-fence claim" in command:
                return brev_env.ExecResult(
                    stdout="VSS_GPU_FENCE_SESSION=session_abcdefghijklmnop\n",
                    stderr="",
                    return_code=0,
                )
            return brev_env.ExecResult(stdout="", stderr="", return_code=0)

        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(
                os.environ,
                self.fence_env,
                clear=False,
            ),
        ):
            env_dir = Path(tmp) / "step-1" / "environment"
            env_dir.mkdir(parents=True)
            environment = brev_env.BrevEnvironment()
            environment.environment_dir = env_dir
            with (
                mock.patch.object(
                    environment,
                    "_resolve_instance_name",
                    return_value="vss-eval-test",
                ),
                mock.patch.object(
                    brev_env,
                    "_find_brev_instance",
                    new=mock.AsyncMock(return_value={"name": "vss-eval-test"}),
                ),
                mock.patch.object(
                    brev_env,
                    "_check_instance_matches",
                    new=mock.AsyncMock(return_value=None),
                ),
                mock.patch.object(
                    brev_env,
                    "_check_live_resources",
                    new=mock.AsyncMock(return_value=None),
                ),
                mock.patch.object(
                    brev_env,
                    "_run_brev_exec",
                    new=mock.AsyncMock(side_effect=fake_exec),
                ),
                mock.patch.object(
                    brev_env,
                    "_run_brev_copy",
                    new=mock.AsyncMock(
                        return_value=brev_env.ExecResult(
                            stdout="",
                            stderr="",
                            return_code=0,
                        )
                    ),
                ),
            ):
                await environment.start(force_build=False)

        raw_commands = [command for command, _timeout in commands]
        claim_index = next(
            index
            for index, command in enumerate(raw_commands)
            if "vss-gpu-fence claim" in command
        )
        self.assertEqual(raw_commands[:claim_index], ["echo harbor-ready"])
        self.assertTrue(
            all(
                "vss-gpu-fence exec --session-id" in command
                for command in raw_commands[claim_index + 1 :]
            ),
            raw_commands,
        )
        self.assertNotIn(
            self.token,
            "\n".join(raw_commands),
        )

    async def test_fenced_exec_uses_only_local_session_id(self):
        with mock.patch.dict(os.environ, self.fence_env, clear=False):
            environment = brev_env.BrevEnvironment()
        environment._instance_name = "vss-eval-test"
        environment._fence_session_id = "session_abcdefghijklmnop"

        raw = mock.AsyncMock(
            return_value=brev_env.ExecResult(stdout="ok", return_code=0)
        )
        with mock.patch.object(brev_env, "_run_brev_exec", new=raw):
            await environment._run_remote("echo mutation", timeout=12)

        command = raw.await_args.args[1]
        self.assertIn(
            "vss-gpu-fence exec --session-id session_abcdefghijklmnop",
            command,
        )
        self.assertIn("echo mutation", command)
        self.assertNotIn(self.token, command)

    async def test_postgres_mode_rejects_missing_fence_metadata(self):
        with mock.patch.dict(
            os.environ,
            {"GPU_WORKER_FENCE_REQUIRED": "1"},
            clear=True,
        ):
            environment = brev_env.BrevEnvironment()
        environment._instance_name = "vss-eval-test"

        with self.assertRaisesRegex(RuntimeError, "missing GPU worker fencing"):
            await environment._claim_gpu_fence()

    def test_debug_log_redacts_claim_token(self):
        command = (
            "sudo vss-gpu-fence claim --gpu-id gpu-a "
            f"--token {self.token} --generation 7"
        )
        redacted = brev_env._redact_remote_command(command)
        self.assertNotIn(self.token, redacted)
        self.assertIn("--token [REDACTED]", redacted)


if __name__ == "__main__":
    unittest.main(verbosity=2)
