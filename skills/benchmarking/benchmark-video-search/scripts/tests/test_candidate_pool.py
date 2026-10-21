# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Returned pools are independent of the metric cutoff and index scan budget."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_eval_flows as runner
from flows.artifacts import RunConfig
from flows.query import CliQueryBackend


def test_pool_caps_returned_hits_without_changing_scan_budget():
    backend = CliQueryBackend(
        vss_cmd=["vss"], top_k=100000, candidate_limit=25000, retrieval_pool_size=10
    )
    argv = backend.build_argv("red coat", backend.plan_for_query("red coat"))
    assert argv[argv.index("--top-k") + 1] == "10"
    assert argv[argv.index("--candidate-limit") + 1] == "25000"
    assert backend.describe()["retrieval_pool_size"] == 10


def test_explicit_query_top_k_remains_narrower_than_pool():
    backend = CliQueryBackend(
        vss_cmd=["vss"],
        top_k=80,
        retrieval_pool_size=10,
        decompositions={"q": {"query": "q", "top_k": 3}},
    )
    argv = backend.build_argv("q", backend.plan_for_query("q"))
    assert argv[argv.index("--top-k") + 1] == "3"


def test_pool_setting_is_preserved_independently_in_config():
    args = runner.parse_args(
        [
            "--endpoint",
            "unused",
            "--top-k",
            "5",
            "--retrieval-pool-size",
            "10",
            "--candidate-limit",
            "20000",
        ]
    )
    cfg = RunConfig.from_args(args, None)
    assert (
        cfg.top_k == 5
        and cfg.retrieval_pool_size == 10
        and cfg.candidate_limit == 20000
    )


@pytest.mark.parametrize(
    "options",
    [
        ["--retrieval-pool-size", "0"],
        ["--retrieval-pool-size", "100001"],
        ["--full-gallery", "--retrieval-pool-size", "10"],
        ["--stage", "ingest", "--retrieval-pool-size", "10"],
    ],
)
def test_invalid_or_conflicting_pool_options(options):
    with pytest.raises(SystemExit):
        runner.parse_args(["--endpoint", "unused", *options])


def test_pool_default_preserves_existing_retrieval():
    backend = CliQueryBackend(vss_cmd=["vss"], top_k=80)
    argv = backend.build_argv("q", backend.plan_for_query("q"))
    assert argv[argv.index("--top-k") + 1] == "80"
    assert "retrieval_pool_size" not in backend.describe()


@pytest.mark.parametrize("value", ["null", "[]", "true", '{"temperature":NaN}'])
def test_chat_template_controls_require_finite_json_object(value):
    with pytest.raises(SystemExit):
        runner.parse_args(["--endpoint", "unused", "--llm-chat-template-kwargs", value])


def test_chat_template_controls_accept_explicit_empty_and_nested_objects():
    for value in ("{}", '{"enable_thinking":false,"nested":{"values":[1,"x",null]}}'):
        args = runner.parse_args(
            ["--endpoint", "unused", "--llm-chat-template-kwargs", value]
        )
        assert isinstance(args.llm_chat_template_kwargs, dict)


def test_gallery_ingestion_defaults_to_readiness_for_all_scenarios():
    args = runner.parse_args(
        ["--endpoint", "unused", "--stage", "ingest", "--gallery-gt", "gallery"]
    )
    assert args.scenarios == ["embed-only", "attribute-only", "live-routed"]
    args = runner.parse_args(
        [
            "--endpoint",
            "unused",
            "--stage",
            "ingest",
            "--gallery-gt",
            "gallery",
            "--scenarios",
            "embed-only",
        ]
    )
    assert args.scenarios == ["embed-only"]
