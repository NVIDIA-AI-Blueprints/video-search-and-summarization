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
from typing import ClassVar

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
    hardware_profile: str | None = None,
    external_ip: str | None = None,
    openai_api_key: str | None = None,
    llm_endpoint_url: str | None = None,
    llm_model_type: str | None = None,
    llm_name: str | None = None,
    vlm_name: str | None = None,
    vlm_endpoint_url: str | None = None,
    vlm_model_type: str | None = None,
    llm_enable_thinking: str | None = None,
    nim_kvcache_percent: str | None = None,
    rtvi_vllm_gpu_memory_utilization: str | None = None,
    profile_mode: str | None = None,
    supported_hardware_profiles: frozenset[str] = frozenset({"igx", "thor"}),
    edge_hardware_profiles: frozenset[str] = frozenset({"igx"}),
    edge_allowed_profiles: frozenset[str] | None = None,
    edge_device_ids: dict[str, str] | None = None,
    hardware_profile_env_overrides: dict[str, dict[str, str | dict[str, str]]] | None = None,
) -> dcu.DryRunRecipe:
    deployments_dir = tmp_path / "deployments"
    deployments_dir.mkdir()
    mdx_data_dir = tmp_path / "mdx-data"
    mdx_data_dir.mkdir()
    source_env_file = tmp_path / "profile.env"
    source_env_file.write_text(env_text.strip() + "\n")

    if edge_allowed_profiles is None:
        edge_allowed_profiles = frozenset({dcu.PROFILE_ALERTS, dcu.PROFILE_SEARCH})
    if edge_device_ids is None:
        edge_device_ids = {"llm": "0", "vlm": "1", "rt_vlm": "2", "rt_cv": "3"}

    return dcu.DryRunRecipe(
        profile=profile,  # type: ignore[arg-type]
        env_overrides=env_overrides or {},
        ngc_cli_api_key=ngc_cli_api_key,
        nvidia_api_key=nvidia_api_key,
        hardware_profile=hardware_profile,
        external_ip=external_ip,
        openai_api_key=openai_api_key,
        llm_endpoint_url=llm_endpoint_url,
        llm_model_type=llm_model_type,
        llm_name=llm_name,
        vlm_name=vlm_name,
        vlm_endpoint_url=vlm_endpoint_url,
        vlm_model_type=vlm_model_type,
        llm_enable_thinking=llm_enable_thinking,
        nim_kvcache_percent=nim_kvcache_percent,
        rtvi_vllm_gpu_memory_utilization=rtvi_vllm_gpu_memory_utilization,
        profile_mode=profile_mode,
        output_env_file=tmp_path / "generated.env",
        output_compose_file=tmp_path / "docker-compose.generated.yml",
        deployments_dir=deployments_dir,
        mdx_data_dir=mdx_data_dir,
        compose_file=tmp_path / "compose.yml",
        source_env_file=source_env_file,
        supported_hardware_profiles=supported_hardware_profiles,
        edge_hardware_profiles=edge_hardware_profiles,
        edge_allowed_profiles=edge_allowed_profiles,
        edge_device_ids=edge_device_ids,
        profile_mode_to_env_modes={
            dcu.PROFILE_ALERTS: {"verification": dcu.MODE_2D_CV, "real-time": dcu.MODE_2D_VLM},
        },
        hardware_profile_env_overrides=(hardware_profile_env_overrides or {}),
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
                "/path/to/deploy/docker",
                "10.0.0.5",
            ]
        )

        assert result == "10.0.0.5"

    def test_first_non_placeholder_returns_empty_when_all_values_are_placeholders(self):
        assert dcu.first_non_placeholder(["", "   ", "<HOST_IP>", "${HOST_IP}"]) == ""


class TestProfileModeToEnvMode:
    def test_profile_mode_to_env_mode_maps_supported_modes(self):
        mapping = {dcu.PROFILE_ALERTS: {"verification": dcu.MODE_2D_CV, "real-time": dcu.MODE_2D_VLM}}
        assert dcu.profile_mode_to_env_mode(dcu.PROFILE_ALERTS, "verification", mapping) == dcu.MODE_2D_CV
        assert dcu.profile_mode_to_env_mode(dcu.PROFILE_ALERTS, "real-time", mapping) == dcu.MODE_2D_VLM

    def test_profile_mode_to_env_mode_rejects_unknown_mode(self):
        with pytest.raises(dcu.ValidationError, match="Supported values"):
            dcu.profile_mode_to_env_mode(
                dcu.PROFILE_ALERTS, "unsupported", {dcu.PROFILE_ALERTS: {"verification": dcu.MODE_2D_CV}}
            )

    def test_profile_mode_to_env_mode_rejects_when_profile_has_no_modes(self):
        with pytest.raises(dcu.ValidationError, match="not configured for profile"):
            dcu.profile_mode_to_env_mode(dcu.PROFILE_BASE, "verification", {dcu.PROFILE_ALERTS: {}})

    def test_profile_mode_to_env_mode_rejects_when_mapping_empty(self):
        with pytest.raises(dcu.ValidationError, match="not configured for profile"):
            dcu.profile_mode_to_env_mode(dcu.PROFILE_ALERTS, "verification", {})


class TestResolveAndApplyProfileMode:
    """Integration coverage of the validation+dispatch helper used by _docker_generate.
    Each test exercises one branch of the rule: modes-required, modes-rejected, MODE
    conflict, invalid mode, valid resolution."""

    MAPPING: ClassVar[dict[str, dict[str, str]]] = {
        dcu.PROFILE_ALERTS: {"verification": dcu.MODE_2D_CV, "real-time": dcu.MODE_2D_VLM},
    }

    def test_modeful_profile_without_profile_mode_raises(self):
        """Regression guard: alerts profile invoked without profile_mode must fail loudly
        instead of silently producing an env with no MODE (the original bug)."""
        env_overrides: dict[str, str] = {}
        with pytest.raises(dcu.ValidationError, match="profile_mode is required when profile='alerts'"):
            dcu.resolve_and_apply_profile_mode(dcu.PROFILE_ALERTS, None, self.MAPPING, env_overrides)
        assert "MODE" not in env_overrides

    def test_modeful_profile_with_valid_mode_sets_env_mode(self):
        env_overrides: dict[str, str] = {}
        dcu.resolve_and_apply_profile_mode(dcu.PROFILE_ALERTS, "verification", self.MAPPING, env_overrides)
        assert env_overrides["MODE"] == dcu.MODE_2D_CV

    def test_modeful_profile_with_unknown_mode_raises(self):
        env_overrides: dict[str, str] = {}
        with pytest.raises(dcu.ValidationError, match="Invalid profile_mode 'bogus'"):
            dcu.resolve_and_apply_profile_mode(dcu.PROFILE_ALERTS, "bogus", self.MAPPING, env_overrides)
        assert "MODE" not in env_overrides

    def test_modeful_profile_with_mode_conflicting_with_env_override_raises(self):
        env_overrides = {"MODE": dcu.MODE_2D_VLM}
        with pytest.raises(dcu.ValidationError, match="conflicts with env override MODE='2d_vlm'"):
            dcu.resolve_and_apply_profile_mode(dcu.PROFILE_ALERTS, "verification", self.MAPPING, env_overrides)
        assert env_overrides["MODE"] == dcu.MODE_2D_VLM  # unchanged

    def test_modeful_profile_with_mode_matching_env_override_succeeds(self):
        """A consistent MODE override is allowed (no conflict)."""
        env_overrides = {"MODE": dcu.MODE_2D_CV}
        dcu.resolve_and_apply_profile_mode(dcu.PROFILE_ALERTS, "verification", self.MAPPING, env_overrides)
        assert env_overrides["MODE"] == dcu.MODE_2D_CV

    def test_modeless_profile_without_profile_mode_is_noop(self):
        """base has no modes; profile_mode=None must succeed and leave env_overrides untouched."""
        env_overrides: dict[str, str] = {}
        dcu.resolve_and_apply_profile_mode(dcu.PROFILE_BASE, None, self.MAPPING, env_overrides)
        assert env_overrides == {}

    def test_modeless_profile_preserves_existing_env_overrides(self):
        """Existing env_overrides (including a pre-set MODE) must pass through untouched
        when the profile has no modes and profile_mode is None."""
        env_overrides = {"MODE": "custom_mode", "OTHER_KEY": "value"}
        dcu.resolve_and_apply_profile_mode(dcu.PROFILE_BASE, None, self.MAPPING, env_overrides)
        assert env_overrides == {"MODE": "custom_mode", "OTHER_KEY": "value"}

    def test_modeless_profile_with_profile_mode_raises(self):
        env_overrides: dict[str, str] = {}
        with pytest.raises(dcu.ValidationError, match="profile_mode is not supported when profile='base'"):
            dcu.resolve_and_apply_profile_mode(dcu.PROFILE_BASE, "verification", self.MAPPING, env_overrides)
        assert env_overrides == {}

    def test_empty_mapping_treats_all_profiles_as_modeless(self):
        """When no profile is configured with modes, all profiles behave as modeless."""
        env_overrides: dict[str, str] = {}
        dcu.resolve_and_apply_profile_mode(dcu.PROFILE_ALERTS, None, {}, env_overrides)
        assert env_overrides == {}
        with pytest.raises(dcu.ValidationError, match="profile_mode is not supported when profile='alerts'"):
            dcu.resolve_and_apply_profile_mode(dcu.PROFILE_ALERTS, "verification", {}, env_overrides)


class TestResolveComposeProfiles:
    def test_resolve_compose_profiles_uses_profile_template(self):
        merged = {
            "BP_PROFILE": "bp_developer_alerts",
            "MODE": "2d_cv",
            "HARDWARE_PROFILE": "H100",
            "LLM_MODE": "local_shared",
            "LLM_NAME_SLUG": "llm-a-slug",
            "VLM_MODE": "local_shared",
            "VLM_NAME_SLUG": "vlm-a-slug",
            "COMPOSE_PROFILES": "${BP_PROFILE}_${MODE},${BP_PROFILE}_${MODE}_${HARDWARE_PROFILE},llm_${LLM_MODE}_${LLM_NAME_SLUG}",
        }

        assert (
            dcu.resolve_compose_profiles(merged, dcu.PROFILE_ALERTS)
            == "bp_developer_alerts_2d_cv,bp_developer_alerts_2d_cv_H100,llm_local_shared_llm-a-slug"
        )

    def test_resolve_compose_profiles_falls_back_for_legacy_env(self):
        merged = {
            "BP_PROFILE": "bp_developer_search",
            "MODE": "2d",
            "HARDWARE_PROFILE": "H100",
            "LLM_MODE": "local_shared",
            "LLM_NAME_SLUG": "llm-a-slug",
            "VLM_MODE": "local_shared",
            "VLM_NAME_SLUG": "vlm-a-slug",
        }

        assert (
            dcu.resolve_compose_profiles(merged, dcu.PROFILE_SEARCH)
            == "bp_developer_search_2d,llm_local_shared_llm-a-slug,vlm_local_shared_vlm-a-slug"
        )


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
                "LLM_MODE=local_shared",
                "LLM_NAME=llm-a",
                "LLM_NAME_SLUG=llm-a-slug",
                "VLM_MODE=local_shared",
                "VLM_NAME=vlm-a",
                "VLM_NAME_SLUG=vlm-a-slug",
                "HOST_IP=<HOST_IP>",
                "VSS_APPS_DIR=/path/to/deploy/docker",
                "COMPOSE_PROFILES=${BP_PROFILE}_${MODE},llm_${LLM_MODE}_${LLM_NAME_SLUG},vlm_${VLM_MODE}_${VLM_NAME_SLUG}",
                "NGC_CLI_API_KEY=",  # pragma: allowlist secret
                "NVIDIA_API_KEY=",  # pragma: allowlist secret
            ),
            profile=dcu.PROFILE_SEARCH,
            env_overrides={"HOST_IP": "10.0.0.5"},
            ngc_cli_api_key="ngc-from-config",  # pragma: allowlist secret
            nvidia_api_key="nvidia-from-config",  # pragma: allowlist secret
        )

        brev_calls: list[tuple[str, str]] = []
        monkeypatch.delenv("BREV_ENV_ID", raising=False)
        monkeypatch.setattr(dcu, "detect_internal_ip", lambda: pytest.fail("HOST_IP override should win"))
        monkeypatch.setattr(dcu, "detect_external_ip", lambda: "44.55.66.77")
        monkeypatch.setattr(dcu, "read_etc_environment", lambda: {"BREV_ENV_ID": "brev-from-etc"})
        monkeypatch.setattr(
            dcu,
            "apply_brev_proxy_env",
            lambda merged, brev_env_id: brev_calls.append((merged["HOST_IP"], brev_env_id)),
        )

        resolved = dcu.build_resolved_env(recipe)

        assert resolved["VLM_MODE"] == "local_shared"
        assert resolved["HOST_IP"] == "10.0.0.5"
        assert resolved["EXTERNALLY_ACCESSIBLE_IP"] == "44.55.66.77"
        assert resolved["EXTERNAL_IP"] == "44.55.66.77"
        assert resolved["VSS_APPS_DIR"] == str(recipe.deployments_dir)
        assert resolved["VSS_DATA_DIR"] == str(recipe.mdx_data_dir)
        assert resolved["NGC_CLI_API_KEY"] == "ngc-from-config"  # pragma: allowlist secret
        assert resolved["NVIDIA_API_KEY"] == "nvidia-from-config"  # pragma: allowlist secret
        assert resolved["LLM_NAME_SLUG"] == "llm-a-slug"
        assert resolved["VLM_NAME_SLUG"] == "vlm-a-slug"
        assert resolved["LLM_DEVICE_ID"] == "0"
        assert resolved["VLM_DEVICE_ID"] == "1"
        assert "SHARED_LLM_VLM_DEVICE_ID" not in resolved
        assert resolved["COMPOSE_PROFILES"] == "search_local,llm_local_shared_llm-a-slug,vlm_local_shared_vlm-a-slug"
        assert brev_calls == [("10.0.0.5", "brev-from-etc")]

    def test_build_resolved_env_uses_recipe_hardware_profile_when_not_overridden(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        recipe = _make_recipe(
            tmp_path,
            _env_text(
                "MODE=local",
                "BP_PROFILE=base",
                "PROXY_MODE=direct",
                "HARDWARE_PROFILE=igx",
                "LLM_MODE=local",
                "LLM_NAME=llm-a",
                "LLM_NAME_SLUG=llm-a-slug",
                "VLM_MODE=local",
                "VLM_NAME=vlm-a",
                "VLM_NAME_SLUG=vlm-a-slug",
                "HOST_IP=10.0.0.8",
                "EXTERNALLY_ACCESSIBLE_IP=198.51.100.5",
                "VSS_APPS_DIR=/path/to/deploy/docker",
            ),
            hardware_profile="thor",
        )
        monkeypatch.setattr(dcu, "read_etc_environment", lambda: {})
        monkeypatch.setattr(dcu, "apply_brev_proxy_env", lambda _merged, _brev_env_id: None)

        resolved = dcu.build_resolved_env(recipe)

        assert resolved["HARDWARE_PROFILE"] == "thor"

    def test_build_resolved_env_prefers_env_override_over_recipe_hardware_profile(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        recipe = _make_recipe(
            tmp_path,
            _env_text(
                "MODE=local",
                "BP_PROFILE=search",
                "PROXY_MODE=direct",
                "HARDWARE_PROFILE=thor",
                "LLM_MODE=local_shared",
                "LLM_NAME=llm-a",
                "LLM_NAME_SLUG=llm-a-slug",
                "VLM_MODE=local_shared",
                "VLM_NAME=vlm-a",
                "VLM_NAME_SLUG=vlm-a-slug",
                "HOST_IP=10.0.0.8",
                "EXTERNALLY_ACCESSIBLE_IP=198.51.100.5",
                "VSS_APPS_DIR=/path/to/deploy/docker",
            ),
            profile=dcu.PROFILE_SEARCH,
            env_overrides={"HARDWARE_PROFILE": "igx"},
            hardware_profile="thor",
        )
        monkeypatch.setattr(dcu, "read_etc_environment", lambda: {})
        monkeypatch.setattr(dcu, "apply_brev_proxy_env", lambda _merged, _brev_env_id: None)

        resolved = dcu.build_resolved_env(recipe)

        assert resolved["HARDWARE_PROFILE"] == "igx"

    def test_build_resolved_env_uses_recipe_api_keys_over_env_file_values(
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
                "LLM_NAME_SLUG=llm-a-slug",
                "VLM_MODE=local",
                "VLM_NAME=vlm-a",
                "VLM_NAME_SLUG=vlm-a-slug",
                "HOST_IP=10.0.0.8",
                "EXTERNALLY_ACCESSIBLE_IP=198.51.100.5",
                "VSS_APPS_DIR=/already/set",
                "NGC_CLI_API_KEY=from-file",  # pragma: allowlist secret
                "NVIDIA_API_KEY=from-file",  # pragma: allowlist secret
            ),
            env_overrides={"VSS_DATA_DIR": "/override/data"},
            ngc_cli_api_key="from-recipe-ngc",  # pragma: allowlist secret
            nvidia_api_key="from-recipe-nvidia",  # pragma: allowlist secret
        )

        monkeypatch.setattr(dcu, "detect_internal_ip", lambda: pytest.fail("env HOST_IP should be used"))
        monkeypatch.setattr(dcu, "detect_external_ip", lambda: pytest.fail("env EXTERNAL_IP should be used"))
        monkeypatch.setattr(dcu, "read_etc_environment", lambda: {})
        monkeypatch.setattr(dcu, "apply_brev_proxy_env", lambda _merged, _brev_env_id: None)

        resolved = dcu.build_resolved_env(recipe)

        assert resolved["HOST_IP"] == "10.0.0.8"
        assert resolved["EXTERNALLY_ACCESSIBLE_IP"] == "198.51.100.5"
        assert "EXTERNAL_IP" in resolved
        assert resolved["VSS_APPS_DIR"] == "/already/set"
        assert resolved["VSS_DATA_DIR"] == "/override/data"
        assert resolved["NGC_CLI_API_KEY"] == "from-recipe-ngc"  # pragma: allowlist secret
        assert resolved["NVIDIA_API_KEY"] == "from-recipe-nvidia"  # pragma: allowlist secret

    def test_build_resolved_env_prefers_env_override_over_recipe_api_keys(
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
                "LLM_NAME_SLUG=llm-a-slug",
                "VLM_MODE=local",
                "VLM_NAME=vlm-a",
                "VLM_NAME_SLUG=vlm-a-slug",
                "HOST_IP=10.0.0.8",
                "EXTERNALLY_ACCESSIBLE_IP=198.51.100.5",
                "VSS_APPS_DIR=/path/to/deploy/docker",
                "NGC_CLI_API_KEY=from-file",  # pragma: allowlist secret
                "NVIDIA_API_KEY=from-file",  # pragma: allowlist secret
            ),
            env_overrides={
                "NGC_CLI_API_KEY": "from-override-ngc",  # pragma: allowlist secret
                "NVIDIA_API_KEY": "from-override-nvidia",  # pragma: allowlist secret
            },
            ngc_cli_api_key="from-recipe-ngc",  # pragma: allowlist secret
            nvidia_api_key="from-recipe-nvidia",  # pragma: allowlist secret
        )
        monkeypatch.setattr(dcu, "read_etc_environment", lambda: {})
        monkeypatch.setattr(dcu, "apply_brev_proxy_env", lambda _merged, _brev_env_id: None)

        resolved = dcu.build_resolved_env(recipe)

        assert resolved["NGC_CLI_API_KEY"] == "from-override-ngc"  # pragma: allowlist secret
        assert resolved["NVIDIA_API_KEY"] == "from-override-nvidia"  # pragma: allowlist secret

    def test_build_resolved_env_alerts_real_time_sets_edge_and_rtvi_overrides(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        recipe = _make_recipe(
            tmp_path,
            _env_text(
                "MODE=2d_cv",
                "BP_PROFILE=bp_developer_alerts",
                "PROXY_MODE=direct",
                "HARDWARE_PROFILE=igx",
                "LLM_MODE=local_shared",
                "LLM_NAME=llm-a",
                "LLM_NAME_SLUG=llm-a-slug",
                "VLM_MODE=local",
                "VLM_NAME=nim_nvidia_cosmos-reason2-8b_hf-1208",
                "VLM_NAME_SLUG=none",
                "HOST_IP=10.0.0.9",
                "VLM_PORT=30099",
                "RTVI_VLM_MODEL_PATH=ngc:nim/nvidia/cosmos-reason2-8b:hf-1208",
                "COMPOSE_PROFILES=${BP_PROFILE}_${MODE},${BP_PROFILE}_${MODE}_${HARDWARE_PROFILE},llm_${LLM_MODE}_${LLM_NAME_SLUG}",
            ),
            profile=dcu.PROFILE_ALERTS,
            env_overrides={"MODE": dcu.MODE_2D_VLM},
        )

        monkeypatch.setattr(dcu, "detect_internal_ip", lambda: pytest.fail("env HOST_IP should be used"))
        monkeypatch.setattr(dcu, "detect_external_ip", lambda: "10.0.0.9")
        monkeypatch.setattr(dcu, "read_etc_environment", lambda: {})
        monkeypatch.setattr(dcu, "apply_brev_proxy_env", lambda _merged, _brev_env_id: None)

        resolved = dcu.build_resolved_env(recipe)

        assert resolved["MODE"] == dcu.MODE_2D_VLM
        assert resolved["PERCEPTION_DOCKERFILE_PREFIX"] == "EDGE-"
        assert resolved["VLM_AS_VERIFIER_CONFIG_FILE_PREFIX"] == "EDGE-LOCAL-VLM-"
        assert resolved["RTVI_VLM_INPUT_WIDTH"] == dcu.EDGE_ALERTS_RTVI_INPUT_WIDTH
        assert resolved["RTVI_VLM_INPUT_HEIGHT"] == dcu.EDGE_ALERTS_RTVI_INPUT_HEIGHT
        assert resolved["RTVI_VLM_DEFAULT_NUM_FRAMES_PER_SECOND_OR_FIXED_FRAMES_CHUNK"] == dcu.EDGE_ALERTS_RTVI_FPS
        assert resolved["RTVI_VLM_MODEL_PATH"] == "ngc:nim/nvidia/cosmos-reason2-8b:hf-1208"
        assert resolved["RTVI_VLM_ENDPOINT"] == "http://10.0.0.9:30099/v1"
        assert resolved["LLM_DEVICE_ID"] == "0"
        assert resolved["VLM_DEVICE_ID"] == "1"
        assert resolved["VLM_NAME"] == "nim_nvidia_cosmos-reason2-8b_hf-1208"
        assert resolved["VLM_NAME_SLUG"] == "none"

    def test_build_resolved_env_alerts_local_applies_vlm_runtime_overrides(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        recipe = _make_recipe(
            tmp_path,
            _env_text(
                "MODE=2d_cv",
                "BP_PROFILE=bp_developer_alerts",
                "PROXY_MODE=direct",
                "HARDWARE_PROFILE=igx",
                "LLM_MODE=local_shared",
                "LLM_NAME=llm-a",
                "LLM_NAME_SLUG=llm-a-slug",
                "VLM_MODE=local_shared",
                "VLM_NAME=nim_nvidia_cosmos-reason2-8b_hf-1208",
                "VLM_NAME_SLUG=none",
                "HOST_IP=10.0.0.9",
                "RTVI_VLM_MODEL_PATH=ngc:nim/nvidia/cosmos-reason2-8b:hf-1208",
                "RTVI_VLM_MODEL_TO_USE=cosmos-reason2",
                "COMPOSE_PROFILES=${BP_PROFILE}_${MODE},llm_${LLM_MODE}_${LLM_NAME_SLUG}",
            ),
            profile=dcu.PROFILE_ALERTS,
        )

        monkeypatch.setattr(dcu, "detect_internal_ip", lambda: pytest.fail("env HOST_IP should be used"))
        monkeypatch.setattr(dcu, "detect_external_ip", lambda: "10.0.0.9")
        monkeypatch.setattr(dcu, "read_etc_environment", lambda: {})
        monkeypatch.setattr(dcu, "apply_brev_proxy_env", lambda _merged, _brev_env_id: None)

        resolved = dcu.build_resolved_env(recipe)

        assert resolved["VLM_NAME"] == "nim_nvidia_cosmos-reason2-8b_hf-1208"
        assert resolved["VLM_NAME_SLUG"] == "none"
        assert resolved["RTVI_VLM_MODEL_PATH"] == "ngc:nim/nvidia/cosmos-reason2-8b:hf-1208"
        assert resolved["RTVI_VLM_MODEL_TO_USE"] == "cosmos-reason2"

    def test_build_resolved_env_alerts_thor_applies_shared_vlm_overrides(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        recipe = _make_recipe(
            tmp_path,
            _env_text(
                "MODE=2d_cv",
                "BP_PROFILE=bp_developer_alerts",
                "PROXY_MODE=direct",
                "HARDWARE_PROFILE=thor",
                "LLM_MODE=local",
                "LLM_NAME=llm-a",
                "LLM_NAME_SLUG=llm-a-slug",
                "VLM_MODE=local",
                "VLM_NAME=vlm-a",
                "VLM_NAME_SLUG=vlm-a-slug",
                "HOST_IP=10.0.0.8",
            ),
            profile=dcu.PROFILE_ALERTS,
            edge_hardware_profiles=frozenset({"thor"}),
            edge_allowed_profiles=frozenset({dcu.PROFILE_ALERTS}),
            hardware_profile_env_overrides={
                "thor": {
                    "VLM_NAME_SLUG": "none",
                    "VLM_NAME": "nim_nvidia_cosmos-reason2-8b_hf-1208",
                    "RTVI_VLM_MODEL_PATH": "ngc:nim/nvidia/cosmos-reason2-8b:hf-1208",
                    "RTVI_VLM_MODEL_TO_USE": "cosmos-reason2",
                    "RTVI_VLLM_GPU_MEMORY_UTILIZATION": "0.35",
                    "VLM_MODEL_TYPE": "rtvi",
                },
            },
        )

        monkeypatch.setattr(dcu, "detect_internal_ip", lambda: pytest.fail("env HOST_IP should be used"))
        monkeypatch.setattr(dcu, "detect_external_ip", lambda: "10.0.0.8")
        monkeypatch.setattr(dcu, "read_etc_environment", lambda: {})
        monkeypatch.setattr(dcu, "apply_brev_proxy_env", lambda _merged, _brev_env_id: None)

        resolved = dcu.build_resolved_env(recipe)

        assert resolved["VLM_NAME"] == "nim_nvidia_cosmos-reason2-8b_hf-1208"
        assert resolved["VLM_NAME_SLUG"] == "none"
        assert resolved["VLM_BASE_URL"] == f"http://10.0.0.8:{dcu.THOR_VLM_PORT}"
        assert resolved["RTVI_VLM_MODEL_PATH"] == "ngc:nim/nvidia/cosmos-reason2-8b:hf-1208"
        assert resolved["RTVI_VLM_MODEL_TO_USE"] == "cosmos-reason2"
        assert resolved["RTVI_VLLM_GPU_MEMORY_UTILIZATION"] == "0.35"


def _base_env(hardware_profile: str, *extra: str) -> tuple[str, ...]:
    return (
        "MODE=local",
        "BP_PROFILE=base",
        "PROXY_MODE=direct",
        f"HARDWARE_PROFILE={hardware_profile}",
        "LLM_MODE=local",
        "LLM_NAME=llm-a",
        "LLM_NAME_SLUG=llm-a-slug",
        "VLM_MODE=local",
        "VLM_NAME=vlm-a",
        "VLM_NAME_SLUG=vlm-a-slug",
        "HOST_IP=10.0.0.1",
        "EXTERNALLY_ACCESSIBLE_IP=198.51.100.5",
        "VSS_APPS_DIR=/path/to/deploy/docker",
        *extra,
    )


def _patch_network(monkeypatch: pytest.MonkeyPatch, ip: str = "10.0.0.1") -> None:
    monkeypatch.setattr(dcu, "detect_internal_ip", lambda: ip)
    monkeypatch.setattr(dcu, "detect_external_ip", lambda: ip)
    monkeypatch.setattr(dcu, "read_etc_environment", lambda: {})
    monkeypatch.setattr(dcu, "apply_brev_proxy_env", lambda _merged, _brev_env_id: None)


class TestPrecedence:
    """Layered precedence for env values (low -> high):

    profile .env  <  yml hardware_profiles[HW]  <  notebook named recipe param  <  per-call env_overrides

    Tests use a non-edge HW (thor) to isolate the layered-precedence logic from
    edge-specific code paths (edge_device_ids, VLM_BASE_URL synthesis).
    """

    def test_dotenv_value_passes_through_when_no_overrides(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        recipe = _make_recipe(
            tmp_path,
            _env_text(*_base_env("thor", "RTVI_VLLM_GPU_MEMORY_UTILIZATION=0.10")),
        )
        _patch_network(monkeypatch)

        resolved = dcu.build_resolved_env(recipe)

        assert resolved["RTVI_VLLM_GPU_MEMORY_UTILIZATION"] == "0.10"

    def test_profile_env_overrides_wins_over_dotenv(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        recipe = _make_recipe(
            tmp_path,
            _env_text(*_base_env("thor", "RTVI_VLLM_GPU_MEMORY_UTILIZATION=0.10")),
            hardware_profile_env_overrides={"thor": {"RTVI_VLLM_GPU_MEMORY_UTILIZATION": "0.20"}},
        )
        _patch_network(monkeypatch)

        resolved = dcu.build_resolved_env(recipe)

        assert resolved["RTVI_VLLM_GPU_MEMORY_UTILIZATION"] == "0.20"

    def test_named_param_wins_over_profile_env_overrides(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        recipe = _make_recipe(
            tmp_path,
            _env_text(*_base_env("thor", "RTVI_VLLM_GPU_MEMORY_UTILIZATION=0.10")),
            hardware_profile_env_overrides={"thor": {"RTVI_VLLM_GPU_MEMORY_UTILIZATION": "0.20"}},
            rtvi_vllm_gpu_memory_utilization="0.30",
        )
        _patch_network(monkeypatch)

        resolved = dcu.build_resolved_env(recipe)

        assert resolved["RTVI_VLLM_GPU_MEMORY_UTILIZATION"] == "0.30"

    def test_env_overrides_wins_over_named_param(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        recipe = _make_recipe(
            tmp_path,
            _env_text(*_base_env("thor", "RTVI_VLLM_GPU_MEMORY_UTILIZATION=0.10")),
            hardware_profile_env_overrides={"thor": {"RTVI_VLLM_GPU_MEMORY_UTILIZATION": "0.20"}},
            rtvi_vllm_gpu_memory_utilization="0.30",
            env_overrides={"RTVI_VLLM_GPU_MEMORY_UTILIZATION": "0.40"},
        )
        _patch_network(monkeypatch)

        resolved = dcu.build_resolved_env(recipe)

        assert resolved["RTVI_VLLM_GPU_MEMORY_UTILIZATION"] == "0.40"

    def test_env_overrides_wins_over_profile_env_overrides_without_named_param(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        recipe = _make_recipe(
            tmp_path,
            _env_text(*_base_env("thor")),
            hardware_profile_env_overrides={"thor": {"VLM_NAME": "yml-vlm"}},
            env_overrides={"VLM_NAME": "override-vlm"},
        )
        _patch_network(monkeypatch)

        resolved = dcu.build_resolved_env(recipe)

        assert resolved["VLM_NAME"] == "override-vlm"

    def test_profile_env_overrides_for_different_hardware_is_ignored(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        recipe = _make_recipe(
            tmp_path,
            _env_text(*_base_env("thor", "RTVI_VLLM_GPU_MEMORY_UTILIZATION=0.10")),
            hardware_profile_env_overrides={"igx": {"RTVI_VLLM_GPU_MEMORY_UTILIZATION": "0.20"}},
        )
        _patch_network(monkeypatch)

        resolved = dcu.build_resolved_env(recipe)

        assert resolved["RTVI_VLLM_GPU_MEMORY_UTILIZATION"] == "0.10"

    def test_key_absent_at_all_layers_is_not_in_resolved_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        recipe = _make_recipe(
            tmp_path,
            _env_text(*_base_env("thor")),
        )
        _patch_network(monkeypatch)

        resolved = dcu.build_resolved_env(recipe)

        assert "RTVI_VLLM_GPU_MEMORY_UTILIZATION" not in resolved

    def test_named_param_writes_key_when_dotenv_lacks_it(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        recipe = _make_recipe(
            tmp_path,
            _env_text(*_base_env("thor")),
            rtvi_vllm_gpu_memory_utilization="0.55",
        )
        _patch_network(monkeypatch)

        resolved = dcu.build_resolved_env(recipe)

        assert resolved["RTVI_VLLM_GPU_MEMORY_UTILIZATION"] == "0.55"


class TestEdgeDeviceIdsPrecedence:
    """yml edge_device_ids apply for edge HW only, and are overridable by env_overrides."""

    def test_edge_device_ids_applied_for_edge_hardware(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        recipe = _make_recipe(
            tmp_path,
            _env_text(*_base_env("igx")),
            profile=dcu.PROFILE_ALERTS,
            edge_allowed_profiles=frozenset({dcu.PROFILE_ALERTS}),
            edge_device_ids={"llm": "5", "vlm": "6", "rt_vlm": "7", "rt_cv": "8"},
        )
        _patch_network(monkeypatch)

        resolved = dcu.build_resolved_env(recipe)

        assert resolved["LLM_DEVICE_ID"] == "5"
        assert resolved["VLM_DEVICE_ID"] == "6"
        assert resolved["RT_VLM_DEVICE_ID"] == "7"
        assert resolved["RT_CV_DEVICE_ID"] == "8"

    def test_edge_device_ids_not_applied_for_non_edge_hardware(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        recipe = _make_recipe(
            tmp_path,
            _env_text(*_base_env("H100")),
            supported_hardware_profiles=frozenset({"igx", "H100"}),
            edge_device_ids={"llm": "5", "vlm": "6", "rt_vlm": "7", "rt_cv": "8"},
        )
        _patch_network(monkeypatch)

        resolved = dcu.build_resolved_env(recipe)

        assert "LLM_DEVICE_ID" not in resolved
        assert "VLM_DEVICE_ID" not in resolved

    def test_env_overrides_wins_over_edge_device_ids(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        recipe = _make_recipe(
            tmp_path,
            _env_text(*_base_env("igx")),
            profile=dcu.PROFILE_ALERTS,
            edge_allowed_profiles=frozenset({dcu.PROFILE_ALERTS}),
            edge_device_ids={"llm": "5", "vlm": "6", "rt_vlm": "7", "rt_cv": "8"},
            env_overrides={"LLM_DEVICE_ID": "99", "VLM_DEVICE_ID": "100"},
        )
        _patch_network(monkeypatch)

        resolved = dcu.build_resolved_env(recipe)

        assert resolved["LLM_DEVICE_ID"] == "99"
        assert resolved["VLM_DEVICE_ID"] == "100"
        # untouched device IDs still come from yml defaults
        assert resolved["RT_VLM_DEVICE_ID"] == "7"
        assert resolved["RT_CV_DEVICE_ID"] == "8"


class TestVlmBaseUrlSynthesis:
    """Late VLM_BASE_URL synthesis: edge HW + base/alerts, dynamic VLM_PORT, notebook wins."""

    def test_synthesizes_for_edge_base_when_unset(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        recipe = _make_recipe(
            tmp_path,
            _env_text(*_base_env("igx", "VLM_PORT=8018")),
            profile=dcu.PROFILE_BASE,
            edge_allowed_profiles=frozenset({dcu.PROFILE_BASE}),
        )
        _patch_network(monkeypatch)

        resolved = dcu.build_resolved_env(recipe)

        assert resolved["VLM_BASE_URL"] == "http://10.0.0.1:8018"

    def test_synthesizes_for_edge_alerts_when_unset(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        recipe = _make_recipe(
            tmp_path,
            _env_text(*_base_env("igx", "VLM_PORT=8018")),
            profile=dcu.PROFILE_ALERTS,
            edge_allowed_profiles=frozenset({dcu.PROFILE_ALERTS}),
        )
        _patch_network(monkeypatch)

        resolved = dcu.build_resolved_env(recipe)

        assert resolved["VLM_BASE_URL"] == "http://10.0.0.1:8018"

    def test_uses_dynamic_vlm_port_from_merged_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        recipe = _make_recipe(
            tmp_path,
            _env_text(*_base_env("igx", "VLM_PORT=30082")),
            profile=dcu.PROFILE_BASE,
            edge_allowed_profiles=frozenset({dcu.PROFILE_BASE}),
        )
        _patch_network(monkeypatch)

        resolved = dcu.build_resolved_env(recipe)

        assert resolved["VLM_BASE_URL"] == "http://10.0.0.1:30082"

    def test_falls_back_to_thor_vlm_port_when_vlm_port_unset(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        recipe = _make_recipe(
            tmp_path,
            _env_text(*_base_env("igx")),
            profile=dcu.PROFILE_BASE,
            edge_allowed_profiles=frozenset({dcu.PROFILE_BASE}),
        )
        _patch_network(monkeypatch)

        resolved = dcu.build_resolved_env(recipe)

        assert resolved["VLM_BASE_URL"] == f"http://10.0.0.1:{dcu.THOR_VLM_PORT}"

    def test_notebook_vlm_endpoint_url_wins_over_synthesis(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        recipe = _make_recipe(
            tmp_path,
            _env_text(*_base_env("igx", "VLM_PORT=8018")),
            profile=dcu.PROFILE_BASE,
            edge_allowed_profiles=frozenset({dcu.PROFILE_BASE}),
            vlm_endpoint_url="http://custom-vlm:9999/v1",
        )
        _patch_network(monkeypatch)

        resolved = dcu.build_resolved_env(recipe)

        assert resolved["VLM_BASE_URL"] == "http://custom-vlm:9999/v1"

    def test_env_overrides_vlm_base_url_wins_over_synthesis(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        recipe = _make_recipe(
            tmp_path,
            _env_text(*_base_env("igx", "VLM_PORT=8018")),
            profile=dcu.PROFILE_BASE,
            edge_allowed_profiles=frozenset({dcu.PROFILE_BASE}),
            env_overrides={"VLM_BASE_URL": "http://override-vlm:9999"},
        )
        _patch_network(monkeypatch)

        resolved = dcu.build_resolved_env(recipe)

        assert resolved["VLM_BASE_URL"] == "http://override-vlm:9999"

    def test_does_not_synthesize_for_non_edge_hardware(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        recipe = _make_recipe(
            tmp_path,
            _env_text(*_base_env("H100", "VLM_PORT=8018")),
            profile=dcu.PROFILE_BASE,
            supported_hardware_profiles=frozenset({"igx", "H100"}),
        )
        _patch_network(monkeypatch)

        resolved = dcu.build_resolved_env(recipe)

        assert resolved.get("VLM_BASE_URL", "") == ""


class TestNestedOverrides:
    """hardware_profiles[HW] supports both HW-wide str-valued keys and
    scoped dict-valued keys (keyed by "<profile>" or "<profile>.<profile_mode>")."""

    def test_hw_level_string_keys_always_apply(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        recipe = _make_recipe(
            tmp_path,
            _env_text(*_base_env("thor")),
            hardware_profile_env_overrides={
                "thor": {
                    "PERCEPTION_TAG": "tag-from-yml",
                    "RTVI_VLM_IMAGE_TAG": "img-from-yml",
                },
            },
        )
        _patch_network(monkeypatch)

        resolved = dcu.build_resolved_env(recipe)

        assert resolved["PERCEPTION_TAG"] == "tag-from-yml"
        assert resolved["RTVI_VLM_IMAGE_TAG"] == "img-from-yml"

    def test_profile_scoped_block_applies_when_profile_matches(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        recipe = _make_recipe(
            tmp_path,
            _env_text(*_base_env("thor")),
            profile=dcu.PROFILE_BASE,
            hardware_profile_env_overrides={
                "thor": {
                    "base": {"VLM_NIM_KVCACHE_PERCENT": "0.2"},
                },
            },
        )
        _patch_network(monkeypatch)

        resolved = dcu.build_resolved_env(recipe)

        assert resolved["VLM_NIM_KVCACHE_PERCENT"] == "0.2"

    def test_profile_scoped_block_ignored_when_profile_differs(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        recipe = _make_recipe(
            tmp_path,
            _env_text(*_base_env("thor")),
            profile=dcu.PROFILE_BASE,
            hardware_profile_env_overrides={
                "thor": {
                    "search": {"VLM_NIM_KVCACHE_PERCENT": "0.2"},
                },
            },
        )
        _patch_network(monkeypatch)

        resolved = dcu.build_resolved_env(recipe)

        assert "VLM_NIM_KVCACHE_PERCENT" not in resolved

    def test_profile_mode_scoped_block_applies_when_both_match(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        recipe = _make_recipe(
            tmp_path,
            _env_text(*_base_env("igx")),
            profile=dcu.PROFILE_ALERTS,
            profile_mode="verification",
            edge_allowed_profiles=frozenset({dcu.PROFILE_ALERTS}),
            hardware_profile_env_overrides={
                "igx": {
                    "alerts.verification": {"RTVI_VLLM_GPU_MEMORY_UTILIZATION": "0.25"},
                },
            },
        )
        _patch_network(monkeypatch)

        resolved = dcu.build_resolved_env(recipe)

        assert resolved["RTVI_VLLM_GPU_MEMORY_UTILIZATION"] == "0.25"

    def test_profile_mode_scoped_block_ignored_when_mode_differs(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        recipe = _make_recipe(
            tmp_path,
            _env_text(*_base_env("igx")),
            profile=dcu.PROFILE_ALERTS,
            profile_mode="real-time",
            edge_allowed_profiles=frozenset({dcu.PROFILE_ALERTS}),
            hardware_profile_env_overrides={
                "igx": {
                    "alerts.verification": {"RTVI_VLLM_GPU_MEMORY_UTILIZATION": "0.25"},
                },
            },
        )
        _patch_network(monkeypatch)

        resolved = dcu.build_resolved_env(recipe)

        assert "RTVI_VLLM_GPU_MEMORY_UTILIZATION" not in resolved

    def test_profile_mode_scoped_block_ignored_when_profile_mode_unset(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        recipe = _make_recipe(
            tmp_path,
            _env_text(*_base_env("igx")),
            profile=dcu.PROFILE_ALERTS,
            # profile_mode left as None
            edge_allowed_profiles=frozenset({dcu.PROFILE_ALERTS}),
            hardware_profile_env_overrides={
                "igx": {
                    "alerts.verification": {"RTVI_VLLM_GPU_MEMORY_UTILIZATION": "0.25"},
                },
            },
        )
        _patch_network(monkeypatch)

        resolved = dcu.build_resolved_env(recipe)

        assert "RTVI_VLLM_GPU_MEMORY_UTILIZATION" not in resolved

    def test_precedence_within_scopes_mode_beats_profile_beats_hw(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Within yml: HW.<profile>.<mode> > HW.<profile> > HW-level str-key."""
        recipe = _make_recipe(
            tmp_path,
            _env_text(*_base_env("igx")),
            profile=dcu.PROFILE_ALERTS,
            profile_mode="verification",
            edge_allowed_profiles=frozenset({dcu.PROFILE_ALERTS}),
            hardware_profile_env_overrides={
                "igx": {
                    "RTVI_VLLM_GPU_MEMORY_UTILIZATION": "0.10",  # HW-level
                    "alerts": {"RTVI_VLLM_GPU_MEMORY_UTILIZATION": "0.20"},  # profile
                    "alerts.verification": {"RTVI_VLLM_GPU_MEMORY_UTILIZATION": "0.30"},  # mode
                },
            },
        )
        _patch_network(monkeypatch)

        resolved = dcu.build_resolved_env(recipe)

        assert resolved["RTVI_VLLM_GPU_MEMORY_UTILIZATION"] == "0.30"

    def test_env_overrides_still_beats_all_yml_scopes(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """env_overrides is the highest layer regardless of yml scoping depth."""
        recipe = _make_recipe(
            tmp_path,
            _env_text(*_base_env("igx")),
            profile=dcu.PROFILE_ALERTS,
            profile_mode="verification",
            edge_allowed_profiles=frozenset({dcu.PROFILE_ALERTS}),
            hardware_profile_env_overrides={
                "igx": {
                    "alerts.verification": {"RTVI_VLLM_GPU_MEMORY_UTILIZATION": "0.30"},
                },
            },
            env_overrides={"RTVI_VLLM_GPU_MEMORY_UTILIZATION": "0.99"},
        )
        _patch_network(monkeypatch)

        resolved = dcu.build_resolved_env(recipe)

        assert resolved["RTVI_VLLM_GPU_MEMORY_UTILIZATION"] == "0.99"

    def test_modeless_profile_honors_hw_root_and_profile_level_overrides(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """When the profile has no modes (profile_mode=None), HW-root str keys AND profile-level
        dict overrides still apply. Any mode-scoped block must be ignored. Regression guard
        for the bug where moving overrides under per-mode keys silently dropped them for
        modeless profiles."""
        recipe = _make_recipe(
            tmp_path,
            _env_text(*_base_env("thor")),
            profile=dcu.PROFILE_BASE,
            # profile_mode left as None — base has no modes
            hardware_profile_env_overrides={
                "thor": {
                    # HW-root: always applies
                    "PERCEPTION_TAG": "tag-from-hw-root",
                    "RTVI_VLM_IMAGE_TAG": "img-from-hw-root",
                    # profile-level: applies for any base run
                    "base": {"VLM_NIM_KVCACHE_PERCENT": "0.2"},
                    # mode-scoped under base (should be ignored — base has no modes)
                    "base.some-mode": {"VLM_NIM_KVCACHE_PERCENT": "0.99"},
                },
            },
        )
        _patch_network(monkeypatch)

        resolved = dcu.build_resolved_env(recipe)

        assert resolved["PERCEPTION_TAG"] == "tag-from-hw-root"
        assert resolved["RTVI_VLM_IMAGE_TAG"] == "img-from-hw-root"
        assert resolved["VLM_NIM_KVCACHE_PERCENT"] == "0.2"


class TestGenerateDryRunArtifacts:
    def test_generate_dry_run_artifacts_persists_profile_mode_in_generated_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        recipe = _make_recipe(
            tmp_path,
            _env_text(
                "MODE=2d_cv",
                "BP_PROFILE=bp_developer_alerts",
                "PROXY_MODE=direct",
                "HARDWARE_PROFILE=igx",
                "LLM_MODE=local_shared",
                "LLM_NAME=llm-a",
                "LLM_NAME_SLUG=llm-a-slug",
                "VLM_MODE=local",
                "VLM_NAME=nim_nvidia_cosmos-reason2-8b_hf-1208",
                "VLM_NAME_SLUG=none",
                "HOST_IP=10.0.0.9",
                "VLM_PORT=30099",
                "COMPOSE_PROFILES=${BP_PROFILE}_${MODE},${BP_PROFILE}_${MODE}_${HARDWARE_PROFILE},"
                "llm_${LLM_MODE}_${LLM_NAME_SLUG}",
            ),
            profile=dcu.PROFILE_ALERTS,
            env_overrides={"MODE": dcu.MODE_2D_VLM},
        )

        monkeypatch.setattr(dcu, "detect_internal_ip", lambda: pytest.fail("env HOST_IP should be used"))
        monkeypatch.setattr(dcu, "detect_external_ip", lambda: "10.0.0.9")
        monkeypatch.setattr(dcu, "read_etc_environment", lambda: {})
        monkeypatch.setattr(dcu, "apply_brev_proxy_env", lambda _merged, _brev_env_id: None)
        monkeypatch.setattr(dcu, "resolve_compose", lambda _config: "services: {}\n")

        resolved_env, env_path, compose_path = dcu.generate_dry_run_artifacts(recipe)

        assert resolved_env["MODE"] == dcu.MODE_2D_VLM
        assert "bp_developer_alerts_2d_vlm" in resolved_env["COMPOSE_PROFILES"]
        assert "vlm_local" not in resolved_env["COMPOSE_PROFILES"]
        assert "MODE=2d_vlm" in env_path.read_text()
        assert (
            "COMPOSE_PROFILES=bp_developer_alerts_2d_vlm,bp_developer_alerts_2d_vlm_igx,llm_local_shared_llm-a-slug"
            in env_path.read_text()
        )
        assert compose_path.read_text() == "services: {}\n"
