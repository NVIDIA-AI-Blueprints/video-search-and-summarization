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


def test_every_path_accepts_the_pre_decomposition_question(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--original-query` is on `_Common`, so it is on all five paths.

    Retrieval never reads it. The critic does: without it the host has to
    rebuild a question out of `--query` and `--attribute`, which is a guess, and
    on `object` -- which sends no text at all -- the guess is empty and
    verification is skipped outright. Callers that decomposed the question are
    the ones that still have it, so this is the field where they hand it back.
    """
    for path in ("embed", "attribute", "tag", "fusion", "object"):
        assert cli.main(["search", "run", path, "--help"]) == 0
        assert "--original-query" in capsys.readouterr().out, path


def test_the_question_reaches_the_library_request_unchanged() -> None:
    """Through model_dump into SearchInput, which is extra=forbid."""
    from vss_cli.search.group import AttributeInput
    from vss_core.search_core.models.search import SearchInput

    asked = "is anyone in a white jacket near the loading door?"
    payload = AttributeInput(attributes=["white jacket"], original_query=asked).model_dump(
        exclude_none=True, exclude_defaults=True
    )
    payload["search_mode"] = "attribute"
    request = SearchInput(**payload)
    request.validate_semantics()
    assert request.original_query == asked
    # Retrieval is untouched: the attribute leg still sees only its attributes.
    assert request.query == ""
    assert request.attributes == ["white jacket"]


def test_an_omitted_question_leaves_the_request_exactly_as_before() -> None:
    """Default None must not become an empty string in the payload.

    `""` is falsy in the host's `if inp.original_query and ...` guard, so it
    would behave the same -- but it would also appear in persisted memory as a
    question the user never asked.
    """
    from vss_cli.search.group import EmbedInput

    payload = EmbedInput(query="forklift").model_dump(exclude_none=True, exclude_defaults=True)
    assert payload == {"query": "forklift"}


def test_run_needs_a_configured_deployment(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Endpoints come from `vss configure`, so absent config is exit 4."""
    from vss_cli import config as config_mod

    monkeypatch.setenv(config_mod.CONFIG_HOME_ENV, str(tmp_path / "absent"))
    assert cli.main(["search", "run", "embed", "--query", "forklift"]) == 4
    assert "vss configure" in capsys.readouterr().err
