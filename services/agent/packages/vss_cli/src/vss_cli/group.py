# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The command-group base class: the framework owns the verbs.

Every group answers the same four verbs (SDD §3.1)::

    vss <group> run       synchronous; returns only when the result is final
    vss <group> status    reconcile a record memory still marks pending
    vss <group> get       fetch a completed record by job_id
    vss <group> list      recent jobs, including in-flight

A group implements exactly one of them. §6.2 makes ``status``/``get``/``list``
pure reads against the memory index -- "get on a completed job, list, and
terminal status never touch a backend" -- so a group has nothing to contribute
to them and inherits the framework's. That is the whole reason this is an ABC
rather than the Protocol it replaces: a Protocol can state a shape, but it
cannot hand down an implementation.

The cost is that a plugin now imports ``vss_cli``, so plugin and CLI can skew.
:data:`API_VERSION` is the guard, checked at load time by
:func:`vss_cli.plugins.load`.

There is deliberately no ``submit`` verb. Fire-and-forget belongs to the
harness (UM-4, Hook 1A): it backgrounds ``run`` and ``notifyOnExit`` delivers
the completion marker. A ``submit`` verb would push the harness back into
model-driven polling, which is the pattern the hook design exists to remove.
"""

from __future__ import annotations

from abc import ABC
from abc import abstractmethod
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import TYPE_CHECKING
from typing import Any
from typing import ClassVar
from typing import final

import click

from . import config as config_mod
from . import params as params_mod
from .exits import Exit

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic import BaseModel

#: Contract version. A group built against a different major is refused at
#: load time rather than half-mounted.
API_VERSION = 1


@dataclass
class Context:
    """What the framework hands a verb.

    ``deployment`` is None only when nothing is configured and no
    ``--base-url`` was supplied; a verb needing a backend should raise
    :class:`vss_cli.config.ConfigError` rather than guess an endpoint.
    """

    deployment: config_mod.Deployment | None = None
    output: str = "json"
    pretty: bool | None = None
    log_level: str = "WARNING"
    #: Memory tier, once it exists. Until then ``status``/``get``/``list``
    #: have nothing to read and say so plainly (exit 4).
    memory: Any = None


@dataclass
class Result:
    """A verb's outcome. ``body`` is the payload; ``exit`` the process code."""

    body: Any = None
    exit: Exit = Exit.SUCCESS
    #: Populated once jobs are minted; feeds the completion marker (§7.2).
    job_id: str = ""
    extra: dict[str, Any] = dc_field(default_factory=dict)


class MemoryUnavailable(click.ClickException):
    """Raised by the inherited read verbs until the memory tier lands.

    Deliberately explicit. ``status``/``get``/``list`` are memory reads by
    definition (§6.2), and ``vss_core`` ships no memory module yet, so they
    cannot work. Failing with a named cause beats three verbs that appear to
    work and silently return nothing.
    """

    exit_code = int(Exit.CONFIGURATION)

    def __init__(self, verb: str) -> None:
        super().__init__(
            f"`{verb}` reads the unified memory index, which this build does not ship yet "
            f"(vss_core has no memory module). Only `run` is available on this deployment."
        )


class CommandGroup(ABC):
    """Base class for a ``vss`` command group."""

    api_version: ClassVar[int] = API_VERSION

    #: Group name as it appears in ``vss <name> ...``.
    name: ClassVar[str]
    #: One-line help. Mirrors the ``vss.command_summaries`` entry point, which
    #: is what ``vss --help`` reads without importing anything.
    summary: ClassVar[str]

    #: Pydantic model for ``run``. Its fields become the flags, its schema
    #: becomes the MCP tool input, and an instance becomes ``job.request``.
    Input: ClassVar[type[BaseModel]]

    #: Shapes the deriver cannot express -- mutually exclusive flags, help
    #: sections. Appended verbatim rather than smuggled through the model.
    extra_params: ClassVar[Sequence[click.Parameter]] = ()

    #: Non-job subcommands. §2 keeps ``search embed|attribute`` as
    #: "low-level non-job primitives (developer surface)": no job_id, no
    #: persistence, not part of the verb grammar.
    primitives: ClassVar[Sequence[click.Command]] = ()

    # -- the one verb a group implements -------------------------------

    @abstractmethod
    def run(self, inputs: BaseModel, ctx: Context) -> Result:
        """Do the work. Persistence and markers are the framework's job."""

    # -- framework-provided reads (§6.2) --------------------------------

    def status(self, job_id: str, ctx: Context) -> Result:
        if ctx.memory is None:
            raise MemoryUnavailable("status")
        return Result(body=ctx.memory.status(self.name, job_id))

    def get(self, job_id: str, ctx: Context) -> Result:
        if ctx.memory is None:
            raise MemoryUnavailable("get")
        return Result(body=ctx.memory.get(self.name, job_id))

    def list(self, filters: dict[str, Any], ctx: Context) -> Result:
        if ctx.memory is None:
            raise MemoryUnavailable("list")
        return Result(body=ctx.memory.query(self.name, filters))

    # -- CLI construction ------------------------------------------------

    @final
    def cli(self) -> click.Group:
        """Build the Click tree. Not overridable -- the grammar is fixed."""
        group = click.Group(name=self.name, help=self.__doc__ or self.summary, short_help=self.summary)
        group.add_command(self._run_command())
        group.add_command(self._handle_command("status", self.status))
        group.add_command(self._handle_command("get", self.get))
        group.add_command(self._list_command())
        for primitive in self.primitives:
            group.add_command(primitive)
        return group

    def _run_command(self) -> click.Command:
        derived = params_mod.options_from_model(self.Input)
        shared = list(params_mod.shared_options())
        owner = self

        def callback(**values: Any) -> None:
            ctx = _context_from(values)
            supplied = params_mod.collect(owner.Input, values)
            payload = _merge_json_payload(values.get("json_payload"), supplied)
            inputs = owner.Input(**payload)
            _emit(owner.run(inputs, ctx), ctx)

        return click.Command(
            name="run",
            params=[*derived, *owner.extra_params, *shared],
            callback=callback,
            short_help=f"Run a {self.name} job.",
        )

    def _handle_command(self, verb: str, fn: Any) -> click.Command:
        owner = self

        def callback(**values: Any) -> None:
            ctx = _context_from(values)
            _emit(fn(values["job_id"], ctx), ctx)

        return click.Command(
            name=verb,
            params=[click.Option(["--job-id"], required=True), *params_mod.shared_options()],
            callback=callback,
            short_help=f"{verb.capitalize()} a {owner.name} job by id.",
        )

    def _list_command(self) -> click.Command:
        owner = self
        filters = (
            click.Option(["--since"], help="Only jobs after this time (ISO-8601 or duration)."),
            click.Option(["--sensor-id"], help="Restrict to one sensor."),
            click.Option(["--status"], help="Restrict to one job status."),
        )

        def callback(**values: Any) -> None:
            ctx = _context_from(values)
            selected = {k: values[k] for k in ("since", "sensor_id", "status") if values.get(k)}
            _emit(owner.list(selected, ctx), ctx)

        return click.Command(
            name="list",
            params=[*filters, *params_mod.shared_options()],
            callback=callback,
            short_help=f"List recent {owner.name} jobs, including in-flight.",
        )


# -- helpers ------------------------------------------------------------


def _context_from(values: dict[str, Any]) -> Context:
    """Assemble a Context from the shared flags, resolving the deployment.

    ``--base-url`` overrides the configured deployment for one call. Absent
    both, ``deployment`` is None and a verb that needs a backend raises.
    """
    base_url = values.get("base_url")
    deployment: config_mod.Deployment | None
    if base_url:
        deployment = config_mod.Deployment(base_url=base_url)
    else:
        try:
            deployment = config_mod.load()
        except config_mod.ConfigError:
            deployment = None
    return Context(
        deployment=deployment,
        output=values.get("output") or "json",
        pretty=values.get("pretty"),
        log_level=values.get("log_level") or "WARNING",
    )


def _merge_json_payload(raw: str | None, supplied: dict[str, Any]) -> dict[str, Any]:
    """``--json`` supplies the base; explicit flags override it."""
    if not raw:
        return supplied
    import json

    try:
        base = json.loads(raw)
    except ValueError as exc:
        raise click.BadParameter(f"--json is not valid JSON: {exc}") from exc
    if not isinstance(base, dict):
        raise click.BadParameter("--json must be a JSON object")
    base.update(supplied)
    return base


def _emit(result: Result, ctx: Context) -> None:
    """Render a Result and carry its exit code out through Click."""
    import json

    if result.body is not None:
        pretty = bool(ctx.pretty)
        text = json.dumps(result.body, indent=2 if pretty else None, default=str)
        click.echo(text)
    if result.exit != Exit.SUCCESS:
        raise SystemExit(int(result.exit))
