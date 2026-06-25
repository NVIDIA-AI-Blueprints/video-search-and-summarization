# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Contract tests for lib.search_core.runtime.

Covers the four invariants that the rest of the library depends on:
  - SearchRuntime.from_env reads the env names this repo actually injects.
  - _interpolate implements shell `:-` semantics (empty-string treated as unset).
  - RuntimeSnapshot.from_dict separates the nested `search` block and
    tolerates unknown forward-compat fields.
  - VLM fields are Optional — embed-only flows must work without them.
"""

from __future__ import annotations

import pytest

from lib.search_core import RuntimeSnapshot
from lib.search_core import SearchOptions
from lib.search_core import SearchRuntime
from lib.search_core.errors import ConfigurationError
from lib.search_core.runtime import _interpolate

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

    def test_vlm_fields_optional(self) -> None:
        """from_env must not require VLM_BASE_URL / VLM_NAME.

        Embed-only workflows must work without these. The lazy facade
        validates VLM presence only when critic() is exercised.
        """
        rt = SearchRuntime.from_env(_HELM_ENV)  # no VLM_BASE_URL / VLM_NAME
        assert rt.vlm_base_url is None
        assert rt.vlm_model_name is None

    def test_vlm_api_key_selected_by_model_type(self) -> None:
        env = dict(_HELM_ENV)
        env["VLM_MODEL_TYPE"] = "openai"
        env["OPENAI_API_KEY"] = "sk-openai"
        env["NVIDIA_API_KEY"] = "nvapi-should-not-be-picked"
        env["VLM_BASE_URL"] = "https://api.openai.com"
        env["VLM_NAME"] = "gpt-4o"
        rt = SearchRuntime.from_env(env)
        assert rt.vlm_api_key is not None
        assert rt.vlm_api_key.get_secret_value() == "sk-openai"


# --------------------------------------------------------------- RuntimeSnapshot


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

    def test_extracts_nested_search_block(self) -> None:
        snap = RuntimeSnapshot.from_dict({**self._base_payload(), "search": {"use_attribute_search": True}})
        assert snap.search.use_attribute_search is True
        assert snap.runtime.es_endpoint == "http://es:9200"

    def test_missing_search_block_defaults_to_false(self) -> None:
        snap = RuntimeSnapshot.from_dict(self._base_payload())
        assert snap.search.use_attribute_search is False

    def test_ignores_unknown_forward_compat_fields(self) -> None:
        """Older hosts must tolerate newer agents that add fields."""
        snap = RuntimeSnapshot.from_dict({**self._base_payload(), "future_field_we_dont_know_about": "shrug"})
        assert snap.runtime.es_endpoint == "http://es:9200"

    def test_search_options_default(self) -> None:
        assert SearchOptions().use_attribute_search is False

    def test_from_config_file_carries_search_options(self, tmp_path) -> None:
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

        assert snap.search.use_attribute_search is True
        assert snap.runtime.default_max_results == 7
        assert snap.runtime.embed_default_max_results == 100
        assert snap.runtime.es_endpoint == _HELM_ENV["ELASTIC_SEARCH_ENDPOINT"]

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

    def test_from_config_file_carries_critic_and_vlm_media_knobs(self, tmp_path) -> None:
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
    enable_critic: true
  critic_agent:
    time_format: offset
    num_videos_to_evaluate: 3
  video_understanding:
    max_frames: 12
    max_fps: 4
  vst_video_clip:
    enable_audio: true
""",
        )

        snap = RuntimeSnapshot.from_config_file(config, env={})

        assert snap.runtime.critic_time_format == "offset"
        assert snap.runtime.critic_evaluation_count == 3
        assert snap.runtime.vlm_max_frames == 12
        assert snap.runtime.vlm_max_fps == 4
        assert snap.runtime.vst_clip_enable_audio is True
