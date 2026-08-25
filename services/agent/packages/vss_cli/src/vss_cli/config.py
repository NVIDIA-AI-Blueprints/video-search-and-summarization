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
class MemoryConfig:
    """Static policy and infrastructure for authoritative VSS memory."""

    enabled: bool = True
    backend: str = "elasticsearch"
    index: str = "vss-memory"
    persist_by_default: bool = True

    def validate(self) -> MemoryConfig:
        if self.backend != "elasticsearch":
            raise ConfigError(f"unsupported memory backend {self.backend!r}; configure `--backend elasticsearch`")
        validate_memory_index(self.index)
        if self.persist_by_default and not self.enabled:
            raise ConfigError(
                "memory persistence cannot be enabled by default while memory is disabled; "
                "use `vss configure memory --disable --no-persist-by-default`"
            )
        return self

    def to_json(self) -> dict[str, Any]:
        self.validate()
        return {
            "enabled": self.enabled,
            "backend": self.backend,
            "index": self.index,
            "persist_by_default": self.persist_by_default,
        }

    @classmethod
    def from_json(cls, raw: object) -> MemoryConfig:
        if not isinstance(raw, dict):
            raise ConfigError("config 'memory' must be a JSON object")
        expected = {"enabled", "backend", "index", "persist_by_default"}
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
