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
from typing import Any

#: Where the resolved deployment lives. Override for tests or for a second
#: deployment via ``VSS_CONFIG_HOME``.
CONFIG_HOME_ENV = "VSS_CONFIG_HOME"

#: Bumped when the on-disk shape changes incompatibly. A file written by a
#: newer CLI is refused rather than half-read.
CONFIG_VERSION = 1

#: Ingress routes a deployment may expose behind one origin: the mount path,
#: and a path under it that answers 200 when the route is genuinely wired.
#:
#: The probe path is not decoration. Requesting the mount root cannot tell
#: "route absent" from "route present, root has no handler" -- an unrouted
#: ``/elasticsearch`` and a routed ``/api`` both answer 404. Only a request
#: for something real distinguishes them, which costs the CLI a little
#: per-service knowledge. The alternative is the deployment declaring its own
#: route map (e.g. ``GET /.well-known/vss``); until an ingress serves that,
#: this is the honest version.
INGRESS_ROUTES: dict[str, tuple[str, str]] = {
    "agent": ("/api", "/api/v1/videos"),
    "vst": ("/vst", "/vst/api/v1/sensor/version"),
    "elasticsearch": ("/elasticsearch", "/elasticsearch/_cluster/health"),
    "cosmos_embed": ("/cosmos-embed", "/cosmos-embed/v1/models"),
    "rtvi_cv": ("/rtvi-cv", "/rtvi-cv/api/v1/streams"),
}


class ConfigError(Exception):
    """Configuration is missing, unreadable, or from an incompatible version."""


def config_home() -> Path:
    """Directory holding ``config.json``. Honours ``VSS_CONFIG_HOME``."""
    override = os.environ.get(CONFIG_HOME_ENV, "").strip()
    return Path(override) if override else Path.home() / ".vss"


def config_path() -> Path:
    return config_home() / "config.json"


@dataclass(frozen=True)
class Deployment:
    """A resolved deployment: the answer ``vss configure`` recorded.

    ``endpoints`` maps the :data:`INGRESS_ROUTES` keys to absolute URLs.
    ``indices`` and ``defaults`` carry the values that used to be per-call
    flags -- index names and the behaviour knobs (request timeout, frame
    lookup, max results, embed-only fallback) that describe how a deployment
    behaves rather than what a caller is asking for.
    """

    base_url: str
    endpoints: dict[str, str] = field(default_factory=dict)
    indices: dict[str, str] = field(default_factory=dict)
    defaults: dict[str, Any] = field(default_factory=dict)
    embed_model: str = ""
    #: ISO-8601. Purely informational, but the thing to quote when a stale
    #: config sends someone chasing a connection error.
    written_at: str = ""

    def endpoint(self, name: str) -> str:
        """Resolve one logical service, or raise with something actionable."""
        url = self.endpoints.get(name, "")
        if not url:
            known = ", ".join(sorted(self.endpoints)) or "(none)"
            raise ConfigError(
                f"deployment at {self.base_url} exposes no {name!r} route; it has: {known}. "
                f"Re-run `vss configure --base-url {self.base_url}` if the deployment changed."
            )
        return url

    def to_json(self) -> dict[str, Any]:
        return {
            "version": CONFIG_VERSION,
            "base_url": self.base_url,
            "endpoints": self.endpoints,
            "indices": self.indices,
            "defaults": self.defaults,
            "embed_model": self.embed_model,
            "written_at": self.written_at,
        }

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> Deployment:
        version = raw.get("version")
        if version != CONFIG_VERSION:
            raise ConfigError(
                f"config at {config_path()} is version {version!r}, this vss expects {CONFIG_VERSION}. "
                f"Re-run `vss configure` to rewrite it."
            )
        return cls(
            base_url=raw.get("base_url", ""),
            endpoints=dict(raw.get("endpoints") or {}),
            indices=dict(raw.get("indices") or {}),
            defaults=dict(raw.get("defaults") or {}),
            embed_model=raw.get("embed_model", ""),
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
