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

"""VSS Orchestrator MCP function group.

Exposes nine tools that wrap the orchestrator utilities:
  - docker_profiles: list all supported deployment profiles
  - docker_prereqs: run Docker/GPU prerequisite checks
  - docker_generate : resolve env + compose YAML artifacts
  - docker_read: fetch generated env/yaml by docker_compose_id
  - docker_list: list docker container names
  - docker_logs: fetch docker logs by container name
  - docker_up: fire-and-return docker compose up
  - docker_status: poll docker_up status and logs
  - docker_down: docker compose down using generated artifacts
"""

import asyncio
from collections import OrderedDict
from collections import deque
from collections.abc import AsyncGenerator
from contextlib import suppress
from dataclasses import dataclass
from dataclasses import field
from enum import StrEnum
import os
from pathlib import Path
import signal
import subprocess
import threading
import time
from typing import Callable
from typing import Final
from typing import Generic
from typing import Literal
from typing import TypeVar
from uuid import uuid4

from nat.builder.builder import Builder
from nat.builder.function import FunctionGroup
from nat.cli.register_workflow import register_function_group
from nat.data_models.function import FunctionGroupBaseConfig
from pydantic import BaseModel
from pydantic import Field
from pydantic import field_validator

from .docker_compose_util import SUPPORTED_PROFILES
from .docker_compose_util import ValidationError
from .docker_compose_util import create_dry_run_recipe
from .docker_compose_util import generate_dry_run_artifacts
from .docker_compose_util import parse_env_file
from .docker_compose_util import parse_env_overrides
from .prereqs_check import run_prereqs_checks
from .storage import ArtifactKind
from .storage import ModelArtifact
from .storage import ensure_data_directories
from .storage import ensure_model_artifacts

_COMPOSE_OPS_LOCK = threading.Lock()
_COMPOSE_SPECS_LOCK = threading.Lock()
_MAX_OPERATION_LOG_LINES = 4000
_MAX_RETAINED_COMPOSE_OPERATIONS = 200
_MAX_RETAINED_COMPOSE_SPECS = 500


@dataclass
class ComposeOperation:
    docker_compose_ops_id: str
    docker_compose_id: str
    action: str
    pid: int
    status: str
    running: bool
    exit_code: int | None
    command: str
    env_file: str
    compose_file: str
    started_at_epoch_s: int
    log_lines: deque[str] = field(default_factory=lambda: deque(maxlen=_MAX_OPERATION_LOG_LINES))


RegistryKeyT = TypeVar("RegistryKeyT")
RegistryValueT = TypeVar("RegistryValueT")


class LruRegistry(Generic[RegistryKeyT, RegistryValueT]):
    """Ordered key-value store with bounded least-recently-used eviction."""

    def __init__(
        self,
        *,
        max_entries: int,
        can_evict: Callable[[RegistryKeyT, RegistryValueT], bool] | None = None,
    ) -> None:
        self._entries: OrderedDict[RegistryKeyT, RegistryValueT] = OrderedDict()
        self._max_entries = max_entries
        self._can_evict = can_evict or (lambda _key, _value: True)

    def get(self, key: RegistryKeyT, *, touch: bool = True) -> RegistryValueT | None:
        value = self._entries.get(key)
        if value is None:
            return None
        if touch:
            self.touch(key)
        return value

    def peek(self, key: RegistryKeyT) -> RegistryValueT | None:
        return self._entries.get(key)

    def set(self, key: RegistryKeyT, value: RegistryValueT) -> None:
        self._entries[key] = value
        self.touch(key)
        self.evict()

    def touch(self, key: RegistryKeyT) -> None:
        if key not in self._entries:
            return
        self._entries.move_to_end(key)

    def evict(self) -> None:
        while len(self._entries) > self._max_entries:
            evict_key = next(
                (
                    candidate_key
                    for candidate_key, candidate_value in self._entries.items()
                    if self._can_evict(candidate_key, candidate_value)
                ),
                None,
            )
            if evict_key is None:
                break
            self._entries.pop(evict_key, None)

    def values(self):
        return self._entries.values()


_COMPOSE_OPERATIONS = LruRegistry[str, ComposeOperation](max_entries=_MAX_RETAINED_COMPOSE_OPERATIONS)
_COMPOSE_SPECS = LruRegistry[str, dict[str, object]](
    max_entries=_MAX_RETAINED_COMPOSE_SPECS,
    can_evict=lambda docker_compose_id, _spec: all(
        not op.running or op.docker_compose_id != docker_compose_id for op in _COMPOSE_OPERATIONS.values()
    ),
)


class ComposeAction(StrEnum):
    UP = "up"
    DOWN = "down"


class ComposeStatus(StrEnum):
    ERROR = "error"
    IGNORED = "ignored"
    STARTED = "started"
    STARTING = "starting"
    RUNNING = "running"
    SUCCESS = "success"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class IntFieldBounds:
    minimum: int
    default: int
    maximum: int


_SUPPORTED_COMPOSE_ACTIONS: Final[frozenset[str]] = frozenset(action.value for action in ComposeAction)
_ALL_KNOWN_STATUSES: Final[frozenset[str]] = frozenset(status.value for status in ComposeStatus)

# Input bounds and defaults
_COMPOSE_STATUS_TAIL_BOUNDS: Final[IntFieldBounds] = IntFieldBounds(minimum=1, default=80, maximum=1000)
_CONTAINER_LOG_TAIL_BOUNDS: Final[IntFieldBounds] = IntFieldBounds(minimum=1, default=100, maximum=10000)
_MAX_DOCKER_LOG_RESPONSE_BYTES: Final[int] = 1024 * 1024


def _reject_option_like_docker_positional(arg_name: str, value: str) -> str:
    """Reject Docker positional arguments that could be parsed as flags."""
    if value.startswith("-"):
        raise ValueError(f"{arg_name} must not begin with '-'.")
    return value


def _truncate_text_to_max_bytes(text: str, *, max_bytes: int) -> str:
    """UTF-8-safe truncation with a fixed-size summary marker."""

    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text

    suffix = f"\n... [truncated, original size {len(encoded)} bytes]"
    suffix_bytes = suffix.encode("utf-8")
    prefix_budget = max(0, max_bytes - len(suffix_bytes))
    if prefix_budget == 0:
        return suffix_bytes[:max_bytes].decode("utf-8", errors="ignore")

    prefix = encoded[:prefix_budget].decode("utf-8", errors="ignore")
    return prefix + suffix


# Polling behavior
_COMPOSE_STATUS_RECOMMENDED_POLL_INTERVAL_S: Final[int] = 5


class GenerateInput(BaseModel):
    """Input for the docker_generate tool."""

    profile: str = Field(
        ...,
        description="Deployment profile. Supported values: 'base', 'search', 'lvs', 'alerts'.",
    )
    env_overrides: list[str] = Field(
        default=[],
        description=(
            "Environment variable overrides as KEY=VALUE strings. "
            "Example: ['HARDWARE_PROFILE=H100', 'LLM_MODE=local', 'HOST_IP=192.168.1.10']. "
            "Keys must be uppercase with only letters, digits, and underscores."
        ),
    )
    ngc_cli_api_key: str | None = Field(
        default=None,
        description="Optional NGC CLI API key to inject when NGC_CLI_API_KEY is absent from the profile .env.",
    )
    nvidia_api_key: str | None = Field(
        default=None,
        description="Optional NVIDIA API key to inject when NVIDIA_API_KEY is absent from the profile .env.",
    )


class ComposeStatusInput(BaseModel):
    """Input for docker_status polling."""

    docker_compose_ops_id: str = Field(
        ...,
        description="Compose operation ID returned by docker_up/docker_down.",
    )
    tail_lines: int = Field(
        default=_COMPOSE_STATUS_TAIL_BOUNDS.default,
        ge=_COMPOSE_STATUS_TAIL_BOUNDS.minimum,
        le=_COMPOSE_STATUS_TAIL_BOUNDS.maximum,
        description="Number of lines to return from the end of the docker_up log.",
    )


class ComposeArtifactsInput(BaseModel):
    """Input for docker_read lookups."""

    docker_compose_id: str = Field(
        ...,
        description="Docker compose ID returned by docker_generate.",
    )


class ComposeContainersInput(BaseModel):
    """Input for docker_list lookup."""

    all_containers: bool = Field(
        default=True,
        description="Include stopped containers when true.",
    )


class ContainerLogsInput(BaseModel):
    """Input for docker_logs lookups."""

    container_name: str = Field(
        ...,
        description="Docker container name.",
    )
    tail: int = Field(
        default=_CONTAINER_LOG_TAIL_BOUNDS.default,
        ge=_CONTAINER_LOG_TAIL_BOUNDS.minimum,
        le=_CONTAINER_LOG_TAIL_BOUNDS.maximum,
        description="Number of trailing log lines to return.",
    )

    @field_validator("container_name")
    @classmethod
    def _validate_container_name(cls, value: str) -> str:
        return _reject_option_like_docker_positional("container_name", value)


class DockerProfilesInput(BaseModel):
    """Input for docker_profiles lookup."""

    pass


class DockerPrereqsInput(BaseModel):
    """Input for docker_prereqs lookup."""

    pass


class ModelArtifactConfig(BaseModel):
    """Config shape for profile model artifacts in MCP YAML."""

    package_ref: str
    downloaded_relative_path: str
    output_name: str
    artifact_kind: Literal["file", "dir"]


class HardwareResolutionConfig(BaseModel):
    """Hardware resolution rules for profile validation/device mapping."""

    supported_profiles: tuple[str, ...]
    edge_profiles: tuple[str, ...]
    edge_allowed_profiles: tuple[str, ...]
    edge_device_ids: dict[str, str]
    thor_base_profiles: tuple[str, ...]


class LlmResolutionConfig(BaseModel):
    """LLM name-to-slug resolution rules."""

    supported_models: dict[str, str]


class VlmResolutionConfig(BaseModel):
    """VLM name-to-slug resolution rules and Thor base overrides."""

    supported_models: dict[str, str]
    thor_base_overrides: dict[str, str]


class ModelResolutionConfig(BaseModel):
    """Model/hardware resolution rules supplied via MCP YAML."""

    hardware: HardwareResolutionConfig
    llm: LlmResolutionConfig
    vlm: VlmResolutionConfig


class OrchestratorToolConfig(FunctionGroupBaseConfig, name="vss_orchestrator"):
    """Configuration for the VSS Orchestrator function group."""

    deployments_dir: str = Field(
        description=(
            "Absolute path to the deployments/ root directory "
            "(e.g. /home/user/video-search-and-summarization/deployments)."
        )
    )
    source_compose_yaml: str = Field(
        ...,
        min_length=1,
        description=("Absolute path to the source docker compose YAML file."),
    )
    source_env: str = Field(
        ...,
        min_length=1,
        description=("Absolute path to the source profile .env file. Supports '{profile}' placeholder."),
    )
    mdx_data_dir: str = Field(
        min_length=1,
        description=(
            "Absolute path for MDX_DATA_DIR resolved from MCP YAML config. "
            "Profile .env MDX_DATA_DIR values are ignored."
        ),
    )
    output_dir: str = Field(
        description=(
            "Directory where docker_generate writes generated artifacts "
            "(generated.<docker_compose_id>.dry-run.env and "
            "compose.resolved.<docker_compose_id>.dry-run.yml)."
        )
    )
    mdx_data_directories: tuple[str, ...] = Field(
        ...,
        description="Relative subdirectories created under MDX_DATA_DIR for all profiles by docker_generate.",
    )
    model_artifacts: dict[str, tuple[ModelArtifactConfig, ...]] = Field(
        ...,
        description="Profile-keyed model artifact definitions used by pre-compose download checks.",
    )
    model_resolution: ModelResolutionConfig = Field(
        ...,
        description="Hardware/model resolution rules used during docker_generate validation.",
    )
    include: list[str] = Field(
        default=[
            "docker_profiles",
            "docker_prereqs",
            "docker_generate",
            "docker_read",
            "docker_list",
            "docker_logs",
            "docker_up",
            "docker_status",
            "docker_down",
        ],
        description="Subset of tools to expose. All tools are included by default.",
    )

    @field_validator("model_artifacts")
    @classmethod
    def _validate_model_artifact_profiles(
        cls,
        value: dict[str, tuple[ModelArtifactConfig, ...]],
    ) -> dict[str, tuple[ModelArtifactConfig, ...]]:
        unknown_profiles = set(value) - SUPPORTED_PROFILES
        if unknown_profiles:
            raise ValueError(
                "model_artifacts contains unsupported profile key(s): "
                f"{sorted(unknown_profiles)}. Supported profiles: {sorted(SUPPORTED_PROFILES)}."
            )
        return value


class ComposeOperationInput(BaseModel):
    """Input for docker_up/docker_down operations."""

    docker_compose_id: str = Field(
        ...,
        description=(
            "Identifier for compose artifacts and operation tracking. "
            "For current deployments, this matches profile names such as 'base', 'search', 'lvs', or 'alerts'."
        ),
    )


@register_function_group(config_type=OrchestratorToolConfig)
async def vss_orchestrator(
    _config: OrchestratorToolConfig,
    _builder: Builder,
) -> AsyncGenerator[FunctionGroup]:
    """VSS Orchestrator function group for managing docker compose deployments."""

    deployments_dir = Path(_config.deployments_dir).resolve()

    # ---------------------------------------------------------------------------
    # Shared helpers
    # ---------------------------------------------------------------------------

    configured_output_dir = Path(_config.output_dir).expanduser().resolve()
    mdx_data_dir = Path(_config.mdx_data_dir).expanduser().resolve()
    configured_mdx_data_directories = tuple(_config.mdx_data_directories)
    configured_model_artifacts_by_profile: dict[str, tuple[ModelArtifact, ...]] = {
        profile: tuple(
            ModelArtifact(
                package_ref=artifact.package_ref,
                downloaded_relative_path=artifact.downloaded_relative_path,
                output_name=artifact.output_name,
                artifact_kind=ArtifactKind(artifact.artifact_kind),
            )
            for artifact in artifacts
        )
        for profile, artifacts in _config.model_artifacts.items()
    }
    configured_model_resolution = _config.model_resolution

    # Bootstrap required data directories as soon as config is loaded, so MCP
    # server startup fails fast if any directory cannot be created.
    try:
        ensure_data_directories(
            str(mdx_data_dir),
            required_subdirectories=configured_mdx_data_directories,
        )
    except RuntimeError as exc:
        raise RuntimeError(f"Startup directory bootstrap failed for mdx_data_dir '{mdx_data_dir}': {exc}") from exc

    print(f"[vss_orchestrator] startup directory bootstrap succeeded for mdx_data_dir: {mdx_data_dir}", flush=True)

    def _resolve_output_paths(docker_compose_id: str) -> tuple[Path, Path]:
        """Return (env_path, compose_path) under the configured output directory."""
        env_path = configured_output_dir / f"generated.{docker_compose_id}.dry-run.env"
        compose_path = configured_output_dir / f"compose.resolved.{docker_compose_id}.dry-run.yml"
        return env_path, compose_path

    def _artifacts_exist(env_path: Path, compose_path: Path) -> str | None:
        """Return an error message if required artifacts are missing, else None."""
        if not env_path.is_file():
            return f"Generated env file not found: {env_path}. Run the 'docker_generate' tool first."
        if not compose_path.is_file():
            return f"Resolved compose file not found: {compose_path}. Run the 'docker_generate' tool first."
        return None

    def _compose_env() -> dict[str, str]:
        compose_env = os.environ.copy()
        compose_env.setdefault("COMPOSE_PROGRESS", "plain")
        compose_env.setdefault("COMPOSE_ANSI", "never")
        return compose_env

    def _terminate_running_op(op: ComposeOperation) -> None:
        pid = op.pid
        if pid < 0:
            return
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except OSError:
            return

        # Give compose a brief chance to shutdown cleanly before forcing kill.
        deadline = time.time() + 5.0
        while time.time() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            except OSError:
                break
            time.sleep(0.1)
        else:
            with suppress(OSError):
                os.kill(pid, signal.SIGKILL)

    def _start_compose_op(docker_compose_id: str, action: str, action_args: list[str]) -> dict:
        if action not in _SUPPORTED_COMPOSE_ACTIONS:
            return {
                "status": ComposeStatus.ERROR.value,
                "error": f"Unsupported action '{action}'. Supported actions: {sorted(_SUPPORTED_COMPOSE_ACTIONS)}.",
            }

        def _running_op_error(existing: ComposeOperation) -> dict:
            return {
                "status": ComposeStatus.ERROR.value,
                "error": (
                    f"Compose operation already running for docker_compose_id '{docker_compose_id}' "
                    f"(action={existing.action}, pid={existing.pid})."
                ),
            }

        with _COMPOSE_SPECS_LOCK:
            spec = _COMPOSE_SPECS.get(docker_compose_id)
        if spec is None:
            return {
                "status": ComposeStatus.ERROR.value,
                "error": (
                    f"Unknown docker_compose_id '{docker_compose_id}'. "
                    "Run docker_generate first to create and register it."
                ),
            }

        env_path = Path(str(spec["env_file"]))
        compose_path = Path(str(spec["compose_file"]))
        missing = _artifacts_exist(env_path, compose_path)
        if missing:
            return {"status": ComposeStatus.ERROR.value, "error": missing}

        pre_compose_check: dict[str, str] | None = None
        if action == ComposeAction.UP.value:
            profile = str(spec.get("profile") or "").strip()
            profile_artifacts = configured_model_artifacts_by_profile.get(profile)
            if profile_artifacts:
                resolved_env = parse_env_file(env_path)
                mdx_data_dir = resolved_env.get("MDX_DATA_DIR", "").strip()
                if not mdx_data_dir:
                    return {
                        "status": ComposeStatus.ERROR.value,
                        "error": f"MDX_DATA_DIR is missing in generated env file: {env_path}",
                    }
                ngc_cli_api_key = resolved_env.get("NGC_CLI_API_KEY", "").strip()
                pre_compose_check = {
                    "type": "ensure_model_artifacts",
                    "profile": profile,
                    "mdx_data_dir": mdx_data_dir,
                    "ngc_cli_api_key": ngc_cli_api_key,
                }

        docker_compose_ops_id = f"{action}-{docker_compose_id}-{uuid4().hex[:8]}"
        running_ops: list[ComposeOperation] = []
        running_up_ops: list[ComposeOperation] = []
        running_down_ops: list[ComposeOperation] = []
        ops_to_terminate: list[ComposeOperation] = []

        with _COMPOSE_OPS_LOCK:
            for existing in _COMPOSE_OPERATIONS.values():
                if existing.docker_compose_id == docker_compose_id and existing.running:
                    running_ops.append(existing)

            if running_ops:
                running_up_ops = [op for op in running_ops if op.action == ComposeAction.UP.value]
                running_down_ops = [op for op in running_ops if op.action == ComposeAction.DOWN.value]

                if action == ComposeAction.DOWN.value:
                    if running_down_ops:
                        chosen = running_down_ops[0]
                        return _running_op_error(chosen)
                    # down preempts all active up ops for this deployment
                    if running_up_ops:
                        for existing in running_up_ops:
                            existing.running = False
                            existing.status = ComposeStatus.CANCELLED.value
                        _COMPOSE_SPECS.evict()
                    ops_to_terminate = list(running_up_ops)
                elif action == ComposeAction.UP.value:
                    if running_down_ops:
                        chosen = running_down_ops[0]
                        return {
                            "status": ComposeStatus.IGNORED.value,
                            "message": (
                                f"Ignoring incoming compose {action} for docker_compose_id '{docker_compose_id}' "
                                f"because compose {chosen.action} is already running."
                            ),
                            "docker_compose_id": docker_compose_id,
                        }
                    if running_up_ops:
                        chosen = running_up_ops[0]
                        return _running_op_error(chosen)

            # Reserve a running slot atomically before process spawn.
            _COMPOSE_OPERATIONS.set(
                docker_compose_ops_id,
                ComposeOperation(
                    docker_compose_ops_id=docker_compose_ops_id,
                    docker_compose_id=docker_compose_id,
                    action=action,
                    pid=-1,
                    status=ComposeStatus.STARTING.value,
                    running=True,
                    exit_code=None,
                    command=f"docker compose {action} {' '.join(action_args)}".strip(),
                    env_file=str(env_path),
                    compose_file=str(compose_path),
                    started_at_epoch_s=int(time.time()),
                ),
            )

        for existing in ops_to_terminate:
            _terminate_running_op(existing)

        def _watch_compose_op() -> None:
            def _append_op_log(line: str) -> None:
                print(f"[compose_{action}:{docker_compose_ops_id}] {line}", flush=True)
                with _COMPOSE_OPS_LOCK:
                    op = _COMPOSE_OPERATIONS.peek(docker_compose_ops_id)
                    if op is not None:
                        op.log_lines.append(line)

            def _is_cancelled_or_not_running() -> bool:
                with _COMPOSE_OPS_LOCK:
                    op = _COMPOSE_OPERATIONS.peek(docker_compose_ops_id)
                    if op is None:
                        return True
                    return op.status == ComposeStatus.CANCELLED.value or not op.running

            if pre_compose_check is not None:
                check_type = pre_compose_check.get("type")
                check_profile = pre_compose_check.get("profile", "unknown")
                _append_op_log(f"Running pre-compose check '{check_type}' for profile '{check_profile}'...")
                try:
                    if check_type == "ensure_model_artifacts":
                        profile_artifacts = configured_model_artifacts_by_profile.get(check_profile)
                        if profile_artifacts is None:
                            raise RuntimeError(
                                f"No model_artifacts configured for profile '{check_profile}'. "
                                "Add model_artifacts.<profile> in MCP config."
                            )
                        ensure_model_artifacts(
                            pre_compose_check["mdx_data_dir"],
                            pre_compose_check["ngc_cli_api_key"],
                            artifacts=profile_artifacts,
                        )
                    else:
                        raise RuntimeError(f"Unsupported pre-compose check: {check_type}")
                except RuntimeError as exc:
                    _append_op_log(f"Pre-compose check failed: {exc}")
                    with _COMPOSE_OPS_LOCK, _COMPOSE_SPECS_LOCK:
                        op = _COMPOSE_OPERATIONS.peek(docker_compose_ops_id)
                        if op is not None:
                            op.exit_code = 1
                            op.running = False
                            if op.status != ComposeStatus.CANCELLED.value:
                                op.status = ComposeStatus.ERROR.value
                        _COMPOSE_OPERATIONS.evict()
                        _COMPOSE_SPECS.evict()
                    _append_op_log("Compose operation failed (pre-compose check).")
                    return
                _append_op_log("Pre-compose check succeeded.")

            if _is_cancelled_or_not_running():
                return

            try:
                process = subprocess.Popen(
                    [
                        "docker",
                        "compose",
                        "-f",
                        str(compose_path),
                        "--env-file",
                        str(env_path),
                        action,
                        *action_args,
                    ],
                    cwd=str(deployments_dir),
                    env=_compose_env(),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
            except FileNotFoundError:
                _append_op_log("docker command not found. Install Docker with Compose v2.")
                with _COMPOSE_OPS_LOCK, _COMPOSE_SPECS_LOCK:
                    op = _COMPOSE_OPERATIONS.peek(docker_compose_ops_id)
                    if op is not None:
                        op.exit_code = 127
                        op.running = False
                        if op.status != ComposeStatus.CANCELLED.value:
                            op.status = ComposeStatus.ERROR.value
                    _COMPOSE_OPERATIONS.evict()
                    _COMPOSE_SPECS.evict()
                _append_op_log("Compose operation failed (docker command not found).")
                return

            with _COMPOSE_OPS_LOCK:
                op = _COMPOSE_OPERATIONS.peek(docker_compose_ops_id)
                if op is not None:
                    op.pid = process.pid
                    op.status = ComposeStatus.RUNNING.value

            if process.stdout is None:
                return
            try:
                for line in process.stdout:
                    line = line.rstrip("\n")
                    _append_op_log(line)
            finally:
                exit_code = process.wait()
                resolved_status = ComposeStatus.CANCELLED.value
                with _COMPOSE_OPS_LOCK, _COMPOSE_SPECS_LOCK:
                    op = _COMPOSE_OPERATIONS.peek(docker_compose_ops_id)
                    if op is not None:
                        op.exit_code = exit_code
                        op.running = False
                        if op.status != ComposeStatus.CANCELLED.value:
                            op.status = ComposeStatus.SUCCESS.value if exit_code == 0 else ComposeStatus.ERROR.value
                        resolved_status = op.status
                    _COMPOSE_OPERATIONS.evict()
                    _COMPOSE_SPECS.evict()

                status_log_message = (
                    "Compose operation succeeded."
                    if resolved_status == ComposeStatus.SUCCESS.value
                    else (
                        f"Compose operation failed with exit code {exit_code}."
                        if resolved_status == ComposeStatus.ERROR.value
                        else f"Compose operation finished with status '{resolved_status}'."
                    )
                )
                _append_op_log(status_log_message)

        watcher = threading.Thread(target=_watch_compose_op, daemon=True)
        watcher.start()

        return {
            "status": ComposeStatus.STARTED.value,
            "docker_compose_ops_id": docker_compose_ops_id,
            "docker_compose_id": docker_compose_id,
            "action": action,
            "command": f"docker compose {action} {' '.join(action_args)}".strip(),
            "poll_tool": "docker_status",
            "status_hint": "Poll docker_status with docker_compose_ops_id for progress/completion.",
            "recommended_poll_interval_s": _COMPOSE_STATUS_RECOMMENDED_POLL_INTERVAL_S,
            "pid": -1,
        }

    # ---------------------------------------------------------------------------
    # Tool: docker_profiles
    # ---------------------------------------------------------------------------
    group = FunctionGroup(config=_config)

    if "docker_profiles" in _config.include:

        async def _docker_profiles(input: DockerProfilesInput) -> dict:
            """List all supported deployment profiles."""
            _ = input
            return {
                "status": ComposeStatus.SUCCESS.value,
                "profiles": sorted(SUPPORTED_PROFILES),
            }

        group.add_function(name="docker_profiles", fn=_docker_profiles, description=_docker_profiles.__doc__)

    # ---------------------------------------------------------------------------
    # Tool: docker_prereqs
    # ---------------------------------------------------------------------------

    if "docker_prereqs" in _config.include:

        async def _docker_prereqs(input: DockerPrereqsInput) -> dict:
            """Run Docker/GPU prerequisite checks."""
            _ = input
            try:
                report = await asyncio.to_thread(run_prereqs_checks)
            except RuntimeError as exc:
                return {"status": ComposeStatus.ERROR.value, "error": str(exc)}
            return {
                "status": ComposeStatus.SUCCESS.value,
                "message": "Prerequisite checks passed.",
                "details": report,
            }

        group.add_function(name="docker_prereqs", fn=_docker_prereqs, description=_docker_prereqs.__doc__)

    # ---------------------------------------------------------------------------
    # Tool: docker_generate
    # ---------------------------------------------------------------------------

    if "docker_generate" in _config.include:

        async def _docker_generate(input: GenerateInput) -> dict:
            """Generate resolved docker compose YAML and .env artifacts.

            Validates environment configuration, resolves all variables (HOST_IP,
            COMPOSE_PROFILES, LLM/VLM slugs, Brev proxy URLs, etc.) and writes:

            - A fully resolved .env file with all substitutions applied.
            - A resolved docker compose YAML with all variable references expanded
              and orphaned depends_on entries removed.

            These artifacts must exist before running docker_up or docker_down.

            Returns a summary dict with artifact paths and key resolved values on
            success, or {"status": "error", "error": "<message>"} on failure.
            """
            try:
                docker_compose_id = f"{input.profile}-{uuid4().hex[:8]}"
                env_path, compose_path = _resolve_output_paths(docker_compose_id)
                env_overrides = parse_env_overrides(input.env_overrides)
                dry_run_recipe = create_dry_run_recipe(
                    profile=input.profile,
                    env_overrides=env_overrides,
                    ngc_cli_api_key=input.ngc_cli_api_key,
                    nvidia_api_key=input.nvidia_api_key,
                    model_resolution=configured_model_resolution,
                    output_env_file=str(env_path),
                    output_compose_file=str(compose_path),
                    deployments_dir=str(deployments_dir),
                    mdx_data_dir=str(mdx_data_dir),
                    source_compose_yaml=_config.source_compose_yaml,
                    source_env=_config.source_env,
                )
                resolved_env, env_path, compose_path = generate_dry_run_artifacts(dry_run_recipe)
                ensure_data_directories(
                    resolved_env["MDX_DATA_DIR"],
                    required_subdirectories=configured_mdx_data_directories,
                )
                with _COMPOSE_OPS_LOCK, _COMPOSE_SPECS_LOCK:
                    _COMPOSE_SPECS.set(
                        docker_compose_id,
                        {
                            "docker_compose_id": docker_compose_id,
                            "profile": input.profile,
                            "env_file": str(env_path),
                            "compose_file": str(compose_path),
                        },
                    )
                result = {
                    "status": ComposeStatus.SUCCESS.value,
                    "docker_compose_id": docker_compose_id,
                    "hardware_profile": resolved_env.get("HARDWARE_PROFILE", "(unset)"),
                    "host_ip": resolved_env.get("HOST_IP", "(unset)"),
                    "external_ip": resolved_env.get("EXTERNALLY_ACCESSIBLE_IP", "(unset)"),
                    "llm_mode": resolved_env.get("LLM_MODE", "(unset)"),
                    "llm_name": resolved_env.get("LLM_NAME", "(unset)"),
                    "vlm_mode": resolved_env.get("VLM_MODE", "(unset)"),
                    "vlm_name": resolved_env.get("VLM_NAME", "(unset)"),
                    "compose_profiles": resolved_env.get("COMPOSE_PROFILES", "(unset)"),
                    "message": "Artifacts generated. Use docker_compose_id with docker_up/docker_down.",
                }
                print(f"[docker_generate:{docker_compose_id}] compose yaml: {compose_path}", flush=True)
                print(f"[docker_generate:{docker_compose_id}] env: {env_path}", flush=True)
                print(f"[docker_generate:{docker_compose_id}] {result}", flush=True)
                return result
            except (ValidationError, RuntimeError) as exc:
                return {"status": ComposeStatus.ERROR.value, "error": str(exc)}

        group.add_function(name="docker_generate", fn=_docker_generate, description=_docker_generate.__doc__)

    # ---------------------------------------------------------------------------
    # Tool: docker_read
    # ---------------------------------------------------------------------------

    if "docker_read" in _config.include:

        async def _docker_read(input: ComposeArtifactsInput) -> dict:
            """Fetch generated env and resolved compose yaml content by docker_compose_id."""
            with _COMPOSE_SPECS_LOCK:
                spec = _COMPOSE_SPECS.get(input.docker_compose_id)
            if spec is None:
                return {
                    "status": ComposeStatus.ERROR.value,
                    "error": (
                        f"Unknown docker_compose_id '{input.docker_compose_id}'. "
                        "Run docker_generate first to create and register it."
                    ),
                }

            env_path = Path(str(spec["env_file"]))
            compose_path = Path(str(spec["compose_file"]))
            missing = _artifacts_exist(env_path, compose_path)
            if missing:
                return {"status": ComposeStatus.ERROR.value, "error": missing}

            return {
                "status": ComposeStatus.SUCCESS.value,
                "docker_compose_id": input.docker_compose_id,
                "profile": spec.get("profile"),
                "env_content": env_path.read_text(encoding="utf-8", errors="replace"),
                "compose_yaml_content": compose_path.read_text(encoding="utf-8", errors="replace"),
            }

        group.add_function(name="docker_read", fn=_docker_read, description=_docker_read.__doc__)

    # ---------------------------------------------------------------------------
    # Tool: docker_list
    # ---------------------------------------------------------------------------

    if "docker_list" in _config.include:

        async def _docker_list(input: ComposeContainersInput) -> dict:
            """List docker container names."""
            args = ["docker", "ps", "--format", "{{.Names}}"]
            if input.all_containers:
                args.insert(2, "--all")

            result = await asyncio.to_thread(
                subprocess.run,
                args,
                cwd=str(deployments_dir),
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                return {
                    "status": ComposeStatus.ERROR.value,
                    "error": result.stderr.strip() or "Failed to list Docker containers.",
                }

            raw = result.stdout.strip()
            container_names = [line.strip() for line in raw.splitlines() if line.strip()] if raw else []

            return {
                "status": ComposeStatus.SUCCESS.value,
                "container_names": container_names,
            }

        group.add_function(name="docker_list", fn=_docker_list, description=_docker_list.__doc__)

    # ---------------------------------------------------------------------------
    # Tool: docker_logs
    # ---------------------------------------------------------------------------

    if "docker_logs" in _config.include:

        async def _docker_logs(input: ContainerLogsInput) -> dict:
            """Fetch docker logs by container name."""
            result = await asyncio.to_thread(
                subprocess.run,
                ["docker", "logs", "--tail", str(input.tail), "--", input.container_name],
                cwd=str(deployments_dir),
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                return {
                    "status": ComposeStatus.ERROR.value,
                    "container_name": input.container_name,
                    "tail": input.tail,
                    "error": result.stderr.strip() or "Failed to fetch container logs.",
                }
            logs = _truncate_text_to_max_bytes(result.stdout, max_bytes=_MAX_DOCKER_LOG_RESPONSE_BYTES)
            return {
                "status": ComposeStatus.SUCCESS.value,
                "container_name": input.container_name,
                "tail": input.tail,
                "logs": logs,
                "logs_truncated": logs != result.stdout,
                "log_bytes": len(logs.encode("utf-8")),
            }

        group.add_function(name="docker_logs", fn=_docker_logs, description=_docker_logs.__doc__)

    # ---------------------------------------------------------------------------
    # Tool: docker_up
    # ---------------------------------------------------------------------------

    if "docker_up" in _config.include:

        async def _docker_up(input: ComposeOperationInput) -> dict:
            """Start docker compose services using previously generated artifacts.

            Runs in background: docker compose up -d --force-recreate --build

            Requires that artifacts for the docker_compose_id already exist.

            Returns immediately for polling via docker_status.
            """
            try:
                return _start_compose_op(
                    docker_compose_id=input.docker_compose_id,
                    action="up",
                    action_args=["-d", "--force-recreate", "--build", "--quiet-pull"],
                )
            except FileNotFoundError:
                return {
                    "status": ComposeStatus.ERROR.value,
                    "error": "docker command not found. Install Docker with Compose v2.",
                }

        group.add_function(name="docker_up", fn=_docker_up, description=_docker_up.__doc__)

    # ---------------------------------------------------------------------------
    # Tool: docker_status
    # ---------------------------------------------------------------------------

    if "docker_status" in _config.include:

        async def _docker_status(input: ComposeStatusInput) -> dict:
            """Poll status and recent logs for a background docker_up operation."""
            with _COMPOSE_OPS_LOCK:
                op = _COMPOSE_OPERATIONS.get(input.docker_compose_ops_id)
                if op is None:
                    return {
                        "status": ComposeStatus.ERROR.value,
                        "error": f"Unknown docker_compose_ops_id '{input.docker_compose_ops_id}'.",
                    }
                recent_lines = list(op.log_lines)[-input.tail_lines :]
                status_value = op.status
                if status_value not in _ALL_KNOWN_STATUSES:
                    status_value = ComposeStatus.ERROR.value
                return {
                    "status": status_value,
                    "docker_compose_ops_id": input.docker_compose_ops_id,
                    "docker_compose_id": op.docker_compose_id,
                    "action": op.action,
                    "pid": op.pid,
                    "running": op.running,
                    "exit_code": op.exit_code,
                    "command": op.command,
                    "tail_lines": input.tail_lines,
                    "log_excerpt": "\n".join(recent_lines),
                }

        group.add_function(name="docker_status", fn=_docker_status, description=_docker_status.__doc__)

    # ---------------------------------------------------------------------------
    # Tool: docker_down
    # ---------------------------------------------------------------------------

    if "docker_down" in _config.include:

        async def _docker_down(input: ComposeOperationInput) -> dict:
            """Stop and remove docker compose services.

            Runs in background: docker compose down -v --remove-orphans

            Requires that artifacts for the docker_compose_id already exist.

            Returns immediately for polling via docker_status.
            """
            try:
                return _start_compose_op(
                    docker_compose_id=input.docker_compose_id,
                    action="down",
                    action_args=["-v", "--remove-orphans"],
                )
            except FileNotFoundError:
                return {
                    "status": ComposeStatus.ERROR.value,
                    "error": "docker command not found. Install Docker with Compose v2.",
                }

        group.add_function(name="docker_down", fn=_docker_down, description=_docker_down.__doc__)

    yield group
