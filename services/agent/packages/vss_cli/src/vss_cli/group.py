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
import inspect
from typing import TYPE_CHECKING
from typing import Any
from typing import ClassVar
from typing import final

import click
from pydantic import ValidationError

from . import config as config_mod
from . import params as params_mod
from .exits import Exit

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic import BaseModel

#: Contract version. A group built against a different major is refused at
#: load time rather than half-mounted.
API_VERSION = 1


@dataclass(frozen=True)
class Action:
    """One execution path under ``run``, with its own input model."""

    name: str
    summary: str
    Input: type[BaseModel]
    #: Services this path actually calls. Declared per action, not per group,
    #: because a group's paths rarely need the same backends -- an embedding
    #: search never contacts the CV service, so demanding it would make an
    #: otherwise-usable deployment refuse a search it could serve. The
    #: framework checks these before dispatch so every group reports a missing
    #: backend the same way.
    requires: frozenset[str] = frozenset()


@dataclass
class Context:
    """What the framework hands a verb.

    ``deployment`` is None only when nothing has been configured; a verb
    needing a backend should raise :class:`vss_cli.config.ConfigError` rather
    than guess an endpoint.
    """

    deployment: config_mod.Deployment | None = None
    output: str = "json"
    pretty: bool | None = None
    log_level: str = "WARNING"
    #: Memory tier, once it exists. Until then ``status``/``get``/``list``
    #: have nothing to read and say so plainly (exit 4).
    memory: Any = None
    #: Why the deployment failed to load, when it did. Carried so a verb can
    #: report the specific cause instead of a generic "nothing configured".
    config_error: str = ""
    #: Values from :attr:`CommandGroup.extra_params` -- flags a group declares
    #: outside its input model. Kept separate from the request so a group can
    #: route them wherever they belong (runtime config, transport, ...) rather
    #: than having them silently folded into the payload.
    extra: dict[str, Any] = dc_field(default_factory=dict)


@dataclass
class Result:
    """A verb's outcome. ``body`` is the payload; ``exit`` the process code."""

    body: Any = None
    exit: Exit = Exit.SUCCESS
    #: Populated once jobs are minted; feeds the completion marker (§7.2).
    job_id: str = ""
    extra: dict[str, Any] = dc_field(default_factory=dict)


class InvalidInput(click.ClickException):
    """A payload the action's input model rejected (exit 2).

    Carries the ``[vss] invalid input:`` prefix so a harness can distinguish a
    malformed call from a backend failure without parsing pydantic's output.
    """

    exit_code = int(Exit.INVALID_INPUT)

    def format_message(self) -> str:
        return f"[vss] invalid input: {self.message}"


def _format_validation(exc: ValidationError) -> str:
    """Render pydantic errors as ``field: reason``, comma-separated."""
    parts = []
    for error in exc.errors():
        location = ".".join(str(piece) for piece in error["loc"]) or "(payload)"
        parts.append(f"{location}: {error['msg']}")
    return "; ".join(parts)


def _require_services(action: Action, ctx: Context) -> None:
    """Fail before dispatch when the deployment lacks a service the action calls.

    Checked here rather than inside each group so the diagnostic is uniform,
    and checked per action so a deployment missing one optional service still
    serves the paths that never touch it.
    """
    if not action.requires:
        return
    if ctx.deployment is None:
        if ctx.config_error:
            raise config_mod.ConfigError(f"`{action.name}` needs a deployment: {ctx.config_error}")
        raise config_mod.ConfigError(
            f"no deployment configured, and `{action.name}` needs "
            f"{', '.join(sorted(action.requires))}. Run `vss configure --base-url <origin>` first."
        )
    missing = sorted(name for name in action.requires if not ctx.deployment.has(name))
    if missing:
        known = ", ".join(sorted(ctx.deployment.services)) or "(none)"
        raise config_mod.ConfigError(
            f"`{action.name}` needs {', '.join(missing)}, which the deployment at "
            f"{ctx.deployment.base_url} does not expose; it has: {known}. "
            f"Re-run `vss configure --base-url {ctx.deployment.base_url}` if the deployment changed."
        )


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
    #: Ignored when :attr:`actions` is non-empty.
    Input: ClassVar[type[BaseModel] | None] = None

    #: Sub-actions of ``run``. When set, ``run`` becomes a group and each
    #: action contributes one command with its own input model.
    #:
    #: This exists so a group with genuinely different execution paths does
    #: not collapse them into one command behind a mode flag. A mode flag
    #: forces every path's fields onto one surface, which then needs runtime
    #: validation to reject the combinations that make no sense
    #: ("search_mode='embed' does not accept attributes"). Separate actions
    #: make those states unrepresentable instead: the grammar refuses what
    #: validation used to catch.
    actions: ClassVar[Sequence[Action]] = ()

    #: Shapes the deriver cannot express -- mutually exclusive flags, help
    #: sections. Appended verbatim rather than smuggled through the model.
    extra_params: ClassVar[Sequence[click.Parameter]] = ()

    #: Non-job subcommands. §2 keeps ``search embed|attribute`` as
    #: "low-level non-job primitives (developer surface)": no job_id, no
    #: persistence, not part of the verb grammar.
    primitives: ClassVar[Sequence[click.Command]] = ()

    # -- the one verb a group implements -------------------------------

    @abstractmethod
    def run(self, action: str, inputs: BaseModel, ctx: Context) -> Result:
        """Do the work. Persistence and markers are the framework's job.

        ``action`` is the sub-action name, or ``""`` for a group that
        declares no :attr:`actions`.
        """

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
        if self.actions:
            group = click.Group(name="run", short_help=f"Run a {self.name} job.")
            for action in self.actions:
                group.add_command(self._action_command(action))
            return group
        if self.Input is None:
            raise TypeError(f"{type(self).__name__} must declare Input or actions")
        return self._action_command(Action(name="run", summary=f"Run a {self.name} job.", Input=self.Input))

    def _action_command(self, action: Action) -> click.Command:
        owner = self
        model = action.Input

        extra_names = {p.name for p in owner.extra_params if p.name}

        def callback(**values: Any) -> None:
            ctx = _context_from(values)
            ctx.extra = {k: v for k, v in values.items() if k in extra_names and v is not None and v != ()}
            supplied = params_mod.collect(model, values)
            payload = _merge_json_payload(values.get("json_payload"), supplied)
            try:
                inputs = model(**payload)
            except ValidationError as exc:
                # Input the model rejects is the caller's error, not a crash:
                # report it as exit 2 with the offending fields named, rather
                # than letting a pydantic traceback out as a generic exit 1.
                raise InvalidInput(_format_validation(exc)) from exc
            _require_services(action, ctx)
            try:
                result = owner.run(action.name if owner.actions else "", inputs, ctx)
            except ValidationError as exc:
                # A group's input model is a CLI-shaped subset of whatever the
                # library accepts, so the library can still reject a value that
                # passed here (a timestamp typed as a string, say). That is
                # equally the caller's error and gets the same exit 2.
                raise InvalidInput(_format_validation(exc)) from exc
            _emit(result, ctx)

        return click.Command(
            name=action.name,
            params=[
                *params_mod.options_from_model(model),
                *owner.extra_params,
                *params_mod.shared_options(),
            ],
            callback=callback,
            short_help=action.summary,
            # The input model's docstring is the long help. Keeping the two
            # together means the description of what a path does lives beside
            # the fields it accepts, rather than drifting from them.
            help=inspect.cleandoc(model.__doc__ or action.summary),
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

    The recorded deployment is the only source of endpoints. When none is
    recorded ``deployment`` is None, and :func:`_require_services` turns that
    into exit 4 naming the command that fixes it.
    """
    deployment: config_mod.Deployment | None
    config_error = ""
    try:
        deployment = config_mod.load()
    except config_mod.ConfigError as exc:
        # Keep the reason. "Not configured", "written by something else" and
        # "records no services" are different problems with different fixes,
        # and collapsing them to a bare None loses the one that says which.
        deployment = None
        config_error = str(exc)
    return Context(
        config_error=config_error,
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
