# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Contract tests for cli.search agent-facing wrappers."""

from __future__ import annotations

import argparse
import asyncio
import io
import json

import pytest

from vss_cli.deployment import PortForwardError
from vss_cli.search import _apply_runtime_overrides
from vss_cli.search import _build_archive_search_payload
from vss_cli.search import _build_facade
from vss_cli.search import _config_env_from_args
from vss_cli.search import _deployment_env_overrides
from vss_cli.search import _exit_code_for_stream_error
from vss_cli.search import _extract_rows
from vss_cli.search import _load_payload
from vss_cli.search import _parse_args
from vss_cli.search import _preflight_embed_model
from vss_cli.search import _preflight_index
from vss_cli.search import _preflight_rtvi_cv
from vss_cli.search import _preflight_search_runtime
from vss_cli.search import _render_output
from vss_cli.search import _required_runtime_args
from vss_cli.search import _resolve_named_sources
from vss_cli.search import _rewrite_deployment_runtime
from vss_cli.search import _runtime_fields_for_request
from vss_cli.search import _runtime_from_args
from vss_cli.search import _validate_payload_before_preflight
from vss_cli.search import _write_search_stream
from vss_cli.search import run
from vss_core._foundation.errors import BackendUnreachableError
from vss_core.search_core import SearchRuntime
from vss_core.search_core.errors import ConfigurationError
from vss_core.search_core.errors import InvalidInputError
from vss_core.search_core.events import ErrorEvent
from vss_core.search_core.models.embed_search import EmbedSearchOutput
from vss_core.search_core.models.embed_search import EmbedSearchResultItem
from vss_core.search_core.models.search import SearchOutput
from vss_core.search_core.models.search import SearchResult
from vss_core.vst import VSTError


def _parse_search_args(argv: list[str]) -> argparse.Namespace:
    """Parse `vss search run` args.

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
            "--search-mode",
            "fusion",
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
        "search_mode": "fusion",
        "attributes": ["white jacket"],
        "object_ids": None,
        "min_cosine_similarity": 0.25,
    }


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
            "--search-mode",
            "object",
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
                '"search_mode":"fusion",'
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
    assert payload["search_mode"] == "fusion"
    assert "use_critic" not in payload


def test_search_archive_flags_override_decomposed_json() -> None:
    args = _parse_search_args(
        [
            "--decomposed-json",
            '{"query":"old query","source_type":"video_file"}',
            "--query",
            "new query",
            "--source-type",
            "rtsp",
        ]
    )

    payload = _build_archive_search_payload(args)

    assert payload["query"] == "new query"
    assert payload["source_type"] == "rtsp"
    assert "use_critic" not in payload


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
    assert facade._rt.default_max_results == 7


def test_config_env_rejects_missing_equals() -> None:
    args = _parse_search_args(["--query", "forklift", "--config-env", "ELASTIC_SEARCH_ENDPOINT"])

    with pytest.raises(InvalidInputError, match="--config-env must be KEY=VALUE"):
        _config_env_from_args(args)


def test_load_payload_rejects_non_object_json() -> None:
    with pytest.raises(InvalidInputError, match="must be a JSON object"):
        _load_payload(argparse.Namespace(json_payload="[]"))


def test_load_payload_rejects_non_object_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("[]"))

    with pytest.raises(InvalidInputError, match="stdin JSON must be an object"):
        _load_payload(argparse.Namespace(json_payload=None))


def test_deployment_runtime_flags_fill_config_interpolation() -> None:
    args = _parse_search_args(
        [
            "--deployment",
            "kubernetes",
            "--namespace",
            "vss",
            "--release",
            "search",
            "--es-endpoint",
            "https://es.example",
            "--cosmos-embed-endpoint",
            "https://embed.example",
            "--rtvi-cv-endpoint",
            "https://cv.example",
            "--vst-internal-url",
            "https://vst.example",
            "--vst-external-url",
            "https://public.example",
            "--query",
            "forklift",
        ]
    )

    env = _deployment_env_overrides(args)

    assert env["ELASTIC_SEARCH_ENDPOINT"] == "https://es.example"
    assert env["COSMOS_EMBED_ENDPOINT"] == "https://embed.example"
    assert env["RTVI_EMBED_BASE_URL"] == "https://embed.example"
    assert env["RTVI_CV_ENDPOINT"] == "https://cv.example"
    assert env["RTVI_CV_BASE_URL"] == "https://cv.example"
    assert env["VST_INTERNAL_URL"] == "https://vst.example"


def test_deployment_index_flags_fill_every_supported_interpolation_alias() -> None:
    args = _parse_search_args(
        [
            "--video-embed-index",
            "tenant-video",
            "--video-embed-index-wildcard",
            "tenant-video-*",
            "--behavior-index",
            "tenant-behavior",
            "--behavior-index-wildcard",
            "tenant-behavior-*",
            "--frames-index",
            "tenant-raw",
            "--frames-index-wildcard",
            "tenant-raw-*",
            "--query",
            "forklift",
        ]
    )

    env = _deployment_env_overrides(args)

    assert env["ELASTIC_SEARCH_INDEX"] == env["RTVI_EMBED_ES_INDEX"] == "tenant-video"
    assert env["ELASTIC_SEARCH_INDEX_WILDCARD"] == "tenant-video-*"
    assert env["RTSP_EMBED_ES_INDEX_PATTERN"] == "tenant-video-*"
    assert env["BEHAVIOR_ES_INDEX"] == env["BEHAVIOR_INDEX"] == "tenant-behavior"
    assert env["BEHAVIOR_INDEX_WILDCARD"] == "tenant-behavior-*"
    assert env["RTSP_BEHAVIOR_ES_INDEX_PATTERN"] == "tenant-behavior-*"
    assert env["FRAMES_INDEX"] == env["RAW_ES_INDEX"] == "tenant-raw"
    assert env["FRAMES_INDEX_WILDCARD"] == "tenant-raw-*"
    assert env["RTSP_RAW_ES_INDEX_PATTERN"] == "tenant-raw-*"


def test_deployment_rewrites_after_explicit_runtime_overrides(tmp_path) -> None:
    config = tmp_path / "config.yml"
    config.write_text(
        """
functions:
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
    args = _parse_search_args(["--es-endpoint", "https://external-es.example", "--query", "forklift"])

    class RecordingDeployment:
        def __init__(self) -> None:
            self.config_path = config
            self.env = {
                "COSMOS_EMBED_ENDPOINT": "http://vss-rtvi-embed:8000",
                "RTVI_CV_BASE_URL": "http://vss-rtvi-cv:9000",
                "VST_INTERNAL_URL": "http://vss-vios-ingress:30888",
                "VST_EXTERNAL_URL": "https://public.example",
            }
            self.seen_es_endpoint: str | None = None

        def rewrite_runtime(self, runtime, *, fields):
            self.seen_es_endpoint = runtime.es_endpoint
            assert fields == {"es_endpoint", "cosmos_embed_endpoint", "vst_external_url"}
            return runtime

    deployment = RecordingDeployment()

    _build_facade(args, _build_archive_search_payload(args), deployment=deployment)  # type: ignore[arg-type]

    assert deployment.seen_es_endpoint == "https://external-es.example"


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
        assert runtime.rtvi_cv_endpoint is None

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
        assert runtime.cosmos_embed_endpoint is None
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


# --------------------------------------------------------------- retired search toggle


@pytest.mark.parametrize("flag", ["--use-attribute-search", "--no-use-attribute-search"])
def test_legacy_attribute_search_flags_are_rejected(flag: str) -> None:
    with pytest.raises(SystemExit):
        _parse_args([flag], operation="run")


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
            '{"query":"forklift","source_type":"video_file"}',
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
    from vss_core.search_core.errors import IndexNotFoundError

    def boom(args, payload=None):
        raise IndexNotFoundError("video_embeddings")

    monkeypatch.setattr("vss_cli.search._build_facade", boom)
    exit_code = run(
        "run",
        ["--json", '{"query":"forklift","source_type":"video_file"}'],
    )
    # Same exit code (3) as the streamed IndexNotFoundError path above.
    assert exit_code == 3


# --------------------------------------------------------------- --stream on non-search (#7 adjacent)


def test_stream_rejected_on_non_search_primitive() -> None:
    with pytest.raises(SystemExit, match="2"):
        run("embed", ["--stream", "--json", "{}"])


# --------------------------------------------------- search-only flags on other primitives (#1)


def test_search_only_flag_rejected_on_embed_search() -> None:
    # --query is a `search`-only flag; embed_search reads --json/stdin. Passing it
    # to embed_search must fail loudly (exit 2), not be silently ignored.
    with pytest.raises(SystemExit, match="2"):
        run("embed", ["--query", "red car", "--json", "{}"])


def test_search_only_list_flag_rejected_on_attribute_search() -> None:
    with pytest.raises(SystemExit, match="2"):
        run("attribute", ["--attribute", "white jacket", "--json", "{}"])


def test_non_search_help_omits_run_only_flags(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        _parse_args(["--help"], operation="embed")
    assert exc.value.code == 0
    help_text = capsys.readouterr().out
    assert "--query" not in help_text
    assert "--stream" not in help_text


def test_search_primitive_still_accepts_search_flags() -> None:
    # The guard only fires for non-search primitives; `search` must be unaffected.
    args = _parse_search_args(["--query", "forklift", "--top-k", "5"])
    payload = _build_archive_search_payload(args)
    assert payload["query"] == "forklift"
    assert payload["top_k"] == 5


# --------------------------------------------------------------- analyzer gating (#8)


def _runtime() -> SearchRuntime:
    return SearchRuntime.from_kwargs(
        es_endpoint="http://es:9200",
        cosmos_embed_endpoint="http://embed:8017",
        rtvi_cv_endpoint="http://cv:9000",
        vst_internal_url="http://vst:30888",
        vst_external_url="http://vst:7777",
    )


def test_named_source_resolution_refuses_unavailable_source(monkeypatch: pytest.MonkeyPatch) -> None:
    async def sources(_endpoint: str) -> dict[str, str]:
        return {"warehouse-camera": "stream-1"}

    monkeypatch.setattr("vss_core.vst.get_name_to_stream_id_map", sources)
    payload = {"video_sources": ["airport-camera"]}

    with pytest.raises(ConfigurationError, match="Stop and clarify"):
        asyncio.run(_resolve_named_sources(payload, _runtime()))


def test_dead_vst_during_named_source_resolution_exits_3(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    class FakeFacade:
        runtime = _runtime()

        async def __aenter__(self) -> FakeFacade:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

    async def dead_vst(_endpoint: str) -> dict[str, str]:
        raise VSTError("connection refused")

    monkeypatch.setattr("vss_cli.search._build_facade", lambda *_args, **_kwargs: FakeFacade())
    monkeypatch.setattr("vss_core.vst.get_name_to_stream_id_map", dead_vst)

    exit_code = run(
        "run",
        ["--json", '{"query":"forklift","source_type":"video_file","video_sources":["warehouse-camera"]}'],
    )

    assert exit_code == 3
    assert "backend unreachable: vst: connection refused" in capsys.readouterr().err


def test_named_source_resolution_uses_only_unambiguous_normalized_match(monkeypatch: pytest.MonkeyPatch) -> None:
    async def sources(_endpoint: str) -> dict[str, str]:
        return {"Warehouse Camera 3": "stream-3"}

    monkeypatch.setattr("vss_core.vst.get_name_to_stream_id_map", sources)
    payload = {"video_sources": ["warehouse-camera-3"]}

    asyncio.run(_resolve_named_sources(payload, _runtime()))
    assert payload["video_sources"] == ["Warehouse Camera 3"]


def test_embed_model_preflight_never_auto_selects_an_available_id(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    class FakeClient:
        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, url: str) -> httpx.Response:
            return httpx.Response(200, json={"data": [{"id": "different-model"}]}, request=httpx.Request("GET", url))

    monkeypatch.setattr("vss_cli.search.httpx.AsyncClient", lambda **_kwargs: FakeClient())

    with pytest.raises(ConfigurationError, match="different-model"):
        asyncio.run(_preflight_embed_model(_runtime()))


def test_embed_model_preflight_retries_transient_statuses(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    calls = 0
    delays: list[float] = []

    class FakeClient:
        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, url: str) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls < 3:
                return httpx.Response(503, request=httpx.Request("GET", url))
            return httpx.Response(
                200,
                json={"data": [{"id": _runtime().cosmos_embed_model}]},
                request=httpx.Request("GET", url),
            )

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr("vss_cli.search.httpx.AsyncClient", lambda **_kwargs: FakeClient())
    monkeypatch.setattr("vss_cli.search.asyncio.sleep", record_sleep)

    asyncio.run(_preflight_embed_model(_runtime()))

    assert calls == 3
    assert delays == [0.25, 0.5]


def test_embed_model_preflight_does_not_retry_semantic_404(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    calls = 0

    class FakeClient:
        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, url: str) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(404, request=httpx.Request("GET", url))

    monkeypatch.setattr("vss_cli.search.httpx.AsyncClient", lambda **_kwargs: FakeClient())

    with pytest.raises(BackendUnreachableError):
        asyncio.run(_preflight_embed_model(_runtime()))

    assert calls == 1


def test_rtsp_index_preflight_uses_runtime_wildcard(monkeypatch: pytest.MonkeyPatch) -> None:
    from vss_core.search_core.clients.elastic import ElasticClient

    class FakeElastic:
        def __init__(self) -> None:
            self.index: str | list[str] | None = None

        async def search(self, *, index: str | list[str], body: object) -> dict[str, object]:
            self.index = index
            assert body == {"size": 0, "query": {"match_all": {}}}
            return {"_shards": {"total": 1}, "hits": {"hits": []}}

    elastic = FakeElastic()
    monkeypatch.setattr(ElasticClient, "from_runtime", classmethod(lambda _cls, _runtime: elastic))

    asyncio.run(_preflight_index(_runtime(), source_type="rtsp"))

    assert elastic.index == ["mdx-embed-filtered-*", "-mdx-embed-filtered-2025-01-01"]


def test_rtsp_index_preflight_rejects_expression_with_no_concrete_shards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from vss_core.search_core.clients.elastic import ElasticClient
    from vss_core.search_core.errors import IndexNotFoundError

    class FakeElastic:
        async def search(self, *, index: str | list[str], body: object) -> dict[str, object]:
            return {"_shards": {"total": 0}, "hits": {"hits": []}}

    monkeypatch.setattr(ElasticClient, "from_runtime", classmethod(lambda _cls, _runtime: FakeElastic()))

    with pytest.raises(IndexNotFoundError):
        asyncio.run(_preflight_index(_runtime(), source_type="rtsp"))


def test_rtsp_index_preflight_preserves_target_in_candidate_diagnostic(monkeypatch: pytest.MonkeyPatch) -> None:
    from vss_core.search_core.clients.elastic import ElasticClient
    from vss_core.search_core.errors import IndexNotFoundError

    target = ["mdx-embed-filtered-*", "-mdx-embed-filtered-2025-01-01"]

    class FakeIndices:
        async def get_alias(self, *, index: str) -> dict[str, object]:
            assert index == "mdx-embed-filtered-*"
            return {"mdx-embed-filtered-2025-02-01": {}}

    class FakeElastic:
        raw = type("Raw", (), {"indices": FakeIndices()})()

        async def search(self, *, index: str | list[str], body: object) -> dict[str, object]:
            assert index == target
            assert body == {"size": 0, "query": {"match_all": {}}}
            raise IndexNotFoundError(index)

    monkeypatch.setattr(ElasticClient, "from_runtime", classmethod(lambda _cls, _runtime: FakeElastic()))

    with pytest.raises(IndexNotFoundError) as error:
        asyncio.run(_preflight_index(_runtime(), source_type="rtsp"))

    assert error.value.index == target
    assert error.value.available_indices == ("mdx-embed-filtered-2025-02-01",)


def test_invalid_embed_payload_fails_before_model_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    async def unexpected_preflight(_runtime: SearchRuntime) -> None:
        pytest.fail("embed model preflight must not run for invalid input")

    monkeypatch.setattr("vss_cli.search._preflight_embed_model", unexpected_preflight)
    exit_code = run(
        "embed",
        [
            "--es-endpoint",
            "http://es:9200",
            "--cosmos-embed-endpoint",
            "http://embed:8017",
            "--vst-internal-url",
            "http://vst:30888",
            "--vst-external-url",
            "http://vst.example",
            "--json",
            "{}",
        ],
    )

    assert exit_code == 2


def test_invalid_attribute_payload_fails_before_rtvi_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    async def unexpected_preflight(*_args: object) -> None:
        pytest.fail("RTVI-CV preflight must not run for invalid input")

    monkeypatch.setattr("vss_cli.search._preflight_rtvi_cv", unexpected_preflight)
    exit_code = run(
        "attribute",
        [
            "--es-endpoint",
            "http://es:9200",
            "--rtvi-cv-endpoint",
            "http://cv:9000",
            "--vst-internal-url",
            "http://vst:30888",
            "--vst-external-url",
            "http://vst.example",
            "--json",
            "{}",
        ],
    )

    assert exit_code == 2


def test_invalid_payload_fails_before_deployment_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_discovery(*_args: object, **_kwargs: object) -> None:
        pytest.fail("deployment discovery must not run for invalid input")

    monkeypatch.setattr("vss_cli.search.discover_deployment", unexpected_discovery)

    exit_code = run(
        "embed",
        [
            "--deployment",
            "kubernetes",
            "--namespace",
            "vss",
            "--release",
            "search",
            "--json",
            "{}",
        ],
    )

    assert exit_code == 2


def test_rtvi_cv_fallback_is_only_used_when_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    class FakeClient:
        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, url: str, **_kwargs: object) -> httpx.Response:
            return httpx.Response(404, request=httpx.Request("POST", url))

    monkeypatch.setattr("vss_cli.search.httpx.AsyncClient", lambda **_kwargs: FakeClient())
    payload = {"attributes": ["white jacket"], "search_mode": "fusion"}
    args = argparse.Namespace(primitive="search", allow_embed_only_fallback=True)

    asyncio.run(_preflight_rtvi_cv(args, payload, _runtime()))
    assert payload == {"attributes": [], "search_mode": "embed"}


def test_rtvi_cv_preflight_retries_transport_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    calls = 0

    class FakeClient:
        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, url: str, **_kwargs: object) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise httpx.ConnectError("service restarting", request=httpx.Request("POST", url))
            return httpx.Response(200, json={"data": [[0.1, 0.2]]}, request=httpx.Request("POST", url))

    async def no_wait(_delay: float) -> None:
        return None

    monkeypatch.setattr("vss_cli.search.httpx.AsyncClient", lambda **_kwargs: FakeClient())
    monkeypatch.setattr("vss_cli.search.asyncio.sleep", no_wait)
    payload = {"attributes": ["white jacket"], "search_mode": "fusion"}
    args = argparse.Namespace(primitive="search", allow_embed_only_fallback=False)

    asyncio.run(_preflight_rtvi_cv(args, payload, _runtime()))

    assert calls == 2


def test_request_runtime_fields_exclude_unused_kubernetes_services() -> None:
    args = _parse_search_args(["--query", "forklift"])

    fields = _runtime_fields_for_request(args, _build_archive_search_payload(args), _runtime())

    assert fields == {"es_endpoint", "cosmos_embed_endpoint", "vst_external_url"}


def test_pydantic_normalization_drives_route_planning() -> None:
    args = _parse_search_args(["--query", "forklift"])
    payload = {
        "query": "forklift",
        "source_type": "video_file",
        "attributes": ["white jacket"],
        "search_mode": "fusion",
    }

    _validate_payload_before_preflight(args, payload)
    fields = _runtime_fields_for_request(args, payload, _runtime())

    assert payload["search_mode"] == "fusion"
    assert {"es_endpoint", "cosmos_embed_endpoint", "behavior_es_endpoint", "rtvi_cv_endpoint"} <= fields
    assert "vlm_base_url" not in fields


def test_rtvi_port_forward_failure_uses_explicit_embed_fallback(capsys: pytest.CaptureFixture[str]) -> None:
    args = _parse_search_args(["--query", "forklift", "--allow-embed-only-fallback"])
    payload = {
        "query": "forklift",
        "source_type": "video_file",
        "attributes": ["white jacket"],
        "search_mode": "attribute",
    }
    _validate_payload_before_preflight(args, payload)
    calls: list[set[str]] = []

    class FailingRTVIDeployment:
        def rewrite_runtime(self, runtime: SearchRuntime, *, fields: set[str]) -> SearchRuntime:
            calls.append(fields)
            if fields == {"rtvi_cv_endpoint"}:
                raise PortForwardError("no ready RTVI-CV pod")
            return runtime

    _rewrite_deployment_runtime(args, payload, _runtime(), FailingRTVIDeployment())  # type: ignore[arg-type]

    assert payload["attributes"] == []
    assert payload["search_mode"] == "embed"
    assert calls == [
        {"rtvi_cv_endpoint"},
        {"es_endpoint", "cosmos_embed_endpoint", "vst_external_url"},
    ]
    assert "explicit embed-only fallback" in capsys.readouterr().err


def test_attribute_fallback_recomputes_embed_preflights(monkeypatch: pytest.MonkeyPatch) -> None:
    args = _parse_search_args(["--query", "forklift", "--allow-embed-only-fallback"])
    payload = {
        "query": "forklift",
        "source_type": "video_file",
        "attributes": ["white jacket"],
        "search_mode": "attribute",
    }
    calls: list[str] = []

    async def fallback(_args: argparse.Namespace, current: dict[str, object], _runtime: SearchRuntime) -> None:
        calls.append("rtvi")
        current["attributes"] = []
        current["search_mode"] = "embed"

    async def index(_runtime: SearchRuntime, *, source_type: str) -> None:
        calls.append(f"index:{source_type}")

    async def model(_runtime: SearchRuntime) -> None:
        calls.append("model")

    monkeypatch.setattr("vss_cli.search._preflight_rtvi_cv", fallback)
    monkeypatch.setattr("vss_cli.search._preflight_index", index)
    monkeypatch.setattr("vss_cli.search._preflight_embed_model", model)

    asyncio.run(_preflight_search_runtime(args, payload, _runtime()))

    assert calls == ["rtvi", "index:video_file", "model"]


def test_single_word_attributes_use_attribute_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    args = _parse_search_args(["--query", "forklift", "--search-mode", "attribute", "--attribute", "red"])
    payload = _build_archive_search_payload(args)
    calls: list[str] = []

    async def rtvi(*_args: object) -> None:
        calls.append("rtvi")

    async def index(_runtime: SearchRuntime, *, source_type: str) -> None:
        calls.append(f"index:{source_type}")

    async def model(_runtime: SearchRuntime) -> None:
        calls.append("model")

    monkeypatch.setattr("vss_cli.search._preflight_rtvi_cv", rtvi)
    monkeypatch.setattr("vss_cli.search._preflight_index", index)
    monkeypatch.setattr("vss_cli.search._preflight_embed_model", model)

    asyncio.run(_preflight_search_runtime(args, payload, _runtime()))

    assert calls == ["rtvi"]


def test_object_id_search_does_not_preflight_embed_or_rtvi(monkeypatch: pytest.MonkeyPatch) -> None:
    args = _parse_search_args(["--query", "forklift", "--search-mode", "object", "--object-id", "42"])
    payload = _build_archive_search_payload(args)

    async def unexpected(*_args: object, **_kwargs: object) -> None:
        pytest.fail("object-id routing must not contact embed or RTVI services")

    monkeypatch.setattr("vss_cli.search._preflight_rtvi_cv", unexpected)
    monkeypatch.setattr("vss_cli.search._preflight_index", unexpected)
    monkeypatch.setattr("vss_cli.search._preflight_embed_model", unexpected)

    asyncio.run(_preflight_search_runtime(args, payload, _runtime()))
