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
"""Library helpers for dev profile dry-run environment generation."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Dict, Final, Iterable, Literal, Mapping

from pydantic import BaseModel
import yaml

from .network_util import (
    apply_brev_proxy_env,
    detect_external_ip,
    detect_internal_ip,
    read_etc_environment,
)

SupportedProfile = Literal["base", "search", "lvs", "alerts"]
PROFILE_BASE: Final[str] = "base"
PROFILE_SEARCH: Final[str] = "search"
PROFILE_LVS: Final[str] = "lvs"
PROFILE_ALERTS: Final[str] = "alerts"
SUPPORTED_PROFILES: Final[frozenset[str]] = frozenset(
    {
        PROFILE_BASE,
        PROFILE_SEARCH,
        PROFILE_LVS,
        PROFILE_ALERTS,
    }
)
VALID_ENV_KEY: Final[re.Pattern[str]] = re.compile(r"^[A-Z][A-Z0-9_]*$")
UNRESOLVED_SHELL_VAR_PATTERN: Final[re.Pattern[str]] = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*")
PLACEHOLDER_VALUES: Final[frozenset[str]] = frozenset(
    {
        "<HOST_IP>",
        "/path/to/deployments",
        "/path/to/metropolis-apps-data",
    }
)

MODE_REMOTE: Final[str] = "remote"
SUPPORTED_RUNTIME_MODES: Final[frozenset[str]] = frozenset({"local", "local_shared", MODE_REMOTE})
MODEL_SLUG_NONE: Final[str] = "none"
THOR_VLM_PORT: Final[int] = 8018
COMPOSE_PROFILE_REQUIRED_KEYS: Final[tuple[str, ...]] = ("MODE", "BP_PROFILE", "PROXY_MODE", "LLM_NAME_SLUG", "VLM_NAME_SLUG")



class ValidationError(ValueError):
    """Raised when user-provided input is invalid."""


class EdgeDeviceIdsInput(BaseModel):
    llm: str
    vlm: str


class HardwareResolutionInput(BaseModel):
    supported_profiles: tuple[str, ...]
    edge_profiles: tuple[str, ...]
    edge_allowed_profiles: tuple[str, ...]
    edge_device_ids: EdgeDeviceIdsInput
    thor_base_profiles: tuple[str, ...]


class LlmResolutionInput(BaseModel):
    supported_models: dict[str, str]


class VlmResolutionInput(BaseModel):
    supported_models: dict[str, str]
    thor_base_overrides: dict[str, str]


class ModelResolutionInput(BaseModel):
    hardware: HardwareResolutionInput
    llm: LlmResolutionInput
    vlm: VlmResolutionInput


@dataclass(frozen=True)
class DryRunRecipe:
    profile: SupportedProfile
    env_overrides: Dict[str, str]
    ngc_cli_api_key: str | None
    nvidia_api_key: str | None
    output_env_file: Path
    output_compose_file: Path
    deployments_dir: Path
    mdx_data_dir: Path
    compose_file: Path
    source_env_file: Path
    supported_hardware_profiles: frozenset[str]
    edge_hardware_profiles: frozenset[str]
    edge_allowed_profiles: frozenset[str]
    edge_device_ids: Mapping[str, str]
    thor_base_profiles: frozenset[str]
    supported_llm_models: Mapping[str, str]
    supported_vlm_models: Mapping[str, str]
    thor_base_vlm_overrides: Mapping[str, str]


def create_dry_run_recipe(
    *,
    profile: str,
    env_overrides: Dict[str, str],
    ngc_cli_api_key: str | None = None,
    nvidia_api_key: str | None = None,
    model_resolution: Any,
    output_env_file: str,
    output_compose_file: str,
    deployments_dir: str,
    mdx_data_dir: str,
) -> DryRunRecipe:
    profile = profile.strip()
    if profile not in SUPPORTED_PROFILES:
        raise ValidationError(f"Unsupported profile '{profile}'. Supported: {sorted(SUPPORTED_PROFILES)}")


    # TODL: Need to make them configurable - deployments_path and source_env_file paths and compose_file path
    deployments_path = Path(deployments_dir).resolve()
    if not deployments_path.is_dir():
        raise ValidationError(f"Deployments directory does not exist: {deployments_path}")

    compose_file = deployments_path / "compose.yml"
    if not compose_file.is_file():
        raise ValidationError(f"Compose file not found: {compose_file}")

    source_env_file = deployments_path / "developer-workflow" / f"dev-profile-{profile}" / ".env"
    if not source_env_file.is_file():
        raise ValidationError(f"Profile source .env not found: {source_env_file}")

    try:
        model_resolution = ModelResolutionInput.model_validate(model_resolution, from_attributes=True)
    except Exception as exc:
        raise ValidationError(
            "model_resolution must include hardware, llm, and vlm sections with required keys."
        ) from exc

    return DryRunRecipe(
        profile=profile,  # type: ignore[arg-type]
        env_overrides=dict(env_overrides),
        ngc_cli_api_key=(ngc_cli_api_key or "").strip() or None,
        nvidia_api_key=(nvidia_api_key or "").strip() or None,
        output_env_file=Path(output_env_file).resolve(),
        output_compose_file=Path(output_compose_file).resolve(),
        deployments_dir=deployments_path,
        mdx_data_dir=Path(mdx_data_dir).expanduser().resolve(),
        compose_file=compose_file,
        source_env_file=source_env_file,
        supported_hardware_profiles=frozenset(model_resolution.hardware.supported_profiles),
        edge_hardware_profiles=frozenset(model_resolution.hardware.edge_profiles),
        edge_allowed_profiles=frozenset(model_resolution.hardware.edge_allowed_profiles),
        edge_device_ids=MappingProxyType(
            {
                "llm": model_resolution.hardware.edge_device_ids.llm,
                "vlm": model_resolution.hardware.edge_device_ids.vlm,
            }
        ),
        thor_base_profiles=frozenset(model_resolution.hardware.thor_base_profiles),
        supported_llm_models=MappingProxyType(dict(model_resolution.llm.supported_models)),
        supported_vlm_models=MappingProxyType(dict(model_resolution.vlm.supported_models)),
        thor_base_vlm_overrides=MappingProxyType(dict(model_resolution.vlm.thor_base_overrides)),
    )


def parse_env_overrides(entries: list[str]) -> Dict[str, str]:
    overrides: Dict[str, str] = {}
    for raw in entries:
        if "=" not in raw:
            raise ValidationError(f"Invalid --env entry '{raw}'. Expected KEY=VALUE.")
        key, value = raw.split("=", 1)
        key = key.strip()
        if not VALID_ENV_KEY.match(key):
            raise ValidationError(f"Invalid env key '{key}'. Must match {VALID_ENV_KEY.pattern}.")
        overrides[key] = value
    return overrides


def parse_env_file(path: Path) -> Dict[str, str]:
    env: Dict[str, str] = {}
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip("'").strip('"')
    return env


def first_non_placeholder(values: Iterable[str]) -> str:
    for value in values:
        normalized = value.strip()
        if not normalized or (normalized.startswith("<") and normalized.endswith(">")):
            continue
        # Treat unresolved shell-style references as placeholders
        # (e.g. ${HOST_IP}, $HOST_IP, http://${HOST_IP}:30888).
        if "${" in normalized or UNRESOLVED_SHELL_VAR_PATTERN.fullmatch(normalized):
            continue
        if normalized in PLACEHOLDER_VALUES:
            continue
        return normalized
    return ""


def _set_env_line(lines: list[str], key: str, value: str) -> None:
    exact = re.compile(rf"^{re.escape(key)}=.*$")
    commented = re.compile(rf"^#[ \t]*{re.escape(key)}=.*$")
    for i, line in enumerate(lines):
        if exact.match(line) or commented.match(line):
            lines[i] = f"{key}={value}"
            return
    lines.append(f"{key}={value}")


def build_resolved_env(config: DryRunRecipe) -> Dict[str, str]:
    merged = parse_env_file(config.source_env_file)
    merged.update(config.env_overrides)
    if config.profile == PROFILE_SEARCH and "VLM_MODE" not in config.env_overrides:
        # For search, default to local VLM unless user explicitly overrides VLM_MODE.
        merged["VLM_MODE"] = "local"
    if config.ngc_cli_api_key and not merged.get("NGC_CLI_API_KEY", "").strip():
        merged["NGC_CLI_API_KEY"] = config.ngc_cli_api_key
    if config.nvidia_api_key and not merged.get("NVIDIA_API_KEY", "").strip():
        merged["NVIDIA_API_KEY"] = config.nvidia_api_key

    host_ip = first_non_placeholder([config.env_overrides.get("HOST_IP", ""), merged.get("HOST_IP", "")]) or detect_internal_ip()
    if not host_ip:
        raise ValidationError("Could not determine HOST_IP. Set --env HOST_IP=<ip>.")
    external_ip = (
        first_non_placeholder(
            [
                config.env_overrides.get("EXTERNAL_IP", ""),
                config.env_overrides.get("EXTERNALLY_ACCESSIBLE_IP", ""),
                merged.get("EXTERNAL_IP", ""),
                merged.get("EXTERNALLY_ACCESSIBLE_IP", ""),
            ]
        )
        or detect_external_ip()
        or host_ip
    )

    merged["MDX_SAMPLE_APPS_DIR"] = first_non_placeholder([merged.get("MDX_SAMPLE_APPS_DIR", ""), str(config.deployments_dir)])
    merged["MDX_DATA_DIR"] = first_non_placeholder(
        [
            config.env_overrides.get("MDX_DATA_DIR", ""),
            str(config.mdx_data_dir),
        ]
    )
    merged["HOST_IP"] = host_ip
    merged["EXTERNALLY_ACCESSIBLE_IP"] = external_ip
    if external_ip != host_ip:
        merged["EXTERNAL_IP"] = external_ip

    brev_env_id = first_non_placeholder([config.env_overrides.get("BREV_ENV_ID", ""), os.environ.get("BREV_ENV_ID", ""), read_etc_environment().get("BREV_ENV_ID", "")])
    if brev_env_id:
        apply_brev_proxy_env(merged, brev_env_id)

    if merged.get("HARDWARE_PROFILE", "") not in config.supported_hardware_profiles:
        raise ValidationError(f"Invalid HARDWARE_PROFILE '{merged.get('HARDWARE_PROFILE', '')}'.")
    if merged.get("HARDWARE_PROFILE", "") in config.edge_hardware_profiles and config.profile not in config.edge_allowed_profiles:
        raise ValidationError(
            f"Invalid HARDWARE_PROFILE '{merged.get('HARDWARE_PROFILE', '')}' for profile '{config.profile}'. "
            f"Edge hardware profiles are only supported for {sorted(config.edge_allowed_profiles)}."
        )
    if merged.get("LLM_MODE", "") not in SUPPORTED_RUNTIME_MODES:
        raise ValidationError(f"Invalid LLM_MODE '{merged.get('LLM_MODE', '')}'.")
    if merged.get("VLM_MODE", "") not in SUPPORTED_RUNTIME_MODES:
        raise ValidationError(f"Invalid VLM_MODE '{merged.get('VLM_MODE', '')}'.")

    if merged["LLM_MODE"] == MODE_REMOTE:
        merged["LLM_NAME_SLUG"] = MODEL_SLUG_NONE
        if not merged.get("LLM_BASE_URL", "").strip():
            raise ValidationError("LLM_BASE_URL is required when LLM_MODE=remote.")
    else:
        llm_name = merged.get("LLM_NAME", "")
        if llm_name not in config.supported_llm_models:
            raise ValidationError(
                f"Invalid LLM_NAME for profile '{config.profile}'. "
                f"Supported values: {sorted(config.supported_llm_models.keys())}"
            )
        merged["LLM_NAME_SLUG"] = config.supported_llm_models[llm_name]

    if merged["VLM_MODE"] == MODE_REMOTE:
        merged["VLM_NAME_SLUG"] = MODEL_SLUG_NONE
        if not merged.get("VLM_BASE_URL", "").strip():
            raise ValidationError("VLM_BASE_URL is required when VLM_MODE=remote.")
    else:
        vlm_name = merged.get("VLM_NAME", "")
        if vlm_name not in config.supported_vlm_models:
            raise ValidationError(
                f"Invalid VLM_NAME for profile '{config.profile}'. "
                f"Supported values: {sorted(config.supported_vlm_models.keys())}"
            )
        merged["VLM_NAME_SLUG"] = config.supported_vlm_models[vlm_name]

    if merged.get("HARDWARE_PROFILE", "") in config.edge_hardware_profiles:
        merged["LLM_DEVICE_ID"] = config.edge_device_ids["llm"]
        merged["VLM_DEVICE_ID"] = config.edge_device_ids["vlm"]

    if merged.get("HARDWARE_PROFILE", "") in config.thor_base_profiles and config.profile == PROFILE_BASE:
        merged.update(config.thor_base_vlm_overrides)
        merged["VLM_BASE_URL"] = f"http://{host_ip}:{THOR_VLM_PORT}"

    if not all(merged.get(key, "") for key in COMPOSE_PROFILE_REQUIRED_KEYS):
        raise ValidationError("Could not compute COMPOSE_PROFILES due to missing required env keys.")
    merged["COMPOSE_PROFILES"] = (
        f"{merged['BP_PROFILE']}_{merged['MODE']},"
        f"{merged['BP_PROFILE']}_{merged['MODE']}_{merged['HARDWARE_PROFILE']},"
        f"{merged['BP_PROFILE']}_{merged['MODE']}_{merged['PROXY_MODE']},"
        f"llm_{merged['LLM_MODE']}_{merged['LLM_NAME_SLUG']},"
        f"vlm_{merged['VLM_MODE']}_{merged['VLM_NAME_SLUG']}"
    )
    return merged


def render_generated_env(source_env_file: Path, resolved: Dict[str, str]) -> str:
    lines = source_env_file.read_text().splitlines()
    for key, value in sorted(resolved.items()):
        _set_env_line(lines, key, value)
    return "\n".join(lines) + "\n"


def resolve_compose(config: DryRunRecipe) -> str:
    try:
        result = subprocess.run(
            ["docker", "compose", "-f", str(config.compose_file), "--env-file", str(config.output_env_file), "config"],
            cwd=str(config.deployments_dir),
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("docker command not found. Install Docker with Compose v2.") from exc
    if result.returncode != 0:
        raise RuntimeError(f"docker compose config failed.\nstdout:\n{result.stdout}\n\nstderr:\n{result.stderr}")
    return sanitize_resolved_compose(result.stdout)


def run_compose_command(config: DryRunRecipe, env_file: Path, compose_file: Path, *args: str) -> None:
    compose_env = os.environ.copy()
    # Prefer plain, non-ANSI output so status logs are visible/persistent in non-interactive captures.
    compose_env.setdefault("COMPOSE_PROGRESS", "plain")
    compose_env.setdefault("COMPOSE_ANSI", "never")
    try:
        result = subprocess.run(
            ["docker", "compose", "-f", str(compose_file), "--env-file", str(env_file), *args],
            cwd=str(config.deployments_dir),
            env=compose_env,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("docker command not found. Install Docker with Compose v2.") from exc
    if result.returncode != 0:
        raise RuntimeError(
            "docker compose command failed.\n"
            f"command: docker compose -f {compose_file} --env-file {env_file} {' '.join(args)}\n"
            f"exit_code: {result.returncode}"
        )


def sanitize_resolved_compose(compose_text: str) -> str:
    """Remove dangling depends_on references from resolved compose output."""

    parsed = yaml.safe_load(compose_text)
    if not isinstance(parsed, dict):
        return compose_text

    services = parsed.get("services")
    if not isinstance(services, dict):
        return compose_text

    defined_services = set(services.keys())
    for service_def in services.values():
        if not isinstance(service_def, dict):
            continue
        depends_on = service_def.get("depends_on")
        if depends_on is None:
            continue

        if isinstance(depends_on, list):
            filtered = [dep for dep in depends_on if dep in defined_services]
            if filtered:
                service_def["depends_on"] = filtered
            else:
                service_def.pop("depends_on", None)
        elif isinstance(depends_on, dict):
            filtered = {dep: cfg for dep, cfg in depends_on.items() if dep in defined_services}
            if filtered:
                service_def["depends_on"] = filtered
            else:
                service_def.pop("depends_on", None)

    return yaml.safe_dump(parsed, sort_keys=False)


def generate_dry_run_artifacts(config: DryRunRecipe) -> tuple[Dict[str, str], Path, Path]:
    resolved_env = build_resolved_env(config)
    config.output_env_file.parent.mkdir(parents=True, exist_ok=True)
    config.output_env_file.write_text(render_generated_env(config.source_env_file, resolved_env))
    config.output_compose_file.parent.mkdir(parents=True, exist_ok=True)
    config.output_compose_file.write_text(resolve_compose(config))
    return resolved_env, config.output_env_file, config.output_compose_file


def print_configuration_summary(config: DryRunRecipe, resolved_env: Dict[str, str]) -> None:
    print("Configuration valid.")
    print(f"  Profile:  {config.profile}")
    print(f"  Hardware: {resolved_env.get('HARDWARE_PROFILE', '(unset)')}")
    print(f"  Source:   {config.deployments_dir}")
    print(f"  Host IP:  {resolved_env.get('HOST_IP', '(unset)')}")
    print(f"  External: {resolved_env.get('EXTERNALLY_ACCESSIBLE_IP', '(unset)')}")
