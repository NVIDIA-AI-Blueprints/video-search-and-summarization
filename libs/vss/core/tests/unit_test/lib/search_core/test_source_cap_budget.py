# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from unittest.mock import Mock

from click.testing import CliRunner
import pytest

from vss_cli import config
from vss_cli.group import Context
from vss_cli.search.group import SearchGroup
from vss_core.search_core.errors import InvalidInputError
from vss_core.search_core.models.search import SearchInput


def test_budget_is_required_and_validated_before_clients(monkeypatch):
    deployment = config.Deployment(
        base_url="http://fixture",
        services={
            "elasticsearch": config.Service(url="http://fixture/es"),
            "rtvi_cv": config.Service(url="http://fixture/cv"),
        },
    )
    monkeypatch.setattr("vss_cli.group.context_from", lambda _: Context(deployment=deployment))
    runtime = Mock(side_effect=AssertionError("invalid input must not construct clients"))
    monkeypatch.setattr("vss_cli.search.group._runtime_from", runtime)
    result = CliRunner().invoke(
        SearchGroup().cli(),
        [
            "run",
            "attribute",
            "--attribute",
            "person",
            "--max-hits-per-source",
            "1",
            "--no-critic",
            "--no-persist",
        ],
    )
    assert result.exit_code == 2, result.output
    runtime.assert_not_called()


def test_python_api_rejects_missing_budget_and_nonintegral_cap():
    with pytest.raises(InvalidInputError):
        SearchInput(search_mode="attribute", attributes=["person"], max_hits_per_source=1).validate_semantics()
    for value in (True, 1.0):
        with pytest.raises(ValueError):
            SearchInput(
                search_mode="attribute",
                attributes=["person"],
                max_hits_per_source=value,
                candidate_limit=100,
            )
