# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The evaluated Claude endpoint must not replace the verifier endpoint."""

import asyncio
import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("harbor")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.nv_claude_code import NvClaudeCode  # noqa: E402
from harbor.agents.installed.claude_code import ClaudeCode  # noqa: E402


@pytest.mark.parametrize("fail", [False, True])
def test_agent_route_is_scoped_to_run(monkeypatch, fail):
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
