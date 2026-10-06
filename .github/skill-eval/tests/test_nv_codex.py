# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Interrupted Codex launches cannot reuse provider config or sessions."""

import asyncio
import importlib.util
from pathlib import Path, PurePosixPath
import tomllib
import types
from unittest.mock import patch

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "agents/nv_codex.py"


def test_cancelled_launch_leaves_no_config_for_next_invocation(tmp_path):
    homes = []
    secrets = []

    class Codex:
        async def run(self, instruction, environment, context):
            home = tmp_path / self._REMOTE_CODEX_HOME.relative_to("/tmp")
            secret = tmp_path / self._REMOTE_CODEX_SECRETS_DIR.relative_to("/tmp")
            home.mkdir(parents=True, exist_ok=True)
            secret.mkdir(parents=True, exist_ok=True)
            homes.append(home)
            secrets.append(secret)
            # Reproduce Harbor's append, leaving both files after interruption.
            with (home / "config.toml").open("a") as handle:
                handle.write('openai_base_url = "http://current-provider/v1"\n')
            (secret / "auth.json").write_text("current credential")
            assert tomllib.loads((home / "config.toml").read_text())["openai_base_url"] == "http://current-provider/v1"
            if len(homes) == 1:
                (home / "sessions").mkdir()
                (home / "sessions/old.jsonl").write_text("old deployment evidence")
                raise asyncio.CancelledError()
            assert not (home / "sessions").exists()

    stub = types.ModuleType("harbor.agents.installed.codex")
    stub.Codex = Codex
    with patch.dict("sys.modules", {"harbor.agents.installed.codex": stub}):
        spec = importlib.util.spec_from_file_location("nv_codex_isolation_test", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    agent = module.NvCodex()
    agent.model_name = "azure/openai/gpt-6-astra"
    assert agent.model_name.split("/")[-1] == "azure/openai/gpt-6-astra"
    assert agent.model_name.split("/", 1) == ["azure", "openai/gpt-6-astra"]
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(agent.run("first", None, None))
    asyncio.run(agent.run("retry", None, None))
    assert homes[0] != homes[1]
    assert secrets[0] != secrets[1]
    assert homes[1].parent == secrets[1].parent
    assert isinstance(agent._REMOTE_CODEX_HOME, PurePosixPath)
