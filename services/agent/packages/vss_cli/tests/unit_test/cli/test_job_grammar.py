# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Contract tests for the fixed verb grammar, config, and option derivation."""

from __future__ import annotations

import json
from typing import Literal

import click
from click.testing import CliRunner
from pydantic import BaseModel
from pydantic import Field
import pytest

from vss_cli import config as config_mod
from vss_cli import params as params_mod
from vss_cli.exits import Exit
from vss_cli.group import CommandGroup
from vss_cli.group import Context
from vss_cli.group import Result


class _Input(BaseModel):
    query: str = Field("", description="What to look for")
    search_mode: Literal["embed", "attribute", "fusion"] | None = Field(None, description="Execution path")
    top_k: int | None = Field(None, ge=1, le=1000, description="Max results")
    min_similarity: float | None = Field(None, ge=-1.0, le=1.0, description="Score floor")
    attributes: list[str] = Field(
        default_factory=list, description="Repeatable", json_schema_extra={"cli_flag": "--attribute"}
    )
    frame_lookup: bool | None = Field(None, description="Tri-state toggle")
    internal: str = Field("", description="Not a flag", json_schema_extra={"cli_hide": True})


class _Group(CommandGroup):
    """Probe group."""

    name = "probe"
    summary = "Probe group"
    Input = _Input

    def __init__(self) -> None:
        self.seen: _Input | None = None

    def run(self, inputs: _Input, ctx: Context) -> Result:  # type: ignore[override]
        self.seen = inputs
        return Result(body={"query": inputs.query, "attributes": inputs.attributes})


# --------------------------------------------------------------------------
# option derivation
# --------------------------------------------------------------------------


def test_flags_are_derived_from_field_names() -> None:
    names = {o.opts[0] for o in params_mod.options_from_model(_Input)}
    assert "--query" in names
    assert "--top-k" in names  # underscore -> dash
    assert "--attribute" in names  # cli_flag alias, not --attributes


def test_hidden_fields_produce_no_flag() -> None:
    names = {o.opts[0] for o in params_mod.options_from_model(_Input)}
    assert "--internal" not in names


def test_literal_becomes_a_choice() -> None:
    opt = next(o for o in params_mod.options_from_model(_Input) if o.opts[0] == "--search-mode")
    assert isinstance(opt.type, click.Choice)
    assert set(opt.type.choices) == {"embed", "attribute", "fusion"}


def test_numeric_constraints_become_ranges() -> None:
    opts = {o.opts[0]: o for o in params_mod.options_from_model(_Input)}
    assert isinstance(opts["--top-k"].type, click.IntRange)
    assert isinstance(opts["--min-similarity"].type, click.FloatRange)


def test_list_field_is_repeatable() -> None:
    opt = next(o for o in params_mod.options_from_model(_Input) if o.opts[0] == "--attribute")
    assert opt.multiple is True


def test_unset_options_do_not_override_model_defaults() -> None:
    """Click yields None/() for untouched flags; those must not reach the model."""
    supplied = params_mod.collect(_Input, {"query": "forklift", "top_k": None, "attributes": ()})
    assert supplied == {"query": "forklift"}


# --------------------------------------------------------------------------
# the fixed verb grammar
# --------------------------------------------------------------------------


def test_every_group_exposes_the_four_verbs() -> None:
    group = _Group().cli()
    assert {"run", "status", "get", "list"} <= set(group.commands)


def test_there_is_no_submit_verb() -> None:
    """Fire-and-forget is harness-owned (UM-4); a submit verb would undo that."""
    assert "submit" not in _Group().cli().commands


def test_run_parses_derived_flags_into_the_model() -> None:
    owner = _Group()
    result = CliRunner().invoke(
        owner.cli(), ["run", "--query", "forklift", "--attribute", "red", "--attribute", "large", "--top-k", "3"]
    )
    assert result.exit_code == 0, result.output
    assert owner.seen is not None
    assert owner.seen.query == "forklift"
    assert owner.seen.attributes == ["red", "large"]
    assert owner.seen.top_k == 3


def test_explicit_flags_override_json_payload() -> None:
    owner = _Group()
    result = CliRunner().invoke(
        owner.cli(), ["run", "--json", '{"query":"from-json","top_k":9}', "--query", "from-flag"]
    )
    assert result.exit_code == 0, result.output
    assert owner.seen is not None
    assert owner.seen.query == "from-flag"  # flag wins
    assert owner.seen.top_k == 9  # json survives where no flag given


def test_out_of_range_value_is_rejected() -> None:
    result = CliRunner().invoke(_Group().cli(), ["run", "--top-k", "9999"])
    assert result.exit_code != 0
    assert "9999" in result.output


def test_read_verbs_fail_honestly_without_a_memory_tier() -> None:
    """status/get/list are memory reads (SDD 6.2); vss_core ships no memory yet."""
    for argv in (["status", "--job-id", "x"], ["get", "--job-id", "x"], ["list"]):
        result = CliRunner().invoke(_Group().cli(), argv)
        assert result.exit_code == int(Exit.CONFIGURATION), argv
        assert "memory" in result.output.lower()


# --------------------------------------------------------------------------
# deployment config
# --------------------------------------------------------------------------


def test_config_roundtrip(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(config_mod.CONFIG_HOME_ENV, str(tmp_path))
    dep = config_mod.Deployment(base_url="http://h:7777", endpoints={"vst": "http://h:7777/vst"})
    path = config_mod.save(dep)
    assert path.stat().st_mode & 0o777 == 0o600  # no credentials, but still not world-readable
    assert config_mod.load().endpoints["vst"] == "http://h:7777/vst"


def test_missing_config_points_at_configure(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(config_mod.CONFIG_HOME_ENV, str(tmp_path / "absent"))
    with pytest.raises(config_mod.ConfigError) as excinfo:
        config_mod.load()
    assert "vss configure" in str(excinfo.value)


def test_future_config_version_is_refused(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(config_mod.CONFIG_HOME_ENV, str(tmp_path))
    (tmp_path / "config.json").write_text(json.dumps({"version": 99, "base_url": "x"}), encoding="utf-8")
    with pytest.raises(config_mod.ConfigError):
        config_mod.load()


def test_absent_route_names_what_is_available(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    dep = config_mod.Deployment(base_url="http://h:7777", endpoints={"vst": "http://h:7777/vst"})
    with pytest.raises(config_mod.ConfigError) as excinfo:
        dep.endpoint("elasticsearch")
    message = str(excinfo.value)
    assert "elasticsearch" in message and "vst" in message and "vss configure" in message
