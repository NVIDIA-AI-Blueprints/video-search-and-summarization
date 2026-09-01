# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Deployment configuration: one origin in, every endpoint out.

A deployment is described once and reused, instead of being re-stated on every
invocation. ``vss configure --base-url <origin>`` probes the ingress, records
what it found in ``~/.vss/config.json``, and every later command reads it.

This replaces two things at once (SDD NFR-6):

* **Per-call endpoint flags.** ``--es-endpoint``, ``--cosmos-embed-endpoint``,
  the six index names and the rest describe a *deployment*, not a request.
  They remain as overrides for development, but they are no longer how a
  normal invocation finds its backends.
* **Deployment discovery.** ``--deployment/--profile/--namespace/--release/
  --kube-context`` inspected compose files and kubectl to work out where
  things were. NFR-6 removes that: the deployment declares its own routes
  behind one origin, and the CLI asks.

Config is *client-side* state, which NFR-3 ("stateless: no daemon") does not
forbid -- that constrains server/job state. Nothing here is authoritative;
the deployment is. The file is a cache of an answer the origin gave.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
import json
import os
from pathlib import Path
import re
from typing import Any

#: Where the resolved deployment lives. Override for tests or for a second
#: deployment via ``VSS_CONFIG_HOME``.
CONFIG_HOME_ENV = "VSS_CONFIG_HOME"

#: Bumped when the on-disk shape changes incompatibly. A file written by a
#: newer CLI is refused rather than half-read.
CONFIG_VERSION = 1

#: Services a deployment may expose behind one origin.
#:
#: Keyed by *service*, not by route or by model -- the three are distinct and
#: conflating them is what made an earlier revision call RT-Embed
#: "cosmos_embed". RT-Embed is the service, ``/cosmos-embed`` is where the
#: ingress mounts it, and ``cosmos-embed1-448p-anomaly-detection`` is one
#: model it happens to serve today. Only the first is stable.
#:
#: ``probe`` is not decoration: requesting a mount root cannot tell "route
#: absent" from "route present, root has no handler" -- an unrouted
#: ``/elasticsearch`` and a routed ``/api`` both answer 404. ``describe`` is
#: the endpoint that reports what the service actually holds, so the config
#: records the backend's own answer rather than a value someone typed.
INGRESS_SERVICES: dict[str, ServiceRoute] = {}  # populated below the dataclasses


class ConfigError(Exception):
    """Configuration is missing, unreadable, or from an incompatible version."""


_ELASTICSEARCH_INDEX_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_ENVIRONMENT_VARIABLE_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

DEFAULT_INTROSPECTION_CRITERIA_PROMPT = """Memory is sufficient only when:
1. The supplied records directly answer every material part of the user's question.
2. The evidence applies to the requested sensor and time range.
3. Each factual conclusion is supported by one or more supplied record IDs.
4. No critical fact required to answer the question is missing.
If any required condition fails, mark the evidence insufficient and produce grounded gaps for the missing information."""


def validate_memory_index(value: str) -> str:
    """Validate one Elasticsearch index name without contacting the backend."""
    index = value.strip()
    if (
        not index
        or len(index.encode("utf-8")) > 255
        or index in {".", ".."}
        or not _ELASTICSEARCH_INDEX_PATTERN.fullmatch(index)
    ):
        raise ConfigError(
            f"invalid memory index {value!r}; use 1-255 lowercase letters, digits, '.', '_' or '-', "
            "starting with a letter or digit"
        )
    return index


def config_home() -> Path:
    """Directory holding ``config.json``. Honours ``VSS_CONFIG_HOME``."""
    override = os.environ.get(CONFIG_HOME_ENV, "").strip()
    return Path(override) if override else Path.home() / ".vss"


def config_path() -> Path:
    return config_home() / "config.json"


@dataclass(frozen=True)
class ServiceRoute:
    """Where a service is mounted, and how to ask it about itself."""

    mount: str
    probe: str
    #: Endpoint reporting the service's own contents. None when the service
    #: exposes no introspection (RT-CV has no such API today).
    describe: str | None = None
    #: Which descriptive key its answer populates: "models" or "indices".
    describes: str = ""


@dataclass(frozen=True)
class Service:
    """One backend, described by what it told us about itself."""

    url: str
    #: Model ids the service reports serving (RT-Embed, RT-VLM).
    models: list[str] = field(default_factory=list)
    #: Index names the service holds (Elasticsearch).
    indices: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"url": self.url}
        if self.models:
            out["models"] = self.models
        if self.indices:
            out["indices"] = self.indices
        return out


@dataclass(frozen=True)
class MarkdownMemoryConfig:
    """Static OpenClaw cache capability and default note policy."""

    enabled: bool = False
    harness: str = "openclaw"
    workspace: str | None = None
    write_by_default: bool = False

    def validate(self) -> MarkdownMemoryConfig:
        if self.harness != "openclaw":
            raise ConfigError(f"unsupported Markdown memory harness {self.harness!r}; configure `--harness openclaw`")
        if self.workspace is not None:
            workspace = Path(self.workspace)
            if not workspace.is_absolute() or ".." in workspace.parts:
                raise ConfigError("Markdown memory workspace must be an absolute path without '..'")
        if self.enabled and not self.workspace:
            raise ConfigError("enabled Markdown memory requires `--workspace /absolute/openclaw/workspace`")
        if self.write_by_default and not self.enabled:
            raise ConfigError(
                "Markdown notes cannot be written by default while Markdown memory is disabled; "
                "use `--markdown --workspace /absolute/path` or `--no-write-notes-by-default`"
            )
        return self

    def to_json(self) -> dict[str, Any]:
        self.validate()
        return {
            "enabled": self.enabled,
            "harness": self.harness,
            "workspace": self.workspace,
            "write_by_default": self.write_by_default,
        }

    @classmethod
    def from_json(cls, raw: object) -> MarkdownMemoryConfig:
        if not isinstance(raw, dict):
            raise ConfigError("config 'memory.markdown' must be a JSON object")
        expected = {"enabled", "harness", "workspace", "write_by_default"}
        unknown = sorted(set(raw) - expected)
        if unknown:
            raise ConfigError(f"config 'memory.markdown' contains unknown fields: {', '.join(unknown)}")
        enabled = raw.get("enabled")
        harness = raw.get("harness")
        workspace = raw.get("workspace")
        write_by_default = raw.get("write_by_default")
        if not isinstance(enabled, bool):
            raise ConfigError("config 'memory.markdown.enabled' must be true or false")
        if not isinstance(harness, str):
            raise ConfigError("config 'memory.markdown.harness' must be a string")
        if workspace is not None and not isinstance(workspace, str):
            raise ConfigError("config 'memory.markdown.workspace' must be a string or null")
        if not isinstance(write_by_default, bool):
            raise ConfigError("config 'memory.markdown.write_by_default' must be true or false")
        return cls(
            enabled=enabled,
            harness=harness,
            workspace=workspace,
            write_by_default=write_by_default,
        ).validate()


@dataclass(frozen=True)
class IntrospectionJudgeConfig:
    """OpenAI-compatible text judge and answer-synthesis endpoint."""

    endpoint: str
    model: str = "openclaw/default"
    backend_model: str | None = None
    api_key_env: str | None = None
    criteria_prompt: str = DEFAULT_INTROSPECTION_CRITERIA_PROMPT

    def validate(self) -> IntrospectionJudgeConfig:
        if not self.endpoint.strip():
            raise ConfigError("introspection judge endpoint must be non-empty")
        if not self.model.strip():
            raise ConfigError("introspection judge model must be non-empty")
        if self.backend_model is not None and not self.backend_model.strip():
            raise ConfigError("introspection judge backend model must be non-empty when configured")
        if self.api_key_env is not None and not _ENVIRONMENT_VARIABLE_PATTERN.fullmatch(self.api_key_env):
            raise ConfigError(
                "introspection judge API-key environment variable must start with a letter or '_' "
                "and contain only letters, digits, or '_'"
            )
        if not self.criteria_prompt.strip():
            raise ConfigError("introspection judge criteria must be non-empty")
        return self

    def to_json(self) -> dict[str, Any]:
        self.validate()
        return {
            "endpoint": self.endpoint,
            "model": self.model,
            "backend_model": self.backend_model,
            "api_key_env": self.api_key_env,
            "criteria_prompt": self.criteria_prompt,
        }

    @classmethod
    def from_json(cls, raw: object) -> IntrospectionJudgeConfig:
        if not isinstance(raw, dict):
            raise ConfigError("config 'memory.introspection.judge' must be a JSON object")
        expected = {"endpoint", "model", "backend_model", "api_key_env", "criteria_prompt"}
        unknown = sorted(set(raw) - expected)
        if unknown:
            raise ConfigError(f"config 'memory.introspection.judge' contains unknown fields: {', '.join(unknown)}")
        endpoint = raw.get("endpoint")
        model = raw.get("model", "openclaw/default")
        backend_model = raw.get("backend_model")
        api_key_env = raw.get("api_key_env")
        criteria_prompt = raw.get("criteria_prompt", DEFAULT_INTROSPECTION_CRITERIA_PROMPT)
        if not isinstance(endpoint, str):
            raise ConfigError("config 'memory.introspection.judge.endpoint' must be a string")
        if not isinstance(model, str):
            raise ConfigError("config 'memory.introspection.judge.model' must be a string")
        if backend_model is not None and not isinstance(backend_model, str):
            raise ConfigError("config 'memory.introspection.judge.backend_model' must be a string or null")
        if api_key_env is not None and not isinstance(api_key_env, str):
            raise ConfigError("config 'memory.introspection.judge.api_key_env' must be a string or null")
        if not isinstance(criteria_prompt, str):
            raise ConfigError("config 'memory.introspection.judge.criteria_prompt' must be a string")
        return cls(
            endpoint=endpoint,
            model=model,
            backend_model=backend_model,
            api_key_env=api_key_env,
            criteria_prompt=criteria_prompt,
        ).validate()


@dataclass(frozen=True)
class IntrospectionMemoryConfig:
    """Static configuration for bounded memory introspection."""

    judge: IntrospectionJudgeConfig

    def validate(self) -> IntrospectionMemoryConfig:
        self.judge.validate()
        return self

    def to_json(self) -> dict[str, Any]:
        self.validate()
        return {"judge": self.judge.to_json()}

    @classmethod
    def from_json(cls, raw: object) -> IntrospectionMemoryConfig:
        if not isinstance(raw, dict):
            raise ConfigError("config 'memory.introspection' must be a JSON object")
        expected = {"judge"}
        unknown = sorted(set(raw) - expected)
        if unknown:
            raise ConfigError(f"config 'memory.introspection' contains unknown fields: {', '.join(unknown)}")
        if "judge" not in raw:
            raise ConfigError("config 'memory.introspection.judge' is required")
        return cls(judge=IntrospectionJudgeConfig.from_json(raw["judge"])).validate()


@dataclass(frozen=True)
class EmbeddingConfig:
    """Static configuration for derived memory embeddings."""

    enabled: bool = False
    provider: str = "openclaw_gateway"
    endpoint: str | None = "http://127.0.0.1:18789/v1"
    model: str | None = "openclaw/default"
    dimensions: int | None = None
    index: str = "vss-memory-embeddings-v1"
    timeout_seconds: float = 30.0
    batch_size: int = 16
    api_key_env: str | None = "OPENCLAW_GATEWAY_TOKEN"
    query_input_type: str | None = None
    document_input_type: str | None = None

    @classmethod
    def for_provider(cls, provider: str, *, enabled: bool = False) -> EmbeddingConfig:
        """Return clean profile defaults without retaining another provider's settings."""
        if provider == "openclaw_gateway":
            return cls(enabled=enabled)
        if provider == "openai_compatible":
            return cls(
                enabled=enabled,
                provider=provider,
                endpoint=None,
                model=None,
                api_key_env=None,
            )
        raise ConfigError(
            f"unsupported embedding provider {provider!r}; choose one of: openclaw_gateway, openai_compatible"
        )

    def validate(self) -> EmbeddingConfig:
        if self.provider not in {"openclaw_gateway", "openai_compatible"}:
            raise ConfigError(
                f"unsupported embedding provider {self.provider!r}; choose one of: openclaw_gateway, openai_compatible"
            )
        if self.dimensions is not None and (
            isinstance(self.dimensions, bool) or not isinstance(self.dimensions, int) or self.dimensions <= 0
        ):
            raise ConfigError("embedding dimensions must be a positive integer")
        if isinstance(self.batch_size, bool) or not isinstance(self.batch_size, int) or not 1 <= self.batch_size <= 128:
            raise ConfigError("embedding batch size must be between 1 and 128")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, int | float)
            or not 0 < self.timeout_seconds <= 300
        ):
            raise ConfigError("embedding timeout must be greater than zero and no greater than 300 seconds")
        if self.model is not None and (not isinstance(self.model, str) or not self.model.strip()):
            raise ConfigError("embedding model must be a non-empty string")
        validate_memory_index(self.index)
        if self.api_key_env is not None and (
            not self.api_key_env or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.api_key_env)
        ):
            raise ConfigError("embedding API-key environment variable must be a valid environment variable name")
        for name, value in (
            ("query", self.query_input_type),
            ("document", self.document_input_type),
        ):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ConfigError(f"embedding {name} input type must be a non-empty string or null")
        if self.endpoint is not None:
            parsed = urlsplit(self.endpoint)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ConfigError("embedding endpoint must be an absolute HTTP or HTTPS URL")
            if parsed.username is not None or parsed.password is not None:
                raise ConfigError(
                    "embedding endpoint must not contain embedded credentials; use `--embedding-api-key-env`"
                )
        if self.enabled and (not self.endpoint or not self.model):
            if self.provider == "openai_compatible":
                raise ConfigError(
                    "enabled openai_compatible embeddings require explicit "
                    "`--embedding-endpoint` and `--embedding-model`"
                )
            raise ConfigError("enabled embeddings require an endpoint and model target")
        return self

    def to_json(self) -> dict[str, Any]:
        self.validate()
        return {
            "enabled": self.enabled,
            "provider": self.provider,
            "endpoint": self.endpoint,
            "model": self.model,
            "dimensions": self.dimensions,
            "index": self.index,
            "timeout_seconds": self.timeout_seconds,
            "batch_size": self.batch_size,
            "api_key_env": self.api_key_env,
            "query_input_type": self.query_input_type,
            "document_input_type": self.document_input_type,
        }

    @classmethod
    def from_json(cls, raw: object) -> EmbeddingConfig:
        if not isinstance(raw, dict):
            raise ConfigError("config 'memory.embeddings' must be a JSON object")
        expected = {
            "api_key_env",
            "batch_size",
            "dimensions",
            "document_input_type",
            "enabled",
            "endpoint",
            "index",
            "model",
            "provider",
            "query_input_type",
            "timeout_seconds",
        }
        unknown = sorted(set(raw) - expected)
        if unknown:
            raise ConfigError(f"config 'memory.embeddings' contains unknown fields: {', '.join(unknown)}")
        raw_provider = raw.get("provider", cls().provider)
        if not isinstance(raw_provider, str):
            raise ConfigError("config 'memory.embeddings.provider' must be a string")
        defaults = cls.for_provider(raw_provider)
        values = {name: raw.get(name, getattr(defaults, name)) for name in expected}
        if not isinstance(values["enabled"], bool):
            raise ConfigError("config 'memory.embeddings.enabled' must be true or false")
        for name in ("provider", "index"):
            if not isinstance(values[name], str):
                raise ConfigError(f"config 'memory.embeddings.{name}' must be a string")
        for name in ("endpoint", "model", "api_key_env", "query_input_type", "document_input_type"):
            if values[name] is not None and not isinstance(values[name], str):
                raise ConfigError(f"config 'memory.embeddings.{name}' must be a string or null")
        if values["dimensions"] is not None and (
            isinstance(values["dimensions"], bool) or not isinstance(values["dimensions"], int)
        ):
            raise ConfigError("config 'memory.embeddings.dimensions' must be an integer or null")
        if isinstance(values["batch_size"], bool) or not isinstance(values["batch_size"], int):
            raise ConfigError("config 'memory.embeddings.batch_size' must be an integer")
        if isinstance(values["timeout_seconds"], bool) or not isinstance(values["timeout_seconds"], int | float):
            raise ConfigError("config 'memory.embeddings.timeout_seconds' must be a number")
        return cls(**values).validate()


@dataclass(frozen=True)
class RetrievalConfig:
    """Static preference for memory retrieval and hybrid ranking."""

    mode: str = "hybrid"
    candidate_count: int = 50
    rrf_rank_constant: int = 60

    def validate(self) -> RetrievalConfig:
        if self.mode not in {"keyword", "semantic", "hybrid"}:
            raise ConfigError("retrieval mode must be one of: keyword, semantic, hybrid")
        if (
            isinstance(self.candidate_count, bool)
            or not isinstance(self.candidate_count, int)
            or self.candidate_count <= 0
        ):
            raise ConfigError("semantic candidate count must be a positive integer")
        if (
            isinstance(self.rrf_rank_constant, bool)
            or not isinstance(self.rrf_rank_constant, int)
            or self.rrf_rank_constant <= 0
        ):
            raise ConfigError("RRF rank constant must be a positive integer")
        return self

    def to_json(self) -> dict[str, Any]:
        self.validate()
        return {
            "mode": self.mode,
            "candidate_count": self.candidate_count,
            "rrf_rank_constant": self.rrf_rank_constant,
        }

    @classmethod
    def from_json(cls, raw: object) -> RetrievalConfig:
        if not isinstance(raw, dict):
            raise ConfigError("config 'memory.retrieval' must be a JSON object")
        expected = {"mode", "candidate_count", "rrf_rank_constant"}
        unknown = sorted(set(raw) - expected)
        if unknown:
            raise ConfigError(f"config 'memory.retrieval' contains unknown fields: {', '.join(unknown)}")
        defaults = cls()
        mode = raw.get("mode", defaults.mode)
        candidate_count = raw.get("candidate_count", defaults.candidate_count)
        rank_constant = raw.get("rrf_rank_constant", defaults.rrf_rank_constant)
        if not isinstance(mode, str):
            raise ConfigError("config 'memory.retrieval.mode' must be a string")
        if isinstance(candidate_count, bool) or not isinstance(candidate_count, int):
            raise ConfigError("config 'memory.retrieval.candidate_count' must be an integer")
        if isinstance(rank_constant, bool) or not isinstance(rank_constant, int):
            raise ConfigError("config 'memory.retrieval.rrf_rank_constant' must be an integer")
        return cls(mode=mode, candidate_count=candidate_count, rrf_rank_constant=rank_constant).validate()


@dataclass(frozen=True)
class MemoryConfig:
    """Static policy and infrastructure for authoritative VSS memory."""

    enabled: bool = True
    backend: str = "elasticsearch"
    index: str = "vss-memory"
    persist_by_default: bool = True
    markdown: MarkdownMemoryConfig = field(default_factory=MarkdownMemoryConfig)
    introspection: IntrospectionMemoryConfig | None = None
    embeddings: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    retrieval: RetrievalConfig = field(default_factory=RetrievalConfig)

    @property
    def effective_retrieval_mode(self) -> str:
        """Use keyword retrieval whenever semantic infrastructure is disabled."""
        return self.retrieval.mode if self.embeddings.enabled else "keyword"

    def validate(self) -> MemoryConfig:
        if self.backend != "elasticsearch":
            raise ConfigError(f"unsupported memory backend {self.backend!r}; configure `--backend elasticsearch`")
        validate_memory_index(self.index)
        if self.persist_by_default and not self.enabled:
            raise ConfigError(
                "memory persistence cannot be enabled by default while memory is disabled; "
                "use `vss configure memory --disable --no-persist-by-default`"
            )
        self.markdown.validate()
        if self.introspection is not None:
            self.introspection.validate()
        self.embeddings.validate()
        self.retrieval.validate()
        if self.embeddings.enabled and self.embeddings.index == self.index:
            raise ConfigError("embedding index must differ from the authoritative memory index")
        if self.markdown.enabled and not self.enabled:
            raise ConfigError("Markdown memory cannot be enabled while authoritative memory is disabled")
        if self.markdown.write_by_default and not self.persist_by_default:
            raise ConfigError(
                "Markdown notes cannot be written by default while authoritative persistence is disabled; "
                "use `--persist-by-default` or `--no-write-notes-by-default`"
            )
        return self

    def to_json(self) -> dict[str, Any]:
        self.validate()
        return {
            "enabled": self.enabled,
            "backend": self.backend,
            "index": self.index,
            "persist_by_default": self.persist_by_default,
            "markdown": self.markdown.to_json(),
            "introspection": self.introspection.to_json() if self.introspection is not None else None,
            "embeddings": self.embeddings.to_json(),
            "retrieval": self.retrieval.to_json(),
        }

    @classmethod
    def from_json(cls, raw: object) -> MemoryConfig:
        if not isinstance(raw, dict):
            raise ConfigError("config 'memory' must be a JSON object")
        expected = {
            "enabled",
            "backend",
            "index",
            "persist_by_default",
            "markdown",
            "introspection",
            "embeddings",
            "retrieval",
        }
        unknown = sorted(set(raw) - expected)
        if unknown:
            raise ConfigError(f"config 'memory' contains unknown fields: {', '.join(unknown)}")
        enabled = raw.get("enabled")
        backend = raw.get("backend")
        index = raw.get("index")
        persist_by_default = raw.get("persist_by_default")
        if not isinstance(enabled, bool):
            raise ConfigError("config 'memory.enabled' must be true or false")
        if not isinstance(backend, str):
            raise ConfigError("config 'memory.backend' must be a string")
        if not isinstance(index, str):
            raise ConfigError("config 'memory.index' must be a string")
        if not isinstance(persist_by_default, bool):
            raise ConfigError("config 'memory.persist_by_default' must be true or false")
        return cls(
            enabled=enabled,
            backend=backend,
            index=index,
            persist_by_default=persist_by_default,
            markdown=MarkdownMemoryConfig.from_json(raw["markdown"]) if "markdown" in raw else MarkdownMemoryConfig(),
            introspection=(
                IntrospectionMemoryConfig.from_json(raw["introspection"])
                if raw.get("introspection") is not None
                else None
            ),
            embeddings=EmbeddingConfig.from_json(raw["embeddings"]) if "embeddings" in raw else EmbeddingConfig(),
            retrieval=RetrievalConfig.from_json(raw["retrieval"]) if "retrieval" in raw else RetrievalConfig(),
        ).validate()


@dataclass(frozen=True)
class Deployment:
    """A resolved deployment: the answer ``vss configure`` recorded.

    Purely descriptive: every field is something a backend reported about
    itself. Nothing here encodes CLI or command-group policy -- request
    timeouts, result caps and fallback behaviour are caller preferences, not
    facts about a deployment, and putting them here would couple the two
    domains. A second CLI reading this file should be able to talk to the
    deployment without inheriting our defaults.
    """

    base_url: str
    services: dict[str, Service] = field(default_factory=dict)
    memory: MemoryConfig | None = None
    #: ISO-8601. Purely informational, but the thing to quote when a stale
    #: config sends someone chasing a connection error.
    written_at: str = ""

    def has(self, name: str) -> bool:
        """Whether the deployment exposes a usable URL for ``name``."""
        service = self.services.get(name)
        return bool(service and service.url)

    def endpoint_or_none(self, name: str) -> str | None:
        """Resolve a service's URL, or None when it is not exposed.

        For services an action can do without -- a search still returns hits
        when VST is absent, it just cannot mint media links. Callers that
        genuinely require a service use :meth:`endpoint`.
        """
        service = self.services.get(name)
        return service.url if service and service.url else None

    def endpoint(self, name: str) -> str:
        """Resolve one service's URL, or raise with something actionable."""
        service = self.services.get(name)
        url = service.url if service else ""
        if not url:
            known = ", ".join(sorted(self.services)) or "(none)"
            raise ConfigError(
                f"deployment at {self.base_url} exposes no {name!r} route; it has: {known}. "
                f"Re-run `vss configure --base-url {self.base_url}` if the deployment changed."
            )
        return url

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "version": CONFIG_VERSION,
            "base_url": self.base_url,
            "written_at": self.written_at,
            "services": {name: svc.to_json() for name, svc in sorted(self.services.items())},
        }
        if self.memory is not None:
            payload["memory"] = self.memory.to_json()
        return payload

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> Deployment:
        version = raw.get("version")
        if version != CONFIG_VERSION:
            raise ConfigError(
                f"config at {config_path()} is version {version!r}, this vss expects {CONFIG_VERSION}. "
                f"Re-run `vss configure` to rewrite it."
            )
        # Right version number, wrong shape: a file this CLI did not write can
        # match on `version` and still carry none of the fields, which used to
        # yield a deployment with an empty origin and no services. That failed
        # later as "the deployment at  does not expose ... it has: (none)",
        # which reads like a broken backend rather than an unreadable file.
        base_url = raw.get("base_url")
        if not isinstance(base_url, str) or not base_url.strip():
            raise ConfigError(
                f"config at {config_path()} has no 'base_url' -- it was not written by "
                f"`vss configure` (top-level keys: {', '.join(sorted(raw)) or 'none'}). "
                f"Re-run `vss configure --base-url <origin>` to rewrite it."
            )
        raw_services = raw.get("services")
        if not isinstance(raw_services, dict) or not raw_services:
            raise ConfigError(
                f"config at {config_path()} records no services. "
                f"Re-run `vss configure --base-url {base_url}` to rediscover them."
            )
        services = {
            name: Service(
                url=body.get("url", ""),
                models=list(body.get("models") or []),
                indices=list(body.get("indices") or []),
            )
            for name, body in raw_services.items()
        }
        raw_memory = raw.get("memory")
        return cls(
            base_url=base_url,
            services=services,
            memory=MemoryConfig.from_json(raw_memory) if raw_memory is not None else None,
            written_at=raw.get("written_at", ""),
        )


def load() -> Deployment:
    """Read the recorded deployment.

    Raises :class:`ConfigError` when absent -- callers map that to exit 4
    (configuration error) with a pointer at ``vss configure``.
    """
    path = config_path()
    if not path.is_file():
        raise ConfigError(
            f"no deployment configured ({path} not found). Run `vss configure --base-url <origin>` first."
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} does not contain a JSON object")
    return Deployment.from_json(raw)


def save(deployment: Deployment) -> Path:
    """Write the deployment, creating ``~/.vss`` if needed.

    Written 0600: the file names internal hosts, and leaving it world-readable
    on a shared box is gratuitous. It deliberately holds **no credentials** --
    tokens stay in the environment.
    """
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(deployment.to_json(), indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


INGRESS_SERVICES.update(
    {
        "agent": ServiceRoute(mount="/api", probe="/api/v1/videos"),
        "vst": ServiceRoute(mount="/vst", probe="/vst/api/v1/sensor/version"),
        "elasticsearch": ServiceRoute(
            mount="/elasticsearch",
            probe="/elasticsearch/_cat/indices?h=index&format=json",
            describe="/elasticsearch/_cat/indices?h=index&format=json",
            describes="indices",
        ),
        # RT-Embed, mounted at the service name rather than the model family
        # it happens to serve: the sibling is /rtvi-cv and the Helm chart
        # already calls it rtvi-embed, so /cosmos-embed was the odd one out.
        # The suffix is whatever the service itself serves -- /v1 here because
        # RT-Embed is OpenAI-shaped, /api/v1 for RT-CV -- which is the same
        # convention /vst/api and /alert-bridge/api/v1 already follow.
        "rt_embed": ServiceRoute(
            mount="/rtvi-embed",
            probe="/rtvi-embed/v1/models",
            describe="/rtvi-embed/v1/models",
            describes="models",
        ),
        # RT-CV exposes no introspection endpoint -- only POST /stream/add
        # and /stream/remove, so there is nothing to describe. The probe is a
        # GET against a real path: it answers 400 (bad request) when routed
        # and 404 when not, which distinguishes the two without mutating
        # anything. Recorded by URL alone until the service grows a
        # read-only endpoint.
        "rtvi_cv": ServiceRoute(mount="/rtvi-cv", probe="/rtvi-cv/api/v1/stream/add"),
        # RT-VLM speaks the same OpenAI shape as RT-Embed, so ``/v1/models`` is
        # both the proof it is routed and the description of what it serves.
        # In remote-VLM deployments the local container stays in the request
        # path as an openai-compat proxy, so the recorded url is local while the
        # model id names the remote backend -- which is the model actually
        # serving, and the honest answer for a descriptive config.
        "rt_vlm": ServiceRoute(
            mount="/rtvi-vlm",
            probe="/rtvi-vlm/v1/models",
            describe="/rtvi-vlm/v1/models",
            describes="models",
        ),
        # Long-video summarization, its own service rather than a route on the
        # agent: it serves POST /v1/summarize on its own port, and the agent
        # exposes no summarize endpoint to proxy it.
        #
        # Probed on liveness, not readiness: /v1/ready answers 503 through
        # several minutes of model warmup, which would record the route as
        # absent on a deployment that is merely still starting, while /v1/live
        # answers as soon as the service is listening.
        #
        # Described from /models -- unprefixed, unlike the /v1 health routes,
        # and verified OpenAI-shaped against a live deployment. It reports the
        # VLM this service summarizes with, so the config carries the backend's
        # own answer instead of a model id someone typed.
        "lvs": ServiceRoute(
            mount="/lvs",
            probe="/lvs/v1/live",
            describe="/lvs/models",
            describes="models",
        ),
    }
)
