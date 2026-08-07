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


def _describe(base_url: str, route: config_mod.ServiceRoute, timeout: float) -> list[str]:
    """Ask a service what it holds. Empty when it offers no introspection.

    The point of a descriptive config: model ids and index names are facts the
    backend already knows, so they are read from it rather than typed by a
    caller and allowed to drift.
    """
    import httpx

    if not route.describe:
        return []
    try:
        response = httpx.get(f"{base_url.rstrip('/')}{route.describe}", timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return []

    # OpenAI-style model list: {"data": [{"id": ...}]}
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return [str(item.get("id")) for item in payload["data"] if isinstance(item, dict) and item.get("id")]
    # Elasticsearch _cat: [{"index": ...}]
    if isinstance(payload, list):
        names = [str(item.get("index")) for item in payload if isinstance(item, dict) and item.get("index")]
        return sorted(n for n in names if not n.startswith("."))
    return []


@click.group(name="configure", invoke_without_command=True)
@click.option("--base-url", help="Deployment origin, e.g. http://10.0.0.1:7777")
@click.option(
    "--timeout",
    type=click.FloatRange(0.1, 120.0),
    default=_PROBE_TIMEOUT_SECONDS,
    show_default=True,
    help="Per-route probe timeout in seconds.",
)
@click.pass_context
def configure(ctx: click.Context, base_url: str | None, timeout: float) -> None:
    """Resolve a VSS deployment from one origin and record it."""
    if ctx.invoked_subcommand is not None:
        return
    if not base_url:
        raise click.UsageError("--base-url is required (or use `vss configure show`)")

    services: dict[str, config_mod.Service] = {}
    click.echo(f"probing {base_url}", err=True)
    for name, route in config_mod.INGRESS_SERVICES.items():
        ok, detail = _probe(base_url, route.probe, timeout)
        described: list[str] = []
        if ok:
            described = _describe(base_url, route, timeout)
            services[name] = config_mod.Service(
                url=f"{base_url.rstrip('/')}{route.mount}",
                models=described if route.describes == "models" else [],
                indices=described if route.describes == "indices" else [],
            )
        note = f"{len(described)} {route.describes}" if described else ""
        click.echo(
            f"  {name:<14} {route.mount:<16} {'routed' if ok else 'absent':<7} {detail:<10} {note}",
            err=True,
        )

    if not services:
        raise click.ClickException(
            f"{base_url} exposed none of the expected routes "
            f"({', '.join(r.mount for r in config_mod.INGRESS_SERVICES.values())}). "
            f"Check the origin and that the ingress is up."
        )

    deployment = config_mod.Deployment(
        base_url=base_url.rstrip("/"),
        services=services,
        written_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )
    path = config_mod.save(deployment)
    click.echo(f"wrote {path} ({len(services)}/{len(config_mod.INGRESS_SERVICES)} services)", err=True)

    # What this file records about Elasticsearch is a snapshot, and indices are
    # created by ingestion rather than by deployment. Configuring a freshly
    # deployed stack therefore records zero indices, and the record stays empty
    # until someone re-runs this -- while search still appears to work, because
    # the runtime falls back to its built-in index names. Say so, rather than
    # leaving a caller to discover it when a readiness check reads no indices
    # out of a config that looks fine.
    es = services.get("elasticsearch")
    if es is not None and not [i for i in es.indices if i.startswith("mdx-")]:
        click.echo(
            "note: elasticsearch is routed but holds no mdx-* search indices yet. "
            "They are created by ingestion, so re-run this command after ingesting "
            "video and before searching, or the recorded index list stays empty.",
            err=True,
        )


@configure.command("show")
def show() -> None:
    """Print the recorded deployment (and memory preferences when present)."""
    try:
        deployment = config_mod.load()
    except config_mod.ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    payload = deployment.to_json()
    memory = config_mod.load_memory_config()
    if memory.harness_sink.enabled or memory.harness_sink.write_memory_notes_default:
        payload["memory"] = memory.to_json()
    else:
        # Still surface an explicit memory section when one was saved.
        try:
            raw = config_mod._read_raw()
        except config_mod.ConfigError:
            raw = {}
        if isinstance(raw.get("memory"), dict):
            payload["memory"] = raw["memory"]
    click.echo(json.dumps(payload, indent=2))


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
    for name, service in sorted(deployment.services.items()):
        route = config_mod.INGRESS_SERVICES.get(name)
        if route is None:
            continue
        ok, detail = _probe(deployment.base_url, route.probe, _PROBE_TIMEOUT_SECONDS)
        click.echo(f"  {name:<14} {'ok' if ok else 'UNREACHABLE':<12} {service.url}  {detail}")
        stale = stale or not ok
    if stale:
        raise SystemExit(int(Exit.BACKEND_UNREACHABLE))


@configure.group("memory", invoke_without_command=True)
@click.option(
    "--harness",
    type=click.Choice(["openclaw"]),
    default=None,
    help="Harness that owns Markdown memory after the initial VSS write.",
)
@click.option(
    "--plugin",
    default=None,
    help="Harness memory plugin (currently memory-core).",
)
@click.option(
    "--workspace",
    default=None,
    help="Harness workspace root (e.g. ~/.openclaw/workspace).",
)
@click.option(
    "--enable-memory-notes/--disable-memory-notes",
    default=None,
    help="Enable or disable the harness Markdown sink.",
)
@click.option(
    "--write-memory-notes-default/--no-write-memory-notes-default",
    default=None,
    help="Default for job commands when --write-memory-note is omitted.",
)
@click.option(
    "--note-path-template",
    default=None,
    help="Relative path template under the workspace (default memory/{date}-vss.md).",
)
@click.option(
    "--timezone",
    default=None,
    help="Timezone used to resolve {date} in the note path template (default UTC).",
)
@click.pass_context
def configure_memory(
    ctx: click.Context,
    harness: str | None,
    plugin: str | None,
    workspace: str | None,
    enable_memory_notes: bool | None,
    write_memory_notes_default: bool | None,
    note_path_template: str | None,
    timezone: str | None,
) -> None:
    """Configure the harness-native Markdown memory sink.

    Elasticsearch remains the authoritative structured store. VSS only writes
    the initial ``memory/*.md`` addendum; OpenClaw ``memory-core`` owns
    indexing, dreaming, retention, and promotion afterward. VSS never writes
    ``MEMORY.md``.
    """
    if ctx.invoked_subcommand is not None:
        return
    if harness is None and plugin is None and workspace is None and enable_memory_notes is None:
        raise click.UsageError(
            "pass sink options (e.g. --harness openclaw --plugin memory-core "
            "--workspace ~/.openclaw/workspace --enable-memory-notes) or use `vss configure memory show`"
        )

    from vss_core.memory.notes import DEFAULT_NOTE_PATH_TEMPLATE
    from vss_core.memory.notes import is_supported_harness_plugin

    current = config_mod.load_memory_config()
    sink = current.harness_sink
    next_harness = harness or sink.harness
    next_plugin = plugin or sink.plugin
    if not is_supported_harness_plugin(next_harness, next_plugin):
        raise click.ClickException(
            f"unsupported harness/plugin combination {next_harness!r}/{next_plugin!r}; "
            f"this release supports openclaw/memory-core"
        )
    next_workspace = workspace if workspace is not None else sink.workspace
    if not str(next_workspace).strip():
        raise click.ClickException("--workspace is required")
    next_enabled = sink.enabled if enable_memory_notes is None else enable_memory_notes
    next_default = (
        sink.write_memory_notes_default if write_memory_notes_default is None else write_memory_notes_default
    )
    if next_enabled and write_memory_notes_default is None and enable_memory_notes is True:
        # Enabling the sink implies job commands may opt into notes by default
        # only when the caller also sets the default flag; keep prior default.
        next_default = sink.write_memory_notes_default

    updated = config_mod.MemoryConfig(
        structured_store_provider=current.structured_store_provider,
        structured_store_enabled=current.structured_store_enabled,
        harness_sink=config_mod.HarnessMemorySinkConfig(
            enabled=next_enabled,
            harness=next_harness,
            plugin=next_plugin,
            workspace=next_workspace,
            note_path_template=note_path_template or sink.note_path_template or DEFAULT_NOTE_PATH_TEMPLATE,
            write_memory_notes_default=next_default,
            timezone=timezone or sink.timezone or "UTC",
        ),
    )
    path = config_mod.save_memory_config(updated)
    click.echo(f"wrote memory config to {path}", err=True)
    click.echo(json.dumps(updated.to_json(), indent=2))


@configure_memory.command("show")
def configure_memory_show() -> None:
    """Print the effective harness memory configuration."""
    memory = config_mod.load_memory_config()
    click.echo(json.dumps({"memory": memory.to_json()}, indent=2))


class _ConfigureGroup:
    """Plugin spec so ``configure`` mounts through the published contract."""

    api_version = 1
    name = "configure"
    summary = "Resolve and record a VSS deployment"

    def cli(self) -> Any:
        return configure


CONFIGURE = _ConfigureGroup()

__all__ = ["CONFIGURE", "configure"]
