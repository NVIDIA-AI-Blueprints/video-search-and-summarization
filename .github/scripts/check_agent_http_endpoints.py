#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Reject Docker-only HTTP endpoints from agent-facing deployment defaults."""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENV_SUFFIX = ".env"
YAML_SUFFIXES = {".yaml", ".yml"}
# One entry per backend the agent reaches through a gateway mount, so the set
# is derived from the route contract rather than from the hosts that happened
# to be wrong when this lint was written. The nine gateway-derived agent
# variables in deploy/docker/README.md -- VIDEO_ANALYSIS_MCP_URL,
# VST_INTERNAL_URL, ELASTIC_SEARCH_ENDPOINT, COSMOS_EMBED_ENDPOINT,
# RTVI_CV_ENDPOINT, RTVI_VLM_BASE_URL, ALERT_BRIDGE_URL, LVS_BACKEND_URL and
# PHOENIX_ENDPOINT -- name these nine Compose service hosts, and the default
# for each is the *_SERVICE_HOST default in services/infra/haproxy/compose.yml.
# Adding a gateway mount that the agent calls means adding its host here.
#
# The last two are the gateway's own bridge-only identities rather than
# backends behind it, and they are here because they are the regression this
# lint would otherwise miss entirely: `http://vss-haproxy-ingress:7777` and
# `http://vss.local:7777` reach the right place from a colocated agent and
# resolve nowhere from a remote one, so they pass every single-host test and
# break exactly the deployment FR-03 and FR-35 exist for. The agent must reach
# the gateway through VSS_GATEWAY_ORIGIN, whose own default spells the alias as
# `${VSS_GATEWAY_HOST:-vss.local}` -- an expansion, which this regex does not
# match, and not the literal these two forbid. remote-agent.env.example:11-28
# says the same thing to the operator: vss.local "must never be used here".
DOCKER_ONLY_HTTP_HOSTS = {
    "alert-bridge",
    "elasticsearch",
    "lvs-server",
    "phoenix",
    "rtvi-embed",
    "rtvi-vlm",
    "vss-rtvi-cv",
    "vss-va-mcp",
    "vst-ingress",
    "vss-haproxy-ingress",
    "vss.local",
}
# Escaped: `vss.local` carries a dot, and an unescaped dot is a wildcard that
# would also match `vss-local` and report a host nobody wrote.
HTTP_URL = re.compile(
    r"https?://(?P<host>"
    + "|".join(re.escape(host) for host in sorted(DOCKER_ONLY_HTTP_HOSTS))
    + r")(?=[:/\"'\s]|$)"
)
# `NAME=` in an env file, `NAME:` in YAML. Used only to name the setting a
# forbidden URL is attached to, so an exemption below can be scoped to one
# variable in one file instead of blinding the whole file.
ASSIGNMENT = re.compile(r"^\s*(?:-\s*)?(?:export\s+)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*[:=]")

# Docker-only values that are correct because they are a service's own
# in-network identity, not an endpoint the agent is handed. Each is a
# (path relative to the repository root, variable) pair rather than a whole
# file, so the rest of the file stays guarded.
#
#   vst.env / VST_INTERNAL_URL   VST's own address, consumed by alert-bridge
#                                on the bridge. The agent never sees it:
#                                services/agent/compose.yml overrides
#                                VST_INTERNAL_URL with the gateway origin.
#   overrides.env /              Read only by the rtvi-vlm service itself, as
#   RTVI_VLM_ENDPOINT            VIA_VLM_ENDPOINT. Sending it through the
#                                gateway would make RT-VLM call itself through
#                                HAProxy to reach its own port.
IN_NETWORK_SELF_ADDRESSES = {
    ("deploy/docker/services/vios/vst.env", "VST_INTERNAL_URL"),
    (
        "deploy/docker/developer-profiles/dev-profile-alerts/overrides.env",
        "RTVI_VLM_ENDPOINT",
    ),
    (
        "deploy/docker/industry-profiles/warehouse-operations/overrides.env",
        "RTVI_VLM_ENDPOINT",
    ),
}


def is_env_file(path: Path) -> bool:
    """Match env files including a bare ``.env``, whose ``Path.suffix`` is empty."""
    return path.suffix == ENV_SUFFIX or path.name == ENV_SUFFIX


def default_paths() -> list[Path]:
    """Return the agent-facing files governed by the gateway contract."""
    # services/ is scanned whole rather than only services/agent: the shared
    # per-service env files are merged into the agent's Compose environment by
    # services/compose.yml, so a Docker-only default written in alert.env or
    # rtvi.env is an agent default no matter which directory it lives in.
    roots = (
        ROOT / "deploy/docker/services",
        ROOT / "deploy/docker/developer-profiles",
        ROOT / "deploy/docker/industry-profiles",
    )
    paths: set[Path] = set()
    for root in roots:
        for path in root.rglob("*"):
            if not (is_env_file(path) or path.suffix in YAML_SUFFIXES):
                continue
            relative_parts = path.relative_to(root).parts
            # services/<name>/<file>.env and <profile>/overrides.env alike.
            is_shared_env = is_env_file(path) and len(relative_parts) == 2
            in_agent_service = root.name == "services" and relative_parts[0] == "agent"
            if in_agent_service or "vss-agent" in path.parts or is_shared_env:
                paths.add(path)
    return sorted(paths)


def scan_paths(paths: Iterable[Path]) -> list[str]:
    """Return actionable diagnostics for forbidden endpoint defaults."""
    failures: list[str] = []
    for path in paths:
        try:
            display_path = path.relative_to(ROOT)
        except ValueError:
            display_path = path
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            # A comment is prose, not a default, and several of these files
            # explain the contract by quoting the hostname it forbids.
            if line.lstrip().startswith("#"):
                continue
            match = HTTP_URL.search(line)
            if not match:
                continue
            assignment = ASSIGNMENT.match(line)
            name = assignment.group("name") if assignment else None
            if name and (display_path.as_posix(), name) in IN_NETWORK_SELF_ADDRESSES:
                continue
            failures.append(
                f"{display_path}:{line_number}: Docker-only HTTP host "
                f"{match.group('host')!r}; use a gateway-derived environment variable"
            )
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path)
    args = parser.parse_args(argv)

    paths = args.paths or default_paths()
    failures = scan_paths(paths)
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1

    print(f"Agent HTTP endpoint lint passed ({len(paths)} files).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
