# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Contract tests for the extensible vss root dispatcher."""

from __future__ import annotations

import pytest

import vss_cli as cli


def test_root_help_lists_registered_domains(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--help"]) == 0
    assert "search" in capsys.readouterr().out


def test_root_help_renders_declared_summary(capsys: pytest.CaptureFixture[str]) -> None:
    """The summary comes from the entry point, not from importing the group."""
    assert cli.main(["--help"]) == 0
    assert "Search indexed video" in capsys.readouterr().out


def test_search_exposes_the_fixed_verbs_and_the_primitives(capsys: pytest.CaptureFixture[str]) -> None:
    """run/status/get/list are the grammar; embed/attribute are the developer surface."""
    assert cli.main(["search", "--help"]) == 0
    help_text = capsys.readouterr().out
    for verb in ("run", "status", "get", "list"):
        assert verb in help_text
    for primitive in ("embed", "attribute"):
        assert primitive in help_text
    # the tier distinction is stated, not left to prose in a group docstring
    assert "developer surface" in help_text


def test_unknown_root_command_returns_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["unknown"]) == 2
    assert "No such command" in capsys.readouterr().err


def test_unknown_search_operation_returns_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["search", "unknown"]) == 2
    assert "No such command" in capsys.readouterr().err


def test_search_run_needs_a_configured_deployment(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Endpoints come from `vss configure`, not from flags -- so absent config is exit 4."""
    from vss_cli import config as config_mod

    monkeypatch.setenv(config_mod.CONFIG_HOME_ENV, str(tmp_path / "absent"))
    assert cli.main(["search", "run", "--query", "forklift"]) == 4
    assert "vss configure" in capsys.readouterr().err


@pytest.mark.parametrize(
    "operation",
    ["embed", "attribute"],
)
def test_search_operations_route_to_their_primitives(monkeypatch: pytest.MonkeyPatch, operation: str) -> None:
    calls: list[tuple[str, list[str]]] = []
    monkeypatch.setattr("vss_cli.search.run", lambda primitive, argv: calls.append((primitive, argv)) or 0)

    assert cli.main(["search", operation, "--json", "{}"]) == 0
    assert calls == [(operation, ["--json", "{}"])]


def test_run_options_are_derived_and_carry_no_deployment_flags(capsys: pytest.CaptureFixture[str]) -> None:
    """run's flags come from SearchRunInput; endpoints and discovery are gone."""
    assert cli.main(["search", "run", "--help"]) == 0
    help_text = capsys.readouterr().out
    assert "--query" in help_text and "--search-mode" in help_text
    for gone in ("--es-endpoint", "--cosmos-embed-endpoint", "--video-embed-index", "--deployment", "--kube-context"):
        assert gone not in help_text, gone


def test_primitives_still_forward_argv_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    """embed/attribute keep the argparse parser that documents their options."""
    seen: list[tuple[str, list[str]]] = []
    monkeypatch.setattr("vss_cli.search.run", lambda op, argv: seen.append((op, argv)) or 0)

    assert cli.main(["search", "embed", "--json", "{}"]) == 0
    assert seen == [("embed", ["--json", "{}"])]
