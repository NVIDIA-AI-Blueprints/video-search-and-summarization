# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HELPER_PATH = Path(__file__).parents[1] / "attach_vss_agent.py"
MODULE_SPEC = importlib.util.spec_from_file_location(
    "attach_vss_agent_under_test", HELPER_PATH
)
if MODULE_SPEC is None or MODULE_SPEC.loader is None:
    raise RuntimeError(f"Could not load {HELPER_PATH}")
attach = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = attach
MODULE_SPEC.loader.exec_module(attach)


class RecordingRunner:
    def __init__(self, *, identity_drift: bool = False) -> None:
        self.dry_run = False
        self.commands: list[list[str]] = []
        self._identity_calls = 0
        self.identity_drift = identity_drift

    def run(
        self,
        command: list[str],
        *,
        capture: bool = False,
        timeout: int = 900,
    ) -> str:
        del capture, timeout
        self.commands.append(command)
        if command[:3] == ["openshell", "forward", "list"]:
            return "SANDBOX BIND PORT PID STATUS\ndemo 127.0.0.1 18789 123 running"
        if command[-2:] == ["gateway-token", "--quiet"]:
            return "not-printed-token"
        if "vss-identity-check" in command:
            self._identity_calls += 1
            root_index = command.index("vss-identity-check") + 1
            root = command[root_index]
            names = command[root_index + 1 :]
            digest = (
                "b" * 64
                if self.identity_drift and self._identity_calls > 1
                else "a" * 64
            )
            return "\n".join(f"{digest}  {root}/{name}" for name in names)
        if "vss-runtime-setup" in command:
            return "vss, version 3.3.0"
        return ""


def make_args(root: Path, runtime: str = "openclaw") -> argparse.Namespace:
    return argparse.Namespace(
        runtime=runtime,
        sandbox="demo",
        vss_origin="http://host.openshell.internal:7777",
        repo_root=root,
        runtime_ref="a" * 40,
        runtime_repository=attach.DEFAULT_RUNTIME_REPOSITORY,
        runtime_dir=attach.DEFAULT_RUNTIME_DIR,
        agent_api_url="http://127.0.0.1:18789",
        skip_api_check=False,
        dry_run=False,
    )


class ValidationTests(unittest.TestCase):
    def test_validates_bare_origins_and_rejects_credentials_or_paths(self) -> None:
        origin = attach.validate_origin("https://vss.example.test:8443/")
        self.assertEqual(origin.url, "https://vss.example.test:8443")
        self.assertEqual(origin.port, 8443)
        for value in (
            "file:///tmp/vss",
            "https://user:pass@vss.example.test",
            "https://vss.example.test/private",
            "https://vss.example.test?token=value",
        ):
            with self.subTest(value=value), self.assertRaises(attach.AttachError):
                attach.validate_origin(value)

    def test_runtime_directory_cannot_escape_sandbox(self) -> None:
        self.assertEqual(
            attach.validate_runtime_dir("/sandbox/.vss/runtime"),
            "/sandbox/.vss/runtime",
        )
        for value in ("sandbox/runtime", "/sandbox", "/sandbox/../etc"):
            with self.subTest(value=value), self.assertRaises(attach.AttachError):
                attach.validate_runtime_dir(value)

    def test_runtime_ref_requires_a_full_commit_id(self) -> None:
        with self.assertRaises(attach.AttachError):
            attach.resolve_runtime_ref(Path("/unused"), "abcdef0")
        self.assertEqual(
            attach.resolve_runtime_ref(Path("/unused"), "A" * 40),
            "a" * 40,
        )

    def test_recursively_discovers_nested_skills(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "skills/operations/search").mkdir(parents=True)
            (root / "skills/deployment/profile").mkdir(parents=True)
            (root / "skills/.hidden/ignored").mkdir(parents=True)
            for path in (
                root / "skills/operations/search/SKILL.md",
                root / "skills/deployment/profile/SKILL.md",
                root / "skills/.hidden/ignored/SKILL.md",
            ):
                path.write_text("---\nname: test\n---\n", encoding="utf-8")
            found = attach.discover_skills(root)
        self.assertEqual(
            [path.name for path in found],
            ["profile", "search"],
        )

    def test_policy_is_limited_to_vss_and_runtime_dependencies(self) -> None:
        policy = attach.build_policy(
            attach.validate_origin("http://host.openshell.internal:7777")
        )
        serialized = str(policy)
        self.assertIn("host.openshell.internal", serialized)
        self.assertIn("github.com", serialized)
        self.assertIn("pypi.org", serialized)
        self.assertNotIn("api.anthropic.com", serialized)
        self.assertNotIn("api.telegram.org", serialized)
        self.assertNotIn("discord.com", serialized)
        self.assertIn("{'path': '/usr/bin/curl'}", serialized)
        rendered = attach.render_policy(
            attach.validate_origin("http://host.openshell.internal:7777")
        )
        self.assertIn("\nnetwork_policies:\n", rendered)
        self.assertNotIn("api.anthropic.com", rendered)

    def test_discovers_runtime_specific_forward(self) -> None:
        runner = RecordingRunner()
        origin = attach.discover_api_origin(
            runner, attach.PROFILES["openclaw"], "demo", None
        )
        self.assertEqual(origin.url, "http://127.0.0.1:18789")

    def test_prefers_the_runtime_default_when_other_forwards_exist(self) -> None:
        runner = RecordingRunner()
        runner.run = mock.Mock(
            return_value=(
                "SANDBOX BIND PORT PID STATUS\n"
                "demo 127.0.0.1 18790 124 running\n"
                "demo 127.0.0.1 18789 123 running"
            )
        )
        origin = attach.discover_api_origin(
            runner, attach.PROFILES["openclaw"], "demo", None
        )
        self.assertEqual(origin.url, "http://127.0.0.1:18789")

    def test_ignores_stale_or_dead_forwards(self) -> None:
        runner = RecordingRunner()
        runner.run = mock.Mock(
            return_value=(
                "SANDBOX BIND PORT PID STATUS\n"
                "demo 127.0.0.1 18789 123 dead\n"
                "demo 127.0.0.1 18790 124 running"
            )
        )
        origin = attach.discover_api_origin(
            runner, attach.PROFILES["openclaw"], "demo", None
        )
        self.assertEqual(origin.url, "http://127.0.0.1:18790")

    def test_hermes_identity_root_covers_its_canonical_state(self) -> None:
        self.assertEqual(attach.PROFILES["hermes"].identity_root, "/sandbox/.hermes")

    def test_api_probe_rejects_an_oversized_model_list(self) -> None:
        runner = RecordingRunner()
        oversized = io.BytesIO(b"x" * (attach.MAX_API_RESPONSE_BYTES + 1))
        with (
            mock.patch.object(attach.urllib.request, "urlopen", return_value=oversized),
            self.assertRaisesRegex(attach.AttachError, "oversized model list"),
        ):
            attach.verify_api(
                runner,
                attach.PROFILES["openclaw"],
                "demo",
                "http://127.0.0.1:18789",
            )


class AttachFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "skills/operations/search").mkdir(parents=True)
        (self.root / "skills/operations/alerts").mkdir(parents=True)
        (self.root / "skills/operations/search/SKILL.md").write_text(
            "---\nname: vss-search-archive\n---\n", encoding="utf-8"
        )
        (self.root / "skills/operations/alerts/SKILL.md").write_text(
            "---\nname: vss-manage-alerts\n---\n", encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_openclaw_attach_is_additive_and_preserves_identity(self) -> None:
        runner = RecordingRunner()
        with (
            mock.patch.object(attach.shutil, "which", return_value="/usr/bin/nemoclaw"),
            mock.patch.object(attach, "verify_source_snapshot"),
            mock.patch.object(attach, "verify_api", return_value="openclaw/default"),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            attach.attach(make_args(self.root), runner)

        flattened = [" ".join(command) for command in runner.commands]
        self.assertEqual(sum(" skill install " in command for command in flattened), 2)
        self.assertTrue(any(" policy add " in command for command in flattened))
        policy_commands = [
            command for command in runner.commands if command[2:4] == ["policy", "add"]
        ]
        self.assertEqual(len(policy_commands), 1)
        policy_path = policy_commands[0][policy_commands[0].index("--from-file") + 1]
        self.assertTrue(policy_path.endswith(".yaml"))
        self.assertNotIn("--trusted-private-host", policy_commands[0])
        self.assertTrue(
            any(
                "openclaw config set gateway.http.endpoints.responses.enabled true"
                in command
                for command in flattened
            )
        )
        self.assertTrue(any(" gateway restart" in command for command in flattened))
        self.assertFalse(any(" onboard" in command for command in flattened))
        self.assertFalse(any(" inference set" in command for command in flattened))
        self.assertFalse(
            any("SOUL.md" in command and " upload " in command for command in flattened)
        )
        uploads = [command for command in flattened if " upload " in command]
        self.assertEqual(len(uploads), 1)
        self.assertIn("/sandbox/.vss/", uploads[0])
        self.assertTrue(
            any(
                command.endswith("chmod 600 /sandbox/.vss/agent-capabilities.json")
                for command in flattened
            )
        )

    def test_hermes_attach_does_not_apply_openclaw_config(self) -> None:
        runner = RecordingRunner()
        args = make_args(self.root, runtime="hermes")
        args.agent_api_url = "http://127.0.0.1:8642"
        with (
            mock.patch.object(
                attach.shutil, "which", return_value="/usr/bin/nemohermes"
            ),
            mock.patch.object(attach, "verify_source_snapshot"),
            mock.patch.object(attach, "verify_api", return_value="hermes-agent"),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            attach.attach(args, runner)
        flattened = [" ".join(command) for command in runner.commands]
        self.assertTrue(
            any(command.startswith("nemohermes demo") for command in flattened)
        )
        self.assertFalse(any("openclaw config" in command for command in flattened))
        self.assertFalse(any(" gateway restart" in command for command in flattened))

    def test_identity_drift_fails_closed(self) -> None:
        runner = RecordingRunner(identity_drift=True)
        with (
            mock.patch.object(attach.shutil, "which", return_value="/usr/bin/nemoclaw"),
            mock.patch.object(attach, "verify_source_snapshot"),
            mock.patch.object(attach, "verify_api", return_value="openclaw/default"),
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaisesRegex(attach.AttachError, "identity files changed"),
        ):
            attach.attach(make_args(self.root), runner)
        self.assertFalse(any("upload" in command for command in runner.commands))


if __name__ == "__main__":
    unittest.main()
