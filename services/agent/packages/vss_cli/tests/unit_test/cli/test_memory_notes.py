# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""CLI tests for harness Markdown memory notes."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from typing import Any

from click.testing import CliRunner
import httpx
import pytest

from vss_cli import config as config_mod
from vss_cli import memory_access
from vss_cli.configure import configure
from vss_cli.exits import Exit
from vss_cli.memory_access import GroupScopedMemory
from vss_cli.search_group import SEARCH
from vss_cli.summarize_group import SUMMARIZE
from vss_core.memory.service import MemoryService
from vss_core.memory.store import InMemoryStore
from vss_core.memory.store import JobFilters

if TYPE_CHECKING:
    from pathlib import Path

BASE_URL = "http://h:7777"


def _deployment() -> config_mod.Deployment:
    return config_mod.Deployment(
        base_url=BASE_URL,
        services={
            "agent": config_mod.Service(url=f"{BASE_URL}/api"),
            "elasticsearch": config_mod.Service(url=f"{BASE_URL}/elasticsearch"),
            "rt_embed": config_mod.Service(url=f"{BASE_URL}/rtvi-embed", models=["bge"]),
            "rt_vlm": config_mod.Service(url=f"{BASE_URL}/rtvi-vlm", models=["cosmos-reason"]),
            "rtvi_cv": config_mod.Service(url=f"{BASE_URL}/rtvi-cv"),
        },
    )


@pytest.fixture
def configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> config_mod.Deployment:
    monkeypatch.setenv(config_mod.CONFIG_HOME_ENV, str(tmp_path / "cfg"))
    deployment = _deployment()
    config_mod.save(deployment)
    return deployment


def _install_memory(monkeypatch: pytest.MonkeyPatch) -> InMemoryStore:
    store = InMemoryStore()
    monkeypatch.setattr(memory_access, "_TEST_MEMORY", GroupScopedMemory(MemoryService(store)))
    return store


def _capture_post(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Response:
        status_code = 200
        text = ""

        def json(self) -> dict[str, Any]:
            return {
                "id": "cmpl-1",
                "created": 1,
                "model": "cosmos-reason",
                "choices": [{"message": {"content": "Three delivery vehicles arrived."}}],
            }

    monkeypatch.setattr(httpx, "post", lambda *_args, **_kwargs: _Response())


def _configure_openclaw(workspace: Path, *, default: bool = False) -> None:
    runner = CliRunner()
    args = [
        "memory",
        "--harness",
        "openclaw",
        "--plugin",
        "memory-core",
        "--workspace",
        str(workspace),
        "--enable-memory-notes",
    ]
    if default:
        args.append("--write-memory-notes-default")
    result = runner.invoke(configure, args)
    assert result.exit_code == 0, result.output


def _stdout_json(output: str) -> dict[str, Any]:
    lines = output.strip().splitlines()
    if lines and "vss_job_" in lines[-1]:
        return json.loads("\n".join(lines[:-1]))
    return json.loads(output)


def test_configure_memory_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(config_mod.CONFIG_HOME_ENV, str(tmp_path / "cfg"))
    workspace = tmp_path / "openclaw" / "workspace"
    _configure_openclaw(workspace, default=True)
    memory = config_mod.load_memory_config()
    assert memory.harness_sink.enabled is True
    assert memory.harness_sink.harness == "openclaw"
    assert memory.harness_sink.plugin == "memory-core"
    assert memory.harness_sink.workspace == str(workspace)
    assert memory.harness_sink.note_path_template == "memory/{date}-vss.md"
    show = CliRunner().invoke(configure, ["memory", "show"])
    assert show.exit_code == 0
    payload = json.loads(show.output)
    assert payload["memory"]["harness_sink"]["enabled"] is True


def test_configure_memory_rejects_unsupported_plugin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(config_mod.CONFIG_HOME_ENV, str(tmp_path / "cfg"))
    result = CliRunner().invoke(
        configure,
        [
            "memory",
            "--harness",
            "openclaw",
            "--plugin",
            "memory-wiki",
            "--workspace",
            str(tmp_path),
            "--enable-memory-notes",
        ],
    )
    assert result.exit_code != 0
    assert "unsupported" in result.output.lower() or "unsupported" in ((result.exception and str(result.exception)) or "")


def test_write_memory_note_flag_on_summarize_and_search() -> None:
    summarize_flags = {
        opt for param in SUMMARIZE.cli().commands["run"].params for opt in (*param.opts, *param.secondary_opts)
    }
    search_flags = {
        opt
        for param in SEARCH.cli().commands["run"].commands["fusion"].params
        for opt in (*param.opts, *param.secondary_opts)
    }
    assert "--write-memory-note" in summarize_flags
    assert "--no-write-memory-note" in summarize_flags
    assert "--write-memory-note" in search_flags
    assert "--no-write-memory-note" in search_flags


def test_no_persist_with_write_memory_note_fails_before_backend(
    configured: config_mod.Deployment, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    called = {"post": False}

    def boom(*args: Any, **kwargs: Any) -> Any:
        called["post"] = True
        raise AssertionError("backend must not be called")

    monkeypatch.setattr(httpx, "post", boom)
    _configure_openclaw(tmp_path / "ws")
    result = CliRunner().invoke(SUMMARIZE.cli(), ["run", "--id", "v1", "--no-persist", "--write-memory-note"])
    assert result.exit_code == int(Exit.INVALID_INPUT)
    assert called["post"] is False


def test_explicit_write_memory_note_requires_provider(
    configured: config_mod.Deployment, monkeypatch: pytest.MonkeyPatch
) -> None:
    _capture_post(monkeypatch)
    _install_memory(monkeypatch)
    result = CliRunner().invoke(SUMMARIZE.cli(), ["run", "--id", "v1", "--write-memory-note"])
    assert result.exit_code == int(Exit.CONFIGURATION)


def test_summarize_writes_note_and_keeps_stdout(
    configured: config_mod.Deployment, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = tmp_path / "ws"
    _configure_openclaw(workspace)
    _capture_post(monkeypatch)
    store = _install_memory(monkeypatch)
    result = CliRunner().invoke(SUMMARIZE.cli(), ["run", "--id", "v1", "--write-memory-note"])
    assert result.exit_code == 0, result.output
    body = _stdout_json(result.output)
    assert body["summary"]["id"] == "cmpl-1"
    assert body["harness_memory"]["written"] is True
    assert len(store.list_jobs(JobFilters())) >= 1
    notes = list((workspace / "memory").glob("*-vss.md"))
    assert len(notes) == 1
    text = notes[0].read_text(encoding="utf-8")
    assert "Three delivery vehicles arrived." in text
    assert body["job_id"] in text
    assert "vss memory get" in text
    assert "Embedding" not in text
    assert "vss_job_completed" in result.output


def test_config_default_can_be_overridden_off(
    configured: config_mod.Deployment, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = tmp_path / "ws"
    _configure_openclaw(workspace, default=True)
    _capture_post(monkeypatch)
    _install_memory(monkeypatch)
    result = CliRunner().invoke(SUMMARIZE.cli(), ["run", "--id", "v1", "--no-write-memory-note"])
    assert result.exit_code == 0, result.output
    body = _stdout_json(result.output)
    assert "harness_memory" not in body
    assert not (workspace / "memory").exists() or not list((workspace / "memory").glob("*-vss.md"))
