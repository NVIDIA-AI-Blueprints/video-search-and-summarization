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

import base64
import binascii
from collections.abc import Mapping as MappingABC
from dataclasses import dataclass
from dataclasses import field
import hashlib
import hmac
import ipaddress
import os
import re
import subprocess
from types import MappingProxyType
from typing import TYPE_CHECKING
from typing import Any
from typing import Final
from typing import Literal

from pydantic import BaseModel
from pydantic import Field
from pydantic import field_validator
import yaml

from .brev_util import apply_brev_proxy_env
from .brev_util import brev_environment_id
from .network_util import detect_external_ip
from .network_util import detect_internal_ip
from .storage import resolve_config_path
from .storage import resolve_required_absolute_file

if TYPE_CHECKING:
    from collections.abc import Iterable
    from collections.abc import Mapping
    from pathlib import Path

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
ENV_VAR_INTERPOLATION_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\$\{(?P<braced>[A-Za-z_][A-Za-z0-9_]*)\}|\$(?P<bare>[A-Za-z_][A-Za-z0-9_]*)"
)
PLACEHOLDER_VALUES: Final[frozenset[str]] = frozenset(
    {
        "<HOST_IP>",
        "/path/to/deploy/docker",
        "/path/to/vss-apps-data",
    }
)

MODE_REMOTE: Final[str] = "remote"
MODE_LOCAL: Final[str] = "local"
MODE_LOCAL_SHARED: Final[str] = "local_shared"
MODE_2D_CV: Final[str] = "2d_cv"
MODE_2D_VLM: Final[str] = "2d_vlm"
SUPPORTED_RUNTIME_MODES: Final[frozenset[str]] = frozenset({MODE_LOCAL, MODE_LOCAL_SHARED, MODE_REMOTE})
MODEL_SLUG_NONE: Final[str] = "none"
THOR_VLM_PORT: Final[int] = 8018
DEFAULT_ALERTS_VLM_PORT: Final[int] = 30082
# Scheme for intra-host service URLs (rtvi-vlm, NIM containers). Plain HTTP
# because these containers do not terminate TLS and traffic stays within the
# host loopback/LAN trust boundary.
INTERNAL_URL_SCHEME: Final[str] = "http"  # NOSONAR S5332
EDGE_ALERTS_RTVI_INPUT_WIDTH: Final[str] = "860"
EDGE_ALERTS_RTVI_INPUT_HEIGHT: Final[str] = "467"
EDGE_ALERTS_RTVI_FPS: Final[str] = "20"
EDGE_PERCEPTION_DOCKERFILE_PREFIX: Final[str] = "EDGE-"
EDGE_ALERTS_VLM_AS_VERIFIER_CONFIG_FILE_PREFIX: Final[str] = "EDGE-LOCAL-VLM-"
COMPOSE_PROFILE_REQUIRED_KEYS: Final[tuple[str, ...]] = (
    "MODE",
    "BP_PROFILE",
    "LLM_NAME_SLUG",
    "VLM_NAME_SLUG",
)
_COMPOSE_SHELL_ENV_BLOCKLIST: Final[frozenset[str]] = frozenset({"LLM_MODE", "VLM_MODE"})
_TRUE_VALUES: Final[frozenset[str]] = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES: Final[frozenset[str]] = frozenset({"0", "false", "no", "off", ""})
DEFAULT_AGENT_GATEWAY_PORT: Final[int] = 18090
MAX_AGENT_CAPABILITY_RECEIPT_BYTES: Final[int] = 256_000


class ValidationError(ValueError):
    """Raised when user-provided input is invalid."""


class EdgeDeviceIdsInput(BaseModel):
    llm: str
    vlm: str
    rt_vlm: str
    rt_cv: str


class HardwareResolutionInput(BaseModel):
    edge_profiles: tuple[str, ...]
    edge_allowed_profiles: tuple[str, ...]
    edge_device_ids: EdgeDeviceIdsInput
    # Keys define the set of supported hardware profiles.
    # Values are env overrides (None/{} = supported, no overrides).
    hardware_profiles: dict[str, dict[str, str | dict[str, str]]] = Field(default_factory=dict)

    @field_validator("hardware_profiles", mode="before")
    @classmethod
    def _coerce_null_overrides_to_empty(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        return {k: (v if v is not None else {}) for k, v in value.items()}


class ModelResolutionInput(BaseModel):
    hardware: HardwareResolutionInput


@dataclass(frozen=True)
class DryRunRecipe:
    profile: SupportedProfile
    env_overrides: dict[str, str]
    ngc_cli_api_key: str | None
    nvidia_api_key: str | None
    hardware_profile: str | None
    external_ip: str | None
    openai_api_key: str | None
    llm_endpoint_url: str | None
    llm_model_type: str | None
    llm_name: str | None
    vlm_name: str | None
    vlm_endpoint_url: str | None
    vlm_model_type: str | None
    llm_enable_thinking: str | None
    nim_kvcache_percent: str | None
    rtvi_vllm_gpu_memory_utilization: str | None
    profile_mode: str | None
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
    profile_mode_to_env_modes: Mapping[str, Mapping[str, str]]
    profile_env_override_file: Path
    containers_env_file: Path
    hardware_profile_env_overrides: Mapping[str, Mapping[str, str | Mapping[str, str]]] = field(
        default_factory=lambda: MappingProxyType({})
    )


def create_dry_run_recipe(
    *,
    profile: str,
    env_overrides: dict[str, str],
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
    model_resolution: Any,
    output_env_file: str,
    output_compose_file: str,
    deployments_dir: str,
    mdx_data_dir: str,
    profile_mode_to_env_modes: dict[str, dict[str, str]] | None,
    source_compose_yaml: str,
    source_env: str,
) -> DryRunRecipe:
    profile = profile.strip()
    if profile not in SUPPORTED_PROFILES:
        raise ValidationError(f"Unsupported profile '{profile}'. Supported: {sorted(SUPPORTED_PROFILES)}")

    deployments_path = resolve_config_path(deployments_dir)
    if not deployments_path.is_dir():
        raise ValidationError(f"Deployments directory does not exist: {deployments_path}")

    compose_file = resolve_required_absolute_file(
        source_compose_yaml,
        field_name="source_compose_yaml",
        missing_label="Compose file",
        error_type=ValidationError,
    )

    try:
        resolved_source_env = source_env.strip().format(profile=profile)
    except (IndexError, KeyError, ValueError) as exc:
        raise ValidationError("source_env format is invalid. Only '{profile}' placeholder is supported.") from exc
    source_env_file = resolve_required_absolute_file(
        resolved_source_env,
        field_name="source_env",
        missing_label="Profile source .env",
        error_type=ValidationError,
    )

    # overrides.env is mandatory and must live next to the profile .env.
    profile_env_override_file = resolve_required_absolute_file(
        str(source_env_file.parent / "overrides.env"),
        field_name="overrides.env",
        missing_label="Profile overrides.env",
        error_type=ValidationError,
    )

    containers_env_file = resolve_required_absolute_file(
        str(deployments_path / "containers.env"),
        field_name="containers.env",
        missing_label="Containers env",
        error_type=ValidationError,
    )

    try:
        model_resolution = ModelResolutionInput.model_validate(model_resolution, from_attributes=True)
    except Exception as exc:
        raise ValidationError("model_resolution must include hardware section with required keys.") from exc

    return DryRunRecipe(
        profile=profile,  # type: ignore[arg-type]
        env_overrides=dict(env_overrides),
        ngc_cli_api_key=(ngc_cli_api_key or "").strip() or None,
        nvidia_api_key=(nvidia_api_key or "").strip() or None,
        hardware_profile=(hardware_profile or "").strip() or None,
        external_ip=(external_ip or "").strip() or None,
        openai_api_key=(openai_api_key or "").strip() or None,
        llm_endpoint_url=(llm_endpoint_url or "").strip() or None,
        llm_model_type=(llm_model_type or "").strip() or None,
        llm_name=(llm_name or "").strip() or None,
        vlm_name=(vlm_name or "").strip() or None,
        vlm_endpoint_url=(vlm_endpoint_url or "").strip() or None,
        vlm_model_type=(vlm_model_type or "").strip() or None,
        llm_enable_thinking=(llm_enable_thinking or "").strip() or None,
        nim_kvcache_percent=(nim_kvcache_percent or "").strip() or None,
        rtvi_vllm_gpu_memory_utilization=(rtvi_vllm_gpu_memory_utilization or "").strip() or None,
        profile_mode=(profile_mode or "").strip() or None,
        output_env_file=resolve_config_path(output_env_file),
        output_compose_file=resolve_config_path(output_compose_file),
        deployments_dir=deployments_path,
        mdx_data_dir=resolve_config_path(mdx_data_dir),
        compose_file=compose_file,
        source_env_file=source_env_file,
        profile_env_override_file=profile_env_override_file,
        containers_env_file=containers_env_file,
        supported_hardware_profiles=frozenset(model_resolution.hardware.hardware_profiles.keys()),
        edge_hardware_profiles=frozenset(model_resolution.hardware.edge_profiles),
        edge_allowed_profiles=frozenset(model_resolution.hardware.edge_allowed_profiles),
        edge_device_ids=MappingProxyType(
            {
                "llm": model_resolution.hardware.edge_device_ids.llm,
                "vlm": model_resolution.hardware.edge_device_ids.vlm,
                "rt_vlm": model_resolution.hardware.edge_device_ids.rt_vlm,
                "rt_cv": model_resolution.hardware.edge_device_ids.rt_cv,
            }
        ),
        profile_mode_to_env_modes=MappingProxyType(
            {profile: MappingProxyType(dict(modes)) for profile, modes in (profile_mode_to_env_modes or {}).items()}
        ),
        hardware_profile_env_overrides=MappingProxyType(
            {
                hw: MappingProxyType(
                    {
                        scope: (MappingProxyType(dict(value)) if isinstance(value, dict) else value)
                        for scope, value in overrides.items()
                    }
                )
                for hw, overrides in model_resolution.hardware.hardware_profiles.items()
            }
        ),
    )


def parse_env_overrides(entries: list[str]) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for raw in entries:
        if "=" not in raw:
            raise ValidationError(f"Invalid --env entry '{raw}'. Expected KEY=VALUE.")
        key, value = raw.split("=", 1)
        key = key.strip()
        if not VALID_ENV_KEY.match(key):
            raise ValidationError(f"Invalid env key '{key}'. Must match {VALID_ENV_KEY.pattern}.")
        overrides[key] = _validate_env_value(key, value)
    return overrides


def _validate_env_value(key: str, value: str) -> str:
    if "\n" in value or "\r" in value:
        raise ValidationError(f"Invalid env value for '{key}'. Newlines are not allowed.")
    return value


def parse_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip("'").strip('"')
    return env


def load_shell_env_file(path: Path, base_env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Load an env file whose values use shell-style ``${VAR:-default}`` self-defaults.

    Source the file with bash exactly as ``dev-profile.sh`` does instead of duplicating
    shell parameter expansion in Python.
    """

    keys = list(parse_env_file(path))
    result = subprocess.run(
        [
            "bash",
            "-c",
            'set -a; source "$1"; shift; for key in "$@"; do printf "%s\\0%s\\0" "$key" "${!key}"; done',
            "bash",
            str(path),
            *keys,
        ],
        check=False,
        capture_output=True,
        env=dict(os.environ if base_env is None else base_env),
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to resolve shell environment file {path}.\nstderr:\n{result.stderr.decode(errors='replace')}"
        )
    fields = result.stdout.split(b"\0")
    if fields and not fields[-1]:
        fields.pop()
    if len(fields) % 2:
        raise RuntimeError(f"Failed to parse resolved shell environment file {path}.")
    return {fields[index].decode(): fields[index + 1].decode() for index in range(0, len(fields), 2)}


def load_profile_env(path: Path) -> dict[str, str]:
    """Load a profile env file plus its sibling overrides.env when present."""

    env = parse_env_file(path)
    overrides = path.with_name("overrides.env")
    if path.name == ".env" and overrides.is_file():
        env.update(parse_env_file(overrides))
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
    value = _validate_env_value(key, value)
    exact = re.compile(rf"^{re.escape(key)}=.*$")
    for i, line in enumerate(lines):
        if exact.match(line):
            lines[i] = f"{key}={value}"
            return
    lines.append(f"{key}={value}")


def profile_mode_to_env_mode(
    profile: str,
    profile_mode: str,
    profile_mode_to_env_modes: Mapping[str, Mapping[str, str]],
) -> str:
    normalized = profile_mode.strip()
    profile_modes = profile_mode_to_env_modes.get(profile, {})
    resolved_mode = profile_modes.get(normalized)
    if resolved_mode:
        return resolved_mode
    supported_modes = sorted(profile_modes)
    if not supported_modes:
        raise ValidationError(f"profile_mode is not configured for profile '{profile}'.")
    raise ValidationError(
        f"Invalid profile_mode '{profile_mode}' for profile '{profile}'. Supported values: {supported_modes}."
    )


def resolve_and_apply_profile_mode(
    profile: str,
    profile_mode: str | None,
    profile_mode_to_env_modes: Mapping[str, Mapping[str, str]],
    env_overrides: dict[str, str],
) -> None:
    """Validate ``profile_mode`` against per-profile mode requirements and write the
    resolved ``MODE`` into ``env_overrides``.

    Rules:
    - If the profile has modes configured (present in ``profile_mode_to_env_modes``),
      ``profile_mode`` is required; otherwise raises ``ValidationError``.
    - If the profile has no modes, ``profile_mode`` must be None; otherwise raises.
    - On a valid combination, sets ``env_overrides["MODE"]`` to the resolved env-mode value.
    - If ``env_overrides`` already carries a conflicting ``MODE``, raises ``ValidationError``.
    """
    profile_supports_modes = bool(profile_mode_to_env_modes.get(profile))
    if profile_mode is not None:
        if not profile_supports_modes:
            raise ValidationError(
                f"profile_mode is not supported when profile={profile!r} "
                f"(no modes configured in profile_mode_to_env_modes)."
            )
        resolved_mode = profile_mode_to_env_mode(profile, profile_mode, profile_mode_to_env_modes)
        existing_mode = env_overrides.get("MODE")
        if existing_mode is not None and existing_mode != resolved_mode:
            raise ValidationError(f"profile_mode={profile_mode!r} conflicts with env override MODE={existing_mode!r}.")
        env_overrides["MODE"] = resolved_mode
    elif profile_supports_modes:
        supported = sorted(profile_mode_to_env_modes[profile])
        raise ValidationError(f"profile_mode is required when profile={profile!r}. Supported values: {supported}.")


def resolve_env_interpolation(value: str, env: Mapping[str, str], *, max_depth: int = 10) -> str:
    """Resolve simple $VAR and ${VAR} references using env values."""

    def _replace(match: re.Match[str]) -> str:
        key = match.group("braced") or match.group("bare")
        return env.get(key, "")

    resolved = value
    for _ in range(max_depth):
        next_resolved = ENV_VAR_INTERPOLATION_PATTERN.sub(_replace, resolved)
        if next_resolved == resolved:
            return next_resolved
        resolved = next_resolved
    return resolved


def expand_env_value_references(env: dict[str, str]) -> None:
    """Expand ``${VAR}``/``$VAR`` references in env values using other env values, in place.

    Only references to keys present in ``env`` are substituted; unknown references are left
    intact (so literal ``$`` and runtime-only vars survive). Chained references (A -> ${B},
    B -> ${C}) are resolved with a bounded fixed-point iteration; any remaining references
    after the cap (e.g. cycles) are left as-is.
    """

    def _sub_known(value: str) -> str:
        def _replace(match: re.Match[str]) -> str:
            key = match.group("braced") or match.group("bare")
            return env[key] if key in env else match.group(0)

        return ENV_VAR_INTERPOLATION_PATTERN.sub(_replace, value)

    for _ in range(len(env) + 1):
        changed = False
        for key, value in env.items():
            if "$" not in value:
                continue
            expanded = _sub_known(value)
            if expanded != value:
                env[key] = expanded
                changed = True
        if not changed:
            break


def resolve_compose_profiles(merged: Mapping[str, str], profile: SupportedProfile) -> str:
    """Resolve COMPOSE_PROFILES from the profile env template, with a legacy fallback."""

    # COMPOSE_PROFILES=${BP_PROFILE}_${MODE},${BP_PROFILE}_${MODE}_${HARDWARE_PROFILE},llm_${LLM_MODE}_${LLM_NAME_SLUG}
    configured_profiles = merged.get("COMPOSE_PROFILES", "").strip()
    if configured_profiles:
        return resolve_env_interpolation(configured_profiles, merged)

    # fallback to old compose profiles
    compose_profiles = [f"{merged['BP_PROFILE']}_{merged['MODE']}"]
    if profile in {PROFILE_BASE, PROFILE_ALERTS}:
        compose_profiles.append(f"{merged['BP_PROFILE']}_{merged['MODE']}_{merged['HARDWARE_PROFILE']}")
    compose_profiles.extend(
        [
            f"llm_{merged['LLM_MODE']}_{merged['LLM_NAME_SLUG']}",
            f"vlm_{merged['VLM_MODE']}_{merged['VLM_NAME_SLUG']}",
        ]
    )
    return ",".join(compose_profiles)


def apply_agent_gateway_env(merged: dict[str, str]) -> None:
    """Add the production UI-to-harness services and defaults when requested.

    The gateway runs with host networking so it can reach notebook-managed
    OpenClaw/Hermes forwards on host loopback. It binds only to Docker's private
    bridge address; the UI reaches that address through ``host-gateway``.
    """

    raw_enabled = merged.get("VSS_AGENT_GATEWAY_ENABLED", "").strip().lower()
    if raw_enabled in _FALSE_VALUES:
        return
    if raw_enabled not in _TRUE_VALUES:
        raise ValidationError("VSS_AGENT_GATEWAY_ENABLED must be true or false.")

    if not merged.get("VSS_AGENT_GATEWAY_TOKEN", "").strip():
        raise ValidationError("VSS_AGENT_GATEWAY_TOKEN is required when VSS_AGENT_GATEWAY_ENABLED=true.")
    if not merged.get("VSS_AGENT_BACKEND_URL", "").strip():
        raise ValidationError("VSS_AGENT_BACKEND_URL is required when VSS_AGENT_GATEWAY_ENABLED=true.")

    encoded_receipt = merged.get("VSS_AGENT_GATEWAY_CAPABILITIES_B64", "").strip()
    receipt_digest = merged.get("VSS_AGENT_GATEWAY_CAPABILITIES_SHA256", "").strip().lower()
    if not encoded_receipt:
        raise ValidationError(
            "VSS_AGENT_GATEWAY_CAPABILITIES_B64 is required when "
            "VSS_AGENT_GATEWAY_ENABLED=true; run attach_vss_agent.py before resolution."
        )
    if not re.fullmatch(r"[0-9a-f]{64}", receipt_digest):
        raise ValidationError("VSS_AGENT_GATEWAY_CAPABILITIES_SHA256 must be a lowercase SHA-256 digest.")
    try:
        raw_receipt = base64.b64decode(encoded_receipt, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValidationError("VSS_AGENT_GATEWAY_CAPABILITIES_B64 must be strict base64.") from exc
    if not raw_receipt or len(raw_receipt) > MAX_AGENT_CAPABILITY_RECEIPT_BYTES:
        raise ValidationError(
            f"Decoded VSS agent capability receipt must be 1..{MAX_AGENT_CAPABILITY_RECEIPT_BYTES} bytes."
        )
    if not hmac.compare_digest(hashlib.sha256(raw_receipt).hexdigest(), receipt_digest):
        raise ValidationError("VSS agent capability receipt digest does not match.")
    expected_runtime_ref = merged.get("VSS_AGENT_GATEWAY_EXPECTED_RUNTIME_REF", "").strip().lower()
    if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", expected_runtime_ref):
        raise ValidationError("VSS_AGENT_GATEWAY_EXPECTED_RUNTIME_REF must be a full Git commit ID.")

    bind_host = merged.get("VSS_AGENT_GATEWAY_BIND_HOST", "").strip()
    if not bind_host:
        raise ValidationError(
            "VSS_AGENT_GATEWAY_BIND_HOST is required when VSS_AGENT_GATEWAY_ENABLED=true; "
            "set it to the private gateway from `docker network inspect bridge`."
        )
    try:
        bind_address = ipaddress.ip_address(bind_host)
    except ValueError as exc:
        raise ValidationError("VSS_AGENT_GATEWAY_BIND_HOST must be a private IPv4 address.") from exc
    if (
        bind_address.version != 4
        or not bind_address.is_private
        or bind_address.is_loopback
        or bind_address.is_unspecified
    ):
        raise ValidationError("VSS_AGENT_GATEWAY_BIND_HOST must be a private, non-loopback IPv4 address.")

    raw_port = merged.get("VSS_AGENT_GATEWAY_PORT", "").strip() or str(DEFAULT_AGENT_GATEWAY_PORT)
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise ValidationError("VSS_AGENT_GATEWAY_PORT must be an integer.") from exc
    if not 1024 <= port <= 65535:
        raise ValidationError("VSS_AGENT_GATEWAY_PORT must be between 1024 and 65535.")

    merged["VSS_AGENT_GATEWAY_ENABLED"] = "true"
    merged["VSS_AGENT_GATEWAY_BIND_HOST"] = bind_host
    merged["VSS_AGENT_GATEWAY_PORT"] = str(port)
    merged["VSS_AGENT_GATEWAY_REQUIRE_CAPABILITIES"] = "true"
    # The gateway is an HTTP/SSE transport. Lock every UI chat surface to the
    # same-origin HTTP API so a persisted WebSocket preference cannot bypass it.
    merged["NEXT_PUBLIC_FORCE_HTTP_CHAT_TRANSPORT"] = "true"
    merged["NEXT_PUBLIC_WEB_SOCKET_DEFAULT_ON"] = "false"
    merged["NEXT_PUBLIC_SIDEBAR_CHAT_WEB_SOCKET_DEFAULT_ON"] = "false"
    defaults = {
        # NOSONAR S5332: this hop stays on the single-host private Docker bridge;
        # bearer authentication remains mandatory and no public route is created.
        "VSS_AGENT_GATEWAY_URL": f"http://host.docker.internal:{port}",  # NOSONAR
        "VSS_AGENT_BACKEND_PROTOCOL": "responses",
        "VSS_AGENT_BACKEND_MODEL": "agent",
        "VSS_AGENT_BACKEND_SESSION_FIELD": "user",
        "NEXT_PUBLIC_ENABLE_CHAT_TAB": "true",
    }
    for key, value in defaults.items():
        if not merged.get(key, "").strip():
            merged[key] = value

    profiles = [item.strip() for item in merged.get("COMPOSE_PROFILES", "").split(",") if item.strip()]
    for required_profile in ("vss-ui", "agent-gateway"):
        if required_profile not in profiles:
            profiles.append(required_profile)
    merged["COMPOSE_PROFILES"] = ",".join(profiles)


def infer_runtime_mode(
    *,
    device_id: str,
    peer_device_id: str,
    is_remote: bool,
    peer_is_remote: bool,
    reserved_device_ids: str,
    fixed_shared_device_ids: str,
) -> str | None:
    if is_remote:
        return MODE_REMOTE
    if not device_id:
        return None
    shared_ids = {
        entry.strip()
        for csv in (reserved_device_ids, fixed_shared_device_ids)
        for entry in csv.split(",")
        if entry.strip()
    }
    if device_id in shared_ids:
        return MODE_LOCAL_SHARED
    if not peer_is_remote and device_id == peer_device_id:
        return MODE_LOCAL_SHARED
    return MODE_LOCAL


def build_resolved_env(config: DryRunRecipe) -> dict[str, str]:
    #   (lowest -> highest precedence)
    #   0. containers.env first-party image coordinates (same position as
    #      `docker compose --env-file containers.env` before the profile env files)
    #   1. profile .env defaults
    #   1b. profile overrides.env (mandatory, next to .env)
    #   2. HARDWARE_PROFILE from notebook (sets the key for the yml lookup)
    #   3. yml hw-defaults from hardware_profiles[HW]:
    #        a. str-valued keys at the HW root (always apply)
    #        b. dict at key "<profile>" (applies when profile matches)
    #        c. dict at key "<profile>.<profile_mode>" (applies when both match)
    #      ... then yml edge_device_ids (for edge HW)
    #   4. notebook's other named recipe params (vlm_name, rtvi_vllm_gpu_memory_utilization, etc.)
    #   5. per-call env_overrides
    merged = load_shell_env_file(config.containers_env_file)
    merged.update(parse_env_file(config.source_env_file))
    merged.update(parse_env_file(config.profile_env_override_file))
    if config.hardware_profile:
        merged["HARDWARE_PROFILE"] = config.hardware_profile
    effective_hardware_profile = (
        config.env_overrides.get("HARDWARE_PROFILE", "").strip() or merged.get("HARDWARE_PROFILE", "").strip()
    )
    hw_block = config.hardware_profile_env_overrides.get(effective_hardware_profile, {})
    # (3a) HW-level: str-valued keys at the HW root.
    for key, value in hw_block.items():
        if isinstance(value, str):
            merged[key] = value
    # (3b) profile-level: dict at HW[<profile>].
    profile_block = hw_block.get(config.profile)
    if isinstance(profile_block, MappingABC):
        merged.update({k: v for k, v in profile_block.items() if isinstance(v, str)})
    # (3c) profile+mode-level: dict at HW["<profile>.<profile_mode>"].
    if config.profile_mode:
        scoped_key = f"{config.profile}.{config.profile_mode}"
        scoped_block = hw_block.get(scoped_key)
        if isinstance(scoped_block, MappingABC):
            merged.update({k: v for k, v in scoped_block.items() if isinstance(v, str)})
    if effective_hardware_profile in config.edge_hardware_profiles:
        merged["LLM_DEVICE_ID"] = config.edge_device_ids["llm"]
        merged["VLM_DEVICE_ID"] = config.edge_device_ids["vlm"]
        merged["RT_VLM_DEVICE_ID"] = config.edge_device_ids["rt_vlm"]
        merged["RT_CV_DEVICE_ID"] = config.edge_device_ids["rt_cv"]
    if config.ngc_cli_api_key:
        merged["NGC_CLI_API_KEY"] = config.ngc_cli_api_key
    if config.nvidia_api_key:
        merged["NVIDIA_API_KEY"] = config.nvidia_api_key
    if config.llm_endpoint_url:
        merged["LLM_BASE_URL"] = config.llm_endpoint_url
        merged["LLM_MODE"] = "remote"
    if config.llm_model_type:
        merged["LLM_MODEL_TYPE"] = config.llm_model_type
    if config.llm_name:
        merged["LLM_NAME"] = config.llm_name
    if config.openai_api_key:
        merged["OPENAI_API_KEY"] = config.openai_api_key
    if config.vlm_name:
        merged["VLM_NAME"] = config.vlm_name
    # Mirror dev-profile.sh `--use-remote-vlm`: VLM_ENDPOINT_URL → VLM_BASE_URL + VLM_MODE=remote.
    if config.vlm_endpoint_url:
        merged["VLM_BASE_URL"] = config.vlm_endpoint_url
        merged["VLM_MODE"] = "remote"
    if config.vlm_model_type:
        merged["VLM_MODEL_TYPE"] = config.vlm_model_type
    if config.llm_enable_thinking:
        merged["LLM_ENABLE_THINKING"] = config.llm_enable_thinking
    if config.nim_kvcache_percent:
        # Outer/profile-level knob; hw-*.env files interpolate this into NIM_KVCACHE_PERCENT.
        merged["VLM_NIM_KVCACHE_PERCENT"] = config.nim_kvcache_percent
    if config.rtvi_vllm_gpu_memory_utilization:
        merged["RTVI_VLLM_GPU_MEMORY_UTILIZATION"] = config.rtvi_vllm_gpu_memory_utilization
    merged.update(config.env_overrides)

    llm_is_remote = bool(config.llm_endpoint_url) or merged.get("LLM_MODE") == MODE_REMOTE
    vlm_is_remote = bool(config.vlm_endpoint_url) or merged.get("VLM_MODE") == MODE_REMOTE
    reserved = merged.get("RESERVED_DEVICE_IDS", "")
    fixed_shared = merged.get("FIXED_SHARED_DEVICE_IDS", "")
    llm_dev = merged.get("LLM_DEVICE_ID", "").strip()
    vlm_dev = merged.get("VLM_DEVICE_ID", "").strip()

    inferred_llm_mode = infer_runtime_mode(
        device_id=llm_dev,
        peer_device_id=vlm_dev,
        is_remote=llm_is_remote,
        peer_is_remote=vlm_is_remote,
        reserved_device_ids=reserved,
        fixed_shared_device_ids=fixed_shared,
    )
    if inferred_llm_mode is not None:
        merged["LLM_MODE"] = inferred_llm_mode

    inferred_vlm_mode = infer_runtime_mode(
        device_id=vlm_dev,
        peer_device_id=llm_dev,
        is_remote=vlm_is_remote,
        peer_is_remote=llm_is_remote,
        reserved_device_ids=reserved,
        fixed_shared_device_ids=fixed_shared,
    )
    if inferred_vlm_mode is not None:
        merged["VLM_MODE"] = inferred_vlm_mode

    if (
        "RT_VLM_DEVICE_ID" not in config.env_overrides
        and effective_hardware_profile not in config.edge_hardware_profiles
    ):
        if merged.get("VLM_MODE") == MODE_LOCAL_SHARED:
            shared_dev = merged.get("SHARED_LLM_VLM_DEVICE_ID", "").strip()
            merged["RT_VLM_DEVICE_ID"] = shared_dev or vlm_dev
        elif merged.get("VLM_MODE") == MODE_REMOTE:
            merged["RT_VLM_DEVICE_ID"] = "0"
        elif vlm_dev:
            merged["RT_VLM_DEVICE_ID"] = vlm_dev

    host_ip = (
        first_non_placeholder([config.env_overrides.get("HOST_IP", ""), merged.get("HOST_IP", "")])
        or detect_internal_ip()
    )
    if not host_ip:
        raise ValidationError("Could not determine HOST_IP. Set --env HOST_IP=<ip>.")
    external_ip = (
        first_non_placeholder(
            [
                config.env_overrides.get("EXTERNAL_IP", ""),
                config.env_overrides.get("EXTERNALLY_ACCESSIBLE_IP", ""),
                merged.get("EXTERNAL_IP", ""),
                merged.get("EXTERNALLY_ACCESSIBLE_IP", ""),
                config.external_ip or "",
            ]
        )
        or detect_external_ip()
        or host_ip
    )

    merged["VSS_APPS_DIR"] = first_non_placeholder([merged.get("VSS_APPS_DIR", ""), str(config.deployments_dir)])
    merged["VSS_DATA_DIR"] = first_non_placeholder(
        [
            config.env_overrides.get("VSS_DATA_DIR", ""),
            str(config.mdx_data_dir),
        ]
    )
    merged["HOST_IP"] = host_ip
    merged["EXTERNALLY_ACCESSIBLE_IP"] = external_ip
    if external_ip != host_ip:
        merged["EXTERNAL_IP"] = external_ip

    disable_brev_proxy_env = first_non_placeholder(
        [
            config.env_overrides.get("VSS_DISABLE_BREV_PROXY_ENV", ""),
            os.environ.get("VSS_DISABLE_BREV_PROXY_ENV", ""),
        ]
    ).lower() in {"1", "true", "yes"}
    brev_env_id = ""
    if not disable_brev_proxy_env:
        brev_env_id = first_non_placeholder(
            [
                config.env_overrides.get("BREV_ENV_ID", ""),
                os.environ.get("BREV_ENV_ID", ""),
                brev_environment_id(),
            ]
        )
    if brev_env_id:
        apply_brev_proxy_env(
            merged,
            brev_env_id,
            explicit_link_domain=config.env_overrides.get("BREV_LINK_DOMAIN", ""),
        )

    if merged.get("HARDWARE_PROFILE", "") not in config.supported_hardware_profiles:
        raise ValidationError(f"Invalid HARDWARE_PROFILE '{merged.get('HARDWARE_PROFILE', '')}'.")
    if (
        merged.get("HARDWARE_PROFILE", "") in config.edge_hardware_profiles
        and config.profile not in config.edge_allowed_profiles
    ):
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

    if merged["VLM_MODE"] == MODE_REMOTE:
        merged["VLM_NAME_SLUG"] = MODEL_SLUG_NONE
        if not merged.get("VLM_BASE_URL", "").strip():
            raise ValidationError("VLM_BASE_URL is required when VLM_MODE=remote.")

    if (
        merged.get("HARDWARE_PROFILE", "") in config.edge_hardware_profiles
        and config.profile in {PROFILE_BASE, PROFILE_ALERTS}
        and not merged.get("VLM_BASE_URL", "").strip()
    ):
        vlm_port = merged.get("VLM_PORT", "").strip() or str(THOR_VLM_PORT)
        merged["VLM_BASE_URL"] = f"{INTERNAL_URL_SCHEME}://{host_ip}:{vlm_port}"

    if config.profile == PROFILE_ALERTS:
        if merged.get("HARDWARE_PROFILE", "") in config.edge_hardware_profiles:
            merged["PERCEPTION_DOCKERFILE_PREFIX"] = EDGE_PERCEPTION_DOCKERFILE_PREFIX
            merged["RTVI_VLM_INPUT_WIDTH"] = EDGE_ALERTS_RTVI_INPUT_WIDTH
            merged["RTVI_VLM_INPUT_HEIGHT"] = EDGE_ALERTS_RTVI_INPUT_HEIGHT
            merged["RTVI_VLM_DEFAULT_NUM_FRAMES_PER_SECOND_OR_FIXED_FRAMES_CHUNK"] = EDGE_ALERTS_RTVI_FPS
            if merged["VLM_MODE"] != MODE_REMOTE:
                merged["VLM_AS_VERIFIER_CONFIG_FILE_PREFIX"] = EDGE_ALERTS_VLM_AS_VERIFIER_CONFIG_FILE_PREFIX

        if merged["MODE"] == MODE_2D_VLM and merged["VLM_MODE"] != MODE_REMOTE:
            vlm_port = merged.get("VLM_PORT", "").strip() or str(DEFAULT_ALERTS_VLM_PORT)
            merged["RTVI_VLM_ENDPOINT"] = f"{INTERNAL_URL_SCHEME}://{host_ip}:{vlm_port}/v1"

    if (
        config.profile == PROFILE_ALERTS
        and merged.get("MODE") == MODE_2D_VLM
        and "COMPOSE_PROFILES" not in config.env_overrides
        and merged.get("COMPOSE_PROFILES_VLM", "").strip()
    ):
        merged["COMPOSE_PROFILES"] = "${COMPOSE_PROFILES_VLM}"

    if not all(merged.get(key, "") for key in COMPOSE_PROFILE_REQUIRED_KEYS):
        raise ValidationError("Could not compute COMPOSE_PROFILES due to missing required env keys.")
    merged["COMPOSE_PROFILES"] = resolve_compose_profiles(merged, config.profile)
    apply_agent_gateway_env(merged)
    # Resolve nested ${VAR} references (e.g. SDR_CONTROLLER_CONFIG_PATH, VST_CONFIG_PATH,
    # REACT_APP_API_ENDPOINT_BASE_URL) so the generated .env holds self-contained values;
    # docker compose does not interpolate env-file values against each other.
    expand_env_value_references(merged)
    return merged


def render_generated_env(source_env_file: Path, resolved: dict[str, str]) -> str:
    lines = source_env_file.read_text().splitlines()
    for key, value in sorted(resolved.items()):
        _set_env_line(lines, key, value)
    return "\n".join(lines) + "\n"


def _compose_subprocess_env(extra_defaults: Mapping[str, str] = MappingProxyType({})) -> dict[str, str]:
    env = os.environ.copy()
    for key in _COMPOSE_SHELL_ENV_BLOCKLIST:
        env.pop(key, None)
    for key, value in extra_defaults.items():
        env.setdefault(key, value)
    return env


def _compose_env_file_args(config: DryRunRecipe, generated_env_file: Path) -> list[str]:
    """Return the env-file layering used by ``dev-profile.sh``."""

    return [
        "--env-file",
        str(config.containers_env_file),
        "--env-file",
        str(config.source_env_file),
        "--env-file",
        str(generated_env_file),
    ]


def _compose_subprocess_env_for_config(
    config: DryRunRecipe,
    extra_defaults: Mapping[str, str] = MappingProxyType({}),
) -> dict[str, str]:
    """Source containers.env into the Compose process environment like dev-profile.sh."""

    compose_env = _compose_subprocess_env(extra_defaults)
    compose_env.update(load_shell_env_file(config.containers_env_file, compose_env))
    return compose_env


def resolve_compose(config: DryRunRecipe, *, agent_gateway_enabled: bool = False) -> str:
    env_file_args = _compose_env_file_args(config, config.output_env_file)
    compose_env = _compose_subprocess_env_for_config(config)
    try:
        result = subprocess.run(
            ["docker", "compose", "-f", str(config.compose_file), *env_file_args, "config"],
            cwd=str(config.deployments_dir),
            capture_output=True,
            text=True,
            env=compose_env,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("docker command not found. Install Docker with Compose v2.") from exc
    if result.returncode != 0:
        raise RuntimeError(f"docker compose config failed.\nstdout:\n{result.stdout}\n\nstderr:\n{result.stderr}")
    return sanitize_resolved_compose(
        result.stdout,
        agent_gateway_ui_source_root=(config.deployments_dir.parent.parent if agent_gateway_enabled else None),
    )


def run_compose_command(config: DryRunRecipe, env_file: Path, compose_file: Path, *args: str) -> None:
    # Prefer plain, non-ANSI output so status logs are visible/persistent in non-interactive captures.
    env_file_args = _compose_env_file_args(config, env_file)
    compose_env = _compose_subprocess_env_for_config(
        config,
        {"COMPOSE_PROGRESS": "plain", "COMPOSE_ANSI": "never"},
    )
    try:
        result = subprocess.run(
            ["docker", "compose", "-f", str(compose_file), *env_file_args, *args],
            cwd=str(config.deployments_dir),
            env=compose_env,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("docker command not found. Install Docker with Compose v2.") from exc
    if result.returncode != 0:
        raise RuntimeError(
            "docker compose command failed.\n"
            f"command: docker compose -f {compose_file} {' '.join(env_file_args)} {' '.join(args)}\n"
            f"exit_code: {result.returncode}"
        )


def sanitize_resolved_compose(
    compose_text: str,
    *,
    agent_gateway_ui_source_root: Path | None = None,
) -> str:
    """Normalize a resolved graph and add source builds required by gateway mode."""

    parsed = yaml.safe_load(compose_text)
    if not isinstance(parsed, dict):
        return compose_text

    services = parsed.get("services")
    if not isinstance(services, dict):
        return compose_text

    # Gateway-mode UI routes are part of this source checkout. Until a
    # compatible released UI image is pinned, build the selected UI service
    # from the same checkout as the locally built gateway.
    if agent_gateway_ui_source_root is not None and "agent-gateway" in services:
        ui_service = services.get("vss-ui")
        if isinstance(ui_service, dict) and "build" not in ui_service:
            ui_service["build"] = {
                "context": str(agent_gateway_ui_source_root),
                "dockerfile": "services/ui/Dockerfile",
                "args": {"BUILD_TYPE": "prod"},
            }

    defined_services = set(services.keys())
    for service_def in services.values():
        if not isinstance(service_def, dict):
            continue
        depends_on = service_def.get("depends_on")
        if depends_on is None:
            continue

        if isinstance(depends_on, list):
            filtered_list = [dep for dep in depends_on if dep in defined_services]
            if filtered_list:
                service_def["depends_on"] = filtered_list
            else:
                service_def.pop("depends_on", None)
        elif isinstance(depends_on, dict):
            filtered_map = {dep: cfg for dep, cfg in depends_on.items() if dep in defined_services}
            if filtered_map:
                service_def["depends_on"] = filtered_map
            else:
                service_def.pop("depends_on", None)

    return yaml.safe_dump(parsed, sort_keys=False)


def write_private_text(path: Path, content: str) -> None:
    """Write a credential-bearing deployment artifact with owner-only access."""

    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            stream.write(content)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def generate_dry_run_artifacts(config: DryRunRecipe) -> tuple[dict[str, str], Path, Path]:
    resolved_env = build_resolved_env(config)
    env_file = config.output_env_file
    compose_file = config.output_compose_file
    env_file.parent.mkdir(parents=True, exist_ok=True)
    write_private_text(  # NOSONAR S2083: --output-env-file is an intentional CLI destination
        env_file, render_generated_env(config.source_env_file, resolved_env)
    )
    compose_file.parent.mkdir(parents=True, exist_ok=True)
    write_private_text(  # NOSONAR S2083: --output-compose-file is an intentional CLI destination
        compose_file,
        resolve_compose(
            config,
            agent_gateway_enabled=resolved_env.get("VSS_AGENT_GATEWAY_ENABLED") == "true",
        ),
    )
    return resolved_env, env_file, compose_file


def print_configuration_summary(config: DryRunRecipe, resolved_env: dict[str, str]) -> None:
    print("Configuration valid.")
    print(f"  Profile:  {config.profile}")
    print(f"  Hardware: {resolved_env.get('HARDWARE_PROFILE', '(unset)')}")
    print(f"  Source:   {config.deployments_dir}")
    print(f"  Host IP:  {resolved_env.get('HOST_IP', '(unset)')}")
    print(f"  External: {resolved_env.get('EXTERNALLY_ACCESSIBLE_IP', '(unset)')}")
