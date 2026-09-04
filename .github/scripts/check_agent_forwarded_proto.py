#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Keep the agent able to learn the scheme its caller actually used.

The agent is the one service in the path contract that mints an absolute URL
from the request itself: Starlette's trailing-slash redirect on ``/static``
builds ``Location`` out of the request scope. Everywhere TLS terminates outside
the deployment -- a Brev secure link is https on 443 forwarding plain HTTP to
the gateway -- the scope says http and the browser blocks the redirect as mixed
content. The gateway already sends ``X-Forwarded-Proto``; whether the agent
believes it is decided entirely by ``FORWARDED_ALLOW_IPS``.

That variable is easy to lose and easy to get subtly wrong, and both failures
are invisible on a single-host http deployment where the two origins coincide:

  * Delete it and uvicorn falls back to trusting only 127.0.0.1. HAProxy
    reaches the agent from the bridge, so the header is discarded and the
    redirect goes back to http. Nothing logs a warning.
  * Set it to a Service or container name -- ``vss-haproxy-ingress`` reads like
    the tightest possible value -- and uvicorn files it as a literal, compares
    it against the peer's numeric address, never matches, and behaves exactly
    as if the variable were absent.
  * Narrow it to a subnet no bridge or pod address can fall in and the same
    thing happens.

So this lint does not merely check the variable is present. It parses the value
the way uvicorn does and asserts that a peer address the gateway can actually
have is inside it. A value that cannot trust any private peer cannot fix the
redirect, and is reported as if it were missing.
"""

from __future__ import annotations

import argparse
import ipaddress
import re
import sys
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

VARIABLE = "FORWARDED_ALLOW_IPS"

# Addresses a peer in front of the agent realistically has. The Docker bridge
# subnet is assigned from the daemon's address pools and a Kubernetes pod CIDR
# is per-cluster, so neither is a fixed number the lint could assert against.
# What it can assert is that the configured value trusts at least one of them,
# which is the property the fix depends on.
GATEWAY_PEERS = (
    "172.17.0.2",  # Docker default bridge
    "172.18.0.3",  # next pool Docker hands out, and this deployment's own
    "192.168.0.2",  # the daemon's other builtin pool
    "10.42.0.7",  # a typical Kubernetes pod CIDR
)

COMPOSE_SERVICE = re.compile(r"^(?P<indent> +)vss-agent:\s*$")
COMPOSE_ASSIGNMENT = re.compile(rf"^\s*{VARIABLE}:\s*(?P<value>.*?)\s*$")
# Compose renders ${NAME:-default}; the lint judges the default, since that is
# what every deployment that sets nothing gets.
COMPOSE_DEFAULT = re.compile(r"^\$\{[A-Z0-9_]+:-(?P<default>.*)\}$")

HELM_AGENT_MARKER = "- name: VSS_AGENT_PORT"
HELM_ENTRY = re.compile(
    rf"^\s*- name: {VARIABLE}\s*\n\s+value: (?P<value>.*?)\s*$", re.MULTILINE
)
HELM_DEFAULT = re.compile(r"\|\s*default\s+\"(?P<default>[^\"]*)\"")


def agent_blocks(text: str) -> list[tuple[int, list[tuple[int, str]]]]:
    """Return each ``vss-agent:`` service block as (header line, body lines).

    Line numbers are 1-based so diagnostics point at something editable.
    """
    lines = text.splitlines()
    blocks: list[tuple[int, list[tuple[int, str]]]] = []
    for index, line in enumerate(lines):
        service = COMPOSE_SERVICE.match(line)
        if not service:
            continue
        indent = len(service.group("indent"))
        body: list[tuple[int, str]] = []
        for offset, candidate in enumerate(lines[index + 1 :], start=index + 2):
            # The block runs to the next key at or above the service's indent.
            if candidate.strip() and not candidate.startswith(" " * (indent + 1)):
                break
            body.append((offset, candidate))
        blocks.append((index + 1, body))
    return blocks


def defines_the_agent(text: str) -> bool:
    """Is this the agent's definition, or an overlay that only patches it?

    An overlay names the service to reset one key and carries no image, so it
    is not where the environment belongs and must not be required to repeat it.
    """
    return any(
        any(line.strip().startswith("image:") for _, line in body)
        for _, body in agent_blocks(text)
    )


def default_paths() -> list[Path]:
    """Return every file that declares the agent's runtime environment.

    Discovered by content rather than by a fixed list, so moving or adding an
    agent definition brings it into scope instead of quietly leaving it out.
    """
    paths: list[Path] = []

    compose_root = ROOT / "deploy/docker"
    if compose_root.is_dir():
        paths.extend(
            path
            for path in compose_root.rglob("compose*.yml")
            if defines_the_agent(path.read_text())
        )

    helm_root = ROOT / "deploy/helm"
    if helm_root.is_dir():
        paths.extend(
            path
            for path in helm_root.rglob("values.yaml")
            if HELM_AGENT_MARKER in path.read_text()
        )

    return sorted(paths)


def trusts_a_gateway_peer(value: str) -> bool:
    """Parse ``value`` as uvicorn does and report whether a real peer matches.

    Mirrors ``uvicorn.middleware.proxy_headers._TrustedHosts``: ``*`` trusts
    everything, entries containing ``/`` are networks, the rest are addresses,
    and anything that is neither is kept as a literal compared against the
    peer's address string -- which is why a hostname never matches.
    """
    entries = [item.strip() for item in value.split(",") if item.strip()]
    if not entries:
        return False
    if entries == ["*"]:
        return True

    networks = []
    hosts = set()
    for entry in entries:
        try:
            if "/" in entry:
                networks.append(ipaddress.ip_network(entry, strict=False))
            else:
                hosts.add(ipaddress.ip_address(entry))
        except ValueError:
            # A literal. uvicorn keeps it, and it can never equal an address.
            continue

    for peer in GATEWAY_PEERS:
        address = ipaddress.ip_address(peer)
        if address in hosts or any(address in network for network in networks):
            return True
    return False


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _effective_compose_value(raw: str) -> str:
    """Return what Compose renders when the deployment overrides nothing."""
    value = _unquote(raw)
    match = COMPOSE_DEFAULT.match(value)
    return match.group("default") if match else value


def _scan_compose(path: Path, display: Path) -> list[str]:
    text = path.read_text()
    if not defines_the_agent(text):
        return []

    failures: list[str] = []
    for header, body in agent_blocks(text):
        if not any(line.strip().startswith("image:") for _, line in body):
            continue

        found = [
            (number, match)
            for number, line in body
            if (match := COMPOSE_ASSIGNMENT.match(line))
        ]
        if not found:
            failures.append(
                f"{display}:{header}: the vss-agent service does not set "
                f"{VARIABLE}; uvicorn then trusts only 127.0.0.1, discards the "
                "X-Forwarded-Proto the gateway sends, and mints the /static "
                "redirect on http -- mixed content wherever TLS terminates "
                "outside the deployment"
            )
            continue

        for number, match in found:
            value = _effective_compose_value(match.group("value"))
            if not trusts_a_gateway_peer(value):
                failures.append(
                    f"{display}:{number}: {VARIABLE}={value!r} trusts no address "
                    "the gateway can reach the agent from, so X-Forwarded-Proto "
                    "is discarded exactly as if it were unset. Use IP addresses "
                    "or CIDR networks -- a container or Service name is accepted "
                    "and then never matches the peer's address"
                )

    return failures


def _scan_helm(path: Path, display: Path) -> list[str]:
    text = path.read_text()
    entry = HELM_ENTRY.search(text)
    if not entry:
        return [
            f"{display}: the agent env list does not set {VARIABLE}; the chart "
            "then leaves uvicorn trusting only 127.0.0.1 and the /static "
            "redirect stays on http behind a TLS terminator (Compose parity: "
            "deploy/docker/services/agent/compose.yml)"
        ]

    raw = _unquote(entry.group("value"))
    default = HELM_DEFAULT.search(raw)
    # A templated value is only judged on the default it falls back to; what an
    # operator supplies in values.yaml is theirs to scope.
    value = default.group("default") if default else raw
    if "{{" in value:
        return []
    if not trusts_a_gateway_peer(value):
        line = text[: entry.start()].count("\n") + 1
        return [
            f"{display}:{line}: {VARIABLE}={value!r} trusts no address a pod "
            "can reach the agent from, so X-Forwarded-Proto is discarded "
            "exactly as if it were unset"
        ]
    return []


def scan_paths(paths: Iterable[Path]) -> list[str]:
    """Return actionable diagnostics for an agent that cannot learn the scheme."""
    failures: list[str] = []
    for path in paths:
        try:
            display = path.relative_to(ROOT)
        except ValueError:
            display = path
        if path.name == "values.yaml":
            failures.extend(_scan_helm(path, display))
        else:
            failures.extend(_scan_compose(path, display))
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

    print(f"Agent forwarded-scheme lint passed ({len(paths)} files).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
