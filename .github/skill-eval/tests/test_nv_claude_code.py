# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The evaluated Claude endpoint must not replace the verifier endpoint."""

import asyncio
import importlib.util
import os
import sys
from pathlib import Path
import types
from unittest.mock import patch

import pytest


def load_agent():
    class ClaudeCode:
        def _get_env(self, key):
            return self._extra_env.get(key)

        async def run(self, instruction, environment, context):
            raise NotImplementedError

    stub = types.ModuleType("harbor.agents.installed.claude_code")
    stub.ClaudeCode = ClaudeCode
    module_path = Path(__file__).resolve().parents[1] / "agents/nv_claude_code.py"
    with patch.dict(sys.modules, {"harbor.agents.installed.claude_code": stub}):
        spec = importlib.util.spec_from_file_location("nv_claude_code_test", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module.NvClaudeCode, ClaudeCode


@pytest.mark.parametrize("fail", [False, True])
def test_agent_route_is_scoped_to_run(monkeypatch, fail):
    NvClaudeCode, ClaudeCode = load_agent()
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://verifier.example/v1")
    seen = []

    async def fake_run(self, instruction, environment, context):
        seen.append(os.environ["ANTHROPIC_BASE_URL"])
        if fail:
            raise RuntimeError("agent failed")

    monkeypatch.setattr(ClaudeCode, "run", fake_run)
    agent = object.__new__(NvClaudeCode)
    agent._extra_env = {"ANTHROPIC_BASE_URL": "http://127.0.0.1:18400"}
    if fail:
        with pytest.raises(RuntimeError, match="agent failed"):
            asyncio.run(agent.run("test", None, None))
    else:
        asyncio.run(agent.run("test", None, None))
    assert seen == ["http://127.0.0.1:18400"]
    assert os.environ["ANTHROPIC_BASE_URL"] == "https://verifier.example/v1"
