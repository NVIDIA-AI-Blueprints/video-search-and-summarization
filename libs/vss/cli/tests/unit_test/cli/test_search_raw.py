# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from unittest.mock import MagicMock
from unittest.mock import Mock

from click.testing import CliRunner

from vss_cli import config
from vss_cli.group import Context
from vss_cli.search.group import SearchGroup
from vss_core.search_core.models.search import SearchOutput


def test_attribute_frame_pooling_reaches_host_without_changing_embed_flags(monkeypatch):
    deployment = config.Deployment(
        base_url="http://fixture",
        services={
            "elasticsearch": config.Service(url="http://fixture/es"),
            "rtvi_cv": config.Service(url="http://fixture/cv"),
        },
    )
    monkeypatch.setattr("vss_cli.group.context_from", lambda _: Context(deployment=deployment))
    monkeypatch.setattr("vss_cli.search.group._runtime_from", lambda *_args, **_kwargs: MagicMock())
    captured = []

    class Search:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def search(self, **kwargs):
            captured.append(kwargs)
            return SearchOutput(data=[], search_messages=[])

        @classmethod
        def from_runtime(cls, *_args, **_kwargs):
            return cls()

    monkeypatch.setattr("vss_core.search_core.host.VSSSearch", Search)
    argv = [
        "run",
        "attribute",
        "--attribute",
        "person",
        "--attribute-frame-pool-size",
        "14",
        "--no-critic",
        "--no-persist",
        "--raw",
    ]
    # Pooling only reorders results within an exact candidate pool.
    unbudgeted = CliRunner().invoke(SearchGroup().cli(), argv)
    assert unbudgeted.exit_code != 0 and not captured
    result = CliRunner().invoke(SearchGroup().cli(), [*argv, "--candidate-limit", "100"])
    assert result.exit_code == 0, result.output
    assert captured[0]["attribute_frame_pool_size"] == 14
    assert captured[0]["search_mode"] == "attribute"
    embed = CliRunner().invoke(SearchGroup().cli(), ["run", "embed", "--help"])
    assert "--attribute-frame-pool-size" not in embed.output


def test_raw_search_never_constructs_verification_or_memory(monkeypatch):
    deployment = config.Deployment(
        base_url="http://fixture",
        services={
            "elasticsearch": config.Service(url="http://fixture/es"),
            "rt_embed": config.Service(url="http://fixture/embed"),
        },
    )
    monkeypatch.setattr("vss_cli.group.context_from", lambda _: Context(deployment=deployment))
    monkeypatch.setattr("vss_cli.search.group._runtime_from", lambda *_args, **_kwargs: MagicMock())
    factory = Mock(side_effect=AssertionError("verification must never initialize"))
    monkeypatch.setattr("vss_cli.search.group._critic_from", factory)

    class Search:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def search(self, **kw):
            return SearchOutput(data=[], search_messages=[])

        @classmethod
        def from_runtime(cls, *_args, **kw):
            assert kw["critic"] is None
            return cls()

    monkeypatch.setattr("vss_core.search_core.host.VSSSearch", Search)
    result = CliRunner().invoke(
        SearchGroup().cli(),
        ["run", "embed", "--query", "person", "--no-critic", "--no-persist", "--raw"],
    )
    assert result.exit_code == 0, result.output
    factory.assert_not_called()


def test_cli_runtime_has_no_frame_scoring_override():
    from vss_cli.search.group import _runtime_from

    deployment = config.Deployment(base_url="http://fixture", services={})
    assert not hasattr(_runtime_from(deployment), "frame_scoring")


def test_generic_representative_weight_is_forwarded_to_runtime():
    from vss_cli.search.group import SearchTuning
    from vss_cli.search.group import _runtime_from

    deployment = config.Deployment(base_url="http://fixture", services={})
    tuning = SearchTuning(attribute_representative_weight=0.5)
    runtime = _runtime_from(deployment, tuning.model_dump(exclude_none=True, exclude_defaults=True))
    assert runtime.attribute_representative_weight == 0.5
    assert _runtime_from(deployment).attribute_representative_weight == 0


def test_fusion_exposes_complete_attribute_pool_and_source_cap():
    import pytest

    from vss_cli.search.group import FusionInput
    from vss_cli.search.group import SearchTuning

    request = FusionInput(query="any query", attributes=["any attribute"], candidate_limit=30, max_hits_per_source=1)
    assert request.model_dump()["candidate_limit"] == 30
    with pytest.raises(ValueError, match="candidate_limit"):
        FusionInput(query="any query", max_hits_per_source=1)
    for bad in [-0.1, 1.1, float("nan"), float("inf")]:
        with pytest.raises(ValueError):
            SearchTuning(attribute_representative_weight=bad)
