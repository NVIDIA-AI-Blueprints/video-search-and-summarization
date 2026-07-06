# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Contract tests for lib.cli.search agent-facing wrappers."""

from __future__ import annotations

import argparse
import asyncio
import json

import pytest

from lib.cli.search import _apply_runtime_overrides
from lib.cli.search import _build_archive_search_payload
from lib.cli.search import _build_facade
from lib.cli.search import _config_env_from_args
from lib.cli.search import _exit_code_for_stream_error
from lib.cli.search import _extract_rows
from lib.cli.search import _maybe_build_vlm_analyzer
from lib.cli.search import _parse_args
from lib.cli.search import _render_output
from lib.cli.search import _required_runtime_args
from lib.cli.search import _runtime_from_args
from lib.cli.search import _search_options_from_args
from lib.cli.search import _write_search_stream
from lib.cli.search import run
from lib.search_core import SearchOptions
from lib.search_core import SearchRuntime
from lib.search_core.errors import ConfigurationError
from lib.search_core.errors import InvalidInputError
from lib.search_core.events import ErrorEvent
from lib.search_core.models.embed_search import EmbedSearchOutput
from lib.search_core.models.embed_search import EmbedSearchResultItem
from lib.search_core.models.search import SearchOutput
from lib.search_core.models.search import SearchResult


def _parse_search_args(argv: list[str]) -> argparse.Namespace:
    """Parse `vss-cli search run` args.

    The agent-friendly search flags live under the `search run` domain command
    (there is no separate `search-archive` script).
    """
    return _parse_args(argv, operation="run")


def test_search_archive_flags_build_structured_search_input() -> None:
    args = _parse_search_args(
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
    args = _parse_search_args(
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
    args = _parse_search_args(
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
    args = _parse_search_args(
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
    args = _parse_search_args(["--query", "forklift", "--no-use-critic"])

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
    args = _parse_search_args(
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
    args = _parse_search_args(["--query", "forklift", "--config-env", "ELASTIC_SEARCH_ENDPOINT"])

    with pytest.raises(InvalidInputError, match="--config-env must be KEY=VALUE"):
        _config_env_from_args(args)


def test_search_archive_rejects_non_positive_top_k() -> None:
    try:
        _parse_search_args(["--query", "find forklifts", "--top-k", "0"])
    except SystemExit as e:
        assert e.code == 2
    else:
        raise AssertionError("expected argparse to reject --top-k 0")


def test_search_archive_rejects_out_of_range_similarity() -> None:
    try:
        _parse_search_args(["--query", "find forklifts", "--min-cosine-similarity", "2"])
    except SystemExit as e:
        assert e.code == 2
    else:
        raise AssertionError("expected argparse to reject cosine similarity > 1")


def _embed_output() -> EmbedSearchOutput:
    return EmbedSearchOutput(
        query_embedding=[0.1, 0.2, 0.3],
        results=[
            EmbedSearchResultItem(
                video_name="clip.mp4",
                start_time="2025-01-01T00:00:00Z",
                end_time="2025-01-01T00:00:05Z",
                sensor_id="8fce43a6-1c35-4d6a-b6e3-391c42090a87",
                similarity_score=0.7,
            )
        ],
    )


def test_render_output_omits_embedding_by_default() -> None:
    # Even compact/raw output drops the embedding so agents get minimal JSON.
    args = argparse.Namespace(pretty=False, raw=True, include_embedding=False)
    rendered = _render_output(_embed_output(), args)
    parsed = json.loads(rendered)
    assert "\n" not in rendered  # compact single line
    assert "query_embedding" not in parsed
    assert parsed["results"][0]["video_name"] == "clip.mp4"


def test_render_output_include_embedding_opts_in() -> None:
    args = argparse.Namespace(pretty=False, raw=True, include_embedding=True)
    parsed = json.loads(_render_output(_embed_output(), args))
    assert parsed["query_embedding"] == [0.1, 0.2, 0.3]


def test_render_output_pretty_is_indented() -> None:
    args = argparse.Namespace(pretty=True, raw=False, include_embedding=False, output="json")
    rendered = _render_output(_embed_output(), args)
    assert "\n" in rendered  # indented
    assert "query_embedding" not in json.loads(rendered)


def _two_result_embed_output() -> EmbedSearchOutput:
    out = _embed_output()
    out.results.append(
        EmbedSearchResultItem(
            video_name="clip2.mp4",
            start_time="2025-01-01T00:01:00Z",
            end_time="2025-01-01T00:01:05Z",
            sensor_id="11111111-2222-3333-4444-555555555555",
            similarity_score=0.6,
        )
    )
    return out


def test_render_output_jsonl_emits_one_object_per_result() -> None:
    args = argparse.Namespace(pretty=False, raw=True, include_embedding=False, output="jsonl")
    rendered = _render_output(_two_result_embed_output(), args)
    lines = rendered.splitlines()
    assert len(lines) == 2
    assert [json.loads(line)["video_name"] for line in lines] == ["clip.mp4", "clip2.mp4"]
    # embedding never appears in per-row output
    assert all("query_embedding" not in json.loads(line) for line in lines)


def test_render_output_table_has_header_and_rows() -> None:
    args = argparse.Namespace(pretty=False, raw=True, include_embedding=False, output="table")
    rendered = _render_output(_two_result_embed_output(), args)
    lines = rendered.splitlines()
    # header + separator + 2 data rows
    assert len(lines) == 4
    assert "video_name" in lines[0]
    assert "similarity_score" in lines[0]
    assert "clip.mp4" in lines[2]
    assert "clip2.mp4" in lines[3]


def test_render_output_table_empty_results() -> None:
    args = argparse.Namespace(pretty=False, raw=True, include_embedding=False, output="table")
    rendered = _render_output(EmbedSearchOutput(query_embedding=[0.1], results=[]), args)
    assert rendered == "(no results)"


def test_extract_rows_uses_search_data_key() -> None:
    out = SearchOutput(
        data=[
            SearchResult(
                video_name="v.mp4",
                description="d",
                start_time="2025-01-01T00:00:00Z",
                end_time="2025-01-01T00:00:05Z",
                sensor_id="s",
                screenshot_url="u",
                similarity=0.5,
            )
        ],
        search_messages=[],
    )
    rows = _extract_rows(out)
    assert len(rows) == 1
    assert rows[0]["video_name"] == "v.mp4"


# --------------------------------------------------------------- config-env newline (#9)


def test_config_env_rejects_newline_in_value() -> None:
    args = _parse_search_args(
        ["--query", "forklift", "--config-env", "ELASTIC_SEARCH_ENDPOINT=http://es:9200\ninjected: true"]
    )
    with pytest.raises(InvalidInputError, match="must not contain newlines"):
        _config_env_from_args(args)


def test_config_env_rejects_carriage_return_in_value() -> None:
    args = _parse_search_args(["--query", "forklift", "--config-env", "K=a\rb"])
    with pytest.raises(InvalidInputError, match="must not contain newlines"):
        _config_env_from_args(args)


# --------------------------------------------------------------- per-primitive required args (#7)


class TestRequiredRuntimeArgs:
    def test_embed_search_does_not_require_rtvi_cv(self) -> None:
        attrs = {attr for attr, _flag in _required_runtime_args("embed_search")}
        assert "rtvi_cv_endpoint" not in attrs
        assert "cosmos_embed_endpoint" in attrs

    def test_attribute_search_does_not_require_cosmos_embed(self) -> None:
        attrs = {attr for attr, _flag in _required_runtime_args("attribute_search")}
        assert "cosmos_embed_endpoint" not in attrs
        assert "rtvi_cv_endpoint" in attrs

    def test_search_requires_all_five(self) -> None:
        attrs = {attr for attr, _flag in _required_runtime_args("search")}
        assert attrs == {
            "es_endpoint",
            "cosmos_embed_endpoint",
            "rtvi_cv_endpoint",
            "vst_internal_url",
            "vst_external_url",
        }

    def test_embed_search_runtime_builds_without_rtvi_cv(self) -> None:
        args = _parse_args(
            [
                "--es-endpoint",
                "http://es:9200",
                "--cosmos-embed-endpoint",
                "http://embed:8017",
                "--vst-internal-url",
                "http://vst:30888",
                "--vst-external-url",
                "http://vst:7777",
            ],
            operation="embed",
        )
        runtime = _runtime_from_args(args)
        assert runtime.es_endpoint == "http://es:9200"
        # Unused endpoint filled with an empty-string sentinel.
        assert runtime.rtvi_cv_endpoint == ""

    def test_attribute_search_runtime_builds_without_cosmos_embed(self) -> None:
        args = _parse_args(
            [
                "--es-endpoint",
                "http://es:9200",
                "--rtvi-cv-endpoint",
                "http://cv:9000",
                "--vst-internal-url",
                "http://vst:30888",
                "--vst-external-url",
                "http://vst:7777",
            ],
            operation="attribute",
        )
        runtime = _runtime_from_args(args)
        assert runtime.cosmos_embed_endpoint == ""
        assert runtime.rtvi_cv_endpoint == "http://cv:9000"

    def test_search_without_rtvi_cv_raises_configuration_error(self) -> None:
        args = _parse_args(
            [
                "--es-endpoint",
                "http://es:9200",
                "--cosmos-embed-endpoint",
                "http://embed:8017",
                "--vst-internal-url",
                "http://vst:30888",
                "--vst-external-url",
                "http://vst:7777",
            ],
            operation="run",
        )
        with pytest.raises(ConfigurationError, match="--rtvi-cv-endpoint"):
            _runtime_from_args(args)


# --------------------------------------------------------------- behavior_es override (#10)


def test_behavior_es_override_not_clobbered_by_es_endpoint_override() -> None:
    base = SearchRuntime.from_kwargs(
        es_endpoint="http://es-primary:9200",
        behavior_es_endpoint="http://es-behavior:9200",
        cosmos_embed_endpoint="http://embed:8017",
        rtvi_cv_endpoint="http://cv:9000",
        vst_internal_url="http://vst:30888",
        vst_external_url="http://vst:7777",
    )
    args = _parse_args(["--es-endpoint", "http://es-new:9200"], operation="run")
    updated = _apply_runtime_overrides(base, args)
    assert updated.es_endpoint == "http://es-new:9200"
    # The distinct behavior cluster from the config must survive.
    assert updated.behavior_es_endpoint == "http://es-behavior:9200"


def test_behavior_es_defaults_from_es_when_base_not_distinct() -> None:
    base = SearchRuntime.from_kwargs(
        es_endpoint="http://es-old:9200",
        behavior_es_endpoint="http://es-old:9200",
        cosmos_embed_endpoint="http://embed:8017",
        rtvi_cv_endpoint="http://cv:9000",
        vst_internal_url="http://vst:30888",
        vst_external_url="http://vst:7777",
    )
    args = _parse_args(["--es-endpoint", "http://es-new:9200"], operation="run")
    updated = _apply_runtime_overrides(base, args)
    assert updated.es_endpoint == "http://es-new:9200"
    assert updated.behavior_es_endpoint == "http://es-new:9200"


# --------------------------------------------------------------- use_attribute_search precedence (#11)


def test_explicit_config_false_beats_payload_fusion_heuristic() -> None:
    args = _parse_args([], operation="run")  # no --use-attribute-search flag
    base = SearchOptions(use_attribute_search=False)
    payload = {"attributes": ["white jacket"], "has_action": True}  # heuristic would say True
    result = _search_options_from_args(args, base=base, search_payload=payload)
    assert result.use_attribute_search is False


def test_explicit_flag_beats_config() -> None:
    args = _parse_args(["--no-use-attribute-search"], operation="run")
    base = SearchOptions(use_attribute_search=True)
    result = _search_options_from_args(args, base=base, search_payload=None)
    assert result.use_attribute_search is False


def test_heuristic_applies_without_config() -> None:
    args = _parse_args([], operation="run")
    payload = {"attributes": ["white jacket"], "has_action": True}
    result = _search_options_from_args(args, base=None, search_payload=payload)
    assert result.use_attribute_search is True


# --------------------------------------------------------------- config error surfaces (#1)


def _config_missing_es(tmp_path) -> str:
    config = tmp_path / "config.yml"
    config.write_text(
        """
functions:
  embed_search:
    cosmos_embed_endpoint: http://embed:8017
    vst_internal_url: http://vst:30888
    vst_external_url: http://vst:7777
  attribute_search:
    rtvi_cv_endpoint: http://cv:9000
""",
    )
    return str(config)


def test_config_error_not_swallowed_when_config_given(tmp_path) -> None:
    # All required CLI runtime flags are present; the OLD code would silently
    # drop the (broken) config and rebuild from args, losing every profile knob.
    config_path = _config_missing_es(tmp_path)
    args = _parse_args(
        [
            "--config",
            config_path,
            "--es-endpoint",
            "http://es:9200",
            "--cosmos-embed-endpoint",
            "http://embed:8017",
            "--rtvi-cv-endpoint",
            "http://cv:9000",
            "--vst-internal-url",
            "http://vst:30888",
            "--vst-external-url",
            "http://vst:7777",
        ],
        operation="run",
    )
    with pytest.raises(ConfigurationError, match="es_endpoint"):
        _build_facade(args, {})


def test_main_malformed_config_exits_4(tmp_path) -> None:
    config = tmp_path / "config.yml"
    config.write_text("functions: {search: [unclosed\n")
    exit_code = run(
        "run",
        [
            "--config",
            str(config),
            "--es-endpoint",
            "http://es:9200",
            "--cosmos-embed-endpoint",
            "http://embed:8017",
            "--rtvi-cv-endpoint",
            "http://cv:9000",
            "--vst-internal-url",
            "http://vst:30888",
            "--vst-external-url",
            "http://vst:7777",
            "--json",
            "{}",
        ],
    )
    assert exit_code == 4


# --------------------------------------------------------------- stream/non-stream exit-code parity (#4)


class TestStreamExitCodes:
    def test_index_not_found_maps_to_3(self) -> None:
        # IndexNotFoundError is a BackendUnreachableError subclass → exit 3.
        assert _exit_code_for_stream_error("IndexNotFoundError") == 3

    def test_backend_unreachable_maps_to_3(self) -> None:
        assert _exit_code_for_stream_error("BackendUnreachableError") == 3

    def test_vst_error_maps_to_3(self) -> None:
        assert _exit_code_for_stream_error("VSTError") == 3

    def test_invalid_input_maps_to_2(self) -> None:
        assert _exit_code_for_stream_error("InvalidInputError") == 2

    def test_validation_error_maps_to_2(self) -> None:
        assert _exit_code_for_stream_error("ValidationError") == 2

    def test_configuration_error_maps_to_4(self) -> None:
        assert _exit_code_for_stream_error("ConfigurationError") == 4

    def test_unknown_maps_to_1(self) -> None:
        assert _exit_code_for_stream_error("UnexpectedError") == 1
        assert _exit_code_for_stream_error("NoFinalResult") == 1

    def test_streamed_index_not_found_yields_exit_3(self) -> None:
        async def fake_stream():
            yield ErrorEvent(error_code="IndexNotFoundError", message="index missing")

        exit_code = asyncio.run(_write_search_stream(fake_stream()))
        assert exit_code == 3


def test_main_index_not_found_non_stream_exits_3(monkeypatch) -> None:
    from lib.search_core.errors import IndexNotFoundError

    def boom(args, payload=None):
        raise IndexNotFoundError("video_embeddings")

    monkeypatch.setattr("lib.cli.search._build_facade", boom)
    exit_code = run("run", ["--json", "{}"])
    # Same exit code (3) as the streamed IndexNotFoundError path above.
    assert exit_code == 3


# --------------------------------------------------------------- --stream on non-search (#7 adjacent)


def test_stream_rejected_on_non_search_primitive() -> None:
    exit_code = run("embed", ["--stream", "--json", "{}"])
    assert exit_code == 2


# --------------------------------------------------- search-only flags on other primitives (#1)


def test_search_only_flag_rejected_on_embed_search() -> None:
    # --query is a `search`-only flag; embed_search reads --json/stdin. Passing it
    # to embed_search must fail loudly (exit 2), not be silently ignored.
    exit_code = run("embed", ["--query", "red car", "--json", "{}"])
    assert exit_code == 2


def test_search_only_list_flag_rejected_on_attribute_search() -> None:
    exit_code = run("attribute", ["--attribute", "white jacket", "--json", "{}"])
    assert exit_code == 2


def test_reject_search_only_flags_names_the_offending_flags() -> None:
    from lib.cli.search import _reject_search_only_flags_for_non_search

    args = _parse_args(["--query", "x", "--top-k", "5"], operation="embed")
    with pytest.raises(InvalidInputError) as exc:
        _reject_search_only_flags_for_non_search(args)
    message = str(exc.value)
    assert "--query" in message
    assert "--top-k" in message
    assert "vss-cli search embed" in message


def test_non_search_primitive_without_search_flags_is_allowed() -> None:
    # No search-only flags provided -> the guard is a no-op even though the flags
    # are registered on the shared parser with default sentinels.
    from lib.cli.search import _reject_search_only_flags_for_non_search

    args = _parse_args(["--es-endpoint", "http://es:9200"], operation="embed")
    _reject_search_only_flags_for_non_search(args)  # must not raise


def test_search_primitive_still_accepts_search_flags() -> None:
    # The guard only fires for non-search primitives; `search` must be unaffected.
    args = _parse_search_args(["--query", "forklift", "--top-k", "5"])
    payload = _build_archive_search_payload(args)
    assert payload["query"] == "forklift"
    assert payload["top_k"] == 5


# --------------------------------------------------------------- analyzer gating (#8)


def _runtime_with_vlm() -> SearchRuntime:
    return SearchRuntime.from_kwargs(
        es_endpoint="http://es:9200",
        cosmos_embed_endpoint="http://embed:8017",
        rtvi_cv_endpoint="http://cv:9000",
        vst_internal_url="http://vst:30888",
        vst_external_url="http://vst:7777",
        vlm_base_url="http://vlm:8000/v1",
        vlm_model_name="gpt-4o",
    )


def test_plain_search_without_critic_builds_no_analyzer() -> None:
    args = _parse_args([], operation="run")
    analyzer = _maybe_build_vlm_analyzer(args, {"use_critic": False}, _runtime_with_vlm())
    assert analyzer is None


def test_critic_request_builds_analyzer() -> None:
    args = _parse_args([], operation="critic")
    analyzer = _maybe_build_vlm_analyzer(args, {}, _runtime_with_vlm())
    assert analyzer is not None
