# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Contract tests for the extensible vss root dispatcher."""

from __future__ import annotations

import pytest  # noqa: TC002 - fixtures are resolved at runtime

import vss_cli as cli


def test_root_help_lists_registered_domains(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--help"]) == 0
    assert "search" in capsys.readouterr().out


def test_root_help_renders_declared_summary(capsys: pytest.CaptureFixture[str]) -> None:
    """The summary comes from the entry point, not from importing the group."""
    assert cli.main(["--help"]) == 0
    assert "Search indexed video" in capsys.readouterr().out


def test_unknown_root_command_returns_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["unknown"]) == 2
    assert "No such command" in capsys.readouterr().err


# --------------------------------------------------------------------------
# search: fixed verbs, retrieval paths as sub-actions of run
# --------------------------------------------------------------------------


def test_search_exposes_only_the_fixed_verbs(capsys: pytest.CaptureFixture[str]) -> None:
    """embed/attribute are no longer siblings of run -- they moved under it."""
    assert cli.main(["search", "--help"]) == 0
    out = capsys.readouterr().out
    for verb in ("run", "status", "get", "list"):
        assert verb in out
    commands = out.split("Commands:", 1)[1]
    for gone in ("embed", "attribute"):
        assert gone not in commands, f"{gone} should live under `run`, not beside it"


def test_run_lists_the_retrieval_paths(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["search", "run", "--help"]) == 0
    out = capsys.readouterr().out
    for action in ("embed", "attribute", "fusion", "object"):
        assert action in out


def test_search_mode_flag_is_gone(capsys: pytest.CaptureFixture[str]) -> None:
    """The sub-action *is* the mode; a flag would let both disagree."""
    assert cli.main(["search", "run", "embed", "--help"]) == 0
    assert "--search-mode" not in capsys.readouterr().out


def test_actions_carry_no_deployment_or_endpoint_flags(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["search", "run", "fusion", "--help"]) == 0
    out = capsys.readouterr().out
    for gone in (
        "--es-endpoint",
        "--cosmos-embed-endpoint",
        "--video-embed-index",
        "--memory-index",
        "--deployment",
        "--kube-context",
    ):
        assert gone not in out, gone


def test_each_action_accepts_only_its_own_fields(capsys: pytest.CaptureFixture[str]) -> None:
    """What SearchInput rejected at runtime is now unrepresentable."""
    assert cli.main(["search", "run", "embed", "--help"]) == 0
    embed_help = capsys.readouterr().out
    assert "--query" in embed_help
    assert "--attribute " not in embed_help  # attributes are not an embed concept

    assert cli.main(["search", "run", "attribute", "--help"]) == 0
    attribute_help = capsys.readouterr().out
    assert "--attribute " in attribute_help
    assert "--query" not in attribute_help


def test_run_needs_a_configured_deployment(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Endpoints come from `vss configure`, so absent config is exit 4."""
    from vss_cli import config as config_mod

    monkeypatch.setenv(config_mod.CONFIG_HOME_ENV, str(tmp_path / "absent"))
    assert cli.main(["search", "run", "embed", "--query", "forklift"]) == 4
    assert "vss configure" in capsys.readouterr().err
