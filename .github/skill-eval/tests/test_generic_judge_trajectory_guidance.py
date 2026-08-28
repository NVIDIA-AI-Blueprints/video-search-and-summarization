# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regression tests for trajectory-inspection guidance used by the LLM judge."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import types


REPO_ROOT = Path(__file__).resolve().parents[3]
GENERIC_JUDGE = REPO_ROOT / ".github/skill-eval/verifiers/generic_judge.py"
NORMALIZED_CALLS_FILTER = """
[.steps[]
 | select(.source == "agent")
 | (.tool_calls // [])[]
 | select(.function_name == "Bash")
 | select((.arguments.command // "") | contains($url))
 | {tool_call_id, command: .arguments.command}]
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
    assert calls == [{"tool_call_id": "toolu_1", "command": command}]


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


def _fake_sdk() -> types.ModuleType:
    module = types.ModuleType("claude_agent_sdk")
    for name in (
        "AssistantMessage",
        "ClaudeAgentOptions",
        "ClaudeSDKClient",
        "ResultMessage",
        "TextBlock",
    ):
        setattr(module, name, object)
    return module


def test_sdk_import_skips_install_when_already_available(monkeypatch) -> None:
    """An installed SDK must not trigger a pip call on every check."""
    judge = _load_generic_judge()
    sdk = _fake_sdk()
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk)
    installs: list[list[str]] = []
    monkeypatch.setattr(
        judge,
        "subprocess",
        types.SimpleNamespace(run=lambda cmd, **kw: installs.append(cmd)),
    )

    assert judge._import_agent_sdk() is sdk
    assert installs == []


def test_sdk_import_retries_past_externally_managed_interpreter(monkeypatch) -> None:
    """PEP 668 nodes (Ubuntu 24.04+, DGX Spark) need the override retry.

    Without it the plain install exits non-zero, the re-import fails, and
    every check reports ModuleNotFoundError instead of a verdict.
    """
    judge = _load_generic_judge()
    sdk = _fake_sdk()
    monkeypatch.delitem(sys.modules, "claude_agent_sdk", raising=False)
    attempts: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        attempts.append(cmd)
        if "--break-system-packages" in cmd:
            sys.modules["claude_agent_sdk"] = sdk

    monkeypatch.setattr(judge, "subprocess", types.SimpleNamespace(run=fake_run))
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk)
    monkeypatch.delitem(sys.modules, "claude_agent_sdk")

    assert judge._import_agent_sdk() is sdk
    assert len(attempts) == 2
    assert "--break-system-packages" not in attempts[0]
    assert "--break-system-packages" in attempts[1]


def test_agent_cli_path_repair_only_when_unresolvable(tmp_path, monkeypatch) -> None:
    """The judge SDK spawns `claude`; a fresh node hides it in ~/.local/bin."""
    judge = _load_generic_judge()
    local_bin = tmp_path / ".local" / "bin"
    local_bin.mkdir(parents=True)
    (local_bin / "claude").write_text("#!/bin/sh\n")
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setattr(judge.Path, "home", staticmethod(lambda: tmp_path))

    monkeypatch.setattr(judge, "shutil", types.SimpleNamespace(which=lambda _: None))
    judge._ensure_agent_cli_on_path()
    assert str(local_bin) in os.environ["PATH"]

    # A verifier running as root still has to find the trial user's CLI.
    trial_home_root = tmp_path / "home"
    trial_bin = trial_home_root / "ubuntu" / ".local" / "bin"
    trial_bin.mkdir(parents=True)
    (trial_bin / "claude").write_text("#!/bin/sh\n")
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setattr(judge.Path, "home", staticmethod(lambda: tmp_path / "root"))
    monkeypatch.setattr(judge, "_TRIAL_HOME_ROOT", trial_home_root)
    judge._ensure_agent_cli_on_path()
    assert str(trial_bin) in os.environ["PATH"]

    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setattr(
        judge, "shutil", types.SimpleNamespace(which=lambda _: "/usr/local/bin/claude")
    )
    judge._ensure_agent_cli_on_path()
    assert os.environ["PATH"] == "/usr/bin"


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
