# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Static ``vss configure memory`` contract tests."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from typing import Any

from click.testing import CliRunner
import pytest

from vss_cli import config as config_mod
from vss_cli import configure as configure_mod
from vss_cli.exits import Exit

if TYPE_CHECKING:
    from pathlib import Path


def _deployment(*, elasticsearch: bool = True) -> config_mod.Deployment:
    services = {"agent": config_mod.Service(url="http://example/api")}
    if elasticsearch:
        services["elasticsearch"] = config_mod.Service(url="http://example/elasticsearch")
    return config_mod.Deployment(
        base_url="http://example",
        services=services,
        written_at="2026-08-24T00:00:00+00:00",
    )


@pytest.fixture
def config_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(config_mod.CONFIG_HOME_ENV, str(tmp_path))
    config_mod.save(_deployment())
    return tmp_path


def _invoke(*args: str) -> Any:
    return CliRunner().invoke(configure_mod.configure, ["memory", *args])


def test_configure_memory_preserves_deployment_and_permissions(config_home: Path) -> None:
    result = _invoke(
        "--enable",
        "--backend",
        "elasticsearch",
        "--index",
        "vss-memory",
        "--persist-by-default",
    )
    assert result.exit_code == 0, result.output

    deployment = config_mod.load()
    assert deployment.services["agent"].url == "http://example/api"
    assert deployment.services["elasticsearch"].url == "http://example/elasticsearch"
    assert deployment.memory == config_mod.MemoryConfig()
    assert config_home.joinpath("config.json").stat().st_mode & 0o777 == 0o600


def test_configure_memory_updates_only_supplied_values(config_home: Path) -> None:
    assert _invoke("--index", "tenant-memory", "--no-persist-by-default").exit_code == 0
    assert _invoke("--disable").exit_code == 0
    memory_config = config_mod.load().memory
    assert memory_config == config_mod.MemoryConfig(
        enabled=False,
        backend="elasticsearch",
        index="tenant-memory",
        persist_by_default=False,
    )


def test_memory_show_prints_only_effective_memory_configuration(config_home: Path) -> None:
    assert _invoke("--index", "tenant-memory").exit_code == 0
    result = _invoke("show")
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {
        "enabled": True,
        "backend": "elasticsearch",
        "index": "tenant-memory",
        "persist_by_default": True,
        "markdown": {
            "enabled": False,
            "harness": "openclaw",
            "workspace": None,
            "write_by_default": False,
        },
    }
    assert "services" not in result.output
    assert "base_url" not in result.output


def test_memory_check_accepts_reachable_backend(
    config_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _invoke().exit_code == 0
    checked: list[tuple[str, str]] = []

    def reachable(deployment: config_mod.Deployment, memory_config: config_mod.MemoryConfig) -> str:
        checked.append((deployment.services["elasticsearch"].url, memory_config.index))
        return "reachable"

    monkeypatch.setattr(configure_mod, "_check_memory_backend", reachable)
    result = _invoke("check")
    assert result.exit_code == 0, result.output
    assert result.output.strip() == "reachable"
    assert checked == [("http://example/elasticsearch", "vss-memory")]


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("--backend", "sqlite"), "unsupported memory backend"),
        (("--index", "VSS Memory"), "invalid memory index"),
        (("--disable",), "cannot be enabled by default"),
    ],
)
def test_invalid_memory_configuration_exits_four(
    config_home: Path,
    args: tuple[str, ...],
    message: str,
) -> None:
    result = _invoke(*args)
    assert result.exit_code == int(Exit.CONFIGURATION)
    assert message in result.output
    assert config_mod.load().memory is None


def test_memory_show_and_check_require_configuration(config_home: Path) -> None:
    for command in ("show", "check"):
        result = _invoke(command)
        assert result.exit_code == int(Exit.CONFIGURATION)
        assert "vss configure memory" in result.output


def test_memory_check_rejects_disabled_memory(config_home: Path) -> None:
    assert _invoke("--disable", "--no-persist-by-default").exit_code == 0
    result = _invoke("check")
    assert result.exit_code == int(Exit.CONFIGURATION)
    assert "memory is disabled" in result.output
    assert "vss configure memory --enable" in result.output


def test_memory_check_requires_elasticsearch_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(config_mod.CONFIG_HOME_ENV, str(tmp_path))
    config_mod.save(
        config_mod.Deployment(
            base_url="http://example",
            services=_deployment(elasticsearch=False).services,
            memory=config_mod.MemoryConfig(),
        )
    )
    result = _invoke("check")
    assert result.exit_code == int(Exit.CONFIGURATION)
    assert "no Elasticsearch route" in result.output
    assert "vss configure --base-url" in result.output


def test_memory_check_reports_backend_reachability_as_exit_three(
    config_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _invoke().exit_code == 0

    class Unreachable:
        def raise_for_status(self) -> None:
            import httpx

            raise httpx.ConnectError("refused")

    monkeypatch.setattr("httpx.get", lambda *_args, **_kwargs: Unreachable())
    result = _invoke("check")
    assert result.exit_code == int(Exit.BACKEND_UNREACHABLE)
    assert "backend unreachable" in result.output
    assert "vss configure memory check" in result.output


def test_older_config_without_memory_remains_valid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(config_mod.CONFIG_HOME_ENV, str(tmp_path))
    tmp_path.joinpath("config.json").write_text(
        json.dumps(
            {
                "version": 1,
                "base_url": "http://example",
                "written_at": "2026-08-24T00:00:00+00:00",
                "services": {"elasticsearch": {"url": "http://example/elasticsearch"}},
            }
        ),
        encoding="utf-8",
    )
    assert config_mod.load().memory is None


def test_main_configure_preserves_memory_policy(
    config_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _invoke("--index", "tenant-memory", "--no-persist-by-default").exit_code == 0
    monkeypatch.setattr(configure_mod, "_probe", lambda *_args, **_kwargs: (True, "HTTP 200"))
    monkeypatch.setattr(configure_mod, "_describe", lambda *_args, **_kwargs: [])

    result = CliRunner().invoke(configure_mod.configure, ["--base-url", "http://new"])
    assert result.exit_code == 0, result.output
    assert config_mod.load().memory == config_mod.MemoryConfig(
        index="tenant-memory",
        persist_by_default=False,
    )


def test_configure_openclaw_markdown_settings(config_home: Path) -> None:
    workspace = config_home / "openclaw-workspace"
    workspace.mkdir()
    result = _invoke(
        "--markdown",
        "--harness",
        "openclaw",
        "--workspace",
        str(workspace),
        "--write-notes-by-default",
    )
    assert result.exit_code == 0, result.output
    memory_config = config_mod.load().memory
    assert memory_config is not None
    assert memory_config.markdown == config_mod.MarkdownMemoryConfig(
        enabled=True,
        harness="openclaw",
        workspace=str(workspace),
        write_by_default=True,
    )
    shown = json.loads(_invoke("show").output)
    assert shown["markdown"]["workspace"] == str(workspace)
    assert shown["markdown"]["write_by_default"] is True


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("--markdown",), "requires `--workspace"),
        (("--markdown", "--workspace", "relative/path"), "absolute path"),
        (("--harness", "other"), "unsupported Markdown memory harness"),
        (("--no-markdown", "--write-notes-by-default"), "while Markdown memory is disabled"),
        (
            (
                "--markdown",
                "--workspace",
                "/tmp/openclaw",
                "--write-notes-by-default",
                "--no-persist-by-default",
            ),
            "authoritative persistence is disabled",
        ),
    ],
)
def test_invalid_markdown_configuration_exits_four(
    config_home: Path,
    args: tuple[str, ...],
    message: str,
) -> None:
    result = _invoke(*args)
    assert result.exit_code == int(Exit.CONFIGURATION)
    assert message in result.output


def test_memory_check_validates_openclaw_workspace(
    config_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = config_home / "missing-workspace"
    assert _invoke("--markdown", "--workspace", str(missing)).exit_code == 0
    monkeypatch.setattr(configure_mod, "_check_memory_backend", lambda *_args, **_kwargs: "reachable")
    result = _invoke("check")
    assert result.exit_code == int(Exit.CONFIGURATION)
    assert "workspace is invalid" in result.output

    missing.mkdir()
    result = _invoke("check")
    assert result.exit_code == 0, result.output
    assert "OpenClaw Markdown cache enabled" in result.output


def test_memory_config_without_markdown_section_uses_disabled_defaults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(config_mod.CONFIG_HOME_ENV, str(tmp_path))
    tmp_path.joinpath("config.json").write_text(
        json.dumps(
            {
                "version": 1,
                "base_url": "http://example",
                "services": {"elasticsearch": {"url": "http://example/elasticsearch"}},
                "memory": {
                    "enabled": True,
                    "backend": "elasticsearch",
                    "index": "vss-memory",
                    "persist_by_default": True,
                },
            }
        ),
        encoding="utf-8",
    )
    memory_config = config_mod.load().memory
    assert memory_config is not None
    assert memory_config.markdown == config_mod.MarkdownMemoryConfig()
