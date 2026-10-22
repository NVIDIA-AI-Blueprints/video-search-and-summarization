# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""`vision-ai configure` and `vision-ai memory`, plus plugin discovery."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from conftest import completion
from conftest import run
import httpx
import pytest


def test_set_show_unset(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result, out = run("configure", "vlm", "set", "base-url=http://vlm:8000", "fps=2", "api_key=-", stdin="s3cret\n")
    assert result.exit_code == 0, result.output
    path = home / "vlm" / "config.json"
    assert json.loads(path.read_text()) == {
        "base_url": "http://vlm:8000",
        "fps": 2.0,
        "api_key": "s3cret",  # pragma: allowlist secret
    }
    assert path.stat().st_mode & 0o777 == 0o600
    assert home.stat().st_mode & 0o777 == 0o700
    assert out["file"]["api_key"] == "***"

    monkeypatch.setenv("VISION_AI_VLM_TIMEOUT", "90")
    _, shown = run("configure", "vlm", "show")
    assert shown["effective"]["timeout"] == 90
    assert shown["effective"]["api_key"] == "***"
    assert shown["env"] == ["VISION_AI_VLM_TIMEOUT"]
    assert "s3cret" not in json.dumps(shown)

    run("configure", "vlm", "unset", "fps")
    assert "fps" not in json.loads(path.read_text())


@pytest.mark.parametrize(
    ("pair", "match"),
    [
        ("sensor=cam", "no setting"),
        ("fps=-1", "fps"),
        ("backend=nope", "backend"),
        ("fps", "KEY=VALUE"),
        ("base_url=not-a-url", "http(s) URL"),
        ("base_url=localhost:9", "http(s) URL"),
        ("api_key=on-argv", "pipe the value on stdin"),
    ],
)
def test_set_rejects_invalid_values_without_writing(home: Path, pair: str, match: str) -> None:
    result, out = run("configure", "vlm", "set", pair)
    assert result.exit_code == 2
    assert match in out["error"]["message"]
    assert not (home / "vlm" / "config.json").exists()


def test_check_lists_models(home: Path, vlm_server: Any) -> None:
    run("configure", "vlm", "set", "base_url=http://vlm:8000", "model=b")
    vlm_server(lambda r: httpx.Response(200, json={"data": [{"id": "a"}]}))
    result, out = run("configure", "vlm", "check")
    assert result.exit_code == 0
    assert out == {
        "tool": "vlm",
        "ok": True,
        "base_url": "http://vlm:8000",
        "models": ["a"],
        "model": "b",
        "model_listed": False,
    }


def test_check_without_base_url_exits_3(home: Path) -> None:
    result, out = run("configure", "vlm", "check")
    assert result.exit_code == 3
    assert out["error"]["code"] == "configuration"


def test_memory_list_and_get(home: Path, vlm_server: Any) -> None:
    run("configure", "vlm", "set", "base_url=http://vlm:8000", "model=m")
    vlm_server(lambda r: httpx.Response(200, json=completion()))
    ids = [run("vlm", "--prompt", f"q{i}", "--media-url", "https://x/c.mp4")[1]["job_id"] for i in range(3)]

    _, listed = run("memory", "list", "--type", "vlm", "--limit", "2")
    assert [job["job_id"] for job in listed["jobs"]] == ids[:0:-1]
    _, none = run("memory", "list", "--type", "search")
    assert none == {"jobs": []}

    result, got = run("memory", "get", ids[0])
    assert result.exit_code == 0
    assert got["input"]["prompt"] == "q0"

    result, missing = run("memory", "get", "vlm-nope")
    assert result.exit_code == 5
    assert missing["error"]["code"] == "not_found"


def test_root_help_lists_tools_and_builtins(home: Path) -> None:
    from click.testing import CliRunner

    from nvidia_vision_ai_cli.app import cli

    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    for name in ("vlm", "configure", "memory"):
        assert name in result.stdout


def test_disabled_plugin_is_hidden(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VISION_AI_DISABLE_PLUGINS", "vlm")
    result, _ = run("vlm", "--prompt", "q")
    assert result.exit_code == 2
    assert "No such command" in result.output


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions (CI containers run as root)")
def test_unwritable_config_dir_exits_3(home: Path) -> None:
    home.mkdir(mode=0o500, parents=True)
    try:
        result, out = run("configure", "vlm", "set", "fps=2")
    finally:
        home.chmod(0o700)
    assert result.exit_code == 3
    assert out["error"]["code"] == "configuration"


def test_memory_db_is_owner_only(home: Path, vlm_server: Any) -> None:
    run("configure", "vlm", "set", "base_url=http://vlm:8000", "model=m")
    vlm_server(lambda r: httpx.Response(200, json=completion()))
    run("vlm", "--prompt", "q", "--media-url", "https://x/c.mp4")
    assert (home / "memory" / "main.db").stat().st_mode & 0o777 == 0o600
    assert (home / "memory").stat().st_mode & 0o777 == 0o700
