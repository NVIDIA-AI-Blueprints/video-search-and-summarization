# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The cleanup phase must preserve grading, ownership and the live coding route."""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import leg_report
import run_leg
import teardown

ROOT = Path(__file__).resolve().parents[3]
SPECS = [
    p
    for p in sorted((ROOT / "skills/operations").glob("*/evals/*.json"))
    if isinstance(json.loads(p.read_text()), dict)
    and "resources" in json.loads(p.read_text())
]


@pytest.mark.parametrize("spec_path", SPECS, ids=lambda p: p.stem)
def test_every_operational_adapter_supports_the_spec_owned_lifecycle(
    spec_path, tmp_path
):
    spec = json.loads(spec_path.read_text())
    config = teardown.load_teardown(spec_path)
    assert config is not None
    platform = next(p for p in spec["resources"]["platforms"] if p != "DGX-SPARK")
    dataset = tmp_path / "dataset"
    skill = spec_path.parent.parent
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / f".github/skill-eval/adapters/{skill.name}/generate.py"),
            "--skill-dir",
            str(skill),
            "--deploy-skill-dir",
            str(ROOT / "skills/vss-build-vision-ai"),
            "--spec",
            str(spec_path),
            "--platform",
            platform,
            "--output-dir",
            str(dataset),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    invocations = run_leg.discover_invocations(dataset)
    setup = invocations[0].harbor_root / invocations[0].include_task_name
    instruction = (setup / "instruction.md").read_text()
    assert (
        "SKILL_EVAL_BUILD_DIR" in instruction and "COMPOSE_PROJECT_NAME" in instruction
    )
    output = teardown.generate_task(config, setup, tmp_path / "teardown", ROOT)
    task = tomllib.loads((output / "task.toml").read_text())
    assert task["metadata"]["phase"] == "teardown"
    assert (
        task["metadata"]["gpu_type"]
        == tomllib.loads((setup / "task.toml").read_text())["metadata"]["gpu_type"]
    )
    assert (
        output / "skills/vss-build-vision-ai/references/teardown.md"
    ).read_text() == (
        ROOT / "skills/vss-build-vision-ai/references/teardown.md"
    ).read_text()
    assert config["query"] in (output / "instruction.md").read_text()
    assert config["checks"][0] not in (output / "instruction.md").read_text()
    assert (
        len(invocations) % len(spec["expects"]) == 0
    )  # teardown is outside the operational chain


@pytest.fixture
def lifecycle(tmp_path, monkeypatch):
    config = teardown.load_teardown(SPECS[0])
    env = {
        "ANTHROPIC_API_KEY": "test-key",  # pragma: allowlist secret (synthetic fixture)
        "ANTHROPIC_BASE_URL": "https://inference.example/v1",
        "ANTHROPIC_MODEL": "judge/model",
        "SKILLS_EVAL_CODING_HARNESS": "codex",
        "SKILLS_EVAL_CODING_MODEL": "coding/model",
        "SKILLS_EVAL_OPERATIONAL_MODEL": "operational/model",
        "SKILLS_EVAL_OPERATIONAL_HARNESS": "nemoclaw",
        "EVAL_SPEC_PATH": "skills/operations/test/evals/test.json",
        "GITHUB_RUN_ID": "test-run",
    }
    invocations = []
    for i in (1, 2):
        task = tmp_path / "dataset" / f"step-{i}"
        task.mkdir(parents=True)
        (task / "task.toml").write_text(
            '[metadata]\ngpu_type = "L40S"\ngpu_count = 1\n'
        )
        invocations.append(
            run_leg.HarborInvocation(task.parent, task.name, "chain", i, 2)
        )
    monkeypatch.setattr(teardown, "load_teardown", lambda _path: config)
    monkeypatch.setattr(run_leg, "latest_reward", lambda *_a: "1.0")
    monkeypatch.setattr(run_leg, "latest_trial_exception", lambda *_a: None)
    monkeypatch.setattr(
        run_leg, "publish_trace", lambda *_a: "https://harbor.example/teardown"
    )
    monkeypatch.setattr(run_leg, "cleanup_deferred_agent_run", lambda *_a: None)
    with mock.patch.dict(run_leg.os.environ, env, clear=True):
        yield invocations, tmp_path / "results", tmp_path / "scratch", config


def invoke(lifecycle, **kwargs):
    tasks, results, scratch, _config = lifecycle
    return run_leg.run_invocations(
        tasks,
        "vss-eval-box",
        results,
        scratch,
        "test",
        "L40S",
        run_leg.DEFAULT_HARBOR_TIMEOUT_SEC,
        run_leg.resolve_model_routes(run_leg.os.environ),
        **kwargs,
    )


@pytest.mark.parametrize("coding", ["codex", "claude-code"])
@pytest.mark.parametrize("operational", ["nemoclaw", "codex", "claude-code"])
def test_cleanup_uses_host_coding_route_after_operations(
    lifecycle, monkeypatch, coding, operational
):
    monkeypatch.setenv("SKILLS_EVAL_CODING_HARNESS", coding)
    monkeypatch.setenv("SKILLS_EVAL_OPERATIONAL_HARNESS", operational)
    events = []

    def operations(*args, **kwargs):
        events.append("operations-and-grading")
        owned = kwargs["lifecycle_env"]
        assert owned["COMPOSE_PROJECT_NAME"].startswith("skill-eval-")
        assert (
            owned["SKILL_EVAL_BUILD_DIR"] == "_builds/" + owned["COMPOSE_PROJECT_NAME"]
        )
        owned.update(run_leg.harbor_env("vss-eval-box"))
        owned[run_leg.DEFER_AGENT_REAP_ENV] = "1"
        return 0

    def cleanup(command, env, timeout):
        events.append("teardown")
        assert env["SKILL_EVAL_PRESERVE_DEPLOYMENT"] == "1"
        assert run_leg.DEFER_AGENT_REAP_ENV not in env
        assert env[run_leg.AGENT_ROUTE_API_KEY_ENV] == "test-key"  # pragma: allowlist secret (synthetic fixture)
        assert "coding/model" in command and "operational/model" not in command
        assert "agents.nemoclaw:NemoClaw" not in command
        assert command[command.index("--agent-timeout-multiplier") + 1] == "1.0"
        assert timeout < run_leg.DEFAULT_HARBOR_TIMEOUT_SEC
        return 0

    monkeypatch.setattr(run_leg, "_run_invocations", operations)
    monkeypatch.setattr(run_leg, "run_command", cleanup)
    assert invoke(lifecycle) == 0
    assert events == ["operations-and-grading", "teardown"]
    record = json.loads((lifecycle[1] / "teardown.json").read_text())[0]
    assert record["state"] == "passed" and record["operational_return_code"] == 0
    assert "test-key" not in (lifecycle[1] / "teardown.json").read_text()


@pytest.mark.parametrize("primary_rc", [1, 124])
def test_failed_or_timed_out_operations_still_run_teardown(
    lifecycle, monkeypatch, primary_rc
):
    monkeypatch.setattr(run_leg, "_run_invocations", lambda *_a, **_k: primary_rc)
    with mock.patch.object(run_leg, "run_command", return_value=0) as cleanup:
        assert invoke(lifecycle) == primary_rc
    cleanup.assert_called_once()
    assert (
        json.loads((lifecycle[1] / "teardown.json").read_text())[0]["state"] == "passed"
    )


@pytest.mark.parametrize("primary_rc", [0, 124])
def test_teardown_failure_is_separate_and_cannot_erase_primary_failure(
    lifecycle, monkeypatch, primary_rc
):
    monkeypatch.setattr(run_leg, "_run_invocations", lambda *_a, **_k: primary_rc)
    monkeypatch.setattr(run_leg, "run_command", lambda *_a: 1)
    assert invoke(lifecycle) == (primary_rc or 1)
    record = json.loads((lifecycle[1] / "teardown.json").read_text())[0]
    assert (
        record["state"] == "failed" and record["operational_return_code"] == primary_rc
    )


def test_passing_reward_cannot_hide_teardown_agent_timeout(lifecycle, monkeypatch):
    monkeypatch.setattr(run_leg, "_run_invocations", lambda *_a, **_k: 0)
    monkeypatch.setattr(run_leg, "run_command", lambda *_a: 0)
    monkeypatch.setattr(
        run_leg, "latest_trial_exception", lambda *_a: "AgentTimeoutError"
    )
    assert invoke(lifecycle) == 1
    assert (
        json.loads((lifecycle[1] / "teardown.json").read_text())[0]["exception"]
        == "AgentTimeoutError"
    )


def test_cancellation_skips_model_work_but_reaps_owned_services(lifecycle, monkeypatch):
    monkeypatch.setattr(run_leg, "_run_invocations", lambda *_a, **_k: 143)
    with (
        mock.patch.object(run_leg, "run_command") as cleanup,
        mock.patch.object(run_leg, "cleanup_deferred_agent_run") as reaper,
    ):
        assert invoke(lifecycle) == 143
    cleanup.assert_not_called()
    reaper.assert_called_once()
    assert (
        json.loads((lifecycle[1] / "teardown.json").read_text())[0]["reason"]
        == "cancelled"
    )


def test_exception_during_setup_still_attempts_skill_teardown(lifecycle, monkeypatch):
    def failed_setup(*_a, **_k):
        raise RuntimeError("setup failed")

    monkeypatch.setattr(run_leg, "_run_invocations", failed_setup)
    with (
        mock.patch.object(run_leg, "run_command", return_value=0) as cleanup,
        pytest.raises(RuntimeError, match="setup failed"),
    ):
        invoke(lifecycle)
    cleanup.assert_called_once()
    assert (
        json.loads((lifecycle[1] / "teardown.json").read_text())[0]["state"] == "passed"
    )


def test_each_chain_cleans_up_before_next_setup(lifecycle, monkeypatch):
    tasks = lifecycle[0]
    tasks.extend(
        [run_leg.dataclasses.replace(t, chain_key="second-chain") for t in tasks[:]]
    )
    events, projects = [], []

    def operations(tasks, *_a, **kwargs):
        events.append("operations:" + tasks[0].chain_key)
        projects.append(kwargs["lifecycle_env"]["COMPOSE_PROJECT_NAME"])
        return 0

    def cleanup(command, _env, _timeout):
        events.append("teardown")
        return 0

    monkeypatch.setattr(run_leg, "_run_invocations", operations)
    monkeypatch.setattr(run_leg, "run_command", cleanup)
    assert invoke(lifecycle) == 0
    assert events == [
        "operations:chain",
        "teardown",
        "operations:second-chain",
        "teardown",
    ]
    assert len(set(projects)) == 2


def test_cancellation_during_teardown_never_starts_another_chain(
    lifecycle, monkeypatch
):
    tasks = lifecycle[0]
    tasks.extend(
        [run_leg.dataclasses.replace(t, chain_key="second-chain") for t in tasks[:]]
    )
    monkeypatch.setattr(run_leg, "run_command", lambda *_a: 143)
    with mock.patch.object(run_leg, "_run_invocations", return_value=0) as operations:
        assert invoke(lifecycle) == 143
    operations.assert_called_once()


def test_report_declares_missing_teardown_without_rewriting_operational_reward(
    tmp_path, monkeypatch
):
    trial = tmp_path / "run/step-1__op"
    trial.mkdir(parents=True)
    (trial / "result.json").write_text(
        json.dumps(
            {
                "trial_name": "step-1__op",
                "verifier_result": {"rewards": {"reward": 1.0}},
            }
        )
    )
    spec = tmp_path / "spec.json"
    spec.write_text(
        json.dumps(
            {"expects": [{"query": "operation"}], "teardown": {"query": "cleanup"}}
        )
    )
    monkeypatch.setattr(leg_report.redact_secrets, "redact_tree", lambda _root: [])
    summary = tmp_path / "summary.json"
    report = tmp_path / "report.md"
    assert (
        leg_report.main(
            [
                "--results-root",
                str(tmp_path),
                "--spec-file",
                str(spec),
                "--summary-json",
                str(summary),
                "--out",
                str(report),
            ]
        )
        == 0
    )
    data = json.loads(summary.read_text())
    assert data["teardown_declared"] and data["teardown"] == []
    assert data["steps"][0]["state"] == "recorded-pass"
    assert "no teardown result was recorded" in report.read_text()


def test_local_nim_is_cleaned_up_only_after_skill_teardown(lifecycle, monkeypatch):
    monkeypatch.setenv("NGC_API_KEY", "test-registry-key")
    monkeypatch.setenv("SKILLS_EVAL_OPERATIONAL_DEPLOYMENT", "local-nim")
    monkeypatch.setenv(
        "SKILLS_EVAL_OPERATIONAL_MODEL", "nvidia/nemotron-3.5-lightning-30b-a3b"
    )
    events = []
    monkeypatch.setattr(
        run_leg, "_run_invocations", lambda *_a, **_k: events.append("operations") or 0
    )

    def cleanup(_command, env, _timeout):
        assert "SKILL_EVAL_LOCAL_NIM_PLAN" not in env
        return events.append("skill-teardown") or 0

    monkeypatch.setattr(run_leg, "run_command", cleanup)
    monkeypatch.setattr(
        run_leg, "cleanup_local_nims", lambda *_a: events.append("eval-nim-cleanup")
    )
    assert invoke(lifecycle) == 0
    assert events == ["operations", "skill-teardown", "eval-nim-cleanup"]


def test_deadline_reserves_teardown_before_starting_a_deployment(
    lifecycle, monkeypatch
):
    monkeypatch.setattr(run_leg.time, "monotonic", lambda: 100)
    with (
        mock.patch.object(run_leg, "_run_invocations") as operations,
        mock.patch.object(run_leg, "run_command") as cleanup,
    ):
        assert invoke(lifecycle, work_deadline=101) == 124
    operations.assert_not_called()
    cleanup.assert_not_called()
    assert (
        json.loads((lifecycle[1] / "teardown.json").read_text())[0]["state"]
        == "not-run"
    )


def test_teardown_results_do_not_replace_operational_steps(lifecycle, monkeypatch):
    monkeypatch.setattr(run_leg, "_run_invocations", lambda *_a, **_k: 0)
    monkeypatch.setattr(run_leg, "run_command", lambda *_a: 0)
    assert invoke(lifecycle) == 0
    results = lifecycle[1]
    for directory, name, reward in [
        (results / "run", "step-1__op", 0.0),
        (results / "teardown/chain/run", "teardown__cleanup", 1.0),
    ]:
        trial = directory / name
        trial.mkdir(parents=True)
        (trial / "result.json").write_text(
            json.dumps(
                {
                    "trial_name": name,
                    "verifier_result": {"rewards": {"reward": reward}},
                }
            )
        )
    leg = leg_report.collect_leg(results)
    assert [t["trial_name"] for t in leg["trials"]] == ["step-1__op"]
    summary = leg_report.leg_summary(leg, declared=["operational query"])
    assert summary["steps"][0]["state"] == "recorded-fail"
    assert summary["teardown"][0]["state"] == "passed"
    body = leg_report.render_comment(
        leg, spec_path="spec.json", platform="L40S", head_sha="12345678"
    )
    assert "Deployment teardown" in body and "teardown trace" in body
