#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import ast
import base64
import builtins
import contextlib
import importlib.util
import io
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[4]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


notebook_adapter = load_module(
    "notebook_setup_adapter",
    REPO_ROOT / ".github" / "skill-eval" / "nemoclaw" / "notebook_setup_adapter.py",
)
deploy_adapter = load_module(
    "vss_deploy_profile_generate",
    REPO_ROOT / ".github" / "skill-eval" / "adapters" / "vss-deploy-profile" / "generate.py",
)
ask_adapter = load_module(
    "vss_ask_video_generate",
    REPO_ROOT / ".github" / "skill-eval" / "adapters" / "vss-ask-video" / "generate.py",
)
orchestrator_mcp_helper = load_module(
    "orchestrator_mcp_helper",
    REPO_ROOT / "deploy" / "docker" / "scripts" / "orchestrator_mcp_helper.py",
)
headless_runner = load_module(
    "nemoclaw_headless_runner",
    REPO_ROOT / ".github" / "skill-eval" / "nemoclaw" / "headless_runner.py",
)
readiness = load_module(
    "nemoclaw_readiness",
    REPO_ROOT / ".github" / "skill-eval" / "nemoclaw" / "readiness.py",
)
setup_failure = load_module(
    "nemoclaw_setup_failure",
    REPO_ROOT / ".github" / "skill-eval" / "nemoclaw" / "setup_failure.py",
)
direct_container_preflight = load_module(
    "nemoclaw_direct_container_preflight",
    REPO_ROOT
    / ".github"
    / "skill-eval"
    / "nemoclaw"
    / "direct_container_preflight.py",
)
gateway_release = load_module(
    "nemoclaw_gateway_release",
    REPO_ROOT
    / ".github"
    / "skill-eval"
    / "nemoclaw"
    / "release_gateway_port.py",
)
nemoclaw_deploy_profile_verifier = load_module(
    "nemoclaw_deploy_profile_verifier",
    REPO_ROOT / ".github" / "skill-eval" / "verifiers" / "nemoclaw_deploy_profile.py",
)
smoke_runner = load_module(
    "nemoclaw_smoke_runner",
    REPO_ROOT / ".github" / "skill-eval" / "nemoclaw" / "smoke_runner.py",
)
skills_eval_agent = load_module(
    "skills_eval_agent",
    REPO_ROOT / ".github" / "skill-eval" / "skills_eval_agent.py",
)


class SetupFailureDiagnosticTest(unittest.TestCase):
    def test_classification_emits_only_fixed_categories(self):
        raw_secret = "sk-secret-value"
        diagnostic = setup_failure.classify_setup_failure(
            "\n".join(
                (
                    f"Could not authenticate using {raw_secret}",
                    "Cannot safely migrate legacy NemoClaw state for this "
                    "gateway port: onboard-session.json conflicts with its "
                    "sandbox registry row",
                    "Sandbox 'demo' was created but did not become ready within 180s.",
                    "reason=ContainerRestarting Container is restarting after a failure",
                )
            ),
            1,
        )

        encoded = json.dumps(diagnostic, sort_keys=True)
        self.assertNotIn(raw_secret, encoded)
        self.assertEqual(
            diagnostic["categories"],
            [
                "legacy_state_conflict",
                "sandbox_container_restarting",
                "sandbox_not_ready",
            ],
        )
        self.assertEqual(
            setup_failure.format_setup_failure(diagnostic),
            "legacy_state_conflict,sandbox_container_restarting,sandbox_not_ready",
        )

    def test_cli_writes_owner_only_safe_json(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            raw_log = root / "setup.log"
            output = root / "setup-failure.json"
            raw_log.write_text(
                "Could not authenticate using sk-secret-value\n"
                "Cannot install trusted lsof for scoped gateway recovery\n",
                encoding="utf-8",
            )

            rc = setup_failure.main(
                [
                    "--input",
                    str(raw_log),
                    "--output",
                    str(output),
                    "--return-code",
                    "1",
                ]
            )

            self.assertEqual(rc, 0)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            encoded = output.read_text(encoding="utf-8")
            self.assertNotIn("sk-secret-value", encoded)
            self.assertEqual(
                json.loads(encoded)["categories"],
                ["trusted_lsof_unavailable"],
            )

    def test_direct_container_refusal_has_a_fixed_safe_category(self):
        raw_secret = "must-never-reach-an-artifact"
        diagnostic = setup_failure.classify_setup_failure(
            f"{raw_secret}\n"
            "NemoClaw direct-container preflight refused: "
            "container_image_invalid\n",
            1,
        )

        self.assertEqual(
            diagnostic["categories"],
            ["direct_container_preflight_refused"],
        )
        self.assertNotIn(raw_secret, json.dumps(diagnostic))


class DirectContainerPreflightTest(unittest.TestCase):
    SANDBOX = "vss-eval-u0-p19080"
    CONTAINER_ID = "a" * 64
    IMAGE_ID = "sha256:" + ("b" * 64)
    IMAGE_REF = f"nemoclaw-sandbox-local:{SANDBOX}-1785357013"

    def _runner(
        self,
        *,
        image_ref: str | None = None,
        candidate_outputs: tuple[str, ...] | None = None,
        local_image_ids: tuple[str, ...] | None = None,
        container_image_id: str | None = None,
        running: str = "true",
        paused: str = "false",
        paused_states: tuple[str, ...] | None = None,
        start_returncode: int = 0,
        probe_returncode: int = 0,
        probe_stdout: str | None = None,
        raise_command: str | None = None,
        raised_exception: BaseException | None = None,
    ):
        image_ref = image_ref or self.IMAGE_REF
        candidate_outputs = candidate_outputs or (
            f"{self.CONTAINER_ID}\topenshell-{self.SANDBOX}-runtime\n",
        )
        local_image_ids = local_image_ids or (self.IMAGE_ID,)
        container_image_id = container_image_id or self.IMAGE_ID
        paused_states = paused_states or (paused,)
        probe_stdout = (
            probe_stdout
            if probe_stdout is not None
            else f"{direct_container_preflight.LIFECYCLE_PROBE_SENTINEL}\n"
        )
        calls: list[list[str]] = []
        candidate_call = 0
        image_call = 0
        inspect_call = 0

        def run(argv, **kwargs):
            nonlocal candidate_call, image_call, inspect_call
            argv = list(argv)
            calls.append(argv)
            self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
            self.assertTrue(kwargs["capture_output"])
            self.assertTrue(kwargs["text"])
            self.assertFalse(kwargs["check"])

            if argv[:2] == ["docker", "ps"]:
                self.assertEqual(
                    kwargs["timeout"],
                    direct_container_preflight.DOCKER_COMMAND_TIMEOUT_SECONDS,
                )
                output = candidate_outputs[
                    min(candidate_call, len(candidate_outputs) - 1)
                ]
                candidate_call += 1
                return subprocess.CompletedProcess(argv, 0, output, "")
            if argv[:2] == ["docker", "inspect"]:
                self.assertEqual(
                    kwargs["timeout"],
                    direct_container_preflight.DOCKER_COMMAND_TIMEOUT_SECONDS,
                )
                inspected_paused = paused_states[
                    min(inspect_call, len(paused_states) - 1)
                ]
                inspect_call += 1
                return subprocess.CompletedProcess(
                    argv,
                    0,
                    (
                        f"{image_ref}\t{container_image_id}\t"
                        f"{running}\t{inspected_paused}\n"
                    ),
                    "",
                )
            if argv[:3] == ["docker", "image", "inspect"]:
                self.assertEqual(
                    kwargs["timeout"],
                    direct_container_preflight.DOCKER_COMMAND_TIMEOUT_SECONDS,
                )
                output = local_image_ids[
                    min(image_call, len(local_image_ids) - 1)
                ]
                image_call += 1
                return subprocess.CompletedProcess(
                    argv,
                    0,
                    f"{output}\n",
                    "",
                )
            if argv[:3] == ["nemoclaw", "sandbox", "start"]:
                self.assertEqual(
                    kwargs["timeout"],
                    direct_container_preflight.SANDBOX_START_TIMEOUT_SECONDS,
                )
                if raise_command == "start":
                    raise raised_exception or OSError("secret start failure")
                return subprocess.CompletedProcess(
                    argv,
                    start_returncode,
                    "",
                    "secret start failure",
                )
            if argv[:2] == ["docker", "exec"]:
                self.assertEqual(
                    kwargs["timeout"],
                    direct_container_preflight.CONTAINER_PROBE_TIMEOUT_SECONDS,
                )
                if raise_command == "probe":
                    raise raised_exception or OSError("secret probe failure")
                return subprocess.CompletedProcess(
                    argv,
                    probe_returncode,
                    probe_stdout,
                    "secret probe failure",
                )
            self.fail(f"unexpected preflight command: {argv}")

        return run, calls

    def test_uses_supported_activation_then_exact_container_probe(self):
        runner, calls = self._runner()
        direct_container_preflight.verify_direct_container(
            self.SANDBOX,
            run=runner,
        )

        expected_ps = [
            "docker",
            "ps",
            "--no-trunc",
            "--filter",
            "label=openshell.ai/managed-by=openshell",
            "--filter",
            f"label=openshell.ai/sandbox-name={self.SANDBOX}",
            "--format",
            "{{.ID}}\t{{.Names}}",
        ]
        expected_inspect = [
            "docker",
            "inspect",
            "--format",
            (
                "{{.Config.Image}}\t{{.Image}}\t"
                "{{.State.Running}}\t{{.State.Paused}}"
            ),
            self.CONTAINER_ID,
        ]
        expected_image_inspect = [
            "docker",
            "image",
            "inspect",
            "--format",
            "{{.Id}}",
            self.IMAGE_REF,
        ]
        expected_start = [
            "nemoclaw",
            "sandbox",
            "start",
            self.SANDBOX,
        ]
        expected_probe = [
            "docker",
            "exec",
            "--user",
            "root",
            self.CONTAINER_ID,
            *direct_container_preflight.LIFECYCLE_PROBE_ARGV,
        ]
        self.assertEqual(
            calls,
            [
                expected_ps,
                expected_inspect,
                expected_image_inspect,
                expected_start,
                expected_ps,
                expected_inspect,
                expected_image_inspect,
                expected_probe,
                expected_ps,
                expected_inspect,
                expected_image_inspect,
            ],
        )
        probe = direct_container_preflight.LIFECYCLE_PROBE_SOURCE
        self.assertIn(
            direct_container_preflight.LIFECYCLE_PROBE_SENTINEL,
            probe,
        )
        for forbidden_check in (
            "normalize_mutable_config_perms",
            "openclaw.json",
            "sandbox_uid",
            "sandbox_gid",
        ):
            self.assertNotIn(forbidden_check, probe)
        self.assertEqual(
            tuple(calls[7][-4:]),
            direct_container_preflight.LIFECYCLE_PROBE_ARGV,
        )
        self.assertEqual(calls[7][4], self.CONTAINER_ID)

    def test_lifecycle_shell_probe_is_fixed_and_read_only(self):
        accepted = subprocess.run(
            list(direct_container_preflight.LIFECYCLE_PROBE_ARGV),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertEqual(
            accepted.stdout.strip(),
            direct_container_preflight.LIFECYCLE_PROBE_SENTINEL,
        )

    def test_timeout_budget_covers_supported_start_and_all_attestations(self):
        docker_attestation_calls = 9
        supported_start_budget = 300
        self.assertGreaterEqual(
            direct_container_preflight.SANDBOX_START_TIMEOUT_SECONDS,
            supported_start_budget
            + (2 * direct_container_preflight.DOCKER_COMMAND_TIMEOUT_SECONDS),
        )
        self.assertGreater(
            notebook_adapter.DIRECT_CONTAINER_PREFLIGHT_TIMEOUT_SECONDS,
            direct_container_preflight.SANDBOX_START_TIMEOUT_SECONDS
            + direct_container_preflight.CONTAINER_PROBE_TIMEOUT_SECONDS
            + (
                docker_attestation_calls
                * direct_container_preflight.DOCKER_COMMAND_TIMEOUT_SECONDS
            ),
        )

    def test_allows_paused_running_container(self):
        runner, _calls = self._runner(
            paused_states=("true", "false", "false"),
        )
        direct_container_preflight.verify_direct_container(
            self.SANDBOX,
            run=runner,
        )

    def test_refuses_container_that_start_did_not_unpause(self):
        runner, calls = self._runner(paused="true")
        with self.assertRaisesRegex(
            direct_container_preflight.DirectContainerPreflightError,
            "container_not_activated",
        ):
            direct_container_preflight.verify_direct_container(
                self.SANDBOX,
                run=runner,
            )
        self.assertFalse(any(argv[:2] == ["docker", "exec"] for argv in calls))

    def test_tag_mismatch_refuses_before_lifecycle_calls(self):
        runner, calls = self._runner(
            local_image_ids=("sha256:" + ("c" * 64),),
        )
        with self.assertRaisesRegex(
            direct_container_preflight.DirectContainerPreflightError,
            "container_image_id_mismatch",
        ):
            direct_container_preflight.verify_direct_container(
                self.SANDBOX,
                run=runner,
            )
        self.assertFalse(any(argv[0] == "nemoclaw" for argv in calls))

    def test_rejects_non_nemoclaw_image_before_lifecycle_calls(self):
        runner, calls = self._runner(image_ref="openshell/sandbox:legacy")
        with self.assertRaisesRegex(
            direct_container_preflight.DirectContainerPreflightError,
            "container_image_invalid",
        ):
            direct_container_preflight.verify_direct_container(
                self.SANDBOX,
                run=runner,
            )
        self.assertFalse(any(argv[0] == "nemoclaw" for argv in calls))

    def test_rejects_malformed_metadata_names_and_image_ids(self):
        valid_row = (
            f"{self.CONTAINER_ID}\topenshell-{self.SANDBOX}-runtime\n"
        )
        cases = (
            (
                self._runner(
                    candidate_outputs=(
                        f"short-id\topenshell-{self.SANDBOX}\n",
                    )
                )[0],
                "container_metadata_invalid",
            ),
            (
                self._runner(
                    candidate_outputs=(
                        f"{self.CONTAINER_ID}\tunrelated-runtime\n",
                    )
                )[0],
                "container_name_invalid",
            ),
            (
                self._runner(
                    candidate_outputs=(valid_row,),
                    local_image_ids=("not-an-image-id",),
                )[0],
                "container_image_resolution_invalid",
            ),
        )
        for runner, reason in cases:
            with (
                self.subTest(reason=reason),
                self.assertRaisesRegex(
                    direct_container_preflight.DirectContainerPreflightError,
                    reason,
                ),
            ):
                direct_container_preflight.verify_direct_container(
                    self.SANDBOX,
                    run=runner,
                )

    def test_rejects_invalid_sandbox_name_without_running_commands(self):
        with mock.patch.object(
            direct_container_preflight.subprocess,
            "run",
        ) as run:
            with self.assertRaisesRegex(
                direct_container_preflight.DirectContainerPreflightError,
                "sandbox_name_invalid",
            ):
                direct_container_preflight.verify_direct_container("../bad")
        run.assert_not_called()

    def test_start_and_probe_failures_are_fixed_and_secret_safe(self):
        raw_secret = "must-never-reach-the-refusal"
        cases = (
            ("start", 1, None, "sandbox_start_failed"),
            ("start", 0, OSError(raw_secret), "sandbox_start_failed"),
            ("probe", 1, None, "container_probe_failed"),
            (
                "probe",
                0,
                subprocess.TimeoutExpired(["docker"], 30, raw_secret),
                "container_probe_failed",
            ),
        )
        for command, returncode, raised, reason in cases:
            with self.subTest(command=command, raised=raised is not None):
                runner, calls = self._runner(
                    start_returncode=returncode if command == "start" else 0,
                    probe_returncode=returncode if command == "probe" else 0,
                    raise_command=command if raised is not None else None,
                    raised_exception=raised,
                )
                with self.assertRaises(
                    direct_container_preflight.DirectContainerPreflightError
                ) as caught:
                    direct_container_preflight.verify_direct_container(
                        self.SANDBOX,
                        run=runner,
                    )
                self.assertEqual(caught.exception.code, reason)
                self.assertNotIn(raw_secret, str(caught.exception))
                self.assertIsNone(caught.exception.__cause__)
                if command == "start":
                    self.assertFalse(
                        any(argv[:2] == ["docker", "exec"] for argv in calls)
                    )

    def test_success_without_fixed_sentinel_is_refused(self):
        raw_secret = "arbitrary-upstream-output"
        runner, _calls = self._runner(probe_stdout=raw_secret)
        with self.assertRaises(
            direct_container_preflight.DirectContainerPreflightError
        ) as caught:
            direct_container_preflight.verify_direct_container(
                self.SANDBOX,
                run=runner,
            )
        self.assertEqual(
            caught.exception.code,
            "container_probe_sentinel_missing",
        )
        self.assertNotIn(raw_secret, str(caught.exception))

    def test_rejects_candidate_or_tag_change_after_probe(self):
        initial = (
            f"{self.CONTAINER_ID}\topenshell-{self.SANDBOX}-runtime\n"
        )
        replacement = (
            f"{'c' * 64}\topenshell-{self.SANDBOX}-replacement\n"
        )
        changed_image = "sha256:" + ("c" * 64)
        cases = (
            (
                self._runner(
                    candidate_outputs=(initial, initial, replacement)
                )[0],
                "container_identity_changed",
            ),
            (
                self._runner(
                    local_image_ids=(
                        self.IMAGE_ID,
                        self.IMAGE_ID,
                        changed_image,
                    )
                )[0],
                "container_image_id_mismatch",
            ),
        )
        for runner, reason in cases:
            with (
                self.subTest(reason=reason),
                self.assertRaisesRegex(
                    direct_container_preflight.DirectContainerPreflightError,
                    reason,
                ),
            ):
                direct_container_preflight.verify_direct_container(
                    self.SANDBOX,
                    run=runner,
                )

    def test_rejects_start_time_identity_change_before_probe(self):
        initial = (
            f"{self.CONTAINER_ID}\topenshell-{self.SANDBOX}-runtime\n"
        )
        replacement = (
            f"{'c' * 64}\topenshell-{self.SANDBOX}-replacement\n"
        )
        runner, calls = self._runner(
            candidate_outputs=(initial, replacement),
        )
        with self.assertRaisesRegex(
            direct_container_preflight.DirectContainerPreflightError,
            "container_identity_changed",
        ):
            direct_container_preflight.verify_direct_container(
                self.SANDBOX,
                run=runner,
            )
        self.assertFalse(
            any(argv[:2] == ["docker", "exec"] for argv in calls)
        )

    def test_rejects_ambiguous_running_candidate_and_invalid_state(self):
        ambiguous = (
            f"{self.CONTAINER_ID}\topenshell-{self.SANDBOX}-runtime\n"
            f"{'c' * 64}\topenshell-{self.SANDBOX}-other\n"
        )
        cases = (
            (
                self._runner(candidate_outputs=(ambiguous,))[0],
                "container_count_invalid",
            ),
            (self._runner(running="false")[0], "container_identity_invalid"),
            (self._runner(paused="unknown")[0], "container_identity_invalid"),
        )
        for runner, reason in cases:
            with (
                self.subTest(reason=reason),
                self.assertRaisesRegex(
                    direct_container_preflight.DirectContainerPreflightError,
                    reason,
                ),
            ):
                direct_container_preflight.verify_direct_container(
                    self.SANDBOX,
                    run=runner,
                )


class GatewayReleaseTest(unittest.TestCase):
    HELPER = (
        REPO_ROOT
        / ".github"
        / "skill-eval"
        / "nemoclaw"
        / "release_gateway_port.py"
    )
    LISTENER_SOURCE = (
        "import socket,sys,time;"
        "s=socket.socket();"
        "s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);"
        "s.bind(('127.0.0.1',int(sys.argv[1])));"
        "s.listen();"
        "time.sleep(30)"
    )

    @staticmethod
    def _unused_port() -> int:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    def _wait_until_listening(self, port: int) -> None:
        for _ in range(100):
            with socket.socket() as sock:
                if sock.connect_ex(("127.0.0.1", port)) == 0:
                    return
            time.sleep(0.02)
        self.fail(f"test listener did not bind port {port}")

    def _run_helper(self, port: int) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/usr/bin/python3", str(self.HELPER), "--port", str(port)],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_default_port_owned_gateway_argv0_is_canonical_and_port_bound(self):
        identity = gateway_release.ProcessIdentity(
            pid=123,
            start_time=456,
            argv=("openshell-gateway[nemoclaw=nemoclaw;port=8080]",),
            executable="/usr/local/bin/openshell-gateway",
        )

        self.assertTrue(gateway_release._is_managed_gateway(identity, 8080))
        self.assertFalse(gateway_release._is_managed_gateway(identity, 19080))

    def test_incomplete_package_fallback_releases_exact_gateway_argv(self):
        with tempfile.TemporaryDirectory() as td:
            gateway_executable = Path(td) / "openshell-gateway"
            shutil.copy2(sys.executable, gateway_executable)
            port = self._unused_port()
            listener = subprocess.Popen(
                [
                    str(gateway_executable),
                    "-c",
                    self.LISTENER_SOURCE,
                    str(port),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                self._wait_until_listening(port)
                stopped = self._run_helper(port)

                self.assertEqual(stopped.returncode, 0, stopped.stderr)
                self.assertIn(
                    f"stopped host process {listener.pid}",
                    stopped.stdout,
                )
                listener.wait(timeout=5)
            finally:
                if listener.poll() is None:
                    listener.terminate()
                    listener.wait(timeout=5)

    def test_fallback_releases_exact_owned_gateway_argv0(self):
        with tempfile.TemporaryDirectory() as td:
            gateway_executable = Path(td) / "openshell-gateway"
            shutil.copy2(sys.executable, gateway_executable)
            port = self._unused_port()
            listener = subprocess.Popen(
                [
                    f"openshell-gateway[nemoclaw=nemoclaw-{port};port={port}]",
                    "-c",
                    self.LISTENER_SOURCE,
                    str(port),
                ],
                executable=gateway_executable,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                self._wait_until_listening(port)
                stopped = self._run_helper(port)

                self.assertEqual(stopped.returncode, 0, stopped.stderr)
                self.assertIn(
                    f"stopped host process {listener.pid}",
                    stopped.stdout,
                )
                listener.wait(timeout=5)
            finally:
                if listener.poll() is None:
                    listener.terminate()
                    listener.wait(timeout=5)

    def test_owned_gateway_argv0_for_another_port_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            gateway_executable = Path(td) / "openshell-gateway"
            shutil.copy2(sys.executable, gateway_executable)
            port = self._unused_port()
            wrong_port = self._unused_port()
            listener = subprocess.Popen(
                [
                    (
                        "openshell-gateway["
                        f"nemoclaw=nemoclaw-{wrong_port};port={wrong_port}]"
                    ),
                    "-c",
                    self.LISTENER_SOURCE,
                    str(port),
                ],
                executable=gateway_executable,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                self._wait_until_listening(port)
                rejected = self._run_helper(port)

                self.assertNotEqual(rejected.returncode, 0)
                self.assertIn(
                    "refusing to signal non-gateway listener",
                    rejected.stderr,
                )
                self.assertIsNone(listener.poll())
            finally:
                listener.terminate()
                listener.wait(timeout=5)

    def test_spoofed_gateway_argv_fails_closed_and_remains_alive(self):
        port = self._unused_port()
        listener = subprocess.Popen(
            ["openshell-gateway", "-c", self.LISTENER_SOURCE, str(port)],
            executable=sys.executable,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            self._wait_until_listening(port)
            rejected = self._run_helper(port)

            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn(
                "refusing to signal non-gateway listener",
                rejected.stderr,
            )
            self.assertIsNone(listener.poll())
            with socket.socket() as sock:
                self.assertEqual(sock.connect_ex(("127.0.0.1", port)), 0)
        finally:
            listener.terminate()
            listener.wait(timeout=5)

    def test_nonmatching_listener_fails_closed_and_remains_alive(self):
        port = self._unused_port()
        listener = subprocess.Popen(
            [sys.executable, "-c", self.LISTENER_SOURCE, str(port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            self._wait_until_listening(port)
            rejected = self._run_helper(port)

            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn(
                "refusing to signal non-gateway listener",
                rejected.stderr,
            )
            self.assertIsNone(listener.poll())
            with socket.socket() as sock:
                self.assertEqual(sock.connect_ex(("127.0.0.1", port)), 0)
        finally:
            listener.terminate()
            listener.wait(timeout=5)


class NotebookSetupAdapterTest(unittest.TestCase):
    def test_ci_confirms_only_the_managed_sandbox_it_recreates(self):
        with mock.patch.dict(
            os.environ,
            {
                "NEMOCLAW_SANDBOX_NAME": "demo",
                "NEMOCLAW_RECREATE_SANDBOX": "1",
            },
            clear=True,
        ):
            notebook_adapter._prepare_ci_nemoclaw_environment()

            self.assertEqual(
                os.environ["NEMOCLAW_CONFIRM_LEGACY_MANAGED_RECREATE"],
                '["demo"]',
            )

    def test_ci_does_not_confirm_legacy_migration_without_recreation(self):
        with mock.patch.dict(
            os.environ,
            {
                "NEMOCLAW_SANDBOX_NAME": "demo",
                "NEMOCLAW_RECREATE_SANDBOX": "0",
            },
            clear=True,
        ):
            notebook_adapter._prepare_ci_nemoclaw_environment()

            self.assertNotIn(
                "NEMOCLAW_CONFIRM_LEGACY_MANAGED_RECREATE",
                os.environ,
            )

    def test_ci_preserves_explicit_legacy_migration_confirmation(self):
        with mock.patch.dict(
            os.environ,
            {
                "NEMOCLAW_SANDBOX_NAME": "demo",
                "NEMOCLAW_RECREATE_SANDBOX": "1",
                "NEMOCLAW_CONFIRM_LEGACY_MANAGED_RECREATE": '["demo","other"]',
            },
            clear=True,
        ):
            notebook_adapter._prepare_ci_nemoclaw_environment()

            self.assertEqual(
                os.environ["NEMOCLAW_CONFIRM_LEGACY_MANAGED_RECREATE"],
                '["demo","other"]',
            )

    def test_sidecar_manifest_matches_current_notebook_cells(self):
        manifest_path = REPO_ROOT / ".github" / "skill-eval" / "nemoclaw" / "notebook_cells.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        sources = [
            json.loads((REPO_ROOT / item["notebook"]).read_text(encoding="utf-8"))
            for item in manifest["notebooks"]
        ]

        built = notebook_adapter.build_notebooks(sources, manifest)

        ids = [cell.get("id") for cell in built["cells"]]
        self.assertEqual(ids.count("ci-parameters-1"), 1)
        self.assertEqual(ids.count("ci-parameters-2"), 1)
        self.assertIn("ci-persist-env", ids)
        self.assertEqual(ids.count("ci-direct-container-preflight"), 1)
        self.assertNotIn("s37-ui-code", ids)
        self.assertNotIn("verify-code", ids)
        self.assertLess(ids.index("ci-parameters-1"), ids.index("e67f6da4"))
        onboard_cell = next(cell for cell in built["cells"] if cell.get("id") == "s31-code")
        self.assertIn(
            "nemoclaw onboard --fresh --non-interactive --agent {AGENT_RUNTIME}",
            onboard_cell["source"],
        )
        self.assertNotIn(
            "nemoclaw onboard --non-interactive --agent {AGENT_RUNTIME}",
            onboard_cell["source"],
        )
        self.assertIn(
            'onboard_cmd = "nemohermes onboard --non-interactive"',
            onboard_cell["source"],
        )
        primary_onboard_assignment = next(
            line
            for line in onboard_cell["source"].splitlines()
            if line.startswith("onboard_cmd = ")
        )
        self.assertNotIn(
            "nemohermes onboard --fresh --non-interactive",
            primary_onboard_assignment,
        )
        self.assertIn(
            "NEMOCLAW_SANDBOX_BASE_IMAGE_REFRESH=1 "
            "nemohermes onboard --fresh --non-interactive",
            onboard_cell["source"],
        )
        self.assertLess(
            ids.index("s31-code"),
            ids.index("ci-direct-container-preflight"),
        )
        self.assertLess(
            ids.index("ci-direct-container-preflight"),
            ids.index("s35-code"),
        )
        preflight_cell = next(
            cell
            for cell in built["cells"]
            if cell.get("id") == "ci-direct-container-preflight"
        )
        self.assertIn(
            ".github/skill-eval/nemoclaw/direct_container_preflight.py",
            preflight_cell["source"],
        )
        self.assertIn("NEMOCLAW_SANDBOX_NAME", preflight_cell["source"])
        self.assertIn(
            "timeout="
            f"{notebook_adapter.DIRECT_CONTAINER_PREFLIGHT_TIMEOUT_SECONDS}",
            preflight_cell["source"],
        )

    def test_build_notebook_injects_parameters_before_derived_cell(self):
        source = {
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {},
            "cells": [
                {"id": "settings", "cell_type": "code", "metadata": {}, "source": ["A=1\n"], "outputs": []},
                {"id": "derived", "cell_type": "code", "metadata": {}, "source": ["B=A\n"], "outputs": []},
            ],
        }
        manifest = {"cells": ["settings", "derived"], "insert_parameters_before": "derived"}

        built = notebook_adapter.build_notebook(source, manifest)
        ids = [cell.get("id") for cell in built["cells"]]

        self.assertEqual(ids, ["settings", "ci-parameters", "derived", "ci-persist-env"])
        self.assertTrue(all(isinstance(cell["source"], str) for cell in built["cells"]))
        self.assertEqual(built["cells"][0]["source"], "A=1\n")

    def test_ci_notebook_makes_optional_9090_forward_best_effort(self):
        source = {
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {},
            "cells": [
                {
                    "id": "run-code",
                    "cell_type": "code",
                    "metadata": {},
                    "source": [
                        "print('setup')\n",
                        "ensure_openshell_forward(9090, NEMOCLAW_SANDBOX_NAME)\n",
                    ],
                    "outputs": [],
                }
            ],
        }
        manifest = {"cells": ["run-code"], "insert_parameters_before": "run-code"}

        built = notebook_adapter.build_notebook(source, manifest)
        run_cell = next(cell for cell in built["cells"] if cell.get("id") == "run-code")

        self.assertIn("try:", run_cell["source"])
        self.assertIn("optional OpenShell forward 9090 skipped in CI", run_cell["source"])
        self.assertIn("ensure_openshell_forward(9090, NEMOCLAW_SANDBOX_NAME)", run_cell["source"])

    def test_ci_notebook_makes_docker_login_best_effort(self):
        source = {
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {},
            "cells": [
                {
                    "id": "4c91fd59",
                    "cell_type": "code",
                    "metadata": {},
                    "source": [
                        'if login_result.returncode != 0:\n',
                        '    raise RuntimeError(f"Docker login to nvcr.io failed\\n{login_result.stderr}")\n',
                        '\n',
                        'print("Docker login to nvcr.io: OK")\n',
                    ],
                    "outputs": [],
                }
            ],
        }
        manifest = {"cells": ["4c91fd59"], "insert_parameters_before": "4c91fd59"}

        built = notebook_adapter.build_notebook(source, manifest)
        login_cell = next(cell for cell in built["cells"] if cell.get("id") == "4c91fd59")

        self.assertIn("WARNING: Docker login to nvcr.io failed; continuing in CI", login_cell["source"])
        self.assertNotIn("raise RuntimeError", login_cell["source"])
        self.assertIn("else:", login_cell["source"])

    def test_redacts_configured_secret_values(self):
        os.environ["NVIDIA_API_KEY"] = "nvapi-secret"
        try:
            redacted = notebook_adapter._redact(
                {"outputs": [{"text": "token=nvapi-secret"}]},
                notebook_adapter._redaction_values(),
            )
        finally:
            os.environ.pop("NVIDIA_API_KEY", None)

        self.assertEqual(redacted["outputs"][0]["text"], "token=<redacted:NVIDIA_API_KEY>")

    def test_redacts_anthropic_api_key_from_notebook_outputs(self):
        os.environ["ANTHROPIC_API_KEY"] = "anthropic-secret"
        try:
            redacted = notebook_adapter._redact(
                {"outputs": [{"text": "token=anthropic-secret"}]},
                notebook_adapter._redaction_values(),
            )
        finally:
            os.environ.pop("ANTHROPIC_API_KEY", None)

        self.assertEqual(redacted["outputs"][0]["text"], "token=<redacted:ANTHROPIC_API_KEY>")

    def test_redacts_managed_mcp_credential_from_notebook_outputs(self):
        credential_env = "VSS_ORCHESTRATOR_MCP_TOKEN"
        with mock.patch.dict(
            os.environ,
            {
                "VSS_ORCHESTRATOR_MCP_CREDENTIAL_ENV": credential_env,
                credential_env: "managed-mcp-secret",
            },
            clear=True,
        ):
            redacted = notebook_adapter._redact(
                {"outputs": [{"text": "token=managed-mcp-secret"}]},
                notebook_adapter._redaction_values(),
            )

        self.assertEqual(
            redacted["outputs"][0]["text"],
            f"token=<redacted:{credential_env}>",
        )

    def test_redacts_generated_openclaw_bearer_token_from_notebook_outputs(self):
        redacted = notebook_adapter._redact(
            {
                "outputs": [
                    {
                        "text": (
                            "$ curl -H 'Authorization: Bearer "
                            "33edab45ea2845acc0498b5139a5142bafd3b4b2d32ebfc58f40a563cba18cae' "
                            "http://127.0.0.1:18789/hooks/agent"
                        )
                    }
                ]
            },
            {},
        )

        self.assertIn("Authorization: Bearer <redacted:OPENCLAW_HOOKS_TOKEN>", redacted["outputs"][0]["text"])
        self.assertNotIn("33edab45", redacted["outputs"][0]["text"])

    def test_persist_cell_keeps_hooks_token_out_of_debug_env_file(self):
        source = notebook_adapter.PERSIST_SOURCE
        keys_block = source.split("_keys = [", 1)[1].split("]", 1)[0]

        self.assertNotIn("OPENCLAW_HOOKS_TOKEN", keys_block)
        self.assertNotIn("RTSP_SAMPLE_URL", keys_block)
        self.assertIn("NEMOCLAW_HOOKS_TOKEN_FILE", source)
        self.assertIn("chmod(0o600)", source)

    def test_rtsp_runtime_value_is_redacted_and_not_persisted(self):
        with tempfile.TemporaryDirectory() as td:
            env_path = Path(td) / "nemoclaw.env"
            token_path = Path(td) / "hooks_token"
            rtsp_url = notebook_adapter.DEFAULT_RTSP_SAMPLE_URL
            with mock.patch.dict(
                os.environ,
                {
                    "RTSP_SAMPLE_URL": rtsp_url,
                    "NEMOCLAW_CI_ENV_OUT": str(env_path),
                    "NEMOCLAW_HOOKS_TOKEN_FILE": str(token_path),
                },
                clear=True,
            ):
                namespace: dict[str, object] = {}
                exec(notebook_adapter.PARAMETER_SOURCE, namespace)
                exec(notebook_adapter.PERSIST_SOURCE, namespace)
                redacted = notebook_adapter._redact(
                    {"output": f"configured {rtsp_url}"},
                    notebook_adapter._redaction_values(),
                )

            persisted = env_path.read_text(encoding="utf-8")

        self.assertEqual(namespace["RTSP_SAMPLE_URL"], rtsp_url)
        self.assertFalse(namespace["OPENCLAW_HOOKS_ENABLED"])
        self.assertFalse(namespace["AGENT_HOOKS_ENABLED"])
        self.assertNotIn("RTSP_SAMPLE_URL", persisted)
        self.assertNotIn(rtsp_url, persisted)
        self.assertEqual(
            redacted["output"],
            "configured <redacted:RTSP_SAMPLE_URL>",
        )

    def test_ci_rtsp_injection_requires_fixed_flag_and_public_relay(self):
        valid = {
            notebook_adapter.NEMOCLAW_CI_RTSP_INJECTION_FLAG: "1",
            "RTSP_SAMPLE_URL": notebook_adapter.DEFAULT_RTSP_SAMPLE_URL,
        }
        with mock.patch.dict(os.environ, valid, clear=True):
            self.assertTrue(notebook_adapter._validate_ci_rtsp_environment())
            namespace: dict[str, object] = {}
            exec(notebook_adapter.PARAMETER_SOURCE, namespace)
            self.assertTrue(
                namespace["NEMOCLAW_CI_INJECT_RTSP_SAMPLE_URL"]
            )

        invalid_cases = (
            {
                notebook_adapter.NEMOCLAW_CI_RTSP_INJECTION_FLAG: "true",
                "RTSP_SAMPLE_URL": notebook_adapter.DEFAULT_RTSP_SAMPLE_URL,
            },
            {
                notebook_adapter.NEMOCLAW_CI_RTSP_INJECTION_FLAG: "1",
                "RTSP_SAMPLE_URL": "rtsp://operator.example.test/override",
            },
        )
        for environment in invalid_cases:
            with self.subTest(environment=environment):
                with (
                    mock.patch.dict(os.environ, environment, clear=True),
                    self.assertRaises(ValueError),
                ):
                    notebook_adapter._validate_ci_rtsp_environment()
                with (
                    mock.patch.dict(os.environ, environment, clear=True),
                    self.assertRaises(ValueError),
                ):
                    exec(notebook_adapter.PARAMETER_SOURCE, {})

        with mock.patch.dict(
            os.environ,
            {"RTSP_SAMPLE_URL": "rtsp://operator.example.test/override"},
            clear=True,
        ):
            self.assertFalse(notebook_adapter._validate_ci_rtsp_environment())

    def test_gateway_binding_round_trips_to_headless_env_file(self):
        with tempfile.TemporaryDirectory() as td:
            env_path = Path(td) / "nemoclaw.env"
            token_path = Path(td) / "hooks_token"
            with mock.patch.dict(
                os.environ,
                {
                    "NEMOCLAW_GATEWAY_PORT": "19080",
                    "OPENSHELL_DOCKER_NETWORK_NAME": "openshell-docker",
                    "NEMOCLAW_CI_ENV_OUT": str(env_path),
                    "NEMOCLAW_HOOKS_TOKEN_FILE": str(token_path),
                },
                clear=True,
            ):
                namespace: dict[str, object] = {}
                exec(notebook_adapter.PARAMETER_SOURCE, namespace)
                exec(notebook_adapter.PERSIST_SOURCE, namespace)

            persisted = env_path.read_text(encoding="utf-8")
            self.assertIn("export NEMOCLAW_GATEWAY_PORT=19080\n", persisted)
            self.assertEqual(
                namespace["NEMOCLAW_GATEWAY_NAME"],
                "nemoclaw-19080",
            )
            self.assertIn(
                "export OPENSHELL_DOCKER_NETWORK_NAME=openshell-docker\n",
                persisted,
            )

    def test_managed_mcp_persists_only_credential_environment_name(self):
        with tempfile.TemporaryDirectory() as td:
            env_path = Path(td) / "nemoclaw.env"
            token_path = Path(td) / "hooks_token"
            raw_credential = "credential-must-not-be-persisted"
            with mock.patch.dict(
                os.environ,
                {
                    "NEMOCLAW_CI_ENV_OUT": str(env_path),
                    "NEMOCLAW_HOOKS_TOKEN_FILE": str(token_path),
                    "VSS_ORCHESTRATOR_MCP_CREDENTIAL_ENV": (
                        "VSS_ORCHESTRATOR_MCP_TOKEN"
                    ),
                    "VSS_ORCHESTRATOR_MCP_TOKEN": raw_credential,
                },
                clear=True,
            ):
                namespace: dict[str, object] = {}
                exec(notebook_adapter.PARAMETER_SOURCE, namespace)
                exec(notebook_adapter.PERSIST_SOURCE, namespace)
            persisted = env_path.read_text(encoding="utf-8")

        self.assertEqual(
            namespace["VSS_ORCHESTRATOR_MCP_CREDENTIAL_ENV"],
            "VSS_ORCHESTRATOR_MCP_TOKEN",
        )
        self.assertIn(
            "export VSS_ORCHESTRATOR_MCP_CREDENTIAL_ENV="
            "VSS_ORCHESTRATOR_MCP_TOKEN\n",
            persisted,
        )
        self.assertNotIn(raw_credential, persisted)

    def test_parameter_cell_maps_remote_runtime_env_and_derives_nemoclaw_provider(self):
        defaults = {
            "HARDWARE_PROFILE": "RTXPRO6000BW",
            "NEMOCLAW_ENDPOINT_URL": "",
            "NEMOCLAW_MODEL": "",
            "COMPATIBLE_API_KEY": "",
            "NEMOCLAW_INSTALL_REF": "",
            "OPENCLAW_HOOKS_PATH": "/hooks",
            "VSS_LLM_NAME": "",
            "VSS_LLM_ENDPOINT_URL": "",
            "VSS_LLM_MODEL_TYPE": "",
            "VSS_LLM_ENABLE_THINKING": "",
            "VSS_OPENAI_API_KEY": "",
            "VSS_VLM_NAME": "",
            "VSS_VLM_ENDPOINT_URL": "",
            "VSS_VLM_MODEL_TYPE": "",
            "LLM_DEVICE_ID": "",
            "VLM_DEVICE_ID": "",
            "EXTERNAL_IP": "",
        }
        env_keys = (
            "LLM_ENDPOINT_URL",
            "LLM_NAME",
            "VSS_LLM_ENDPOINT_URL",
            "VSS_LLM_NAME",
            "LLM_REMOTE_URL",
            "LLM_REMOTE_MODEL",
            "VLM_ENDPOINT_URL",
            "VLM_NAME",
            "VSS_VLM_ENDPOINT_URL",
            "VSS_VLM_NAME",
            "VLM_REMOTE_URL",
            "VLM_REMOTE_MODEL",
            "ANTHROPIC_BASE_URL",
            "ANTHROPIC_MODEL",
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "NEMOCLAW_ENDPOINT_URL",
            "NEMOCLAW_MODEL",
            "COMPATIBLE_API_KEY",
            "NVIDIA_API_KEY",
            "VSS_ORCHESTRATOR_MCP_URL",
            "VSS_ORCHESTRATOR_MCP_CREDENTIAL_ENV",
            "VSS_ORCHESTRATOR_MCP_TYPE",
        )
        previous = {key: os.environ.get(key) for key in env_keys}
        for key in env_keys:
            os.environ.pop(key, None)
        os.environ["LLM_REMOTE_URL"] = "https://inference-api.example/v1/models/"
        os.environ["LLM_REMOTE_MODEL"] = "nvidia/example-model"
        os.environ["VLM_REMOTE_URL"] = "https://vlm-api.example/v1/"
        os.environ["VLM_REMOTE_MODEL"] = "nvidia/example-vlm"
        os.environ["NVIDIA_API_KEY"] = "nvapi-ci"
        try:
            exec(notebook_adapter.PARAMETER_SOURCE, defaults)
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.assertEqual(defaults["NEMOCLAW_ENDPOINT_URL"], "https://inference-api.example/v1")
        self.assertEqual(defaults["NEMOCLAW_MODEL"], "nvidia/example-model")
        self.assertEqual(defaults["COMPATIBLE_API_KEY"], "nvapi-ci")
        self.assertEqual(defaults["LLM_ENDPOINT_URL"], "https://inference-api.example")
        self.assertEqual(defaults["LLM_NAME"], "nvidia/example-model")
        self.assertEqual(defaults["VLM_ENDPOINT_URL"], "https://vlm-api.example")
        self.assertEqual(defaults["VLM_NAME"], "nvidia/example-vlm")
        self.assertEqual(defaults["OPENCLAW_DISABLE_STREAMING_TOOL_CALLS"], "1")
        self.assertEqual(defaults["VSS_ORCHESTRATOR_MCP_TYPE"], "streamable-http")
        self.assertEqual(defaults["VSS_ORCHESTRATOR_MCP_CREDENTIAL_ENV"], "")
        self.assertEqual(defaults["VSS_ORCHESTRATOR_MCP_URL"], "http://host.openshell.internal:9988/mcp")
        self.assertEqual(defaults["MCP_URL"], "http://127.0.0.1:9988/mcp")

    def test_parameter_cell_prefers_nonempty_canonical_then_legacy_runtime_env(self):
        with mock.patch.dict(
            os.environ,
            {
                "LLM_ENDPOINT_URL": "",
                "VSS_LLM_ENDPOINT_URL": "https://legacy-llm.example/v1",
                "LLM_REMOTE_URL": "https://remote-llm.example/v1",
                "LLM_NAME": "",
                "VSS_LLM_NAME": "legacy-llm",
                "LLM_REMOTE_MODEL": "remote-llm",
                "VLM_ENDPOINT_URL": "https://canonical-vlm.example/v1/models",
                "VSS_VLM_ENDPOINT_URL": "https://legacy-vlm.example/v1",
                "VLM_REMOTE_URL": "https://remote-vlm.example/v1",
                "VLM_NAME": "canonical-vlm",
                "VSS_VLM_NAME": "legacy-vlm",
                "VLM_REMOTE_MODEL": "remote-vlm",
            },
            clear=True,
        ):
            namespace: dict[str, object] = {}
            exec(notebook_adapter.PARAMETER_SOURCE, namespace)

        self.assertEqual(namespace["LLM_ENDPOINT_URL"], "https://legacy-llm.example")
        self.assertEqual(namespace["LLM_NAME"], "legacy-llm")
        self.assertEqual(namespace["VLM_ENDPOINT_URL"], "https://canonical-vlm.example")
        self.assertEqual(namespace["VLM_NAME"], "canonical-vlm")

    def test_parameter_cell_uses_https_for_orchestrator_when_enabled(self):
        with tempfile.TemporaryDirectory() as td:
            env_path = Path(td) / "nemoclaw.env"
            token_path = Path(td) / "hooks_token"
            with mock.patch.dict(
                os.environ,
                {
                    "ORCHESTRATOR_ENABLE_HTTPS": "true",
                    "VSS_ORCHESTRATOR_MCP_PORT": "10443",
                    "HOST_INTERNAL_ALIAS": "host.nemoclaw.internal",
                    "NEMOCLAW_CI_ENV_OUT": str(env_path),
                    "NEMOCLAW_HOOKS_TOKEN_FILE": str(token_path),
                },
                clear=True,
            ):
                namespace: dict[str, object] = {}
                exec(notebook_adapter.PARAMETER_SOURCE, namespace)
                exec(notebook_adapter.PERSIST_SOURCE, namespace)

                self.assertIs(namespace["ORCHESTRATOR_ENABLE_HTTPS"], True)
                self.assertEqual(namespace["MCP_SCHEME"], "https")
                self.assertEqual(
                    namespace["VSS_ORCHESTRATOR_MCP_URL"],
                    "https://host.nemoclaw.internal:10443/mcp",
                )
                self.assertEqual(
                    namespace["MCP_URL"], "https://127.0.0.1:10443/mcp"
                )
                self.assertEqual(os.environ["ORCHESTRATOR_ENABLE_HTTPS"], "true")
                self.assertIn(
                    "export ORCHESTRATOR_ENABLE_HTTPS=True\n",
                    env_path.read_text(encoding="utf-8"),
                )

    def test_parameter_cell_accepts_ngc_api_key_alias(self):
        defaults = {
            "HARDWARE_PROFILE": "RTXPRO6000BW",
            "NEMOCLAW_ENDPOINT_URL": "",
            "NEMOCLAW_MODEL": "",
            "COMPATIBLE_API_KEY": "",
        }
        env_keys = ("NGC_CLI_API_KEY", "NGC_API_KEY")
        previous = {key: os.environ.get(key) for key in env_keys}
        for key in env_keys:
            os.environ.pop(key, None)
        os.environ["NGC_API_KEY"] = "ngc-alias"
        try:
            exec(notebook_adapter.PARAMETER_SOURCE, defaults)
            self.assertEqual(os.environ.get("NGC_CLI_API_KEY"), "ngc-alias")
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.assertEqual(defaults["NGC_CLI_API_KEY"], "ngc-alias")

    def test_parameter_cell_prefers_ci_agent_model_over_vss_runtime_model(self):
        defaults = {
            "HARDWARE_PROFILE": "RTXPRO6000BW",
            "NEMOCLAW_ENDPOINT_URL": "",
            "NEMOCLAW_MODEL": "",
            "COMPATIBLE_API_KEY": "",
        }
        env_keys = (
            "LLM_REMOTE_URL",
            "LLM_REMOTE_MODEL",
            "ANTHROPIC_BASE_URL",
            "ANTHROPIC_MODEL",
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "NEMOCLAW_ENDPOINT_URL",
            "NEMOCLAW_MODEL",
            "COMPATIBLE_API_KEY",
            "NVIDIA_API_KEY",
        )
        previous = {key: os.environ.get(key) for key in env_keys}
        for key in env_keys:
            os.environ.pop(key, None)
        os.environ["LLM_REMOTE_URL"] = "https://vss-runtime.example"
        os.environ["LLM_REMOTE_MODEL"] = "nvidia/nvidia-nemotron-nano-9b-v2"
        os.environ["ANTHROPIC_BASE_URL"] = "https://ci-agent.example/v1"
        os.environ["ANTHROPIC_MODEL"] = "aws/anthropic/bedrock-claude-opus-4-8"
        os.environ["ANTHROPIC_API_KEY"] = "anthropic-ci"
        os.environ["OPENAI_API_KEY"] = "unrelated-openai-key"
        os.environ["NVIDIA_API_KEY"] = "nvapi-ci"
        try:
            exec(notebook_adapter.PARAMETER_SOURCE, defaults)
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.assertEqual(defaults["NEMOCLAW_ENDPOINT_URL"], "https://ci-agent.example/v1")
        self.assertEqual(defaults["NEMOCLAW_MODEL"], "aws/anthropic/bedrock-claude-opus-4-8")
        self.assertEqual(defaults["COMPATIBLE_API_KEY"], "anthropic-ci")

    def test_parameter_cell_tolerates_missing_advanced_defaults(self):
        defaults = {
            "HARDWARE_PROFILE": "RTXPRO6000BW",
            "NEMOCLAW_ENDPOINT_URL": "",
            "NEMOCLAW_MODEL": "",
            "COMPATIBLE_API_KEY": "",
        }

        exec(notebook_adapter.PARAMETER_SOURCE, defaults)

        self.assertEqual(defaults["OPENCLAW_HOOKS_PATH"], "/hooks")
        self.assertEqual(defaults["NEMOCLAW_INSTALL_REF"], "")
        self.assertEqual(defaults["VSS_ORCHESTRATOR_MCP_TYPE"], "streamable-http")
        self.assertEqual(
            defaults["VSS_ORCHESTRATOR_MCP_URL"],
            "http://host.openshell.internal:9988/mcp",
        )
        self.assertEqual(defaults["MCP_URL"], "http://127.0.0.1:9988/mcp")

    def test_vss_notebook_leaves_gpu_placement_to_profile_defaults(self):
        notebook = json.loads(
            (REPO_ROOT / "deploy" / "docker" / "scripts" / "deploy_vss_orchestrator.ipynb").read_text()
        )
        settings_cells = [cell for cell in notebook["cells"] if cell.get("id") == "20b35654"]
        self.assertEqual(len(settings_cells), 1)
        source = "".join(settings_cells[0].get("source", ""))

        self.assertIn('LLM_DEVICE_ID = ""', source)
        self.assertIn('VLM_DEVICE_ID = ""', source)
        self.assertNotIn('LLM_DEVICE_ID = "0"', source)
        self.assertNotIn('VLM_DEVICE_ID = "1"', source)

    def test_vss_notebook_does_not_print_masked_ngc_credentials(self):
        notebook = json.loads(
            (
                REPO_ROOT
                / "deploy"
                / "docker"
                / "scripts"
                / "deploy_vss_orchestrator.ipynb"
            ).read_text()
        )
        ngc_cells = [
            cell for cell in notebook["cells"] if cell.get("id") == "6a72f8d2"
        ]
        self.assertEqual(len(ngc_cells), 1)
        source = "".join(ngc_cells[0].get("source", ""))

        self.assertIn('print("NGC CLI configured.")', source)
        self.assertNotIn("ngc config current", source)

    def test_parameter_cell_preserves_blank_notebook_gpu_defaults(self):
        defaults = {
            "HARDWARE_PROFILE": "RTXPRO6000BW",
            "NEMOCLAW_ENDPOINT_URL": "",
            "NEMOCLAW_MODEL": "",
            "COMPATIBLE_API_KEY": "",
            "LLM_DEVICE_ID": "",
            "VLM_DEVICE_ID": "",
        }

        with mock.patch.dict(os.environ, {}, clear=True):
            exec(notebook_adapter.PARAMETER_SOURCE, defaults)

        self.assertEqual(defaults["LLM_DEVICE_ID"], "")
        self.assertEqual(defaults["VLM_DEVICE_ID"], "")

    def test_parameter_cell_honors_explicit_ci_gpu_placement(self):
        defaults = {
            "HARDWARE_PROFILE": "RTXPRO6000BW",
            "NEMOCLAW_ENDPOINT_URL": "",
            "NEMOCLAW_MODEL": "",
            "COMPATIBLE_API_KEY": "",
            "LLM_DEVICE_ID": "",
            "VLM_DEVICE_ID": "",
        }

        with mock.patch.dict(
            os.environ,
            {"LLM_DEVICE_ID": "2", "VLM_DEVICE_ID": "3"},
            clear=True,
        ):
            exec(notebook_adapter.PARAMETER_SOURCE, defaults)

        self.assertEqual(defaults["LLM_DEVICE_ID"], "2")
        self.assertEqual(defaults["VLM_DEVICE_ID"], "3")

    def test_agent_setup_cell_compiles_from_split_vss_notebook(self):
        notebook = json.loads(
            (REPO_ROOT / "deploy" / "docker" / "scripts" / "deploy_vss_orchestrator.ipynb").read_text()
        )
        setup_cells = [cell for cell in notebook["cells"] if cell.get("id") == "c13aaf5e"]
        self.assertEqual(len(setup_cells), 1)
        source = "".join(setup_cells[0].get("source", ""))

        self.assertIn(
            'REQUIRED_MCP_APT_PACKAGES = ("ffmpeg", "libcairo2-dev", "pkg-config", "python3-dev")',
            source,
        )
        self.assertIn("run_uv_sync", source)
        self.assertIn('"uv", "sync", "--no-dev", "--extra", "agent"', source)
        self.assertIn("ORCHESTRATOR_MCP_NAT_BIN", source)
        self.assertIn("ORCHESTRATOR_MODULE_PATH", source)
        self.assertIn("ORCHESTRATOR_MCP_SOURCE_SHA256", source)
        self.assertIn('os.environ.get("PR_HEAD_SHA", "").strip()', source)
        self.assertIn("[str(ORCHESTRATOR_MCP_NAT_BIN), \"mcp\", \"--help\"]", source)
        self.assertIn("ensure_agent_venv", source)
        self.assertIn('command.append("--clear")', source)
        self.assertIn('if "--force" in uv_venv_help.stdout', source)
        self.assertNotIn('command.extend(["--clear", "--force"])', source)
        self.assertIn("Refusing to replace symlinked orchestrator environment", source)
        compile(source, "deploy_vss_orchestrator.ipynb:c13aaf5e", "exec")

        tree = ast.parse(source)
        ensure_venv_nodes = [
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name in {"uv_env_for_agent", "ensure_agent_venv"}
        ]
        self.assertEqual(
            [node.name for node in ensure_venv_nodes],
            ["uv_env_for_agent", "ensure_agent_venv"],
        )
        ensure_venv_module = ast.fix_missing_locations(
            ast.Module(body=ensure_venv_nodes, type_ignores=[])
        )

        with tempfile.TemporaryDirectory() as tempdir:
            agent_dir = Path(tempdir) / "services" / "agent"
            venv_dir = agent_dir / ".venv"
            venv_dir.mkdir(parents=True)
            commands: list[tuple[list[str], dict[str, object]]] = []
            supports_force = {"value": False}

            def fake_run(command, **kwargs):
                commands.append((command, kwargs))
                if command == ["uv", "venv", "--help"]:
                    stdout = "      --force\\n" if supports_force["value"] else ""
                    return subprocess.CompletedProcess(command, 0, stdout=stdout)
                venv_python = venv_dir / "bin" / "python"
                venv_python.parent.mkdir(parents=True, exist_ok=True)
                venv_python.write_text("#!/bin/sh\n")
                venv_python.chmod(0o755)
                return subprocess.CompletedProcess(command, 0)

            namespace = {
                "AGENT_DIR": agent_dir,
                "ORCHESTRATOR_MCP_VENV_DIR": venv_dir,
                "ORCHESTRATOR_MCP_PYTHON_VERSION": "3.10",
                "os": os,
                "subprocess": mock.Mock(run=fake_run),
            }
            exec(
                compile(
                    ensure_venv_module,
                    "deploy_vss_orchestrator.ipynb:c13aaf5e:ensure_agent_venv",
                    "exec",
                ),
                namespace,
            )
            ensure_agent_venv = namespace["ensure_agent_venv"]

            with mock.patch.dict(
                os.environ,
                {
                    "VIRTUAL_ENV": "/outside/kernel-venv",
                    "UV_PROJECT_ENVIRONMENT": "/outside/project-venv",
                },
            ):
                ensure_agent_venv()
            self.assertEqual(len(commands), 2)
            help_command, help_kwargs = commands[0]
            self.assertEqual(help_command, ["uv", "venv", "--help"])
            self.assertEqual(help_kwargs["cwd"], str(agent_dir))
            self.assertTrue(help_kwargs["check"])
            self.assertTrue(help_kwargs["capture_output"])
            self.assertTrue(help_kwargs["text"])
            self.assertNotIn("VIRTUAL_ENV", help_kwargs["env"])
            self.assertNotIn("UV_PROJECT_ENVIRONMENT", help_kwargs["env"])

            command, kwargs = commands[1]
            self.assertEqual(
                command,
                [
                    "uv",
                    "venv",
                    "--clear",
                    "--python",
                    "3.10",
                    str(venv_dir),
                ],
            )
            self.assertEqual(kwargs["cwd"], str(agent_dir))
            self.assertTrue(kwargs["check"])
            self.assertNotIn("VIRTUAL_ENV", kwargs["env"])
            self.assertNotIn("UV_PROJECT_ENVIRONMENT", kwargs["env"])

            ensure_agent_venv()
            self.assertEqual(len(commands), 2)

            (venv_dir / "bin" / "python").unlink()
            supports_force["value"] = True
            ensure_agent_venv()
            self.assertEqual(len(commands), 4)
            self.assertEqual(commands[2][0], ["uv", "venv", "--help"])
            self.assertEqual(
                commands[3][0],
                [
                    "uv",
                    "venv",
                    "--clear",
                    "--force",
                    "--python",
                    "3.10",
                    str(venv_dir),
                ],
            )

            target_dir = agent_dir / "target-venv"
            target_python = target_dir / "bin" / "python"
            target_python.parent.mkdir(parents=True)
            target_python.write_text("#!/bin/sh\n")
            target_python.chmod(0o755)
            symlink_dir = agent_dir / "symlink-venv"
            symlink_dir.symlink_to(target_dir, target_is_directory=True)
            namespace["ORCHESTRATOR_MCP_VENV_DIR"] = symlink_dir
            with self.assertRaisesRegex(
                RuntimeError, "Refusing to replace symlinked orchestrator environment"
            ):
                ensure_agent_venv()
            self.assertEqual(len(commands), 4)

    def test_composed_agent_setup_omits_unused_torch_runtime(self):
        notebook = json.loads(
            (
                REPO_ROOT
                / "deploy"
                / "docker"
                / "scripts"
                / "deploy_vss_orchestrator.ipynb"
            ).read_text()
        )
        setup_cells = [
            cell for cell in notebook["cells"] if cell.get("id") == "c13aaf5e"
        ]
        self.assertEqual(len(setup_cells), 1)

        patched = notebook_adapter._patch_ci_cell(
            "c13aaf5e",
            notebook_adapter._normalize_cell_source(setup_cells[0]),
        )
        source = patched["source"]

        self.assertIn(
            '["uv", "sync", "--no-dev", "--extra", "agent", '
            '"--no-install-package", "torch"]',
            source,
        )
        self.assertNotIn(
            '["uv", "sync", "--no-dev", "--extra", "agent"]',
            source,
        )
        self.assertIn('UV_NO_SYNC = "1"', source)
        self.assertIn(
            'os.environ["UV_NO_SYNC"] = UV_NO_SYNC\n'
            'agent_env = uv_env_for_agent()',
            source,
        )
        self.assertIn('"UV_NO_SYNC",', notebook_adapter.PERSIST_SOURCE)
        compile(source, "composed:c13aaf5e", "exec")

        with tempfile.TemporaryDirectory() as tempdir:
            env_path = Path(tempdir) / "nemoclaw.env"
            token_path = Path(tempdir) / "hooks-token"
            with mock.patch.dict(
                os.environ,
                {
                    "NEMOCLAW_CI_ENV_OUT": str(env_path),
                    "NEMOCLAW_HOOKS_TOKEN_FILE": str(token_path),
                },
                clear=True,
            ):
                exec(notebook_adapter.PERSIST_SOURCE, {"UV_NO_SYNC": "1"})
            self.assertIn("export UV_NO_SYNC=1\n", env_path.read_text())

        tree = ast.parse(source)
        run_uv_sync_nodes = [
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "run_uv_sync"
        ]
        self.assertEqual(len(run_uv_sync_nodes), 1)
        run_uv_sync_module = ast.fix_missing_locations(
            ast.Module(body=run_uv_sync_nodes, type_ignores=[])
        )
        agent_dir = Path("/tmp/vss-agent")
        run = mock.Mock(
            return_value=subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="",
                stderr="",
            )
        )
        namespace = {
            "AGENT_DIR": agent_dir,
            "subprocess": mock.Mock(run=run),
            "uv_env_for_agent": lambda: {"PATH": "/usr/bin"},
        }
        exec(
            compile(run_uv_sync_module, "composed:c13aaf5e:run_uv_sync", "exec"),
            namespace,
        )

        result = namespace["run_uv_sync"]()

        self.assertEqual(result.returncode, 0)
        run.assert_called_once_with(
            [
                "uv",
                "sync",
                "--no-dev",
                "--extra",
                "agent",
                "--no-install-package",
                "torch",
            ],
            cwd=str(agent_dir),
            env={"PATH": "/usr/bin"},
            check=False,
            capture_output=True,
            text=True,
        )

    def test_rtsp_sample_probe_is_wired_without_mcp_secret_argument(self):
        config = (
            REPO_ROOT / "deploy" / "docker" / "scripts" / "vss_orchestrator_mcp_config.yml"
        ).read_text(encoding="utf-8")
        notebook = json.loads(
            (
                REPO_ROOT
                / "deploy"
                / "docker"
                / "scripts"
                / "deploy_vss_orchestrator.ipynb"
            ).read_text(encoding="utf-8")
        )
        notebook_markdown = "\n".join(
            "".join(cell.get("source", ""))
            for cell in notebook["cells"]
            if cell.get("cell_type") == "markdown"
        )
        notebook_code = "\n".join(
            "".join(cell.get("source", ""))
            for cell in notebook["cells"]
            if cell.get("cell_type") == "code"
        )
        skill = (
            REPO_ROOT / "skills" / "vss-deploy-dense-captioning" / "SKILL.md"
        ).read_text(encoding="utf-8")
        nemoclaw_agents = (
            REPO_ROOT / ".openclaw" / "workspace" / "_nemoclaw" / "AGENTS.md"
        ).read_text(encoding="utf-8")

        self.assertIn("\n    - rtsp_sample_probe\n", config)
        self.assertIn("exposes ten tools", notebook_markdown)
        self.assertIn("| `rtsp_sample_probe` |", notebook_markdown)
        self.assertIn(
            'env["RTSP_SAMPLE_URL"] = configured_rtsp_sample_url',
            notebook_code,
        )
        self.assertIn(
            "`vss_orchestrator__rtsp_sample_probe` MCP tool with no URL argument",
            skill,
        )
        self.assertIn(
            "Call `vss_orchestrator__rtsp_sample_probe` with no URL argument",
            nemoclaw_agents,
        )

    def test_ci_parameters_drive_nemoclaw_provider_derivation(self):
        notebook = json.loads(
            (REPO_ROOT / "deploy" / "docker" / "scripts" / "deploy_nemoclaw.ipynb").read_text()
        )
        sources = {
            cell.get("id"): "".join(cell.get("source", ""))
            for cell in notebook["cells"]
        }
        namespace: dict[str, object] = {}
        ci_env = {
            "LLM_REMOTE_URL": "https://inference-api.example",
            "LLM_REMOTE_MODEL": "nvidia/example-model",
            "NVIDIA_API_KEY": "nvapi-ci",
            "VSS_REPO_DIR": str(REPO_ROOT),
        }

        with (
            mock.patch.dict(os.environ, ci_env, clear=True),
            mock.patch("subprocess.check_output", return_value="hooks-token\n"),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            exec(sources["994c77c2"], namespace)
            exec(sources["47d20bb1"], namespace)
            exec(notebook_adapter.PARAMETER_SOURCE, namespace)
            exec(sources["e67f6da4"], namespace)

        self.assertEqual(namespace["NEMOCLAW_PROVIDER"], "custom")
        self.assertEqual(namespace["NEMOCLAW_ENDPOINT_URL"], "https://inference-api.example/v1")
        self.assertEqual(namespace["NEMOCLAW_MODEL"], "nvidia/example-model")
        self.assertEqual(namespace["COMPATIBLE_API_KEY"], "nvapi-ci")

    def test_nemoclaw_docker_pin_uses_portable_sudo_environment(self):
        notebook = json.loads(
            (REPO_ROOT / "deploy" / "docker" / "scripts" / "deploy_nemoclaw.ipynb").read_text()
        )
        cells = [
            cell
            for cell in notebook["cells"]
            if cell.get("id") == "cb782286-f0bd-401e-9056-39a81821e3c4"
        ]

        self.assertEqual(len(cells), 1)
        source = "".join(cells[0].get("source", ""))
        self.assertIn(
            "sudo /usr/bin/env DEBIAN_FRONTEND=noninteractive "
            "apt-get install -y",
            source,
        )
        self.assertNotIn(
            "sudo DEBIAN_FRONTEND=noninteractive apt-get",
            source,
        )

    def test_nemoclaw_pin_includes_owning_gateway_and_first_boot_fixes(self):
        notebook = json.loads(
            (REPO_ROOT / "deploy" / "docker" / "scripts" / "deploy_nemoclaw.ipynb").read_text()
        )
        cells = [cell for cell in notebook["cells"] if cell.get("id") == "e67f6da4"]

        self.assertEqual(len(cells), 1)
        source = "".join(cells[0].get("source", ""))
        self.assertIn('NEMOCLAW_INSTALL_REF = "v0.0.103"', source)
        self.assertIn("owning-gateway routing fix from v0.0.88", source)
        self.assertIn("first-boot", source)
        self.assertNotIn('NEMOCLAW_INSTALL_REF = "v0.0.80"', source)

    def test_split_notebooks_use_refactored_brev_util_path(self):
        expected_parts = (
            '"packages"',
            '"vss_agents"',
            '"src"',
            '"vss_agents"',
            '"orchestrator"',
            '"brev_util.py"',
        )
        for notebook_name, cell_id in (
            ("deploy_nemoclaw.ipynb", "e67f6da4"),
            ("deploy_vss_orchestrator.ipynb", "20b35654"),
        ):
            notebook = json.loads(
                (REPO_ROOT / "deploy" / "docker" / "scripts" / notebook_name).read_text()
            )
            cells = [cell for cell in notebook["cells"] if cell.get("id") == cell_id]
            self.assertEqual(len(cells), 1)
            source = "".join(cells[0].get("source", ""))
            brev_util_line = next(
                line for line in source.splitlines() if line.startswith("BREV_UTIL_PATH = ")
            )
            for part in expected_parts:
                self.assertIn(part, brev_util_line)
            self.assertNotIn('/ "agent" / "orchestrator"', brev_util_line)

    def test_source_nemoclaw_notebook_has_native_mcp_runtime_branches(self):
        notebook = json.loads(
            (
                REPO_ROOT
                / "deploy"
                / "docker"
                / "scripts"
                / "deploy_nemoclaw.ipynb"
            ).read_text()
        )
        cells = {
            cell.get("id"): "".join(cell.get("source", ""))
            for cell in notebook["cells"]
        }
        parameters = cells["e67f6da4"]
        mcp_markdown = cells["s36-md"]
        mcp_source = cells["s36-code"]

        self.assertIn(
            "VSS_ORCHESTRATOR_MCP_CREDENTIAL_ENV = os.environ.get(",
            parameters,
        )
        self.assertIn("public HTTPS DNS endpoint", mcp_markdown)
        self.assertIn("credential variable **name**", mcp_markdown)

        self.assertIn('if AGENT_RUNTIME == "hermes":', mcp_source)
        self.assertIn("configure_hermes_mcp()", mcp_source)
        self.assertIn('elif AGENT_RUNTIME == "openclaw":', mcp_source)
        self.assertIn("configure_openclaw_mcp()", mcp_source)

        self.assertIn(
            'cmd = ["nemohermes", NEMOCLAW_SANDBOX_NAME, *args]',
            mcp_source,
        )
        self.assertIn('"mcp", "status", "vss_orchestrator"', mcp_source)
        self.assertIn('"--json", "--no-probe",', mcp_source)
        self.assertIn("def classify_hermes_mcp_status(", mcp_source)
        self.assertIn('"mcp", "add", "vss_orchestrator",', mcp_source)
        self.assertIn('"--env", credential_env,', mcp_source)
        self.assertIn('"shields", "down",', mcp_source)
        self.assertIn('"shields", "up"', mcp_source)
        self.assertIn("    finally:", mcp_source)
        self.assertNotIn('"--env", os.environ', mcp_source)

        self.assertIn(
            'cmd = ["nemoclaw", "sandbox", "exec", '
            'NEMOCLAW_SANDBOX_NAME, "--", *args]',
            mcp_source,
        )
        self.assertIn(
            '"mcporter", "config", "add", "vss_orchestrator"',
            mcp_source,
        )
        self.assertIn(
            '"mcporter", "config", "get", "vss_orchestrator", "--json"',
            mcp_source,
        )
        compile(mcp_source, "deploy_nemoclaw.ipynb:s36-code", "exec")

    def test_composed_notebook_separates_host_and_sandbox_mcp_urls(self):
        manifest = json.loads(
            (
                REPO_ROOT
                / ".github"
                / "skill-eval"
                / "nemoclaw"
                / "notebook_cells.json"
            ).read_text()
        )
        notebooks = [
            json.loads((REPO_ROOT / item["notebook"]).read_text())
            for item in manifest["notebooks"]
        ]
        built = notebook_adapter.build_notebooks(notebooks, manifest)
        sources = {
            cell.get("id"): "".join(cell.get("source", ""))
            for cell in built["cells"]
        }

        self.assertIn('"mcporter", "config", "add", "vss_orchestrator"', sources["s36-code"])
        self.assertIn('"--url", VSS_ORCHESTRATOR_MCP_URL', sources["s36-code"])
        self.assertIn('"--scope", "home"', sources["s36-code"])
        self.assertIn(
            '"mcporter", "config", "get", "vss_orchestrator", "--json"',
            sources["s36-code"],
        )
        self.assertNotIn("!nemoclaw sandbox mcp", sources["s36-code"])
        self.assertIn('if AGENT_RUNTIME == "hermes":', sources["s36-code"])
        self.assertIn(
            'cmd = ["nemohermes", NEMOCLAW_SANDBOX_NAME, *args]',
            sources["s36-code"],
        )
        self.assertIn(
            '"mcp", "status", "vss_orchestrator"',
            sources["s36-code"],
        )
        self.assertIn('"--json", "--no-probe",', sources["s36-code"])
        self.assertIn(
            "def classify_hermes_mcp_status(",
            sources["s36-code"],
        )
        self.assertIn(
            '"mcp", "add", "vss_orchestrator",',
            sources["s36-code"],
        )
        self.assertIn('"--env", credential_env,', sources["s36-code"])
        self.assertIn(
            '"shields", "down",',
            sources["s36-code"],
        )
        self.assertIn(
            '"--timeout", "15m",',
            sources["s36-code"],
        )
        self.assertIn('"shields", "up"', sources["s36-code"])
        self.assertIn("    finally:", sources["s36-code"])
        self.assertIn(
            "VSS_ORCHESTRATOR_MCP_CREDENTIAL_ENV",
            sources["ci-parameters-1"],
        )
        compile(sources["s36-code"], "s36-code", "exec")
        self.assertIn(
            '"openshell", "sandbox", "exec", "--name",',
            sources["s35-code"],
        )
        self.assertIn(
            '"-g", NEMOCLAW_GATEWAY_NAME, "--",',
            sources["s35-code"],
        )
        self.assertIn(
            "stdin=_ci_subprocess.DEVNULL",
            sources["s35-code"],
        )
        self.assertIn(
            f"timeout={notebook_adapter.SANDBOX_EXEC_TIMEOUT_SECONDS}",
            sources["s35-code"],
        )
        self.assertNotIn(
            "!nemoclaw sandbox exec",
            sources["s35-code"],
        )
        self.assertIn(
            'cmd = ["openshell", "sandbox", "exec", "--name", '
            "NEMOCLAW_SANDBOX_NAME,",
            sources["s36-code"],
        )
        self.assertIn(
            '"-g", NEMOCLAW_GATEWAY_NAME, "--", *args]',
            sources["s36-code"],
        )
        self.assertNotIn(
            'cmd = ["nemoclaw", "sandbox", "exec"',
            sources["s36-code"],
        )
        self.assertIn(
            "stdin=subprocess.DEVNULL",
            sources["s36-code"],
        )
        self.assertIn(
            f"timeout={notebook_adapter.SANDBOX_EXEC_TIMEOUT_SECONDS}",
            sources["s36-code"],
        )
        self.assertIn("NEMOCLAW_RECREATE_SANDBOX", sources["s31-code"])
        self.assertIn("if _exit_code != 0 or _recreate_sandbox:", sources["s31-code"])
        self.assertIn(
            "orchestrator_mcp_helper.check_mcp_health(",
            sources["042eabd1"],
        )
        self.assertIn(
            "expected_instance_id=VSS_ORCHESTRATOR_MCP_INSTANCE_ID",
            sources["042eabd1"],
        )
        self.assertIn(
            "expected_source_sha256=ORCHESTRATOR_MCP_SOURCE_SHA256",
            sources["042eabd1"],
        )
        self.assertIn(
            "expected_git_sha=ORCHESTRATOR_MCP_GIT_SHA",
            sources["042eabd1"],
        )
        self.assertIn(
            "stop_existing_orchestrator_mcp_listener(",
            sources["042eabd1"],
        )
        self.assertIn(
            "str(ORCHESTRATOR_MCP_NAT_BIN)",
            sources["042eabd1"],
        )
        self.assertIn("env = agent_env.copy()", sources["042eabd1"])
        self.assertIn(
            'MCP_SCHEME = "https" if ORCHESTRATOR_ENABLE_HTTPS else "http"',
            sources["20b35654"],
        )
        self.assertIn(
            'f"{MCP_SCHEME}://127.0.0.1:{MCP_PORT}/mcp"',
            sources["20b35654"],
        )
        self.assertIn("ssrf_denied|policy_denied", sources["df8210f5"])
        self.assertIn(
            "AGENT_HOOKS_ENABLED = OPENCLAW_HOOKS_ENABLED",
            sources["e67f6da4"],
        )
        self.assertIn(
            'os.environ.get(\n    "OPENCLAW_HOOKS_ENABLED",\n    "0",\n)',
            sources["ci-parameters-1"],
        )
        self.assertIn(
            'config_sets.append(("env.vars.RTSP_SAMPLE_URL", RTSP_SAMPLE_URL))',
            sources["s37-code"],
        )
        self.assertIn(
            'config_sets.append(("hooks.enabled", "true"))',
            sources["s37-code"],
        )
        self.assertIn(
            "Restarting gateway to apply sandbox config...",
            sources["s37-code"],
        )
        self.assertNotIn("webhook config", sources["s37-code"])
        self.assertEqual(
            sources["s37-code"].count(
                "!nemoclaw sandbox gateway restart"
            ),
            1,
        )
        self.assertIn(
            ".github/skill-eval/nemoclaw/direct_container_preflight.py",
            sources["s37-code"],
        )
        self.assertLess(
            sources["s37-code"].index(
                ".github/skill-eval/nemoclaw/direct_container_preflight.py"
            ),
            sources["s37-code"].index("!nemoclaw sandbox config set"),
        )

    @staticmethod
    def _hermes_mcp_status_absent():
        return {
            "server": "vss_orchestrator",
            "agent": "hermes",
            "warnings": [],
            "support": {
                "supported": True,
                "mode": "bridge",
                "adapter": "hermes-config",
            },
            "env": {"names": [], "missing": [], "ready": False},
            "provider": {
                "registryPresent": False,
                "gatewayPresent": False,
                "attached": None,
                "credentialReady": None,
            },
            "policy": {
                "registryPresent": False,
                "gatewayPresent": False,
            },
            "adapter": {"registered": None},
        }

    @staticmethod
    def _hermes_mcp_status_ready(
        *,
        url="https://mcp.example.com/mcp",
        credential_env="VSS_ORCHESTRATOR_MCP_TOKEN",
    ):
        return {
            "server": "vss_orchestrator",
            "agent": "hermes",
            "warnings": [
                "OpenShell currently attaches this credential provider at "
                "sandbox scope."
            ],
            "support": {
                "supported": True,
                "mode": "bridge",
                "adapter": "hermes-config",
            },
            "url": url,
            "env": {
                "names": [credential_env],
                "missing": [],
                "ready": True,
            },
            "provider": {
                "name": "demo-mcp-vss-orchestrator-0123456789abcdef",
                "registryPresent": True,
                "gatewayPresent": True,
                "attached": True,
                "credentialReady": True,
            },
            "policy": {
                "name": "mcp-bridge-vss-orchestrator",
                "registryPresent": True,
                "gatewayPresent": True,
            },
            "adapter": {"registered": True},
            "addedAt": "2026-08-04T00:00:00.000Z",
        }

    def test_composed_hermes_registers_absent_managed_mcp_with_shields(self):
        notebook = json.loads(
            (
                REPO_ROOT
                / "deploy"
                / "docker"
                / "scripts"
                / "deploy_nemoclaw.ipynb"
            ).read_text(encoding="utf-8")
        )
        source_cell = next(
            cell for cell in notebook["cells"] if cell.get("id") == "s36-code"
        )
        source = notebook_adapter._patch_ci_cell(
            "s36-code",
            {
                **source_cell,
                "source": "".join(source_cell["source"]),
            },
        )["source"]
        credential_env = "VSS_ORCHESTRATOR_MCP_TOKEN"
        results = [
            mock.Mock(
                returncode=0,
                stdout=json.dumps(self._hermes_mcp_status_absent()),
                stderr="",
            ),
            mock.Mock(returncode=0, stdout="", stderr=""),
            mock.Mock(returncode=0, stdout="", stderr=""),
            mock.Mock(
                returncode=0,
                stdout=json.dumps(
                    self._hermes_mcp_status_ready(
                        credential_env=credential_env,
                    )
                ),
                stderr="",
            ),
            mock.Mock(returncode=0, stdout="", stderr=""),
        ]
        raw_credential = "raw-bearer-must-stay-in-process-env"
        public_address = (
            socket.AF_INET,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
            "",
            ("93.184.216.34", 443),
        )
        namespace = {
            "AGENT_RUNTIME": "hermes",
            "NEMOCLAW_SANDBOX_NAME": "demo",
            "NEMOCLAW_GATEWAY_NAME": "nemoclaw-19080",
            "VSS_ORCHESTRATOR_MCP_URL": "https://mcp.example.com/mcp",
            "VSS_ORCHESTRATOR_MCP_CREDENTIAL_ENV": credential_env,
        }

        with (
            mock.patch.dict(
                os.environ,
                {credential_env: raw_credential},
                clear=True,
            ),
            mock.patch(
                "socket.getaddrinfo",
                return_value=[public_address],
            ),
            mock.patch("subprocess.run", side_effect=results) as run,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            exec(compile(source, "s36-code", "exec"), namespace)

        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(
            commands,
            [
                [
                    "nemohermes",
                    "demo",
                    "mcp",
                    "status",
                    "vss_orchestrator",
                    "--json",
                    "--no-probe",
                ],
                [
                    "nemohermes",
                    "demo",
                    "shields",
                    "down",
                    "--timeout",
                    "15m",
                    "--reason",
                    "MCP maintenance",
                ],
                [
                    "nemohermes",
                    "demo",
                    "mcp",
                    "add",
                    "vss_orchestrator",
                    "--url",
                    "https://mcp.example.com/mcp",
                    "--env",
                    credential_env,
                ],
                [
                    "nemohermes",
                    "demo",
                    "mcp",
                    "status",
                    "vss_orchestrator",
                    "--json",
                    "--no-probe",
                ],
                ["nemohermes", "demo", "shields", "up"],
            ],
        )
        self.assertNotIn(raw_credential, repr(commands))
        self.assertTrue(
            all(
                call.kwargs["timeout"]
                == notebook_adapter.MANAGED_MCP_TIMEOUT_SECONDS
                for call in run.call_args_list
            )
        )

    def test_composed_hermes_restores_shields_when_mcp_add_fails(self):
        notebook = json.loads(
            (
                REPO_ROOT
                / "deploy"
                / "docker"
                / "scripts"
                / "deploy_nemoclaw.ipynb"
            ).read_text(encoding="utf-8")
        )
        source_cell = next(
            cell for cell in notebook["cells"] if cell.get("id") == "s36-code"
        )
        source = notebook_adapter._patch_ci_cell(
            "s36-code",
            {
                **source_cell,
                "source": "".join(source_cell["source"]),
            },
        )["source"]
        results = [
            mock.Mock(
                returncode=0,
                stdout=json.dumps(self._hermes_mcp_status_absent()),
                stderr="",
            ),
            mock.Mock(returncode=0, stdout="", stderr=""),
            mock.Mock(returncode=1, stdout="", stderr="add failed"),
            mock.Mock(returncode=0, stdout="", stderr=""),
        ]
        credential_env = "VSS_ORCHESTRATOR_MCP_TOKEN"
        namespace = {
            "AGENT_RUNTIME": "hermes",
            "NEMOCLAW_SANDBOX_NAME": "demo",
            "NEMOCLAW_GATEWAY_NAME": "nemoclaw-19080",
            "VSS_ORCHESTRATOR_MCP_URL": "https://mcp.example.com/mcp",
            "VSS_ORCHESTRATOR_MCP_CREDENTIAL_ENV": credential_env,
        }
        public_address = (
            socket.AF_INET,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
            "",
            ("93.184.216.34", 443),
        )

        with (
            mock.patch.dict(os.environ, {credential_env: "secret"}, clear=True),
            mock.patch("socket.getaddrinfo", return_value=[public_address]),
            mock.patch("subprocess.run", side_effect=results) as run,
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaisesRegex(
                RuntimeError,
                "managed MCP registration failed",
            ),
        ):
            exec(compile(source, "s36-code", "exec"), namespace)

        self.assertEqual(
            run.call_args_list[-1].args[0],
            ["nemohermes", "demo", "shields", "up"],
        )

    def test_composed_hermes_ready_managed_mcp_is_not_mutated(self):
        notebook = json.loads(
            (
                REPO_ROOT
                / "deploy"
                / "docker"
                / "scripts"
                / "deploy_nemoclaw.ipynb"
            ).read_text(encoding="utf-8")
        )
        source_cell = next(
            cell for cell in notebook["cells"] if cell.get("id") == "s36-code"
        )
        source = notebook_adapter._patch_ci_cell(
            "s36-code",
            {
                **source_cell,
                "source": "".join(source_cell["source"]),
            },
        )["source"]
        credential_env = "VSS_ORCHESTRATOR_MCP_TOKEN"
        public_address = (
            socket.AF_INET,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
            "",
            ("93.184.216.34", 443),
        )
        namespace = {
            "AGENT_RUNTIME": "hermes",
            "NEMOCLAW_SANDBOX_NAME": "demo",
            "NEMOCLAW_GATEWAY_NAME": "nemoclaw-19080",
            "VSS_ORCHESTRATOR_MCP_URL": "https://mcp.example.com/mcp",
            "VSS_ORCHESTRATOR_MCP_CREDENTIAL_ENV": credential_env,
        }

        with (
            mock.patch.dict(os.environ, {credential_env: "secret"}, clear=True),
            mock.patch("socket.getaddrinfo", return_value=[public_address]),
            mock.patch(
                "subprocess.run",
                return_value=mock.Mock(
                    returncode=0,
                    stdout=json.dumps(
                        self._hermes_mcp_status_ready(
                            credential_env=credential_env,
                        )
                    ),
                    stderr="",
                ),
            ) as run,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            exec(compile(source, "s36-code", "exec"), namespace)

        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            [[
                "nemohermes",
                "demo",
                "mcp",
                "status",
                "vss_orchestrator",
                "--json",
                "--no-probe",
            ]],
        )

    def test_composed_hermes_partial_or_drifted_mcp_fails_closed(self):
        notebook = json.loads(
            (
                REPO_ROOT
                / "deploy"
                / "docker"
                / "scripts"
                / "deploy_nemoclaw.ipynb"
            ).read_text(encoding="utf-8")
        )
        source_cell = next(
            cell for cell in notebook["cells"] if cell.get("id") == "s36-code"
        )
        source = notebook_adapter._patch_ci_cell(
            "s36-code",
            {
                **source_cell,
                "source": "".join(source_cell["source"]),
            },
        )["source"]
        credential_env = "VSS_ORCHESTRATOR_MCP_TOKEN"
        public_address = (
            socket.AF_INET,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
            "",
            ("93.184.216.34", 443),
        )
        drifted = self._hermes_mcp_status_ready(
            url="https://other.example.com/mcp",
            credential_env=credential_env,
        )
        partial = self._hermes_mcp_status_ready(
            credential_env=credential_env,
        )
        partial["provider"]["gatewayPresent"] = False

        for label, payload in (("drifted-url", drifted), ("partial-provider", partial)):
            with self.subTest(label=label):
                namespace = {
                    "AGENT_RUNTIME": "hermes",
                    "NEMOCLAW_SANDBOX_NAME": "demo",
                    "NEMOCLAW_GATEWAY_NAME": "nemoclaw-19080",
                    "VSS_ORCHESTRATOR_MCP_URL": "https://mcp.example.com/mcp",
                    "VSS_ORCHESTRATOR_MCP_CREDENTIAL_ENV": credential_env,
                }
                with (
                    mock.patch.dict(
                        os.environ,
                        {credential_env: "secret"},
                        clear=True,
                    ),
                    mock.patch(
                        "socket.getaddrinfo",
                        return_value=[public_address],
                    ),
                    mock.patch(
                        "subprocess.run",
                        return_value=mock.Mock(
                            returncode=0,
                            stdout=json.dumps(payload),
                            stderr="",
                        ),
                    ) as run,
                    contextlib.redirect_stdout(io.StringIO()),
                    self.assertRaisesRegex(
                        RuntimeError,
                        "partial or drifted",
                    ),
                ):
                    exec(compile(source, "s36-code", "exec"), namespace)

                self.assertEqual(len(run.call_args_list), 1)
                self.assertEqual(
                    run.call_args_list[0].args[0],
                    [
                        "nemohermes",
                        "demo",
                        "mcp",
                        "status",
                        "vss_orchestrator",
                        "--json",
                        "--no-probe",
                    ],
                )

    def test_composed_hermes_mcp_validation_fails_before_commands(self):
        notebook = json.loads(
            (
                REPO_ROOT
                / "deploy"
                / "docker"
                / "scripts"
                / "deploy_nemoclaw.ipynb"
            ).read_text(encoding="utf-8")
        )
        source_cell = next(
            cell for cell in notebook["cells"] if cell.get("id") == "s36-code"
        )
        source = notebook_adapter._patch_ci_cell(
            "s36-code",
            {
                **source_cell,
                "source": "".join(source_cell["source"]),
            },
        )["source"]
        public_address = (
            socket.AF_INET,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
            "",
            ("93.184.216.34", 443),
        )
        private_address = (
            socket.AF_INET,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
            "",
            ("10.0.0.8", 443),
        )
        cases = (
            (
                "non-https",
                "http://mcp.example.com/mcp",
                "VSS_ORCHESTRATOR_MCP_TOKEN",
                {"VSS_ORCHESTRATOR_MCP_TOKEN": "secret"},
                [public_address],
                "public HTTPS URL",
            ),
            (
                "internal-hostname",
                "https://host.openshell.internal/mcp",
                "VSS_ORCHESTRATOR_MCP_TOKEN",
                {"VSS_ORCHESTRATOR_MCP_TOKEN": "secret"},
                [private_address],
                "public DNS hostname",
            ),
            (
                "private-resolution",
                "https://mcp.example.com/mcp",
                "VSS_ORCHESTRATOR_MCP_TOKEN",
                {"VSS_ORCHESTRATOR_MCP_TOKEN": "secret"},
                [private_address],
                "resolve only to public addresses",
            ),
            (
                "missing-credential",
                "https://mcp.example.com/mcp",
                "VSS_ORCHESTRATOR_MCP_TOKEN",
                {},
                [public_address],
                "empty or missing environment variable",
            ),
        )
        for name, url, credential_env, env, addresses, error in cases:
            with (
                self.subTest(case=name),
                mock.patch.dict(os.environ, env, clear=True),
                mock.patch("socket.getaddrinfo", return_value=addresses),
                mock.patch("subprocess.run") as run,
                contextlib.redirect_stdout(io.StringIO()),
                self.assertRaisesRegex(RuntimeError, error),
            ):
                exec(
                    compile(source, "s36-code", "exec"),
                    {
                        "AGENT_RUNTIME": "hermes",
                        "NEMOCLAW_SANDBOX_NAME": "demo",
                        "NEMOCLAW_GATEWAY_NAME": "nemoclaw-19080",
                        "VSS_ORCHESTRATOR_MCP_URL": url,
                        "VSS_ORCHESTRATOR_MCP_CREDENTIAL_ENV": credential_env,
                    },
                )
            run.assert_not_called()

    def test_config_preflight_patch_fails_closed_without_anchor(self):
        with self.assertRaisesRegex(
            ValueError,
            "missing the expected config anchors",
        ):
            notebook_adapter._patch_ci_cell(
                "s37-code",
                {"cell_type": "code", "source": "print('changed upstream')\n"},
            )

    def test_hooks_default_patch_fails_closed_without_anchor(self):
        with self.assertRaisesRegex(
            ValueError,
            "missing the expected hooks default",
        ):
            notebook_adapter._patch_ci_cell(
                "e67f6da4",
                {"cell_type": "code", "source": "print('changed upstream')\n"},
            )

    def test_ci_exec_patches_fail_closed_without_expected_anchors(self):
        for cell_id, message in (
            ("s35-code", "missing the expected sandbox exec"),
            ("s36-code", "missing the expected sandbox exec command"),
        ):
            with (
                self.subTest(cell_id=cell_id),
                self.assertRaisesRegex(ValueError, message),
            ):
                notebook_adapter._patch_ci_cell(
                    cell_id,
                    {
                        "cell_type": "code",
                        "source": "print('changed upstream')\n",
                    },
                )

    def test_policy_allows_supported_private_host_gateway_ranges(self):
        policy = (
            REPO_ROOT / "assets" / "vss_nemoclaw_policy.yaml"
        ).read_text(encoding="utf-8")
        host_route_count = policy.count("host: host.openshell.internal")

        self.assertGreater(host_route_count, 0)
        for private_range in (
            "- 10.0.0.0/8",
            "- 172.16.0.0/12",
            "- 192.168.0.0/16",
        ):
            self.assertEqual(policy.count(private_range), host_route_count)

    def test_brev_util_imports_without_stdlib_strenum(self):
        path = (
            REPO_ROOT
            / "services"
            / "agent"
            / "packages"
            / "vss_agents"
            / "src"
            / "vss_agents"
            / "orchestrator"
            / "brev_util.py"
        )
        spec = importlib.util.spec_from_file_location("brev_util_py310_compat", path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        original_import = builtins.__import__

        def import_without_strenum(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "enum" and "StrEnum" in fromlist:
                raise ImportError("simulated Python 3.10 enum")
            return original_import(name, globals, locals, fromlist, level)

        with mock.patch("builtins.__import__", side_effect=import_without_strenum):
            spec.loader.exec_module(module)

        self.assertEqual(module.BrevEnvKey.BREV_ENV_ID.value, "BREV_ENV_ID")


class SkillsEvalAgentProtocolTest(unittest.TestCase):
    def test_final_marker_must_be_last_nonempty_line(self):
        self.assertIsNone(
            skills_eval_agent._final_protocol_marker([
                "I will emit `DONE:` later.\n",
                "The monitor is still running.",
            ])
        )
        self.assertEqual(
            skills_eval_agent._final_protocol_marker(["analysis\n", "BLOCKED: mcp policy denied\n"]),
            "BLOCKED: mcp policy denied",
        )


class NemoClawEnvFileTest(unittest.TestCase):
    def test_readiness_commands_close_stdin(self):
        completed = subprocess.CompletedProcess(
            ["openshell", "sandbox", "get"],
            0,
            stdout="",
            stderr="",
        )
        with mock.patch.object(
            readiness.subprocess,
            "run",
            return_value=completed,
        ) as run:
            result = readiness._run(
                ["openshell", "sandbox", "get"],
                timeout=17,
            )

        self.assertIs(result, completed)
        run.assert_called_once_with(
            ["openshell", "sandbox", "get"],
            cwd=None,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=17,
        )

    def test_headless_runner_reads_hooks_token_from_token_file(self):
        with tempfile.TemporaryDirectory() as td:
            token_path = Path(td) / "hooks_token"
            token_path.write_text("secret-token\n", encoding="utf-8")
            previous = {
                "OPENCLAW_HOOKS_TOKEN": os.environ.pop("OPENCLAW_HOOKS_TOKEN", None),
                "NEMOCLAW_HOOKS_TOKEN_FILE": os.environ.get("NEMOCLAW_HOOKS_TOKEN_FILE"),
            }
            os.environ["NEMOCLAW_HOOKS_TOKEN_FILE"] = str(token_path)
            try:
                self.assertEqual(headless_runner._read_hooks_token(), "secret-token")
            finally:
                if previous["OPENCLAW_HOOKS_TOKEN"] is not None:
                    os.environ["OPENCLAW_HOOKS_TOKEN"] = previous["OPENCLAW_HOOKS_TOKEN"]
                else:
                    os.environ.pop("OPENCLAW_HOOKS_TOKEN", None)
                if previous["NEMOCLAW_HOOKS_TOKEN_FILE"] is not None:
                    os.environ["NEMOCLAW_HOOKS_TOKEN_FILE"] = previous["NEMOCLAW_HOOKS_TOKEN_FILE"]
                else:
                    os.environ.pop("NEMOCLAW_HOOKS_TOKEN_FILE", None)

    def test_readiness_env_parser_matches_shell_quoting(self):
        with tempfile.TemporaryDirectory() as td:
            env_path = Path(td) / "nemoclaw.env"
            env_path.write_text("export NEMOCLAW_SANDBOX_NAME='demo sandbox'\n", encoding="utf-8")
            previous = os.environ.pop("NEMOCLAW_SANDBOX_NAME", None)
            try:
                readiness._load_env_file(env_path)
                self.assertEqual(os.environ["NEMOCLAW_SANDBOX_NAME"], "demo sandbox")
            finally:
                if previous is not None:
                    os.environ["NEMOCLAW_SANDBOX_NAME"] = previous
                else:
                    os.environ.pop("NEMOCLAW_SANDBOX_NAME", None)

    def test_readiness_requires_gateway_health_inside_sandbox(self):
        calls: list[tuple[str, ...]] = []

        def fake_run(cmd, *, timeout=30, cwd=None):
            calls.append(tuple(cmd))
            if "get" in cmd:
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    stdout="sandbox exists",
                    stderr="",
                )
            return subprocess.CompletedProcess(
                cmd,
                7,
                stdout="",
                stderr="curl: failed to connect",
            )

        with (
            mock.patch.object(readiness.shutil, "which", return_value="/usr/bin/openshell"),
            mock.patch.object(readiness, "_run", side_effect=fake_run),
            mock.patch.object(readiness, "GATEWAY_HEALTH_ATTEMPTS", 2),
            mock.patch.object(readiness.time, "sleep") as sleep,
        ):
            report = readiness._check_sandbox("demo", "19080")

        self.assertFalse(report["ok"])
        self.assertTrue(report["lookup_ok"])
        self.assertFalse(report["gateway_ok"])
        self.assertIsNone(report["gateway_http_code"])
        self.assertEqual(report["gateway_probe_attempts"], 2)
        self.assertTrue(report["gateway_recovery_attempted"])
        self.assertEqual(report["gateway_check_method"], "")
        self.assertIn("recovery exited 7", report["gateway_stderr_tail"])
        sleep.assert_called_once_with(readiness.GATEWAY_HEALTH_RETRY_SECONDS)
        gateway_call = (
            "openshell",
            "sandbox",
            "exec",
            "--name",
            "demo",
            "-g",
            "nemoclaw-19080",
            "--",
            "curl",
            "--noproxy",
            "*",
            "-sS",
            "--connect-timeout",
            "3",
            "--max-time",
            "10",
            "-o",
            "/dev/null",
            "-w",
            "%{http_code}",
            "http://127.0.0.1:18789/health",
        )
        self.assertEqual(
            calls,
            [
                (
                    "openshell",
                    "sandbox",
                    "get",
                    "-g",
                    "nemoclaw-19080",
                    "demo",
                ),
                gateway_call,
                gateway_call,
                (
                    "nemoclaw",
                    "sandbox",
                    "recover",
                    "demo",
                ),
            ],
        )

    def test_readiness_gateway_name_uses_canonical_default_and_scoped_port(self):
        self.assertEqual(readiness._gateway_name_for_port("8080"), "nemoclaw")
        self.assertEqual(
            readiness._gateway_name_for_port("19080"),
            "nemoclaw-19080",
        )
        self.assertEqual(
            readiness._gateway_name_for_port("08080"),
            "nemoclaw",
        )

    def test_readiness_rejects_invalid_gateway_port_without_running_commands(self):
        for raw_port in ("", "not-a-port", "1023", "65536"):
            with self.subTest(raw_port=raw_port):
                with mock.patch.object(readiness, "_run") as run:
                    report = readiness._check_sandbox("demo", raw_port)

                self.assertFalse(report["ok"])
                self.assertFalse(report["lookup_ok"])
                self.assertFalse(report["gateway_ok"])
                self.assertEqual(report["error"], "gateway port is invalid")
                run.assert_not_called()

    def test_readiness_reports_missing_openshell(self):
        with (
            mock.patch.object(readiness.shutil, "which", return_value=None),
            mock.patch.object(readiness, "_run") as run,
        ):
            report = readiness._check_sandbox("demo", "19080")

        self.assertFalse(report["ok"])
        self.assertFalse(report["lookup_ok"])
        self.assertFalse(report["gateway_ok"])
        self.assertEqual(report["error"], "openshell not found")
        run.assert_not_called()

    def test_readiness_skips_gateway_probe_when_scoped_lookup_fails(self):
        with (
            mock.patch.object(readiness.shutil, "which", return_value="/usr/bin/openshell"),
            mock.patch.object(
                readiness,
                "_run",
                return_value=subprocess.CompletedProcess(
                    ["openshell", "sandbox", "get"],
                    1,
                    stdout="",
                    stderr="sandbox not found",
                ),
            ) as run,
        ):
            report = readiness._check_sandbox("demo", "19080")

        self.assertFalse(report["ok"])
        self.assertFalse(report["lookup_ok"])
        self.assertFalse(report["gateway_ok"])
        self.assertIn("not found", report["stderr_tail"])
        run.assert_called_once_with(
            ["openshell", "sandbox", "get", "-g", "nemoclaw-19080", "demo"],
            timeout=60,
        )

    def test_readiness_accepts_gateway_alive_http_codes_inside_sandbox(self):
        for http_code in ("200", "401"):
            with self.subTest(http_code=http_code):
                responses = [
                    subprocess.CompletedProcess(
                        ["openshell", "sandbox", "get"],
                        0,
                        stdout="sandbox exists",
                        stderr="",
                    ),
                    subprocess.CompletedProcess(
                        ["openshell", "sandbox", "exec"],
                        0,
                        stdout=http_code,
                        stderr="",
                    ),
                ]
                with (
                    mock.patch.object(
                        readiness.shutil,
                        "which",
                        return_value="/usr/bin/openshell",
                    ),
                    mock.patch.object(
                        readiness,
                        "_run",
                        side_effect=responses,
                    ) as run,
                ):
                    report = readiness._check_sandbox("demo", "8080")

                self.assertTrue(report["ok"])
                self.assertTrue(report["lookup_ok"])
                self.assertTrue(report["gateway_ok"])
                self.assertEqual(report["gateway_http_code"], int(http_code))
                self.assertEqual(report["gateway_probe_attempts"], 1)
                self.assertFalse(report["gateway_recovery_attempted"])
                self.assertEqual(report["gateway_check_method"], "scoped_http")
                self.assertEqual(run.call_count, 2)
                self.assertEqual(
                    run.call_args_list[1],
                    mock.call(
                        [
                            "openshell",
                            "sandbox",
                            "exec",
                            "--name",
                            "demo",
                            "-g",
                            "nemoclaw",
                            "--",
                            "curl",
                            "--noproxy",
                            "*",
                            "-sS",
                            "--connect-timeout",
                            "3",
                            "--max-time",
                            "10",
                            "-o",
                            "/dev/null",
                            "-w",
                            "%{http_code}",
                            "http://127.0.0.1:18789/health",
                        ],
                        timeout=30,
                    ),
                )

    def test_readiness_retries_gateway_health_until_alive(self):
        responses = [
            subprocess.CompletedProcess(
                ["openshell", "sandbox", "get"],
                0,
                stdout="sandbox exists",
                stderr="",
            ),
            subprocess.CompletedProcess(
                ["openshell", "sandbox", "exec"],
                0,
                stdout="503",
                stderr="",
            ),
            subprocess.CompletedProcess(
                ["openshell", "sandbox", "exec"],
                0,
                stdout="401",
                stderr="",
            ),
        ]
        with (
            mock.patch.object(readiness.shutil, "which", return_value="/usr/bin/openshell"),
            mock.patch.object(readiness, "_run", side_effect=responses),
            mock.patch.object(readiness.time, "sleep") as sleep,
        ):
            report = readiness._check_sandbox("demo", "19080")

        self.assertTrue(report["ok"])
        self.assertEqual(report["gateway_http_code"], 401)
        self.assertEqual(report["gateway_probe_attempts"], 2)
        self.assertFalse(report["gateway_recovery_attempted"])
        self.assertEqual(report["gateway_check_method"], "scoped_http")
        sleep.assert_called_once_with(readiness.GATEWAY_HEALTH_RETRY_SECONDS)

    def test_readiness_accepts_managed_recovery_when_scoped_http_is_unreachable(self):
        responses = [
            subprocess.CompletedProcess(
                ["openshell", "sandbox", "get"],
                0,
                stdout="sandbox exists",
                stderr="",
            ),
            subprocess.CompletedProcess(
                ["openshell", "sandbox", "exec"],
                7,
                stdout="000",
                stderr="curl: failed to connect",
            ),
            subprocess.CompletedProcess(
                ["nemoclaw", "sandbox", "recover"],
                0,
                stdout="Probe complete: OpenClaw gateway is running.",
                stderr="",
            ),
        ]
        with (
            mock.patch.object(readiness.shutil, "which", return_value="/usr/bin/openshell"),
            mock.patch.object(readiness, "_run", side_effect=responses) as run,
            mock.patch.object(readiness, "GATEWAY_HEALTH_ATTEMPTS", 1),
        ):
            report = readiness._check_sandbox("demo", "19080")

        self.assertTrue(report["ok"])
        self.assertTrue(report["lookup_ok"])
        self.assertTrue(report["gateway_ok"])
        self.assertIsNone(report["gateway_http_code"])
        self.assertEqual(report["gateway_probe_attempts"], 1)
        self.assertTrue(report["gateway_recovery_attempted"])
        self.assertEqual(report["gateway_check_method"], "managed_recover")
        run.assert_called_with(
            ["nemoclaw", "sandbox", "recover", "demo"],
            timeout=readiness.GATEWAY_RECOVERY_TIMEOUT_SECONDS,
        )

    def test_readiness_reports_managed_recovery_timeout(self):
        sandbox_result = subprocess.CompletedProcess(
            ["openshell", "sandbox", "get"],
            0,
            stdout="sandbox exists",
            stderr="",
        )
        with (
            mock.patch.object(readiness.shutil, "which", return_value="/usr/bin/openshell"),
            mock.patch.object(
                readiness,
                "_run",
                side_effect=[
                    sandbox_result,
                    subprocess.TimeoutExpired(["openshell"], 30),
                    subprocess.TimeoutExpired(
                        ["nemoclaw", "sandbox", "recover"],
                        readiness.GATEWAY_RECOVERY_TIMEOUT_SECONDS,
                    ),
                ],
            ),
            mock.patch.object(readiness, "GATEWAY_HEALTH_ATTEMPTS", 1),
        ):
            report = readiness._check_sandbox("demo", "19080")

        self.assertFalse(report["ok"])
        self.assertTrue(report["lookup_ok"])
        self.assertFalse(report["gateway_ok"])
        self.assertTrue(report["gateway_recovery_attempted"])
        self.assertIn(
            f"timed out after {readiness.GATEWAY_RECOVERY_TIMEOUT_SECONDS}s",
            report["gateway_stderr_tail"],
        )

    def test_readiness_reports_scoped_lookup_timeout(self):
        with (
            mock.patch.object(readiness.shutil, "which", return_value="/usr/bin/openshell"),
            mock.patch.object(
                readiness,
                "_run",
                side_effect=subprocess.TimeoutExpired(["openshell"], 60),
            ) as run,
        ):
            report = readiness._check_sandbox("demo", "19080")

        self.assertFalse(report["ok"])
        self.assertFalse(report["lookup_ok"])
        self.assertFalse(report["gateway_ok"])
        self.assertIn("timed out after 60s", report["stderr_tail"])
        run.assert_called_once()

    def test_readiness_discovers_required_mcp_tools_inside_sandbox(self):
        output = json.dumps(
            {
                "mode": "server",
                "name": "vss_orchestrator",
                "status": "ok",
                "tools": [
                    {"name": "profiles"},
                    {"name": "docker_status"},
                ]
            }
        )
        with (
            mock.patch.object(readiness.shutil, "which", return_value="/usr/bin/openshell"),
            mock.patch.object(
                readiness,
                "_run",
                return_value=subprocess.CompletedProcess(
                    ["openshell"],
                    0,
                    stdout=output,
                    stderr="",
                ),
            ) as run,
        ):
            report = readiness._check_sandbox_mcp(
                "demo",
                "19080",
                [
                    "vss_orchestrator__profiles",
                    "vss_orchestrator__docker_status",
                ],
            )

        self.assertTrue(report["ok"])
        self.assertEqual(report["missing_tools"], [])
        self.assertEqual(
            report["discovered_tools"],
            ["docker_status", "profiles"],
        )
        run.assert_called_once_with(
            [
                "openshell",
                "sandbox",
                "exec",
                "--name",
                "demo",
                "-g",
                "nemoclaw-19080",
                "--",
                "mcporter",
                "list",
                "vss_orchestrator",
                "--json",
            ],
            timeout=90,
        )

    def test_readiness_mcp_rejects_invalid_gateway_without_running_commands(self):
        with (
            mock.patch.object(readiness.shutil, "which") as which,
            mock.patch.object(readiness, "_run") as run,
        ):
            report = readiness._check_sandbox_mcp(
                "demo",
                "not-a-port",
                ["vss_orchestrator__profiles"],
            )

        self.assertFalse(report["ok"])
        self.assertEqual(report["error"], "gateway port is invalid")
        self.assertEqual(
            report["error_category"],
            "sandbox_mcp_unavailable",
        )
        which.assert_not_called()
        run.assert_not_called()

    def test_readiness_mcp_requires_openshell_for_scoped_routing(self):
        with (
            mock.patch.object(readiness.shutil, "which", return_value=None),
            mock.patch.object(readiness, "_run") as run,
        ):
            report = readiness._check_sandbox_mcp(
                "demo",
                "19080",
                ["vss_orchestrator__profiles"],
            )

        self.assertFalse(report["ok"])
        self.assertEqual(report["error"], "openshell not found")
        self.assertEqual(
            report["error_category"],
            "sandbox_mcp_unavailable",
        )
        run.assert_not_called()

    def test_readiness_rejects_sandbox_mcp_policy_denial(self):
        with (
            mock.patch.object(readiness.shutil, "which", return_value="/usr/bin/openshell"),
            mock.patch.object(
                readiness,
                "_run",
                return_value=subprocess.CompletedProcess(
                    ["openshell"],
                    1,
                    stdout="",
                    stderr="HTTP 403: ssrf_denied",
                ),
            ),
        ):
            report = readiness._check_sandbox_mcp(
                "demo",
                "19080",
                ["vss_orchestrator__profiles"],
            )

        self.assertFalse(report["ok"])
        self.assertEqual(report["returncode"], 1)
        self.assertIn("ssrf_denied", report["stderr_tail"])

    def test_readiness_rejects_missing_sandbox_mcp_tools(self):
        with (
            mock.patch.object(readiness.shutil, "which", return_value="/usr/bin/openshell"),
            mock.patch.object(
                readiness,
                "_run",
                return_value=subprocess.CompletedProcess(
                    ["openshell"],
                    0,
                    stdout=(
                        '{"mode":"server","name":"vss_orchestrator",'
                        '"status":"ok","tools":[]}'
                    ),
                    stderr="",
                ),
            ),
        ):
            report = readiness._check_sandbox_mcp(
                "demo",
                "19080",
                ["vss_orchestrator__profiles"],
            )

        self.assertFalse(report["ok"])
        self.assertEqual(
            report["missing_tools"],
            ["vss_orchestrator__profiles"],
        )

    def test_readiness_requires_exact_sandbox_mcp_tool_names(self):
        with (
            mock.patch.object(readiness.shutil, "which", return_value="/usr/bin/openshell"),
            mock.patch.object(
                readiness,
                "_run",
                return_value=subprocess.CompletedProcess(
                    ["openshell"],
                    0,
                    stdout=(
                        '{"mode":"server","name":"vss_orchestrator",'
                        '"status":"ok",'
                        '"tools":[{"name":"profiles_extended"}]}'
                    ),
                    stderr="",
                ),
            ),
        ):
            report = readiness._check_sandbox_mcp(
                "demo",
                "19080",
                ["vss_orchestrator__profiles"],
            )

        self.assertFalse(report["ok"])
        self.assertEqual(
            report["error_category"],
            "sandbox_mcp_missing_required_tools",
        )
        self.assertEqual(
            report["missing_tools"],
            ["vss_orchestrator__profiles"],
        )

    def test_readiness_rejects_invalid_sandbox_mcp_json(self):
        with (
            mock.patch.object(readiness.shutil, "which", return_value="/usr/bin/openshell"),
            mock.patch.object(
                readiness,
                "_run",
                return_value=subprocess.CompletedProcess(
                    ["openshell"],
                    0,
                    stdout="not-json",
                    stderr="",
                ),
            ),
        ):
            report = readiness._check_sandbox_mcp(
                "demo",
                "19080",
                ["vss_orchestrator__profiles"],
            )

        self.assertFalse(report["ok"])
        self.assertEqual(
            report["error_category"],
            "sandbox_mcp_invalid_response",
        )

    def test_readiness_safe_summary_excludes_raw_diagnostics(self):
        raw_secret = "sk-readiness-secret"
        report = {
            "commands": [
                {"name": name, "ok": True, "path": f"/secret/{raw_secret}"}
                for name in readiness.COMMANDS
            ],
            "sandbox": {
                "ok": True,
                "lookup_ok": True,
                "gateway_ok": True,
                "stderr_tail": raw_secret,
            },
            "mcp": {"ok": False, "message": raw_secret},
            "sandbox_mcp": {
                "ok": False,
                "returncode": 0,
                "error_category": "sandbox_mcp_missing_required_tools",
                "missing_tools": [
                    "vss_orchestrator__profiles",
                    raw_secret,
                ],
                "stdout_tail": raw_secret,
            },
            "ok": False,
        }

        summary = readiness._build_safe_summary(report)
        encoded = json.dumps(summary, sort_keys=True)

        self.assertNotIn(raw_secret, encoded)
        self.assertEqual(
            summary["categories"],
            [
                "host_mcp_unhealthy",
                "sandbox_mcp_missing_required_tools",
            ],
        )
        self.assertEqual(
            summary["sandbox_mcp"]["missing_required_tool_count"],
            2,
        )
        self.assertTrue(summary["sandbox"]["lookup_ok"])
        self.assertNotIn("missing_required_tools", summary["sandbox_mcp"])

    def test_readiness_safe_summary_separates_lookup_and_gateway_failures(self):
        base_report = {
            "commands": [
                {"name": name, "ok": True}
                for name in readiness.COMMANDS
            ],
            "mcp": {"ok": True},
            "sandbox_mcp": {"ok": True, "returncode": 0},
            "ok": False,
        }

        lookup_failure = readiness._build_safe_summary(
            {
                **base_report,
                "sandbox": {
                    "ok": False,
                    "lookup_ok": False,
                    "gateway_ok": False,
                },
            }
        )
        gateway_failure = readiness._build_safe_summary(
            {
                **base_report,
                "sandbox": {
                    "ok": False,
                    "lookup_ok": True,
                    "gateway_ok": False,
                },
            }
        )

        self.assertEqual(
            lookup_failure["categories"],
            ["sandbox_unavailable"],
        )
        self.assertEqual(
            gateway_failure["categories"],
            ["sandbox_gateway_unhealthy"],
        )

    def test_readiness_rejects_wrong_mcporter_server_envelope(self):
        output = json.dumps(
            {
                "mode": "server",
                "name": "different_server",
                "status": "ok",
                "tools": [{"name": "profiles"}],
            }
        )
        with (
            mock.patch.object(readiness.shutil, "which", return_value="/usr/bin/openshell"),
            mock.patch.object(
                readiness,
                "_run",
                return_value=subprocess.CompletedProcess(
                    ["openshell"],
                    0,
                    stdout=output,
                    stderr="",
                ),
            ),
        ):
            report = readiness._check_sandbox_mcp(
                "demo",
                "19080",
                ["vss_orchestrator__profiles"],
            )

        self.assertFalse(report["ok"])
        self.assertEqual(
            report["error_category"],
            "sandbox_mcp_invalid_response",
        )

    def test_readiness_json_writer_refuses_symlinked_output(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "target.json"
            target.write_text("sentinel\n", encoding="utf-8")
            output = Path(td) / "readiness.json"
            output.symlink_to(target)

            with self.assertRaisesRegex(
                RuntimeError,
                "refusing existing readiness output",
            ):
                readiness._write_json(output, {"ok": True})

            self.assertEqual(target.read_text(encoding="utf-8"), "sentinel\n")

    def test_readiness_main_emits_and_writes_only_safe_summary(self):
        raw_secret = "sk-readiness-secret"
        with tempfile.TemporaryDirectory() as td:
            raw_output = Path(td) / "readiness.json"
            summary_output = Path(td) / "readiness-summary.json"
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                mock.patch.object(
                    readiness,
                    "_check_cmd",
                    side_effect=lambda name: {
                        "name": name,
                        "ok": True,
                        "path": f"/secret/{raw_secret}",
                    },
                ),
                mock.patch.object(
                    readiness,
                    "_check_sandbox",
                    return_value={
                        "ok": True,
                        "lookup_ok": True,
                        "gateway_ok": True,
                        "stderr_tail": raw_secret,
                    },
                ),
                mock.patch.object(
                    readiness,
                    "_check_mcp",
                    return_value={"ok": False, "message": raw_secret},
                ),
                mock.patch.object(
                    readiness,
                    "_check_sandbox_mcp",
                    return_value={
                        "ok": False,
                        "returncode": 0,
                        "error_category": "sandbox_mcp_missing_required_tools",
                        "missing_tools": [raw_secret],
                        "stdout_tail": raw_secret,
                    },
                ),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                return_code = readiness.main(
                    [
                        "--env-file",
                        str(Path(td) / "missing.env"),
                        "--required-tools",
                        "vss_orchestrator__profiles",
                        "--output",
                        str(raw_output),
                        "--summary-output",
                        str(summary_output),
                    ]
                )

            self.assertEqual(return_code, 1)
            self.assertIn(raw_secret, raw_output.read_text(encoding="utf-8"))
            summary_text = summary_output.read_text(encoding="utf-8")
            self.assertNotIn(raw_secret, summary_text)
            self.assertNotIn(raw_secret, stdout.getvalue())
            self.assertNotIn(raw_secret, stderr.getvalue())
            self.assertIn("NemoClaw readiness failed", stderr.getvalue())
            self.assertEqual(summary_output.stat().st_mode & 0o777, 0o600)


class NemoClawHeadlessRunnerTest(unittest.TestCase):
    @staticmethod
    def _rtsp_tool_trajectory(
        *,
        command=headless_runner.RTSP_TOOL_ENV_PROBE_COMMAND,
        function_name="tool_call",
        tool_id=headless_runner.RTSP_TOOL_ID,
        envelope_tool_id=headless_runner.RTSP_TOOL_ID,
        status="completed",
        exit_code=0,
        text=headless_runner.RTSP_TOOL_ENV_READY_SENTINEL,
        aggregated=headless_runner.RTSP_TOOL_ENV_READY_SENTINEL,
    ):
        envelope = {
            "tool": {"id": envelope_tool_id},
            "result": {
                "content": [{"type": "text", "text": text}],
                "details": {
                    "status": status,
                    "exitCode": exit_code,
                    "aggregated": aggregated,
                },
            },
        }
        return {
            "steps": [
                {
                    "source": "agent",
                    "tool_calls": [
                        {
                            "tool_call_id": "exec-1",
                            "function_name": function_name,
                            "arguments": {
                                "id": tool_id,
                                "args": {"command": command},
                            },
                        }
                    ],
                    "observation": {
                        "results": [
                            {
                                "source_call_id": "exec-1",
                                "content": json.dumps(envelope),
                            }
                        ]
                    },
                }
            ]
        }

    def test_runtime_redaction_scrubs_derived_rtsp_endpoint_components(self):
        rtsp_url = notebook_adapter.DEFAULT_RTSP_SAMPLE_URL
        raw = "\n".join(
            (
                f"exact={rtsp_url}",
                "host-port=global.stg.ga.launchpad.nvidia.com:11333",
                "host=global.stg.ga.launchpad.nvidia.com",
                "label=launchpad",
                "path=/camera03 segment=camera03",
                "other=rtsp://127.0.0.1:8554/live/example",
                r"escaped=rtsp:\/\/127.0.0.1:8554\/live\/example",
            )
        )

        with mock.patch.dict(
            os.environ,
            {"RTSP_SAMPLE_URL": rtsp_url},
            clear=False,
        ):
            redacted = headless_runner._redact_runtime_text(raw)

        self.assertIn(headless_runner.RTSP_EXACT_REDACTION, redacted)
        self.assertIn(headless_runner.RTSP_URI_REDACTION, redacted)
        for sensitive_fragment in (
            rtsp_url,
            "global.stg.ga.launchpad.nvidia.com:11333",
            "global.stg.ga.launchpad.nvidia.com",
            "launchpad",
            "/camera03",
            "camera03",
            "rtsp://",
            r"rtsp:\/\/",
        ):
            self.assertNotIn(sensitive_fragment, redacted)

    def test_runtime_redaction_scrubs_encoded_rtsp_components(self):
        rtsp_url = (
            "rtsp://media.example.test:8554/"
            "live%2Fcamera03?profile=eval%2Fprimary#fragment%2Fcamera"
        )
        raw = "\n".join(
            (
                '{"type":"message","message":{"role":"user"}}',
                f"exact={rtsp_url}",
                "path=live%2Fcamera03 decoded=live/camera03",
                "query=eval%2Fprimary decoded=eval/primary",
                "fragment=fragment%2Fcamera decoded=fragment/camera",
                "port=8554 common_path=/stream",
            )
        )

        with mock.patch.dict(
            os.environ,
            {"RTSP_SAMPLE_URL": rtsp_url},
            clear=False,
        ):
            redacted = headless_runner._redact_runtime_text(raw)

        self.assertIn(headless_runner.RTSP_EXACT_REDACTION, redacted)
        self.assertIn('"role":"user"', redacted)
        self.assertIn("port=8554 common_path=/stream", redacted)
        for sensitive_fragment in (
            rtsp_url,
            "live%2Fcamera03",
            "live/camera03",
            "eval%2Fprimary",
            "eval/primary",
            "fragment%2Fcamera",
            "fragment/camera",
        ):
            self.assertNotIn(sensitive_fragment, redacted)

    def test_non_json_hook_response_is_not_treated_as_success(self):
        self.assertFalse(headless_runner._response_ok({"status": 200, "body": "ok"}))
        self.assertTrue(headless_runner._response_ok({"status": 200, "body": {"ok": True}}))

    def test_openclaw_cli_uses_a_fresh_session_id_per_invocation(self):
        with mock.patch.dict(
            os.environ,
            {"GITHUB_RUN_ID": "30311388211"},
            clear=False,
        ):
            first = headless_runner._openclaw_cli_command("prompt", 60)
            second = headless_runner._openclaw_cli_command("prompt", 60)

        first_args = shlex.split(first)
        second_args = shlex.split(second)
        first_session = first_args[first_args.index("--session-id") + 1]
        second_session = second_args[second_args.index("--session-id") + 1]
        self.assertRegex(
            first_session,
            r"^30311388211-[0-9a-f]{32}$",
        )
        self.assertNotEqual(first_session, second_session)

    def test_healthy_dashboard_forward_is_kept_even_if_registry_is_empty(self):
        calls: list[tuple[str, ...]] = []
        previous = {
            "_dashboard_healthy": headless_runner._dashboard_healthy,
            "_forward_running": headless_runner._forward_running,
            "_run": headless_runner._run,
        }

        def fake_run(cmd, *, timeout=30):
            calls.append(tuple(cmd))
            raise AssertionError("ensure_forward should not restart a healthy dashboard")

        headless_runner._dashboard_healthy = lambda port: True
        headless_runner._forward_running = lambda port, sandbox: False
        headless_runner._run = fake_run
        try:
            headless_runner.ensure_forward("18789", "demo")
        finally:
            headless_runner._dashboard_healthy = previous["_dashboard_healthy"]
            headless_runner._forward_running = previous["_forward_running"]
            headless_runner._run = previous["_run"]

        self.assertEqual(calls, [])

    def test_forward_failure_writes_structured_report(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            prompt = root / "prompt.md"
            log_dir = root / "logs"
            prompt.write_text("deploy base", encoding="utf-8")
            previous = {
                "OPENCLAW_HOOKS_TOKEN": os.environ.get("OPENCLAW_HOOKS_TOKEN"),
                "NEMOCLAW_HOOKS_TOKEN_FILE": os.environ.get("NEMOCLAW_HOOKS_TOKEN_FILE"),
                "ensure_forward": headless_runner.ensure_forward,
            }
            os.environ["OPENCLAW_HOOKS_TOKEN"] = "token"
            os.environ.pop("NEMOCLAW_HOOKS_TOKEN_FILE", None)
            headless_runner.ensure_forward = lambda port, sandbox: (_ for _ in ()).throw(RuntimeError("forward down"))
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    rc = headless_runner.main([
                        "--prompt-file",
                        str(prompt),
                        "--log-dir",
                        str(log_dir),
                    ])
            finally:
                headless_runner.ensure_forward = previous["ensure_forward"]
                if previous["OPENCLAW_HOOKS_TOKEN"] is None:
                    os.environ.pop("OPENCLAW_HOOKS_TOKEN", None)
                else:
                    os.environ["OPENCLAW_HOOKS_TOKEN"] = previous["OPENCLAW_HOOKS_TOKEN"]
                if previous["NEMOCLAW_HOOKS_TOKEN_FILE"] is None:
                    os.environ.pop("NEMOCLAW_HOOKS_TOKEN_FILE", None)
                else:
                    os.environ["NEMOCLAW_HOOKS_TOKEN_FILE"] = previous["NEMOCLAW_HOOKS_TOKEN_FILE"]

            report = json.loads((log_dir / "nemoclaw_hooks_response.json").read_text(encoding="utf-8"))

        self.assertEqual(rc, 1)
        self.assertEqual(report["response"]["error_type"], "RuntimeError")
        self.assertIn("forward down", report["response"]["error"])

    def test_missing_prompt_file_writes_structured_report(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_dir = root / "logs"
            missing_prompt = root / "missing.md"
            previous = {
                "OPENCLAW_HOOKS_TOKEN": os.environ.get("OPENCLAW_HOOKS_TOKEN"),
                "NEMOCLAW_HOOKS_TOKEN_FILE": os.environ.get("NEMOCLAW_HOOKS_TOKEN_FILE"),
            }
            os.environ["OPENCLAW_HOOKS_TOKEN"] = "token"
            os.environ.pop("NEMOCLAW_HOOKS_TOKEN_FILE", None)
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    rc = headless_runner.main([
                        "--prompt-file",
                        str(missing_prompt),
                        "--log-dir",
                        str(log_dir),
                    ])
            finally:
                if previous["OPENCLAW_HOOKS_TOKEN"] is None:
                    os.environ.pop("OPENCLAW_HOOKS_TOKEN", None)
                else:
                    os.environ["OPENCLAW_HOOKS_TOKEN"] = previous["OPENCLAW_HOOKS_TOKEN"]
                if previous["NEMOCLAW_HOOKS_TOKEN_FILE"] is None:
                    os.environ.pop("NEMOCLAW_HOOKS_TOKEN_FILE", None)
                else:
                    os.environ["NEMOCLAW_HOOKS_TOKEN_FILE"] = previous["NEMOCLAW_HOOKS_TOKEN_FILE"]

            report = json.loads((log_dir / "nemoclaw_hooks_response.json").read_text(encoding="utf-8"))

        self.assertEqual(rc, 1)
        self.assertEqual(report["response"]["error_type"], "FileNotFoundError")
        self.assertIn("missing.md", report["response"]["error"])

    def test_expected_skill_rejects_stale_prompt(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_dir = root / "logs"
            prompt = root / "prompt.md"
            prompt.write_text(
                "Use the `/vss-generate-video-report` skill for this task.",
                encoding="utf-8",
            )

            with contextlib.redirect_stdout(io.StringIO()):
                rc = headless_runner.main([
                    "--prompt-file",
                    str(prompt),
                    "--log-dir",
                    str(log_dir),
                    "--expected-skill",
                    "vss-deploy-dense-captioning",
                    "--launch-mode",
                    "cli",
                ])

            report = json.loads((log_dir / "nemoclaw_hooks_response.json").read_text(encoding="utf-8"))

        self.assertEqual(rc, 1)
        self.assertEqual(report["response"]["error_type"], "RuntimeError")
        self.assertIn("does not reference expected skill", report["response"]["error"])

    def test_rtsp_attestation_requires_exact_exec_tool_envelope(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "trajectory.json"
            path.write_text(
                json.dumps(self._rtsp_tool_trajectory()),
                encoding="utf-8",
            )
            headless_runner._assert_rtsp_tool_shell_visibility(path)

            invalid_cases = {
                "command-bypass": self._rtsp_tool_trajectory(
                    command=(
                        'test -n "${RTSP_SAMPLE_URL:-}" || true; '
                        "printf 'RTSP_SAMPLE_URL is set\\n'"
                    )
                ),
                "command-suffix": self._rtsp_tool_trajectory(
                    command=(
                        headless_runner.RTSP_TOOL_ENV_PROBE_COMMAND + "; true"
                    )
                ),
                "wrong-function": self._rtsp_tool_trajectory(
                    function_name="exec"
                ),
                "wrong-tool": self._rtsp_tool_trajectory(
                    tool_id="openclaw:core:process"
                ),
                "wrong-envelope-tool": self._rtsp_tool_trajectory(
                    envelope_tool_id="openclaw:core:process"
                ),
                "running": self._rtsp_tool_trajectory(status="running"),
                "nonzero": self._rtsp_tool_trajectory(exit_code=1),
                "float-zero": self._rtsp_tool_trajectory(exit_code=0.0),
                "text-prefix": self._rtsp_tool_trajectory(
                    text="prefix RTSP_SAMPLE_URL is set"
                ),
                "aggregate-suffix": self._rtsp_tool_trajectory(
                    aggregated="RTSP_SAMPLE_URL is set suffix"
                ),
            }
            late_valid = self._rtsp_tool_trajectory(command="true")
            late_valid["steps"].extend(
                self._rtsp_tool_trajectory()["steps"]
            )
            invalid_cases["valid-probe-after-first-exec"] = late_valid
            env_bootstrap_first = self._rtsp_tool_trajectory(
                command="cat /sandbox/.openclaw/workspace/ENV.md"
            )
            env_bootstrap_first["steps"].extend(
                self._rtsp_tool_trajectory()["steps"]
            )
            invalid_cases["valid-probe-after-env-bootstrap"] = (
                env_bootstrap_first
            )
            direct_exec_first = self._rtsp_tool_trajectory()
            direct_exec_first["steps"].insert(
                0,
                {
                    "source": "agent",
                    "tool_calls": [
                        {
                            "tool_call_id": "direct-exec-1",
                            "function_name": "exec",
                            "arguments": {"command": "true"},
                        }
                    ],
                    "observation": {"results": []},
                },
            )
            invalid_cases["valid-probe-after-direct-exec"] = (
                direct_exec_first
            )
            for name, trajectory in invalid_cases.items():
                with self.subTest(name=name):
                    path.write_text(json.dumps(trajectory), encoding="utf-8")
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "first exec tool call|actual exec tool shell",
                    ):
                        headless_runner._assert_rtsp_tool_shell_visibility(path)

            tool_search_then_probe = self._rtsp_tool_trajectory()
            tool_search_then_probe["steps"].insert(
                0,
                {
                    "source": "agent",
                    "tool_calls": [
                        {
                            "tool_call_id": "tool-search-1",
                            "function_name": "tool_search",
                            "arguments": {"query": "OpenClaw exec tool"},
                        }
                    ],
                    "observation": {"results": []},
                },
            )
            path.write_text(
                json.dumps(tool_search_then_probe),
                encoding="utf-8",
            )
            headless_runner._assert_rtsp_tool_shell_visibility(path)

    def test_dense_cli_main_requires_attestation_without_runtime_argument(self):
        captured: dict[str, object] = {}

        def fake_run_openclaw(
            sandbox,
            message,
            timeout,
            logs,
            wait_profile="",
            deadline=None,
        ):
            captured["sandbox"] = sandbox
            return {
                "status": 200,
                "body": {"ok": True},
                "stdout_tail": "",
                "stderr_tail": "",
            }

        def fake_collect(_sandbox, logs, *_args, **_kwargs):
            (logs / "trajectory.json").write_text(
                json.dumps(self._rtsp_tool_trajectory()),
                encoding="utf-8",
            )
            return {"trajectory_steps": 2}

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            prompt = root / "prompt.md"
            prompt.write_text(
                "Use the `/vss-deploy-dense-captioning` skill.",
                encoding="utf-8",
            )
            log_dir = root / "logs"
            with (
                mock.patch.object(
                    headless_runner,
                    "run_openclaw_cli",
                    side_effect=fake_run_openclaw,
                ),
                mock.patch.object(
                    headless_runner,
                    "wait_for_profile",
                    return_value={"waited": False},
                ),
                mock.patch.object(
                    headless_runner,
                    "stop_openclaw_cli",
                    return_value=None,
                ),
                mock.patch.object(
                    headless_runner,
                    "collect_openclaw_cli_log",
                    return_value=None,
                ),
                mock.patch.object(
                    headless_runner,
                    "collect_and_publish_openclaw_trajectory",
                    side_effect=fake_collect,
                ),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                rc = headless_runner.main(
                    [
                        "--prompt-file",
                        str(prompt),
                        "--log-dir",
                        str(log_dir),
                        "--launch-mode",
                        "cli",
                        "--expected-skill",
                        "vss-deploy-dense-captioning",
                    ]
                )

        self.assertEqual(rc, 0)
        self.assertEqual(captured["sandbox"], "demo")

    def test_cli_launch_runs_openclaw_agent_inside_sandbox(self):
        calls: list[tuple[str, ...]] = []
        previous = {
            "_run": headless_runner._run,
            "ensure_openclaw_gateway": headless_runner.ensure_openclaw_gateway,
        }

        def fake_run(cmd, *, timeout=30):
            call_index = len(calls)
            calls.append(tuple(cmd))
            if call_index == 1:
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    stdout=(
                        f"{headless_runner.OPENCLAW_STATE_PREFIX}stopped\n"
                        f"{headless_runner.OPENCLAW_RC_PREFIX}0\n"
                        f"{headless_runner.OPENCLAW_LOG_BEGIN}\n"
                        '{"result":{"payloads":[{"text":"done"}]}}'
                    ),
                    stderr="",
                )
            return subprocess.CompletedProcess(cmd, 0, stdout="started", stderr="")

        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td)
            headless_runner._run = fake_run
            headless_runner.ensure_openclaw_gateway = (
                lambda sandbox, logs, deadline=None: None
            )
            try:
                response = headless_runner.run_openclaw_cli(
                    "demo",
                    "Deploy base",
                    30,
                    log_dir,
                )
            finally:
                headless_runner._run = previous["_run"]
                headless_runner.ensure_openclaw_gateway = previous[
                    "ensure_openclaw_gateway"
                ]

            launch_log = (log_dir / "openclaw-launch.log").read_text(encoding="utf-8")

        self.assertEqual(response["status"], 200)
        self.assertEqual(response["body"]["mode"], "cli")
        self.assertEqual(response["body"]["returncode"], 0)
        self.assertTrue(any("base64 -d" in " ".join(call) for call in calls))
        self.assertIn("mode=blocking-poll", launch_log)
        self.assertIn("completed=true", launch_log)
        wrapper = next(call[-1] for call in calls if "base64 -d" in " ".join(call))
        encoded = wrapper.split("printf %s ", 1)[1].split(" | base64 -d", 1)[0].strip("'")
        script = base64.b64decode(encoded).decode("utf-8")
        self.assertIn("echo started", script)
        self.assertIn("openclaw-agent.rc", script)
        self.assertIn("openclaw-agent.rc.tmp", script)
        self.assertIn("mv /tmp/vss-skill-eval-openclaw/openclaw-agent.rc.tmp", script)
        self.assertNotIn("while kill -0", script)
        self.assertIn("TERM INT HUP", script)
        self.assertIn("while :; do sleep 60; done", script)
        self.assertNotIn('exit "$rc"', script)
        self.assertIn("--message", script)
        self.assertNotIn("--local", script)
        self.assertIn("--json", script)
        self.assertIn("--thinking off", script)
        self.assertNotIn("--thinking medium", script)
        self.assertIn("OPENCLAW_DISABLE_STREAMING_TOOL_CALLS=1", script)
        self.assertIn("NO_PROXY=localhost,127.0.0.1,::1,10.200.0.1", script)
        no_proxy_exports = [
            part
            for part in script.split("; ")
            if part.startswith(("export NO_PROXY=", "export no_proxy="))
        ]
        self.assertEqual(len(no_proxy_exports), 2)
        self.assertTrue(all("host.openshell.internal" not in part for part in no_proxy_exports))

    def test_cli_timeout_budget_includes_gateway_startup(self):
        calls: list[str] = []
        previous = {
            "_start_openclaw_cli_async": headless_runner._start_openclaw_cli_async,
            "_openclaw_cli_snapshot": headless_runner._openclaw_cli_snapshot,
            "monotonic": headless_runner.time.monotonic,
        }
        now = iter([100.0, 161.0])

        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td)
            headless_runner._start_openclaw_cli_async = (
                lambda sandbox, prompt, timeout, logs, deadline=None: {
                    "status": 200,
                    "body": {"ok": True},
                    "stdout_tail": "",
                    "stderr_tail": "",
                }
            )
            headless_runner._openclaw_cli_snapshot = (
                lambda sandbox, logs, deadline=None: calls.append("snapshot")
            )
            headless_runner.time.monotonic = lambda: next(now)
            try:
                response = headless_runner.run_openclaw_cli(
                    "demo",
                    "Deploy base",
                    60,
                    log_dir,
                )
            finally:
                headless_runner._start_openclaw_cli_async = previous[
                    "_start_openclaw_cli_async"
                ]
                headless_runner._openclaw_cli_snapshot = previous[
                    "_openclaw_cli_snapshot"
                ]
                headless_runner.time.monotonic = previous["monotonic"]

        self.assertEqual(response["status"], 500)
        self.assertEqual(response["body"]["returncode"], 124)
        self.assertEqual(response["error_type"], "Timeout")
        self.assertEqual(calls, [])

    def test_gateway_recovery_cannot_launch_after_agent_deadline(self):
        now = [100.0]
        managed_calls: list[tuple[str, ...]] = []
        sandbox_scripts: list[str] = []
        previous = {
            "_run": headless_runner._run,
            "_sandbox_exec": headless_runner._sandbox_exec,
            "monotonic": headless_runner.time.monotonic,
        }

        def fake_run(cmd, *, timeout=30):
            managed_calls.append(tuple(cmd))
            now[0] = 161.0
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        def fake_sandbox_exec(sandbox, script, *, timeout):
            sandbox_scripts.append(script)
            return subprocess.CompletedProcess(
                ["sandbox", sandbox],
                1,
                stdout="",
                stderr="gateway down",
            )

        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td)
            headless_runner._run = fake_run
            headless_runner._sandbox_exec = fake_sandbox_exec
            headless_runner.time.monotonic = lambda: now[0]
            try:
                with self.assertRaises(TimeoutError):
                    headless_runner._start_openclaw_cli_async(
                        "demo",
                        "Deploy base",
                        60,
                        log_dir,
                        deadline=160.0,
                    )
            finally:
                headless_runner._run = previous["_run"]
                headless_runner._sandbox_exec = previous["_sandbox_exec"]
                headless_runner.time.monotonic = previous["monotonic"]

            recover_log = (
                log_dir / "openclaw_gateway_recover.log"
            ).read_text(encoding="utf-8")

        self.assertEqual(sandbox_scripts, [])
        self.assertEqual(
            managed_calls,
            [("nemoclaw", "sandbox", "recover", "demo")],
        )
        self.assertIn("sandbox recover", recover_log)
        self.assertIn("returncode=0", recover_log)

    def test_openclaw_launch_uses_only_remaining_agent_budget(self):
        calls: list[tuple[str, int]] = []
        previous = {
            "ensure_openclaw_gateway": headless_runner.ensure_openclaw_gateway,
            "_openclaw_cli_command": headless_runner._openclaw_cli_command,
            "_sandbox_exec": headless_runner._sandbox_exec,
            "monotonic": headless_runner.time.monotonic,
        }

        def fake_command(prompt, timeout, session_id=None):
            calls.append(("openclaw", timeout))
            return "echo complete"

        def fake_sandbox_exec(sandbox, script, *, timeout):
            calls.append(("launcher", timeout))
            return subprocess.CompletedProcess(
                ["sandbox", sandbox],
                0,
                stdout="started",
                stderr="",
            )

        with tempfile.TemporaryDirectory() as td:
            headless_runner.ensure_openclaw_gateway = (
                lambda sandbox, logs, deadline=None: None
            )
            headless_runner._openclaw_cli_command = fake_command
            headless_runner._sandbox_exec = fake_sandbox_exec
            headless_runner.time.monotonic = lambda: 100.0
            try:
                response = headless_runner._start_openclaw_cli_async(
                    "demo",
                    "Deploy base",
                    60,
                    Path(td),
                    deadline=130.0,
                )
            finally:
                headless_runner.ensure_openclaw_gateway = previous[
                    "ensure_openclaw_gateway"
                ]
                headless_runner._openclaw_cli_command = previous[
                    "_openclaw_cli_command"
                ]
                headless_runner._sandbox_exec = previous["_sandbox_exec"]
                headless_runner.time.monotonic = previous["monotonic"]

        self.assertEqual(response["status"], 200)
        self.assertEqual(calls, [("openclaw", 30), ("launcher", 30)])

    def test_openclaw_completion_accepts_v0080_json_envelopes(self):
        fixtures = (
            '{"result":{"payloads":[{"text":"done"}]}}',
            '{"payloads":[{"text":"done"}]}',
            'openclaw info\\n{"result":{"payloads":[{"text":"done"}]}}\\n',
        )
        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td)
            for fixture in fixtures:
                (log_dir / "openclaw-agent.log").write_text(fixture, encoding="utf-8")
                self.assertTrue(headless_runner._openclaw_log_completed(log_dir))

            (log_dir / "openclaw-agent.log").write_text(
                '{"result":{"payloads":[]}}',
                encoding="utf-8",
            )
            self.assertFalse(headless_runner._openclaw_log_completed(log_dir))

            for fixture in (
                '{"status":"error","payloads":[{"text":"not complete"}]}',
                '{"result":{"error":"boom","payloads":[{"text":"not complete"}]}}',
                '{"payloads":[{"isError":true,"text":"not complete"}]}',
            ):
                (log_dir / "openclaw-agent.log").write_text(fixture, encoding="utf-8")
                self.assertFalse(headless_runner._openclaw_log_completed(log_dir))

    def test_openclaw_timeout_marker_is_narrow_and_terminal(self):
        marker = (
            "GatewayClientRequestError: FailoverError: "
            "LLM request timed out"
        )
        self.assertTrue(
            headless_runner._openclaw_transient_llm_timeout(
                f"warning\n{marker}.\nTerminated\n"
            )
        )
        self.assertTrue(
            headless_runner._openclaw_transient_llm_timeout(
                "\x1b[31mGatewayClientRequestError\x1b[0m: "
                "\x1b[31mFailoverError\x1b[0m: "
                "LLM request timed out\n"
            )
        )
        for other_failure in (
            "FailoverError: LLM request timed out",
            "GatewayClientRequestError: LLM request timed out",
            "GatewayClientRequestError: FailoverError: deployment timed out",
            "VLM request timed out",
            f"[agent/embedded] error={marker}",
            f"{marker}\nTerminated\nanother failure",
            f"{marker}\nunrelated terminal failure",
        ):
            with self.subTest(other_failure=other_failure):
                self.assertFalse(
                    headless_runner._openclaw_transient_llm_timeout(
                        other_failure
                    )
                )
        self.assertFalse(
            headless_runner._openclaw_transient_llm_timeout(
                f"{marker}\nTerminated\nTerminated\n"
            )
        )

    def test_openclaw_timeout_retry_requires_exact_stopped_rc1_state(self):
        marker = (
            "GatewayClientRequestError: FailoverError: "
            "LLM request timed out.\nTerminated\n"
        )
        self.assertTrue(
            headless_runner._openclaw_retryable_llm_timeout(
                state="stopped",
                cli_returncode=1,
                error_type="OpenClawStopped",
                raw_log=marker,
            )
        )
        for state, returncode, error_type in (
            ("running", 1, "OpenClawStopped"),
            ("missing", 1, "OpenClawMissingState"),
            ("stopped", 0, "OpenClawMissingOutput"),
            ("stopped", 7, "OpenClawStopped"),
            ("stopped", 1, "OpenClawMissingExitStatus"),
        ):
            with self.subTest(
                state=state,
                returncode=returncode,
                error_type=error_type,
            ):
                self.assertFalse(
                    headless_runner._openclaw_retryable_llm_timeout(
                        state=state,
                        cli_returncode=returncode,
                        error_type=error_type,
                        raw_log=marker,
                    )
                )

    def _run_openclaw_retry_fixture(
        self,
        second_returncode: int,
        *,
        deadline: float = 1000.0,
        first_log: str | None = None,
        first_returncode: int = 1,
        first_state: str = "stopped",
        wait_profile: str = "",
        root_session_jsonl: str | BaseException | None = None,
        second_start_ok: bool = True,
    ) -> tuple[
        dict[str, Any],
        list[dict[str, Any]],
        list[str],
        Path,
        tempfile.TemporaryDirectory[str],
    ]:
        root = tempfile.TemporaryDirectory()
        log_dir = Path(root.name)
        calls: list[dict[str, Any]] = []
        root_reads: list[str] = []
        timeout_marker = (
            "GatewayClientRequestError: FailoverError: "
            "LLM request timed out"
        )
        first_attempt_log = first_log or timeout_marker
        valid_root_session = "\n".join(
            [
                json.dumps(
                    {
                        "type": "session",
                        "id": "run-root",
                        "parentSession": None,
                    }
                ),
                json.dumps(
                    {
                        "type": "message",
                        "id": "user-1",
                        "message": {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": (
                                        "Deploy base and verify its "
                                        "endpoints."
                                    ),
                                }
                            ],
                        },
                    }
                ),
            ]
        ) + "\n"

        def fake_start(
            sandbox,
            prompt,
            timeout,
            logs,
            attempt_deadline=None,
            session_id=None,
        ):
            root_session_id = session_id or "run-root"
            root_session_file = str(
                headless_runner.OPENCLAW_SESSION_DIR
                / f"{root_session_id}.jsonl"
            )
            calls.append(
                {
                    "prompt": prompt,
                    "timeout": timeout,
                    "deadline": attempt_deadline,
                    "session_id": root_session_id,
                }
            )
            if len(calls) == 2 and not second_start_ok:
                return {
                    "status": 500,
                    "body": {"ok": False, "returncode": 70},
                    "stdout_tail": "",
                    "stderr_tail": "pre-launch cleanup failed",
                    "error": "continuation did not launch",
                    "error_type": "OpenClawLaunchError",
                    "root_session_id": root_session_id,
                    "root_session_file": root_session_file,
                }
            (
                logs
                / headless_runner.OPENCLAW_LAUNCH_SESSION_METADATA
            ).write_text(
                json.dumps(
                    {
                        "root_session_id": root_session_id,
                        "root_session_file": root_session_file,
                    }
                ),
                encoding="utf-8",
            )
            (logs / "openclaw-launch.log").write_text(
                f"launch attempt {len(calls)}\n",
                encoding="utf-8",
            )
            return {
                "status": 200,
                "body": {"ok": True},
                "stdout_tail": "",
                "stderr_tail": "",
                "root_session_id": root_session_id,
                "root_session_file": root_session_file,
            }

        def fake_snapshot(sandbox, logs, attempt_deadline=None):
            if len(calls) == 1:
                log = first_attempt_log
                returncode = first_returncode
                state = first_state
            else:
                log = (
                    '{"result":{"payloads":[{"text":"done"}]}}'
                    if second_returncode == 0
                    else timeout_marker
                )
                returncode = second_returncode
                state = "stopped"
            (logs / "openclaw-agent.log").write_text(
                log,
                encoding="utf-8",
            )
            return state, returncode

        def fake_read_root(
            sandbox,
            session_file,
            max_bytes,
            read_deadline,
        ):
            root_reads.append(session_file)
            if isinstance(root_session_jsonl, BaseException):
                raise root_session_jsonl
            return root_session_jsonl or valid_root_session

        patches = (
            mock.patch.object(
                headless_runner,
                "_start_openclaw_cli_async",
                side_effect=fake_start,
            ),
            mock.patch.object(
                headless_runner,
                "_openclaw_cli_snapshot",
                side_effect=fake_snapshot,
            ),
            mock.patch.object(
                headless_runner,
                "_read_managed_openclaw_session",
                side_effect=fake_read_root,
            ),
            mock.patch.object(
                headless_runner.time,
                "monotonic",
                return_value=100.0,
            ),
        )
        with patches[0], patches[1], patches[2], patches[3]:
            response = headless_runner.run_openclaw_cli(
                "demo",
                "Deploy base and verify its endpoints.",
                900,
                log_dir,
                wait_profile=wait_profile,
                deadline=deadline,
            )
        return response, calls, root_reads, log_dir, root

    def test_openclaw_timeout_retries_once_in_same_session_and_succeeds(self):
        response, calls, root_reads, log_dir, temporary = (
            self._run_openclaw_retry_fixture(
                0,
                deadline=872.0,
                wait_profile="base",
            )
        )
        try:
            self.assertEqual(response["status"], 200)
            self.assertEqual(response["attempts"], 2)
            self.assertTrue(response["retry"]["attempted"])
            self.assertTrue(response["attempt_2_launched"])
            self.assertEqual(len(calls), 2)
            self.assertEqual(
                root_reads,
                [
                    (
                        "/sandbox/.openclaw/agents/main/sessions/"
                        "run-root.jsonl"
                    )
                ],
            )
            self.assertEqual(
                [call["session_id"] for call in calls],
                ["run-root", "run-root"],
            )
            self.assertEqual(
                calls[1]["prompt"],
                headless_runner.OPENCLAW_LLM_TIMEOUT_CONTINUATION_PROMPT,
            )
            self.assertNotIn("Deploy base", calls[1]["prompt"])
            self.assertIn("Do not repeat", calls[1]["prompt"])
            self.assertEqual(
                calls[1]["timeout"],
                headless_runner.OPENCLAW_LLM_TIMEOUT_RETRY_MAX_SECONDS,
            )
            self.assertEqual(calls[1]["deadline"], 700.0)
            self.assertEqual(
                response["retry"]["readiness_reserve_s"],
                headless_runner.OPENCLAW_PROFILE_READINESS_RESERVE_SECONDS,
            )
            self.assertEqual(872.0 - calls[1]["deadline"], 172.0)
            self.assertIn(
                "LLM request timed out",
                (
                    log_dir / "openclaw-agent-attempt-1.log"
                ).read_text(encoding="utf-8"),
            )
            self.assertIn(
                '"text":"done"',
                (
                    log_dir / "openclaw-agent-attempt-2.log"
                ).read_text(encoding="utf-8"),
            )
            self.assertIn(
                "launch attempt 1",
                (
                    log_dir / "openclaw-launch-attempt-1.log"
                ).read_text(encoding="utf-8"),
            )
            self.assertIn(
                "launch attempt 2",
                (
                    log_dir / "openclaw-launch-attempt-2.log"
                ).read_text(encoding="utf-8"),
            )
        finally:
            temporary.cleanup()

    def test_openclaw_timeout_retry_failure_is_not_retried_again(self):
        response, calls, _, log_dir, temporary = (
            self._run_openclaw_retry_fixture(7)
        )
        try:
            self.assertEqual(response["status"], 500)
            self.assertEqual(response["body"]["returncode"], 7)
            self.assertEqual(response["attempts"], 2)
            self.assertEqual(len(calls), 2)
            self.assertTrue(response["retry"]["attempted"])
            self.assertIn(
                "LLM request timed out",
                (
                    log_dir / "openclaw-agent-attempt-2.log"
                ).read_text(encoding="utf-8"),
            )
        finally:
            temporary.cleanup()

    def test_openclaw_does_not_retry_other_failures(self):
        response, calls, root_reads, _, temporary = (
            self._run_openclaw_retry_fixture(
                0,
                first_log="docker compose dependency failed to start",
            )
        )
        try:
            self.assertEqual(response["status"], 500)
            self.assertEqual(response["attempts"], 1)
            self.assertEqual(len(calls), 1)
            self.assertEqual(root_reads, [])
            self.assertNotIn("retry", response)
        finally:
            temporary.cleanup()

    def test_openclaw_timeout_retry_requires_shared_deadline_budget(self):
        response, calls, _, log_dir, temporary = (
            self._run_openclaw_retry_fixture(
                0,
                deadline=(
                    100.0
                    + headless_runner.OPENCLAW_LLM_TIMEOUT_RETRY_MIN_SECONDS
                    - 1
                ),
            )
        )
        try:
            self.assertEqual(response["status"], 500)
            self.assertEqual(len(calls), 1)
            self.assertFalse(response["retry"]["attempted"])
            self.assertEqual(
                response["retry"]["skipped"],
                "insufficient_shared_deadline",
            )
            self.assertFalse(
                (log_dir / "openclaw-agent-attempt-2.log").exists()
            )
        finally:
            temporary.cleanup()

    def test_openclaw_timeout_retry_reserves_profile_readiness_budget(self):
        response, calls, root_reads, log_dir, temporary = (
            self._run_openclaw_retry_fixture(
                0,
                deadline=(
                    100.0
                    + headless_runner.OPENCLAW_LLM_TIMEOUT_RETRY_MIN_SECONDS
                    + headless_runner.OPENCLAW_PROFILE_READINESS_RESERVE_SECONDS
                    - 1
                ),
                wait_profile="alerts",
            )
        )
        try:
            self.assertEqual(response["status"], 500)
            self.assertEqual(len(calls), 1)
            self.assertEqual(root_reads, [])
            self.assertFalse(response["retry"]["attempted"])
            self.assertEqual(
                response["retry"]["skipped"],
                "insufficient_shared_deadline",
            )
            self.assertEqual(
                response["retry"]["readiness_reserve_s"],
                headless_runner.OPENCLAW_PROFILE_READINESS_RESERVE_SECONDS,
            )
            self.assertFalse(
                (log_dir / "openclaw-agent-attempt-2.log").exists()
            )
        finally:
            temporary.cleanup()

    def test_openclaw_timeout_retry_deadline_preserves_profile_reserve(self):
        response, calls, _, _, temporary = (
            self._run_openclaw_retry_fixture(
                0,
                deadline=600.0,
                wait_profile="lvs",
            )
        )
        try:
            self.assertEqual(response["status"], 200)
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[1]["timeout"], 380)
            self.assertEqual(calls[1]["deadline"], 480.0)
            self.assertEqual(
                600.0 - calls[1]["deadline"],
                headless_runner.OPENCLAW_PROFILE_READINESS_RESERVE_SECONDS,
            )
        finally:
            temporary.cleanup()

    def test_openclaw_timeout_retry_requires_valid_persisted_root_prompt(self):
        parent_session = (
            "/sandbox/.openclaw/agents/main/sessions/run-parent.jsonl"
        )
        invalid_roots: tuple[
            tuple[str, str | BaseException],
            ...,
        ] = (
            ("missing", RuntimeError("root session unavailable")),
            ("malformed", "{not-json}\n"),
            (
                "parented",
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "session",
                                "id": "run-root",
                                "parentSession": parent_session,
                            }
                        ),
                        json.dumps(
                            {
                                "type": "message",
                                "message": {
                                    "role": "user",
                                    "content": "original prompt",
                                },
                            }
                        ),
                    ]
                )
                + "\n",
            ),
            (
                "header-only",
                json.dumps(
                    {
                        "type": "session",
                        "id": "run-root",
                        "parentSession": None,
                    }
                )
                + "\n",
            ),
            (
                "empty-user",
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "session",
                                "id": "run-root",
                                "parentSession": None,
                            }
                        ),
                        json.dumps(
                            {
                                "type": "message",
                                "message": {
                                    "role": "user",
                                    "content": [
                                        {"type": "text", "text": "  "}
                                    ],
                                },
                            }
                        ),
                    ]
                )
                + "\n",
            ),
            (
                "different-user",
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "session",
                                "id": "run-root",
                                "parentSession": None,
                            }
                        ),
                        json.dumps(
                            {
                                "type": "message",
                                "message": {
                                    "role": "user",
                                    "content": (
                                        "Deploy a different profile."
                                    ),
                                },
                            }
                        ),
                    ]
                )
                + "\n",
            ),
            (
                "mismatched-session-id",
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "session",
                                "id": "other-root",
                                "parentSession": None,
                            }
                        ),
                        json.dumps(
                            {
                                "type": "message",
                                "message": {
                                    "role": "user",
                                    "content": (
                                        "Deploy base and verify its "
                                        "endpoints."
                                    ),
                                },
                            }
                        ),
                    ]
                )
                + "\n",
            ),
            (
                "out-of-order-mismatched-session-id",
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "message",
                                "message": {
                                    "role": "user",
                                    "content": (
                                        "Deploy base and verify its "
                                        "endpoints."
                                    ),
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "type": "session",
                                "id": "other-root",
                                "parentSession": None,
                            }
                        ),
                    ]
                )
                + "\n",
            ),
        )
        for label, root_session_jsonl in invalid_roots:
            with self.subTest(label=label):
                response, calls, root_reads, log_dir, temporary = (
                    self._run_openclaw_retry_fixture(
                        0,
                        root_session_jsonl=root_session_jsonl,
                    )
                )
                try:
                    self.assertEqual(response["status"], 500)
                    self.assertEqual(len(calls), 1)
                    self.assertEqual(
                        root_reads,
                        [
                            (
                                "/sandbox/.openclaw/agents/main/sessions/"
                                "run-root.jsonl"
                            )
                        ],
                    )
                    self.assertFalse(response["retry"]["attempted"])
                    self.assertEqual(
                        response["retry"]["skipped"],
                        "invalid_retry_root_session",
                    )
                    self.assertFalse(
                        (
                            log_dir
                            / "openclaw-agent-attempt-2.log"
                        ).exists()
                    )
                    self.assertFalse(
                        (
                            log_dir
                            / "openclaw.failure-session.jsonl"
                        ).exists()
                    )
                finally:
                    temporary.cleanup()

    def test_openclaw_retry_prelaunch_failure_is_not_attempt_two(self):
        response, calls, root_reads, log_dir, temporary = (
            self._run_openclaw_retry_fixture(
                0,
                second_start_ok=False,
            )
        )
        try:
            self.assertEqual(response["status"], 500)
            self.assertEqual(response["attempts"], 1)
            self.assertFalse(response["attempt_2_launched"])
            self.assertFalse(response["retry"]["attempted"])
            self.assertFalse(response["retry"]["attempt_2_launched"])
            self.assertTrue(response["retry"]["launch_failed"])
            self.assertEqual(len(calls), 2)
            self.assertEqual(len(root_reads), 1)
            self.assertTrue(
                (log_dir / "openclaw-agent-attempt-1.log").exists()
            )
            self.assertFalse(
                (log_dir / "openclaw-agent-attempt-2.log").exists()
            )
        finally:
            temporary.cleanup()

    def test_snapshot_treats_rc_file_as_complete_before_sentinel_exit(self):
        scripts: list[str] = []

        def fake_sandbox_exec(sandbox, script, *, timeout):
            scripts.append(script)
            return subprocess.CompletedProcess(
                ["sandbox", sandbox],
                0,
                stdout=(
                    f"{headless_runner.OPENCLAW_STATE_PREFIX}stopped\n"
                    f"{headless_runner.OPENCLAW_RC_PREFIX}0\n"
                    f"{headless_runner.OPENCLAW_LOG_BEGIN}\n"
                    '{"result":{"payloads":[{"text":"done"}]}}'
                ),
                stderr="",
            )

        with tempfile.TemporaryDirectory() as td, mock.patch.object(
            headless_runner,
            "_sandbox_exec",
            side_effect=fake_sandbox_exec,
        ):
            state, returncode = headless_runner._openclaw_cli_snapshot(
                "demo",
                Path(td),
            )

        self.assertEqual((state, returncode), ("stopped", 0))
        self.assertEqual(len(scripts), 1)
        self.assertLess(
            scripts[0].index("[ -f /tmp/vss-skill-eval-openclaw/openclaw-agent.rc ]"),
            scripts[0].index('kill -0 "$pid"'),
        )

    def test_openclaw_session_file_accepts_nested_and_direct_envelopes(self):
        session_file = (
            "/sandbox/.openclaw/agents/main/sessions/current-session.jsonl"
        )
        nested = (
            "node warning\n"
            + json.dumps(
                {
                    "status": "ok",
                    "result": {
                        "payloads": [{"text": "done"}],
                        "meta": {
                            "agentMeta": {
                                "sessionId": "run-123",
                                "sessionFile": session_file,
                            }
                        },
                    },
                }
            )
        )
        direct = json.dumps(
            {
                "payloads": [{"text": "done"}],
                "meta": {"agentMeta": {"sessionFile": session_file}},
            }
        )

        nested_envelope, nested_path = headless_runner._openclaw_session_file(
            nested
        )
        direct_envelope, direct_path = headless_runner._openclaw_session_file(
            direct
        )

        self.assertEqual(nested_path, session_file)
        self.assertEqual(direct_path, session_file)
        self.assertEqual(nested_envelope["payloads"][0]["text"], "done")
        self.assertEqual(direct_envelope["payloads"][0]["text"], "done")

    def test_openclaw_session_file_rejects_untrusted_or_stale_paths(self):
        def output(path):
            return json.dumps(
                {
                    "meta": {
                        "agentMeta": {
                            "sessionFile": path,
                        }
                    }
                }
            )

        for path in (
            "relative.jsonl",
            "/sandbox/.openclaw/agents/main/sessions/../secret.jsonl",
            "/sandbox/.openclaw/agents/main/sessions/nested/current.jsonl",
            "/sandbox/.openclaw/agents/other/sessions/current.jsonl",
            "/sandbox/.openclaw/agents/main/sessions/current.txt",
            "/sandbox/.openclaw/agents/main/sessions/current.jsonl\nother",
        ):
            with self.subTest(path=path):
                with self.assertRaises(RuntimeError):
                    headless_runner._openclaw_session_file(output(path))

        valid = output(
            "/sandbox/.openclaw/agents/main/sessions/current.jsonl"
        )
        final_failed_document = valid + '\n{"status":"error","error":"boom"}'
        with self.assertRaisesRegex(
            RuntimeError,
            "did not provide meta.agentMeta.sessionFile",
        ):
            headless_runner._openclaw_session_file(final_failed_document)

    def test_openclaw_session_jsonl_maps_current_tool_calls_to_atif(self):
        session_jsonl = "\n".join(
            json.dumps(row)
            for row in (
                {
                    "type": "message",
                    "timestamp": "2026-07-27T23:00:00Z",
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": "stale prompt"}],
                    },
                },
                {
                    "type": "message",
                    "timestamp": "2026-07-27T23:00:01Z",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "Checking prerequisite."},
                            {
                                "type": "toolCall",
                                "id": "call-1",
                                "name": "tool_call",
                                "arguments": {
                                    "id": headless_runner.RTSP_TOOL_ID,
                                    "args": {
                                        "command": (
                                            headless_runner.RTSP_TOOL_ENV_PROBE_COMMAND
                                        )
                                    },
                                },
                            },
                        ],
                        "usage": {
                            "input": 10,
                            "output": 4,
                            "cacheRead": 20,
                            "cacheWrite": 3,
                        },
                    },
                },
                {
                    "type": "message",
                    "message": {
                        "role": "toolResult",
                        "toolCallId": "call-1",
                        "details": {
                            "aggregated": json.dumps(
                                {
                                    "tool": {
                                        "id": headless_runner.RTSP_TOOL_ID
                                    },
                                    "result": {
                                        "content": [
                                            {
                                                "type": "text",
                                                "text": (
                                                    headless_runner.RTSP_TOOL_ENV_READY_SENTINEL
                                                ),
                                            }
                                        ],
                                        "details": {
                                            "status": "completed",
                                            "exitCode": 0,
                                            "aggregated": (
                                                headless_runner.RTSP_TOOL_ENV_READY_SENTINEL
                                            ),
                                        },
                                    },
                                }
                            )
                        },
                    },
                },
                {
                    "type": "message",
                    "timestamp": "2026-07-27T23:00:02Z",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "text",
                                "text": "Deployment completed.",
                            }
                        ],
                    },
                },
            )
        )
        envelope = {
            "meta": {
                "agentMeta": {
                    "sessionId": "run-123",
                    "model": "aws/anthropic/test-model",
                    "usage": {
                        "input": 100,
                        "output": 25,
                        "cacheRead": 200,
                    },
                }
            }
        }

        trajectory = headless_runner._openclaw_session_jsonl_to_atif(
            session_jsonl,
            instruction="current eval prompt",
            envelope=envelope,
        )

        self.assertIsNotNone(trajectory)
        assert trajectory is not None
        self.assertEqual(trajectory["schema_version"], "ATIF-v1.7")
        self.assertEqual(trajectory["session_id"], "run-123")
        self.assertEqual(trajectory["steps"][0]["message"], "current eval prompt")
        tool_step = trajectory["steps"][1]
        self.assertEqual(tool_step["source"], "agent")
        self.assertEqual(tool_step["tool_calls"][0]["tool_call_id"], "call-1")
        self.assertEqual(
            tool_step["tool_calls"][0]["function_name"],
            "tool_call",
        )
        self.assertEqual(
            tool_step["tool_calls"][0]["arguments"],
            {
                "id": headless_runner.RTSP_TOOL_ID,
                "args": {
                    "command": headless_runner.RTSP_TOOL_ENV_PROBE_COMMAND
                },
            },
        )
        self.assertEqual(tool_step["metrics"]["prompt_tokens"], 30)
        self.assertEqual(
            trajectory["steps"][-1]["message"],
            "Deployment completed.",
        )
        self.assertEqual(
            trajectory["final_metrics"],
            {
                "total_prompt_tokens": 300,
                "total_completion_tokens": 25,
                "total_cached_tokens": 200,
                "total_steps": 3,
            },
        )
        with tempfile.TemporaryDirectory() as td:
            trajectory_path = Path(td) / "trajectory.json"
            trajectory_path.write_text(
                json.dumps(trajectory),
                encoding="utf-8",
            )
            headless_runner._assert_rtsp_tool_shell_visibility(
                trajectory_path
            )

    def test_openclaw_trajectory_uses_unique_ids_for_missing_tool_ids(self):
        session_jsonl = "\n".join(
            [
                json.dumps(
                    {
                        "type": "session",
                        "id": "current-session",
                        "parentSession": None,
                    }
                ),
                json.dumps(
                    {
                        "type": "message",
                        "id": "user-1",
                        "message": {"role": "user", "content": "prompt"},
                    }
                ),
                json.dumps(
                    {
                        "type": "message",
                        "id": "assistant-1",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "toolCall",
                                    "name": "exec",
                                    "arguments": '{"command":"one"}',
                                },
                                {
                                    "type": "toolCall",
                                    "name": "exec",
                                    "arguments": "not-json",
                                },
                            ],
                        },
                    }
                ),
            ]
        )
        trajectory = headless_runner._openclaw_session_jsonl_to_atif(
            session_jsonl,
            instruction="prompt",
            envelope={"meta": {"agentMeta": {}}},
        )

        self.assertIsNotNone(trajectory)
        assert trajectory is not None
        calls = trajectory["steps"][1]["tool_calls"]
        self.assertEqual(
            [call["tool_call_id"] for call in calls],
            ["openclaw-tool-000001", "openclaw-tool-000002"],
        )
        self.assertEqual(calls[0]["arguments"], {"command": "one"})
        self.assertEqual(calls[1]["arguments"], {"raw": "not-json"})

    def test_publish_openclaw_trajectory_writes_current_agent_and_artifacts(self):
        rtsp_url = notebook_adapter.DEFAULT_RTSP_SAMPLE_URL
        session_file = (
            "/sandbox/.openclaw/agents/main/sessions/current-session.jsonl"
        )
        envelope = {
            "status": "ok",
            "result": {
                "payloads": [{"text": f"done with {rtsp_url}"}],
                "meta": {
                    "agentMeta": {
                        "sessionId": "current-session",
                        "sessionFile": session_file,
                        "model": "aws/anthropic/test-model",
                    }
                },
            },
        }
        session_jsonl = "\n".join(
            [
                json.dumps(
                    {
                        "type": "session",
                        "id": "current-session",
                        "parentSession": None,
                    }
                ),
                json.dumps(
                    {
                        "type": "message",
                        "id": "user-1",
                        "message": {"role": "user", "content": "prompt"},
                    }
                ),
                json.dumps(
                    {
                        "type": "message",
                        "id": "assistant-1",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "text",
                                    "text": f"current answer for {rtsp_url}",
                                }
                            ],
                        },
                    }
                ),
            ]
        )
        scripts: list[str] = []

        def fake_sandbox_exec(sandbox, script, *, timeout):
            scripts.append(script)
            return subprocess.CompletedProcess(
                ["sandbox", sandbox],
                0,
                stdout=session_jsonl,
                stderr="",
            )

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            artifact_dir = root / "artifacts"
            agent_dir = root / "agent"
            artifact_dir.mkdir()
            (artifact_dir / headless_runner.OPENCLAW_LAUNCH_SESSION_METADATA).write_text(
                json.dumps(
                    {
                        "root_session_id": "current-session",
                        "root_session_file": session_file,
                    }
                ),
                encoding="utf-8",
            )
            (artifact_dir / "openclaw-agent.log").write_text(
                "node warning\n" + json.dumps(envelope),
                encoding="utf-8",
            )
            with mock.patch.object(
                headless_runner,
                "_sandbox_exec",
                side_effect=fake_sandbox_exec,
            ), mock.patch.dict(
                os.environ,
                {"RTSP_SAMPLE_URL": rtsp_url},
                clear=False,
            ):
                report = (
                    headless_runner.collect_and_publish_openclaw_trajectory(
                        "demo",
                        artifact_dir,
                        agent_dir,
                        "current prompt",
                    )
                )

            agent_trajectory = json.loads(
                (agent_dir / "trajectory.json").read_text(encoding="utf-8")
            )
            artifact_trajectory = json.loads(
                (artifact_dir / "trajectory.json").read_text(
                    encoding="utf-8"
                )
            )
            published_text = "\n".join(
                path.read_text(encoding="utf-8")
                for path in (
                    agent_dir / "openclaw.session.jsonl",
                    agent_dir / "trajectory.json",
                    artifact_dir / "openclaw-agent.log",
                    artifact_dir / "openclaw.session.jsonl",
                    artifact_dir / "trajectory.json",
                )
            )

        self.assertEqual(report["trajectory_steps"], 2)
        self.assertEqual(report["session_chain_depth"], 1)
        self.assertEqual(report["session_files"], [session_file])
        self.assertEqual(
            agent_trajectory["steps"][-1]["message"],
            (
                "current answer for "
                "<redacted:RTSP_SAMPLE_URL;match=exact-runtime-value>"
            ),
        )
        self.assertEqual(agent_trajectory, artifact_trajectory)
        self.assertNotIn(rtsp_url, published_text)
        self.assertIn(
            "<redacted:RTSP_SAMPLE_URL;match=exact-runtime-value>",
            published_text,
        )
        self.assertEqual(len(scripts), 1)
        self.assertIn(session_file, scripts[0])
        self.assertIn("readlink -f", scripts[0])
        self.assertIn(str(headless_runner.OPENCLAW_SESSION_MAX_BYTES), scripts[0])

    def test_missing_result_envelope_publishes_only_fresh_root_failure_session(self):
        rtsp_url = notebook_adapter.DEFAULT_RTSP_SAMPLE_URL
        root_session = (
            "/sandbox/.openclaw/agents/main/sessions/run-root.jsonl"
        )
        session_jsonl = "\n".join(
            [
                json.dumps(
                    {
                        "type": "session",
                        "id": "run-root",
                        "parentSession": None,
                    }
                ),
                json.dumps(
                    {
                        "type": "message",
                        "id": "user-1",
                        "message": {
                            "role": "user",
                            "content": f"deploy from {rtsp_url}",
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "message",
                        "id": "assistant-1",
                        "message": {
                            "role": "assistant",
                            "errorMessage": (
                                "GatewayClientRequestError: FailoverError: "
                                "LLM request timed out"
                            ),
                            "content": [],
                        },
                    }
                ),
            ]
        ) + "\n"
        scripts: list[str] = []

        def fake_sandbox_exec(sandbox, script, *, timeout):
            scripts.append(script)
            return subprocess.CompletedProcess(
                ["sandbox", sandbox],
                0,
                stdout=session_jsonl,
                stderr="",
            )

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            artifact_dir = root / "artifacts"
            agent_dir = root / "agent"
            artifact_dir.mkdir()
            (
                artifact_dir
                / headless_runner.OPENCLAW_LAUNCH_SESSION_METADATA
            ).write_text(
                json.dumps(
                    {
                        "root_session_id": "run-root",
                        "root_session_file": root_session,
                    }
                ),
                encoding="utf-8",
            )
            (artifact_dir / "openclaw-agent.log").write_text(
                (
                    "GatewayClientRequestError: FailoverError: "
                    "LLM request timed out\n"
                ),
                encoding="utf-8",
            )
            with (
                mock.patch.object(
                    headless_runner,
                    "_sandbox_exec",
                    side_effect=fake_sandbox_exec,
                ),
                mock.patch.dict(
                    os.environ,
                    {"RTSP_SAMPLE_URL": rtsp_url},
                    clear=False,
                ),
                self.assertRaisesRegex(
                    RuntimeError,
                    "failure session was published",
                ),
            ):
                headless_runner.collect_and_publish_openclaw_trajectory(
                    "demo",
                    artifact_dir,
                    agent_dir,
                    "current prompt",
                )

            artifact_failure = (
                artifact_dir / "openclaw.failure-session.jsonl"
            ).read_text(encoding="utf-8")
            agent_failure_exists = (
                agent_dir / "openclaw.failure-session.jsonl"
            ).exists()
            report = json.loads(
                (
                    artifact_dir / "openclaw_failure_session.json"
                ).read_text(encoding="utf-8")
            )
            trajectory_exists = (
                artifact_dir / "trajectory.json"
            ).exists()

        self.assertFalse(agent_failure_exists)
        self.assertNotIn(rtsp_url, artifact_failure)
        self.assertIn(headless_runner.RTSP_EXACT_REDACTION, artifact_failure)
        self.assertEqual(report["root_session_file"], root_session)
        self.assertEqual(
            report["reason"],
            "missing_openclaw_result_envelope",
        )
        self.assertFalse(trajectory_exists)
        self.assertEqual(len(scripts), 1)
        self.assertIn(root_session, scripts[0])
        self.assertNotIn("find ", scripts[0])

    def test_publish_openclaw_trajectory_merges_compaction_lineage(self):
        root_session = (
            "/sandbox/.openclaw/agents/main/sessions/run-root.jsonl"
        )
        leaf_session = (
            "/sandbox/.openclaw/agents/main/sessions/run-leaf.jsonl"
        )
        retained = {
            "type": "message",
            "id": "assistant-retained",
            "parentId": "summarized-parent",
            "timestamp": "2026-07-28T01:10:00Z",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "retained turn"},
                    {
                        "type": "thinking",
                        "thinking": "retained reasoning",
                        "thinkingSignature": "thinking-signature",
                        "signature": "signature",
                        "thought_signature": "thought-signature",
                    },
                    {
                        "type": "redacted_thinking",
                        "data": "redacted-thinking-data",
                        "signature": "redacted-signature",
                    },
                ],
            },
        }
        retained_successor = {
            **retained,
            "parentId": None,
            "message": {
                **retained["message"],
                "content": [
                    {"type": "text", "text": "retained turn"},
                    {
                        "type": "thinking",
                        "thinking": "retained reasoning",
                    },
                    {"type": "redacted_thinking"},
                ],
            },
        }
        compaction = {
            "type": "compaction",
            "id": "compaction-1",
            "parentId": "assistant-retained",
            "summary": "must not become an ATIF step",
            "firstKeptEntryId": "assistant-retained",
            "tokensBefore": 42000,
        }
        compaction_successor = {
            **compaction,
            "firstKeptEntryId": "assistant-guard",
        }
        root_jsonl = "\n".join(
            json.dumps(record)
            for record in (
                {
                    "type": "session",
                    "id": "run-root",
                    "parentSession": None,
                },
                {
                    "type": "message",
                    "id": "user-prompt",
                    "message": {"role": "user", "content": "stale body"},
                },
                {
                    "type": "message",
                    "id": "assistant-guard",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "toolCall",
                                "id": "guard-call",
                                "name": "exec",
                                "arguments": {
                                    "command": (
                                        'test -n "${RTSP_SAMPLE_URL:-}"'
                                    )
                                },
                            }
                        ],
                    },
                },
                {
                    "type": "message",
                    "id": "guard-result",
                    "message": {
                        "role": "toolResult",
                        "toolCallId": "guard-call",
                        "details": {
                            "aggregated": "RTSP_SAMPLE_URL is set\n"
                        },
                    },
                },
                retained,
                compaction,
            )
        )
        leaf_jsonl = "\n".join(
            json.dumps(record)
            for record in (
                {
                    "type": "session",
                    "id": "run-leaf",
                    "parentSession": root_session,
                },
                retained_successor,
                compaction_successor,
                {
                    "type": "message",
                    "id": "assistant-final",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "finished"}],
                    },
                },
            )
        )
        envelope = {
            "result": {
                "payloads": [{"text": "finished"}],
                "meta": {
                    "agentMeta": {
                        "sessionId": "run-leaf",
                        "sessionFile": leaf_session,
                    }
                },
            }
        }
        scripts: list[str] = []

        def fake_sandbox_exec(sandbox, script, *, timeout):
            scripts.append(script)
            transcript = (
                leaf_jsonl
                if "run-leaf.jsonl" in script
                else root_jsonl
            )
            return subprocess.CompletedProcess(
                ["sandbox", sandbox],
                0,
                stdout=transcript,
                stderr="",
            )

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            artifact_dir = root / "artifacts"
            agent_dir = root / "agent"
            artifact_dir.mkdir()
            (
                artifact_dir
                / headless_runner.OPENCLAW_LAUNCH_SESSION_METADATA
            ).write_text(
                json.dumps(
                    {
                        "root_session_id": "run-root",
                        "root_session_file": root_session,
                    }
                ),
                encoding="utf-8",
            )
            (artifact_dir / "openclaw-agent.log").write_text(
                json.dumps(envelope),
                encoding="utf-8",
            )
            with mock.patch.object(
                headless_runner,
                "_sandbox_exec",
                side_effect=fake_sandbox_exec,
            ):
                report = (
                    headless_runner.collect_and_publish_openclaw_trajectory(
                        "demo",
                        artifact_dir,
                        agent_dir,
                        "current prompt",
                    )
                )
            trajectory = json.loads(
                (artifact_dir / "trajectory.json").read_text(
                    encoding="utf-8"
                )
            )
            published_session = (
                artifact_dir / "openclaw.session.jsonl"
            ).read_text(encoding="utf-8")
            published_records = [
                json.loads(line)
                for line in published_session.splitlines()
                if line.strip()
            ]

        self.assertEqual(report["session_chain_depth"], 2)
        self.assertEqual(
            report["session_files"],
            [root_session, leaf_session],
        )
        self.assertEqual(
            trajectory["steps"][0],
            {
                "step_id": 1,
                "source": "user",
                "message": "current prompt",
            },
        )
        self.assertEqual(
            trajectory["steps"][1]["tool_calls"][0]["arguments"],
            {"command": 'test -n "${RTSP_SAMPLE_URL:-}"'},
        )
        self.assertEqual(
            [
                step["message"]
                for step in trajectory["steps"]
                if step["source"] == "agent"
            ],
            ["(no assistant text)", "retained turn", "finished"],
        )
        published_ids = [record.get("id") for record in published_records]
        self.assertEqual(published_ids.count("assistant-retained"), 1)
        self.assertEqual(published_ids.count("compaction-1"), 1)
        self.assertNotIn("must not become an ATIF step", json.dumps(trajectory))
        self.assertEqual(len(scripts), 2)
        self.assertIn("run-leaf.jsonl", scripts[0])
        self.assertIn("run-root.jsonl", scripts[1])
        self.assertTrue(all("find " not in script for script in scripts))

    def test_openclaw_parent_session_rejects_untrusted_paths(self):
        for parent in (
            "relative.jsonl",
            "/sandbox/.openclaw/agents/main/sessions/../stale.jsonl",
            "/sandbox/.openclaw/agents/main/sessions/nested/stale.jsonl",
            "/sandbox/.openclaw/agents/other/sessions/stale.jsonl",
            "/sandbox/.openclaw/agents/main/sessions/stale.txt",
            "/sandbox/.openclaw/agents/main/sessions/stale.jsonl\nother",
        ):
            with self.subTest(parent=parent):
                session_jsonl = json.dumps(
                    {
                        "type": "session",
                        "id": "leaf",
                        "parentSession": parent,
                    }
                )
                with self.assertRaises(RuntimeError):
                    headless_runner._openclaw_parent_session_file(
                        session_jsonl
                    )

    def test_openclaw_session_merge_rejects_divergent_duplicate_ids(self):
        first = json.dumps(
            {
                "type": "message",
                "id": "same-id",
                "message": {"role": "assistant", "content": "first"},
            }
        )
        second = json.dumps(
            {
                "type": "message",
                "id": "same-id",
                "message": {"role": "assistant", "content": "changed"},
            }
        )

        with self.assertRaisesRegex(RuntimeError, "different content"):
            headless_runner._merge_openclaw_session_chain(
                [
                    ("root.jsonl", first),
                    ("leaf.jsonl", second),
                ]
            )

    def test_openclaw_session_merge_rejects_changed_compaction_summary(self):
        original = json.dumps(
            {
                "type": "compaction",
                "id": "same-id",
                "parentId": "kept-user",
                "summary": "original summary",
                "firstKeptEntryId": "kept-user",
                "tokensBefore": 42000,
            }
        )
        successor = json.dumps(
            {
                "type": "compaction",
                "id": "same-id",
                "parentId": "preserved-assistant",
                "summary": "changed summary",
                "firstKeptEntryId": "preserved-assistant",
                "tokensBefore": 42000,
            }
        )

        with self.assertRaisesRegex(RuntimeError, "different content"):
            headless_runner._merge_openclaw_session_chain(
                [
                    ("root.jsonl", original),
                    ("leaf.jsonl", successor),
                ]
            )

    def test_openclaw_session_collection_rejects_parent_cycle(self):
        session_dir = "/sandbox/.openclaw/agents/main/sessions"
        root_session = f"{session_dir}/run-root.jsonl"
        leaf_session = f"{session_dir}/run-leaf.jsonl"
        middle_session = f"{session_dir}/run-middle.jsonl"
        transcripts = {
            "run-leaf.jsonl": json.dumps(
                {
                    "type": "session",
                    "id": "leaf",
                    "parentSession": middle_session,
                }
            ),
            "run-middle.jsonl": json.dumps(
                {
                    "type": "session",
                    "id": "middle",
                    "parentSession": leaf_session,
                }
            ),
        }
        envelope = {
            "result": {
                "payloads": [{"text": "done"}],
                "meta": {
                    "agentMeta": {
                        "sessionId": "leaf",
                        "sessionFile": leaf_session,
                    }
                },
            }
        }

        def fake_sandbox_exec(sandbox, script, *, timeout):
            name = next(
                name for name in transcripts if name in script
            )
            return subprocess.CompletedProcess(
                ["sandbox", sandbox],
                0,
                stdout=transcripts[name],
                stderr="",
            )

        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td) / "artifacts"
            agent_dir = Path(td) / "agent"
            log_dir.mkdir()
            (
                log_dir
                / headless_runner.OPENCLAW_LAUNCH_SESSION_METADATA
            ).write_text(
                json.dumps(
                    {
                        "root_session_id": "run-root",
                        "root_session_file": root_session,
                    }
                ),
                encoding="utf-8",
            )
            (log_dir / "openclaw-agent.log").write_text(
                json.dumps(envelope),
                encoding="utf-8",
            )
            with mock.patch.object(
                headless_runner,
                "_sandbox_exec",
                side_effect=fake_sandbox_exec,
            ), self.assertRaisesRegex(RuntimeError, "parent cycle"):
                headless_runner.collect_and_publish_openclaw_trajectory(
                    "demo",
                    log_dir,
                    agent_dir,
                    "prompt",
                )

            self.assertFalse((log_dir / "trajectory.json").exists())
            self.assertFalse((agent_dir / "trajectory.json").exists())

    def test_collect_openclaw_cli_log_copies_sandbox_output(self):
        calls: list[tuple[str, ...]] = []
        previous = headless_runner._run

        def fake_run(cmd, *, timeout=30):
            calls.append(tuple(cmd))
            return subprocess.CompletedProcess(cmd, 0, stdout="agent transcript", stderr="")

        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td)
            headless_runner._run = fake_run
            try:
                headless_runner.collect_openclaw_cli_log("demo", log_dir)
            finally:
                headless_runner._run = previous

            openclaw_log = (log_dir / "openclaw-agent.log").read_text(encoding="utf-8")

        self.assertEqual(openclaw_log, "agent transcript")
        wrapper = next(call[-1] for call in calls if "base64 -d" in " ".join(call))
        encoded = wrapper.split("printf %s ", 1)[1].split(" | base64 -d", 1)[0].strip("'")
        script = base64.b64decode(encoded).decode("utf-8")
        self.assertIn("openclaw-agent.log", script)

    def test_collect_openclaw_cli_log_preserves_snapshot_on_transport_error(self):
        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td)
            log_path = log_dir / "openclaw-agent.log"
            log_path.write_text("valid snapshot", encoding="utf-8")
            with mock.patch.object(
                headless_runner,
                "_sandbox_exec",
                return_value=subprocess.CompletedProcess(
                    ["sandbox", "demo"],
                    7,
                    stdout="",
                    stderr="transport failed",
                ),
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "transport failed",
                ):
                    headless_runner.collect_openclaw_cli_log(
                        "demo",
                        log_dir,
                    )

            self.assertEqual(
                log_path.read_text(encoding="utf-8"),
                "valid snapshot",
            )

    def test_stop_openclaw_cli_validates_and_kills_private_process_group(self):
        calls: list[tuple[str, ...]] = []
        previous = headless_runner._run

        def fake_run(cmd, *, timeout=30):
            calls.append(tuple(cmd))
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        headless_runner._run = fake_run
        try:
            headless_runner.stop_openclaw_cli("demo")
        finally:
            headless_runner._run = previous

        wrapper = next(call[-1] for call in calls if "base64 -d" in " ".join(call))
        encoded = wrapper.split("printf %s ", 1)[1].split(" | base64 -d", 1)[0].strip("'")
        script = base64.b64decode(encoded).decode("utf-8")
        self.assertIn("openclaw-agent.pgid", script)
        self.assertIn('actual_pgid=$(ps -o pgid=', script)
        self.assertIn('cmdline=$(tr "\\000" " "', script)
        self.assertIn('kill -TERM -"$pgid"', script)
        self.assertIn('kill -KILL -"$pgid"', script)

    def test_cleanup_stops_only_the_recorded_openclaw_process_group(self):
        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td)
            marker = run_dir / "openclaw-agent.rc.tmp"
            process = subprocess.Popen(
                [
                    "setsid",
                    "sh",
                    "-lc",
                    f"sleep 30; : > {shlex.quote(str(marker))}",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                for _ in range(100):
                    if process.poll() is not None:
                        self.fail("test OpenClaw process exited before cleanup")
                    if os.getpgid(process.pid) == process.pid:
                        break
                    time.sleep(0.01)
                else:
                    self.fail("test OpenClaw process did not create a process group")
                (run_dir / "openclaw-agent.pid").write_text(
                    f"{process.pid}\n",
                    encoding="utf-8",
                )
                (run_dir / "openclaw-agent.pgid").write_text(
                    f"{process.pid}\n",
                    encoding="utf-8",
                )
                result = subprocess.run(
                    [
                        "sh",
                        "-lc",
                        headless_runner._openclaw_process_cleanup_script(
                            str(run_dir)
                        ),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=10,
                )
                process.wait(timeout=5)
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, 9)
                    process.wait(timeout=5)

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_cleanup_kills_group_when_leader_exits_but_child_ignores_term(self):
        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td)
            marker = run_dir / "openclaw-agent.rc.tmp"
            child_file = run_dir / "term-ignoring-child.pid"
            worker = (
                "trap 'exit 0' TERM; "
                "sh -c 'trap \"\" TERM; while :; do sleep 1; done' & "
                "child=$!; "
                f"printf '%s\\n' \"$child\" > {shlex.quote(str(child_file))}; "
                'wait "$child"; '
                f": > {shlex.quote(str(marker))}"
            )
            process = subprocess.Popen(
                ["setsid", "sh", "-lc", worker],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            child_pid = 0
            try:
                for _ in range(100):
                    if process.poll() is not None:
                        self.fail("test OpenClaw leader exited before cleanup")
                    if (
                        child_file.exists()
                        and os.getpgid(process.pid) == process.pid
                    ):
                        child_pid = int(child_file.read_text().strip())
                        break
                    time.sleep(0.01)
                else:
                    self.fail("test OpenClaw child was not launched")
                (run_dir / "openclaw-agent.pid").write_text(
                    f"{process.pid}\n",
                    encoding="utf-8",
                )
                (run_dir / "openclaw-agent.pgid").write_text(
                    f"{process.pid}\n",
                    encoding="utf-8",
                )
                result = subprocess.run(
                    [
                        "sh",
                        "-lc",
                        headless_runner._openclaw_process_cleanup_script(
                            str(run_dir)
                        ),
                    ],
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
        self.assertFalse(
            any(
                int(fields[1]) == process.pid
                and not fields[2].startswith("Z")
                for line in members
                if len(fields := line.split()) >= 3
            )
        )

    def test_cleanup_fails_closed_if_leader_was_already_gone(self):
        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td)
            marker = run_dir / "openclaw-agent.rc.tmp"
            child_file = run_dir / "orphaned-child.pid"
            worker = (
                "sh -c 'trap \"\" TERM; while :; do sleep 1; done' & "
                "child=$!; "
                f"printf '%s\\n' \"$child\" > {shlex.quote(str(child_file))}; "
                f": > {shlex.quote(str(marker))}"
            )
            process = subprocess.Popen(
                ["setsid", "sh", "-lc", worker],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                process.wait(timeout=5)
                child_pid = int(child_file.read_text().strip())
                self.assertEqual(os.getpgid(child_pid), process.pid)
                (run_dir / "openclaw-agent.pid").write_text(
                    f"{process.pid}\n",
                    encoding="utf-8",
                )
                (run_dir / "openclaw-agent.pgid").write_text(
                    f"{process.pid}\n",
                    encoding="utf-8",
                )
                result = subprocess.run(
                    [
                        "sh",
                        "-lc",
                        headless_runner._openclaw_process_cleanup_script(
                            str(run_dir)
                        ),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=10,
                )
            finally:
                try:
                    os.killpg(process.pid, 9)
                except ProcessLookupError:
                    pass

        self.assertEqual(result.returncode, 70)
        self.assertIn(
            "leader exited with live group members",
            result.stderr,
        )

    def test_cli_waits_for_completion_when_legacy_fast_mode_is_set(self):
        calls: list[str] = []
        previous = {
            "_start_openclaw_cli_async": (
                headless_runner._start_openclaw_cli_async
            ),
            "_openclaw_cli_snapshot": headless_runner._openclaw_cli_snapshot,
            "_openclaw_log_completed": headless_runner._openclaw_log_completed,
            "NEMOCLAW_FAST_READINESS_MODE": os.environ.get(
                "NEMOCLAW_FAST_READINESS_MODE"
            ),
        }

        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td)
            headless_runner._start_openclaw_cli_async = (
                lambda sandbox, prompt, timeout, logs, deadline=None: {
                    "status": 200,
                    "body": {"ok": True},
                    "stdout_tail": "",
                    "stderr_tail": "",
                }
            )
            headless_runner._openclaw_cli_snapshot = (
                lambda sandbox, logs, deadline=None: (
                    calls.append("snapshot") or ("stopped", 0)
                )
            )
            headless_runner._openclaw_log_completed = (
                lambda logs: True
            )
            os.environ["NEMOCLAW_FAST_READINESS_MODE"] = "1"
            try:
                response = headless_runner.run_openclaw_cli(
                    "demo",
                    "Deploy base",
                    30,
                    log_dir,
                    wait_profile="base",
                )
            finally:
                headless_runner._start_openclaw_cli_async = previous[
                    "_start_openclaw_cli_async"
                ]
                headless_runner._openclaw_cli_snapshot = previous[
                    "_openclaw_cli_snapshot"
                ]
                headless_runner._openclaw_log_completed = previous[
                    "_openclaw_log_completed"
                ]
                if previous["NEMOCLAW_FAST_READINESS_MODE"] is None:
                    os.environ.pop("NEMOCLAW_FAST_READINESS_MODE", None)
                else:
                    os.environ["NEMOCLAW_FAST_READINESS_MODE"] = previous["NEMOCLAW_FAST_READINESS_MODE"]

        self.assertEqual(response["status"], 200)
        self.assertEqual(response["body"]["mode"], "cli")
        self.assertEqual(calls, ["snapshot"])

    def test_wait_for_lvs_profile_requires_lvs_ready_endpoint(self):
        previous = {
            "_run": headless_runner._run,
            "sleep": headless_runner.time.sleep,
            "time": headless_runner.time.time,
            "monotonic": headless_runner.time.monotonic,
        }
        calls: list[list[str]] = []
        now = [0.0]

        def fake_run(cmd, *, timeout=30):
            calls.append(cmd)
            if "38111/v1/ready" in " ".join(cmd):
                return subprocess.CompletedProcess(cmd, 7, stdout="", stderr="connection refused")
            if cmd[:2] == ["docker", "ps"]:
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    stdout="vss-agent\nvss-agent-ui\nredis\nvss-lvs\n",
                    stderr="",
                )
            return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

        with tempfile.TemporaryDirectory() as td:
            headless_runner._run = fake_run
            headless_runner.time.sleep = lambda seconds: now.__setitem__(
                0,
                now[0] + seconds,
            )
            headless_runner.time.time = lambda: now[0]
            headless_runner.time.monotonic = lambda: now[0]
            try:
                report = headless_runner.wait_for_profile("lvs", 60, Path(td))
            finally:
                headless_runner._run = previous["_run"]
                headless_runner.time.sleep = previous["sleep"]
                headless_runner.time.time = previous["time"]
                headless_runner.time.monotonic = previous["monotonic"]

        self.assertTrue(report["waited"])
        self.assertFalse(report["ok"])
        self.assertEqual(report["profile"], "lvs")
        self.assertIn("38111/v1/ready", report["message"])
        self.assertTrue(any("38111/v1/ready" in " ".join(call) for call in calls))

    def test_wait_for_alerts_profile_requires_rtvlm_and_kafka(self):
        previous = {
            "_run": headless_runner._run,
            "sleep": headless_runner.time.sleep,
            "time": headless_runner.time.time,
            "monotonic": headless_runner.time.monotonic,
        }
        calls: list[list[str]] = []
        now = [0.0]

        def fake_run(cmd, *, timeout=30):
            calls.append(cmd)
            if cmd[:2] == ["docker", "ps"]:
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    stdout="vss-agent\nvss-agent-ui\nredis\nvss-rtvi-vlm\n",
                    stderr="",
                )
            return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

        with tempfile.TemporaryDirectory() as td:
            headless_runner._run = fake_run
            headless_runner.time.sleep = lambda seconds: now.__setitem__(
                0,
                now[0] + seconds,
            )
            headless_runner.time.time = lambda: now[0]
            headless_runner.time.monotonic = lambda: now[0]
            try:
                report = headless_runner.wait_for_profile("alerts", 60, Path(td))
            finally:
                headless_runner._run = previous["_run"]
                headless_runner.time.sleep = previous["sleep"]
                headless_runner.time.time = previous["time"]
                headless_runner.time.monotonic = previous["monotonic"]

        self.assertTrue(report["waited"])
        self.assertFalse(report["ok"])
        self.assertEqual(report["profile"], "alerts")
        self.assertIn("kafka", report["message"])
        self.assertTrue(
            any("8018/v1/health/ready" in " ".join(call) for call in calls)
        )

    def test_dense_captioning_alerts_prompt_uses_source_kafka_container(self):
        spec = json.loads(
            (
                REPO_ROOT
                / "skills"
                / "vss-deploy-dense-captioning"
                / "evals"
                / "alerts_profile_api.json"
            ).read_text(encoding="utf-8")
        )
        infra_compose = (
            REPO_ROOT / "deploy" / "docker" / "services" / "infra" / "compose.yml"
        ).read_text(encoding="utf-8")
        first_query = spec["expects"][0]["query"]
        second_task = json.dumps(spec["expects"][1])
        skill_text = (
            REPO_ROOT / "skills" / "vss-deploy-dense-captioning" / "SKILL.md"
        ).read_text(encoding="utf-8")
        kafka_reference = (
            REPO_ROOT
            / "skills"
            / "vss-deploy-dense-captioning"
            / "references"
            / "kafka-workflows.md"
        ).read_text(encoding="utf-8")
        rt_vlm_reference = (
            REPO_ROOT
            / "skills"
            / "vss-deploy-dense-captioning"
            / "references"
            / "integrate-rt-vlm.md"
        ).read_text(encoding="utf-8")

        self.assertIn("container_name: kafka", infra_compose)
        self.assertIn("Kafka container `kafka` running", first_query)
        self.assertNotIn("`mdx-kafka` running", first_query)
        self.assertIn("grep -qx kafka", second_task)
        self.assertIn("`docker exec` against `kafka`", second_task)
        self.assertNotIn("mdx-kafka", second_task)
        self.assertIn("Kafka container\n  `kafka`", skill_text)
        for documentation in (skill_text, kafka_reference, rt_vlm_reference):
            self.assertNotIn("mdx-kafka", documentation)
        self.assertIn(
            "docker exec kafka kafka-console-consumer",
            kafka_reference,
        )
        self.assertIn(
            "docker exec kafka kafka-console-consumer",
            rt_vlm_reference,
        )

    def test_wait_for_alerts_profile_passes_with_shared_deadline(self):
        previous = {
            "_run": headless_runner._run,
            "monotonic": headless_runner.time.monotonic,
        }
        calls: list[list[str]] = []
        timeouts: list[int] = []

        def fake_run(cmd, *, timeout=30):
            calls.append(cmd)
            timeouts.append(timeout)
            if cmd[:2] == ["docker", "ps"]:
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    stdout=(
                        "vss-agent\nvss-agent-ui\nredis\n"
                        "vss-rtvi-vlm\nkafka\n"
                    ),
                    stderr="",
                )
            return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

        headless_runner._run = fake_run
        headless_runner.time.monotonic = lambda: 100.0
        try:
            ok, message = headless_runner._profile_ready(
                "alerts",
                deadline=107.0,
            )
        finally:
            headless_runner._run = previous["_run"]
            headless_runner.time.monotonic = previous["monotonic"]

        self.assertTrue(ok)
        self.assertIn("alerts readiness probes passed", message)
        self.assertTrue(
            any("8018/v1/health/ready" in " ".join(call) for call in calls)
        )
        self.assertEqual(timeouts, [7, 7, 7, 7, 7])

    def test_profile_wait_clamps_probe_and_sleep_to_shared_deadline(self):
        now = [100.0]
        timeouts: list[int] = []
        sleeps: list[float] = []
        previous = {
            "_run": headless_runner._run,
            "_profile_ready": headless_runner._profile_ready,
            "sleep": headless_runner.time.sleep,
            "time": headless_runner.time.time,
            "monotonic": headless_runner.time.monotonic,
        }

        def fake_run(cmd, *, timeout=30):
            timeouts.append(timeout)
            return subprocess.CompletedProcess(
                cmd,
                7,
                stdout="",
                stderr="not ready",
            )

        headless_runner._run = fake_run
        headless_runner.time.monotonic = lambda: 154.0
        try:
            ready, _ = headless_runner._vss_base_ready(deadline=160.0)
        finally:
            headless_runner._run = previous["_run"]
            headless_runner.time.monotonic = previous["monotonic"]

        def fake_sleep(seconds):
            sleeps.append(seconds)
            now[0] += seconds

        with tempfile.TemporaryDirectory() as td:
            headless_runner._profile_ready = (
                lambda profile, deadline=None: (False, "not ready")
            )
            headless_runner.time.sleep = fake_sleep
            headless_runner.time.time = lambda: now[0]
            headless_runner.time.monotonic = lambda: now[0]
            try:
                report = headless_runner.wait_for_profile(
                    "base",
                    60,
                    Path(td),
                    deadline=110.0,
                )
            finally:
                headless_runner._profile_ready = previous["_profile_ready"]
                headless_runner.time.sleep = previous["sleep"]
                headless_runner.time.time = previous["time"]
                headless_runner.time.monotonic = previous["monotonic"]

        self.assertFalse(ready)
        self.assertEqual(timeouts, [6])
        self.assertFalse(report["ok"])
        self.assertEqual(sleeps, [10.0])

    def test_cli_launch_stops_openclaw_even_when_readiness_fails(self):
        calls: list[str] = []
        previous = {
            "run_openclaw_cli": headless_runner.run_openclaw_cli,
            "wait_for_profile": headless_runner.wait_for_profile,
            "collect_openclaw_cli_log": headless_runner.collect_openclaw_cli_log,
            "collect_and_publish_openclaw_trajectory": (
                headless_runner.collect_and_publish_openclaw_trajectory
            ),
            "stop_openclaw_cli": headless_runner.stop_openclaw_cli,
        }

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            prompt = root / "prompt.md"
            prompt.write_text("Deploy base", encoding="utf-8")
            log_dir = root / "logs"

            headless_runner.run_openclaw_cli = (
                lambda sandbox, message, timeout, logs, wait_profile="", deadline=None: {
                "status": 202,
                "body": {"ok": True},
                }
            )
            headless_runner.wait_for_profile = (
                lambda profile, timeout, logs, deadline=None: {
                    "waited": True,
                    "ok": False,
                    "profile": profile,
                }
            )
            headless_runner.collect_openclaw_cli_log = (
                lambda sandbox, logs, deadline=None: calls.append("collect")
            )
            headless_runner.collect_and_publish_openclaw_trajectory = (
                lambda sandbox, logs, agent_logs, prompt, deadline=None: (
                    calls.append("trajectory")
                )
            )
            headless_runner.stop_openclaw_cli = (
                lambda sandbox, deadline=None: calls.append("stop")
            )
            try:
                rc = headless_runner.main([
                    "--prompt-file",
                    str(prompt),
                    "--log-dir",
                    str(log_dir),
                    "--launch-mode",
                    "cli",
                    "--wait-profile",
                    "base",
                ])
            finally:
                headless_runner.run_openclaw_cli = previous["run_openclaw_cli"]
                headless_runner.wait_for_profile = previous["wait_for_profile"]
                headless_runner.collect_openclaw_cli_log = previous["collect_openclaw_cli_log"]
                headless_runner.collect_and_publish_openclaw_trajectory = previous[
                    "collect_and_publish_openclaw_trajectory"
                ]
                headless_runner.stop_openclaw_cli = previous["stop_openclaw_cli"]

        self.assertEqual(rc, 1)
        self.assertEqual(calls, ["stop", "collect", "trajectory"])

    def test_cli_launch_cleans_up_when_blocking_runner_raises(self):
        calls: list[str] = []
        previous = {
            "run_openclaw_cli": headless_runner.run_openclaw_cli,
            "collect_openclaw_cli_log": headless_runner.collect_openclaw_cli_log,
            "collect_and_publish_openclaw_trajectory": (
                headless_runner.collect_and_publish_openclaw_trajectory
            ),
            "stop_openclaw_cli": headless_runner.stop_openclaw_cli,
        }

        def raise_after_launch(*args, **kwargs):
            raise subprocess.TimeoutExpired("sandbox launcher", 30)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            prompt = root / "prompt.md"
            prompt.write_text("Deploy base", encoding="utf-8")
            log_dir = root / "logs"
            headless_runner.run_openclaw_cli = raise_after_launch
            headless_runner.stop_openclaw_cli = (
                lambda sandbox, deadline=None: calls.append("stop")
            )
            headless_runner.collect_openclaw_cli_log = (
                lambda sandbox, logs, deadline=None: calls.append("collect")
            )
            headless_runner.collect_and_publish_openclaw_trajectory = (
                lambda sandbox, logs, agent_logs, prompt, deadline=None: (
                    calls.append("trajectory")
                )
            )
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    rc = headless_runner.main(
                        [
                            "--prompt-file",
                            str(prompt),
                            "--log-dir",
                            str(log_dir),
                            "--launch-mode",
                            "cli",
                        ]
                    )
            finally:
                headless_runner.run_openclaw_cli = previous["run_openclaw_cli"]
                headless_runner.stop_openclaw_cli = previous["stop_openclaw_cli"]
                headless_runner.collect_openclaw_cli_log = previous[
                    "collect_openclaw_cli_log"
                ]
                headless_runner.collect_and_publish_openclaw_trajectory = previous[
                    "collect_and_publish_openclaw_trajectory"
                ]

            report = json.loads(
                (log_dir / "nemoclaw_hooks_response.json").read_text()
            )

        self.assertEqual(rc, 1)
        self.assertEqual(calls, ["stop", "collect", "trajectory"])
        self.assertEqual(
            report["response"]["error_type"],
            "TimeoutExpired",
        )

    def test_cli_prelaunch_retry_failure_does_not_relabel_attempt_one(self):
        preserve = mock.Mock()

        def fake_collect(sandbox, logs, deadline=None):
            (logs / "openclaw-agent.log").write_text(
                "attempt one remote log\n",
                encoding="utf-8",
            )

        failed_retry_launch = {
            "status": 500,
            "body": {
                "ok": False,
                "mode": "cli",
                "returncode": 70,
            },
            "stdout_tail": "",
            "stderr_tail": "pre-launch cleanup failed",
            "error": "continuation did not launch",
            "error_type": "OpenClawLaunchError",
            "attempts": 1,
            "attempt_2_launched": False,
            "retry": {
                "attempted": False,
                "attempt_2_launched": False,
                "launch_failed": True,
            },
        }

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            prompt = root / "prompt.md"
            prompt.write_text("Deploy base", encoding="utf-8")
            log_dir = root / "artifacts"
            agent_dir = root / "agent"
            with (
                mock.patch.object(
                    headless_runner,
                    "run_openclaw_cli",
                    return_value=failed_retry_launch,
                ),
                mock.patch.object(
                    headless_runner,
                    "stop_openclaw_cli",
                ),
                mock.patch.object(
                    headless_runner,
                    "collect_openclaw_cli_log",
                    side_effect=fake_collect,
                ),
                mock.patch.object(
                    headless_runner,
                    "collect_and_publish_openclaw_trajectory",
                ),
                mock.patch.object(
                    headless_runner,
                    "_preserve_openclaw_attempt_logs",
                    preserve,
                ),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                rc = headless_runner.main(
                    [
                        "--prompt-file",
                        str(prompt),
                        "--log-dir",
                        str(log_dir),
                        "--agent-log-dir",
                        str(agent_dir),
                        "--launch-mode",
                        "cli",
                    ]
                )

            canonical_log = (
                log_dir / "openclaw-agent.log"
            ).read_text(encoding="utf-8")
            attempt_two_exists = (
                log_dir / "openclaw-agent-attempt-2.log"
            ).exists()

        self.assertEqual(rc, 1)
        preserve.assert_not_called()
        self.assertEqual(canonical_log, "attempt one remote log\n")
        self.assertFalse(attempt_two_exists)

    def test_cli_launch_collects_evidence_after_cleanup_validation_error(self):
        calls: list[str] = []
        previous = {
            "run_openclaw_cli": headless_runner.run_openclaw_cli,
            "collect_openclaw_cli_log": headless_runner.collect_openclaw_cli_log,
            "collect_and_publish_openclaw_trajectory": (
                headless_runner.collect_and_publish_openclaw_trajectory
            ),
            "stop_openclaw_cli": headless_runner.stop_openclaw_cli,
        }

        def fail_stop(*args, **kwargs):
            calls.append("stop")
            raise RuntimeError("ownership validation failed")

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            prompt = root / "prompt.md"
            prompt.write_text("Deploy base", encoding="utf-8")
            log_dir = root / "logs"
            headless_runner.run_openclaw_cli = (
                lambda *args, **kwargs: {
                    "status": 200,
                    "body": {"ok": True},
                }
            )
            headless_runner.stop_openclaw_cli = fail_stop
            headless_runner.collect_openclaw_cli_log = (
                lambda sandbox, logs, deadline=None: calls.append("collect")
            )
            headless_runner.collect_and_publish_openclaw_trajectory = (
                lambda sandbox, logs, agent_logs, prompt, deadline=None: (
                    calls.append("trajectory")
                )
            )
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    rc = headless_runner.main(
                        [
                            "--prompt-file",
                            str(prompt),
                            "--log-dir",
                            str(log_dir),
                            "--launch-mode",
                            "cli",
                        ]
                    )
            finally:
                headless_runner.run_openclaw_cli = previous["run_openclaw_cli"]
                headless_runner.stop_openclaw_cli = previous["stop_openclaw_cli"]
                headless_runner.collect_openclaw_cli_log = previous[
                    "collect_openclaw_cli_log"
                ]
                headless_runner.collect_and_publish_openclaw_trajectory = previous[
                    "collect_and_publish_openclaw_trajectory"
                ]

            report = json.loads(
                (log_dir / "nemoclaw_hooks_response.json").read_text()
            )

        self.assertEqual(rc, 1)
        self.assertEqual(calls, ["stop", "collect", "trajectory"])
        self.assertEqual(
            report["response"]["error_type"],
            "OpenClawCleanupError",
        )

    def test_cli_launch_shares_timeout_with_profile_readiness(self):
        calls: list[tuple[str, float | int]] = []
        previous = {
            "run_openclaw_cli": headless_runner.run_openclaw_cli,
            "wait_for_profile": headless_runner.wait_for_profile,
            "collect_openclaw_cli_log": headless_runner.collect_openclaw_cli_log,
            "collect_and_publish_openclaw_trajectory": (
                headless_runner.collect_and_publish_openclaw_trajectory
            ),
            "stop_openclaw_cli": headless_runner.stop_openclaw_cli,
            "monotonic": headless_runner.time.monotonic,
        }
        now = iter([100.0])

        def fake_run_openclaw(
            sandbox,
            message,
            timeout,
            logs,
            wait_profile="",
            deadline=None,
        ):
            calls.append(("openclaw_timeout", timeout))
            calls.append(("deadline", deadline))
            return {"status": 200, "body": {"ok": True}}

        def fake_wait(profile, timeout, logs, deadline=None):
            calls.append(("readiness_timeout", timeout))
            calls.append(("readiness_deadline", deadline))
            return {"waited": True, "ok": True, "profile": profile}

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            prompt = root / "prompt.md"
            prompt.write_text("Deploy base", encoding="utf-8")
            log_dir = root / "logs"

            headless_runner.run_openclaw_cli = fake_run_openclaw
            headless_runner.wait_for_profile = fake_wait
            headless_runner.collect_openclaw_cli_log = (
                lambda sandbox, logs, deadline=None: None
            )
            headless_runner.collect_and_publish_openclaw_trajectory = (
                lambda sandbox, logs, agent_logs, prompt, deadline=None: {
                    "trajectory_steps": 2
                }
            )
            headless_runner.stop_openclaw_cli = (
                lambda sandbox, deadline=None: None
            )
            headless_runner.time.monotonic = lambda: next(now)
            try:
                rc = headless_runner.main([
                    "--prompt-file",
                    str(prompt),
                    "--log-dir",
                    str(log_dir),
                    "--launch-mode",
                    "cli",
                    "--wait-profile",
                    "base",
                    "--timeout",
                    "60",
                ])
            finally:
                headless_runner.run_openclaw_cli = previous["run_openclaw_cli"]
                headless_runner.wait_for_profile = previous["wait_for_profile"]
                headless_runner.collect_openclaw_cli_log = previous[
                    "collect_openclaw_cli_log"
                ]
                headless_runner.collect_and_publish_openclaw_trajectory = previous[
                    "collect_and_publish_openclaw_trajectory"
                ]
                headless_runner.stop_openclaw_cli = previous["stop_openclaw_cli"]
                headless_runner.time.monotonic = previous["monotonic"]

        self.assertEqual(rc, 0)
        self.assertEqual(
            calls,
            [
                ("openclaw_timeout", 60),
                ("deadline", 154.0),
                ("readiness_timeout", 60),
                ("readiness_deadline", 154.0),
            ],
        )

    def test_cli_success_fails_closed_when_current_trajectory_is_missing(self):
        previous = {
            "run_openclaw_cli": headless_runner.run_openclaw_cli,
            "collect_openclaw_cli_log": (
                headless_runner.collect_openclaw_cli_log
            ),
            "collect_and_publish_openclaw_trajectory": (
                headless_runner.collect_and_publish_openclaw_trajectory
            ),
            "stop_openclaw_cli": headless_runner.stop_openclaw_cli,
        }

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            prompt = root / "prompt.md"
            prompt.write_text("Deploy base", encoding="utf-8")
            log_dir = root / "artifacts"
            agent_dir = root / "agent"
            headless_runner.run_openclaw_cli = (
                lambda sandbox, message, timeout, logs, wait_profile="", deadline=None: {
                    "status": 200,
                    "body": {"ok": True},
                }
            )
            headless_runner.collect_openclaw_cli_log = (
                lambda sandbox, logs, deadline=None: None
            )
            headless_runner.collect_and_publish_openclaw_trajectory = (
                lambda sandbox, logs, agent_logs, message, deadline=None: (
                    (_ for _ in ()).throw(
                        RuntimeError("session transcript unavailable")
                    )
                )
            )
            headless_runner.stop_openclaw_cli = (
                lambda sandbox, deadline=None: None
            )
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    rc = headless_runner.main(
                        [
                            "--prompt-file",
                            str(prompt),
                            "--log-dir",
                            str(log_dir),
                            "--agent-log-dir",
                            str(agent_dir),
                            "--launch-mode",
                            "cli",
                        ]
                    )
            finally:
                headless_runner.run_openclaw_cli = previous[
                    "run_openclaw_cli"
                ]
                headless_runner.collect_openclaw_cli_log = previous[
                    "collect_openclaw_cli_log"
                ]
                headless_runner.collect_and_publish_openclaw_trajectory = (
                    previous["collect_and_publish_openclaw_trajectory"]
                )
                headless_runner.stop_openclaw_cli = previous[
                    "stop_openclaw_cli"
                ]

            report = json.loads(
                (log_dir / "nemoclaw_hooks_response.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(rc, 1)
        self.assertEqual(
            report["response"]["error_type"],
            "OpenClawTrajectoryError",
        )
        self.assertIn(
            "session transcript unavailable",
            report["response"]["error"],
        )

    def test_cli_cleanup_makes_no_remote_call_after_overall_deadline(self):
        now = [100.0]
        remote_calls: list[str] = []
        previous = {
            "run_openclaw_cli": headless_runner.run_openclaw_cli,
            "_sandbox_exec": headless_runner._sandbox_exec,
            "monotonic": headless_runner.time.monotonic,
        }

        def fake_run_openclaw(
            sandbox,
            message,
            timeout,
            logs,
            wait_profile="",
            deadline=None,
        ):
            now[0] = 161.0
            return {"status": 500, "body": {"ok": False}}

        def fake_sandbox_exec(sandbox, script, *, timeout):
            remote_calls.append(script)
            return subprocess.CompletedProcess(
                ["sandbox", sandbox],
                0,
                stdout="",
                stderr="",
            )

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            prompt = root / "prompt.md"
            prompt.write_text("Deploy base", encoding="utf-8")
            log_dir = root / "logs"
            headless_runner.run_openclaw_cli = fake_run_openclaw
            headless_runner._sandbox_exec = fake_sandbox_exec
            headless_runner.time.monotonic = lambda: now[0]
            try:
                rc = headless_runner.main([
                    "--prompt-file",
                    str(prompt),
                    "--log-dir",
                    str(log_dir),
                    "--launch-mode",
                    "cli",
                    "--timeout",
                    "60",
                ])
            finally:
                headless_runner.run_openclaw_cli = previous["run_openclaw_cli"]
                headless_runner._sandbox_exec = previous["_sandbox_exec"]
                headless_runner.time.monotonic = previous["monotonic"]

            cleanup_log = (log_dir / "openclaw-cleanup.log").read_text(
                encoding="utf-8"
            )

        self.assertEqual(rc, 1)
        self.assertEqual(remote_calls, [])
        self.assertIn("deadline exceeded", cleanup_log)

    def test_sandbox_exec_wraps_multiline_scripts_for_openshell(self):
        calls: list[tuple[str, ...]] = []
        previous = {
            "_run": headless_runner._run,
            "shutil_which": headless_runner.shutil_which,
        }

        def fake_run(cmd, *, timeout=30):
            calls.append(tuple(cmd))
            return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

        headless_runner._run = fake_run
        headless_runner.shutil_which = lambda name: "/usr/bin/openshell" if name == "openshell" else None
        try:
            with mock.patch.dict(
                os.environ,
                {"NEMOCLAW_GATEWAY_PORT": "19080"},
                clear=False,
            ):
                result = headless_runner._sandbox_exec(
                    "demo",
                    "echo one\necho two",
                    timeout=30,
                )
        finally:
            headless_runner._run = previous["_run"]
            headless_runner.shutil_which = previous["shutil_which"]

        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(calls), 1)
        command = calls[0]
        self.assertEqual(
            command[:8],
            (
                "openshell",
                "sandbox",
                "exec",
                "-n",
                "demo",
                "-g",
                "nemoclaw-19080",
                "--",
            ),
        )
        self.assertTrue(all("\n" not in arg and "\r" not in arg for arg in command))
        self.assertIn("base64 -d", " ".join(command))

    def test_gateway_health_uses_idempotent_sandbox_recover(self):
        calls: list[tuple[str, ...]] = []
        previous = headless_runner._run

        def fake_run(cmd, *, timeout=30):
            calls.append(tuple(cmd))
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td)
            headless_runner._run = fake_run
            try:
                headless_runner.ensure_openclaw_gateway("demo", log_dir)
            finally:
                headless_runner._run = previous

            recover_log = (log_dir / "openclaw_gateway_recover.log").read_text(encoding="utf-8")

        self.assertIn("sandbox recover", recover_log)
        self.assertIn("returncode=0", recover_log)
        self.assertEqual(
            calls,
            [("nemoclaw", "sandbox", "recover", "demo")],
        )

    def test_gateway_nonzero_recovery_fails_closed(self):
        calls: list[tuple[str, ...]] = []
        previous = headless_runner._run

        def fake_run(cmd, *, timeout=30):
            calls.append(tuple(cmd))
            return subprocess.CompletedProcess(
                cmd,
                1,
                stdout="recovery refused",
                stderr="route ownership mismatch",
            )

        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td)
            headless_runner._run = fake_run
            try:
                with self.assertRaisesRegex(
                    RuntimeError,
                    "gateway recovery failed",
                ):
                    headless_runner.ensure_openclaw_gateway("demo", log_dir)
            finally:
                headless_runner._run = previous

            recover_log = (log_dir / "openclaw_gateway_recover.log").read_text(encoding="utf-8")

        self.assertIn("sandbox recover", recover_log)
        self.assertIn("returncode=1", recover_log)
        self.assertIn("recovery refused", recover_log)
        self.assertEqual(
            calls,
            [("nemoclaw", "sandbox", "recover", "demo")],
        )

    def test_gateway_recovery_timeout_fails_closed(self):
        calls: list[tuple[str, ...]] = []
        previous = headless_runner._run

        def fake_run(cmd, *, timeout=30):
            calls.append(tuple(cmd))
            raise subprocess.TimeoutExpired(cmd, timeout)

        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td)
            headless_runner._run = fake_run
            try:
                with self.assertRaisesRegex(
                    RuntimeError,
                    "gateway recovery timed out",
                ):
                    headless_runner.ensure_openclaw_gateway("demo", log_dir)
            finally:
                headless_runner._run = previous

            recover_log = (
                log_dir / "openclaw_gateway_recover.log"
            ).read_text(encoding="utf-8")

        self.assertIn("sandbox recover timed out", recover_log)
        self.assertEqual(
            calls,
            [("nemoclaw", "sandbox", "recover", "demo")],
        )


class NemoClawSmokeRunnerTest(unittest.TestCase):
    BREV_AUTH_TRACE = (
        "github.com/brevdev/brev-cli/pkg/auth.Auth.PromptForLogin\n"
        "/go/src/github.com/brevdev/brev-cli/pkg/auth/auth.go:247\n"
        ": [error]\n"
        "github.com/brevdev/brev-cli/pkg/auth.shouldLogin\n"
        ": EOF\nEOF\n"
    )

    def setUp(self):
        # Scheduling tests use compressed synthetic clocks and budgets. Keep
        # those fixtures small without weakening the production constants that
        # main() validates before a real Harbor invocation.
        runtime_budget_patch = mock.patch.multiple(
            smoke_runner.worker_pool,
            DEFAULT_HARBOR_TIMEOUT_SEC=3300,
            MIN_HARBOR_BACKSTOP_SEC=0,
        )
        runtime_budget_patch.start()
        self.addCleanup(runtime_budget_patch.stop)

    def test_default_smoke_profile_is_lightweight_base(self):
        self.assertEqual(smoke_runner.DEFAULT_PROFILE, "base")
        self.assertEqual(
            smoke_runner._gpu_count_from_spec("base", "RTXPRO6000BW"),
            1,
        )

    def test_harbor_command_pins_python_and_harbor_runtime(self):
        scenario = smoke_runner.NemoClawScenario(
            skill="vss-ask-video",
            spec_name="base_profile_video_understanding",
            spec_path=Path("/tmp/spec.json"),
            platform="RTXPRO6000BW",
            gpu_count=1,
            task_dir=Path("/tmp/task"),
            harbor_path=Path("/tmp/harbor"),
            task_name="rtxpro6000bw",
            deployment_profile="base",
        )
        with (
            mock.patch.object(smoke_runner, "_ensure_uvx", return_value="/usr/bin/uvx"),
            mock.patch.dict(
                os.environ,
                {
                    "ANTHROPIC_MODEL": "model",
                    "ANTHROPIC_BASE_URL": "https://example.test/v1",
                },
                clear=True,
            ),
        ):
            command = smoke_runner._harbor_command(
                scenario,
                Path("/tmp/results"),
                "run-id",
            )

        self.assertEqual(
            command[:7],
            [
                "/usr/bin/uvx",
                "--python",
                sys.executable,
                "--from",
                smoke_runner.worker_pool.HARBOR_REQUIREMENT,
                "harbor",
                "run",
            ],
        )

    def test_runner_blocks_before_harbor_when_remaining_budget_is_unsafe(self):
        scenario = smoke_runner.NemoClawScenario(
            skill="vss-deploy-profile",
            spec_name="base",
            spec_path=Path("/tmp/base.json"),
            platform="RTXPRO6000BW",
            gpu_count=1,
            task_dir=Path("/tmp/dataset/base/rtxpro6000bw"),
            harbor_path=Path("/tmp/dataset/base"),
            task_name="rtxpro6000bw",
            deployment_profile="base",
        )
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            blocked = mock.Mock()
            harbor_command = mock.Mock(return_value=["harbor", "run"])
            stream_command = mock.Mock()
            with (
                mock.patch.dict(
                    os.environ,
                    {
                        "GITHUB_RUN_ID": "underfunded-run",
                        "NEMOCLAW_RUN_TIMEOUT_SEC": "50",
                    },
                ),
                mock.patch.object(
                    smoke_runner.worker_pool,
                    "MIN_HARBOR_BACKSTOP_SEC",
                    100,
                ),
                mock.patch.object(
                    smoke_runner,
                    "_discover_scenarios",
                    return_value=([scenario], []),
                ),
                mock.patch.object(
                    smoke_runner,
                    "_scenario_groups",
                    return_value=[[scenario]],
                ),
                mock.patch.object(
                    smoke_runner,
                    "_select_and_lock_instance",
                    return_value=(
                        "vss-eval-rtx-1g-2",
                        smoke_runner.WorkerLock(123, object(), None),
                    ),
                ),
                mock.patch.object(smoke_runner, "_release_lock"),
                mock.patch.object(
                    smoke_runner,
                    "_append_blocked_summary",
                    blocked,
                ),
                mock.patch.object(
                    smoke_runner,
                    "_harbor_command",
                    harbor_command,
                ),
                mock.patch.object(
                    smoke_runner,
                    "_stream_command",
                    stream_command,
                ),
            ):
                rc = smoke_runner.main(
                    [
                        "--skills",
                        "vss-deploy-profile",
                        "--harbor-timeout",
                        "200",
                        "--dataset-root",
                        str(root / "dataset"),
                        "--results-root",
                        str(root / "results"),
                        "--scratch-root",
                        str(root / "scratch"),
                    ]
                )

        self.assertEqual(rc, 1)
        harbor_command.assert_not_called()
        stream_command.assert_not_called()
        blocked.assert_called_once()
        self.assertIn(
            "remaining smoke-run budget cannot safely start Harbor",
            blocked.call_args.kwargs["reason"],
        )

    def test_report_adapter_receives_query_analytics_dependency(self):
        with tempfile.TemporaryDirectory() as td:
            output_root = Path(td) / "dataset"
            run = mock.Mock(
                return_value=subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout="",
                    stderr="",
                )
            )
            with (
                mock.patch.object(
                    smoke_runner,
                    "_adapter_help",
                    return_value=(
                        "--spec --platform --deploy-skill-dir "
                        "--video-io-skill-dir --query-analytics-skill-dir"
                    ),
                ),
                mock.patch.object(smoke_runner, "_run", run),
            ):
                smoke_runner._run_adapter(
                    skill="vss-generate-video-report",
                    spec_path=(
                        REPO_ROOT
                        / "skills"
                        / "vss-generate-video-report"
                        / "evals"
                        / "base_profile_report.json"
                    ),
                    platform="RTXPRO6000BW",
                    output_root=output_root,
                )

        command = run.call_args.args[0]
        dependency_index = command.index("--query-analytics-skill-dir")
        self.assertEqual(
            command[dependency_index + 1],
            "skills/vss-query-analytics",
        )

    def test_brev_json_parser_ignores_trailing_cli_text(self):
        raw = '[{"name":"vss-eval-rtx-1g-2","status":"RUNNING READY"}]\nNext steps...'

        parsed = smoke_runner._parse_brev_json(raw)

        self.assertEqual(parsed[0]["name"], "vss-eval-rtx-1g-2")

    def test_brev_json_parser_accepts_workspaces_object(self):
        raw = json.dumps(
            {
                "workspaces": [
                    {
                        "id": "instance-explicit",
                        "name": "vss-eval-rtx-1g-10",
                        "status": "RUNNING",
                        "gpu": "RTXPro6000",
                    }
                ]
            }
        )

        parsed = smoke_runner._parse_brev_json(raw)

        self.assertEqual(parsed[0]["id"], "instance-explicit")
        self.assertEqual(parsed[0]["name"], "vss-eval-rtx-1g-10")

    def test_inventory_tracks_registered_nodes_from_shared_pool_snapshot(self):
        snapshot = [
            {
                "id": "managed-1",
                "name": "vss-eval-rtx-1g-2",
                "status": "RUNNING",
                "gpu": "RTX PRO 6000",
            },
            {
                "name": "vss-eval-rtx-2g-VM1b",
                "status": "RUNNING",
                "gpu": "RTX PRO 6000",
                "instance_type": "registered-external-node",
                "_registered": True,
            },
        ]

        previous_registered = set(smoke_runner._REGISTERED_WORKERS)
        try:
            with mock.patch.object(
                smoke_runner.worker_pool,
                "_list_pool_instances",
                return_value=snapshot,
            ):
                instances = smoke_runner._list_instances()
                registered_workers = set(
                    smoke_runner._REGISTERED_WORKERS
                )
        finally:
            smoke_runner._REGISTERED_WORKERS.clear()
            smoke_runner._REGISTERED_WORKERS.update(previous_registered)

        by_name = {instance["name"]: instance for instance in instances}
        self.assertIn("vss-eval-rtx-1g-2", by_name)
        self.assertIn("vss-eval-rtx-2g-VM1b", by_name)
        registered = by_name["vss-eval-rtx-2g-VM1b"]
        self.assertTrue(registered["_registered"])
        self.assertEqual(registered["status"], "RUNNING")
        self.assertEqual(registered["gpu"], "RTX PRO 6000")
        self.assertIn(
            "vss-eval-rtx-2g-vm1b",
            registered_workers,
        )

    def test_instance_candidates_prefer_matching_gpu_partition(self):
        instances = [
            {
                "name": "vss-eval-rtx-2g",
                "status": "RUNNING",
                "gpu": "RTX PRO 6000",
                "instance_type": "g7e.12xlarge",
            },
            {
                "name": "vss-eval-rtx-1g-2",
                "status": "RUNNING",
                "gpu": "RTX PRO 6000",
                "instance_type": "g7e.4xlarge",
            },
            {
                "name": "personal-rtx",
                "status": "RUNNING READY",
                "gpu": "RTX PRO 6000",
            },
        ]

        candidates = smoke_runner._instance_candidates(
            instances,
            platform="RTXPRO6000BW",
            gpu_count=1,
        )

        self.assertEqual(candidates[0], "vss-eval-rtx-1g-2")
        self.assertNotIn("personal-rtx", candidates)
        self.assertIn("vss-eval-rtx-2g", candidates)

    def test_instance_candidates_prefer_dedicated_registered_pool_tier(self):
        instances = [
            {
                "name": "vss-eval-rtx-1g-2",
                "status": "RUNNING",
                "gpu": "RTX PRO 6000",
            },
            {
                "name": "vss-eval-rtx-2g-VM1b",
                "status": "RUNNING",
                "gpu": "RTX PRO 6000",
                "_registered": True,
            },
        ]

        candidates = smoke_runner._instance_candidates(
            instances,
            platform="RTXPRO6000BW",
            gpu_count=1,
        )

        self.assertEqual(
            candidates,
            ["vss-eval-rtx-2g-VM1b", "vss-eval-rtx-1g-2"],
        )

    def test_registered_candidates_require_matching_gpu_type_and_count(self):
        instances = [
            {
                "name": "vss-eval-geforce-rtx4090-vm1",
                "status": "RUNNING",
                "gpu": "GEFORCE RTX 4090",
                "_registered": True,
            },
            {
                "name": "vss-eval-rtx-1g-VM1b",
                "status": "RUNNING",
                "gpu": "RTX PRO 6000",
                "_registered": True,
            },
            {
                "name": "vss-eval-rtx-2g-VM2b",
                "status": "RUNNING",
                "gpu": "RTX PRO 6000",
                "_registered": True,
            },
        ]

        candidates = smoke_runner._instance_candidates(
            instances,
            platform="RTXPRO6000BW",
            gpu_count=2,
        )

        self.assertEqual(candidates, ["vss-eval-rtx-2g-VM2b"])

    def test_instance_candidates_allow_larger_partition_for_one_gpu_smoke(self):
        instances = [
            {"name": "vss-eval-rtx-1g-2", "status": "RUNNING", "gpu": "RTX PRO 6000"},
            {"name": "vss-eval-rtx-2g-4", "status": "RUNNING", "gpu": "RTX PRO 6000"},
        ]

        one_gpu = smoke_runner._instance_candidates(
            instances,
            platform="RTXPRO6000BW",
            gpu_count=1,
        )
        two_gpu = smoke_runner._instance_candidates(
            instances,
            platform="RTXPRO6000BW",
            gpu_count=2,
        )

        self.assertEqual(one_gpu, ["vss-eval-rtx-1g-2", "vss-eval-rtx-2g-4"])
        self.assertEqual(two_gpu, ["vss-eval-rtx-2g-4"])

    def test_instance_candidates_allow_any_platform_for_gpu_free_tasks(self):
        instances = [
            {"name": "vss-eval-l40s-1g", "status": "RUNNING", "gpu": "L40S"},
            {"name": "vss-eval-rtx-1g", "status": "RUNNING", "gpu": "RTX PRO 6000"},
            {"name": "personal-l40s", "status": "RUNNING", "gpu": "L40S"},
        ]

        candidates = smoke_runner._instance_candidates(
            instances,
            platform="ANY",
            gpu_count=0,
        )

        self.assertCountEqual(candidates, ["vss-eval-l40s-1g", "vss-eval-rtx-1g"])
        self.assertNotIn("personal-l40s", candidates)

    def test_all_skills_matrix_uses_one_representative_row_per_skill(self):
        rows, blockers = smoke_runner._build_matrix(
            skills_filter="*",
            profile_filter=None,
            platform_filter=None,
            spec_filter=None,
            representative_per_skill=True,
        )

        skills = [row["skill"] for row in rows]
        self.assertEqual(len(skills), len(set(skills)))
        self.assertEqual(
            {
                row["skill"]: (row["spec_stem"], int(row["task_limit"]))
                for row in rows
            },
            {
                "vss-ask-video": (
                    "base_profile_video_understanding",
                    4,
                ),
                "vss-deploy-dense-captioning": (
                    "alerts_profile_api",
                    2,
                ),
                "vss-deploy-profile": ("base", 1),
                "vss-generate-video-report": (
                    "base_profile_report",
                    4,
                ),
                "vss-manage-alerts": ("alerts_vlm_real_time", 2),
                "vss-query-analytics": ("query_analytics", 3),
                "vss-setup-behavior-analytics": (
                    "deploy_search_and_alerts",
                    1,
                ),
                "vss-summarize-video": ("lvs_api_ops", 2),
            },
        )
        self.assertIn("vss-deploy-profile", skills)
        self.assertIn("vss-ask-video", skills)
        deploy_row = next(row for row in rows if row["skill"] == "vss-deploy-profile")
        self.assertEqual(deploy_row["spec_stem"], "base")
        self.assertNotIn("vss-deploy-detection-tracking-2d", skills)
        self.assertNotIn("vss-deploy-detection-tracking-3d", skills)
        self.assertNotIn("vss-deploy-video-embedding", skills)
        self.assertNotIn("vss-generate-video-calibration", skills)
        self.assertNotIn("vss-manage-video-io-storage", skills)
        self.assertNotIn("vss-search-archive", skills)
        self.assertIn("vss-setup-behavior-analytics", skills)
        self.assertNotIn("vss-setup-video-analytics-api", skills)
        self.assertNotIn("evals", [row["spec_stem"] for row in rows])
        behavior_row = next(
            row
            for row in rows
            if row["skill"] == "vss-setup-behavior-analytics"
        )
        self.assertEqual(behavior_row["spec_stem"], "deploy_search_and_alerts")
        self.assertEqual(behavior_row["platform"], "ANY")
        self.assertTrue(
            any("vss-generate-video-report-rag: missing Harbor adapter" in item for item in blockers)
        )
        self.assertTrue(
            any(
                "vss-setup-behavior-analytics/standalone_deploy.json: standalone host-Docker eval"
                in item
                for item in blockers
            )
        )
        self.assertTrue(
            any(
                "vss-search-archive/search.json: search archive is not yet bounded"
                in item
                for item in blockers
            )
        )
        self.assertTrue(
            any(
                "vss-deploy-profile/alerts_cv.json: alerts CV mode requires real RT-CV model artifacts"
                in item
                for item in blockers
            )
        )

    def test_combined_behavior_analytics_uses_repo_config_override(self):
        compose = (
            REPO_ROOT
            / "deploy"
            / "docker"
            / "developer-profiles"
            / "dev-profile-search"
            / "video-analytics-2d-app"
            / "compose.yml"
        ).read_text(encoding="utf-8")
        reference = (
            REPO_ROOT
            / "skills"
            / "vss-setup-behavior-analytics"
            / "references"
            / "deploy-behavior-analytics-service.md"
        ).read_text(encoding="utf-8")
        integration_reference = (
            REPO_ROOT
            / "skills"
            / "vss-setup-behavior-analytics"
            / "references"
            / "integrate-behavior-analytics-service.md"
        ).read_text(encoding="utf-8")
        config = json.loads(
            (
                REPO_ROOT
                / "services"
                / "analytics"
                / "behavior-analytics"
                / "configs"
                / "search_and_alerts_config.json"
            ).read_text(encoding="utf-8")
        )
        eval_spec = json.loads(
            (
                REPO_ROOT
                / "skills"
                / "vss-setup-behavior-analytics"
                / "evals"
                / "deploy_search_and_alerts.json"
            ).read_text(encoding="utf-8")
        )
        checks = eval_spec["expects"][0]["checks"]
        roi_eval_spec = json.loads(
            (
                REPO_ROOT
                / "skills"
                / "vss-setup-behavior-analytics"
                / "evals"
                / "roi_bbox_overlap.json"
            ).read_text(encoding="utf-8")
        )
        roi_checks = roi_eval_spec["expects"][0]["checks"]
        roi_entrypoint_check = roi_checks[1]
        fov_checks = json.loads(
            (
                REPO_ROOT
                / "skills"
                / "vss-setup-behavior-analytics"
                / "evals"
                / "fov_count_alert.json"
            ).read_text(encoding="utf-8")
        )["expects"][0]["checks"]
        proximity_checks = json.loads(
            (
                REPO_ROOT
                / "skills"
                / "vss-setup-behavior-analytics"
                / "evals"
                / "proximity_alert.json"
            ).read_text(encoding="utf-8")
        )["expects"][0]["checks"]
        workers = {
            item["name"]: int(item["value"])
            for item in config["app"]
            if item["name"].startswith("numWorkersFor")
        }

        self.assertIn(
            (
                "${VSS_BEHAVIOR_ANALYTICS_CONFIG_PATH:-${VSS_APPS_DIR}/"
                "developer-profiles/dev-profile-search/video-analytics-2d-app/"
                "vss-search-analytics/configs/vss-search-analytics-"
                "${STREAM_TYPE}-config.json}:"
                "/resources/vss-search-analytics-config.json"
            ),
            compose,
        )
        self.assertIn(
            (
                "VSS_BEHAVIOR_ANALYTICS_CONFIG_PATH=${VSS_APPS_DIR}/"
                "../../services/analytics/behavior-analytics/configs/"
                "search_and_alerts_config.json"
            ),
            reference,
        )
        bounded_profiles = re.search(
            r'"COMPOSE_PROFILES=([^"]+)"',
            reference,
        )
        self.assertIsNotNone(bounded_profiles)
        self.assertEqual(
            set(bounded_profiles.group(1).split(",")),
            {
                "vss-search-analytics-2d-fusion",
                "kafka",
                "kafka-topic-init-container",
                "broker-health-check",
            },
        )
        self.assertEqual(
            workers,
            {
                "numWorkersForIncidentGeneration": 2,
                "numWorkersForBehaviorCreation": 2,
                "numWorkersForEmbedFiltering": 1,
            },
        )
        self.assertIn("grep -qx vss-behavior-analytics", checks[0])
        self.assertIn("docker inspect vss-behavior-analytics", checks[1])
        self.assertIn("/resources/vss-search-analytics-config.json", checks[1])
        self.assertIn("config_dest=$(docker inspect vss-behavior-analytics", checks[2])
        self.assertIn("index(\"--config\")", checks[2])
        self.assertIn("--arg dest \"$config_dest\"", checks[2])
        self.assertIn("select(.Destination == $dest)", checks[2])
        self.assertIn("test -f \"$config_src\"", checks[2])
        self.assertIn("do not evaluate any other mounted config", checks[2])
        self.assertNotIn("/resources/*config.json", checks[2])
        self.assertNotIn("without ROI / tripwire events", reference)
        self.assertIn("including ROI / tripwire events", reference)
        self.assertIn("main_search_and_alerts_app.py", roi_entrypoint_check)
        self.assertIn("main_composite_app.py", roi_entrypoint_check)
        self.assertNotIn("must NOT be main_search_and_alerts_app.py", roi_entrypoint_check)
        self.assertIn("grep -qx vss-behavior-analytics", roi_checks[0])
        self.assertIn("docker inspect vss-behavior-analytics", roi_entrypoint_check)
        self.assertIn("select(.Destination == $dest)", roi_checks[2])
        self.assertIn("roiEventDetectionMode", roi_checks[2])
        self.assertIn("numWorkersForBehaviorCreation", roi_checks[2])
        self.assertIn("last // 0) > 0", roi_checks[2])
        self.assertIn('select(.name == "sinkType")', roi_checks[2])
        self.assertIn(".redisStream.streams", roi_checks[2])
        self.assertIn('any($destinations[]?; .name == "events"', roi_checks[2])
        self.assertNotIn("Could not find a kafka topic with key", integration_reference)
        self.assertIn(
            "No destination configured for '<key>'; output for it is disabled",
            integration_reference,
        )
        self.assertIn("Configured topics must be pre-created", integration_reference)
        self.assertIn('`"geo"` | `CalibrationG`', reference)
        self.assertNotIn('`"geo"` | `Calibration` |', reference)
        self.assertIn("apps/composite/main_composite_app.py", reference)
        self.assertIn("configs/composite_config.json", reference)
        self.assertIn("apps/composite/main_composite_app.py", integration_reference)
        for scenario_checks in (fov_checks, proximity_checks):
            self.assertIn("grep -qx vss-behavior-analytics", scenario_checks[0])
            self.assertTrue(any("main_composite_app.py" in check for check in scenario_checks))
            self.assertTrue(
                any("docker inspect vss-behavior-analytics" in check for check in scenario_checks)
            )
            self.assertTrue(any("select(.Destination == $dest)" in check for check in scenario_checks))
            self.assertTrue(any("numWorkersForFrameEnhancement" in check for check in scenario_checks))
            self.assertTrue(any("--arg app \"$app\"" in check for check in scenario_checks))
            self.assertTrue(
                any('if $app == "main_search_and_alerts_app.py"' in check for check in scenario_checks)
            )
            self.assertTrue(any('.name == "incidents"' in check for check in scenario_checks))
            self.assertFalse(any("/resources/*config.json" in check for check in scenario_checks))

    def test_manual_single_skill_matrix_uses_representative_row_by_default(self):
        previous_env = {
            key: os.environ.pop(key, None)
            for key in (
                "MANUAL_SKILLS_FILTER",
                "NEMOCLAW_EVAL_SPEC",
                "NEMOCLAW_EVAL_PLATFORM",
                "NEMOCLAW_ALL_SPECS",
            )
        }
        stdout = io.StringIO()
        stderr = io.StringIO()
        try:
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                rc = smoke_runner.main([
                    "--print-matrix",
                    "--skills",
                    "vss-manage-alerts",
                ])
        finally:
            for key, value in previous_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.assertEqual(rc, 0)
        rows = json.loads(stdout.getvalue())["include"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["skill"], "vss-manage-alerts")
        self.assertEqual(rows[0]["spec_stem"], "alerts_vlm_real_time")
        self.assertEqual(rows[0]["platform"], "RTXPRO6000BW")
        self.assertEqual(rows[0]["task_limit"], "2")

    def test_representative_task_prefixes_reach_target_skill_behavior(self):
        expected_target_queries = {
            ("vss-ask-video", "base_profile_video_understanding"): (
                4,
                "wearing PPE",
            ),
            ("vss-deploy-dense-captioning", "alerts_profile_api"): (
                2,
                "/vss-deploy-dense-captioning",
            ),
            ("vss-deploy-profile", "base"): (
                1,
                "/vss-deploy-profile",
            ),
            ("vss-generate-video-report", "base_profile_report"): (
                4,
                "Give me a report",
            ),
            ("vss-manage-alerts", "alerts_vlm_real_time"): (
                2,
                "start an alert",
            ),
            ("vss-query-analytics", "query_analytics"): (
                3,
                "List the sensors",
            ),
            ("vss-setup-behavior-analytics", "deploy_search_and_alerts"): (
                1,
                "/vss-setup-behavior-analytics",
            ),
            ("vss-summarize-video", "lvs_api_ops"): (
                2,
                "vss-summarize-video",
            ),
        }

        self.assertEqual(
            smoke_runner.REPRESENTATIVE_TASK_LIMITS,
            {
                key: limit
                for key, (limit, _query_marker) in expected_target_queries.items()
            },
        )
        for (skill, spec_stem), (limit, query_marker) in (
            expected_target_queries.items()
        ):
            with self.subTest(skill=skill, spec=spec_stem):
                spec = json.loads(
                    (
                        REPO_ROOT
                        / "skills"
                        / skill
                        / "evals"
                        / f"{spec_stem}.json"
                    ).read_text(encoding="utf-8")
                )
                expects = spec["expects"]
                self.assertGreaterEqual(len(expects), limit)
                self.assertIn(query_marker, expects[limit - 1]["query"])

    def test_query_analytics_representative_prefix_accepts_clean_state(self):
        spec = json.loads(
            (
                REPO_ROOT
                / "skills"
                / "vss-query-analytics"
                / "evals"
                / "query_analytics.json"
            ).read_text(encoding="utf-8")
        )
        limit = smoke_runner.REPRESENTATIVE_TASK_LIMITS[
            ("vss-query-analytics", "query_analytics")
        ]
        representative = spec["expects"][:limit]

        self.assertEqual(limit, 3)
        self.assertIn("Do not seed sensors or incidents", representative[0]["query"])
        self.assertIn(
            "fresh deployment may legitimately return empty sensor or incident lists",
            representative[0]["query"],
        )
        self.assertNotIn("return non-empty results", representative[0]["query"])
        self.assertTrue(
            all("nemoclaw_sample_files" not in expect for expect in representative)
        )

        liveness_checks = representative[1]["checks"]
        sensor_checks = representative[2]["checks"]
        self.assertIn("${HOST_IP:-localhost}", liveness_checks[0])
        self.assertIn("host.openshell.internal", liveness_checks[0])
        self.assertIn("${HOST_IP:-localhost}", sensor_checks[0])
        self.assertIn("host.openshell.internal", sensor_checks[0])
        self.assertIn("if the tool returned `[]`", sensor_checks[-1])
        self.assertIn("no sensors are currently registered", sensor_checks[-1])

        # The live verifier runs on the worker host, where localhost is the
        # correct route. Only trajectory checks need the sandbox host alias.
        self.assertEqual(
            liveness_checks[1],
            "`curl -sf --max-time 5 -o /dev/null -w '%{http_code}' "
            "http://localhost:9901/mcp` returns an HTTP code in the "
            "2xx/3xx/405/406 range (the endpoint exists and speaks MCP; a "
            "bare GET without `Accept: text/event-stream` legitimately gets "
            "406 from a streamable-HTTP MCP server) — not connection refused.",
        )

    def test_representative_matrix_blocks_unregistered_task_prefix(self):
        unknown_spec = (
            REPO_ROOT
            / "skills"
            / "vss-ask-video"
            / "evals"
            / "future.json"
        )
        with mock.patch.object(
            smoke_runner,
            "_selected_specs",
            return_value=(
                [("vss-ask-video", unknown_spec, ["RTXPRO6000BW"])],
                [],
            ),
        ):
            rows, blockers = smoke_runner._build_matrix(
                skills_filter="vss-ask-video",
                profile_filter=None,
                platform_filter=None,
                spec_filter=None,
                representative_per_skill=True,
            )

        self.assertEqual(rows, [])
        self.assertEqual(
            blockers,
            [
                "vss-ask-video/future.json: no bounded representative "
                "task prefix is registered for the NemoClaw sweep"
            ],
        )

    def test_alerts_cv_is_blocked_for_nemoclaw_unless_rt_cv_is_enabled(self):
        previous = os.environ.pop("NEMOCLAW_ENABLE_RTCV", None)
        try:
            rows, blockers = smoke_runner._build_matrix(
                skills_filter="vss-deploy-profile",
                profile_filter=None,
                platform_filter=None,
                spec_filter="alerts_cv",
                representative_per_skill=False,
            )

            self.assertEqual(rows, [])
            self.assertTrue(
                any("alerts CV mode requires real RT-CV model artifacts" in item for item in blockers)
            )

            os.environ["NEMOCLAW_ENABLE_RTCV"] = "1"
            rows, blockers = smoke_runner._build_matrix(
                skills_filter="vss-deploy-profile",
                profile_filter=None,
                platform_filter=None,
                spec_filter="alerts_cv",
                representative_per_skill=False,
            )

            self.assertEqual(blockers, [])
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["spec_stem"], "alerts_cv")
        finally:
            if previous is None:
                os.environ.pop("NEMOCLAW_ENABLE_RTCV", None)
            else:
                os.environ["NEMOCLAW_ENABLE_RTCV"] = previous

    def test_standalone_host_docker_spec_is_blocked_while_live_specs_run(self):
        rows, blockers = smoke_runner._build_matrix(
            skills_filter="vss-setup-behavior-analytics",
            profile_filter=None,
            platform_filter=None,
            spec_filter=None,
            representative_per_skill=False,
        )

        self.assertEqual(
            {row["spec_stem"] for row in rows},
            {
                "deploy_search_and_alerts",
                "fov_count_alert",
                "proximity_alert",
                "roi_bbox_overlap",
            },
        )
        self.assertTrue(all(row["platform"] == "ANY" for row in rows))
        self.assertTrue(
            any(
                "vss-setup-behavior-analytics/standalone_deploy.json: standalone host-Docker eval"
                in item
                for item in blockers
            )
        )

    def test_explicit_array_spec_is_not_treated_as_nemoclaw_live_scenario(self):
        rows, blockers = smoke_runner._build_matrix(
            skills_filter="vss-search-archive",
            profile_filter=None,
            platform_filter=None,
            spec_filter="evals",
            representative_per_skill=False,
        )

        self.assertEqual(rows, [])
        self.assertTrue(
            any(
                "vss-search-archive/evals.json: array-format skill eval is not a NemoClaw live scenario"
                in item
                for item in blockers
            )
        )

    def test_task_dir_sort_key_orders_steps_naturally(self):
        root = Path("/tmp/dataset/base/l40s")
        task_dirs = [root / "step-10", root / "step-2", root / "step-1"]

        ordered = sorted(task_dirs, key=smoke_runner._task_dir_sort_key)

        self.assertEqual([path.name for path in ordered], ["step-1", "step-2", "step-10"])

    def test_scenario_groups_keep_multistep_tasks_on_same_worker(self):
        root = Path("/tmp/dataset/base/l40s")
        scenarios = [
            smoke_runner.NemoClawScenario(
                skill="vss-ask-video",
                spec_name="base_profile_video_understanding",
                spec_path=Path("spec.json"),
                platform="L40S",
                gpu_count=1,
                task_dir=root / "step-1",
                harbor_path=root,
                task_name="step-1",
                deployment_profile="base",
            ),
            smoke_runner.NemoClawScenario(
                skill="vss-ask-video",
                spec_name="base_profile_video_understanding",
                spec_path=Path("spec.json"),
                platform="L40S",
                gpu_count=1,
                task_dir=root / "step-2",
                harbor_path=root,
                task_name="step-2",
                deployment_profile="base",
            ),
            smoke_runner.NemoClawScenario(
                skill="vss-deploy-profile",
                spec_name="base",
                spec_path=Path("base.json"),
                platform="RTXPRO6000BW",
                gpu_count=1,
                task_dir=Path("/tmp/dataset/deploy/base/rtxpro6000bw"),
                harbor_path=Path("/tmp/dataset/deploy/base"),
                task_name="rtxpro6000bw",
                deployment_profile="base",
            ),
        ]

        groups = smoke_runner._scenario_groups(scenarios)

        self.assertEqual([len(group) for group in groups], [2, 1])
        self.assertEqual([scenario.task_name for scenario in groups[0]], ["step-1", "step-2"])

    def test_focused_deploy_profile_matrix_keeps_base_smoke(self):
        rows, blockers = smoke_runner._build_matrix(
            skills_filter="vss-deploy-profile",
            profile_filter="base",
            platform_filter="RTXPRO6000BW",
            spec_filter=None,
            representative_per_skill=False,
        )

        self.assertEqual(blockers, [])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["skill"], "vss-deploy-profile")
        self.assertEqual(rows[0]["spec_stem"], "base")
        self.assertEqual(rows[0]["platform"], "RTXPRO6000BW")
        self.assertEqual(rows[0]["task_limit"], "0")

    def test_worker_selection_skips_locked_candidate(self):
        previous = {
            "_list_instances": smoke_runner._list_instances,
            "_reachable": smoke_runner._reachable,
            "_try_acquire_lock": smoke_runner._try_acquire_lock,
        }
        instances = [
            {"name": "vss-eval-rtx-1g-2", "status": "RUNNING", "gpu": "RTX PRO 6000"},
            {"name": "vss-eval-rtx-1g-3", "status": "RUNNING", "gpu": "RTX PRO 6000"},
        ]

        smoke_runner._list_instances = lambda: instances
        smoke_runner._reachable = lambda instance, exec_target=None: True
        smoke_runner._try_acquire_lock = (
            lambda instance, exec_target=None: None
            if instance == "vss-eval-rtx-1g-2"
            else smoke_runner.WorkerLock(123, object(), None)
        )
        try:
            selected, _lock = smoke_runner._select_and_lock_instance(
                "RTXPRO6000BW",
                1,
                None,
                10,
            )
        finally:
            smoke_runner._list_instances = previous["_list_instances"]
            smoke_runner._reachable = previous["_reachable"]
            smoke_runner._try_acquire_lock = previous["_try_acquire_lock"]

        self.assertEqual(selected, "vss-eval-rtx-1g-3")

    def test_worker_selection_skips_excluded_candidate(self):
        instances = [
            {"name": "vss-eval-rtx-1g-2", "status": "RUNNING", "gpu": "RTX PRO 6000"},
            {"name": "vss-eval-rtx-1g-3", "status": "RUNNING", "gpu": "RTX PRO 6000"},
        ]
        acquire_lock = mock.Mock(
            return_value=smoke_runner.WorkerLock(123, object(), None)
        )

        with (
            mock.patch.object(smoke_runner, "_list_instances", return_value=instances),
            mock.patch.object(smoke_runner, "_reachable", return_value=True),
            mock.patch.object(smoke_runner, "_try_acquire_lock", acquire_lock),
        ):
            selected, _lock = smoke_runner._select_and_lock_instance(
                "RTXPRO6000BW",
                1,
                None,
                10,
                excluded={"vss-eval-rtx-1g-2"},
            )

        self.assertEqual(selected, "vss-eval-rtx-1g-3")
        acquire_lock.assert_called_once_with(
            "vss-eval-rtx-1g-3",
            "vss-eval-rtx-1g-3",
        )

    def test_latest_reward_rejects_non_finite_and_out_of_range_values(self):
        with tempfile.TemporaryDirectory() as td:
            results_root = Path(td)
            reward_path = (
                results_root
                / "30324411561"
                / "2026-07-28__03-14-31"
                / "any__ssr45bt"
                / "verifier"
                / "reward.txt"
            )
            reward_path.parent.mkdir(parents=True)
            result_path = reward_path.parent.parent / "result.json"
            result_path.write_text(
                json.dumps({"exception_info": None}),
                encoding="utf-8",
            )

            reward_path.write_text("0.75\n", encoding="utf-8")
            self.assertEqual(
                smoke_runner._latest_reward(
                    results_root,
                    "30324411561",
                ),
                (0.75, reward_path),
            )

            for malformed in ("nan", "inf", "-inf", "-0.1", "1.1"):
                with self.subTest(malformed=malformed):
                    reward_path.write_text(
                        f"{malformed}\n",
                        encoding="utf-8",
                    )
                    self.assertEqual(
                        smoke_runner._latest_reward(
                            results_root,
                            "30324411561",
                        ),
                        (None, reward_path),
                    )

            reward_path.write_text("1.0\n", encoding="utf-8")
            result_path.write_text(
                json.dumps(
                    {
                        "exception_info": {
                            "exception_type": "NonZeroAgentExitCodeError",
                        }
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                smoke_runner._latest_reward(
                    results_root,
                    "30324411561",
                ),
                (None, reward_path),
            )

    def test_attempt_owner_status_verifies_exact_collected_marker(self):
        token = "a" * 32
        result = {
            "environment_setup": {
                "started_at": "2026-07-28T03:14:31Z",
                "finished_at": "2026-07-28T03:23:27Z",
            }
        }
        with tempfile.TemporaryDirectory() as td:
            trial_dir = Path(td) / "trial"
            artifact_dir = trial_dir / "artifacts" / "logs" / "artifacts"
            artifact_dir.mkdir(parents=True)
            (trial_dir / "artifacts" / "manifest.json").write_text(
                json.dumps(
                    [
                        {
                            "source": "/logs/artifacts",
                            "destination": "artifacts/logs/artifacts",
                            "type": "directory",
                            "status": "ok",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            (artifact_dir / smoke_runner.ATTEMPT_OWNER_FILE).write_text(
                f"{token}\n",
                encoding="utf-8",
            )
            with mock.patch.object(
                smoke_runner,
                "_latest_trial",
                return_value=(trial_dir, result),
            ):
                status = smoke_runner._attempt_owner_status(
                    Path(td),
                    "30324411561",
                    since=1.0,
                    expected_token=token,
                )

        self.assertEqual(
            status,
            smoke_runner.AttemptOwnerStatus(
                "verified",
                "attempt owner marker verified",
            ),
        )

    def test_attempt_owner_status_detects_missing_or_foreign_marker(self):
        token = "a" * 32
        result = {
            "environment_setup": {
                "started_at": "2026-07-28T03:14:31Z",
                "finished_at": "2026-07-28T03:23:27Z",
            }
        }
        for marker_content, reason in (
            (
                None,
                "attempt owner marker disappeared after successful "
                "artifact collection",
            ),
            (
                f"{'b' * 32}\n",
                "attempt owner marker belongs to another worker consumer",
            ),
            (
                "malformed\n",
                "attempt owner marker belongs to another worker consumer",
            ),
        ):
            with self.subTest(marker_content=marker_content):
                with tempfile.TemporaryDirectory() as td:
                    trial_dir = Path(td) / "trial"
                    artifact_dir = (
                        trial_dir / "artifacts" / "logs" / "artifacts"
                    )
                    artifact_dir.mkdir(parents=True)
                    (trial_dir / "artifacts" / "manifest.json").write_text(
                        json.dumps(
                            [
                                {
                                    "source": "/logs/artifacts",
                                    "destination": "artifacts/logs/artifacts",
                                    "type": "directory",
                                    "status": "ok",
                                }
                            ]
                        ),
                        encoding="utf-8",
                    )
                    if marker_content is not None:
                        (
                            artifact_dir / smoke_runner.ATTEMPT_OWNER_FILE
                        ).write_text(marker_content, encoding="utf-8")
                    with mock.patch.object(
                        smoke_runner,
                        "_latest_trial",
                        return_value=(trial_dir, result),
                    ):
                        status = smoke_runner._attempt_owner_status(
                            Path(td),
                            "30324411561",
                            since=1.0,
                            expected_token=token,
                        )

                self.assertEqual(
                    status,
                    smoke_runner.AttemptOwnerStatus("contaminated", reason),
                )

    def test_attempt_owner_status_does_not_guess_from_incomplete_manifest(self):
        token = "a" * 32
        result = {
            "environment_setup": {
                "started_at": "2026-07-28T03:14:31Z",
                "finished_at": "2026-07-28T03:23:27Z",
            }
        }
        manifests = (
            None,
            [],
            [
                {
                    "source": "/logs/artifacts",
                    "destination": "../../foreign",
                    "type": "directory",
                    "status": "ok",
                }
            ],
            [
                {
                    "source": "/logs/artifacts",
                    "destination": "artifacts/logs/artifacts",
                    "type": "directory",
                    "status": "error",
                }
            ],
        )
        for manifest in manifests:
            with self.subTest(manifest=manifest):
                with tempfile.TemporaryDirectory() as td:
                    trial_dir = Path(td) / "trial"
                    (trial_dir / "artifacts").mkdir(parents=True)
                    if manifest is not None:
                        (trial_dir / "artifacts" / "manifest.json").write_text(
                            json.dumps(manifest),
                            encoding="utf-8",
                        )
                    with mock.patch.object(
                        smoke_runner,
                        "_latest_trial",
                        return_value=(trial_dir, result),
                    ):
                        status = smoke_runner._attempt_owner_status(
                            Path(td),
                            "30324411561",
                            since=1.0,
                            expected_token=token,
                        )

                self.assertEqual(status.status, "unavailable")

    def test_live_attempt_owner_status_retries_transport_and_uses_exec_target(self):
        token = "a" * 32
        verified = smoke_runner.CommandResult(
            0,
            "brev spinner\n__NEMOCLAW_ATTEMPT_OWNER_VERIFIED__\n",
            "",
        )
        with (
            mock.patch.object(
                smoke_runner,
                "_run",
                side_effect=[
                    subprocess.TimeoutExpired(["brev", "exec"], 45),
                    verified,
                ],
            ) as run,
            mock.patch.object(smoke_runner.time, "sleep") as sleep,
        ):
            status = smoke_runner._live_attempt_owner_status(
                "instance-123",
                token,
            )

        self.assertEqual(status.status, "verified")
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args.args[0][:3], ["brev", "exec", "instance-123"])
        command = run.call_args.args[0][3]
        self.assertIn(f"expected={token}", command)
        self.assertNotIn('cat "$owner"', command)
        sleep.assert_called_once_with(
            smoke_runner.ATTEMPT_OWNER_PROBE_RETRY_DELAY_S
        )

    def test_live_attempt_owner_status_uses_selected_worker_executor(self):
        calls: list[tuple[str, int]] = []

        def run_remote(command: str, timeout: int):
            calls.append((command, timeout))
            return smoke_runner.CommandResult(
                0,
                "__NEMOCLAW_ATTEMPT_OWNER_VERIFIED__\n",
                "",
            )

        with mock.patch.object(
            smoke_runner,
            "_worker_remote_executor",
        ) as default_executor:
            status = smoke_runner._live_attempt_owner_status(
                "vss-eval-rtx-2g-VM1b",
                "a" * 32,
                run_remote,
            )

        self.assertEqual(status.status, "verified")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], 45)
        default_executor.assert_not_called()

    def test_live_attempt_owner_status_classifies_definite_replacement(self):
        token = "a" * 32
        for returncode, reason in (
            (44, "disappeared or was malformed"),
            (45, "disappeared or was malformed"),
            (46, "belongs to another worker consumer"),
        ):
            with self.subTest(returncode=returncode):
                with (
                    mock.patch.object(
                        smoke_runner,
                        "_run",
                        return_value=smoke_runner.CommandResult(
                            returncode,
                            "",
                            "",
                        ),
                    ) as run,
                    mock.patch.object(smoke_runner.time, "sleep") as sleep,
                ):
                    status = smoke_runner._live_attempt_owner_status(
                        "instance-123",
                        token,
                    )

                self.assertEqual(status.status, "contaminated")
                self.assertIn(reason, status.reason)
                run.assert_called_once()
                sleep.assert_not_called()

    def test_live_attempt_owner_status_fails_closed_after_transient_retries(self):
        with (
            mock.patch.object(
                smoke_runner,
                "_run",
                side_effect=OSError("brev unavailable"),
            ) as run,
            mock.patch.object(smoke_runner.time, "sleep") as sleep,
        ):
            status = smoke_runner._live_attempt_owner_status(
                "instance-123",
                "a" * 32,
            )

        self.assertEqual(status.status, "unavailable")
        self.assertIn("after 3 attempts", status.reason)
        self.assertEqual(run.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_attempt_evidence_owner_status_short_circuits_collected_contamination(
        self,
    ):
        contaminated = smoke_runner.AttemptOwnerStatus(
            "contaminated",
            "collected marker was replaced",
        )
        with (
            mock.patch.object(
                smoke_runner,
                "_attempt_owner_status",
                return_value=contaminated,
            ),
            mock.patch.object(
                smoke_runner,
                "_live_attempt_owner_status",
            ) as live,
        ):
            status = smoke_runner._attempt_evidence_owner_status(
                Path("/tmp/results"),
                "30324411561",
                since=1.0,
                remote_target="instance-123",
                expected_token="a" * 32,
            )

        self.assertEqual(status, contaminated)
        live.assert_not_called()

    def test_attempt_evidence_owner_status_requires_collected_and_live_match(
        self,
    ):
        verified = smoke_runner.AttemptOwnerStatus("verified", "verified")
        unavailable = smoke_runner.AttemptOwnerStatus(
            "unavailable",
            "live probe unavailable",
        )
        with (
            mock.patch.object(
                smoke_runner,
                "_attempt_owner_status",
                return_value=verified,
            ),
            mock.patch.object(
                smoke_runner,
                "_live_attempt_owner_status",
                side_effect=[verified, unavailable],
            ) as live,
        ):
            matched = smoke_runner._attempt_evidence_owner_status(
                Path("/tmp/results"),
                "30324411561",
                since=1.0,
                remote_target="instance-123",
                expected_token="a" * 32,
            )
            missing = smoke_runner._attempt_evidence_owner_status(
                Path("/tmp/results"),
                "30324411561",
                since=2.0,
                remote_target="instance-456",
                expected_token="b" * 32,
            )

        self.assertEqual(matched.status, "verified")
        self.assertEqual(missing.status, "unavailable")
        self.assertEqual(
            [call.args[0] for call in live.call_args_list],
            ["instance-123", "instance-456"],
        )

    def test_discard_contaminated_attempt_removes_timestamp_result_tree(self):
        with tempfile.TemporaryDirectory() as td:
            results_root = Path(td) / "results"
            run_root = results_root / "30324411561"
            timestamp_root = run_root / "2026-07-28__03-14-31"
            trial_dir = timestamp_root / "any__ssr45bt"
            trial_dir.mkdir(parents=True)
            (trial_dir / "result.json").write_text("{}", encoding="utf-8")
            (trial_dir / "trial.log").write_text("foreign", encoding="utf-8")

            discarded, reason = smoke_runner._discard_contaminated_attempt(
                results_root,
                "30324411561",
                since=0.0,
            )

            self.assertTrue(discarded, reason)
            self.assertFalse(timestamp_root.exists())
            self.assertTrue(run_root.is_dir())

    def test_discard_contaminated_attempt_rejects_symlinked_result_tree(self):
        with tempfile.TemporaryDirectory() as td:
            results_root = Path(td) / "results"
            run_root = results_root / "30324411561"
            outside = Path(td) / "outside"
            trial_dir = outside / "any__ssr45bt"
            trial_dir.mkdir(parents=True)
            (trial_dir / "result.json").write_text("{}", encoding="utf-8")
            (trial_dir / "trial.log").write_text("foreign", encoding="utf-8")
            run_root.mkdir(parents=True)
            timestamp_link = run_root / "2026-07-28__03-14-31"
            timestamp_link.symlink_to(outside, target_is_directory=True)
            linked_trial = timestamp_link / trial_dir.name

            with mock.patch.object(
                smoke_runner,
                "_latest_trial",
                return_value=(linked_trial, {}),
            ):
                discarded, reason = (
                    smoke_runner._discard_contaminated_attempt(
                        results_root,
                        "30324411561",
                        since=0.0,
                    )
                )

            self.assertFalse(discarded)
            self.assertIn("symlinked", reason)
            self.assertTrue(outside.is_dir())

    def test_pre_agent_brev_environment_failure_is_retryable(self):
        result = {
            "config": {
                "environment": {
                    "import_path": "envs.brev_env:BrevEnvironment",
                }
            },
            "environment_setup": {
                "started_at": "2026-07-27T12:49:45Z",
                "finished_at": "2026-07-27T12:52:09Z",
            },
            "agent_setup": None,
            "agent_execution": None,
            "verifier": None,
            "agent_result": None,
            "verifier_result": None,
            "exception_info": {
                "exception_type": "RuntimeError",
                "exception_message": (
                    "NemoClaw setup failed on vss-eval-rtx-1g-3: exit 1"
                ),
                "exception_traceback": (
                    'File "/workspace/.github/skill-eval/envs/brev_env.py", '
                    "line 489, in start"
                ),
            },
        }

        with mock.patch.object(
            smoke_runner,
            "_latest_trial",
            return_value=(Path("/tmp/trial"), result),
        ):
            reason = smoke_runner._retryable_worker_setup_failure(
                Path("/tmp/results"),
                "30266918843",
                since=1.0,
                instance="vss-eval-rtx-1g-3",
            )
            mismatched_worker_reason = (
                smoke_runner._retryable_worker_setup_failure(
                    Path("/tmp/results"),
                    "30266918843",
                    since=1.0,
                    instance="vss-eval-rtx-1g-2",
                )
            )

        self.assertEqual(
            reason,
            "NemoClaw setup failed on vss-eval-rtx-1g-3: exit 1",
        )
        self.assertIsNone(mismatched_worker_reason)

    def test_pre_agent_brev_environment_failure_keeps_actionable_detail(self):
        result = {
            "config": {
                "environment": {
                    "import_path": "envs.brev_env:BrevEnvironment",
                }
            },
            "environment_setup": {
                "started_at": "2026-07-29T18:11:36Z",
                "finished_at": "2026-07-29T18:23:01Z",
            },
            "agent_setup": None,
            "agent_execution": None,
            "verifier": None,
            "agent_result": None,
            "verifier_result": None,
            "exception_info": {
                "exception_type": "RuntimeError",
                "exception_message": (
                    "NemoClaw setup failed on vss-eval-rtx-2g-2: "
                    "exit 1; output tail:\n"
                    "\x1b[31mAssertionError\x1b[0m: nemoclaw onboard failed\n"
                    "Sandbox 'demo' was created but did not become ready "
                    "within 180s.\n"
                    "reason=ContainerRestarting Container is restarting "
                    "after a failure\n"
                    "Could not authenticate using sk-secret-value\n"
                    "reason=ContainerRestarting Container is restarting "
                    "after a failure\n"
                ),
                "exception_traceback": (
                    'File "/workspace/.github/skill-eval/envs/brev_env.py", '
                    "line 1280, in start"
                ),
            },
        }

        with mock.patch.object(
            smoke_runner,
            "_latest_trial",
            return_value=(Path("/tmp/trial"), result),
        ):
            reason = smoke_runner._retryable_worker_setup_failure(
                Path("/tmp/results"),
                "30478078792",
                since=1.0,
                instance="vss-eval-rtx-2g-2",
            )

        self.assertIsNotNone(reason)
        self.assertIn("did not become ready", reason)
        self.assertIn("ContainerRestarting", reason)
        self.assertNotIn("sk-secret-value", reason)
        self.assertNotIn("\x1b", reason)
        self.assertEqual(reason.count("ContainerRestarting"), 1)

    def test_worker_bound_transport_and_resource_failures_are_retryable(self):
        messages = (
            "Upload dir failed on vss-eval-rtx-1g-3: No space left on device",
            (
                "Cannot reach Brev instance 'vss-eval-rtx-1g-3': "
                "connection reset"
            ),
            (
                "Brev instance 'vss-eval-rtx-1g-3' root disk is 100 GB; "
                "task requires at least 400 GB"
            ),
            (
                "Brev instance 'vss-eval-rtx-1g-3' root disk could not be "
                "determined: df returned no standalone GB size"
            ),
            (
                "Brev instance 'vss-eval-rtx-1g-3' not found "
                "(is it deleted? wrong org?)"
            ),
            (
                "Brev instance 'vss-eval-rtx-1g-3' does not meet task "
                "requirements:"
            ),
            (
                "Brev instance 'vss-eval-rtx-1g-3' has NVIDIA driver 570; "
                "task requires 580+"
            ),
            (
                "Unexpected response from instance 'vss-eval-rtx-1g-3': "
                "'not-ready'"
            ),
        )

        for message in messages:
            result = {
                "config": {
                    "environment": {
                        "import_path": "envs.brev_env:BrevEnvironment",
                    }
                },
                "environment_setup": {
                    "started_at": "2026-07-27T12:49:45Z",
                    "finished_at": "2026-07-27T12:52:09Z",
                },
                "agent_setup": None,
                "agent_execution": None,
                "verifier": None,
                "agent_result": None,
                "verifier_result": None,
                "exception_info": {
                    "exception_type": "RuntimeError",
                    "exception_message": message,
                    "exception_traceback": (
                        'File "/workspace/.github/skill-eval/envs/brev_env.py", '
                        "line 210, in start"
                    ),
                },
            }
            with (
                self.subTest(message=message),
                mock.patch.object(
                    smoke_runner,
                    "_latest_trial",
                    return_value=(Path("/tmp/trial"), result),
                ),
            ):
                reason = smoke_runner._retryable_worker_setup_failure(
                    Path("/tmp/results"),
                    "30266918843",
                    since=1.0,
                    instance="vss-eval-rtx-1g-3",
                )
                mismatched_worker_reason = (
                    smoke_runner._retryable_worker_setup_failure(
                        Path("/tmp/results"),
                        "30266918843",
                        since=1.0,
                        instance="vss-eval-rtx-1g-2",
                    )
                )

            self.assertEqual(reason, message)
            self.assertIsNone(mismatched_worker_reason)

    def test_unbound_or_post_agent_transport_failures_are_not_retryable(self):
        messages = (
            "Upload dir failed: No space left on device",
            "Upload failed: connection reset",
            "Download dir failed: connection reset",
            (
                "No BREV_INSTANCE set and no `brev_instance` in task.toml "
                "[metadata]"
            ),
        )
        for message in messages:
            result = {
                "config": {
                    "environment": {
                        "import_path": "envs.brev_env:BrevEnvironment",
                    }
                },
                "environment_setup": {
                    "started_at": "2026-07-27T12:49:45Z",
                    "finished_at": "2026-07-27T12:52:09Z",
                },
                "agent_setup": None,
                "agent_execution": None,
                "verifier": None,
                "agent_result": None,
                "verifier_result": None,
                "exception_info": {
                    "exception_type": "RuntimeError",
                    "exception_message": message,
                    "exception_traceback": (
                        'File "/workspace/.github/skill-eval/envs/brev_env.py", '
                        "line 210, in start"
                    ),
                },
            }
            with (
                self.subTest(message=message),
                mock.patch.object(
                    smoke_runner,
                    "_latest_trial",
                    return_value=(Path("/tmp/trial"), result),
                ),
            ):
                reason = smoke_runner._retryable_worker_setup_failure(
                    Path("/tmp/results"),
                    "30266918843",
                    since=1.0,
                    instance="vss-eval-rtx-1g-3",
                )

            self.assertIsNone(reason)

    def test_worker_failure_after_agent_setup_is_not_retryable(self):
        result = {
            "config": {
                "environment": {
                    "import_path": "envs.brev_env:BrevEnvironment",
                }
            },
            "environment_setup": {},
            "agent_setup": {
                "started_at": "2026-07-27T12:52:09Z",
            },
            "agent_execution": None,
            "verifier": None,
            "agent_result": None,
            "verifier_result": None,
            "exception_info": {
                "exception_type": "RuntimeError",
                "exception_message": (
                    "NemoClaw setup failed on vss-eval-rtx-1g-3: exit 1"
                ),
                "exception_traceback": (
                    'File "/workspace/.github/skill-eval/envs/brev_env.py", '
                    "line 489, in start"
                ),
            },
        }

        with mock.patch.object(
            smoke_runner,
            "_latest_trial",
            return_value=(Path("/tmp/trial"), result),
        ):
            reason = smoke_runner._retryable_worker_setup_failure(
                Path("/tmp/results"),
                "30266918843",
                since=1.0,
                instance="vss-eval-rtx-1g-3",
            )

        self.assertIsNone(reason)

    def test_runner_fails_over_once_and_reports_only_final_attempt(self):
        scenario = smoke_runner.NemoClawScenario(
            skill="vss-deploy-profile",
            spec_name="base",
            spec_path=Path("/tmp/base.json"),
            platform="RTXPRO6000BW",
            gpu_count=1,
            task_dir=Path("/tmp/dataset/base/rtxpro6000bw"),
            harbor_path=Path("/tmp/dataset/base"),
            task_name="rtxpro6000bw",
            deployment_profile="base",
        )
        selections: list[set[str]] = []
        selection_timeouts: list[int] = []
        harbor_timeouts: list[int] = []
        events: list[str] = []
        now = [0.0]

        def select_worker(*args, excluded=None, **kwargs):
            selections.append(set(excluded or set()))
            selection_timeouts.append(args[3])
            instance = (
                "vss-eval-rtx-1g-2"
                if len(selections) == 1
                else "vss-eval-rtx-1g-3"
            )
            events.append(f"select:{instance}")
            return instance, smoke_runner.WorkerLock(123, object(), None)

        def release_worker(instance, worker_lock):
            events.append(f"release:{instance}")

        def stream_command(cmd, *, timeout_s, **kwargs):
            harbor_timeouts.append(timeout_s)
            if len(harbor_timeouts) == 1:
                now[0] = 70.0
            return 0

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            report = mock.Mock()
            release = mock.Mock(side_effect=release_worker)
            with (
                mock.patch.dict(
                    os.environ,
                    {
                        "GITHUB_RUN_ID": "30266918843",
                        "NEMOCLAW_MAX_WORKER_FAILOVERS": "2",
                        "NEMOCLAW_RUN_TIMEOUT_SEC": "200",
                    },
                ),
                mock.patch.object(
                    smoke_runner.time,
                    "monotonic",
                    side_effect=lambda: now[0],
                ),
                mock.patch.object(
                    smoke_runner,
                    "_discover_scenarios",
                    return_value=([scenario], []),
                ),
                mock.patch.object(
                    smoke_runner,
                    "_scenario_groups",
                    return_value=[[scenario]],
                ),
                mock.patch.object(
                    smoke_runner,
                    "_select_and_lock_instance",
                    side_effect=select_worker,
                ),
                mock.patch.object(
                    smoke_runner,
                    "_harbor_command",
                    return_value=["harbor", "run"],
                ),
                mock.patch.object(
                    smoke_runner,
                    "_stream_command",
                    side_effect=stream_command,
                ),
                mock.patch.object(
                    smoke_runner,
                    "_latest_reward",
                    side_effect=[(None, None), (1.0, Path("/tmp/reward.txt"))],
                ),
                mock.patch.object(
                    smoke_runner,
                    "_retryable_worker_setup_failure",
                    side_effect=["repo sync failed on worker", None],
                ),
                mock.patch.object(
                    smoke_runner,
                    "_attempt_evidence_owner_status",
                    return_value=smoke_runner.AttemptOwnerStatus(
                        "verified",
                        "attempt owner marker verified",
                    ),
                ),
                mock.patch.object(
                    smoke_runner,
                    "_append_harbor_report",
                    report,
                ),
                mock.patch.object(smoke_runner, "_release_lock", release),
            ):
                rc = smoke_runner.main(
                    [
                        "--skills",
                        "vss-deploy-profile",
                        "--lock-timeout",
                        "80",
                        "--harbor-timeout",
                        "90",
                        "--dataset-root",
                        str(root / "dataset"),
                        "--results-root",
                        str(root / "results"),
                        "--scratch-root",
                        str(root / "scratch"),
                    ]
                )

        self.assertEqual(rc, 0)
        self.assertEqual(
            selections,
            [set(), {"vss-eval-rtx-1g-2"}],
        )
        self.assertEqual(selection_timeouts, [80, 40])
        self.assertEqual(harbor_timeouts, [90, 90])
        self.assertEqual(
            events,
            [
                "select:vss-eval-rtx-1g-2",
                "release:vss-eval-rtx-1g-2",
                "select:vss-eval-rtx-1g-3",
                "release:vss-eval-rtx-1g-3",
            ],
        )
        self.assertEqual(release.call_count, 2)
        report.assert_called_once()
        self.assertEqual(
            report.call_args.kwargs["instance"],
            "vss-eval-rtx-1g-3",
        )
        self.assertIn(
            "vss-eval-rtx-1g-3",
            report.call_args.kwargs["log_path"].name,
        )
        self.assertEqual(
            report.call_args.kwargs["log_path"].parent,
            root / "scratch" / "30266918843" / "harbor",
        )

    def test_runner_retains_third_setup_failure_after_two_failovers(self):
        scenario = smoke_runner.NemoClawScenario(
            skill="vss-deploy-dense-captioning",
            spec_name="alerts_profile_api",
            spec_path=Path("/tmp/alerts_profile_api.json"),
            platform="RTXPRO6000BW",
            gpu_count=1,
            task_dir=Path("/tmp/dataset/alerts/rtxpro6000bw"),
            harbor_path=Path("/tmp/dataset/alerts"),
            task_name="step-1",
            deployment_profile="alerts",
        )
        workers = [
            "vss-eval-rtx-1g-2",
            "vss-eval-rtx-1g-3",
            "vss-eval-rtx-2g-3",
        ]
        selections: list[set[str]] = []

        def select_worker(*args, excluded=None, **kwargs):
            selections.append(set(excluded or set()))
            worker = workers[len(selections) - 1]
            return worker, smoke_runner.WorkerLock(123, object(), None)

        def setup_failure(*args, instance, **kwargs):
            return (
                f"Brev instance '{instance}' root disk is 117 GB; "
                "task requires at least 160 GB"
            )

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            report = mock.Mock()
            blocked = mock.Mock()
            owner_status = mock.Mock()
            collected_owner = mock.Mock(
                return_value=smoke_runner.AttemptOwnerStatus(
                    "verified",
                    "collected attempt owner marker verified",
                )
            )
            discard = mock.Mock()
            release = mock.Mock()
            with (
                mock.patch.dict(
                    os.environ,
                    {
                        "GITHUB_RUN_ID": "30447047501",
                        "NEMOCLAW_MAX_WORKER_FAILOVERS": "2",
                        "NEMOCLAW_RUN_TIMEOUT_SEC": "9000",
                    },
                ),
                mock.patch.object(
                    smoke_runner,
                    "_discover_scenarios",
                    return_value=([scenario], []),
                ),
                mock.patch.object(
                    smoke_runner,
                    "_scenario_groups",
                    return_value=[[scenario]],
                ),
                mock.patch.object(
                    smoke_runner,
                    "_select_and_lock_instance",
                    side_effect=select_worker,
                ),
                mock.patch.object(
                    smoke_runner,
                    "_harbor_command",
                    return_value=["harbor", "run"],
                ),
                mock.patch.object(
                    smoke_runner,
                    "_stream_command",
                    return_value=0,
                ),
                mock.patch.object(
                    smoke_runner,
                    "_latest_reward",
                    return_value=(None, None),
                ),
                mock.patch.object(
                    smoke_runner,
                    "_retryable_worker_setup_failure",
                    side_effect=setup_failure,
                ),
                mock.patch.object(
                    smoke_runner,
                    "_attempt_evidence_owner_status",
                    owner_status,
                ),
                mock.patch.object(
                    smoke_runner,
                    "_attempt_owner_status",
                    collected_owner,
                ),
                mock.patch.object(
                    smoke_runner,
                    "_discard_contaminated_attempt",
                    discard,
                ),
                mock.patch.object(
                    smoke_runner,
                    "_append_harbor_report",
                    report,
                ),
                mock.patch.object(
                    smoke_runner,
                    "_append_blocked_summary",
                    blocked,
                ),
                mock.patch.object(
                    smoke_runner,
                    "_release_lock",
                    release,
                ),
            ):
                rc = smoke_runner.main(
                    [
                        "--skills",
                        "vss-deploy-dense-captioning",
                        "--dataset-root",
                        str(root / "dataset"),
                        "--results-root",
                        str(root / "results"),
                        "--scratch-root",
                        str(root / "scratch"),
                    ]
                )

        self.assertEqual(rc, 1)
        self.assertEqual(
            selections,
            [
                set(),
                {"vss-eval-rtx-1g-2"},
                {"vss-eval-rtx-1g-2", "vss-eval-rtx-1g-3"},
            ],
        )
        self.assertEqual(release.call_count, 3)
        blocked.assert_called_once()
        reason = blocked.call_args.kwargs["reason"]
        self.assertIn("vss-eval-rtx-2g-3", reason)
        self.assertIn("root disk is 117 GB", reason)
        self.assertIn("retry limit reached", reason)
        owner_status.assert_not_called()
        self.assertEqual(collected_owner.call_count, 3)
        discard.assert_not_called()
        report.assert_not_called()

    def test_setup_failure_blocks_when_unverified_evidence_cannot_be_discarded(
        self,
    ):
        scenario = smoke_runner.NemoClawScenario(
            skill="vss-deploy-profile",
            spec_name="base",
            spec_path=Path("/tmp/base.json"),
            platform="RTXPRO6000BW",
            gpu_count=1,
            task_dir=Path("/tmp/dataset/base/rtxpro6000bw"),
            harbor_path=Path("/tmp/dataset/base"),
            task_name="step-1",
            deployment_profile="base",
        )
        selected = mock.Mock(
            return_value=(
                "vss-eval-rtx-1g-2",
                smoke_runner.WorkerLock(123, object(), None),
            )
        )

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            blocked = mock.Mock()
            report = mock.Mock()
            release = mock.Mock()
            with (
                mock.patch.dict(
                    os.environ,
                    {
                        "GITHUB_RUN_ID": "30447047501",
                        "NEMOCLAW_RUN_TIMEOUT_SEC": "9000",
                    },
                ),
                mock.patch.object(
                    smoke_runner,
                    "_discover_scenarios",
                    return_value=([scenario], []),
                ),
                mock.patch.object(
                    smoke_runner,
                    "_scenario_groups",
                    return_value=[[scenario]],
                ),
                mock.patch.object(
                    smoke_runner,
                    "_select_and_lock_instance",
                    selected,
                ),
                mock.patch.object(
                    smoke_runner,
                    "_harbor_command",
                    return_value=["harbor", "run"],
                ),
                mock.patch.object(
                    smoke_runner,
                    "_stream_command",
                    return_value=0,
                ),
                mock.patch.object(
                    smoke_runner,
                    "_latest_reward",
                    return_value=(None, None),
                ),
                mock.patch.object(
                    smoke_runner,
                    "_retryable_worker_setup_failure",
                    return_value="root disk is 117 GB",
                ),
                mock.patch.object(
                    smoke_runner,
                    "_attempt_owner_status",
                    return_value=smoke_runner.AttemptOwnerStatus(
                        "unavailable",
                        "trial environment setup did not finish",
                    ),
                ),
                mock.patch.object(
                    smoke_runner,
                    "_discard_contaminated_attempt",
                    return_value=(
                        False,
                        "contaminated trial path is outside the current run",
                    ),
                ),
                mock.patch.object(
                    smoke_runner,
                    "_append_harbor_report",
                    report,
                ),
                mock.patch.object(
                    smoke_runner,
                    "_append_blocked_summary",
                    blocked,
                ),
                mock.patch.object(
                    smoke_runner,
                    "_release_lock",
                    release,
                ),
            ):
                rc = smoke_runner.main(
                    [
                        "--skills",
                        "vss-deploy-profile",
                        "--dataset-root",
                        str(root / "dataset"),
                        "--results-root",
                        str(root / "results"),
                        "--scratch-root",
                        str(root / "scratch"),
                    ]
                )

        self.assertEqual(rc, 1)
        selected.assert_called_once()
        release.assert_called_once()
        report.assert_not_called()
        blocked.assert_called_once()
        reason = blocked.call_args.kwargs["reason"]
        self.assertIn("could not be made safe", reason)
        self.assertIn("outside the current run", reason)
        self.assertIn("root disk is 117 GB", reason)

    def test_setup_failure_near_deadline_retains_capacity_reason(self):
        scenario = smoke_runner.NemoClawScenario(
            skill="vss-deploy-dense-captioning",
            spec_name="alerts_profile_api",
            spec_path=Path("/tmp/alerts_profile_api.json"),
            platform="RTXPRO6000BW",
            gpu_count=1,
            task_dir=Path("/tmp/dataset/alerts/rtxpro6000bw"),
            harbor_path=Path("/tmp/dataset/alerts"),
            task_name="step-1",
            deployment_profile="alerts",
        )
        now = [0.0]

        def stream_command(*args, **kwargs):
            now[0] = 999.0
            return 0

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            select = mock.Mock(
                return_value=(
                    "vss-eval-rtx-1g-2",
                    smoke_runner.WorkerLock(123, object(), None),
                )
            )
            blocked = mock.Mock()
            owner_status = mock.Mock()
            report = mock.Mock()
            release = mock.Mock()
            with (
                mock.patch.dict(
                    os.environ,
                    {
                        "GITHUB_RUN_ID": "30447047501",
                        "NEMOCLAW_MAX_WORKER_FAILOVERS": "",
                        "NEMOCLAW_RUN_TIMEOUT_SEC": "1000",
                    },
                ),
                mock.patch.object(
                    smoke_runner.time,
                    "monotonic",
                    side_effect=lambda: now[0],
                ),
                mock.patch.object(
                    smoke_runner,
                    "_discover_scenarios",
                    return_value=([scenario], []),
                ),
                mock.patch.object(
                    smoke_runner,
                    "_scenario_groups",
                    return_value=[[scenario]],
                ),
                mock.patch.object(
                    smoke_runner,
                    "_select_and_lock_instance",
                    select,
                ),
                mock.patch.object(
                    smoke_runner,
                    "_harbor_command",
                    return_value=["harbor", "run"],
                ),
                mock.patch.object(
                    smoke_runner,
                    "_stream_command",
                    side_effect=stream_command,
                ),
                mock.patch.object(
                    smoke_runner,
                    "_latest_reward",
                    return_value=(None, None),
                ),
                mock.patch.object(
                    smoke_runner,
                    "_retryable_worker_setup_failure",
                    return_value=(
                        "Brev instance 'vss-eval-rtx-1g-2' root disk is "
                        "117 GB; task requires at least 160 GB"
                    ),
                ),
                mock.patch.object(
                    smoke_runner,
                    "_attempt_evidence_owner_status",
                    owner_status,
                ),
                mock.patch.object(
                    smoke_runner,
                    "_append_harbor_report",
                    report,
                ),
                mock.patch.object(
                    smoke_runner,
                    "_append_blocked_summary",
                    blocked,
                ),
                mock.patch.object(
                    smoke_runner,
                    "_release_lock",
                    release,
                ),
            ):
                rc = smoke_runner.main(
                    [
                        "--skills",
                        "vss-deploy-dense-captioning",
                        "--harbor-timeout",
                        "900",
                        "--dataset-root",
                        str(root / "dataset"),
                        "--results-root",
                        str(root / "results"),
                        "--scratch-root",
                        str(root / "scratch"),
                    ]
                )

        self.assertEqual(rc, 1)
        select.assert_called_once()
        blocked.assert_called_once()
        self.assertIn(
            "root disk is 117 GB",
            blocked.call_args.kwargs["reason"],
        )
        self.assertIn(
            "reserved Harbor budget exhausted",
            blocked.call_args.kwargs["reason"],
        )
        owner_status.assert_not_called()
        report.assert_not_called()
        release.assert_called_once()

    def test_setup_failures_do_not_consume_contamination_retry(self):
        scenario = smoke_runner.NemoClawScenario(
            skill="vss-deploy-dense-captioning",
            spec_name="alerts_profile_api",
            spec_path=Path("/tmp/alerts_profile_api.json"),
            platform="RTXPRO6000BW",
            gpu_count=1,
            task_dir=Path("/tmp/dataset/alerts/rtxpro6000bw"),
            harbor_path=Path("/tmp/dataset/alerts"),
            task_name="step-1",
            deployment_profile="alerts",
        )
        workers = [
            "vss-eval-rtx-1g-2",
            "vss-eval-rtx-1g-3",
            "vss-eval-rtx-2g-3",
            "vss-eval-rtx-2g-VM1b",
            "vss-eval-rtx-2g-VM2b",
        ]
        selections: list[set[str]] = []

        def select_worker(*args, excluded=None, **kwargs):
            selections.append(set(excluded or set()))
            worker = workers[len(selections) - 1]
            return worker, smoke_runner.WorkerLock(123, object(), None)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            report = mock.Mock()
            blocked = mock.Mock()
            owner_status = mock.Mock(
                side_effect=[
                    smoke_runner.AttemptOwnerStatus(
                        "contaminated",
                        "attempt owner marker was replaced",
                    ),
                    smoke_runner.AttemptOwnerStatus(
                        "verified",
                        "attempt owner marker verified",
                    ),
                ]
            )
            collected_owner = mock.Mock(
                return_value=smoke_runner.AttemptOwnerStatus(
                    "unavailable",
                    "trial environment setup did not finish",
                )
            )
            discard = mock.Mock(
                return_value=(True, "removed untrusted trial tree")
            )
            release = mock.Mock()
            with (
                mock.patch.dict(
                    os.environ,
                    {
                        "GITHUB_RUN_ID": "30447047501",
                        "NEMOCLAW_MAX_WORKER_FAILOVERS": "",
                        "NEMOCLAW_MAX_CONTAMINATION_FAILOVERS": "1",
                        "NEMOCLAW_RUN_TIMEOUT_SEC": "9000",
                    },
                ),
                mock.patch.object(
                    smoke_runner,
                    "_discover_scenarios",
                    return_value=([scenario], []),
                ),
                mock.patch.object(
                    smoke_runner,
                    "_scenario_groups",
                    return_value=[[scenario]],
                ),
                mock.patch.object(
                    smoke_runner,
                    "_select_and_lock_instance",
                    side_effect=select_worker,
                ),
                mock.patch.object(
                    smoke_runner,
                    "_harbor_command",
                    return_value=["harbor", "run"],
                ),
                mock.patch.object(
                    smoke_runner,
                    "_stream_command",
                    return_value=0,
                ),
                mock.patch.object(
                    smoke_runner,
                    "_latest_reward",
                    side_effect=[
                        (None, None),
                        (None, None),
                        (None, None),
                        (1.0, Path("/tmp/reward.txt")),
                        (1.0, Path("/tmp/reward.txt")),
                    ],
                ),
                mock.patch.object(
                    smoke_runner,
                    "_retryable_worker_setup_failure",
                    side_effect=[
                        "root disk is 117 GB",
                        "root disk is 117 GB",
                        "root disk is 117 GB",
                        None,
                        None,
                    ],
                ),
                mock.patch.object(
                    smoke_runner,
                    "_attempt_evidence_owner_status",
                    owner_status,
                ),
                mock.patch.object(
                    smoke_runner,
                    "_attempt_owner_status",
                    collected_owner,
                ),
                mock.patch.object(
                    smoke_runner,
                    "_discard_contaminated_attempt",
                    discard,
                ),
                mock.patch.object(
                    smoke_runner,
                    "_append_harbor_report",
                    report,
                ),
                mock.patch.object(
                    smoke_runner,
                    "_append_blocked_summary",
                    blocked,
                ),
                mock.patch.object(
                    smoke_runner,
                    "_release_lock",
                    release,
                ),
            ):
                rc = smoke_runner.main(
                    [
                        "--skills",
                        "vss-deploy-dense-captioning",
                        "--lock-timeout",
                        "1200",
                        "--harbor-timeout",
                        "7800",
                        "--dataset-root",
                        str(root / "dataset"),
                        "--results-root",
                        str(root / "results"),
                        "--scratch-root",
                        str(root / "scratch"),
                    ]
                )

        self.assertEqual(rc, 0)
        self.assertEqual(len(selections), 5)
        self.assertEqual(
            selections[-1],
            set(workers[:4]),
        )
        self.assertEqual(release.call_count, 5)
        self.assertEqual(owner_status.call_count, 2)
        self.assertEqual(collected_owner.call_count, 3)
        self.assertEqual(discard.call_count, 4)
        report.assert_called_once()
        self.assertEqual(
            report.call_args.kwargs["instance"],
            "vss-eval-rtx-2g-VM2b",
        )
        blocked.assert_not_called()

    def test_runner_discards_reward_from_contaminated_worker_and_retries(self):
        scenario = smoke_runner.NemoClawScenario(
            skill="vss-setup-behavior-analytics",
            spec_name="deploy_search_and_alerts",
            spec_path=Path("/tmp/deploy_search_and_alerts.json"),
            platform="ANY",
            gpu_count=0,
            task_dir=Path("/tmp/dataset/any"),
            harbor_path=Path("/tmp/dataset"),
            task_name="any",
            deployment_profile=None,
        )
        selections: list[set[str]] = []
        selection_timeouts: list[int] = []
        harbor_timeouts: list[int] = []
        attempt_tokens: list[str] = []
        now = [0.0]

        def select_worker(*args, excluded=None, **kwargs):
            selections.append(set(excluded or set()))
            selection_timeouts.append(args[3])
            instance = (
                "vss-eval-l40s"
                if len(selections) == 1
                else "vss-eval-l40s-1g"
            )
            return instance, smoke_runner.WorkerLock(
                123,
                object(),
                None,
                f"{instance}-id",
            )

        def stream_command(cmd, *, env, timeout_s, **kwargs):
            attempt_tokens.append(env[smoke_runner.ATTEMPT_OWNER_ENV])
            harbor_timeouts.append(timeout_s)
            if len(attempt_tokens) == 1:
                now[0] = 50.0
            return 0

        owner_calls = [0]

        def owner_status(*args, remote_target, expected_token, **kwargs):
            self.assertEqual(expected_token, attempt_tokens[owner_calls[0]])
            selected_instance = (
                "vss-eval-l40s"
                if owner_calls[0] == 0
                else "vss-eval-l40s-1g"
            )
            self.assertEqual(remote_target, f"{selected_instance}-id")
            owner_calls[0] += 1
            if owner_calls[0] == 1:
                return smoke_runner.AttemptOwnerStatus(
                    "contaminated",
                    "attempt owner marker disappeared after successful "
                    "artifact collection",
                )
            return smoke_runner.AttemptOwnerStatus(
                "verified",
                "attempt owner marker verified",
            )

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            report = mock.Mock()
            blocked = mock.Mock()
            release = mock.Mock()
            with (
                mock.patch.dict(
                    os.environ,
                    {
                        "GITHUB_RUN_ID": "30324411561",
                        "NEMOCLAW_MAX_WORKER_FAILOVERS": "2",
                        "NEMOCLAW_MAX_CONTAMINATION_FAILOVERS": "1",
                        "NEMOCLAW_RUN_TIMEOUT_SEC": "1000",
                    },
                ),
                mock.patch.object(
                    smoke_runner.time,
                    "monotonic",
                    side_effect=lambda: now[0],
                ),
                mock.patch.object(
                    smoke_runner,
                    "_discover_scenarios",
                    return_value=([scenario], []),
                ),
                mock.patch.object(
                    smoke_runner,
                    "_scenario_groups",
                    return_value=[[scenario]],
                ),
                mock.patch.object(
                    smoke_runner,
                    "_select_and_lock_instance",
                    side_effect=select_worker,
                ),
                mock.patch.object(
                    smoke_runner,
                    "_harbor_command",
                    return_value=["harbor", "run"],
                ),
                mock.patch.object(
                    smoke_runner,
                    "_stream_command",
                    side_effect=stream_command,
                ),
                mock.patch.object(
                    smoke_runner,
                    "_latest_reward",
                    side_effect=[
                        (1.0, Path("/tmp/foreign-reward.txt")),
                        (1.0, Path("/tmp/current-reward.txt")),
                    ],
                ),
                mock.patch.object(
                    smoke_runner,
                    "_retryable_worker_setup_failure",
                    return_value=None,
                ),
                mock.patch.object(
                    smoke_runner,
                    "_attempt_evidence_owner_status",
                    side_effect=owner_status,
                ),
                mock.patch.object(
                    smoke_runner,
                    "_append_harbor_report",
                    report,
                ),
                mock.patch.object(
                    smoke_runner,
                    "_append_blocked_summary",
                    blocked,
                ),
                mock.patch.object(
                    smoke_runner,
                    "_release_lock",
                    release,
                ),
            ):
                rc = smoke_runner.main(
                    [
                        "--skills",
                        "vss-setup-behavior-analytics",
                        "--lock-timeout",
                        "80",
                        "--harbor-timeout",
                        "900",
                        "--dataset-root",
                        str(root / "dataset"),
                        "--results-root",
                        str(root / "results"),
                        "--scratch-root",
                        str(root / "scratch"),
                    ]
                )

        self.assertEqual(rc, 0)
        self.assertEqual(
            selections,
            [set(), {"vss-eval-l40s"}],
        )
        self.assertEqual(selection_timeouts, [80, 50])
        self.assertEqual(harbor_timeouts, [900, 900])
        self.assertEqual(len(attempt_tokens), 2)
        self.assertNotEqual(attempt_tokens[0], attempt_tokens[1])
        self.assertTrue(
            all(
                re.fullmatch(r"[0-9a-f]{32}", token)
                for token in attempt_tokens
            )
        )
        self.assertEqual(report.call_count, 1)
        self.assertEqual(
            report.call_args.kwargs["instance"],
            "vss-eval-l40s-1g",
        )
        blocked.assert_not_called()
        self.assertEqual(release.call_count, 2)

    def test_runner_rejects_unowned_success_without_contamination_retry(self):
        scenario = smoke_runner.NemoClawScenario(
            skill="vss-setup-behavior-analytics",
            spec_name="deploy_search_and_alerts",
            spec_path=Path("/tmp/deploy_search_and_alerts.json"),
            platform="ANY",
            gpu_count=0,
            task_dir=Path("/tmp/dataset/any"),
            harbor_path=Path("/tmp/dataset"),
            task_name="any",
            deployment_profile=None,
        )

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            registered_name = "vss-eval-rtx-2g-VM1b"
            remote_executor = mock.Mock()
            select = mock.Mock(
                return_value=(
                    registered_name,
                    smoke_runner.WorkerLock(
                        123,
                        object(),
                        None,
                        registered_name,
                        None,
                        None,
                        remote_executor,
                    ),
                )
            )
            stream = mock.Mock(return_value=0)
            owner_status = mock.Mock(
                return_value=smoke_runner.AttemptOwnerStatus(
                    "unavailable",
                    "artifact manifest is missing",
                )
            )
            report = mock.Mock()
            blocked = mock.Mock()
            release = mock.Mock()
            with (
                mock.patch.dict(
                    os.environ,
                    {
                        "GITHUB_RUN_ID": "30324411561",
                        "NEMOCLAW_RUN_TIMEOUT_SEC": "5000",
                    },
                ),
                mock.patch.object(
                    smoke_runner,
                    "_discover_scenarios",
                    return_value=([scenario], []),
                ),
                mock.patch.object(
                    smoke_runner,
                    "_scenario_groups",
                    return_value=[[scenario]],
                ),
                mock.patch.object(
                    smoke_runner,
                    "_select_and_lock_instance",
                    select,
                ),
                mock.patch.object(
                    smoke_runner,
                    "_harbor_command",
                    return_value=["harbor", "run"],
                ),
                mock.patch.object(
                    smoke_runner,
                    "_stream_command",
                    stream,
                ),
                mock.patch.object(
                    smoke_runner,
                    "_latest_reward",
                    return_value=(1.0, Path("/tmp/reward.txt")),
                ),
                mock.patch.object(
                    smoke_runner,
                    "_retryable_worker_setup_failure",
                    return_value=None,
                ),
                mock.patch.object(
                    smoke_runner,
                    "_attempt_evidence_owner_status",
                    owner_status,
                ),
                mock.patch.object(
                    smoke_runner,
                    "_append_harbor_report",
                    report,
                ),
                mock.patch.object(
                    smoke_runner,
                    "_append_blocked_summary",
                    blocked,
                ),
                mock.patch.object(
                    smoke_runner,
                    "_release_lock",
                    release,
                ),
            ):
                rc = smoke_runner.main(
                    [
                        "--skills",
                        "vss-setup-behavior-analytics",
                        "--dataset-root",
                        str(root / "dataset"),
                        "--results-root",
                        str(root / "results"),
                        "--scratch-root",
                        str(root / "scratch"),
                    ]
                )

        self.assertEqual(rc, 1)
        select.assert_called_once()
        self.assertEqual(
            stream.call_args.kwargs["env"]["BREV_INSTANCE"],
            registered_name,
        )
        self.assertEqual(
            owner_status.call_args.kwargs["remote_target"],
            registered_name,
        )
        self.assertIs(
            owner_status.call_args.kwargs["remote_executor"],
            remote_executor,
        )
        report.assert_not_called()
        blocked.assert_called_once()
        self.assertIn(
            "refusing to accept reward=1.0",
            blocked.call_args.kwargs["reason"],
        )
        release.assert_called_once()

    def test_runner_does_not_fail_over_explicit_worker(self):
        scenario = smoke_runner.NemoClawScenario(
            skill="vss-deploy-profile",
            spec_name="base",
            spec_path=Path("/tmp/base.json"),
            platform="RTXPRO6000BW",
            gpu_count=1,
            task_dir=Path("/tmp/dataset/base/rtxpro6000bw"),
            harbor_path=Path("/tmp/dataset/base"),
            task_name="rtxpro6000bw",
            deployment_profile="base",
        )

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            select = mock.Mock(
                return_value=(
                    "pinned-worker",
                    smoke_runner.WorkerLock(123, object(), None),
                )
            )
            report = mock.Mock()
            blocked = mock.Mock()
            owner_status = mock.Mock(
                return_value=smoke_runner.AttemptOwnerStatus(
                    "verified",
                    "attempt owner marker verified",
                )
            )
            release = mock.Mock()
            with (
                mock.patch.dict(
                    os.environ,
                    {
                        "GITHUB_RUN_ID": "30266918843",
                        "NEMOCLAW_MAX_WORKER_FAILOVERS": "2",
                    },
                ),
                mock.patch.object(
                    smoke_runner,
                    "_discover_scenarios",
                    return_value=([scenario], []),
                ),
                mock.patch.object(
                    smoke_runner,
                    "_scenario_groups",
                    return_value=[[scenario]],
                ),
                mock.patch.object(
                    smoke_runner,
                    "_select_and_lock_instance",
                    select,
                ),
                mock.patch.object(
                    smoke_runner,
                    "_harbor_command",
                    return_value=["harbor", "run"],
                ),
                mock.patch.object(
                    smoke_runner,
                    "_stream_command",
                    return_value=0,
                ),
                mock.patch.object(
                    smoke_runner,
                    "_latest_reward",
                    return_value=(None, None),
                ),
                mock.patch.object(
                    smoke_runner,
                    "_retryable_worker_setup_failure",
                    return_value="repo sync failed on pinned-worker",
                ),
                mock.patch.object(
                    smoke_runner,
                    "_attempt_evidence_owner_status",
                    owner_status,
                ),
                mock.patch.object(
                    smoke_runner,
                    "_append_harbor_report",
                    report,
                ),
                mock.patch.object(
                    smoke_runner,
                    "_append_blocked_summary",
                    blocked,
                ),
                mock.patch.object(smoke_runner, "_release_lock", release),
            ):
                rc = smoke_runner.main(
                    [
                        "--skills",
                        "vss-deploy-profile",
                        "--instance",
                        "pinned-worker",
                        "--dataset-root",
                        str(root / "dataset"),
                        "--results-root",
                        str(root / "results"),
                        "--scratch-root",
                        str(root / "scratch"),
                    ]
                )

        self.assertEqual(rc, 1)
        select.assert_called_once()
        report.assert_not_called()
        blocked.assert_called_once()
        self.assertIn(
            "explicit worker is pinned",
            blocked.call_args.kwargs["reason"],
        )
        owner_status.assert_not_called()
        release.assert_called_once()

    def test_runner_does_not_restart_group_after_second_step_setup_failure(self):
        scenarios = [
            smoke_runner.NemoClawScenario(
                skill="vss-ask-video",
                spec_name="base_profile_video_understanding",
                spec_path=Path("/tmp/base_profile_video_understanding.json"),
                platform="RTXPRO6000BW",
                gpu_count=1,
                task_dir=Path("/tmp/dataset/step-1"),
                harbor_path=Path("/tmp/dataset"),
                task_name="step-1",
                deployment_profile="base",
            ),
            smoke_runner.NemoClawScenario(
                skill="vss-ask-video",
                spec_name="base_profile_video_understanding",
                spec_path=Path("/tmp/base_profile_video_understanding.json"),
                platform="RTXPRO6000BW",
                gpu_count=1,
                task_dir=Path("/tmp/dataset/step-2"),
                harbor_path=Path("/tmp/dataset"),
                task_name="step-2",
                deployment_profile="base",
            ),
        ]

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            select = mock.Mock(
                return_value=(
                    "vss-eval-rtx-1g-2",
                    smoke_runner.WorkerLock(123, object(), None),
                )
            )
            report = mock.Mock()
            blocked = mock.Mock()
            owner_status = mock.Mock(
                return_value=smoke_runner.AttemptOwnerStatus(
                    "verified",
                    "attempt owner marker verified",
                )
            )
            release = mock.Mock()
            with (
                mock.patch.dict(
                    os.environ,
                    {
                        "GITHUB_RUN_ID": "30266918843",
                        "NEMOCLAW_MAX_WORKER_FAILOVERS": "2",
                    },
                ),
                mock.patch.object(
                    smoke_runner,
                    "_discover_scenarios",
                    return_value=(scenarios, []),
                ),
                mock.patch.object(
                    smoke_runner,
                    "_scenario_groups",
                    return_value=[scenarios],
                ),
                mock.patch.object(
                    smoke_runner,
                    "_select_and_lock_instance",
                    select,
                ),
                mock.patch.object(
                    smoke_runner,
                    "_harbor_command",
                    return_value=["harbor", "run"],
                ),
                mock.patch.object(
                    smoke_runner,
                    "_stream_command",
                    side_effect=[0, 0],
                ),
                mock.patch.object(
                    smoke_runner,
                    "_latest_reward",
                    side_effect=[(1.0, Path("/tmp/reward.txt")), (None, None)],
                ),
                mock.patch.object(
                    smoke_runner,
                    "_retryable_worker_setup_failure",
                    return_value="NemoClaw setup failed on vss-eval-rtx-1g-2",
                ),
                mock.patch.object(
                    smoke_runner,
                    "_attempt_evidence_owner_status",
                    owner_status,
                ),
                mock.patch.object(
                    smoke_runner,
                    "_append_harbor_report",
                    report,
                ),
                mock.patch.object(
                    smoke_runner,
                    "_append_blocked_summary",
                    blocked,
                ),
                mock.patch.object(smoke_runner, "_release_lock", release),
            ):
                rc = smoke_runner.main(
                    [
                        "--skills",
                        "vss-ask-video",
                        "--dataset-root",
                        str(root / "dataset"),
                        "--results-root",
                        str(root / "results"),
                        "--scratch-root",
                        str(root / "scratch"),
                    ]
                )

        self.assertEqual(rc, 1)
        select.assert_called_once()
        report.assert_called_once()
        blocked.assert_called_once()
        self.assertIn(
            "cannot restart a multi-step group after step 2",
            blocked.call_args.kwargs["reason"],
        )
        owner_status.assert_called_once()
        release.assert_called_once()

    def test_worker_selection_uses_brev_id_as_exec_target(self):
        previous = {
            "_list_instances": smoke_runner._list_instances,
            "_reachable": smoke_runner._reachable,
            "_try_acquire_lock": smoke_runner._try_acquire_lock,
        }
        calls: dict[str, tuple[str, str | None]] = {}
        instances = [
            {
                "id": "instance-123",
                "name": "vss-eval-rtx-1g-2",
                "status": "RUNNING",
                "gpu": "RTX PRO 6000",
            },
        ]

        def fake_reachable(instance: str, exec_target: str | None = None) -> bool:
            calls["reachable"] = (instance, exec_target)
            return True

        def fake_lock(instance: str, exec_target: str | None = None):
            calls["lock"] = (instance, exec_target)
            return smoke_runner.WorkerLock(123, object(), "owner", exec_target)

        smoke_runner._list_instances = lambda: instances
        smoke_runner._reachable = fake_reachable
        smoke_runner._try_acquire_lock = fake_lock
        try:
            selected, lock = smoke_runner._select_and_lock_instance(
                "RTXPRO6000BW",
                1,
                None,
                10,
            )
        finally:
            smoke_runner._list_instances = previous["_list_instances"]
            smoke_runner._reachable = previous["_reachable"]
            smoke_runner._try_acquire_lock = previous["_try_acquire_lock"]

        self.assertEqual(selected, "vss-eval-rtx-1g-2")
        self.assertEqual(calls["reachable"], ("vss-eval-rtx-1g-2", "instance-123"))
        self.assertEqual(calls["lock"], ("vss-eval-rtx-1g-2", "instance-123"))
        self.assertEqual(lock.remote_target, "instance-123")

    def test_worker_selection_fails_fast_on_brev_login_eof(self):
        instances = [
            {
                "id": "instance-123",
                "name": "vss-eval-rtx-1g-2",
                "status": "RUNNING",
                "gpu": "RTX PRO 6000",
            },
        ]
        auth_result = smoke_runner.CommandResult(
            1,
            "",
            self.BREV_AUTH_TRACE,
        )
        with (
            mock.patch.object(
                smoke_runner,
                "_list_instances",
                return_value=instances,
            ),
            mock.patch.object(
                smoke_runner,
                "_run",
                return_value=auth_result,
            ) as run,
            mock.patch.object(smoke_runner.time, "sleep") as sleep,
            self.assertRaisesRegex(
                smoke_runner.worker_pool.BrevAuthenticationError,
                "PromptForLogin reached EOF",
            ) as ctx,
        ):
            smoke_runner._select_and_lock_instance(
                "RTXPRO6000BW",
                1,
                None,
                1200,
            )

        self.assertNotIn("lock timeout", str(ctx.exception))
        run.assert_called_once()
        sleep.assert_not_called()

    def test_remote_lock_fails_fast_on_brev_login_eof(self):
        handle = mock.Mock()

        def fake_remote_lock(remote_executor, instance):
            self.assertEqual(instance, "vss-eval-rtx-1g-2")
            try:
                remote_executor("remote lock command", 60)
            except smoke_runner.worker_pool.BrevAuthenticationError:
                # Match remote_worker_lock's fail-closed transport contract:
                # the helper logs arbitrary executor failures and returns no
                # lease rather than propagating them.
                pass
            return None

        with (
            mock.patch.object(smoke_runner.os, "open", return_value=123),
            mock.patch.object(smoke_runner.os, "fdopen", return_value=handle),
            mock.patch.object(smoke_runner.fcntl, "flock"),
            mock.patch.object(
                smoke_runner,
                "_run",
                return_value=smoke_runner.CommandResult(
                    1,
                    "",
                    self.BREV_AUTH_TRACE,
                ),
            ),
            mock.patch.object(
                smoke_runner.remote_worker_lock,
                "try_acquire_remote_worker_lock",
                side_effect=fake_remote_lock,
            ) as acquire,
            self.assertRaisesRegex(
                smoke_runner.worker_pool.BrevAuthenticationError,
                "PromptForLogin reached EOF",
            ),
        ):
            smoke_runner._try_acquire_lock(
                "vss-eval-rtx-1g-2",
                "instance-123",
            )

        acquire.assert_called_once()
        handle.close.assert_called_once()

    def test_registered_worker_uses_human_name_instead_of_node_id(self):
        target = smoke_runner._exec_target_for_instance(
            {
                "id": "registered-node-id",
                "name": "vss-eval-rtx-2g-VM1b",
                "_registered": True,
            }
        )

        self.assertEqual(target, "vss-eval-rtx-2g-VM1b")

    def test_explicit_worker_uses_brev_id_as_exec_target_when_visible(self):
        previous = {
            "_list_instances": smoke_runner._list_instances,
            "_reachable": smoke_runner._reachable,
            "_try_acquire_lock": smoke_runner._try_acquire_lock,
        }
        calls: dict[str, tuple[str, str | None]] = {}
        instances = [
            {
                "id": "instance-explicit",
                "name": "vss-eval-rtx-1g-10",
                "status": "RUNNING",
                "gpu": "RTXPro6000",
            },
        ]

        def fake_reachable(instance: str, exec_target: str | None = None) -> bool:
            calls["reachable"] = (instance, exec_target)
            return True

        def fake_lock(instance: str, exec_target: str | None = None):
            calls["lock"] = (instance, exec_target)
            return smoke_runner.WorkerLock(123, object(), "owner", exec_target)

        smoke_runner._list_instances = lambda: instances
        smoke_runner._reachable = fake_reachable
        smoke_runner._try_acquire_lock = fake_lock
        try:
            selected, lock = smoke_runner._select_and_lock_instance(
                "RTXPRO6000BW",
                1,
                "vss-eval-rtx-1g-10",
                10,
            )
        finally:
            smoke_runner._list_instances = previous["_list_instances"]
            smoke_runner._reachable = previous["_reachable"]
            smoke_runner._try_acquire_lock = previous["_try_acquire_lock"]

        self.assertEqual(selected, "vss-eval-rtx-1g-10")
        self.assertEqual(calls["reachable"], ("vss-eval-rtx-1g-10", "instance-explicit"))
        self.assertEqual(calls["lock"], ("vss-eval-rtx-1g-10", "instance-explicit"))
        self.assertEqual(lock.remote_target, "instance-explicit")

    def test_worker_selection_reports_visible_pool_when_platform_missing(self):
        previous = {"_list_instances": smoke_runner._list_instances}
        smoke_runner._list_instances = lambda: [
            {"name": "vss-eval-l40s-1g", "status": "RUNNING", "gpu": "L40S"},
        ]
        try:
            with self.assertRaises(smoke_runner.InfrastructureBlocked) as ctx:
                smoke_runner._select_and_lock_instance(
                    "RTXPRO6000BW",
                    1,
                    None,
                    0,
                )
        finally:
            smoke_runner._list_instances = previous["_list_instances"]

        message = str(ctx.exception)
        self.assertIn("no running vss-eval-* candidate for RTXPRO6000BW", message)
        self.assertIn("vss-eval-l40s-1g", message)

    def test_explicit_worker_timeout_names_worker(self):
        previous = {
            "_list_instances": smoke_runner._list_instances,
            "_reachable": smoke_runner._reachable,
            "sleep": smoke_runner.time.sleep,
        }
        smoke_runner._list_instances = lambda: []
        smoke_runner._reachable = lambda instance, exec_target=None: False
        smoke_runner.time.sleep = lambda seconds: None
        try:
            with self.assertRaises(smoke_runner.InfrastructureBlocked) as ctx:
                smoke_runner._select_and_lock_instance(
                    "RTXPRO6000BW",
                    1,
                    "vss-eval-rtx-2g-5",
                    0,
                )
        finally:
            smoke_runner._list_instances = previous["_list_instances"]
            smoke_runner._reachable = previous["_reachable"]
            smoke_runner.time.sleep = previous["sleep"]

        message = str(ctx.exception)
        self.assertIn("explicit worker vss-eval-rtx-2g-5", message)
        self.assertIn("RTXPRO6000BW", message)

    def test_worker_selection_retries_transient_inventory_timeout(self):
        previous = {
            "_list_instances": smoke_runner._list_instances,
            "_reachable": smoke_runner._reachable,
            "_try_acquire_lock": smoke_runner._try_acquire_lock,
            "sleep": smoke_runner.time.sleep,
        }
        calls = {"list": 0}

        def fake_list_instances():
            calls["list"] += 1
            if calls["list"] == 1:
                raise smoke_runner.InfrastructureBlocked(
                    "brev ls --json timed out after 45s"
                )
            return [
                {"name": "vss-eval-rtx-1g-2", "status": "RUNNING", "gpu": "RTX PRO 6000"},
            ]

        smoke_runner._list_instances = fake_list_instances
        smoke_runner._reachable = lambda instance, exec_target=None: True
        smoke_runner._try_acquire_lock = lambda instance, exec_target=None: smoke_runner.WorkerLock(
            123, object(), None
        )
        smoke_runner.time.sleep = lambda seconds: None
        try:
            selected, _lock = smoke_runner._select_and_lock_instance(
                "RTXPRO6000BW",
                1,
                None,
                10,
            )
        finally:
            smoke_runner._list_instances = previous["_list_instances"]
            smoke_runner._reachable = previous["_reachable"]
            smoke_runner._try_acquire_lock = previous["_try_acquire_lock"]
            smoke_runner.time.sleep = previous["sleep"]

        self.assertEqual(selected, "vss-eval-rtx-1g-2")
        self.assertEqual(calls["list"], 2)

    def test_worker_selection_reports_inventory_timeout_after_deadline(self):
        previous = {
            "_list_instances": smoke_runner._list_instances,
            "sleep": smoke_runner.time.sleep,
            "time": smoke_runner.time.time,
        }
        times = iter([0, 0, 20])

        smoke_runner._list_instances = lambda: (_ for _ in ()).throw(
            smoke_runner.InfrastructureBlocked("brev ls --json timed out after 45s")
        )
        smoke_runner.time.sleep = lambda seconds: None
        smoke_runner.time.time = lambda: next(times)
        try:
            with self.assertRaises(smoke_runner.InfrastructureBlocked) as ctx:
                smoke_runner._select_and_lock_instance(
                    "RTXPRO6000BW",
                    1,
                    None,
                    10,
                )
        finally:
            smoke_runner._list_instances = previous["_list_instances"]
            smoke_runner.time.sleep = previous["sleep"]
            smoke_runner.time.time = previous["time"]

        message = str(ctx.exception)
        self.assertIn("worker inventory unavailable for RTXPRO6000BW after 10s", message)
        self.assertIn("brev ls --json timed out after 45s", message)

    def test_remote_lock_owner_helpers_are_conservative(self):
        self.assertEqual(
            smoke_runner._remote_lock_owner_from_output(
                "NemoClaw worker is locked by 27354810855__nemoclaw-eval__123"
            ),
            "27354810855__nemoclaw-eval__123",
        )
        self.assertEqual(
            smoke_runner._github_run_id_from_lock_owner("27354810855__nemoclaw-eval__123"),
            "27354810855",
        )
        self.assertIsNone(smoke_runner._github_run_id_from_lock_owner("manual__nemoclaw"))

    def test_remote_lock_from_current_run_is_active(self):
        previous = {"GITHUB_RUN_ID": os.environ.get("GITHUB_RUN_ID")}
        os.environ["GITHUB_RUN_ID"] = "27354810855"
        try:
            self.assertFalse(
                smoke_runner._remote_lock_owner_is_inactive(
                    "27354810855__nemoclaw-eval__123"
                )
            )
        finally:
            if previous["GITHUB_RUN_ID"] is None:
                os.environ.pop("GITHUB_RUN_ID", None)
            else:
                os.environ["GITHUB_RUN_ID"] = previous["GITHUB_RUN_ID"]

    def test_remote_lock_reconciles_exact_owner_after_response_loss(self):
        calls: list[list[str]] = []

        def fake_run(cmd, *, timeout=60, env=None):
            calls.append(cmd)
            command_body = cmd[3]
            match = re.search(r"^owner=([^\n]+)$", command_body, re.MULTILINE)
            self.assertIsNotNone(match)
            owner = match.group(1)
            return smoke_runner.CommandResult(
                1,
                f"NemoClaw worker is locked by {owner} age=0s",
                "",
            )

        nonce = mock.Mock(hex="a" * 32)
        with (
            mock.patch.object(smoke_runner, "_run", side_effect=fake_run),
            mock.patch.object(smoke_runner.os, "getpid", return_value=1234),
            mock.patch.object(smoke_runner.time, "time", return_value=1730000000),
            mock.patch.object(smoke_runner.uuid, "uuid4", return_value=nonce),
            mock.patch.dict(
                os.environ,
                {
                    "GITHUB_RUN_ID": "30275546898",
                    "GITHUB_RUN_ATTEMPT": "2",
                    "NEMOCLAW_LOCK_OWNER_CONTEXT": (
                        "vss-ask-video/base_profile_video_understanding/"
                        "RTXPRO6000BW"
                    ),
                },
            ),
        ):
            owner = smoke_runner._try_acquire_remote_worker_lock(
                "vss-eval-rtx-2g-2"
            )

        self.assertEqual(
            owner,
            "v2__30275546898__2__"
            "vss-ask-video-base-profile-video-understanding-rtxpro6000bw__"
            "1234__1730000000__"
            f"{'a' * 32}",
        )
        self.assertEqual(len(calls), 1)
        self.assertIn("cleanup_incomplete_lock", calls[0][3])

    def test_remote_lock_refresh_is_atomic_and_exact_owner_only(self):
        with mock.patch.object(
            smoke_runner,
            "_run",
            return_value=smoke_runner.CommandResult(
                0,
                "refreshed NemoClaw worker lock owned by expected-owner",
                "",
            ),
        ) as run:
            status = smoke_runner._refresh_remote_worker_lock(
                "worker-id",
                "expected-owner",
            )

        self.assertEqual(status, "refreshed")
        command = run.call_args.args[0][3]
        self.assertIn("expected=expected-owner", command)
        self.assertIn("stat -Lc '%d:%i'", command)
        self.assertIn('mktemp "$lock_dir/.created.', command)
        self.assertIn('mv -f "$tmp" "$lock_dir/created"', command)
        self.assertNotIn('mkdir "$lock_dir"', command)
        self.assertNotIn('rm -rf "$lock_dir"', command)

    def test_remote_lock_refresh_reports_owner_loss_without_mutation(self):
        with mock.patch.object(
            smoke_runner,
            "_run",
            return_value=smoke_runner.CommandResult(
                3,
                "NemoClaw worker lock is not owned by expected-owner",
                "",
            ),
        ):
            status = smoke_runner._refresh_remote_worker_lock(
                "worker-id",
                "expected-owner",
            )

        self.assertEqual(status, "not_owner")

    def test_remote_lock_refresh_timeout_is_unknown(self):
        with mock.patch.object(
            smoke_runner,
            "_run",
            side_effect=subprocess.TimeoutExpired(["brev", "exec"], 30),
        ):
            status = smoke_runner._refresh_remote_worker_lock(
                "worker-id",
                "expected-owner",
            )

        self.assertEqual(status, "unknown")

    def test_remote_lock_refresh_os_error_is_unknown(self):
        with mock.patch.object(
            smoke_runner,
            "_run",
            side_effect=OSError("brev executable unavailable"),
        ):
            status = smoke_runner._refresh_remote_worker_lock(
                "worker-id",
                "expected-owner",
            )

        self.assertEqual(status, "unknown")

    def test_shared_remote_lease_failure_releases_local_lock(self):
        handle = mock.Mock()
        with (
            mock.patch.object(smoke_runner.os, "open", return_value=123),
            mock.patch.object(smoke_runner.os, "fdopen", return_value=handle),
            mock.patch.object(smoke_runner.fcntl, "flock"),
            mock.patch.object(
                smoke_runner.remote_worker_lock,
                "try_acquire_remote_worker_lock",
                side_effect=RuntimeError("thread start failed"),
            ),
            self.assertRaisesRegex(RuntimeError, "thread start failed"),
        ):
            smoke_runner._try_acquire_lock(
                "worker-name",
                "worker-id",
            )

        handle.close.assert_called_once()

    def test_smoke_lock_rejects_unsafe_worker_names(self):
        with mock.patch.object(smoke_runner.os, "open") as open_lock:
            for worker in ("", ".", "..", "vss-eval/../../unsafe"):
                with (
                    self.subTest(worker=worker),
                    self.assertRaisesRegex(ValueError, "invalid worker name"),
                ):
                    smoke_runner._try_acquire_lock(worker)

        open_lock.assert_not_called()

    def test_smoke_lock_uses_and_releases_shared_remote_lease(self):
        handle = mock.Mock()
        heartbeat = smoke_runner.remote_worker_lock.RemoteLockHeartbeat(
            threading.Event(),
            threading.Event(),
            mock.Mock(),
        )
        remote_lease = mock.Mock(
            owner="expected-owner",
            heartbeat=heartbeat,
        )
        with (
            mock.patch.object(smoke_runner.os, "open", return_value=123),
            mock.patch.object(smoke_runner.os, "fdopen", return_value=handle),
            mock.patch.object(smoke_runner.fcntl, "flock"),
            mock.patch.object(
                smoke_runner.remote_worker_lock,
                "try_acquire_remote_worker_lock",
                return_value=remote_lease,
            ) as acquire,
        ):
            lock = smoke_runner._try_acquire_lock(
                "worker-name",
                "worker-id",
            )
            assert lock is not None
            smoke_runner._release_lock("worker-name", lock)

        self.assertIs(lock.remote_lease, remote_lease)
        self.assertIs(lock.heartbeat, heartbeat)
        acquire.assert_called_once()
        self.assertEqual(acquire.call_args.args[1], "worker-name")
        remote_lease.release.assert_called_once()
        handle.close.assert_called_once()

    def test_smoke_lock_retains_exact_executor_used_by_remote_lease(self):
        handle = mock.Mock()
        remote_executor = mock.Mock()
        heartbeat = smoke_runner.remote_worker_lock.RemoteLockHeartbeat(
            threading.Event(),
            threading.Event(),
            mock.Mock(),
        )
        remote_lease = mock.Mock(
            owner="expected-owner",
            heartbeat=heartbeat,
        )
        with (
            mock.patch.object(smoke_runner.os, "open", return_value=123),
            mock.patch.object(smoke_runner.os, "fdopen", return_value=handle),
            mock.patch.object(smoke_runner.fcntl, "flock"),
            mock.patch.object(
                smoke_runner,
                "_worker_remote_executor",
                return_value=remote_executor,
            ),
            mock.patch.object(
                smoke_runner.remote_worker_lock,
                "try_acquire_remote_worker_lock",
                return_value=remote_lease,
            ) as acquire,
        ):
            lock = smoke_runner._try_acquire_lock(
                "vss-eval-rtx-2g-VM1b"
            )
            assert lock is not None
            smoke_runner._release_lock(
                "vss-eval-rtx-2g-VM1b",
                lock,
            )

        self.assertIs(acquire.call_args.args[0], remote_executor)
        self.assertIs(lock.remote_executor, remote_executor)
        remote_lease.release.assert_called_once()
        handle.close.assert_called_once()

    def test_release_stops_heartbeat_before_exact_owner_delete(self):
        events: list[str] = []
        heartbeat = smoke_runner.RemoteLockHeartbeat(
            threading.Event(),
            threading.Event(),
            mock.Mock(),
        )
        handle = mock.Mock()

        def stop_heartbeat(_heartbeat):
            events.append("stop")

        def run(cmd, *, timeout=60, env=None):
            events.append("delete")
            return smoke_runner.CommandResult(0, "", "")

        with (
            mock.patch.object(
                smoke_runner,
                "_stop_remote_worker_lock_heartbeat",
                side_effect=stop_heartbeat,
            ),
            mock.patch.object(smoke_runner, "_run", side_effect=run),
            mock.patch.object(smoke_runner.fcntl, "flock"),
        ):
            smoke_runner._release_lock(
                "worker-name",
                smoke_runner.WorkerLock(
                    123,
                    handle,
                    "expected-owner",
                    "worker-id",
                    heartbeat,
                ),
            )

        self.assertEqual(events, ["stop", "delete"])
        handle.close.assert_called_once()

    def test_stream_command_aborts_after_confirmed_lock_loss(self):
        abort_event = threading.Event()
        abort_event.set()
        with tempfile.TemporaryDirectory() as td:
            log_path = Path(td) / "harbor.log"
            with mock.patch.object(
                smoke_runner.worker_pool,
                "_cancel_process_tree",
                wraps=smoke_runner.worker_pool._cancel_process_tree,
            ) as cancel_tree:
                rc = smoke_runner._stream_command(
                    [
                        sys.executable,
                        "-c",
                        "pass",
                    ],
                    timeout_s=10,
                    env=os.environ.copy(),
                    log_path=log_path,
                    abort_event=abort_event,
                )

            log = log_path.read_text(encoding="utf-8")
            registry_path = cancel_tree.call_args.args[2]

        self.assertEqual(rc, 125)
        self.assertIn("aborting Harbor after remote worker lock loss", log)
        cancel_tree.assert_called_once()
        self.assertFalse(registry_path.exists())

    def test_stream_command_records_timeout_before_terminating(self):
        with tempfile.TemporaryDirectory() as td:
            log_path = Path(td) / "harbor.log"
            with mock.patch.object(
                smoke_runner.worker_pool,
                "_cancel_process_tree",
                wraps=smoke_runner.worker_pool._cancel_process_tree,
            ) as cancel_tree:
                rc = smoke_runner._stream_command(
                    [
                        sys.executable,
                        "-c",
                        "import time; time.sleep(60)",
                    ],
                    timeout_s=0,
                    env=os.environ.copy(),
                    log_path=log_path,
                )

            log = log_path.read_text(encoding="utf-8")
            registry_path = cancel_tree.call_args.args[2]

        self.assertEqual(rc, 124)
        self.assertIn(
            "Harbor exceeded the 0s timeout; terminating process group",
            log,
        )
        cancel_tree.assert_called_once()
        self.assertFalse(registry_path.exists())

    def test_stream_command_propagates_and_reaps_transport_registry(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            captured_registry = root / "registry.txt"
            log_path = root / "harbor.log"
            rc = smoke_runner._stream_command(
                [
                    sys.executable,
                    "-c",
                    (
                        "import os, pathlib, sys; "
                        "pathlib.Path(sys.argv[1]).write_text("
                        "os.environ['BREV_TRANSPORT_PGID_FILE'])"
                    ),
                    str(captured_registry),
                ],
                timeout_s=10,
                env=os.environ.copy(),
                log_path=log_path,
            )

            registry_path = Path(captured_registry.read_text(encoding="utf-8"))

        self.assertEqual(rc, 0)
        self.assertTrue(registry_path.name.startswith("skill-eval-transport-pgids-"))
        self.assertFalse(registry_path.exists())

    def test_completed_matrix_job_lock_is_inactive_within_current_run(self):
        owner = (
            "v2__30275546898__1__"
            "vss-ask-video-base-profile-video-understanding-rtxpro6000bw__"
            "1234__1730000000__nonce"
        )
        with (
            mock.patch.dict(
                os.environ,
                {"GITHUB_RUN_ID": "30275546898"},
            ),
            mock.patch.object(
                smoke_runner,
                "_github_job_status",
                return_value="completed",
            ) as job_status,
            mock.patch.object(smoke_runner, "_github_run_status") as run_status,
        ):
            inactive = smoke_runner._remote_lock_owner_is_inactive(owner)

        self.assertTrue(inactive)
        job_status.assert_called_once_with(
            "30275546898",
            "1",
            "vss-ask-video-base-profile-video-understanding-rtxpro6000bw",
        )
        run_status.assert_not_called()

    def test_github_job_status_matches_matrix_context(self):
        payload = {
            "jobs": [
                {
                    "name": (
                        "NemoClaw / vss-ask-video/"
                        "base_profile_video_understanding/RTXPRO6000BW"
                    ),
                    "status": "completed",
                },
                {
                    "name": "NemoClaw / vss-deploy-profile/base/RTXPRO6000BW",
                    "status": "in_progress",
                },
            ]
        }
        with (
            mock.patch.dict(
                os.environ,
                {
                    "GITHUB_REPOSITORY": (
                        "NVIDIA-AI-Blueprints/video-search-and-summarization"
                    ),
                    "GH_TOKEN": "test-token",
                },
            ),
            mock.patch.object(
                smoke_runner,
                "_run",
                return_value=smoke_runner.CommandResult(
                    0,
                    json.dumps(payload),
                    "",
                ),
            ) as run,
        ):
            status = smoke_runner._github_job_status(
                "30275546898",
                "1",
                "vss-ask-video-base-profile-video-understanding-rtxpro6000bw",
            )

        self.assertEqual(status, "completed")
        self.assertIn(
            "repos/NVIDIA-AI-Blueprints/video-search-and-summarization/"
            "actions/runs/30275546898/attempts/1/jobs?per_page=100",
            run.call_args.args[0],
        )

    def test_remote_lock_from_completed_run_is_cleared_and_retried(self):
        previous = {
            "_run": smoke_runner._run,
            "_github_run_status": smoke_runner._github_run_status,
            "GITHUB_RUN_ID": os.environ.get("GITHUB_RUN_ID"),
            "GITHUB_JOB": os.environ.get("GITHUB_JOB"),
        }
        calls: list[list[str]] = []

        def fake_run(cmd, *, timeout=60, env=None):
            calls.append(cmd)
            command_body = cmd[3] if cmd[:2] == ["brev", "exec"] and len(cmd) > 3 else ""
            if command_body and "expected=" in command_body and "rm -rf" in command_body:
                return smoke_runner.CommandResult(
                    0,
                    "removed NemoClaw worker lock owned by 27354810855__nemoclaw-eval__old",
                    "",
                )
            if cmd[:2] == ["brev", "exec"] and len(calls) == 1:
                return smoke_runner.CommandResult(
                    1,
                    "NemoClaw worker is locked by 27354810855__nemoclaw-eval__old",
                    "",
                )
            return smoke_runner.CommandResult(0, "", "")

        smoke_runner._run = fake_run
        smoke_runner._github_run_status = lambda run_id: "completed"
        os.environ["GITHUB_RUN_ID"] = "27358558981"
        os.environ["GITHUB_JOB"] = "nemoclaw-eval"
        try:
            owner = smoke_runner._try_acquire_remote_worker_lock("vss-eval-rtx-2g-2")
        finally:
            smoke_runner._run = previous["_run"]
            smoke_runner._github_run_status = previous["_github_run_status"]
            for key in ("GITHUB_RUN_ID", "GITHUB_JOB"):
                if previous[key] is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = previous[key]

        self.assertIsNotNone(owner)
        self.assertEqual(len(calls), 3)
        self.assertIn("rm -rf", calls[1][3])

    def test_aged_remote_lock_from_active_run_is_never_evicted(self):
        calls: list[list[str]] = []

        def fake_run(cmd, *, timeout=60, env=None):
            calls.append(cmd)
            return smoke_runner.CommandResult(
                1,
                (
                    "NemoClaw worker is locked by "
                    "30266918843__nemoclaw-eval__old age=1800s"
                ),
                "",
            )

        with (
            mock.patch.object(smoke_runner, "_run", side_effect=fake_run),
            mock.patch.object(
                smoke_runner,
                "_github_run_status",
                return_value="in_progress",
            ),
            mock.patch.dict(
                os.environ,
                {
                    "GITHUB_RUN_ID": "30272325661",
                    "GITHUB_JOB": "nemoclaw-eval",
                },
            ),
        ):
            owner = smoke_runner._try_acquire_remote_worker_lock(
                "vss-eval-rtx-2g-2"
            )

        self.assertIsNone(owner)
        self.assertEqual(len(calls), 1)
        self.assertIn("age=$((now - created))", calls[0][3])
        self.assertNotIn("expected=", calls[0][3])

    def test_brev_inventory_timeout_is_infrastructure_blocked(self):
        with (
            mock.patch.object(
                smoke_runner.worker_pool,
                "_list_pool_instances",
                return_value=[],
            ),
            self.assertRaises(smoke_runner.InfrastructureBlocked) as ctx,
        ):
            smoke_runner._list_instances()

        self.assertIn(
            "managed and registered worker inventories returned no",
            str(ctx.exception),
        )

    def test_registered_inventory_survives_managed_brev_timeout(self):
        previous_registered = set(smoke_runner._REGISTERED_WORKERS)
        try:
            with (
                mock.patch.object(
                    smoke_runner.worker_pool,
                    "_list_brev_instances",
                    return_value=[],
                ),
                mock.patch.object(
                    smoke_runner.worker_pool,
                    "_list_registered_nodes",
                    return_value=[
                        {
                            "name": "vss-eval-rtx-2g-VM1b",
                            "status": "Connected",
                        }
                    ],
                ),
                mock.patch.dict(
                    os.environ,
                    {"BREV_REGISTERED_POOL": "vss-eval-rtx-2g-VM1b"},
                ),
            ):
                instances = smoke_runner._list_instances()
        finally:
            smoke_runner._REGISTERED_WORKERS.clear()
            smoke_runner._REGISTERED_WORKERS.update(previous_registered)

        self.assertEqual(
            [instance["name"] for instance in instances],
            ["vss-eval-rtx-2g-VM1b"],
        )

    def test_registered_worker_commands_use_direct_ssh_transport(self):
        calls: list[tuple[list[str], dict]] = []

        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            return subprocess.CompletedProcess(
                cmd,
                0,
                "harbor-ready\n",
                "",
            )

        previous_registered = set(smoke_runner._REGISTERED_WORKERS)
        transport_key = "vss-eval-rtx-2g-vm1b"
        previous_transport = smoke_runner.worker_pool._WORKER_TRANSPORTS.get(
            transport_key
        )
        smoke_runner._REGISTERED_WORKERS.add("vss-eval-rtx-2g-vm1b")
        try:
            with mock.patch.object(
                smoke_runner.worker_pool.subprocess,
                "run",
                side_effect=fake_run,
            ):
                executor = smoke_runner._worker_remote_executor(
                    "vss-eval-rtx-2g-VM1b"
                )
                result = executor("echo harbor-ready", 45)
        finally:
            smoke_runner._REGISTERED_WORKERS.clear()
            smoke_runner._REGISTERED_WORKERS.update(previous_registered)
            if previous_transport is None:
                smoke_runner.worker_pool._WORKER_TRANSPORTS.pop(
                    transport_key,
                    None,
                )
            else:
                smoke_runner.worker_pool._WORKER_TRANSPORTS[
                    transport_key
                ] = previous_transport

        self.assertEqual(result.returncode, 0)
        command, kwargs = calls[0]
        self.assertEqual(command[0], "ssh")
        self.assertIn("vss-eval-rtx-2g-vm1b", command)
        self.assertEqual(command[-1], "echo harbor-ready")
        self.assertNotIn("brev", command)
        self.assertEqual(kwargs["input"], "")

    def test_registered_reachability_failure_never_runs_brev_refresh(self):
        transport_key = "vss-eval-rtx-2g-vm1b"
        previous_registered = set(smoke_runner._REGISTERED_WORKERS)
        previous_transport = smoke_runner.worker_pool._WORKER_TRANSPORTS.get(
            transport_key
        )
        smoke_runner._REGISTERED_WORKERS.add(transport_key)
        refresh = mock.Mock()
        try:
            with (
                mock.patch.object(
                    smoke_runner.worker_pool.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess(
                        [],
                        255,
                        "",
                        "ssh: Could not resolve hostname registered-worker",
                    ),
                ),
                mock.patch.object(smoke_runner, "_run", refresh),
            ):
                reachable = smoke_runner._reachable(
                    "vss-eval-rtx-2g-VM1b"
                )
        finally:
            smoke_runner._REGISTERED_WORKERS.clear()
            smoke_runner._REGISTERED_WORKERS.update(previous_registered)
            if previous_transport is None:
                smoke_runner.worker_pool._WORKER_TRANSPORTS.pop(
                    transport_key,
                    None,
                )
            else:
                smoke_runner.worker_pool._WORKER_TRANSPORTS[
                    transport_key
                ] = previous_transport

        self.assertFalse(reachable)
        refresh.assert_not_called()

    def test_reachability_timeout_skips_candidate(self):
        previous = {"_run": smoke_runner._run}
        smoke_runner._run = lambda *args, **kwargs: (_ for _ in ()).throw(
            subprocess.TimeoutExpired(["brev", "exec", "vss-eval-rtx-2g-4"], 45)
        )
        try:
            reachable = smoke_runner._reachable("vss-eval-rtx-2g-4")
        finally:
            smoke_runner._run = previous["_run"]

        self.assertFalse(reachable)

    def test_reachability_failure_logs_brev_output(self):
        previous = {"_run": smoke_runner._run}
        smoke_runner._run = lambda *args, **kwargs: smoke_runner.CommandResult(
            255,
            "",
            "ssh: Could not resolve hostname vss-eval-rtx-2g-4",
        )
        output = io.StringIO()
        try:
            with contextlib.redirect_stdout(output):
                reachable = smoke_runner._reachable("vss-eval-rtx-2g-4")
        finally:
            smoke_runner._run = previous["_run"]

        self.assertFalse(reachable)
        self.assertIn("reachability failed rc=255", output.getvalue())
        self.assertIn("Could not resolve hostname", output.getvalue())

    def test_reachability_rejects_rc_zero_echoed_command_and_dns_failure(self):
        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[:2] == ["brev", "refresh"]:
                return smoke_runner.CommandResult(0, "refreshed\n", "")
            return smoke_runner.CommandResult(
                0,
                "echo harbor-ready\n",
                "ssh: Could not resolve hostname vss-eval-rtx-2g-4\n",
            )

        with mock.patch.object(smoke_runner, "_run", side_effect=fake_run):
            reachable = smoke_runner._reachable("vss-eval-rtx-2g-4")

        self.assertFalse(reachable)
        self.assertEqual(
            calls,
            [
                ["brev", "exec", "vss-eval-rtx-2g-4", "echo harbor-ready"],
                ["brev", "refresh"],
                ["brev", "exec", "vss-eval-rtx-2g-4", "echo harbor-ready"],
            ],
        )

    def test_reachability_requires_harbor_ready_as_a_standalone_line(self):
        with mock.patch.object(
            smoke_runner,
            "_run",
            return_value=smoke_runner.CommandResult(
                0,
                "remote output contains harbor-ready but not alone\n",
                "",
            ),
        ):
            reachable = smoke_runner._reachable("vss-eval-rtx-2g-4")

        self.assertFalse(reachable)

    def test_reachability_refreshes_ssh_config_after_hostname_failure(self):
        previous = {"_run": smoke_runner._run}
        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[:2] == ["brev", "refresh"]:
                return smoke_runner.CommandResult(0, "refreshed\n", "")
            if len(calls) == 1:
                return smoke_runner.CommandResult(
                    1,
                    "",
                    "ssh: Could not resolve hostname vss-eval-rtx-2g-4",
                )
            return smoke_runner.CommandResult(0, "harbor-ready\n", "")

        smoke_runner._run = fake_run
        try:
            reachable = smoke_runner._reachable("vss-eval-rtx-2g-4")
        finally:
            smoke_runner._run = previous["_run"]

        self.assertTrue(reachable)
        self.assertEqual(
            calls,
            [
                ["brev", "exec", "vss-eval-rtx-2g-4", "echo harbor-ready"],
                ["brev", "refresh"],
                ["brev", "exec", "vss-eval-rtx-2g-4", "echo harbor-ready"],
            ],
        )

    def test_vss_ask_video_adapter_renders_platform_placeholders(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            skill_dir = root / "skill"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text("# Ask video\n", encoding="utf-8")
            spec_path = skill_dir / "evals" / "render.json"
            spec_path.parent.mkdir()
            spec = {
                "_source_path": str(spec_path),
                "profile": "base",
                "expects": [
                    {
                        "query": (
                            "Deploy on `{{ platform }}` from `{{repo_root}}`."
                        ),
                        "checks": [
                            "The selected host is `{{platform}}` and the "
                            "repository is `{{ repo_root }}`."
                        ],
                    }
                ],
            }

            output_root = root / "datasets"
            ask_adapter.generate_task(
                "RTXPRO6000BW",
                "base",
                spec,
                output_root,
                skill_dir,
                None,
                None,
            )

            task_dir = output_root / "base" / "rtxpro6000bw"
            instruction = (task_dir / "instruction.md").read_text(
                encoding="utf-8"
            )
            staged_spec = json.loads(
                (task_dir / "tests" / "render.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertIn("RTXPRO6000BW", instruction)
        self.assertIn("$HOME/video-search-and-summarization", instruction)
        self.assertNotIn("{{", instruction)
        staged_text = json.dumps(staged_spec)
        self.assertIn("RTXPRO6000BW", staged_text)
        self.assertIn("$HOME/video-search-and-summarization", staged_text)
        self.assertNotIn("{{", staged_text)

    def test_generic_task_wrapper_creates_nemoclaw_launcher(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            task_dir = root / "base" / "l40s" / "step-1"
            task_dir.mkdir(parents=True)
            (task_dir / "instruction.md").write_text(
                "Use the /vss-ask-video skill against the already running base profile.",
                encoding="utf-8",
            )
            (task_dir / "task.toml").write_text(
                textwrap.dedent(
                    """
                    [task]
                    name = "nvidia-vss/vss-ask-video-base-l40s-step-1"

                    [metadata]
                    skill = "vss-ask-video"
                    profile = "base"
                    platform = "L40S"
                    gpu_count = 1
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            scenario = smoke_runner._wrap_task_for_nemoclaw(
                task_dir=task_dir,
                skill="vss-ask-video",
                spec_path=REPO_ROOT / "skills" / "vss-ask-video" / "evals" / "base_profile_video_understanding.json",
                platform="L40S",
            )

            prompt = (task_dir / "tests" / "nemoclaw_prompt.md").read_text(encoding="utf-8")
            instruction = (task_dir / "instruction.md").read_text(encoding="utf-8")
            task_toml = (task_dir / "task.toml").read_text(encoding="utf-8")

        self.assertEqual(scenario.skill, "vss-ask-video")
        self.assertEqual(scenario.task_name, "step-1")
        self.assertEqual(scenario.deployment_profile, "base")
        self.assertIn("Use the `/vss-ask-video` skill as the primary workflow", prompt)
        self.assertIn("requires the `base` VSS profile", prompt)
        self.assertIn("`pull_always=true` and `force_recreate=true`", prompt)
        self.assertIn("terminal `success` with `running=false`", prompt)
        self.assertIn("`running` operation is never deployment success", prompt)
        self.assertIn("Never infer readiness from container-name presence", prompt)
        self.assertIn("structured `docker_list` states and combined `docker_logs`", prompt)
        self.assertIn("Agent API on port 8000 and UI on port 3000", prompt)
        self.assertIn("Use the /vss-ask-video skill against", prompt)
        self.assertIn("## GPU resource boundary", prompt)
        self.assertIn("only valid device ID is 0", prompt)
        self.assertIn("Never request GPU 1", prompt)
        self.assertIn("headless_runner.py", instruction)
        self.assertIn("--wait-profile base", instruction)
        self.assertIn("--expected-skill vss-ask-video", instruction)
        self.assertNotIn("--runtime-env", instruction)
        self.assertIn('runner = "nemoclaw"', task_toml)
        self.assertIn('expected_skill = "vss-ask-video"', task_toml)
        self.assertIn("vss_orchestrator__docker_status", task_toml)

    def test_dense_captioning_launcher_has_no_runtime_value_argument(self):
        instruction = smoke_runner._headless_launcher_instruction(
            "vss-deploy-dense-captioning",
            "alerts",
        )

        self.assertIn(
            "--expected-skill vss-deploy-dense-captioning",
            instruction,
        )
        self.assertNotIn("--runtime-env", instruction)
        self.assertIn("--wait-profile alerts", instruction)
        self.assertNotIn("rtsp://", instruction)

    def test_dense_captioning_wrapper_requires_no_argument_host_sample_probe(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            task_dir = root / "alerts_profile_api" / "rtxpro6000bw" / "step-2"
            task_dir.mkdir(parents=True)
            (task_dir / "instruction.md").write_text(
                "Use /vss-deploy-dense-captioning with the configured RTSP sample.",
                encoding="utf-8",
            )
            (task_dir / "task.toml").write_text(
                textwrap.dedent(
                    """
                    [task]
                    name = "nvidia-vss/vss-deploy-dense-captioning-alerts-step-2"

                    [metadata]
                    skill = "vss-deploy-dense-captioning"
                    deployment_profile = "alerts"
                    platform = "RTXPRO6000BW"
                    gpu_count = 1
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            smoke_runner._wrap_task_for_nemoclaw(
                task_dir=task_dir,
                skill="vss-deploy-dense-captioning",
                spec_path=(
                    REPO_ROOT
                    / "skills"
                    / "vss-deploy-dense-captioning"
                    / "evals"
                    / "alerts_profile_api.json"
                ),
                platform="RTXPRO6000BW",
            )

            prompt = (task_dir / "tests" / "nemoclaw_prompt.md").read_text(
                encoding="utf-8"
            )
            task_toml = (task_dir / "task.toml").read_text(encoding="utf-8")

        self.assertIn("`vss_orchestrator__rtsp_sample_probe`", prompt)
        self.assertIn("As your first OpenClaw `exec` call", prompt)
        self.assertIn("with no URL argument", prompt)
        self.assertIn("never pass the URL through MCP", prompt)
        self.assertIn("never pass the URL through MCP or print its value", prompt)
        self.assertIn("timeout, probe failure, or no-video result is terminal", prompt)
        self.assertIn(
            f"`{headless_runner.RTSP_TOOL_ENV_PROBE_COMMAND}`",
            prompt,
        )
        self.assertNotIn("rtsp://", prompt)
        self.assertIn(
            'required_mcp_tools = ["vss_orchestrator__profiles", '
            '"vss_orchestrator__docker_status", '
            '"vss_orchestrator__rtsp_sample_probe"]',
            task_toml,
        )

    def test_alerts_profile_first_action_matches_rtsp_attestation_command(self):
        spec = json.loads(
            (
                REPO_ROOT
                / "skills"
                / "vss-deploy-dense-captioning"
                / "evals"
                / "alerts_profile_api.json"
            ).read_text(encoding="utf-8")
        )
        first = spec["expects"][0]
        canonical = headless_runner.RTSP_TOOL_ENV_PROBE_COMMAND

        self.assertIn(
            f"first shell action, before deployment or any stream work, "
            f"must be exactly `{canonical}`",
            first["query"],
        )
        self.assertIn(f"exactly `{canonical}`", first["checks"][0])
        self.assertEqual(len(first["checks"]), 5)

    def test_nemoclaw_workspace_runs_dense_rtsp_probe_before_env_bootstrap(self):
        workspace_instructions = (
            REPO_ROOT
            / ".openclaw"
            / "workspace"
            / "_nemoclaw"
            / "AGENTS.md"
        ).read_text(encoding="utf-8")
        canonical = headless_runner.RTSP_TOOL_ENV_PROBE_COMMAND

        self.assertIn(f"`{canonical}`", workspace_instructions)
        self.assertLess(
            workspace_instructions.index(canonical),
            workspace_instructions.index("Run every `export` in `ENV.md`"),
        )
        self.assertIn(
            "automated `/vss-deploy-dense-captioning` evaluation",
            workspace_instructions,
        )
        self.assertIn(
            "Do not prepend `cat ENV.md`,\ncombine the probe with another command",
            workspace_instructions,
        )
        self.assertIn(
            "no other request may reorder\nthe session bootstrap",
            workspace_instructions,
        )

    def test_nemoclaw_wrapper_rejects_conflicting_gpu_boundary(self):
        with self.assertRaisesRegex(RuntimeError, "disagrees with task gpu_count=1"):
            smoke_runner._with_gpu_resource_guidance(
                "This trial reserves exactly 2 GPUs; valid device IDs are 0 through 1.\n",
                gpu_count=1,
            )

    def test_generic_task_wrapper_replaces_stale_launcher_without_wait_profile(self):
        with tempfile.TemporaryDirectory() as td:
            task_dir = Path(td) / "base" / "rtxpro6000bw" / "step-1"
            task_dir.mkdir(parents=True)
            (task_dir / "instruction.md").write_text(
                textwrap.dedent(
                    """
                    This Harbor trial is a thin launcher for NemoClaw/OpenClaw.

                    ```bash
                    python3 .github/skill-eval/nemoclaw/headless_runner.py \\
                      --prompt-file /tests/nemoclaw_prompt.md \\
                      --log-dir /logs/artifacts/nemoclaw \\
                      --launch-mode cli \\
                      --timeout 1500
                    ```
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            (task_dir / "task.toml").write_text(
                textwrap.dedent(
                    """
                    [task]
                    name = "nvidia-vss/vss-ask-video-base-rtx-step-1"

                    [metadata]
                    skill = "vss-ask-video"
                    profile = "base"
                    platform = "RTXPRO6000BW"
                    gpu_count = 1
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            smoke_runner._wrap_task_for_nemoclaw(
                task_dir=task_dir,
                skill="vss-ask-video",
                spec_path=REPO_ROOT / "skills" / "vss-ask-video" / "evals" / "base_profile_video_understanding.json",
                platform="RTXPRO6000BW",
            )

            instruction = (task_dir / "instruction.md").read_text(encoding="utf-8")

        self.assertIn("headless_runner.py", instruction)
        self.assertIn("--wait-profile base", instruction)

    def test_generic_task_wrapper_infers_profile_from_eval_spec(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            task_dir = root / "generated" / "rtxpro6000bw" / "step-1"
            task_dir.mkdir(parents=True)
            (task_dir / "instruction.md").write_text(
                "Existing launcher without profile wait\n"
                "python3 .github/skill-eval/nemoclaw/headless_runner.py\n",
                encoding="utf-8",
            )
            (task_dir / "task.toml").write_text(
                textwrap.dedent(
                    """
                    [task]
                    name = "nvidia-vss/generated-alerts-step-1"

                    [metadata]
                    skill = "vss-manage-alerts"
                    platform = "RTXPRO6000BW"
                    gpu_count = 1
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            spec_path = root / "alerts_vlm_real_time.json"
            spec_path.write_text(
                json.dumps(
                    {
                        "expects": [
                            {
                                "query": "Deploy the VSS **alerts** profile in `real-time` mode on `{{platform}}`."
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            scenario = smoke_runner._wrap_task_for_nemoclaw(
                task_dir=task_dir,
                skill="vss-manage-alerts",
                spec_path=spec_path,
                platform="RTXPRO6000BW",
            )

            instruction = (task_dir / "instruction.md").read_text(encoding="utf-8")
            task_toml = (task_dir / "task.toml").read_text(encoding="utf-8")

        self.assertEqual(scenario.deployment_profile, "alerts")
        self.assertIn("--wait-profile alerts", instruction)
        self.assertIn('deployment_profile = "alerts"', task_toml)

    def test_task_metadata_reader_falls_back_without_tomllib(self):
        with tempfile.TemporaryDirectory() as td:
            task_dir = Path(td)
            (task_dir / "task.toml").write_text(
                textwrap.dedent(
                    """
                    [metadata]
                    skill = "vss-ask-video"
                    profile = "base"
                    platform = "L40S"
                    gpu_count = 1
                    requires_nemoclaw = true
                    required_mcp_tools = ["vss_orchestrator__profiles", "vss_orchestrator__docker_status"]
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            previous = smoke_runner.tomllib
            smoke_runner.tomllib = None
            try:
                parsed = smoke_runner._read_task_toml(task_dir)
            finally:
                smoke_runner.tomllib = previous

        self.assertEqual(parsed["metadata"]["skill"], "vss-ask-video")
        self.assertEqual(parsed["metadata"]["gpu_count"], 1)
        self.assertTrue(parsed["metadata"]["requires_nemoclaw"])
        self.assertEqual(
            parsed["metadata"]["required_mcp_tools"],
            ["vss_orchestrator__profiles", "vss_orchestrator__docker_status"],
        )

    def test_nemoclaw_report_uses_harbor_eval_format(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            results_root = root / "results"
            run_id = "123456"
            trial_dir = (
                results_root
                / run_id
                / "2026-06-02__08-00-00"
                / "nvidia-vss-vss-deploy-profile-base-rtxpro6000bw"
            )
            (trial_dir / "verifier").mkdir(parents=True)
            (trial_dir / "agent").mkdir()
            (trial_dir / "result.json").write_text(
                json.dumps(
                    {
                        "trial_started_at": "2026-06-02T08:00:00Z",
                        "trial_finished_at": "2026-06-02T08:26:57Z",
                    }
                ),
                encoding="utf-8",
            )
            (trial_dir / "verifier" / "reward.txt").write_text("0.5", encoding="utf-8")
            (trial_dir / "verifier" / "judge.json").write_text(
                json.dumps(
                    {
                        "total": 2,
                        "passed": 1,
                        "checks": [
                            {"pass": True, "check": "docs endpoint responds"},
                            {
                                "pass": False,
                                "check": "MCP docker_status reached terminal state",
                                "rationale": "docker_status was not observed",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (trial_dir / "agent" / "trajectory.json").write_text(
                json.dumps(
                    {
                        "steps": [
                            {
                                "message": json.dumps(
                                    {
                                        "type": "assistant",
                                        "message": {
                                            "usage": {
                                                "input_tokens": 100,
                                                "cache_read_input_tokens": 10,
                                            }
                                        },
                                    }
                                )
                            }
                        ],
                        "final_metrics": {
                            "modelUsage": {
                                "claude": {
                                    "inputTokens": 8400,
                                    "cacheReadInputTokens": 100,
                                    "cacheCreationInputTokens": 50,
                                }
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            summary = root / "summary.md"
            previous = {
                "GITHUB_STEP_SUMMARY": os.environ.get("GITHUB_STEP_SUMMARY"),
                "GITHUB_RUN_ID": os.environ.get("GITHUB_RUN_ID"),
                "PR_HEAD_SHA": os.environ.get("PR_HEAD_SHA"),
                "PR_REPO": os.environ.get("PR_REPO"),
                "BREV_ENV_ID": os.environ.get("BREV_ENV_ID"),
            }
            os.environ["GITHUB_STEP_SUMMARY"] = str(summary)
            os.environ["GITHUB_RUN_ID"] = run_id
            os.environ["PR_HEAD_SHA"] = "abcdef0123456789"
            os.environ["PR_REPO"] = "NVIDIA-AI-Blueprints/video-search-and-summarization"
            os.environ["BREV_ENV_ID"] = "abc123"
            old_scratch = smoke_runner.SCRATCH_ROOT
            smoke_runner.SCRATCH_ROOT = root / "scratch"
            scenario = smoke_runner.NemoClawScenario(
                skill="vss-deploy-profile",
                spec_name="base",
                spec_path=REPO_ROOT / "skills" / "vss-deploy-profile" / "evals" / "base.json",
                platform="RTXPRO6000BW",
                gpu_count=1,
                task_dir=trial_dir,
                harbor_path=trial_dir.parent,
                task_name="rtxpro6000bw",
                deployment_profile="base",
            )
            try:
                smoke_runner._append_harbor_report(
                    scenario=scenario,
                    instance="vss-eval-rtx-1g-2",
                    results_root=results_root,
                    run_id=run_id,
                    reward=0.5,
                    harbor_rc=1,
                    log_path=root / "harbor.log",
                )
            finally:
                smoke_runner.SCRATCH_ROOT = old_scratch
                for key, value in previous.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

            report = summary.read_text(encoding="utf-8")
            benchmark = (root / "scratch" / run_id / "benchmark.md").read_text(encoding="utf-8")

        self.assertIn("## Harbor Eval - `skills/vss-deploy-profile/evals/base.json`", report)
        self.assertIn("runtime `NemoClaw/OpenClaw`", report)
        self.assertIn("| RTXPRO6000BW | FAIL 0.5 (1/2) | 0.5 | 26m 57s | 1 | 8.4k | 150 |", report)
        self.assertIn("MCP docker_status reached terminal state", report)
        self.assertIn("[trace](https://harbor-abc123.brevlab.com/jobs/", report)
        self.assertIn("Skills Eval Benchmark - NemoClaw sweep", benchmark)

    def test_nemoclaw_report_reads_openclaw_json_usage_when_trajectory_missing(self):
        with tempfile.TemporaryDirectory() as td:
            trial_dir = Path(td) / "trial"
            log_dir = trial_dir / "artifacts" / "nemoclaw"
            log_dir.mkdir(parents=True)
            (log_dir / "openclaw-agent.log").write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "assistant_message",
                                "usage": {
                                    "input_tokens": 8400,
                                    "cache_read_input_tokens": 100,
                                    "cache_creation_input_tokens": 50,
                                },
                            }
                        ),
                        "prefixed log "
                        + json.dumps(
                            {
                                "role": "assistant",
                                "modelUsage": {
                                    "main": {
                                        "inputTokens": 1100,
                                        "cacheReadInputTokens": 25,
                                    }
                                },
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            metrics = smoke_runner._load_trajectory_metrics(
                trial_dir,
                {"agent_result": {"n_input_tokens": None, "n_cache_tokens": None}},
            )

        self.assertEqual(metrics, ("2", "9.5k", "175"))

    def test_nemoclaw_report_reads_pretty_openclaw_result_payloads(self):
        with tempfile.TemporaryDirectory() as td:
            trial_dir = Path(td) / "trial"
            log_dir = trial_dir / "artifacts" / "nemoclaw"
            log_dir.mkdir(parents=True)
            (log_dir / "openclaw-agent.log").write_text(
                "warning before json\n"
                + json.dumps(
                    {
                        "runId": "abc",
                        "status": "ok",
                        "result": {
                            "payloads": [{"text": "one"}, {"text": "two"}],
                            "meta": {
                                "agentMeta": {
                                    "lastCallUsage": {
                                        "input": 42,
                                        "cacheRead": 5,
                                        "cacheWrite": 7,
                                    }
                                }
                            },
                        },
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            metrics = smoke_runner._load_trajectory_metrics(
                trial_dir,
                {"agent_result": {"n_input_tokens": None, "n_cache_tokens": None}},
            )

        self.assertEqual(metrics, ("2", "42", "12"))

    def test_nemoclaw_report_preserves_zero_openclaw_usage(self):
        with tempfile.TemporaryDirectory() as td:
            trial_dir = Path(td) / "trial"
            log_dir = trial_dir / "artifacts" / "nemoclaw"
            log_dir.mkdir(parents=True)
            (log_dir / "openclaw-agent.log").write_text(
                json.dumps(
                    {
                        "result": {
                            "payloads": [{"text": "done"}],
                            "meta": {
                                "agentMeta": {
                                    "lastCallUsage": {
                                        "input": 0,
                                        "cacheRead": 0,
                                        "cacheWrite": 0,
                                    }
                                }
                            },
                        }
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            metrics = smoke_runner._load_trajectory_metrics(
                trial_dir,
                {"agent_result": {"n_input_tokens": None, "n_cache_tokens": None}},
            )

        self.assertEqual(metrics, ("1", "0", "0"))

    def test_nemoclaw_report_estimates_prompt_tokens_when_openclaw_usage_is_zero(self):
        with tempfile.TemporaryDirectory() as td:
            trial_dir = Path(td) / "trial"
            log_dir = trial_dir / "artifacts" / "nemoclaw"
            log_dir.mkdir(parents=True)
            (log_dir / "openclaw-agent.log").write_text(
                json.dumps(
                    {
                        "result": {
                            "payloads": [{"text": "done"}],
                            "meta": {
                                "systemPromptReport": {
                                    "systemPrompt": {"chars": 4000},
                                    "skills": {"promptChars": 800},
                                },
                                "finalPromptText": "x" * 400,
                                "agentMeta": {
                                    "lastCallUsage": {
                                        "input": 0,
                                        "cacheRead": 0,
                                        "cacheWrite": 0,
                                    }
                                },
                            },
                        }
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            metrics = smoke_runner._load_trajectory_metrics(
                trial_dir,
                {"agent_result": {"n_input_tokens": None, "n_cache_tokens": None}},
            )

        self.assertEqual(metrics, ("1", "~1.3k", "0"))

    def test_nemoclaw_report_falls_back_to_harbor_tokens_when_openclaw_zero(self):
        with tempfile.TemporaryDirectory() as td:
            trial_dir = Path(td) / "trial"
            log_dir = trial_dir / "artifacts" / "nemoclaw"
            log_dir.mkdir(parents=True)
            (log_dir / "openclaw-agent.log").write_text(
                json.dumps(
                    {
                        "result": {
                            "payloads": [{"text": "done"}],
                            "meta": {
                                "agentMeta": {
                                    "lastCallUsage": {
                                        "input": 0,
                                        "cacheRead": 0,
                                        "cacheWrite": 0,
                                    }
                                }
                            },
                        }
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            metrics = smoke_runner._load_trajectory_metrics(
                trial_dir,
                {"agent_result": {"n_input_tokens": 28_008_935, "n_cache_tokens": 27_445_245}},
            )

        self.assertEqual(metrics, ("1", "28.0M", "27.4M"))

    def test_nemoclaw_report_marks_async_metrics_as_not_emitted(self):
        with tempfile.TemporaryDirectory() as td:
            trial_dir = Path(td) / "trial"
            log_dir = trial_dir / "artifacts" / "nemoclaw"
            log_dir.mkdir(parents=True)
            (log_dir / "openclaw-agent.log").write_text(
                json.dumps({"type": "assistant_partial"}) + "\n",
                encoding="utf-8",
            )
            (log_dir / "nemoclaw_hooks_response.json").write_text(
                json.dumps(
                    {
                        "elapsed_s": 699.94,
                        "response": {
                            "status": 200,
                            "body": {
                                "ok": True,
                                "mode": "cli-async",
                                "returncode": 0,
                            },
                        },
                        "wait": {
                            "waited": True,
                            "ok": True,
                            "profile": "base",
                        },
                    }
                ),
                encoding="utf-8",
            )
            (log_dir / "nemoclaw_wait.json").write_text(
                json.dumps([{"ok": False}, {"ok": True}]),
                encoding="utf-8",
            )

            metrics = smoke_runner._load_trajectory_metrics(
                trial_dir,
                {"agent_result": {"n_input_tokens": None, "n_cache_tokens": None}},
            )
            details = smoke_runner._nemoclaw_runtime_details(trial_dir)

        self.assertEqual(metrics, ("async readiness", "not emitted", "not emitted"))
        self.assertIn("- Readiness wait: `11m 40s`", details)
        self.assertIn("- Readiness polls: `2`", details)

    def test_nemoclaw_report_prefers_leaf_trial_and_links_run_when_viewer_unavailable(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            results_root = root / "results"
            run_id = "999"
            job_dir = results_root / run_id / "2026-06-02__08-00-00"
            trial_dir = job_dir / "rtxpro6000bw__abc"
            trial_dir.mkdir(parents=True)
            (job_dir / "result.json").write_text(
                json.dumps({"started_at": "2026-06-02T08:00:00Z", "finished_at": "2026-06-02T09:00:00Z"}),
                encoding="utf-8",
            )
            (trial_dir / "verifier").mkdir()
            (trial_dir / "result.json").write_text(
                json.dumps(
                    {
                        "started_at": "2026-06-02T08:10:00Z",
                        "finished_at": "2026-06-02T08:20:00Z",
                        "agent_result": {
                            "n_input_tokens": None,
                            "n_cache_tokens": None,
                        },
                    }
                ),
                encoding="utf-8",
            )
            (trial_dir / "verifier" / "reward.txt").write_text("1.0", encoding="utf-8")
            (trial_dir / "verifier" / "judge.json").write_text(
                json.dumps({"total": 7, "passed": 7, "checks": [{"pass": True, "check": "ok"}]}),
                encoding="utf-8",
            )
            summary = root / "summary.md"
            previous = {
                "GITHUB_STEP_SUMMARY": os.environ.get("GITHUB_STEP_SUMMARY"),
                "GITHUB_RUN_ID": os.environ.get("GITHUB_RUN_ID"),
                "PR_REPO": os.environ.get("PR_REPO"),
                "BREV_ENV_ID": os.environ.get("BREV_ENV_ID"),
            }
            os.environ["GITHUB_STEP_SUMMARY"] = str(summary)
            os.environ["GITHUB_RUN_ID"] = run_id
            os.environ["PR_REPO"] = "NVIDIA-AI-Blueprints/video-search-and-summarization"
            os.environ.pop("BREV_ENV_ID", None)
            old_scratch = smoke_runner.SCRATCH_ROOT
            smoke_runner.SCRATCH_ROOT = root / "scratch"
            scenario = smoke_runner.NemoClawScenario(
                skill="vss-deploy-profile",
                spec_name="base",
                spec_path=REPO_ROOT / "skills" / "vss-deploy-profile" / "evals" / "base.json",
                platform="RTXPRO6000BW",
                gpu_count=1,
                task_dir=trial_dir,
                harbor_path=trial_dir.parent,
                task_name="rtxpro6000bw",
                deployment_profile="base",
            )
            try:
                smoke_runner._append_harbor_report(
                    scenario=scenario,
                    instance="vss-eval-rtx-2g-4",
                    results_root=results_root,
                    run_id=run_id,
                    reward=1.0,
                    harbor_rc=0,
                    log_path=root / "harbor.log",
                )
            finally:
                smoke_runner.SCRATCH_ROOT = old_scratch
                for key, value in previous.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

            report = summary.read_text(encoding="utf-8")

        self.assertIn("PASS 1 (7/7)", report)
        self.assertIn("Total: `10m 0s`", report)
        self.assertIn("| RTXPRO6000BW | PASS 1 (7/7) | 1 | 10m 0s | n/a | n/a | n/a |", report)
        self.assertIn("[artifacts](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization/actions/runs/999)", report)


class OrchestratorMcpHelperCompatTest(unittest.TestCase):
    def test_orchestrator_tool_is_string_enum_on_eval_workers(self):
        self.assertIsInstance(orchestrator_mcp_helper.OrchestratorTool.PROFILES, str)
        self.assertEqual(
            orchestrator_mcp_helper.OrchestratorTool.RTSP_SAMPLE_PROBE,
            "vss_orchestrator__rtsp_sample_probe",
        )
        source = (REPO_ROOT / "deploy" / "docker" / "scripts" / "orchestrator_mcp_helper.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("except ImportError", source)

    def test_rtsp_sample_probe_client_takes_no_url_argument(self):
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout='{"status":"success","has_video":true}',
            stderr="",
        )
        output = io.StringIO()

        with (
            mock.patch.object(
                orchestrator_mcp_helper.subprocess,
                "run",
                return_value=completed,
            ) as run,
            contextlib.redirect_stdout(output),
        ):
            result = orchestrator_mcp_helper.tool_call(
                orchestrator_mcp_helper.OrchestratorTool.RTSP_SAMPLE_PROBE,
                mcp_url="http://127.0.0.1:9988/mcp",
                agent_dir=REPO_ROOT / "services" / "agent",
            )

        self.assertEqual(result["status"], "success")
        self.assertNotIn("RTSP_SAMPLE_URL", output.getvalue())
        self.assertNotIn("--json-args", run.call_args.args[0])


class DeployProfileNemoClawAdapterTest(unittest.TestCase):
    def test_evals_dir_and_nemoclaw_metadata_are_supported(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            skill_dir = root / "skills" / "vss-deploy-profile"
            (skill_dir / "evals").mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# skill\n", encoding="utf-8")
            (skill_dir / "evals" / "base.json").write_text(
                json.dumps(
                    {
                        "skills": ["vss-deploy-profile"],
                        "runner": "nemoclaw",
                        "requires_mcp": True,
                        "resources": {"platforms": {"L40S": {"gpu_count": 1}}},
                        "env": "env",
                        "expects": [{"query": "deploy base", "checks": ["ok"]}],
                    }
                ),
                encoding="utf-8",
            )
            out = root / "datasets"

            matrix, skipped = deploy_adapter.expand_matrix("base", "L40S", skill_dir=skill_dir)
            self.assertEqual(skipped, [])
            self.assertEqual(matrix, [("base", "L40S", 1)])

            deploy_adapter.generate_task(
                "base",
                "L40S",
                deploy_adapter.PROFILES["base"],
                out,
                skill_dir,
                gpu_count=1,
            )

            task_dir = out / "base" / "l40s"
            task_toml = (task_dir / "task.toml").read_text(encoding="utf-8")
            instruction = (task_dir / "instruction.md").read_text(encoding="utf-8")
            test_script = (task_dir / "tests" / "test.sh").read_text(encoding="utf-8")
            solve_script = (task_dir / "solution" / "solve.sh").read_text(encoding="utf-8")

            self.assertIn('runner = "nemoclaw"', task_toml)
            self.assertIn('requires_mcp = true', task_toml)
            self.assertIn('vss_orchestrator__docker_up', task_toml)
            self.assertIn("headless_runner.py", instruction)
            self.assertIn("--log-dir /logs/artifacts/nemoclaw", instruction)
            self.assertIn("--launch-mode cli", instruction)
            self.assertIn("--timeout 1500", instruction)
            self.assertIn("--wait-profile base", instruction)
            self.assertIn("nemoclaw_deploy_profile.py", test_script)
            self.assertTrue((task_dir / "tests" / "nemoclaw_deploy_profile.py").exists())
            prompt = (task_dir / "tests" / "nemoclaw_prompt.md").read_text(encoding="utf-8")
            self.assertIn("Use the `/vss-deploy-profile` skill", prompt)
            self.assertIn("reserves exactly 1 GPU", prompt)
            self.assertIn("only valid device ID is 0", prompt)
            self.assertIn("Leave GPU device-ID overrides unset", prompt)
            self.assertIn("shared placement on GPU 0", prompt)
            self.assertIn("Never request GPU 1", prompt)
            self.assertIn("git clean -fdx -e data/ -e /.env", solve_script)
            self.assertIn("-e /.mdx_data/models/", solve_script)
            self.assertNotIn("git clean -fdx -e data/ -e .env", solve_script)

    def test_nemoclaw_prompt_bounds_multi_gpu_device_ids(self):
        prompt = deploy_adapter.generate_nemoclaw_prompt(
            "base",
            "H100",
            deploy_adapter.PROFILES["base"],
            gpu_count=2,
        )

        self.assertIn("reserves exactly 2 GPUs", prompt)
        self.assertIn("valid device IDs are 0 through 1", prompt)
        self.assertIn("Never request an out-of-range device", prompt)

    def test_nemoclaw_deploy_profile_checks_remain_live_only(self):
        source_checks = [
            "`curl -sf --max-time 15 http://localhost:8000/health` returns exit 0",
            "`curl -sf --max-time 15 http://localhost:3000/` returns exit 0",
            "`docker ps --format '{{.Names}}' | grep -qx vss-agent` returns exit 0",
            "`docker ps --format '{{.Names}}' | grep -qx phoenix` returns exit 0",
        ]
        rendered = deploy_adapter._render_nemoclaw_eval_spec(
            {
                "expects": [
                    {
                        "checks": source_checks,
                    }
                ]
            }
        )

        checks = rendered["expects"][0]["checks"]
        self.assertEqual(checks, source_checks)
        self.assertIsNot(checks, source_checks)
        self.assertNotIn("final assistant text", "\n".join(checks))

    def test_nemoclaw_deploy_profile_execution_gate_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            report_path = Path(td) / "nemoclaw_hooks_response.json"
            valid = {
                "response": {
                    "status": 200,
                    "body": {"ok": True, "returncode": 0},
                },
                "wait": {"waited": True, "ok": True},
            }
            report_path.write_text(
                json.dumps(valid),
                encoding="utf-8",
            )
            with mock.patch.object(
                nemoclaw_deploy_profile_verifier,
                "HOOKS_REPORT_PATH",
                report_path,
            ):
                self.assertEqual(
                    nemoclaw_deploy_profile_verifier._execution_gate(),
                    (
                        True,
                        "agent response and deployment readiness wait passed",
                    ),
                )
                valid["wait"]["ok"] = False
                report_path.write_text(json.dumps(valid), encoding="utf-8")
                ok, reason = (
                    nemoclaw_deploy_profile_verifier._execution_gate()
                )
                self.assertFalse(ok)
                self.assertIn("readiness wait failed", reason)

                report_path.write_text("not-json", encoding="utf-8")
                ok, reason = (
                    nemoclaw_deploy_profile_verifier._execution_gate()
                )
                self.assertFalse(ok)
                self.assertIn("malformed", reason)

                report_path.unlink()
                ok, reason = (
                    nemoclaw_deploy_profile_verifier._execution_gate()
                )
                self.assertFalse(ok)
                self.assertIn("missing", reason)

    def test_nemoclaw_deploy_profile_verifier_checks_container_health(self):
        states = (
            ({"Running": True, "Health": {"Status": "healthy"}}, True),
            ({"Running": True, "Health": {"Status": "unhealthy"}}, False),
            ({"Running": True}, True),
            ({"Running": False}, False),
        )
        for state, expected in states:
            with (
                self.subTest(state=state),
                mock.patch.object(
                    nemoclaw_deploy_profile_verifier.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess(
                        ["docker", "inspect"],
                        0,
                        stdout=json.dumps(state),
                        stderr="",
                    ),
                ),
            ):
                ok, _reason = (
                    nemoclaw_deploy_profile_verifier._service_state(
                        "vss-vios-postgres"
                    )
                )
            self.assertEqual(ok, expected)

    def test_nemoclaw_deploy_profile_failed_probe_has_no_text_fallback(self):
        check = (
            "`curl -sf --max-time 15 http://localhost:8000/health` "
            "returns exit 0 OR final assistant text says 200 OK"
        )
        with mock.patch.object(
            nemoclaw_deploy_profile_verifier,
            "_run_shell",
            return_value=(False, "exit=7"),
        ):
            result = nemoclaw_deploy_profile_verifier._evaluate_check(check)

        self.assertFalse(result["pass"])
        self.assertEqual(result["matched"], "exit=7")

    def test_missing_eval_spec_does_not_generate_nemoclaw_launcher(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            skill_dir = root / "skills" / "vss-deploy-profile"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# skill\n", encoding="utf-8")
            out = root / "datasets"
            previous = os.environ.get("SKILLS_EVAL_RUNNER")
            os.environ["SKILLS_EVAL_RUNNER"] = "nemoclaw"
            try:
                deploy_adapter.generate_task(
                    "base",
                    "L40S",
                    deploy_adapter.PROFILES["base"],
                    out,
                    skill_dir,
                    gpu_count=1,
                )
            finally:
                if previous is None:
                    os.environ.pop("SKILLS_EVAL_RUNNER", None)
                else:
                    os.environ["SKILLS_EVAL_RUNNER"] = previous

            task_dir = out / "base" / "l40s"
            instruction = (task_dir / "instruction.md").read_text(encoding="utf-8")
            test_script = (task_dir / "tests" / "test.sh").read_text(encoding="utf-8")
            task_toml = (task_dir / "task.toml").read_text(encoding="utf-8")
            prompt_exists = (task_dir / "tests" / "nemoclaw_prompt.md").exists()

        self.assertNotIn("headless_runner.py", instruction)
        self.assertFalse(prompt_exists)
        self.assertIn("FAIL: no eval spec", test_script)
        self.assertNotIn('runner = "nemoclaw"', task_toml)


class NemoClawResultScopeTest(unittest.TestCase):
    def test_cleanup_recreates_current_run_without_touching_siblings(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            current = root / "current"
            sibling = root / "sibling"
            viewer = root / "_viewer"
            for path in (current, sibling, viewer):
                path.mkdir()
                (path / "marker").write_text(path.name, encoding="utf-8")

            smoke_runner._cleanup_results(root, "current")

            self.assertTrue(current.is_dir())
            self.assertEqual(list(current.iterdir()), [])
            self.assertTrue((sibling / "marker").is_file())
            self.assertTrue((viewer / "marker").is_file())

    def test_controlled_completion_seals_row_and_accounts_for_skips(self):
        with tempfile.TemporaryDirectory() as td:
            previous = smoke_runner.SCRATCH_ROOT
            smoke_runner.SCRATCH_ROOT = Path(td)
            try:
                smoke_runner._append_eval_row_completion(
                    run_id="123",
                    planned=3,
                    executed=1,
                    skipped=[("skill/spec/platform/step-2", "prior step failed")],
                )
                report = (Path(td) / "123" / "benchmark.md").read_text(
                    encoding="utf-8"
                )
            finally:
                smoke_runner.SCRATCH_ROOT = previous

        self.assertIn("- Planned scenarios: `3`", report)
        self.assertIn("- Executed scenarios: `1`", report)
        self.assertIn("- Skipped scenarios: `2`", report)
        self.assertIn("skill/spec/platform/step-2", report)
        self.assertTrue(
            report.rstrip().endswith(smoke_runner.EVAL_ROW_COMPLETION_MARKER)
        )


class SkillsEvalWorkflowTimeoutTest(unittest.TestCase):
    def test_nemoclaw_runner_defaults_to_safe_harbor_backstop(self):
        previous_scratch = smoke_runner.SCRATCH_ROOT
        validation = mock.Mock(side_effect=ValueError("stop after parse"))
        stderr = io.StringIO()
        try:
            with (
                tempfile.TemporaryDirectory() as td,
                mock.patch.dict(os.environ, {}, clear=True),
                mock.patch.object(
                    smoke_runner.worker_pool,
                    "validate_harbor_timeout_sec",
                    validation,
                ),
                contextlib.redirect_stderr(stderr),
                self.assertRaises(SystemExit),
            ):
                root = Path(td)
                smoke_runner.main(
                    [
                        "--dataset-root",
                        str(root / "dataset"),
                        "--results-root",
                        str(root / "results"),
                        "--scratch-root",
                        str(root / "scratch"),
                    ]
                )
        finally:
            smoke_runner.SCRATCH_ROOT = previous_scratch

        validation.assert_called_once_with(
            smoke_runner.worker_pool.DEFAULT_HARBOR_TIMEOUT_SEC
        )

    def test_skill_eval_actions_pin_public_rtsp_after_coordinator_env(self):
        coordinator_env = (
            "source /home/ubuntu/eval-coordinator/.env   # Anthropic / NGC / Brev"
        )
        rtsp_export = (
            'export RTSP_SAMPLE_URL="rtsp://global.stg.ga.'
            'launchpad.nvidia.com:11333/camera03"'
        )
        for relative in (
            ".github/workflows/skills-eval.yml",
            ".github/workflows/skills-eval-daily.yml",
        ):
            with self.subTest(workflow=relative):
                source = (REPO_ROOT / relative).read_text(encoding="utf-8")
                env_index = source.index(coordinator_env)
                set_index = source.index("set +a", env_index)
                venv_index = source.index('export VIRTUAL_ENV="$skill_eval_venv_dir"', set_index)
                rtsp_index = source.index(rtsp_export, venv_index)
                self.assertLess(env_index, set_index)
                self.assertLess(set_index, venv_index)
                self.assertLess(venv_index, rtsp_index)

    def test_nemoclaw_workflow_exports_bounded_timeouts(self):
        source = (REPO_ROOT / ".github" / "workflows" / "skills-eval.yml").read_text(
            encoding="utf-8"
        )
        nemoclaw_plan_source, nemoclaw_eval_and_report_source = source.split(
            "\n  nemoclaw_plan:", 1
        )[1].split("\n  nemoclaw-eval:", 1)
        nemoclaw_eval_source = nemoclaw_eval_and_report_source.split(
            "\n  nemoclaw-report:", 1
        )[0]
        nemoclaw_report_source = nemoclaw_eval_and_report_source.split(
            "\n  nemoclaw-report:", 1
        )[1]
        nemoclaw_job_header = nemoclaw_eval_source.split("\n    steps:", 1)[0]

        self.assertIn("max-parallel: 2", source)
        for job_source in (
            nemoclaw_plan_source,
            nemoclaw_eval_source,
            nemoclaw_report_source,
        ):
            self.assertIn("actions/setup-python@", job_source)
            self.assertIn(
                "python-version: ${{ env.SKILL_EVAL_PYTHON_VERSION }}",
                job_source,
            )
        self.assertIn('skill_eval_python="$(command -v python3)"', nemoclaw_eval_source)
        self.assertIn(
            '"$skill_eval_python" .github/skill-eval/nemoclaw/smoke_runner.py',
            nemoclaw_eval_source,
        )
        self.assertIn("nemoclaw_instance:", source)
        self.assertIn(
            "runs-on: [self-hosted, vss-skill-eval-runner, brev]",
            nemoclaw_plan_source,
        )
        self.assertIn(
            "runs-on: [self-hosted, vss-skill-eval-runner, brev]",
            nemoclaw_report_source,
        )
        self.assertIn(
            "runs-on: [self-hosted, vss-skill-eval-runner, brev]",
            nemoclaw_eval_source,
        )
        self.assertIn("timeout-minutes: 300", nemoclaw_job_header)
        self.assertNotIn("timeout-minutes: 180", nemoclaw_job_header)
        self.assertIn("timeout-minutes: 270", nemoclaw_eval_source)
        self.assertIn("export NEMOCLAW_LOCK_TIMEOUT_SEC=1200", source)
        self.assertIn("export NEMOCLAW_RUN_TIMEOUT_SEC=13260", source)
        self.assertIn(
            "export NEMOCLAW_REMOTE_LOCK_HEARTBEAT_SEC=180",
            source,
        )
        self.assertIn(
            "export NEMOCLAW_REMOTE_LOCK_HEARTBEAT_MAX_SILENCE_SEC=660",
            source,
        )
        self.assertIn(
            'export SKILL_EVAL_LOCK_OWNER_CONTEXT="${{ matrix.name }}"',
            source,
        )
        self.assertIn(
            "export SKILL_EVAL_REMOTE_LOCK_HEARTBEAT_SEC=180",
            source,
        )
        self.assertIn(
            "export SKILL_EVAL_REMOTE_LOCK_HEARTBEAT_MAX_SILENCE_SEC=660",
            source,
        )
        self.assertIn("NEMOCLAW_INPUT_INSTANCE:", source)
        self.assertIn('export NEMOCLAW_BREV_INSTANCE="$NEMOCLAW_INPUT_INSTANCE"', source)
        self.assertIn("export NEMOCLAW_HARBOR_TIMEOUT_SEC=12000", source)
        self.assertGreater(
            12000,
            smoke_runner.worker_pool.MIN_HARBOR_BACKSTOP_SEC,
        )
        self.assertGreaterEqual(13260, 1200 + 12000)
        self.assertLessEqual(
            13260 + smoke_runner.worker_pool.HARBOR_SHUTDOWN_GRACE_SEC,
            270 * 60,
        )
        self.assertIn("export NEMOCLAW_INSTALL_REF=v0.0.103", source)
        node_setup_index = nemoclaw_eval_source.index(
            "Set up pinned NemoClaw build runtime"
        )
        payload_index = nemoclaw_eval_source.index(
            "Prepare exact NemoClaw CLI payload"
        )
        agent_index = nemoclaw_eval_source.index(
            "Run NemoClaw skills eval agent"
        )
        self.assertLess(node_setup_index, payload_index)
        self.assertLess(payload_index, agent_index)
        self.assertIn(
            "actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020",
            nemoclaw_eval_source,
        )
        self.assertIn("node-version: 22.22.1", nemoclaw_eval_source)
        self.assertIn(
            ".github/skill-eval/nemoclaw/prepare_cli_payload.py",
            nemoclaw_eval_source,
        )
        self.assertIn('--github-env "$GITHUB_ENV"', nemoclaw_eval_source)
        self.assertIn("export NEMOCLAW_REMOTE_SETUP_TIMEOUT_SEC=1500", source)
        self.assertIn("export NEMOCLAW_SETUP_TIMEOUT_SEC=1620", source)
        self.assertIn("export NEMOCLAW_SETUP_CELL_TIMEOUT=1200", source)
        self.assertIn("export NEMOCLAW_AGENT_TIMEOUT_SEC=3300", source)
        self.assertNotIn("NEMOCLAW_FAIL_FAST_ON_STEP_FAILURE=0", source)
        self.assertIn("export NEMOCLAW_FAIL_FAST_ON_STEP_FAILURE=1", source)
        self.assertIn("export NEMOCLAW_GATEWAY_PORT=19080", source)
        self.assertIn("unset NEMOCLAW_OPENSHELL_GATEWAY_STATE_DIR", source)
        self.assertIn(
            "export OPENSHELL_DOCKER_NETWORK_NAME=openshell-docker",
            source,
        )
        self.assertIn(
            'export NEMOCLAW_LOCK_OWNER_CONTEXT="NemoClaw / ${{ matrix.name }}"',
            source,
        )
        self.assertIn(
            "RUN_SCRATCH: /tmp/skill-eval/nemoclaw/${{ matrix.slug }}/${{ github.run_id }}",
            source,
        )
        self.assertIn("Initialize current-attempt result sentinel", source)
        self.assertIn("Publish current-attempt result sentinel", source)
        self.assertIn(
            "RUN_RESULTS: /tmp/skill-eval/results/${{ matrix.slug }}/${{ github.run_id }}",
            source,
        )
        self.assertIn('rm -rf "$RUN_SCRATCH" "$RUN_RESULTS"', source)
        self.assertIn("The NemoClaw skill selection produced no reportable rows", source)
        self.assertIn('TAR_PATHS+=("nemoclaw/${{ matrix.slug }}/${{ github.run_id }}")', source)
        self.assertNotIn(
            'if [ ! -d "$RUN_RESULTS" ]; then\n'
            '            echo "no results dir for this run',
            source,
        )
        self.assertNotIn("NEMOCLAW_REMOTE_LOCK_STALE_SEC", source)
        self.assertIn("if: matrix.kind == 'eval'", source)
        self.assertIn("report_results.py blocked", source)
        self.assertIn("id: nemoclaw-blocked", source)
        self.assertIn("steps.nemoclaw-blocked.outcome", source)
        self.assertIn("report_results.py verdict", source)
        self.assertIn("\n  nemoclaw-report:", source)
        self.assertIn("needs: [nemoclaw_plan, nemoclaw-eval]", source)
        self.assertIn("report_results.py aggregate", source)
        self.assertIn("Initialize current-attempt report scope", source)
        self.assertIn('rm -rf "$REPORT_ROOT"', source)
        self.assertIn("Enforce complete result publication", source)
        self.assertIn("overwrite: true", source)
        self.assertEqual(nemoclaw_eval_source.count("github.run_attempt"), 1)
        self.assertIn(
            "nemoclaw-cli-cache-${{ github.run_id }}-${{ github.run_attempt }}",
            nemoclaw_eval_source,
        )
        self.assertIn('--cache-root "$NEMOCLAW_CLI_CACHE"', nemoclaw_eval_source)
        self.assertIn("PLAN_RESULT: ${{ needs.nemoclaw_plan.result }}", source)
        self.assertIn("nemoclaw-plan__missing", source)


if __name__ == "__main__":
    unittest.main()
