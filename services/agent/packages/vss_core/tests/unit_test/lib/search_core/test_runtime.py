# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Contract tests for lib.search_core.runtime.

Covers the four invariants that the rest of the library depends on:
  - SearchRuntime.from_env reads the env names this repo actually injects.
  - _interpolate implements shell `:-` semantics (empty-string treated as unset).
  - RuntimeSnapshot.from_dict ignores legacy/unknown forward-compat fields.
  - VLM fields are Optional — embed-only flows must work without them.
"""

from __future__ import annotations

import pytest

from vss_core.search_core import RuntimeSnapshot
from vss_core.search_core import SearchRuntime
from vss_core.search_core.errors import BackendUnreachableError
from vss_core.search_core.errors import ConfigurationError
from vss_core.search_core.runtime import _interpolate

# --------------------------------------------------------------- _interpolate


class TestInterpolate:
    """`:-` semantics — empty-string vars must resolve to default."""

    def test_default_fires_when_var_unset(self) -> None:
        assert _interpolate("${X:-fallback}", {}) == "fallback"

    def test_default_fires_when_var_empty_string(self) -> None:
        # Plain env.get would return "" here; the helper must treat empty as unset.
        assert _interpolate("${X:-fallback}", {"X": ""}) == "fallback"

    def test_non_empty_value_overrides_default(self) -> None:
        assert _interpolate("${X:-fallback}", {"X": "set"}) == "set"

    def test_newline_in_value_is_rejected(self) -> None:
        # A value with an embedded newline could inject arbitrary YAML keys.
        with pytest.raises(ConfigurationError, match="newline"):
            _interpolate("es: ${X}", {"X": "http://es:9200\ninjected: true"})

    def test_carriage_return_in_value_is_rejected(self) -> None:
        with pytest.raises(ConfigurationError, match="newline"):
            _interpolate("es: ${X}", {"X": "a\rb"})

    def test_no_default_form_unset_resolves_to_empty(self) -> None:
        assert _interpolate("${X}", {}) == ""

    def test_no_default_form_set_returns_value(self) -> None:
        assert _interpolate("${X}", {"X": "val"}) == "val"

    def test_multiple_substitutions_in_one_string(self) -> None:
        env = {"HOST_IP": "10.0.0.1"}
        out = _interpolate("http://${HOST_IP}:${PORT:-8017}", env)
        assert out == "http://10.0.0.1:8017"


# --------------------------------------------------------------- from_env


_HELM_ENV = {
    "ELASTIC_SEARCH_ENDPOINT": "http://elasticsearch:9200",
    "ELASTIC_SEARCH_INDEX": "video_embeddings",
    "RTVI_CV_BASE_URL": "http://vss-rtvi-cv:9000",
    "COSMOS_EMBED_ENDPOINT": "http://vss-rtvi-embed:8017",
    "VST_INTERNAL_URL": "http://vss-vios-ingress:30888",
    "VST_EXTERNAL_URL": "https://vss.example.com",
    "RTVI_EMBED_MODEL": "cosmos-embed1-448p-anomaly-detection",
}


_DOCKER_ENV = {
    "ELASTIC_SEARCH_ENDPOINT": "http://10.0.0.1:9200",
    "ELASTIC_SEARCH_INDEX": "video_embeddings",
    "HOST_IP": "10.0.0.1",
    "RTVI_CV_PORT": "9000",
    "RTVI_EMBED_PORT": "8017",
    "VST_INTERNAL_URL": "http://10.0.0.1:30888",
    "VST_EXTERNAL_URL": "http://10.0.0.1:7777",
}


class TestFromEnv:
    """SearchRuntime.from_env reads the env names this repo actually injects."""

    def test_helm_env_works(self) -> None:
        rt = SearchRuntime.from_env(_HELM_ENV)
        assert rt.rtvi_cv_endpoint == "http://vss-rtvi-cv:9000"
        assert rt.cosmos_embed_endpoint == "http://vss-rtvi-embed:8017"
        assert rt.es_endpoint == "http://elasticsearch:9200"
        assert rt.video_embed_index == "video_embeddings"
        assert rt.cosmos_embed_model == "cosmos-embed1-448p-anomaly-detection"

    def test_docker_env_assembles_rtvi_cv_from_host_ip(self) -> None:
        rt = SearchRuntime.from_env(_DOCKER_ENV)
        assert rt.rtvi_cv_endpoint == "http://10.0.0.1:9000"
        assert rt.cosmos_embed_endpoint == "http://10.0.0.1:8017"

    def test_docker_env_prefers_exported_rtvi_cv_endpoint(self) -> None:
        env = {**_DOCKER_ENV, "RTVI_CV_ENDPOINT": "http://vss-rtvi-cv:9000"}
        rt = SearchRuntime.from_env(env)
        assert rt.rtvi_cv_endpoint == "http://vss-rtvi-cv:9000"

    def test_missing_rtvi_cv_raises(self) -> None:
        env = dict(_HELM_ENV)
        env.pop("RTVI_CV_BASE_URL")
        # No HOST_IP/RTVI_CV_PORT either → can't assemble
        with pytest.raises(ConfigurationError, match="RTVI CV"):
            SearchRuntime.from_env(env)

    def test_missing_embed_endpoint_raises(self) -> None:
        env = dict(_HELM_ENV)
        env.pop("COSMOS_EMBED_ENDPOINT")
        with pytest.raises(ConfigurationError, match="Embed endpoint"):
            SearchRuntime.from_env(env)


class TestRuntimeSnapshot:
    """RuntimeSnapshot.from_dict shape contract for /api/v1/runtime/config consumers."""

    def _base_payload(self) -> dict:
        return {
            "es_endpoint": "http://es:9200",
            "cosmos_embed_endpoint": "http://embed:8017",
            "rtvi_cv_endpoint": "http://cv:9000",
            "vst_internal_url": "http://vst:30888",
            "vst_external_url": "http://vst:7777",
        }

    def test_ignores_legacy_nested_search_block(self) -> None:
        snap = RuntimeSnapshot.from_dict({**self._base_payload(), "search": {"use_attribute_search": True}})
        assert snap.runtime.es_endpoint == "http://es:9200"

    def test_missing_search_block_is_valid(self) -> None:
        snap = RuntimeSnapshot.from_dict(self._base_payload())
        assert snap.runtime.es_endpoint == "http://es:9200"

    def test_ignores_unknown_forward_compat_fields(self) -> None:
        """Older hosts must tolerate newer agents that add fields."""
        snap = RuntimeSnapshot.from_dict({**self._base_payload(), "future_field_we_dont_know_about": "shrug"})
        assert snap.runtime.es_endpoint == "http://es:9200"

    def test_from_config_file_ignores_legacy_search_toggle(self, tmp_path) -> None:
        config = tmp_path / "config.yml"
        config.write_text(
            """
functions:
  search:
    use_attribute_search: true
    default_max_results: 7
  embed_search:
    es_endpoint: ${ELASTIC_SEARCH_ENDPOINT}
    es_index: video_embeddings
    vst_internal_url: ${VST_INTERNAL_URL}
    vst_external_url: ${VST_EXTERNAL_URL}
    default_max_results: 100
  attribute_search:
    rtvi_cv_endpoint: ${RTVI_CV_BASE_URL}
""",
        )

        snap = RuntimeSnapshot.from_config_file(config, env=_HELM_ENV)

        assert snap.runtime.default_max_results == 7
        assert snap.runtime.es_endpoint == _HELM_ENV["ELASTIC_SEARCH_ENDPOINT"]

    def test_from_config_file_carries_custom_index_contract(self, tmp_path) -> None:
        config = tmp_path / "config.yml"
        config.write_text(
            """
functions:
  embed_search:
    es_endpoint: http://es:9200
    es_index: tenant-video
    es_index_wildcard: tenant-video-*
    cosmos_embed_endpoint: http://embed:8017
    vst_internal_url: http://vst:30888
    vst_external_url: http://vst.external
  attribute_search:
    rtvi_cv_endpoint: http://cv:9000
    behavior_index: tenant-behavior
    behavior_index_wildcard: tenant-behavior-*
    frames_index: tenant-raw
    frames_index_wildcard: tenant-raw-*
""",
        )

        runtime = RuntimeSnapshot.from_config_file(config, env={}).runtime

        assert runtime.video_embed_index == "tenant-video"
        assert runtime.video_embed_index_wildcard == "tenant-video-*"
        assert runtime.behavior_index == "tenant-behavior"
        assert runtime.behavior_index_wildcard == "tenant-behavior-*"
        assert runtime.frames_index == "tenant-raw"
        assert runtime.frames_index_wildcard == "tenant-raw-*"

    def test_from_config_file_empty_env_mapping_does_not_read_process_env(self, monkeypatch, tmp_path) -> None:
        for key, value in _HELM_ENV.items():
            monkeypatch.setenv(key, value)
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
        )

        with pytest.raises(ConfigurationError, match="Required runtime value 'es_endpoint'"):
            RuntimeSnapshot.from_config_file(config, env={})

    def test_from_config_file_accepts_literal_values_with_empty_env(self, tmp_path) -> None:
        config = tmp_path / "config.yml"
        config.write_text(
            """
functions:
  embed_search:
    es_endpoint: http://es:9200
    cosmos_embed_endpoint: http://embed:8017
    vst_internal_url: http://vst:30888
    vst_external_url: http://vst.external
  attribute_search:
    rtvi_cv_endpoint: http://cv:9000
""",
        )

        snap = RuntimeSnapshot.from_config_file(config, env={})

        assert snap.runtime.es_endpoint == "http://es:9200"
        assert snap.runtime.cosmos_embed_endpoint == "http://embed:8017"


class TestConfigLoadErrors:
    """Malformed / unreadable config maps to ConfigurationError (exit 4), not exit 1."""

    def test_malformed_yaml_raises_configuration_error(self, tmp_path) -> None:
        config = tmp_path / "config.yml"
        # Unbalanced brackets → yaml.YAMLError, which must become ConfigurationError.
        config.write_text("functions: {search: [unclosed\n")
        with pytest.raises(ConfigurationError, match="parse YAML"):
            RuntimeSnapshot.from_config_file(config, env={})

    def test_directory_as_config_raises_configuration_error(self, tmp_path) -> None:
        directory = tmp_path / "a_directory"
        directory.mkdir()
        # Path.read_text on a directory raises IsADirectoryError (an OSError).
        with pytest.raises(ConfigurationError, match="could not read config file"):
            RuntimeSnapshot.from_config_file(directory, env={})

    def test_missing_file_raises_configuration_error(self, tmp_path) -> None:
        with pytest.raises(ConfigurationError, match="could not read config file"):
            RuntimeSnapshot.from_config_file(tmp_path / "does-not-exist.yml", env={})


# --------------------------------------------------------------- from_remote


def _patch_httpx_with_handler(monkeypatch, handler) -> None:
    """Route from_remote's httpx.Client through a MockTransport running `handler`."""
    import httpx

    real_client = httpx.Client

    def factory(**kwargs):
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "Client", factory)


def _valid_remote_payload() -> dict:
    return {
        "es_endpoint": "http://es:9200",
        "cosmos_embed_endpoint": "http://embed:8017",
        "rtvi_cv_endpoint": "http://cv:9000",
        "vst_internal_url": "http://vst:30888",
        "vst_external_url": "http://vst:7777",
        "search": {"use_attribute_search": True},
    }


class TestFromRemote:
    """from_remote maps transport/HTTP/JSON failures onto library errors."""

    def test_success_returns_snapshot(self, monkeypatch) -> None:
        import httpx

        def handler(request):
            return httpx.Response(200, json=_valid_remote_payload())

        _patch_httpx_with_handler(monkeypatch, handler)
        snap = RuntimeSnapshot.from_remote("http://agent:8000")
        assert snap.runtime.es_endpoint == "http://es:9200"

    def test_connection_error_maps_to_backend_unreachable(self, monkeypatch) -> None:
        import httpx

        def handler(request):
            raise httpx.ConnectError("refused", request=request)

        _patch_httpx_with_handler(monkeypatch, handler)
        with pytest.raises(BackendUnreachableError) as excinfo:
            RuntimeSnapshot.from_remote("http://agent:8000")
        assert excinfo.value.backend == "agent"
        assert isinstance(excinfo.value.__cause__, httpx.ConnectError)

    def test_timeout_maps_to_backend_unreachable(self, monkeypatch) -> None:
        import httpx

        def handler(request):
            raise httpx.ConnectTimeout("slow", request=request)

        _patch_httpx_with_handler(monkeypatch, handler)
        with pytest.raises(BackendUnreachableError) as excinfo:
            RuntimeSnapshot.from_remote("http://agent:8000")
        assert excinfo.value.backend == "agent"

    def test_non_200_maps_to_configuration_error(self, monkeypatch) -> None:
        import httpx

        def handler(request):
            return httpx.Response(503, text="unavailable")

        _patch_httpx_with_handler(monkeypatch, handler)
        with pytest.raises(ConfigurationError, match="HTTP 503"):
            RuntimeSnapshot.from_remote("http://agent:8000")

    def test_invalid_json_maps_to_configuration_error(self, monkeypatch) -> None:
        import httpx

        def handler(request):
            return httpx.Response(200, text="not json")

        _patch_httpx_with_handler(monkeypatch, handler)
        with pytest.raises(ConfigurationError, match="invalid JSON"):
            RuntimeSnapshot.from_remote("http://agent:8000")

    def test_search_runtime_from_remote_returns_flat_runtime(self, monkeypatch) -> None:
        import httpx

        def handler(request):
            return httpx.Response(200, json=_valid_remote_payload())

        _patch_httpx_with_handler(monkeypatch, handler)
        rt = SearchRuntime.from_remote("http://agent:8000")
        assert rt.es_endpoint == "http://es:9200"


# --------------------------------------------------------------- from_dict validation & coercion


class TestFromDictValidation:
    """A partial or stringly-typed payload must map to library errors, not TypeError/str fields."""

    def test_missing_required_fields_raises_configuration_error(self) -> None:
        with pytest.raises(ConfigurationError) as excinfo:
            RuntimeSnapshot.from_dict({"es_endpoint": "http://es:9200"})
        message = str(excinfo.value)
        # It should name the absent required fields rather than raising TypeError.
        assert "cosmos_embed_endpoint" in message
        assert "rtvi_cv_endpoint" in message
        assert "vst_internal_url" in message
        assert "vst_external_url" in message

    def test_partial_payload_does_not_raise_typeerror(self) -> None:
        # Regression: bare SearchRuntime.from_kwargs(**partial) used to raise TypeError.
        with pytest.raises(ConfigurationError):
            RuntimeSnapshot.from_dict({})

    def _full_payload(self, **overrides) -> dict:
        payload = {
            "es_endpoint": "http://es:9200",
            "cosmos_embed_endpoint": "http://embed:8017",
            "rtvi_cv_endpoint": "http://cv:9000",
            "vst_internal_url": "http://vst:30888",
            "vst_external_url": "http://vst:7777",
        }
        payload.update(overrides)
        return payload

    def test_stringly_typed_int_is_coerced(self) -> None:
        snap = RuntimeSnapshot.from_dict(self._full_payload(default_max_results="7"))
        assert snap.runtime.default_max_results == 7
        assert isinstance(snap.runtime.default_max_results, int)

    def test_stringly_typed_float_is_coerced(self) -> None:
        snap = RuntimeSnapshot.from_dict(self._full_payload(embed_confidence_threshold="0.25"))
        assert snap.runtime.embed_confidence_threshold == pytest.approx(0.25)

    def test_bad_int_raises_configuration_error(self) -> None:
        with pytest.raises(ConfigurationError, match="default_max_results"):
            RuntimeSnapshot.from_dict(self._full_payload(default_max_results="not-a-number"))

    def test_fractional_integer_is_rejected(self) -> None:
        with pytest.raises(ConfigurationError, match="default_max_results"):
            RuntimeSnapshot.from_dict(self._full_payload(default_max_results=1.9))

    def test_non_object_payload_is_rejected(self) -> None:
        with pytest.raises(ConfigurationError, match="JSON object"):
            RuntimeSnapshot.from_dict([])  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("default_max_results", 0),
            ("request_timeout_seconds", 0),
            ("rrf_k", 0),
            ("top_percent_filter", 1.0),
            ("fusion_method", "unknown"),
        ],
    )
    def test_invalid_runtime_knob_is_rejected(self, field, value) -> None:
        with pytest.raises(ConfigurationError, match=field):
            RuntimeSnapshot.from_dict(self._full_payload(**{field: value}))


class TestNumericCoercion:
    def test_non_mapping_function_block_is_rejected(self, tmp_path) -> None:
        config = tmp_path / "config.yml"
        config.write_text("functions:\n  search: invalid\n")
        with pytest.raises(ConfigurationError, match="must be a mapping"):
            SearchRuntime.from_config_file(config, env={})

    def test_from_config_file_coerces_quoted_numbers(self, tmp_path) -> None:
        config = tmp_path / "config.yml"
        config.write_text(
            """
functions:
  embed_search:
    es_endpoint: http://es:9200
    cosmos_embed_endpoint: http://embed:8017
    vst_internal_url: http://vst:30888
    vst_external_url: http://vst.external
  attribute_search:
    rtvi_cv_endpoint: http://cv:9000
  search:
    default_max_results: "9"
    w_attribute: "0.6"
""",
        )
        snap = RuntimeSnapshot.from_config_file(config, env={})
        assert snap.runtime.default_max_results == 9
        assert snap.runtime.w_attribute == pytest.approx(0.6)

    def test_from_config_file_bad_number_raises_configuration_error(self, tmp_path) -> None:
        config = tmp_path / "config.yml"
        config.write_text(
            """
functions:
  embed_search:
    es_endpoint: http://es:9200
    cosmos_embed_endpoint: http://embed:8017
    vst_internal_url: http://vst:30888
    vst_external_url: http://vst.external
  attribute_search:
    rtvi_cv_endpoint: http://cv:9000
  search:
    rrf_k: not-a-number
""",
        )
        with pytest.raises(ConfigurationError, match="rrf_k"):
            RuntimeSnapshot.from_config_file(config, env={})


def test_raw_index_aliases_frames_index() -> None:
    """The RUNTIME_JSON contract key is ``raw_index``; the field is ``frames_index``.

    Both attribute names must resolve so an improvised resolver reaching for
    ``runtime.raw_index`` (as eval agents do) is not an AttributeError trap.
    """
    rt = SearchRuntime.from_kwargs(
        es_endpoint="http://es:9200",
        cosmos_embed_endpoint="http://embed:8000",
        vst_external_url="https://vst.example",
        video_embed_index="mdx-embed-filtered-2025-01-01",
        frames_index="mdx-raw-2025-01-01",
    )
    assert rt.raw_index == "mdx-raw-2025-01-01"
    assert rt.raw_index == rt.frames_index
