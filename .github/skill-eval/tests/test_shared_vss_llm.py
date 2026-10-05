# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Checks for the opt-in VSS-owned operational LLM route."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import model_config
import run_leg
import shared_vss_llm

SHARED_ENV = {
    "ANTHROPIC_API_KEY": "hosted-key",
    "CODEX_MODEL": "azure/openai/gpt-6-astra",
    "NGC_CLI_API_KEY": "ngc-key",
    "SKILLS_EVAL_CODING_HARNESS": "codex",
    "SKILLS_EVAL_CODING_DEPLOYMENT": "hosted-nvidia-inference",
    "SKILLS_EVAL_OPERATIONAL_HARNESS": "nemoclaw",
    "SKILLS_EVAL_OPERATIONAL_DEPLOYMENT": "local-nim",
    "SKILLS_EVAL_OPERATIONAL_MODEL": "nvidia/nemotron-3.5-lightning-30b-a3b",
    "SKILLS_EVAL_SHARE_LOCAL_LLM_WITH_VSS": "true",
    "EVAL_SPEC_PATH": "skills/operations/vss-ask-video/evals/base_profile_video_understanding.json",
}


def test_shared_route_requires_compatible_spec_and_hosted_coding() -> None:
    routes = model_config.resolve_model_routes(SHARED_ENV)
    assert routes.operational.model == "nvidia/nemotron-3.5-lightning-30b-a3b"

    with pytest.raises(ValueError, match="does not support shared local"):
        model_config.resolve_model_routes({
            **SHARED_ENV,
            "EVAL_SPEC_PATH": "skills/operations/vss-search-archive/evals/search.json",
        })
    with pytest.raises(ValueError, match="requires hosted coding"):
        model_config.resolve_model_routes({
            **SHARED_ENV,
            "SKILLS_EVAL_CODING_DEPLOYMENT": "local-nim",
            "SKILLS_EVAL_CODING_MODEL": "nvidia/nemotron-3.5-lightning-30b-a3b",
        })


def test_shared_route_skips_eval_owned_nim(monkeypatch, tmp_path: Path) -> None:
    routes = model_config.resolve_model_routes(SHARED_ENV)
    monkeypatch.setenv("SKILLS_EVAL_SHARE_LOCAL_LLM_WITH_VSS", "true")
    with mock.patch.object(run_leg, "_run_invocations", return_value=0) as run, \
         mock.patch.object(run_leg, "cleanup_local_nims") as cleanup:
        rc = run_leg.run_invocations(
            [], "Spark-ba-WiFi", tmp_path / "results", tmp_path / "scratch",
            "base_profile_video_understanding", "DGX-SPARK", 100, routes,
        )
    assert rc == 0
    run.assert_called_once()
    cleanup.assert_not_called()


def test_shared_setup_changes_only_llm_placement(tmp_path: Path) -> None:
    task = tmp_path / "dataset" / "step-1"
    task.mkdir(parents=True)
    (task / "instruction.md").write_text(
        "Use the configured remote model endpoints and run autonomously."
    )
    invocation = run_leg.HarborInvocation(
        harbor_root=tmp_path / "dataset", include_task_name="step-1",
        chain_key="base", step_index=1, step_count=2,
    )
    run_leg.prepare_nemoclaw_setup_task(
        invocation, "vss-ask-video", "nvidia/nemotron-3.5-lightning-30b-a3b"
    )
    instruction = (task / "instruction.md").read_text()
    assert "configured VLM endpoint and a local VSS LLM" in instruction
    assert "LLM_MODE=local_shared" in instruction
    assert "shared_vss_llm.py" in instruction
    assert "do not start a" in instruction


def test_shared_route_uses_deployed_vss_port_and_model(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "override.env").write_text("LLM_MODE=local_shared\n")
    resolved = tmp_path / "resolved.yml"
    resolved.write_text("services: {}\n")
    model = "nvidia/nemotron-3.5-lightning-30b-a3b"
    config = {"services": {"llm": {
        "container_name": "vss-llm-nim",
        "environment": {"NIM_SERVED_MODEL_NAME": model},
        "ports": [{"target": 8000, "published": "30081"}],
    }}}
    monkeypatch.setattr(shared_vss_llm, "_compose_config", lambda _: config)
    monkeypatch.setattr(
        shared_vss_llm.subprocess, "run",
        lambda *args, **kwargs: mock.Mock(stdout="true\n"),
    )

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def read(self):
            return json.dumps({"data": [{"id": model}]}).encode()

    monkeypatch.setattr(shared_vss_llm, "urlopen", lambda *args, **kwargs: Response())
    container, served, endpoint = shared_vss_llm.resolve_route(resolved, model)
    assert (container, served, endpoint) == (
        "vss-llm-nim", model, "http://host.openshell.internal:30081/v1"
    )


def test_shared_route_rejects_remote_vss_llm(tmp_path: Path) -> None:
    (tmp_path / "override.env").write_text("LLM_MODE=remote\n")
    resolved = tmp_path / "resolved.yml"
    resolved.write_text("services: {}\n")
    with pytest.raises(ValueError, match="must be local"):
        shared_vss_llm.resolve_route(resolved, "nvidia/nemotron-3.5-lightning-30b-a3b")
