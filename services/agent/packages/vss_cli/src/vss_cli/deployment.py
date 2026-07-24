# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Deployment-aware, host-side configuration for :mod:`cli.search`.

This module deliberately does *not* query the agent's runtime endpoint.  That
endpoint is both an unnecessary control-plane dependency and, on Kubernetes,
contains in-cluster URLs that are not usable from a workstation.  Instead we
reconstruct the non-secret runtime inputs from the deployment's source of
truth:

* Docker: shared service defaults, a profile's layered .env/generated.env
  files, and checked-out agent config;
* Kubernetes: the live agent Deployment, ConfigMap, and non-secret ConfigMap
  references used by that Deployment.

Only a small, explicit allow-list is read from Kubernetes.  In particular,
``secretKeyRef`` and Secret-backed ``envFrom`` values are never read, logged,
or passed to the host process.
"""

from __future__ import annotations

import atexit
from collections.abc import Collection
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
import json
from pathlib import Path
import re
import signal
import socket
import subprocess
import tempfile
import time
from typing import Any
from urllib.parse import urlsplit
from urllib.parse import urlunsplit

from vss_core.search_core.errors import ConfigurationError


class PortForwardError(ConfigurationError):
    """A managed Kubernetes Service port-forward could not become ready."""


# These are the values consumed while parsing the NAT config and constructing
# SearchRuntime.  Keeping this list narrow is the security boundary for the
# Kubernetes discovery path: a Deployment Secret must never become a host CLI
# input merely because it happens to be present on the agent container.
RUNTIME_ENV_ALLOWLIST = frozenset(
    {
        "BEHAVIOR_ES_ENDPOINT",
        "BEHAVIOR_ES_INDEX",
        "BEHAVIOR_INDEX",
        "BEHAVIOR_INDEX_WILDCARD",
        "COSMOS_EMBED_ENDPOINT",
        "ELASTICSEARCH_HOST_PORT",
        "ELASTIC_SEARCH_ENDPOINT",
        "ELASTIC_SEARCH_INDEX",
        "ELASTIC_SEARCH_INDEX_WILDCARD",
        "ENABLE_AUDIO",
        "ENABLE_CRITIC",
        "FRAMES_INDEX",
        "FRAMES_INDEX_WILDCARD",
        "HOST_IP",
        "RAW_ES_ENDPOINT",
        "RAW_ES_INDEX",
        "RTSP_BEHAVIOR_ES_INDEX_PATTERN",
        "RTSP_EMBED_ES_INDEX_PATTERN",
        "RTSP_RAW_ES_INDEX_PATTERN",
        "RTVI_CV_BASE_URL",
        "RTVI_CV_ENDPOINT",
        "RTVI_CV_HOST_PORT",
        "RTVI_CV_PORT",
        "RTVI_EMBED_BASE_URL",
        "RTVI_EMBED_MODEL",
        "RTVI_EMBED_ES_INDEX",
        "RTVI_EMBED_PORT",
        "RTVI_VLM_PORT",
        "VLM_MODE",
        "VLM_BASE_URL",
        "VLM_PORT",
        "VLM_MODEL_TYPE",
        "VLM_NAME",
        "VST_BASE_URL",
        "VST_EXTERNAL_URL",
        "VST_INTERNAL_URL",
        "VST_INGRESS_HOST_PORT",
        "VST_PORT",
        "VSS_AGENT_HOST_PORT",
    }
)

_ENV_LINE = re.compile(r"(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_INTERPOLATION = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)(?::-([^}]*))?\}")
_INTERNAL_DOCKER_HOSTS = frozenset(
    {
        "elasticsearch",
        "rtvi-embed",
        "vss-rtvi-embed",
        "rtvi-cv",
        "vss-rtvi-cv",
        "rtvi-vlm",
        "vss-rtvi-vlm",
        "vst-ingress",
    }
)


def _repo_root() -> Path:
    """Find the checkout root without assuming where ``uv run`` was launched."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "deploy" / "docker").is_dir() and (parent / "libs").is_dir():
            return parent
    raise ConfigurationError("could not locate repository root containing deploy/docker")


def _parse_env_file(path: Path) -> dict[str, str]:
    """Parse Docker's simple ``KEY=VALUE`` env-file grammar without sourcing it."""
    try:
        lines = path.read_text().splitlines()
    except OSError as e:
        raise ConfigurationError(f"could not read Docker environment file {str(path)!r}: {e}") from e

    env: dict[str, str] = {}
    for number, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _ENV_LINE.fullmatch(line)
        if match is None:
            raise ConfigurationError(f"invalid Docker environment line {number} in {str(path)!r}")
        key, value = match.groups()
        # Docker accepts quoted values in env files.  We do not expand shell
        # syntax: RuntimeSnapshot owns the small, safe ${VAR} interpolation
        # grammar for config.yml.
        if len(value) >= 2 and value[:1] == value[-1:] and value[:1] in {"'", '"'}:
            value = value[1:-1]
        if "\n" in value or "\r" in value:
            raise ConfigurationError(f"Docker environment value for {key!r} in {str(path)!r} must not contain newlines")
        env[key] = value
    return env


def _expand_env_values(values: Mapping[str, str]) -> dict[str, str]:
    """Resolve Docker-style references in a merged profile environment.

    The checked-in ``.env`` supplies stable defaults and ``generated.env``
    overlays deployment-specific values. This bounded expansion matches the
    config interpolation semantics and never consults process env.
    """
    source = dict(values)
    resolved: dict[str, str] = {}

    def resolve(key: str, visiting: tuple[str, ...]) -> str:
        cached = resolved.get(key)
        if cached is not None:
            return cached
        if key in visiting:
            cycle = " -> ".join((*visiting[visiting.index(key) :], key))
            raise ConfigurationError(f"Docker profile environment contains a circular variable reference: {cycle}")

        def substitute(match: re.Match[str]) -> str:
            dependency = match.group(1)
            default = match.group(2) or ""
            if dependency not in source:
                return default
            return resolve(dependency, (*visiting, key)) or default

        rendered = _INTERPOLATION.sub(substitute, source[key])
        resolved[key] = rendered
        return rendered

    for name in source:
        resolve(name, ())
    return resolved


def _replace_url_host(value: str, *, host: str, port: int) -> str:
    parsed = urlsplit(value)
    if not parsed.scheme or not parsed.hostname:
        return value
    auth = ""
    if parsed.username:
        auth = parsed.username
        if parsed.password:
            auth += f":{parsed.password}"
        auth += "@"
    return urlunsplit((parsed.scheme, f"{auth}{host}:{port}", parsed.path, parsed.query, parsed.fragment))


def _is_docker_internal_url(value: str) -> bool:
    parsed = urlsplit(value)
    if not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    # Compose service aliases are single-label names.  A public deployment URL
    # may legitimately start with ``vss-`` or ``rtvi-`` too; never rewrite a
    # dotted/FQDN host merely because of that prefix.
    return host in _INTERNAL_DOCKER_HOSTS or ("." not in host and host.startswith(("vss-", "rtvi-")))


def _env_port(env: Mapping[str, str], *keys: str, default: int) -> int:
    """Read the first configured Docker host port with a typed diagnostic."""
    raw = next((env[key] for key in keys if env.get(key)), str(default))
    try:
        port = int(raw)
    except ValueError as e:
        raise ConfigurationError(f"Docker profile port {keys[0]!r} must be an integer, got {raw!r}") from e
    if not 1 <= port <= 65535:
        raise ConfigurationError(f"Docker profile port {keys[0]!r} must be in [1, 65535], got {port}")
    return port


@dataclass(slots=True)
class DeploymentConfig:
    """A config file plus interpolation values discovered for one deployment."""

    config_path: Path
    env: dict[str, str]
    _temporary_directory: tempfile.TemporaryDirectory[str] | None = None

    def rewrite_runtime(self, runtime: Any, *, fields: Collection[str] | None = None) -> Any:
        """Return runtime unchanged for Docker/external endpoints.

        Kubernetes subclasses replace in-cluster service URLs with local
        port-forwards after the config has been parsed.
        """
        _ = fields
        return runtime

    def close(self) -> None:
        if self._temporary_directory is not None:
            self._temporary_directory.cleanup()
            self._temporary_directory = None


def _docker_profile_runtime(profile: str) -> tuple[Path, dict[str, str]]:
    """Return a profile config and its fully expanded Compose environment."""
    normalized = profile.removeprefix("dev-profile-")
    docker_root = _repo_root() / "deploy" / "docker"
    profile_dir = docker_root / "developer-profiles" / f"dev-profile-{normalized}"
    stable = profile_dir / ".env"
    generated = profile_dir / "generated.env"
    config = profile_dir / "vss-agent" / "configs" / "config.yml"
    service_defaults = (
        docker_root / "services" / "vios" / "vst.env",
        docker_root / "services" / "rtvi" / "rtvi.env",
    )
    if not stable.is_file():
        raise ConfigurationError(f"Docker profile {profile!r} has no .env at {str(stable)!r}.")
    if not generated.is_file():
        raise ConfigurationError(
            f"Docker profile {profile!r} has no generated.env at {str(generated)!r}. "
            "Start it with deploy/docker/scripts/dev-profile.sh first."
        )
    if not config.is_file():
        raise ConfigurationError(f"Docker profile {profile!r} has no agent config at {str(config)!r}")
    for path in service_defaults:
        if not path.is_file():
            raise ConfigurationError(f"Docker profile {profile!r} has no service environment at {str(path)!r}")

    # Compose includes the shared VST/RTVI defaults beneath the profile. Keep
    # only runtime keys from those broad service files so unrelated variables
    # (including self-defaulting Compose expressions) cannot affect discovery.
    env: dict[str, str] = {}
    for path in service_defaults:
        env.update({key: value for key, value in _parse_env_file(path).items() if key in RUNTIME_ENV_ALLOWLIST})

    # Match Docker Compose's --env-file ordering: stable profile values first,
    # then deployment-specific generated values as the authoritative overlay.
    env.update(_parse_env_file(stable))
    env.update(_parse_env_file(generated))
    # agent/compose.yml supplies this private, service-to-service endpoint
    # directly. RTVI-CV does not expose TLS inside the Compose network.
    env.setdefault(
        "RTVI_CV_ENDPOINT",
        "http://vss-rtvi-cv:${RTVI_CV_PORT:-9000}",  # NOSONAR S5332
    )
    return config, _expand_env_values(env)


def discover_docker_host_endpoints(profile: str) -> dict[str, str]:
    """Return safe loopback management URLs for a running Docker profile."""
    _, env = _docker_profile_runtime(profile)
    return {
        # Docker publishes these management ports to the local host only.
        "agent_url": f"http://127.0.0.1:{_env_port(env, 'VSS_AGENT_HOST_PORT', default=8000)}",  # NOSONAR
        "vst_url": f"http://127.0.0.1:{_env_port(env, 'VST_INGRESS_HOST_PORT', 'VST_PORT', default=30888)}",  # NOSONAR
        "es_url": f"http://127.0.0.1:{_env_port(env, 'ELASTICSEARCH_HOST_PORT', default=9200)}",  # NOSONAR
        # RT-VLM proxy: host-published loopback port (compose DNS rtvi-vlm:8000 is
        # not reachable from the host, where this CLI runs). Keyed on RTVI_VLM_PORT
        # only — VLM_PORT is the separate Cosmos VLM NIM, not the RT-VLM proxy.
        "rtvi_vlm_url": f"http://127.0.0.1:{_env_port(env, 'RTVI_VLM_PORT', default=8018)}",  # NOSONAR
    }


def discover_docker(profile: str) -> DeploymentConfig:
    """Load host-search configuration for a local Docker developer profile."""
    config, env = _docker_profile_runtime(profile)

    # Compose uses private DNS between containers, while the supported CLI
    # execution is on the host.  Repoint only known in-cluster endpoints to the
    # published loopback ports.  Explicit --*-endpoint flags still win later.
    docker_ports = {
        "ELASTIC_SEARCH_ENDPOINT": _env_port(env, "ELASTICSEARCH_HOST_PORT", default=9200),
        "BEHAVIOR_ES_ENDPOINT": _env_port(env, "ELASTICSEARCH_HOST_PORT", default=9200),
        "COSMOS_EMBED_ENDPOINT": _env_port(env, "RTVI_EMBED_PORT", default=8017),
        "RTVI_EMBED_BASE_URL": _env_port(env, "RTVI_EMBED_PORT", default=8017),
        "RTVI_CV_ENDPOINT": _env_port(env, "RTVI_CV_HOST_PORT", "RTVI_CV_PORT", default=9000),
        "RTVI_CV_BASE_URL": _env_port(env, "RTVI_CV_HOST_PORT", "RTVI_CV_PORT", default=9000),
        "VST_INTERNAL_URL": _env_port(env, "VST_INGRESS_HOST_PORT", "VST_PORT", default=30888),
    }
    vlm_url = env.get("VLM_BASE_URL", "")
    vlm_host = (urlsplit(vlm_url).hostname or "").lower()
    if vlm_host in {"rtvi-vlm", "vss-rtvi-vlm"}:
        docker_ports["VLM_BASE_URL"] = _env_port(env, "RTVI_VLM_PORT", "VLM_PORT", default=8018)
    else:
        docker_ports["VLM_BASE_URL"] = _env_port(env, "VLM_PORT", default=30082)
    for key, port in docker_ports.items():
        value = env.get(key)
        if value and _is_docker_internal_url(value):
            env[key] = _replace_url_host(value, host="127.0.0.1", port=port)
    safe_env = {key: value for key, value in env.items() if key in RUNTIME_ENV_ALLOWLIST}
    return DeploymentConfig(config_path=config, env=safe_env)


class _Kubectl:
    def __init__(self, namespace: str, context: str | None) -> None:
        self.namespace = namespace
        self.context = context

    def command(self, *args: str, namespace: str | None = None) -> list[str]:
        command = ["kubectl"]
        if self.context:
            command.extend(["--context", self.context])
        command.extend(["--namespace", namespace or self.namespace, *args])
        return command

    def _execute(self, *args: str, namespace: str | None = None) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(self.command(*args, namespace=namespace), capture_output=True, text=True, check=False)
        except OSError as e:
            raise ConfigurationError(f"could not execute kubectl: {e}") from e

    def _run(self, *args: str, namespace: str | None = None) -> subprocess.CompletedProcess[str]:
        process = self._execute(*args, namespace=namespace)
        if process.returncode != 0:
            detail = process.stderr.strip() or process.stdout.strip() or "kubectl command failed"
            raise ConfigurationError(detail)
        return process

    def json(self, *args: str) -> Mapping[str, Any]:
        process = self._run(*args)
        try:
            payload = json.loads(process.stdout)
        except json.JSONDecodeError as e:
            raise ConfigurationError("kubectl returned invalid JSON") from e
        if not isinstance(payload, Mapping):
            raise ConfigurationError("kubectl returned an unexpected JSON document")
        return payload

    def json_optional(self, *args: str) -> Mapping[str, Any] | None:
        """Return one JSON resource, or ``None`` only for --ignore-not-found."""
        process = self._run(*args)
        if not process.stdout.strip():
            return None
        try:
            payload = json.loads(process.stdout)
        except json.JSONDecodeError as e:
            raise ConfigurationError("kubectl returned invalid JSON") from e
        if not isinstance(payload, Mapping):
            raise ConfigurationError("kubectl returned an unexpected JSON document")
        return payload

    def service_exists(self, name: str, *, namespace: str | None = None) -> bool:
        process = self._execute("get", "service", name, "--ignore-not-found", "-o", "name", namespace=namespace)
        if process.returncode != 0:
            detail = process.stderr.strip() or process.stdout.strip() or "kubectl command failed"
            normalized = detail.lower()
            if "(notfound)" in normalized and ("service" in normalized or "namespace" in normalized):
                return False
            raise ConfigurationError(detail)
        return bool(process.stdout.strip())


def _configmap_data(
    kubectl: _Kubectl,
    name: str,
    cache: dict[str, Mapping[str, str]],
    *,
    optional: bool = False,
) -> Mapping[str, str]:
    if name in cache:
        return cache[name]
    if optional:
        resource = kubectl.json_optional("get", "configmap", name, "--ignore-not-found", "-o", "json")
        if resource is None:
            return {}
    else:
        resource = kubectl.json("get", "configmap", name, "-o", "json")
    raw_data = resource.get("data") or {}
    if not isinstance(raw_data, Mapping) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in raw_data.items()
    ):
        raise ConfigurationError(f"ConfigMap {name!r} has invalid data")
    data = dict(raw_data)
    cache[name] = data
    return data


def _agent_deployment(kubectl: _Kubectl, release: str) -> Mapping[str, Any]:
    # Use the exact application label so vss-agent-ui cannot be mistaken for
    # the backend. Fall back to the two supported prefixed/unprefixed resource
    # names for older chart releases that did not carry this label.
    listing = kubectl.json("get", "deployment", "-l", f"app.kubernetes.io/instance={release}", "-o", "json")
    items = listing.get("items") or []
    candidates = [item for item in items if isinstance(item, Mapping)]
    labeled = [
        item
        for item in candidates
        if str(((item.get("metadata") or {}).get("labels") or {}).get("app.kubernetes.io/name", "")) == "vss-agent"
    ]
    if len(labeled) == 1:
        return labeled[0]

    named: list[Mapping[str, Any]] = []
    for name in (f"{release}-vss-agent", "vss-agent"):
        resource = kubectl.json_optional("get", "deployment", name, "--ignore-not-found", "-o", "json")
        if resource is None:
            continue
        labels = (resource.get("metadata") or {}).get("labels") or {}
        instance = str(labels.get("app.kubernetes.io/instance", ""))
        app_name = str(labels.get("app.kubernetes.io/name", ""))
        # A supported legacy name may omit chart labels. Once labels exist,
        # however, they must identify this release's backend rather than a
        # similarly named Deployment owned by another release/application.
        if (instance and instance != release) or (app_name and app_name != "vss-agent"):
            continue
        named.append(resource)
    if len(named) == 1:
        return named[0]
    raise ConfigurationError(
        f"could not uniquely identify the vss-agent Deployment for release {release!r} in namespace {kubectl.namespace!r}"
    )


def _agent_container(deployment: Mapping[str, Any]) -> Mapping[str, Any]:
    containers = (((deployment.get("spec") or {}).get("template") or {}).get("spec") or {}).get("containers", [])
    candidates = [container for container in containers if isinstance(container, Mapping)]
    for container in candidates:
        if "agent" in str(container.get("name", "")).lower():
            return container
    if len(candidates) == 1:
        return candidates[0]
    raise ConfigurationError("vss-agent Deployment does not contain an identifiable agent container")


def _agent_configmap_name(deployment: Mapping[str, Any], container: Mapping[str, Any]) -> tuple[str, str]:
    pod_spec = ((deployment.get("spec") or {}).get("template") or {}).get("spec") or {}
    volumes = pod_spec.get("volumes") or []
    config_volumes: dict[str, Mapping[str, Any]] = {
        str(volume.get("name")): volume
        for volume in volumes
        if isinstance(volume, Mapping) and isinstance(volume.get("configMap"), Mapping)
    }
    mounts = container.get("volumeMounts") or []
    for mount in mounts:
        if not isinstance(mount, Mapping):
            continue
        volume = config_volumes.get(str(mount.get("name")))
        if volume is None:
            continue
        config_map = volume["configMap"]
        name = config_map.get("name")
        if not isinstance(name, str):
            continue
        # The config volume convention maps a config.yml key.  Prefer a mount
        # under /etc/vss-agent, but tolerate a chart that has a single configmap.
        mount_path = str(mount.get("mountPath", ""))
        if "vss-agent" in mount_path or mount.get("subPath") == "config.yml":
            return name, str(mount.get("subPath") or "config.yml")
    if len(config_volumes) == 1:
        only = next(iter(config_volumes.values()))
        config_map = only["configMap"]
        name = config_map.get("name")
        if isinstance(name, str):
            return name, "config.yml"
    raise ConfigurationError("could not identify the vss-agent config ConfigMap from the live Deployment")


def _read_nonsecret_environment(
    container: Mapping[str, Any], kubectl: _Kubectl, cache: dict[str, Mapping[str, str]]
) -> tuple[dict[str, str], set[str]]:
    """Read allow-listed literal/configMap env, tracking secret-backed keys."""
    env: dict[str, str] = {}
    secret_backed: set[str] = set()
    for source in container.get("envFrom") or []:
        if not isinstance(source, Mapping):
            continue
        config_ref = source.get("configMapRef")
        if isinstance(config_ref, Mapping) and isinstance(config_ref.get("name"), str):
            values = _configmap_data(
                kubectl,
                config_ref["name"],
                cache,
                optional=bool(config_ref.get("optional", False)),
            )
            prefix = str(source.get("prefix") or "")
            for key, value in values.items():
                effective_key = prefix + key
                if effective_key in RUNTIME_ENV_ALLOWLIST:
                    env[effective_key] = value
        secret_ref = source.get("secretRef")
        if isinstance(secret_ref, Mapping):
            # envFrom cannot say which keys exist without reading the Secret;
            # intentionally do nothing. A required missing key is diagnosed
            # after collecting all non-secret values.
            continue

    for item in container.get("env") or []:
        if not isinstance(item, Mapping) or not isinstance(item.get("name"), str):
            continue
        name = item["name"]
        if name not in RUNTIME_ENV_ALLOWLIST:
            continue
        if isinstance(item.get("value"), str):
            env[name] = item["value"]
            secret_backed.discard(name)
            continue
        value_from = item.get("valueFrom")
        if not isinstance(value_from, Mapping):
            continue
        config_ref = value_from.get("configMapKeyRef")
        if (
            isinstance(config_ref, Mapping)
            and isinstance(config_ref.get("name"), str)
            and isinstance(config_ref.get("key"), str)
        ):
            values = _configmap_data(
                kubectl,
                config_ref["name"],
                cache,
                optional=bool(config_ref.get("optional", False)),
            )
            key = config_ref["key"]
            if key in values:
                env[name] = values[key]
                secret_backed.discard(name)
            elif not config_ref.get("optional", False):
                raise ConfigurationError(f"ConfigMap {config_ref['name']!r} does not contain key {key!r}")
        elif "secretKeyRef" in value_from:
            # Explicit env entries override envFrom and earlier duplicate env
            # entries. Do not retain a stale non-secret value when the pod's
            # effective value is Secret-backed.
            env.pop(name, None)
            secret_backed.add(name)
    return env, secret_backed


def _required_runtime_env_keys(config_text: str) -> set[str]:
    # ``${KEY:-default}`` is self-sufficient when KEY is absent/empty. Only a
    # plain ``${KEY}`` reference makes discovery require a live value.
    return {
        match.group(1)
        for match in _INTERPOLATION.finditer(config_text)
        if match.group(1) in RUNTIME_ENV_ALLOWLIST and match.group(2) is None
    }


class KubernetesDeploymentConfig(DeploymentConfig):
    """Deployment config with managed service port-forwards."""

    def __init__(
        self,
        *,
        config_path: Path,
        env: dict[str, str],
        kubectl: _Kubectl,
        temporary_directory: tempfile.TemporaryDirectory[str],
    ) -> None:
        super().__init__(config_path=config_path, env=env, _temporary_directory=temporary_directory)
        self._kubectl = kubectl
        self._forwards: list[subprocess.Popen[str]] = []
        self._forward_by_target: dict[tuple[str, str, int], int] = {}
        self._saved_handlers: dict[int, Any] = {}
        self._closed = False
        atexit.register(self.close)

    def _service_target(self, hostname: str) -> tuple[str, str] | None:
        normalized = hostname.rstrip(".").lower()
        if normalized in {"localhost", "127.0.0.1", "::1"}:
            return None
        labels = normalized.split(".")
        service = labels[0]
        if len(labels) == 1:
            namespace = self._kubectl.namespace
        elif len(labels) == 2:
            # Kubernetes expands service.namespace names through the cluster
            # DNS search path. If this is actually a public two-label host,
            # the existence check simply leaves it untouched.
            namespace = labels[1]
        elif len(labels) >= 3 and labels[2] == "svc":
            namespace = labels[1]
        else:
            return None
        return (service, namespace) if self._kubectl.service_exists(service, namespace=namespace) else None

    @staticmethod
    def _free_local_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    def _install_signal_handlers(self) -> None:
        if self._saved_handlers:
            return
        try:
            for signum in (signal.SIGINT, signal.SIGTERM):
                previous = signal.getsignal(signum)
                self._saved_handlers[signum] = previous

                def _cleanup_then_interrupt(_signum: int, _frame: Any) -> None:
                    self.close()
                    raise KeyboardInterrupt

                signal.signal(signum, _cleanup_then_interrupt)
        except ValueError:
            # Signal handlers can only be installed from the main thread. The
            # normal CLI is main-threaded; tests/embedded callers still get
            # deterministic normal-exit cleanup through close().
            self._saved_handlers.clear()

    def _start_forward(self, service: str, namespace: str, remote_port: int) -> int:
        key = (namespace, service, remote_port)
        existing = self._forward_by_target.get(key)
        if existing is not None:
            return existing
        local_port = self._free_local_port()
        try:
            process = subprocess.Popen(
                self._kubectl.command(
                    "port-forward",
                    "--address",
                    "127.0.0.1",
                    f"service/{service}",
                    f"{local_port}:{remote_port}",
                    namespace=namespace,
                ),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
        except OSError as e:
            raise PortForwardError(
                f"could not start kubectl port-forward for service/{service} in namespace {namespace!r}: {e}"
            ) from e
        self._forwards.append(process)
        # Install cleanup immediately: SIGINT/SIGTERM can arrive while the
        # readiness loop is still waiting, and kubectl runs in its own session.
        # Delaying this until after readiness would orphan the child process.
        self._install_signal_handlers()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if process.poll() is not None:
                detail = process.stderr.read().strip() if process.stderr is not None else ""
                self._discard_forward(process)
                raise PortForwardError(
                    f"kubectl port-forward service/{service} in namespace {namespace!r} failed: "
                    f"{detail or 'process exited'}"
                )
            try:
                with socket.create_connection(("127.0.0.1", local_port), timeout=0.2):
                    self._forward_by_target[key] = local_port
                    return local_port
            except OSError:
                time.sleep(0.1)
        self._discard_forward(process)
        raise PortForwardError(
            f"timed out waiting for kubectl port-forward service/{service} in namespace {namespace!r}"
        )

    def _discard_forward(self, process: subprocess.Popen[str]) -> None:
        """Reap one failed forward without closing otherwise usable discovery state."""
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        with suppress(ValueError):
            self._forwards.remove(process)
        if process.stderr is not None:
            process.stderr.close()

    def _rewrite_endpoint(self, endpoint: str) -> str:
        parsed = urlsplit(endpoint)
        if not parsed.hostname:
            return endpoint
        target = self._service_target(parsed.hostname)
        if target is None:
            return endpoint
        service, namespace = target
        # URL ports may be omitted (notably ingress URLs); supply the scheme's
        # conventional port before invoking kubectl.
        try:
            remote_port = parsed.port or (443 if parsed.scheme == "https" else 80)
        except ValueError as e:
            raise ConfigurationError(f"deployment endpoint has an invalid port: {endpoint!r}") from e
        local_port = self._start_forward(service, namespace, remote_port)
        return _replace_url_host(endpoint, host="127.0.0.1", port=local_port)

    def rewrite_runtime(self, runtime: Any, *, fields: Collection[str] | None = None) -> Any:
        from dataclasses import replace

        rewritable = (
            "es_endpoint",
            "behavior_es_endpoint",
            "cosmos_embed_endpoint",
            "rtvi_cv_endpoint",
            "vst_internal_url",
        )
        selected = set((*rewritable, "vst_external_url") if fields is None else fields)
        external_vst = getattr(runtime, "vst_external_url", None)
        external_host = urlsplit(external_vst).hostname if external_vst else None
        if "vst_external_url" in selected and external_host and self._service_target(external_host) is not None:
            # Screenshot URLs are returned to the caller and must remain valid
            # after normal-exit cleanup closes every managed port-forward.
            # Rewriting this field would emit dead localhost links.
            raise ConfigurationError(
                "the live VST_EXTERNAL_URL points to an in-cluster Service and cannot produce durable host links. "
                "Pass --vst-external-url with a host-reachable ingress URL (or an operator-managed localhost forward)."
            )

        updates: dict[str, str] = {}
        for field in rewritable:
            if field not in selected:
                continue
            value = getattr(runtime, field, None)
            if value:
                updates[field] = self._rewrite_endpoint(value)
        return replace(runtime, **updates) if updates else runtime

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for process in reversed(self._forwards):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
            if process.stderr is not None:
                process.stderr.close()
        self._forwards.clear()
        for signum, previous in self._saved_handlers.items():
            with suppress(ValueError):
                signal.signal(signum, previous)
        self._saved_handlers.clear()
        super().close()


def discover_kubernetes(
    *,
    namespace: str,
    release: str,
    context: str | None = None,
    env_overrides: Mapping[str, str] | None = None,
) -> KubernetesDeploymentConfig:
    """Discover a live, non-secret Kubernetes agent runtime configuration."""
    kubectl = _Kubectl(namespace, context)
    deployment = _agent_deployment(kubectl, release)
    container = _agent_container(deployment)
    cache: dict[str, Mapping[str, str]] = {}
    configmap_name, config_key = _agent_configmap_name(deployment, container)
    config_data = _configmap_data(kubectl, configmap_name, cache)
    config_text = config_data.get(config_key) or config_data.get("config.yml")
    if not config_text:
        raise ConfigurationError(f"agent ConfigMap {configmap_name!r} does not contain config.yml")
    env, secret_backed = _read_nonsecret_environment(container, kubectl, cache)
    # Explicit CLI/config interpolation values are operator-provided and take
    # precedence over discovered non-secret values. Applying them before the
    # required-key check lets an explicit endpoint remediate a Secret-backed
    # or otherwise unavailable deployment value without ever reading a Secret.
    env.update(env_overrides or {})
    required = _required_runtime_env_keys(config_text)
    missing = sorted(required - env.keys())
    secret_missing = sorted(set(missing) & secret_backed)
    if secret_missing:
        raise ConfigurationError(
            "required runtime value(s) are sourced from Kubernetes Secrets and cannot be used by host vss: "
            + ", ".join(secret_missing)
            + ". Supply an explicit non-secret --*-endpoint override or run an operator-managed authenticated workflow."
        )
    if missing:
        raise ConfigurationError(
            "could not obtain required non-secret runtime value(s) from the live Deployment/ConfigMaps: "
            + ", ".join(missing)
            + ". Add them as literal or ConfigMap-backed vss-agent environment values, or pass explicit CLI overrides."
        )

    temporary_directory = tempfile.TemporaryDirectory(prefix="vss-kubernetes-")
    config_path = Path(temporary_directory.name) / "config.yml"
    config_path.write_text(config_text)
    return KubernetesDeploymentConfig(
        config_path=config_path,
        env=env,
        kubectl=kubectl,
        temporary_directory=temporary_directory,
    )


def discover_deployment(args: Any, *, env_overrides: Mapping[str, str] | None = None) -> DeploymentConfig | None:
    """Resolve an optional deployment selector from parsed CLI arguments."""
    deployment = getattr(args, "deployment", None)
    if deployment is None:
        return None
    if getattr(args, "config", None) is not None:
        raise ConfigurationError(
            "--config cannot be combined with --deployment; Docker always uses the selected profile config and "
            "Kubernetes always uses the live agent ConfigMap"
        )
    if deployment == "docker":
        if not getattr(args, "profile", None):
            raise ConfigurationError("--deployment docker requires --profile")
        return discover_docker(args.profile)
    if deployment == "kubernetes":
        if not getattr(args, "namespace", None) or not getattr(args, "release", None):
            raise ConfigurationError("--deployment kubernetes requires --namespace and --release")
        return discover_kubernetes(
            namespace=args.namespace,
            release=args.release,
            context=args.kube_context,
            env_overrides=env_overrides,
        )
    raise ConfigurationError(f"unsupported deployment {deployment!r}")
