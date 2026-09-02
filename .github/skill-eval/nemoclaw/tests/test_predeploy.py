# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for the harness-side VSS pre-deploy (orchestrator MCP path)."""
from __future__ import annotations

import importlib.util
import json
import types
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[4]
NEMOCLAW_DIR = REPO_ROOT / ".github/skill-eval/nemoclaw"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DeployModeMappingTests(unittest.TestCase):
    """`deploy_mode` is overloaded in the specs and must be demultiplexed.

    `verification` / `real-time` are alerts pipeline modes and belong in
    `profile_mode`; `remote-all` is LLM/VLM placement and has no `profile_mode`
    representation, so passing it through would make docker_generate reject the
    call.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.predeploy = _load("predeploy", NEMOCLAW_DIR / "predeploy.py")

    def test_alerts_pipeline_modes_become_profile_mode(self) -> None:
        for mode in ("verification", "real-time"):
            with self.subTest(mode=mode):
                profile_mode, overrides = self.predeploy._split_deploy_mode(
                    "alerts", mode
                )
                self.assertEqual(profile_mode, mode)
                self.assertEqual(overrides, {})

    def test_remote_all_becomes_env_overrides_not_profile_mode(self) -> None:
        profile_mode, overrides = self.predeploy._split_deploy_mode(
            "search", "remote-all"
        )
        self.assertIsNone(profile_mode)
        self.assertEqual(overrides, {"LLM_MODE": "remote", "VLM_MODE": "remote"})

    def test_empty_deploy_mode_is_neither(self) -> None:
        self.assertEqual(self.predeploy._split_deploy_mode("base", ""), (None, {}))
        self.assertEqual(self.predeploy._split_deploy_mode("base", None), (None, {}))

    def test_pipeline_mode_on_non_alerts_profile_is_rejected(self) -> None:
        # Silently dropping it would deploy the wrong stack and surface as
        # mystery check failures much later.
        with self.assertRaisesRegex(ValueError, "only valid for the alerts"):
            self.predeploy._split_deploy_mode("base", "verification")

    def test_unknown_deploy_mode_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported deploy_mode"):
            self.predeploy._split_deploy_mode("base", "warp-speed")

    def test_unsupported_profile_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported profile"):
            self.predeploy.predeploy("warehouse")

    def test_supported_profiles_match_the_corpus(self) -> None:
        self.assertEqual(
            set(self.predeploy.SUPPORTED_PROFILES),
            {"base", "lvs", "search", "alerts"},
        )


class PredeploySequenceTests(unittest.TestCase):
    """The documented order is profiles -> prereqs -> generate -> up -> status."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.predeploy = _load("predeploy", NEMOCLAW_DIR / "predeploy.py")

    def _fake_helper(self, statuses):
        calls: list[tuple[str, dict]] = []

        class Tool:
            PROFILES = "vss_orchestrator__profiles"
            PREREQS = "vss_orchestrator__prereqs"
            DOCKER_GENERATE = "vss_orchestrator__docker_generate"
            DOCKER_UP = "vss_orchestrator__docker_up"
            DOCKER_STATUS = "vss_orchestrator__docker_status"

        status_iter = iter(statuses)

        def tool_call(name, *, mcp_url, agent_dir, arguments=None, **kw):
            calls.append((str(name), dict(arguments or {})))
            if name == Tool.DOCKER_GENERATE:
                return {"docker_compose_id": "cid-1"}
            if name == Tool.DOCKER_UP:
                return {"docker_compose_ops_id": "ops-1"}
            if name == Tool.DOCKER_STATUS:
                return next(status_iter)
            return {}

        def require_success(result, label):
            if result.get("status") == "error":
                raise RuntimeError(f"{label} failed")
            return result

        return types.SimpleNamespace(
            OrchestratorTool=Tool,
            tool_call=tool_call,
            require_success=require_success,
            check_mcp_health=lambda url, d: (True, "ok"),
        ), calls

    def _run(self, statuses, **kwargs):
        helper, calls = self._fake_helper(statuses)
        with (
            mock.patch.object(self.predeploy, "_load_helper", return_value=helper),
            mock.patch.object(
                self.predeploy, "_repo_dir", return_value=Path("/repo")
            ),
            mock.patch.object(Path, "is_dir", return_value=True),
            mock.patch.object(self.predeploy.time, "sleep", lambda _s: None),
        ):
            result = self.predeploy.predeploy(
                kwargs.pop("profile", "base"), poll_sleep_s=0, **kwargs
            )
        return result, calls

    def test_documented_tool_order(self) -> None:
        _, calls = self._run([{"running": False, "exit_code": 0}])
        self.assertEqual(
            [name for name, _ in calls],
            [
                "vss_orchestrator__profiles",
                "vss_orchestrator__prereqs",
                "vss_orchestrator__docker_generate",
                "vss_orchestrator__docker_up",
                "vss_orchestrator__docker_status",
            ],
        )

    def test_polls_until_running_is_false(self) -> None:
        _, calls = self._run(
            [
                {"running": True, "exit_code": None},
                {"running": True, "exit_code": None},
                {"running": False, "exit_code": 0},
            ]
        )
        status_calls = [n for n, _ in calls if n.endswith("docker_status")]
        self.assertEqual(len(status_calls), 3)

    def test_nonzero_exit_code_fails_even_when_not_running(self) -> None:
        """The regression this whole module exists to avoid.

        `orchestrator_mcp_helper.poll_compose_op` returns as soon as `running`
        is false and never inspects `exit_code`, so a failed deploy looks
        successful. The docs define ready as running == false AND
        exit_code == 0.
        """
        with self.assertRaisesRegex(RuntimeError, "VSS pre-deploy failed"):
            self._run([{"running": False, "exit_code": 1}])

    def test_missing_compose_id_fails_loudly(self) -> None:
        helper, _ = self._fake_helper([{"running": False, "exit_code": 0}])
        original = helper.tool_call

        def tool_call(name, **kw):
            if name == helper.OrchestratorTool.DOCKER_GENERATE:
                return {}
            return original(name, **kw)

        helper.tool_call = tool_call
        with (
            mock.patch.object(self.predeploy, "_load_helper", return_value=helper),
            mock.patch.object(
                self.predeploy, "_repo_dir", return_value=Path("/repo")
            ),
            mock.patch.object(Path, "is_dir", return_value=True),
        ):
            with self.assertRaisesRegex(RuntimeError, "no docker_compose_id"):
                self.predeploy.predeploy("base")

    def test_alerts_mode_reaches_docker_generate(self) -> None:
        _, calls = self._run(
            [{"running": False, "exit_code": 0}],
            profile="alerts",
            deploy_mode="verification",
        )
        args = dict(calls)["vss_orchestrator__docker_generate"]
        self.assertEqual(args["profile"], "alerts")
        self.assertEqual(args["profile_mode"], "verification")

    def test_remote_all_reaches_docker_generate_as_env_overrides(self) -> None:
        _, calls = self._run(
            [{"running": False, "exit_code": 0}],
            profile="search",
            deploy_mode="remote-all",
        )
        args = dict(calls)["vss_orchestrator__docker_generate"]
        self.assertNotIn("profile_mode", args)
        self.assertEqual(
            args["env_overrides"], ["LLM_MODE=remote", "VLM_MODE=remote"]
        )

    def test_no_profile_mode_key_when_none(self) -> None:
        _, calls = self._run([{"running": False, "exit_code": 0}], profile="base")
        args = dict(calls)["vss_orchestrator__docker_generate"]
        self.assertNotIn("profile_mode", args)
        self.assertEqual(args["env_overrides"], [])


class OptInContractTests(unittest.TestCase):
    """Pre-deploy is opt-in by `profile` in task.toml [metadata].

    That is what keeps vss-deploy-* / vss-setup-* correct — pre-deploying a
    skill whose eval IS the deploy would make it vacuous — with no per-skill
    branching in the environment.
    """

    def test_ask_video_adapter_emits_profile(self) -> None:
        text = (
            REPO_ROOT
            / ".github/skill-eval/adapters/vss-ask-video/generate.py"
        ).read_text()
        self.assertIn('f\'profile = "{profile}"\'', text)

    def test_deploy_and_setup_adapters_do_not_emit_profile(self) -> None:
        adapters = REPO_ROOT / ".github/skill-eval/adapters"
        offenders = []
        for gen in sorted(adapters.glob("*/generate.py")):
            skill = gen.parent.name
            if not skill.startswith(("vss-deploy-", "vss-setup-")):
                continue
            body = gen.read_text()
            # Only the [metadata] emission matters; local `profile = ...`
            # variables are fine.
            if 'f\'profile = "{profile}"\'' in body:
                offenders.append(skill)
        self.assertEqual(
            offenders,
            [],
            "deploy/setup skills must not opt into VSS pre-deploy — the deploy "
            "is the measurement",
        )


class PreamblePredeployCouplingTests(unittest.TestCase):
    """The prompt must not contradict the environment.

    Emitting `profile` into [metadata] (harness deploys) while still rendering
    the stock PREAMBLE ("you are pre-authorized to deploy ... /vss-deploy-profile")
    tells the agent to do the one thing that destroys the harness's deployment:
    the skill's Step 0 is a teardown. These two decisions are the same decision.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.gen = _load(
            "ask_video_generate",
            REPO_ROOT / ".github/skill-eval/adapters/vss-ask-video/generate.py",
        )

    def test_predeployed_preamble_forbids_deploying(self) -> None:
        text = self.gen._preamble_for({}, predeployed=True)
        self.assertIn("ALREADY DEPLOYED", text)
        self.assertNotIn("pre-authorized to deploy", text)

    def test_legacy_preamble_still_available_for_non_predeployed(self) -> None:
        text = self.gen._preamble_for({}, predeployed=False)
        self.assertIn("pre-authorized to deploy", text)

    def test_cli_clause_still_keyed_off_the_spec(self) -> None:
        spec = {"expects": [{"checks": ["use `vss vios clip --sensor x`"]}]}
        self.assertIn("vios clip --sensor", self.gen._preamble_for(spec))
        self.assertNotIn("vios clip --sensor", self.gen._preamble_for({}))

    def test_ask_video_specs_no_longer_ask_the_agent_to_deploy(self) -> None:
        evals = REPO_ROOT / "skills/vss-ask-video/evals"
        for spec_path in sorted(evals.glob("*.json")):
            if spec_path.name == "evals.json":
                continue
            spec = json.loads(spec_path.read_text())
            first = spec["expects"][0]["query"]
            with self.subTest(spec=spec_path.name):
                self.assertNotIn(
                    "/vss-deploy-profile",
                    first,
                    "expects[0] still instructs a deploy the harness now owns",
                )

    def test_no_spec_step_asserts_deploy_liveness_only(self) -> None:
        """Checks the harness pre-deploy already guarantees are free passes."""
        evals = REPO_ROOT / "skills/vss-ask-video/evals"
        banned = "grep -qx vss-agent-ui"
        for spec_path in sorted(evals.glob("*.json")):
            if spec_path.name == "evals.json":
                continue
            spec = json.loads(spec_path.read_text())
            for i, expect in enumerate(spec["expects"]):
                for check in expect.get("checks") or []:
                    with self.subTest(spec=spec_path.name, step=i + 1):
                        self.assertNotIn(banned, check)


if __name__ == "__main__":
    unittest.main()


class TailLinesBoundTests(unittest.TestCase):
    """`docker_status` validates tail_lines <= 20 server-side.

    Run 33588684082 failed both legs with `tail_lines=200` (validation requires
    <= 20). `orchestrator_mcp_helper.poll_compose_op` defaults to 200 and has
    the same latent problem.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.predeploy = _load("predeploy", NEMOCLAW_DIR / "predeploy.py")

    def test_max_is_twenty(self) -> None:
        self.assertEqual(self.predeploy.MAX_TAIL_LINES, 20)

    def test_default_is_within_the_server_bound(self) -> None:
        sig = __import__("inspect").signature(self.predeploy.predeploy)
        self.assertLessEqual(
            sig.parameters["tail_lines"].default, self.predeploy.MAX_TAIL_LINES
        )

    def test_oversized_request_is_clamped_not_sent(self) -> None:
        helper, calls = PredeploySequenceTests()._fake_helper(
            [{"running": False, "exit_code": 0}]
        )
        with (
            mock.patch.object(self.predeploy, "_load_helper", return_value=helper),
            mock.patch.object(self.predeploy, "_repo_dir", return_value=Path("/repo")),
            mock.patch.object(Path, "is_dir", return_value=True),
        ):
            self.predeploy.predeploy("base", tail_lines=200)
        sent = dict(calls)["vss_orchestrator__docker_status"]["tail_lines"]
        self.assertEqual(sent, 20)
