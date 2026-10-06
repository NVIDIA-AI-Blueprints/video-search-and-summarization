# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Run the pinned Harbor config writer against isolated trial state."""

import asyncio
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tomllib

import pytest

pytest.importorskip("harbor.agents.installed.codex")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agents.nv_codex import NvCodex
from harbor.agents.installed.codex import Codex
from harbor.environments.base import ExecResult
from harbor.models.agent.context import AgentContext


@pytest.mark.parametrize("prior_cancelled", [False, True])
def test_codex_config_is_fresh_after_prior_trial(monkeypatch, tmp_path, prior_cancelled):
    stale = tmp_path / "shared-home"
    stale.mkdir()
    (stale / "config.toml").write_text('openai_base_url = "https://old.example/v1"\n')
    monkeypatch.setattr(Codex, "_REMOTE_CODEX_HOME", PurePosixPath(stale))
    monkeypatch.setenv("OPENAI_BASE_URL", "https://current.example/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "non-secret-test-placeholder")
    agents = [NvCodex(logs_dir=tmp_path / f"logs-{n}", model_name="azure/openai/gpt-6.1-sol") for n in (1, 2)]
    assert agents[0]._REMOTE_CODEX_HOME != agents[1]._REMOTE_CODEX_HOME
    assert agents[0]._REMOTE_CODEX_SECRETS_DIR != agents[1]._REMOTE_CODEX_SECRETS_DIR
    requests = []
    for n, agent in enumerate(agents):
        # Keep all test files in pytest's scratch while retaining the unique
        # instance scope allocated by the adapter.
        scope = agent._REMOTE_CODEX_HOME.parent.name
        agent._REMOTE_CODEX_HOME = PurePosixPath(tmp_path / scope / "home")
        agent._REMOTE_CODEX_SECRETS_DIR = PurePosixPath(tmp_path / scope / "secrets")

        async def execute(self, environment, command, env):
            home = Path(env["CODEX_HOME"])
            home.mkdir(parents=True, exist_ok=True)
            Path(self._REMOTE_CODEX_SECRETS_DIR).mkdir(parents=True, exist_ok=True)
            if 'cat >>"$CODEX_HOME/config.toml"' in command:
                # Execute Harbor's actual TOML append, not a replacement writer.
                block = command[command.index('cat >>"$CODEX_HOME/config.toml"'):]
                block = block.split("\nTOML", 1)[0] + "\nTOML\n"
                subprocess.run(["bash", "-c", block], env={**os.environ, **env}, check=True)
            if "codex exec" in command:
                with (home / "config.toml").open("rb") as handle:
                    config = tomllib.load(handle)
                assert config["openai_base_url"] == "https://current.example/v1"
                assert "--model azure/openai/gpt-6.1-sol" in command
                requests.append(home)
                if n == 0 and prior_cancelled:
                    raise RuntimeError("simulated cancellation")
            return ExecResult(return_code=0)

        monkeypatch.setattr(NvCodex, "exec_as_agent", execute)
        if n == 0 and prior_cancelled:
            with pytest.raises(RuntimeError, match="simulated cancellation"):
                asyncio.run(agent.run("test", None, AgentContext()))
        else:
            asyncio.run(agent.run("test", None, AgentContext()))
    assert len(requests) == 2
    assert requests[0] != requests[1]
    assert (stale / "config.toml").read_text() == 'openai_base_url = "https://old.example/v1"\n'
