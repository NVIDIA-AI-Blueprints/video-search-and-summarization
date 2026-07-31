# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""``vss configure`` -- resolve a deployment once, from one origin (SDD §4.0).

C1: take a base URL and discover which services the ingress exposes.
C2: record the result in ``~/.vss/config.json``.
C3: report reachability while doing it.

Not a command group: it has no job lifecycle, so ``run``/``status``/``get``/
``list`` would be meaningless. It is the bootstrap that makes the groups
usable, and it is the only command that works without an existing config.

Discovery is a probe, not a guess. Each known route is requested and recorded
only if the origin answers; a route the deployment does not expose is absent
from the config rather than present-but-broken, so the failure surfaces at
configure time with a URL attached instead of much later as a connection
error inside a search.
"""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
import json
from typing import Any

import click

from . import config as config_mod
from .exits import Exit

#: A route counts as present only if its probe path answers. 404 means the
#: ingress has no such mapping -- verified against a live deployment, where an
#: unrouted ``/elasticsearch`` and a routed ``/api`` both answered 404 at their
#: roots while ``/elasticsearch/_cluster/health`` answered 404 and
#: ``/vst/api/v1/sensor/version`` answered 200. Auth challenges (401/403) do
#: prove a mapping, so they count as present.
_PRESENT_STATUSES = frozenset({200, 201, 204, 400, 401, 403, 405, 422})

_PROBE_TIMEOUT_SECONDS = 5.0


def _probe(base_url: str, probe_path: str, timeout: float) -> tuple[bool, str]:
    """Return (routed, detail) for one ingress route."""
    import httpx

    url = f"{base_url.rstrip('/')}{probe_path}"
    try:
        response = httpx.get(url, timeout=timeout, follow_redirects=True)
    except httpx.HTTPError as exc:
        return False, f"{type(exc).__name__}: {exc}"
    routed = response.status_code in _PRESENT_STATUSES
    return routed, f"HTTP {response.status_code}"


@click.group(name="configure", invoke_without_command=True)
@click.option("--base-url", help="Deployment origin, e.g. http://10.0.0.1:7777")
@click.option(
    "--timeout",
    type=click.FloatRange(0.1, 120.0),
    default=_PROBE_TIMEOUT_SECONDS,
    show_default=True,
    help="Per-route probe timeout in seconds.",
)
@click.option("--embed-model", default="", help="Embedding model the deployment serves.")
@click.pass_context
def configure(ctx: click.Context, base_url: str | None, timeout: float, embed_model: str) -> None:
    """Resolve a VSS deployment from one origin and record it."""
    if ctx.invoked_subcommand is not None:
        return
    if not base_url:
        raise click.UsageError("--base-url is required (or use `vss configure show`)")

    endpoints: dict[str, str] = {}
    click.echo(f"probing {base_url}", err=True)
    for service, (mount, probe_path) in config_mod.INGRESS_ROUTES.items():
        ok, detail = _probe(base_url, probe_path, timeout)
        click.echo(f"  {service:<14} {mount:<16} {'routed' if ok else 'absent':<7} {detail}", err=True)
        if ok:
            endpoints[service] = f"{base_url.rstrip('/')}{mount}"

    if not endpoints:
        raise click.ClickException(
            f"{base_url} exposed none of the expected routes "
            f"({', '.join(m for m, _ in config_mod.INGRESS_ROUTES.values())}). "
            f"Check the origin and that the ingress is up."
        )

    deployment = config_mod.Deployment(
        base_url=base_url.rstrip("/"),
        endpoints=endpoints,
        embed_model=embed_model,
        written_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )
    path = config_mod.save(deployment)
    click.echo(f"wrote {path} ({len(endpoints)}/{len(config_mod.INGRESS_ROUTES)} routes)", err=True)


@configure.command("show")
def show() -> None:
    """Print the recorded deployment."""
    try:
        deployment = config_mod.load()
    except config_mod.ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(deployment.to_json(), indent=2))


@configure.command("check")
def check() -> None:
    """Re-probe the recorded deployment and report drift (C3).

    A config records what was true when it was written. This is the cheap way
    to find out that it no longer is -- the failure mode a cached config
    introduces, and the reason the file carries ``written_at``.
    """
    try:
        deployment = config_mod.load()
    except config_mod.ConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"configured {deployment.written_at or 'unknown'} against {deployment.base_url}", err=True)
    stale = False
    for service, url in sorted(deployment.endpoints.items()):
        route = config_mod.INGRESS_ROUTES.get(service)
        if route is None:
            continue
        ok, detail = _probe(deployment.base_url, route[1], _PROBE_TIMEOUT_SECONDS)
        click.echo(f"  {service:<14} {'ok' if ok else 'UNREACHABLE':<12} {url}  {detail}")
        stale = stale or not ok
    if stale:
        raise SystemExit(int(Exit.BACKEND_UNREACHABLE))


class _ConfigureGroup:
    """Plugin spec so ``configure`` mounts through the published contract."""

    api_version = 1
    name = "configure"
    summary = "Resolve and record a VSS deployment"

    def cli(self) -> Any:
        return configure


CONFIGURE = _ConfigureGroup()

__all__ = ["CONFIGURE", "configure"]
