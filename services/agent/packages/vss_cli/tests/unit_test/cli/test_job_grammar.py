# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Contract tests for the fixed verb grammar, config, and option derivation."""

from __future__ import annotations

import asyncio
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
        self.action: str = "?"

    def run(self, action: str, inputs: _Input, ctx: Context) -> Result:  # type: ignore[override]
        self.action = action
        self.seen = inputs
        return Result(body={"query": inputs.query, "attributes": inputs.attributes})


class _MarkerGroup(_Group):
    name = "search"

    def run(self, action: str, inputs: _Input, ctx: Context) -> Result:  # type: ignore[override]
        return Result(
            body={"data": [{"description": "paid-for result"}]},
            job_id="search-01",
            extra={"marker": {"asset_id": "camera-1", "status": "completed", "persisted": True}},
        )


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


def test_run_ends_with_compact_sdd_completion_marker_even_when_pretty() -> None:
    result = CliRunner().invoke(_MarkerGroup().cli(), ["run", "--pretty"])
    assert result.exit_code == 0, result.output
    marker_line = result.stdout.splitlines()[-1]
    marker = json.loads(marker_line)
    assert marker == {
        "event": "vss_job_completed",
        "group": "search",
        "job_id": "search-01",
        "asset_id": "camera-1",
        "status": "completed",
        "persisted": True,
        "exit_hint": 0,
    }
    assert len(marker_line.encode()) <= 1024


def test_out_of_range_value_is_rejected() -> None:
    result = CliRunner().invoke(_Group().cli(), ["run", "--top-k", "9999"])
    assert result.exit_code != 0
    assert "9999" in result.output


def test_read_verbs_fail_honestly_without_a_deployment(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """status/get/list are memory reads (SDD 6.2), so no deployment means no answer.

    Exit 4 naming memory, not an empty result: three verbs that appear to work
    and silently return nothing are worse than one that says why. An index that
    does not exist yet is the opposite case and reads as empty -- see
    `test_elasticsearch_list_before_anything_is_ingested_is_empty`.
    """
    monkeypatch.setenv(config_mod.CONFIG_HOME_ENV, str(tmp_path / "absent"))
    for argv in (["status", "--job-id", "x"], ["get", "--job-id", "x"], ["list"]):
        result = CliRunner().invoke(_Group().cli(), argv)
        assert result.exit_code == int(Exit.CONFIGURATION), argv
        assert "memory" in result.output.lower()


def test_read_verbs_require_enabled_memory(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(config_mod.CONFIG_HOME_ENV, str(tmp_path))
    config_mod.save(
        config_mod.Deployment(
            base_url="http://h:7777",
            services={"elasticsearch": config_mod.Service(url="http://h:7777/elasticsearch")},
            memory=config_mod.MemoryConfig(enabled=False, persist_by_default=False),
        )
    )
    for argv in (["status", "--job-id", "x"], ["get", "--job-id", "x"], ["list"]):
        result = CliRunner().invoke(_Group().cli(), argv)
        assert result.exit_code == int(Exit.CONFIGURATION), argv
        assert "memory is disabled" in result.output
        assert "vss configure memory --enable" in result.output


def test_since_only_advertises_what_it_accepts(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A rejected `--since` is the caller's mistake, so it exits 2 with a sentence.

    The help used to offer "ISO-8601 or duration" while the parser took only the
    first, and the difference surfaced as a traceback from inside the time
    helpers rather than as a usage error.
    """
    monkeypatch.setenv(config_mod.CONFIG_HOME_ENV, str(tmp_path / "absent"))
    since = next(param for param in _Group().cli().commands["list"].params if "--since" in param.opts)
    assert "duration" not in (since.help or "")

    result = CliRunner().invoke(_Group().cli(), ["list", "--since", "1h"])
    assert result.exit_code == int(Exit.INVALID_INPUT), result.output
    assert "ISO-8601" in result.output
    assert "Traceback" not in result.output


def test_read_verbs_do_not_expose_static_memory_index() -> None:
    """The authoritative index is configured once, not selected by a read."""
    for verb in ("status", "get", "list"):
        params = _Group().cli().commands[verb].params
        assert "--memory-index" not in {opt for param in params for opt in param.opts}, verb


def test_memory_builder_uses_configured_authoritative_index(monkeypatch: pytest.MonkeyPatch) -> None:
    from vss_cli import memory as memory_mod
    import vss_core.memory.backends.elasticsearch as elasticsearch_mod

    built: list[tuple[str, str]] = []

    class Store:
        def __init__(self, *, endpoint: str, index: str) -> None:
            built.append((endpoint, index))

        def close(self) -> None:
            return None

    monkeypatch.setattr(elasticsearch_mod, "ElasticsearchMemoryStore", Store)
    deployment = config_mod.Deployment(
        base_url="http://h:7777",
        services={"elasticsearch": config_mod.Service(url="http://h:7777/elasticsearch")},
        memory=config_mod.MemoryConfig(index="tenant-memory"),
    )
    facade = memory_mod.build(deployment)
    assert facade.index == "tenant-memory"
    assert built == [("http://h:7777/elasticsearch", "tenant-memory")]


@pytest.mark.parametrize(
    "memory_config",
    [
        None,
        config_mod.MemoryConfig(enabled=False, persist_by_default=False),
    ],
)
def test_memory_builder_requires_enabled_static_configuration(memory_config: config_mod.MemoryConfig | None) -> None:
    from vss_cli import memory as memory_mod

    deployment = config_mod.Deployment(
        base_url="http://h:7777",
        services={"elasticsearch": config_mod.Service(url="http://h:7777/elasticsearch")},
        memory=memory_config,
    )
    with pytest.raises(memory_mod.MemoryUnavailable):
        memory_mod.build(deployment)


# --------------------------------------------------------------------------
# deployment config
# --------------------------------------------------------------------------


def test_config_is_purely_descriptive(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Config records what backends said about themselves -- no CLI policy."""
    monkeypatch.setenv(config_mod.CONFIG_HOME_ENV, str(tmp_path))
    dep = config_mod.Deployment(
        base_url="http://h:7777",
        services={
            "vst": config_mod.Service(url="http://h:7777/vst"),
            "rt_embed": config_mod.Service(url="http://h:7777/cosmos-embed", models=["cosmos-embed1-448p"]),
        },
    )
    path = config_mod.save(dep)
    assert path.stat().st_mode & 0o777 == 0o600  # no credentials, but still not world-readable
    loaded = config_mod.load()
    assert loaded.endpoint("vst") == "http://h:7777/vst"
    # the descriptive half survives the round trip: models belong to the
    # service that reported them, not to a top-level knob
    assert loaded.services["rt_embed"].models == ["cosmos-embed1-448p"]


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


def test_right_version_wrong_shape_is_refused(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A file this CLI did not write must not load as an empty deployment.

    Observed live: another tool wrote a multi-deployment config
    (``{"version": 1, "current": ..., "deployments": {...}}``) to the same path.
    It matched on ``version``, so it parsed to base_url="" with no services, and
    the first search failed with "the deployment at  does not expose ... it has:
    (none)" -- which reads like a broken backend, not an unreadable file.
    """
    monkeypatch.setenv(config_mod.CONFIG_HOME_ENV, str(tmp_path))
    foreign = {
        "version": config_mod.CONFIG_VERSION,
        "current": "default",
        "deployments": {"default": {"base_url": "http://x"}},
    }
    (tmp_path / "config.json").write_text(json.dumps(foreign), encoding="utf-8")

    with pytest.raises(config_mod.ConfigError) as excinfo:
        config_mod.load()
    message = str(excinfo.value)
    assert "base_url" in message
    # names the keys it did find, so the writer is identifiable
    assert "deployments" in message


def test_config_without_services_is_refused(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(config_mod.CONFIG_HOME_ENV, str(tmp_path))
    (tmp_path / "config.json").write_text(
        json.dumps({"version": config_mod.CONFIG_VERSION, "base_url": "http://h:7777", "services": {}}),
        encoding="utf-8",
    )
    with pytest.raises(config_mod.ConfigError) as excinfo:
        config_mod.load()
    assert "no services" in str(excinfo.value)


def test_absent_route_names_what_is_available(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    dep = config_mod.Deployment(base_url="http://h:7777", services={"vst": config_mod.Service(url="http://h:7777/vst")})
    with pytest.raises(config_mod.ConfigError) as excinfo:
        dep.endpoint("elasticsearch")
    message = str(excinfo.value)
    assert "elasticsearch" in message and "vst" in message and "vss configure" in message


def test_library_errors_map_to_exit_codes() -> None:
    """A typed library failure is a diagnosis, not a crash.

    A missing index is the ordinary "nothing ingested yet" case; without the
    mapping it exits 1 with an Elasticsearch traceback, which no harness can
    branch on.
    """
    from vss_cli.group import _exit_for

    class LibraryError(Exception): ...

    class BackendUnreachableError(LibraryError): ...

    class IndexNotFoundError(BackendUnreachableError): ...

    class ConfigurationError(LibraryError): ...

    # most-derived wins: IndexNotFoundError subclasses BackendUnreachableError
    assert _exit_for(IndexNotFoundError("gone")) == Exit.NOT_FOUND
    assert _exit_for(BackendUnreachableError("down")) == Exit.BACKEND_UNREACHABLE
    assert _exit_for(ConfigurationError("bad")) == Exit.CONFIGURATION
    # anything unrecognised propagates rather than being flattened to one code
    assert _exit_for(RuntimeError("?")) is None


def test_configure_warns_when_elasticsearch_holds_no_search_indices(tmp_path, monkeypatch) -> None:
    """Indices come from ingestion, not deployment.

    Configuring a freshly deployed stack records zero indices and the record
    stays empty until someone re-runs, while search still appears to work
    because the runtime falls back to built-in index names. An eval spent 90
    minutes before a readiness check read no indexes out of a config that
    looked fine.
    """
    from vss_cli import configure as configure_mod

    monkeypatch.setenv(config_mod.CONFIG_HOME_ENV, str(tmp_path))
    monkeypatch.setattr(configure_mod, "_probe", lambda *_a, **_k: (True, "HTTP 200"))
    monkeypatch.setattr(configure_mod, "_describe", lambda *_a, **_k: [])

    result = CliRunner().invoke(configure_mod.configure, ["--base-url", "http://h:7777"])
    assert result.exit_code == 0, result.output
    assert "no mdx-* search indices yet" in result.output
    assert "re-run this command after ingesting" in result.output


def test_rt_vlm_is_discovered_from_the_same_origin(tmp_path, monkeypatch) -> None:
    """RT-VLM is reachable through the ingress, not only on its host port.

    ``describes`` is matched by exact string, so a typo silently records the
    url with no models and raises nothing -- it would pass every other test in
    this file. Pin the recorded shape so that failure cannot ship quietly.
    """
    from vss_cli import configure as configure_mod

    monkeypatch.setenv(config_mod.CONFIG_HOME_ENV, str(tmp_path))
    monkeypatch.setattr(
        configure_mod,
        "_probe",
        lambda _base, path, _t: (path == "/rtvi-vlm/v1/models", "HTTP 200"),
    )
    monkeypatch.setattr(configure_mod, "_describe", lambda *_a, **_k: ["cosmos-reason"])

    result = CliRunner().invoke(configure_mod.configure, ["--base-url", "http://h:7777"])
    assert result.exit_code == 0, result.output

    recorded = config_mod.load()
    assert recorded.services.keys() == {"rt_vlm"}
    assert recorded.endpoint("rt_vlm") == "http://h:7777/rtvi-vlm"
    assert recorded.services["rt_vlm"].models == ["cosmos-reason"]


def test_search_critic_is_optional_when_vlm_is_not_deployed() -> None:
    from vss_cli.search.group import _critic_from

    deployment = config_mod.Deployment(
        base_url="http://h:7777",
        services={
            "vst": config_mod.Service(url="http://h:7777/vst"),
            "elasticsearch": config_mod.Service(url="http://h:7777/elasticsearch"),
        },
    )

    assert asyncio.run(_critic_from(deployment)) == (None, None)


def test_search_critic_reuses_configured_vst_and_rt_vlm(monkeypatch: pytest.MonkeyPatch) -> None:
    from vss_cli.search import group as search_group

    deployment = config_mod.Deployment(
        base_url="https://vss.example",
        services={
            "vst": config_mod.Service(url="https://vss.example/vst"),
            "rt_vlm": config_mod.Service(
                url="https://vss.example/rtvi-vlm",
                models=["cosmos-reason3"],
            ),
        },
    )

    async def available(_url: str, _model: str) -> bool:
        return True

    monkeypatch.setattr(search_group, "_rt_vlm_available", available)
    critic, vlm = asyncio.run(search_group._critic_from(deployment))

    assert critic is not None and vlm is not None
    assert critic._time_format == "offset"
    assert critic._default_eval_count is None
    assert vlm._base_url == "https://vss.example/rtvi-vlm/v1"
    assert vlm._model == "cosmos-reason3"
    assert vlm._media_mode == "video_url"
    assert vlm._video_url_scope == "external"
    assert vlm._cosmos_nim_runtime_options is False


def test_search_critic_is_disabled_when_configured_vlm_is_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    from vss_cli.search import group as search_group

    deployment = config_mod.Deployment(
        base_url="https://vss.example",
        services={
            "vst": config_mod.Service(url="https://vss.example/vst"),
            "rt_vlm": config_mod.Service(url="https://vss.example/rtvi-vlm", models=["cosmos-reason3"]),
        },
    )
    probes: list[tuple[str, str]] = []

    async def unavailable(url: str, model: str) -> bool:
        probes.append((url, model))
        return False

    monkeypatch.setattr(search_group, "_rt_vlm_available", unavailable)

    assert asyncio.run(search_group._critic_from(deployment)) == (None, None)
    assert probes == [("https://vss.example/rtvi-vlm", "cosmos-reason3")]
