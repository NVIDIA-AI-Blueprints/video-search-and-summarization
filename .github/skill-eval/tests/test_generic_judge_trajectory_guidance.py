# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regression tests for trajectory-inspection guidance used by the LLM judge."""

import asyncio
import importlib.util
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


REPO_ROOT = Path(__file__).resolve().parents[3]
GENERIC_JUDGE = REPO_ROOT / ".github/skill-eval/verifiers/generic_judge.py"
NORMALIZED_CALLS_FILTER = """
[.steps[]
 | select(.source == "agent")
 | (.tool_calls // [])[]
 | select(.function_name == "Bash" or .function_name == "exec_command")
 | {tool_call_id, command: (.arguments.command // .arguments.cmd // "")}
 | select(.command | contains($url))]
| unique_by(.tool_call_id)
"""
LEGACY_COMMANDS_FILTER = """
.steps[].message
| fromjson?
| .message.content[]?
| select(.type == "tool_use" and .name == "Bash")
| .input.command // empty
"""


def _load_generic_judge():
    """Load the generic judge module directly from its repository path."""
    spec = importlib.util.spec_from_file_location("generic_judge", GENERIC_JUDGE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_judge_counts_normalized_tool_calls_without_metadata_duplicates() -> None:
    """Require guidance that counts canonical calls instead of metadata copies."""
    prompt = _load_generic_judge()._JUDGE_SYSTEM_PROMPT

    assert "steps[].tool_calls" in prompt
    assert "unique_by(.tool_call_id)" in prompt
    assert "never count this copy" in prompt
    assert "never count raw string occurrences" in prompt
    assert "grep -oF 'POST <URL>'" not in prompt


def test_judge_retains_legacy_encoded_message_guidance() -> None:
    """Keep extraction guidance for trajectories using encoded messages."""
    prompt = _load_generic_judge()._JUDGE_SYSTEM_PROMPT

    assert "Older trajectories may instead store" in prompt
    assert ".message | fromjson?" in prompt
    assert "Show legacy Bash commands" in prompt
    assert "Get legacy final assistant text" in prompt


def test_selected_step_query_reaches_every_judge(tmp_path: Path) -> None:
    judge = _load_generic_judge()
    query = "Where did the worker put the box down in the first 10 seconds?"
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"expects": [
        {"query": "Deploy an alerts profile", "checks": ["Deployment is ready"]},
        {"query": query, "checks": ["Exactly one VLM call", "It does not invent scope"]},
    ]}))
    dispatch = AsyncMock(return_value={"pass": True})
    with patch.object(judge, "_judge_llm_agent", dispatch), patch.object(
        judge, "locate_trajectory", return_value=None,
    ), patch.object(sys, "argv", [
        "generic_judge.py", "--spec", str(spec), "--step", "2",
        "--reward-file", str(tmp_path / "reward.txt"),
        "--details-file", str(tmp_path / "judge.json"),
    ]):
        assert judge.main() == 0
    assert len(dispatch.await_args_list) == 2
    for call in dispatch.await_args_list:
        assert call.kwargs["query"] == query
        assert call.kwargs["step"] == 2


def test_normalized_recipe_ignores_duplicated_raw_arguments(tmp_path: Path) -> None:
    """Verify the normalized jq recipe deduplicates repeated raw arguments."""
    command = 'curl -X POST "http://localhost:38111/v1/summarize"'
    trajectory = {
        "steps": [
            {
                "source": "agent",
                "tool_calls": [
                    {
                        "tool_call_id": "toolu_1",
                        "function_name": "Bash",
                        "arguments": {"command": command},
                        "extra": {"raw_arguments": {"command": command}},
                    }
                ],
            },
            {
                "source": "agent",
                "tool_calls": [{
                    "tool_call_id": "call_codex",
                    "function_name": "exec_command",
                    "arguments": {"cmd": command},
                }],
            },
            {
                "source": "user",
                "tool_calls": [{
                    "tool_call_id": "not_an_agent_call",
                    "function_name": "exec_command",
                    "arguments": {"cmd": command},
                }],
            }
        ]
    }
    path = tmp_path / "trajectory.json"
    path.write_text(json.dumps(trajectory))

    result = subprocess.run(
        [
            "jq",
            "--arg",
            "url",
            "http://localhost:38111/v1/summarize",
            NORMALIZED_CALLS_FILTER,
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    calls = json.loads(result.stdout)
    assert calls == [
        {"tool_call_id": "call_codex", "command": command},
        {"tool_call_id": "toolu_1", "command": command},
    ]


def test_legacy_recipe_reads_encoded_message(tmp_path: Path) -> None:
    """Verify the legacy jq recipe extracts an encoded Bash tool call."""
    command = "curl -X POST http://localhost:38111/v1/summarize"
    encoded_message = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Bash",
                        "input": {"command": command},
                    }
                ]
            },
        }
    )
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps({"steps": [{"source": "agent", "message": encoded_message}]}))

    result = subprocess.run(
        ["jq", "-r", LEGACY_COMMANDS_FILTER, str(path)],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == command


class JudgeVerdictRecovery(unittest.IsolatedAsyncioTestCase):
    async def test_only_explicit_turn_budget_errors_get_one_verdict_nudge(self):
        class TextBlock:
            def __init__(self, text):
                self.text = text

        class AssistantMessage:
            def __init__(self, text):
                self.content = [TextBlock(text)]

        class ResultMessage:
            def __init__(self, *, is_error=False, subtype="success", cost=1):
                self.is_error = is_error
                self.subtype = subtype
                self.total_cost_usd = cost

        for subtype, recovery, expected_pass, stalls in (
            ("success", True, True, False),
            ("error_max_turns", True, True, False),
            ("error_during_execution", False, False, False),
            ("error_max_turns", True, False, False),
            ("error_max_turns", True, False, True),
        ):
            with self.subTest(subtype=subtype, expected_pass=expected_pass, stalls=stalls):
                queries = []

                class Client:
                    def __init__(self, **kwargs):
                        pass

                    async def __aenter__(self):
                        return self

                    async def __aexit__(self, *args):
                        pass

                    async def query(self, prompt):
                        queries.append(prompt)
                        if len(queries) == 2 and stalls:
                            await asyncio.Event().wait()

                    async def receive_response(self):
                        if len(queries) == 1:
                            yield AssistantMessage("Observed authenticated health and a configured VSS route.")
                            yield ResultMessage(is_error=subtype != "success", subtype=subtype)
                        else:
                            yield AssistantMessage(
                                '{"pass":true,"matched":"health ok","rationale":"verified"}'
                                if expected_pass else "No verdict."
                            )
                            yield ResultMessage(cost=2)

                sdk = SimpleNamespace(
                    AssistantMessage=AssistantMessage, TextBlock=TextBlock,
                    ResultMessage=ResultMessage, ClaudeSDKClient=Client,
                    ClaudeAgentOptions=lambda **kwargs: SimpleNamespace(**kwargs),
                )
                with patch.dict(sys.modules, {"claude_agent_sdk": sdk}), patch.dict(
                    os.environ, {"ANTHROPIC_API_KEY": "test-placeholder"},
                ):
                    judge = _load_generic_judge()
                    result = await judge._judge_llm_agent(
                        "Gateway is ready", None, timeout_s=0.02,
                        query="Use the existing warehouse deployment", step=11,
                    )
                self.assertEqual(result["pass"], expected_pass)
                self.assertEqual(len(queries), 2 if recovery else 1)
                self.assertEqual(result["cost_usd"], 2 if recovery and not stalls else 1)
                self.assertIn('"query": "Use the existing warehouse deployment"', queries[0])
                self.assertIn('"step": 11', queries[0])
                self.assertIn("report that mismatch and fail", queries[0])
                if recovery:
                    self.assertIn("Do not call any tools", queries[1])
                if stalls:
                    self.assertIn("timed out", result["rationale"])


def test_normalized_jsonl_recipe_reads_canonical_call(tmp_path: Path) -> None:
    """Verify JSONL detection and extraction for a canonical Bash call."""
    command = "curl -X POST http://localhost:38111/v1/summarize"
    step = {
        "source": "agent",
        "tool_calls": [
            {
                "tool_call_id": "toolu_jsonl",
                "function_name": "Bash",
                "arguments": {"command": command},
            }
        ],
    }
    path = tmp_path / "trajectory.jsonl"
    path.write_text(json.dumps(step) + "\n")
    detector = 'any(.[]; (.tool_calls? | type) == "array")'
    jsonl_filter = """
    select(.source == "agent")
    | (.tool_calls // [])[]
    | select(.function_name == "Bash")
    | .arguments.command // empty
    """

    detection = subprocess.run(
        ["jq", "-s", detector, str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    result = subprocess.run(
        ["jq", "-r", jsonl_filter, str(path)],
        check=True,
        capture_output=True,
        text=True,
    )

    assert detection.stdout.strip() == "true"
    assert result.stdout.strip() == command
