#!/usr/bin/env -S uv run --quiet --script
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Apply host-CLI ownership rules and derive analytics readiness probes."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

VSS_AGENT = "vss-agent"
VSS_VA_MCP = "vss-va-mcp"


@dataclass(frozen=True)
class ReadinessTarget:
    service: str
    url: str


ANALYTICS_READINESS_TARGETS = (
    ReadinessTarget(
        "vss-video-analytics-api",
        "http://${HOST_IP}:${VIDEO_ANALYTICS_API_HOST_PORT:-8081}/livez",
    ),
    ReadinessTarget(
        "alert-bridge",
        "http://${HOST_IP}:${ALERT_BRIDGE_HOST_PORT:-9080}/health",
    ),
    ReadinessTarget(
        VSS_VA_MCP,
        "http://${HOST_IP}:${VSS_VA_MCP_HOST_PORT:-9901}/health",
    ),
)


def resolve_service_profiles(
    foundation_profiles: Iterable[str],
    requested_profiles: Iterable[str] = (),
    *,
    host_cli: bool,
) -> tuple[str, ...]:
    """Return an ordered profile set after applying explicit ownership rules."""
    requested = tuple(requested_profiles)
    profiles = dict.fromkeys((*foundation_profiles, *requested))

    if host_cli:
        explicitly_requested = set(requested)
        for profile in (VSS_AGENT, VSS_VA_MCP):
            if profile not in explicitly_requested:
                profiles.pop(profile, None)

    return tuple(profiles)


def analytics_readiness_targets(
    resolved_services: Iterable[str],
) -> tuple[ReadinessTarget, ...]:
    """Return only analytics probes whose owning service resolved."""
    selected = set(resolved_services)
    return tuple(
        target for target in ANALYTICS_READINESS_TARGETS if target.service in selected
    )
