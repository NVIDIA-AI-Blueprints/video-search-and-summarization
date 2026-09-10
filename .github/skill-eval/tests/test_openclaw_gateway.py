# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import json
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[3]
    / "skills"
    / "benchmarking"
    / "benchmark-unified-memory"
    / "scripts"
    / "start_openclaw_gateway.py"
)
SPEC = importlib.util.spec_from_file_location("start_openclaw_gateway", SCRIPT)
assert SPEC and SPEC.loader
gateway = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gateway)


def test_gateway_config_is_judge_only_and_secret_free() -> None:
    config = gateway.build_gateway_config("aws/anthropic/bedrock-claude-opus-4-6")
    encoded = json.dumps(config)

    assert config["models"]["providers"]["anthropic"] == {
        "baseUrl": "${ANTHROPIC_BASE_URL}",
        "apiKey": "${ANTHROPIC_API_KEY}",
    }
    assert config["agents"]["defaults"]["model"]["primary"] == (
        "anthropic/aws/anthropic/bedrock-claude-opus-4-6"
    )
    assert config["gateway"]["auth"] == {"mode": "none"}
    assert config["gateway"]["http"]["endpoints"]["chatCompletions"] == {
        "enabled": True
    }
    assert "memory" not in config
    assert "embedding" not in encoded.lower()
    assert "secret-value" not in encoded


def test_docker_command_uses_pinned_image_and_env_names_only() -> None:
    command = gateway.docker_create_command()

    assert command[command.index("--name") + 1] == "openclaw-gateway"
    assert "--network" in command and "host" in command
    assert "ANTHROPIC_API_KEY" in command
    assert "ANTHROPIC_BASE_URL" in command
    assert all("secret-value" not in item for item in command)
    assert command[-1].startswith("ghcr.io/openclaw/openclaw:2026.7.1-2@sha256:")


def test_vss_introspection_configuration_uses_gateway_as_judge(
    monkeypatch, tmp_path
) -> None:
    commands: list[list[str]] = []

    def fake_run(command, *, check=True):
        commands.append(list(command))

    monkeypatch.setattr(gateway, "run", fake_run)
    gateway.configure_vss(tmp_path)

    configure = commands[0]
    assert configure[-8:] == [
        "memory",
        "introspection",
        "--judge-endpoint",
        "http://127.0.0.1:18789/v1",
        "--judge-model",
        "openclaw/default",
        "--clear-judge-api-key-env",
        "--clear-judge-backend-model",
    ]
    assert not any("embedding" in item for command in commands for item in command)
