# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
import json
from unittest.mock import AsyncMock

from click.testing import CliRunner
import pytest

from vss_cli import config as config_mod
from vss_cli.search import frame_capabilities
from vss_cli.search.group import SearchGroup
from vss_cli.search.group import SearchTuning


@pytest.fixture
def configured(tmp_path, monkeypatch):
    monkeypatch.setenv(config_mod.CONFIG_HOME_ENV, str(tmp_path))
    config_mod.save(
        config_mod.Deployment(
            base_url="http://example",
            services={
                "elasticsearch": config_mod.Service(url="http://example/elasticsearch", indices=["mdx-raw-2025-01-01"]),
            },
        )
    )


def test_probe_has_no_job_or_model_requirement(configured, monkeypatch):
    body = {"enabled": True, "fingerprint": "identity", "selected_scoring": "indexed"}
    probe = AsyncMock(return_value=body)
    monkeypatch.setattr(frame_capabilities, "probe", probe)
    result = CliRunner().invoke(
        SearchGroup().cli(),
        [
            "frame-capabilities",
            "--raw",
            "--source-type",
            "rtsp",
            "--frame-scoring",
            "source",
            "--frame-lookup-transport",
            "single",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == body
    runtime, source_type = probe.call_args.args
    assert runtime.frame_scoring == "source"
    assert runtime.frame_lookup_transport == "single"
    assert runtime.behavior_es_endpoint or runtime.es_endpoint == "http://example/elasticsearch"
    assert source_type == "rtsp"


def test_disabled_frames_need_no_backend():
    import asyncio

    from vss_core.search_core.runtime import SearchRuntime

    assert asyncio.run(frame_capabilities.probe(SearchRuntime(), "video_file"))["enabled"] is False


@pytest.mark.parametrize("field,value", [("frame_scoring", "bad"), ("frame_lookup_transport", "bad")])
def test_modes_are_validated(field, value):
    with pytest.raises(ValueError):
        SearchTuning(**{field: value})


@pytest.mark.parametrize(
    "source_type,expected", [("video_file", "mdx-raw-2025-01-01"), ("rtsp", ["mdx-raw-*", "-mdx-raw-2025-01-01"])]
)
def test_probe_resolves_partition_and_releases_client(monkeypatch, source_type, expected):
    import asyncio
    from types import SimpleNamespace

    from vss_core.search_core._internal import frame_refinement
    from vss_core.search_core.clients.elastic import ElasticClient
    from vss_core.search_core.runtime import SearchRuntime

    client = SimpleNamespace(aclose=AsyncMock())

    def factory(_runtime):
        return client

    monkeypatch.setattr(ElasticClient, "from_runtime_behavior", factory)
    resolver = AsyncMock(return_value={"fingerprint": "identity"})
    monkeypatch.setattr(frame_refinement, "resolve_frame_capabilities", resolver)
    runtime = SearchRuntime(frames_index="mdx-raw-2025-01-01")
    profile = asyncio.run(frame_capabilities.probe(runtime, source_type))
    assert profile["enabled"] is True
    assert resolver.call_args.args == (client, expected)
    client.aclose.assert_awaited_once()


def test_absent_frame_partition_is_disabled_and_stable(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from vss_core.search_core._internal import frame_refinement
    from vss_core.search_core.clients.elastic import ElasticClient
    from vss_core.search_core.errors import IndexNotFoundError
    from vss_core.search_core.runtime import SearchRuntime

    client = SimpleNamespace(aclose=AsyncMock())
    monkeypatch.setattr(ElasticClient, "from_runtime_behavior", lambda _runtime: client)
    monkeypatch.setattr(
        frame_refinement, "resolve_frame_capabilities", AsyncMock(side_effect=IndexNotFoundError("missing"))
    )
    runtime = SearchRuntime(frames_index="missing")
    first = asyncio.run(frame_capabilities.probe(runtime, "video_file"))
    second = asyncio.run(frame_capabilities.probe(runtime, "video_file"))
    assert first == second
    assert first["enabled"] is False
    assert first["fingerprint"]


def test_empty_live_wildcard_does_not_block_upload_campaign(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from vss_core.search_core.clients.elastic import ElasticClient
    from vss_core.search_core.runtime import SearchRuntime

    # Elasticsearch's wildcard inspection returns {}, rather than a 404, when
    # no live index matches and ignore_unavailable/allow_no_indices are enabled.
    client = SimpleNamespace(aclose=AsyncMock(), frame_capabilities=AsyncMock(return_value={}))
    monkeypatch.setattr(ElasticClient, "from_runtime_behavior", lambda _runtime: client)
    runtime = SearchRuntime(frames_index="mdx-raw-2025-01-01")
    profile = asyncio.run(frame_capabilities.probe(runtime, "rtsp"))
    assert profile["enabled"] is False
    assert profile["indices"] == []
    assert profile["fingerprint"]
    client.aclose.assert_awaited_once()
