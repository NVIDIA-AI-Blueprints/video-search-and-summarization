# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Environment-backed configuration with fail-closed network defaults."""

from __future__ import annotations

import ipaddress
import json
import os
from dataclasses import dataclass
from urllib.parse import urlparse

SUPPORTED_PROTOCOLS = frozenset({"responses", "legacy-chat"})
RESERVED_UPSTREAM_HEADERS = frozenset(
    {"authorization", "content-length", "host", "transfer-encoding"}
)


class ConfigError(ValueError):
    """Gateway configuration is incomplete or unsafe."""


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} must be true or false")


def _int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise ConfigError(f"{name} must be an integer") from error
    if not minimum <= value <= maximum:
        raise ConfigError(f"{name} must be between {minimum} and {maximum}")
    return value


def _float_env(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as error:
        raise ConfigError(f"{name} must be a number") from error
    if not minimum <= value <= maximum:
        raise ConfigError(f"{name} must be between {minimum} and {maximum}")
    return value


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _validated_url(value: str, name: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigError(f"{name} must be an absolute http or https URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ConfigError(
            f"{name} must not contain credentials, a query, or a fragment"
        )
    return value.rstrip("/")


def _extra_headers() -> dict[str, str]:
    raw = os.environ.get("AGENT_BACKEND_HEADERS_JSON", "{}")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ConfigError("AGENT_BACKEND_HEADERS_JSON must be valid JSON") from error
    if not isinstance(value, dict):
        raise ConfigError("AGENT_BACKEND_HEADERS_JSON must be a JSON object")
    headers: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise ConfigError("AGENT_BACKEND_HEADERS_JSON values must be strings")
        if key.lower() in RESERVED_UPSTREAM_HEADERS:
            raise ConfigError(f"AGENT_BACKEND_HEADERS_JSON cannot override {key}")
        if any(character in key + item for character in "\r\n\0"):
            raise ConfigError(
                "AGENT_BACKEND_HEADERS_JSON cannot contain control characters"
            )
        headers[key] = item
    return headers


@dataclass(frozen=True, slots=True)
class GatewayConfig:
    bind_host: str
    bind_port: int
    gateway_token: str | None
    backend_protocol: str
    backend_url: str
    backend_path: str
    backend_token: str | None
    backend_model: str
    backend_session_field: str | None
    backend_session_header: str | None
    backend_headers: dict[str, str]
    request_timeout_seconds: float
    run_retention_seconds: int
    max_runs: int
    max_events_per_run: int

    @classmethod
    def from_env(cls) -> GatewayConfig:
        bind_host = os.environ.get("AGENT_GATEWAY_BIND_HOST", "127.0.0.1").strip()
        gateway_token = os.environ.get("AGENT_GATEWAY_TOKEN", "").strip() or None
        allow_insecure = _bool_env("AGENT_GATEWAY_ALLOW_INSECURE", False)
        if not _is_loopback(bind_host) and gateway_token is None and not allow_insecure:
            raise ConfigError(
                "AGENT_GATEWAY_TOKEN is required for a non-loopback bind; "
                "set AGENT_GATEWAY_ALLOW_INSECURE=true only on an isolated trusted network",
            )

        protocol = os.environ.get("AGENT_BACKEND_PROTOCOL", "responses").strip().lower()
        if protocol not in SUPPORTED_PROTOCOLS:
            supported = ", ".join(sorted(SUPPORTED_PROTOCOLS))
            raise ConfigError(f"AGENT_BACKEND_PROTOCOL must be one of: {supported}")
        backend_url = _validated_url(
            os.environ.get("AGENT_BACKEND_URL", ""), "AGENT_BACKEND_URL"
        )
        default_path = "/v1/responses" if protocol == "responses" else "/chat/stream"
        backend_path = os.environ.get("AGENT_BACKEND_PATH", "").strip() or default_path
        if (
            not backend_path.startswith("/")
            or "?" in backend_path
            or "#" in backend_path
        ):
            raise ConfigError(
                "AGENT_BACKEND_PATH must be an absolute URL path without query or fragment"
            )

        session_field = (
            os.environ.get("AGENT_BACKEND_SESSION_FIELD", "user").strip() or None
        )
        session_header = (
            os.environ.get("AGENT_BACKEND_SESSION_HEADER", "").strip() or None
        )
        if session_header and any(
            character in session_header for character in "\r\n\0"
        ):
            raise ConfigError(
                "AGENT_BACKEND_SESSION_HEADER contains invalid characters"
            )

        return cls(
            bind_host=bind_host,
            bind_port=_int_env("AGENT_GATEWAY_PORT", 8090, 1, 65535),
            gateway_token=gateway_token,
            backend_protocol=protocol,
            backend_url=backend_url,
            backend_path=backend_path,
            backend_token=os.environ.get("AGENT_BACKEND_TOKEN", "").strip() or None,
            backend_model=os.environ.get("AGENT_BACKEND_MODEL", "agent").strip()
            or "agent",
            backend_session_field=session_field,
            backend_session_header=session_header,
            backend_headers=_extra_headers(),
            request_timeout_seconds=_float_env(
                "AGENT_BACKEND_TIMEOUT_SECONDS", 900.0, 1.0, 3600.0
            ),
            run_retention_seconds=_int_env(
                "AGENT_GATEWAY_RUN_RETENTION_SECONDS", 3600, 60, 86400
            ),
            max_runs=_int_env("AGENT_GATEWAY_MAX_RUNS", 1000, 1, 10000),
            max_events_per_run=_int_env(
                "AGENT_GATEWAY_MAX_EVENTS_PER_RUN", 10000, 100, 100000
            ),
        )
