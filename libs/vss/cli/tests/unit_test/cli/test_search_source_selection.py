# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Source selection usage errors must precede clients, probes, and jobs."""

from unittest.mock import Mock

from click.testing import CliRunner
import pytest

from vss_cli import config as config_mod
from vss_cli.group import Context
from vss_cli.search.group import SearchGroup
from vss_cli.search.group import SearchTuning


@pytest.mark.parametrize("value", [True, False, 0, -1, 100001, 1.5])
def test_source_cap_rejects_nonpositive_or_nonintegral_values(value):
    with pytest.raises(ValueError):
        SearchTuning(max_hits_per_source=value)


@pytest.mark.parametrize(
    "arguments",
    [
        ["embed", "--query", "red", "--candidate-limit", "20"],
        ["fusion", "--query", "red", "--attribute", "red", "--candidate-limit", "20"],
        ["attribute", "--attribute", "red"],
        ["attribute", "--attribute", "red", "--source-type", "rtsp", "--candidate-limit", "20"],
    ],
)
def test_unsupported_selection_fails_before_runtime_or_job(monkeypatch, arguments):
    deployment = config_mod.Deployment(
        base_url="http://fixture",
        services={
            "elasticsearch": config_mod.Service(url="http://fixture/elasticsearch"),
            "rt_embed": config_mod.Service(url="http://fixture/cosmos-embed"),
            "rtvi_cv": config_mod.Service(url="http://fixture/rtvi-cv"),
        },
    )
    monkeypatch.setattr("vss_cli.group.context_from", lambda _values: Context(deployment=deployment))
    runtime = Mock(side_effect=AssertionError("must not construct runtime"))
    job = Mock(side_effect=AssertionError("must not create job"))
    critic = Mock(side_effect=AssertionError("must not probe critic"))
    monkeypatch.setattr("vss_cli.search.group._runtime_from", runtime)
    monkeypatch.setattr("vss_cli.search.group._mint_job_id", job)
    monkeypatch.setattr("vss_cli.search.group._critic_from", critic)
    result = CliRunner().invoke(SearchGroup().cli(), ["run", *arguments, "--max-hits-per-source", "1"])
    assert result.exit_code == 2, result.output
    runtime.assert_not_called()
    job.assert_not_called()
    critic.assert_not_called()


def test_source_cap_is_exposed_and_unset_by_default():
    assert SearchTuning().max_hits_per_source is None
    help_result = CliRunner().invoke(SearchGroup().cli(), ["run", "attribute", "--help"])
    assert help_result.exit_code == 0, help_result.output
    assert "--max-hits-per-source" in help_result.output
