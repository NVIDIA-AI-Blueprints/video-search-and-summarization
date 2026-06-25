# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Contract tests for lib.search_core.cli agent-facing wrappers."""

from __future__ import annotations

import pytest

from lib.search_core.cli import _build_archive_search_payload
from lib.search_core.cli import _build_facade
from lib.search_core.cli import _config_env_from_args
from lib.search_core.cli import _parse_archive_search_args
from lib.search_core.errors import InvalidInputError


def test_search_archive_flags_build_structured_search_input() -> None:
    args = _parse_archive_search_args(
        [
            "--query",
            "person wearing a white jacket climbing a ladder",
            "--source-type",
            "video_file",
            "--video-source",
            "sample-warehouse-ladder",
            "--attribute",
            "white jacket",
            "--has-action",
            "true",
            "--top-k",
            "5",
            "--min-cosine-similarity",
            "0.25",
        ]
    )

    payload = _build_archive_search_payload(args)

    assert payload == {
        "query": "person wearing a white jacket climbing a ladder",
        "original_query": "person wearing a white jacket climbing a ladder",
        "source_type": "video_file",
        "video_sources": ["sample-warehouse-ladder"],
        "description": None,
        "timestamp_start": None,
        "timestamp_end": None,
        "top_k": 5,
        "attributes": ["white jacket"],
        "has_action": True,
        "object_ids": None,
        "min_cosine_similarity": 0.25,
        "agent_mode": False,
    }
    assert "use_critic" not in payload


def test_search_archive_supports_repeated_sources_and_object_ids() -> None:
    args = _parse_archive_search_args(
        [
            "--query",
            "find similar objects",
            "--source-type",
            "rtsp",
            "--video-source",
            "dock_cam",
            "--video-source",
            "gate_cam",
            "--object-id",
            "42",
            "--object-id",
            "99",
        ]
    )

    payload = _build_archive_search_payload(args)

    assert payload["video_sources"] == ["dock_cam", "gate_cam"]
    assert payload["object_ids"] == [42, 99]
    assert payload["source_type"] == "rtsp"


def test_search_archive_decomposed_json_preserves_host_agent_fields() -> None:
    args = _parse_archive_search_args(
        [
            "--decomposed-json",
            (
                '{"query":"person in a white jacket climbing a ladder",'
                '"original_query":"Who climbed the ladder?",'
                '"source_type":"rtsp",'
                '"video_sources":["dock_cam"],'
                '"attributes":["white jacket"],'
                '"has_action":true,'
                '"use_critic":true,'
                '"top_k":3}'
            ),
        ]
    )

    payload = _build_archive_search_payload(args)

    assert payload["query"] == "person in a white jacket climbing a ladder"
    assert payload["original_query"] == "Who climbed the ladder?"
    assert payload["source_type"] == "rtsp"
    assert payload["video_sources"] == ["dock_cam"]
    assert payload["attributes"] == ["white jacket"]
    assert payload["has_action"] is True
    assert payload["use_critic"] is True
    assert payload["agent_mode"] is False


def test_search_archive_flags_override_decomposed_json() -> None:
    args = _parse_archive_search_args(
        [
            "--decomposed-json",
            '{"query":"old query","source_type":"video_file","use_critic":false}',
            "--query",
            "new query",
            "--source-type",
            "rtsp",
            "--use-critic",
        ]
    )

    payload = _build_archive_search_payload(args)

    assert payload["query"] == "new query"
    assert payload["source_type"] == "rtsp"
    assert payload["use_critic"] is True


def test_search_archive_no_use_critic_sets_false() -> None:
    args = _parse_archive_search_args(["--query", "forklift", "--no-use-critic"])

    payload = _build_archive_search_payload(args)

    assert payload["use_critic"] is False


def test_search_archive_config_env_interpolates_config_without_process_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ELASTIC_SEARCH_ENDPOINT", "http://process-env-must-not-be-used.invalid")
    config = tmp_path / "config.yml"
    config.write_text(
        """
functions:
  search:
    use_attribute_search: true
    default_max_results: 7
  embed_search:
    es_endpoint: ${ELASTIC_SEARCH_ENDPOINT}
    cosmos_embed_endpoint: ${COSMOS_EMBED_ENDPOINT}
    vst_internal_url: ${VST_INTERNAL_URL}
    vst_external_url: ${VST_EXTERNAL_URL}
  attribute_search:
    rtvi_cv_endpoint: ${RTVI_CV_BASE_URL}
""",
        encoding="utf-8",
    )
    args = _parse_archive_search_args(
        [
            "--config",
            str(config),
            "--config-env",
            "ELASTIC_SEARCH_ENDPOINT=http://arg-es:9200",
            "--config-env",
            "COSMOS_EMBED_ENDPOINT=http://arg-embed:8017",
            "--config-env",
            "RTVI_CV_BASE_URL=http://arg-cv:9000",
            "--config-env",
            "VST_INTERNAL_URL=http://arg-vst:30888",
            "--config-env",
            "VST_EXTERNAL_URL=http://arg-vst.external",
            "--query",
            "forklift",
        ]
    )

    payload = _build_archive_search_payload(args)
    facade = _build_facade(args, payload)

    assert facade._rt.es_endpoint == "http://arg-es:9200"
    assert facade._rt.cosmos_embed_endpoint == "http://arg-embed:8017"
    assert facade._rt.rtvi_cv_endpoint == "http://arg-cv:9000"
    assert facade._opts.use_attribute_search is True
    assert facade._rt.default_max_results == 7


def test_config_env_rejects_missing_equals() -> None:
    args = _parse_archive_search_args(["--query", "forklift", "--config-env", "ELASTIC_SEARCH_ENDPOINT"])

    with pytest.raises(InvalidInputError, match="--config-env must be KEY=VALUE"):
        _config_env_from_args(args)


def test_search_archive_rejects_non_positive_top_k() -> None:
    try:
        _parse_archive_search_args(["--query", "find forklifts", "--top-k", "0"])
    except SystemExit as e:
        assert e.code == 2
    else:
        raise AssertionError("expected argparse to reject --top-k 0")


def test_search_archive_rejects_out_of_range_similarity() -> None:
    try:
        _parse_archive_search_args(["--query", "find forklifts", "--min-cosine-similarity", "2"])
    except SystemExit as e:
        assert e.code == 2
    else:
        raise AssertionError("expected argparse to reject cosine similarity > 1")
