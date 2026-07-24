# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Contract tests for the extensible vss root dispatcher."""

from __future__ import annotations

import pytest

import vss_cli as cli


def test_root_help_lists_registered_domains(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--help"]) == 0
    assert "search" in capsys.readouterr().out


def test_search_help_lists_operations(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["search", "--help"]) == 0
    help_text = capsys.readouterr().out
    assert "{run,embed,attribute}" in help_text
    assert "Normal archive search: vss search run --help" in help_text
    assert "set VSS_REPO_ROOT" in help_text
    assert 'uv run --project "$VSS_REPO_ROOT/services/agent" --no-dev' in help_text


def test_unknown_root_command_returns_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["unknown"]) == 2
    assert "unknown command" in capsys.readouterr().err


def test_unknown_search_operation_returns_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["search", "unknown"]) == 2
    assert "unknown operation" in capsys.readouterr().err


def test_search_run_routes_to_search_implementation(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, list[str]]] = []

    def fake_run(operation: str, argv: list[str]) -> int:
        calls.append((operation, argv))
        return 7

    monkeypatch.setattr("vss_cli.search.run", fake_run)

    assert cli.main(["search", "run", "--query", "forklift"]) == 7
    assert calls == [("run", ["--query", "forklift"])]


@pytest.mark.parametrize(
    "operation",
    ["embed", "attribute"],
)
def test_search_operations_route_to_their_primitives(monkeypatch: pytest.MonkeyPatch, operation: str) -> None:
    calls: list[tuple[str, list[str]]] = []
    monkeypatch.setattr("vss_cli.search.run", lambda primitive, argv: calls.append((primitive, argv)) or 0)

    assert cli.main(["search", operation, "--json", "{}"]) == 0
    assert calls == [(operation, ["--json", "{}"])]


def test_operation_help_uses_nested_command_grammar(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["search", "run", "--help"]) == 0
    help_text = capsys.readouterr().out
    assert "usage: vss search run" in help_text
    assert "embed_search" not in help_text
