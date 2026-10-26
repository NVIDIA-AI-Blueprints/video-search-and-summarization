# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from unittest.mock import MagicMock
from unittest.mock import Mock

from click.testing import CliRunner

from vss_cli import config
from vss_cli.group import Context
from vss_cli.search.group import SearchGroup
from vss_core.search_core.models.search import SearchOutput


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
