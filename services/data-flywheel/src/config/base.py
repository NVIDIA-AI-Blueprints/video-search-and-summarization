# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
"""Shared (backend-agnostic) config contract + the ``Settings`` machinery.

Only cross-cutting pieces live here: the ``SystemConfig`` / ``JobConfig`` bases,
``MLflow`` / ``Logging`` / ``Backend`` config, the ``config_type -> schema``
registry, and ``Settings`` (+ ``from_yaml``, which dispatches to the per-backend
loader in ``src.config.nmp`` / ``src.config.deft``). Backend-specific *schemas*
live in those sibling modules; the package ``__init__`` re-exports everything so
``from src.config import X`` keeps working.
"""

import os
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

import yaml
from pydantic import BaseModel, Field, PrivateAttr, SerializeAsAny
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.config_renderer import render_environment_values

__all__ = [
    "JOB_CONFIG_TYPES",
    "BackendConfig",
    "JobConfig",
    "LoggingConfig",
    "MLflowConfig",
    "Settings",
    "SystemConfig",
    "build_job_config",
    "build_shared_system_kwargs",
    "config_type_for",
    "settings",
]


class LoggingConfig(BaseModel):
    """Configuration for logging"""

    level: str = "INFO"


class MLflowConfig(BaseModel):
    """Configuration for MLflow integration"""

    enabled: bool = Field(
        # MLflow is opt-in for NMP (the `mlflow` compose profile) but REQUIRED
        # for the DEFT/AOI loop (candidate registration, alias trail, baseline
        # resolution, HITL approval detection all run through the registry) —
        # the `deft` profile therefore implies it. config.deft.yaml also pins
        # `enabled: true` explicitly so a deft build never silently no-ops.
        default_factory=lambda: bool(
            {"mlflow", "deft"} & set(os.getenv("COMPOSE_PROFILES", "").split(","))
        ),
        description="Enable MLflow integration (COMPOSE_PROFILES has mlflow or deft)",
    )
    tracking_uri: str = Field(default="http://0.0.0.0:5000", description="MLflow tracking URI")
    experiment_name_prefix: str = Field(
        default="data-flywheel", description="Prefix for experiment names"
    )
    artifact_location: str = Field(default="./mlruns", description="Location for MLflow artifacts")


class SystemConfig(BaseSettings):
    """Base (shared) system configuration — backend-agnostic fields only.

    Backend-specific system config lives on the backend-derived subclass:
    ``NmpSystemConfig`` (``nmp_config`` + ``llm_judge_config``) and
    ``DeftSystemConfig`` (``deft_config``). ``Settings.system_config`` holds
    whichever concrete ``from_yaml`` loads for the active ``backend.kind``, so
    ``settings.system_config.<backend field>`` stays valid on that build.
    """

    mlflow_config: MLflowConfig = Field(default_factory=MLflowConfig)
    logging_config: LoggingConfig = Field(default_factory=LoggingConfig)


class JobConfig(BaseSettings):
    """Base placeholder for job-level configuration.

    ``Settings.job_configs`` is a ``dict[str, JobConfig]`` keyed by ``job_type``;
    each backend fills it with its own concretes (NMP: ``LLMJobConfig`` /
    ``EmbeddingJobConfig``; DEFT: ``DeftJobConfig``, registered under BOTH
    ``aoi_job`` and ``its_job``). New job types are new dict entries —
    ``Settings`` never grows a field.
    """

    # Which REGISTRY KEY built this object — ``aoi_job`` and ``its_job`` are the
    # SAME class, so ``config_type_for`` cannot answer by isinstance alone.
    # ``PrivateAttr`` keeps it out of ``model_dump()`` (Mongo's ``config_data``
    # stays byte-identical) and out of ``model_fields`` (``build_job_config``'s
    # unknown-field guard would reject it at the ``/configs`` edge).
    _config_type: str | None = PrivateAttr(default=None)

    @classmethod
    def from_dict(cls, data: dict) -> "JobConfig":
        """Build from a plain dict. Every registered concrete overrides this."""
        raise NotImplementedError(f"{cls.__name__} does not define from_dict")

    @classmethod
    def build(cls, data: dict, config_type: str) -> "JobConfig":
        """Build from raw ``data``, recording which registry key built it.

        Every construction door goes through here — ``build_job_config`` and both
        backend loaders. A door that bypasses it does not fail loudly; it
        silently mislabels ITS as AOI.
        """
        cfg = cls.from_dict(data)
        cfg._config_type = config_type
        return cfg

    def __eq__(self, other: object) -> bool:
        """Value equality — the ``_config_type`` stamp is NOT part of it.

        pydantic v2 compares ``__pydantic_private__`` alongside the field store,
        so without this a stamped config would compare UNEQUAL to a
        field-identical one that lost its stamp on a ``model_dump()`` ->
        re-construct round trip (Celery hands TaskResult between stages as a
        dict).
        """
        if isinstance(other, BaseModel) and type(self) is type(other):
            return (
                self.__dict__ == other.__dict__
                and self.__pydantic_extra__ == other.__pydantic_extra__
            )
        return NotImplemented


class BackendConfig(BaseModel):
    """Selects the execution backend implementation.

    ``kind`` chooses the backend provider (currently ``nmp``; ``deft`` is reserved
    for a future implementation). The ``BACKEND`` environment variable overrides
    this at runtime — see ``src/backends/factory.py``.
    """

    kind: str = "nmp"


def build_shared_system_kwargs(config_data: dict) -> dict:
    """Shared (backend-agnostic) ``SystemConfig`` kwargs from raw config data.

    Used by each backend's config loader so the shared blocks (MLflow / logging /
    model-manager) are parsed one way, and the backend adds its own fields.
    """
    return {
        "mlflow_config": MLflowConfig(**config_data.get("mlflow_config", {})),
        "logging_config": LoggingConfig(**config_data.get("logging_config", {})),
    }


class Settings(BaseSettings):
    """Combined settings: shared system config + backend-loaded job configs.

    ``system_config`` holds the backend-derived ``SystemConfig`` concrete (so
    ``settings.system_config.<backend field>`` is valid on that build).
    ``job_configs`` is keyed by ``job_type`` and filled by the active backend
    (e.g. ``settings.job_configs["llm_job"]`` / ``["aoi_job"]``). ``from_yaml``
    dispatches config building to the backend selected by ``backend.kind``.
    """

    # These fields are always built explicitly in ``from_yaml`` — never populated
    # from bare environment variables. A prefix keeps pydantic-settings from
    # auto-mapping the ``BACKEND`` env var onto the complex ``backend`` field
    # (which would crash trying to JSON-parse "deft"); ``BACKEND`` is instead the
    # intentional backend-selection override read in ``from_yaml`` + the factory.
    model_config = SettingsConfigDict(env_prefix="DFW_SETTINGS_")

    # SerializeAsAny so a subclass held in a base-typed field (NmpSystemConfig /
    # LLMJobConfig / …) serializes with its OWN schema — otherwise model_dump()
    # would drop the subclass fields (base schema has none).
    system_config: SerializeAsAny[SystemConfig]
    job_configs: dict[str, SerializeAsAny[JobConfig]] = Field(default_factory=dict)
    backend: BackendConfig = BackendConfig()

    def get_api_key(self, env_var: str) -> str | None:
        """Get API key from environment variable."""
        return os.getenv(env_var)

    @classmethod
    def from_yaml(cls, config_path: str | Path) -> "Settings":
        """Load settings from YAML, building backend config by ``backend.kind``."""
        config_data = cls._load_yaml_data(config_path)
        backend_config = BackendConfig(**config_data.get("backend", {}))
        kind = (os.environ.get("BACKEND") or backend_config.kind).strip().lower()
        if kind == "deft":
            rendered = render_environment_values(config_data)
            if not isinstance(rendered, dict):  # Mapping shape is preserved recursively.
                raise TypeError(f"Rendered config must remain a mapping: {config_path}")
            config_data = rendered
        # Reflect the effective (env-overridable) kind so settings.backend.kind
        # agrees with the loader that ran + the provider the factory selects.
        backend_config = backend_config.model_copy(update={"kind": kind})

        # Lazily import only the selected backend's loader from its config
        # submodule (never the package facade, which is half-initialized here).
        if kind == "nmp":
            from src.config.nmp import load_nmp_settings

            system_config, job_configs = load_nmp_settings(config_data)
        elif kind == "deft":
            from src.config.deft import load_deft_settings

            system_config, job_configs = load_deft_settings(config_data)
        else:
            raise ValueError(f"Unknown backend kind: {kind!r} (expected 'nmp' or 'deft')")

        return cls(
            system_config=system_config,
            job_configs=job_configs,
            backend=backend_config,
        )

    @classmethod
    def _load_yaml_data(cls, config_path: str | Path) -> dict:
        """Load and validate raw YAML configuration data."""
        config_path = Path(config_path)
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        with open(config_path, encoding="utf-8") as f:
            loaded = yaml.safe_load(f)
        if loaded is None:
            return {}
        if not isinstance(loaded, dict):
            raise ValueError(f"Top-level config must be a mapping: {config_path}")
        return loaded


# --- job-config registry (the contract layer owns config_type -> schema) -----
#
# Imported HERE, not at the top: ``src.config.nmp`` and ``src.config.deft`` both
# import from this file, so a top-level import is a cycle. It cannot live in the
# facade either — that is still half-initialized when ``settings`` is built at
# the bottom of this file.
from src.config.deft import DEFT_JOB_CONFIG_TYPES  # noqa: E402
from src.config.nmp import EmbeddingJobConfig, LLMJobConfig  # noqa: E402

# Frozen so it can't be mutated at runtime or in tests. Backend *gating* — which
# config types a deployment accepts — stays at the API edge via
# ``assert_supported_type``, so an nmp build still rejects an ``aoi_job`` /
# ``its_job`` config at ``/configs`` and ``/jobs``.
JOB_CONFIG_TYPES: Mapping[str, type[JobConfig]] = MappingProxyType(
    {
        "llm_job": LLMJobConfig,
        "embedding_job": EmbeddingJobConfig,
        # ``aoi_job`` and ``its_job`` map to the SAME class — see config_type_for.
        **DEFT_JOB_CONFIG_TYPES,
    }
)


def build_job_config(config_type: str, data: dict) -> JobConfig:
    """Build the typed ``JobConfig`` for ``config_type`` from a raw dict.

    Rejects unknown config types and unknown top-level fields. The schemas are
    ``extra="forbid"``, but the NMP ``from_dict`` builders cherry-pick keys, so
    the top-level guard is enforced here uniformly.
    """
    model = JOB_CONFIG_TYPES.get(config_type)
    if model is None:
        raise ValueError(f"Unsupported configuration type: {config_type}")
    unknown = set(data) - set(model.model_fields)
    if unknown:
        raise ValueError(
            f"Invalid configuration data for {config_type}: unexpected field(s) {sorted(unknown)}"
        )
    return model.build(data, config_type)


def config_type_for(job_config: JobConfig) -> str:
    """Return the ``config_type`` string for a built ``JobConfig`` object.

    INVARIANT: ``config_type_for(build_job_config(t, data)) == t`` for EVERY
    registered ``t``, held up by the stamp ``JobConfig.build`` applies —
    ``isinstance`` cannot, because ``aoi_job`` and ``its_job`` are one class.

    The isinstance scan survives only as the fallback for objects built outside a
    registry door (``DeftJobConfig(...)`` in a test, or one round-tripped through
    ``model_dump()``). It is order-dependent and deliberately answers ``aoi_job``
    for an unstamped ``DeftJobConfig``, as every pre-ITS caller already got.
    """
    stamped = getattr(job_config, "_config_type", None)
    if stamped is not None:
        if stamped not in JOB_CONFIG_TYPES:
            # Only the builders stamp, and only with registry keys — so this is a
            # stamp from a registry that has since changed shape. Loud beats a
            # silent downgrade to the isinstance guess.
            raise TypeError(f"Job config carries an unregistered config_type {stamped!r}")
        return stamped
    for config_type, model in JOB_CONFIG_TYPES.items():
        if isinstance(job_config, model):
            return config_type
    raise TypeError(f"Cannot classify job config {type(job_config).__name__}")


def _default_config_path() -> Path:
    """Resolve the config file for the active backend.

    Order: ``DFW_CONFIG_PATH`` (explicit override, for deploys/tests) → per-kind
    file ``config/config.<kind>.yaml`` when ``BACKEND`` selects a non-nmp backend
    → ``config/config.yaml`` (the NMP default). This module lives at
    ``repo_root/src/config/base.py``, hence ``parents[2]`` for the repo root.
    """
    if override := os.environ.get("DFW_CONFIG_PATH"):
        return Path(override)
    config_dir = Path(__file__).resolve().parents[2] / "config"
    kind = (os.environ.get("BACKEND") or "").strip().lower()
    if kind and kind != "nmp":
        per_kind = config_dir / f"config.{kind}.yaml"
        if per_kind.exists():
            return per_kind
    return config_dir / "config.yaml"


settings = Settings.from_yaml(_default_config_path())
