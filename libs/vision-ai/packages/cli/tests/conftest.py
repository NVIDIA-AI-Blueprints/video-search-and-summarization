# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Shared fixtures: an isolated VISION_AI_HOME and a mock VLM endpoint."""

from __future__ import annotations

from collections.abc import Callable
import json
from pathlib import Path
from typing import Any

from click.testing import CliRunner
from click.testing import Result
import httpx
import pytest

from nvidia_vision_ai.vlm import client as client_mod
from nvidia_vision_ai_cli.app import cli

Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name in list(__import__("os").environ):
        if name.startswith("VISION_AI_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("VISION_AI_HOME", str(tmp_path / "vision-ai"))
    return tmp_path / "vision-ai"


@pytest.fixture
def vlm_server(monkeypatch: pytest.MonkeyPatch) -> Callable[[Handler], list[httpx.Request]]:
    def install(handler: Handler) -> list[httpx.Request]:
        seen: list[httpx.Request] = []

        def record(request: httpx.Request) -> httpx.Response:
            request.read()
            seen.append(request)
            return handler(request)

        monkeypatch.setattr(
            client_mod, "_new_client", lambda timeout: httpx.AsyncClient(transport=httpx.MockTransport(record))
        )
        return seen

    return install


def run(*args: str, stdin: str | None = None) -> tuple[Result, dict[str, Any]]:
    """Invoke `vision-ai ...`; stdout must be exactly one JSON object, on success or failure."""
    result = CliRunner().invoke(cli, list(args), input=stdin)
    assert result.exception is None or isinstance(result.exception, SystemExit), result.exception
    lines = result.stdout.splitlines()
    assert len(lines) == 1, f"expected one JSON line on stdout, got {result.stdout!r}"
    payload = json.loads(lines[0])
    assert isinstance(payload, dict)
    return result, payload


def completion(answer: str = "a person walks") -> dict[str, Any]:
    return {"id": "cmpl-1", "model": "served-m", "choices": [{"message": {"content": answer}}]}
