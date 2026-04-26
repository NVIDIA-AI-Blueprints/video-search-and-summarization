# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Tests for vss_agents/orchestrator/docker_compose_util.py."""

from pathlib import Path

import pytest
import yaml

from vss_agents.orchestrator import docker_compose_util as dcu


def _env_text(*lines: str) -> str:
    return "\n".join(lines)


def _make_recipe(
    tmp_path: Path,
    env_text: str,
    *,
    profile: str = dcu.PROFILE_BASE,
    env_overrides: dict[str, str] | None = None,
    ngc_cli_api_key: str | None = None,
    nvidia_api_key: str | None = None,
) -> dcu.DryRunRecipe:
    deployments_dir = tmp_path / "deployments"
    deployments_dir.mkdir()
    mdx_data_dir = tmp_path / "mdx-data"
    mdx_data_dir.mkdir()
    source_env_file = tmp_path / "profile.env"
    source_env_file.write_text(env_text.strip() + "\n")

    return dcu.DryRunRecipe(
        profile=profile,  # type: ignore[arg-type]
        env_overrides=env_overrides or {},
        ngc_cli_api_key=ngc_cli_api_key,
        nvidia_api_key=nvidia_api_key,
        output_env_file=tmp_path / "generated.env",
        output_compose_file=tmp_path / "docker-compose.generated.yml",
        deployments_dir=deployments_dir,
        mdx_data_dir=mdx_data_dir,
        compose_file=tmp_path / "compose.yml",
        source_env_file=source_env_file,
        supported_hardware_profiles=frozenset({"igx", "thor"}),
        edge_hardware_profiles=frozenset({"igx"}),
        edge_allowed_profiles=frozenset({dcu.PROFILE_SEARCH}),
        edge_device_ids={"llm": "0", "vlm": "1"},
        thor_base_profiles=frozenset({"thor"}),
        supported_llm_models={"llm-a": "llm-a-slug"},
        supported_vlm_models={"vlm-a": "vlm-a-slug"},
        thor_base_vlm_overrides={"VLM_NAME": "vlm-a"},
    )


class TestParseEnvOverrides:
    def test_parse_env_overrides_accepts_valid_entries(self):
        result = dcu.parse_env_overrides(["HOST_IP=10.0.0.5", "PASSWORD=a=b=c"])  # pragma: allowlist secret
        assert result == {"HOST_IP": "10.0.0.5", "PASSWORD": "a=b=c"}  # pragma: allowlist secret

    def test_parse_env_overrides_rejects_missing_equals(self):
        with pytest.raises(dcu.ValidationError, match="Expected KEY=VALUE"):
            dcu.parse_env_overrides(["HOST_IP"])

    def test_parse_env_overrides_rejects_invalid_key(self):
        with pytest.raises(dcu.ValidationError, match="Invalid env key"):
            dcu.parse_env_overrides(["host_ip=10.0.0.5"])

    def test_parse_env_overrides_rejects_newlines(self):
        with pytest.raises(dcu.ValidationError, match="Newlines are not allowed"):
            dcu.parse_env_overrides(["TOKEN=line1\nline2"])  # pragma: allowlist secret


class TestParseEnvFile:
    def test_parse_env_file_ignores_comments_and_strips_quotes(self, tmp_path: Path):
        env_file = tmp_path / "test.env"
        env_file.write_text(
            "\n".join(
                [
                    "# comment",
                    "",
                    "HOST_IP = 10.0.0.5",
                    'DOUBLE="quoted"',
                    "SINGLE='also-quoted'",
                    "BROKEN_LINE",
                ]
            )
            + "\n"
        )

        result = dcu.parse_env_file(env_file)

        assert result == {
            "HOST_IP": "10.0.0.5",
            "DOUBLE": "quoted",
            "SINGLE": "also-quoted",
        }


class TestFirstNonPlaceholder:
    def test_first_non_placeholder_skips_known_placeholders(self):
        result = dcu.first_non_placeholder(
            [
                "",
                "  <HOST_IP>  ",
                "$HOST_IP",
                "${HOST_IP}",
                "http://${HOST_IP}:30888",
                "/path/to/deployments",
                "10.0.0.5",
            ]
        )

        assert result == "10.0.0.5"

    def test_first_non_placeholder_returns_empty_when_all_values_are_placeholders(self):
        assert dcu.first_non_placeholder(["", "   ", "<HOST_IP>", "${HOST_IP}"]) == ""


class TestSanitizeResolvedCompose:
    def test_sanitize_resolved_compose_removes_dangling_depends_on(self):
        compose_text = """
 services:
   web:
     image: nginx
     depends_on:
       - db
       - ghost
   worker:
     image: busybox
     depends_on:
       db:
         condition: service_started
       ghost:
         condition: service_started
   orphan:
     image: alpine
     depends_on:
       - ghost
   db:
     image: postgres
 """

        sanitized = yaml.safe_load(dcu.sanitize_resolved_compose(compose_text))

        assert sanitized["services"]["web"]["depends_on"] == ["db"]
        assert sanitized["services"]["worker"]["depends_on"] == {"db": {"condition": "service_started"}}
        assert "depends_on" not in sanitized["services"]["orphan"]

    def test_sanitize_resolved_compose_returns_original_text_for_non_mapping_yaml(self):
        compose_text = "- just\n- a\n- list\n"
        assert dcu.sanitize_resolved_compose(compose_text) == compose_text


class TestBuildResolvedEnv:
    def test_build_resolved_env_merges_defaults_and_overrides(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        recipe = _make_recipe(
            tmp_path,
            _env_text(
                "MODE=local",
                "BP_PROFILE=search",
                "PROXY_MODE=direct",
                "HARDWARE_PROFILE=igx",
                "LLM_MODE=local",
                "LLM_NAME=llm-a",
                "VLM_NAME=vlm-a",
                "HOST_IP=<HOST_IP>",
                "MDX_SAMPLE_APPS_DIR=/path/to/deployments",
                "NGC_CLI_API_KEY=",  # pragma: allowlist secret
                "NVIDIA_API_KEY=",  # pragma: allowlist secret
            ),
            profile=dcu.PROFILE_SEARCH,
            env_overrides={"HOST_IP": "10.0.0.5"},
            ngc_cli_api_key="ngc-from-config",  # pragma: allowlist secret
            nvidia_api_key="nvidia-from-config",  # pragma: allowlist secret
        )

        brev_calls: list[tuple[str, str]] = []
        monkeypatch.setattr(dcu, "detect_internal_ip", lambda: pytest.fail("HOST_IP override should win"))
        monkeypatch.setattr(dcu, "detect_external_ip", lambda: "44.55.66.77")
        monkeypatch.setattr(dcu, "read_etc_environment", lambda: {"BREV_ENV_ID": "brev-from-etc"})
        monkeypatch.setattr(
            dcu,
            "apply_brev_proxy_env",
            lambda merged, brev_env_id: brev_calls.append((merged["HOST_IP"], brev_env_id)),
        )

        resolved = dcu.build_resolved_env(recipe)

        assert resolved["VLM_MODE"] == "local"
        assert resolved["HOST_IP"] == "10.0.0.5"
        assert resolved["EXTERNALLY_ACCESSIBLE_IP"] == "44.55.66.77"
        assert resolved["EXTERNAL_IP"] == "44.55.66.77"
        assert resolved["MDX_SAMPLE_APPS_DIR"] == str(recipe.deployments_dir)
        assert resolved["MDX_DATA_DIR"] == str(recipe.mdx_data_dir)
        assert resolved["NGC_CLI_API_KEY"] == "ngc-from-config"  # pragma: allowlist secret
        assert resolved["NVIDIA_API_KEY"] == "nvidia-from-config"  # pragma: allowlist secret
        assert resolved["LLM_NAME_SLUG"] == "llm-a-slug"
        assert resolved["VLM_NAME_SLUG"] == "vlm-a-slug"
        assert resolved["LLM_DEVICE_ID"] == "0"
        assert resolved["VLM_DEVICE_ID"] == "1"
        assert resolved["COMPOSE_PROFILES"] == (
            "search_local,search_local_igx,search_local_direct,llm_local_llm-a-slug,vlm_local_vlm-a-slug"
        )
        assert brev_calls == [("10.0.0.5", "brev-from-etc")]

    def test_build_resolved_env_preserves_nonempty_env_file_values(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        recipe = _make_recipe(
            tmp_path,
            _env_text(
                "MODE=local",
                "BP_PROFILE=base",
                "PROXY_MODE=direct",
                "HARDWARE_PROFILE=thor",
                "LLM_MODE=local",
                "LLM_NAME=llm-a",
                "VLM_MODE=local",
                "VLM_NAME=vlm-a",
                "HOST_IP=10.0.0.8",
                "EXTERNALLY_ACCESSIBLE_IP=198.51.100.5",
                "MDX_SAMPLE_APPS_DIR=/already/set",
                "NGC_CLI_API_KEY=from-file",  # pragma: allowlist secret
                "NVIDIA_API_KEY=from-file",  # pragma: allowlist secret
            ),
            env_overrides={"MDX_DATA_DIR": "/override/data"},
            ngc_cli_api_key="ignored-ngc",  # pragma: allowlist secret
            nvidia_api_key="ignored-nvidia",  # pragma: allowlist secret
        )

        monkeypatch.setattr(dcu, "detect_internal_ip", lambda: pytest.fail("env HOST_IP should be used"))
        monkeypatch.setattr(dcu, "detect_external_ip", lambda: pytest.fail("env EXTERNAL_IP should be used"))
        monkeypatch.setattr(dcu, "read_etc_environment", lambda: {})
        monkeypatch.setattr(dcu, "apply_brev_proxy_env", lambda _merged, _brev_env_id: None)

        resolved = dcu.build_resolved_env(recipe)

        assert resolved["HOST_IP"] == "10.0.0.8"
        assert resolved["EXTERNALLY_ACCESSIBLE_IP"] == "198.51.100.5"
        assert "EXTERNAL_IP" in resolved
        assert resolved["MDX_SAMPLE_APPS_DIR"] == "/already/set"
        assert resolved["MDX_DATA_DIR"] == "/override/data"
        assert resolved["NGC_CLI_API_KEY"] == "from-file"  # pragma: allowlist secret
        assert resolved["NVIDIA_API_KEY"] == "from-file"  # pragma: allowlist secret
