# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regression tests for trajectory-inspection guidance used by the LLM judge."""

import importlib.util
import json
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
GENERIC_JUDGE = REPO_ROOT / ".github/skill-eval/verifiers/generic_judge.py"
NORMALIZED_CALLS_FILTER = """
[.steps[]
 | select(.source == "agent")
 | (.tool_calls // [])[]
 | select(.function_name == "Bash" or .function_name == "exec")
 | select((.arguments.command // "") | contains($url))
 | {tool_call_id, function_name, command: .arguments.command}]
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
    assert '.function_name=="Bash" or .function_name=="exec"' in prompt
    assert "grep -oF 'POST <URL>'" not in prompt


def test_judge_retains_legacy_encoded_message_guidance() -> None:
    """Keep extraction guidance for trajectories using encoded messages."""
    prompt = _load_generic_judge()._JUDGE_SYSTEM_PROMPT

    assert "Older trajectories may instead store" in prompt
    assert ".message | fromjson?" in prompt
    assert "Show legacy Bash commands" in prompt
    assert "Get legacy final assistant text" in prompt


def test_judge_understands_rtsp_exact_match_redaction_marker() -> None:
    """Retain equality evidence while keeping the configured RTSP URL secret."""
    prompt = _load_generic_judge()._JUDGE_SYSTEM_PROMPT

    assert "<redacted:RTSP_SAMPLE_URL;match=exact-runtime-value>" in prompt
    assert "complete `liveStreamUrl` field value" in prompt
    assert "secret itself must not be recovered or printed" in prompt


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
        {
            "tool_call_id": "toolu_1",
            "function_name": "Bash",
            "command": command,
        }
    ]


def test_normalized_recipe_reads_openclaw_exec_calls(tmp_path: Path) -> None:
    """Treat OpenClaw's native exec tool as a canonical shell call."""
    command = 'curl -X POST "http://localhost:38111/v1/summarize"'
    trajectory = {
        "steps": [
            {
                "source": "agent",
                "tool_calls": [
                    {
                        "tool_call_id": "openclaw-1",
                        "function_name": "exec",
                        "arguments": {"command": command},
                    }
                ],
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

    assert json.loads(result.stdout) == [
        {
            "tool_call_id": "openclaw-1",
            "function_name": "exec",
            "command": command,
        }
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
