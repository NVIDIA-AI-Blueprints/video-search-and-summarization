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
from . import memory as memory_mod
from . import params as params_mod
from .exits import Exit

if TYPE_CHECKING:
    from collections.abc import Callable
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
    pretty: bool | None = None
    log_level: str = "WARNING"
    #: Memory tier (:class:`vss_cli.memory.Memory`). None until something asks
    #: for it: :meth:`CommandGroup.memory` opens it on first use, so only the
    #: commands that read or write memory pay for the connection.
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


def requires_note(requires: frozenset[str]) -> str:
    """The services a command calls, as a line for its help text.

    Static, so it costs no probe and is true on any machine. Without it the
    only way to learn a command needs Elasticsearch is to run it and read the
    exit-4 -- fine as a diagnosis, poor as documentation.
    """
    if not requires:
        return ""
    return f"\n\nRequires: {', '.join(sorted(requires))} (see `vss configure show`)."


def _exit_for(exc: Exception) -> Exit | None:
    """Map a library error to an exit code, or None to let it propagate.

    Kept as a name-based table so the CLI does not import ``vss_core`` at
    module scope purely to catch its exceptions -- the whole group is loaded
    lazily, and importing the search library to define an ``except`` clause
    would undo that.
    """
    by_name = {
        "InvalidInputError": Exit.INVALID_INPUT,
        "VIOSInvalidInputError": Exit.INVALID_INPUT,
        "VIOSNotFoundError": Exit.NOT_FOUND,
        "VIOSTimeoutError": Exit.TIMEOUT,
        "NestedCollectionError": Exit.INVALID_INPUT,
        "IndexNotFoundError": Exit.NOT_FOUND,
        "MemoryNotFoundError": Exit.NOT_FOUND,
        "BackendUnreachableError": Exit.BACKEND_UNREACHABLE,
        # A stored document that will not decode is a real failure with nothing
        # for the caller to correct, so it keeps exit 1 -- but as a sentence
        # naming the document, not the pydantic traceback it would be unmapped.
        "MemoryDecodeError": Exit.ERROR,
        "ConfigurationError": Exit.CONFIGURATION,
        "NoFinalResultError": Exit.PARTIAL,
        # The store translates connection and transport trouble, but a status
        # rejection -- a read-only ingress answering 405, a 403, a 5xx -- comes
        # back as the client's own ApiError, which is not a TransportError.
        # `memory.write_failures()` says the same thing for the write path.
        "ApiError": Exit.BACKEND_UNREACHABLE,
        # httpx transport failures that vss_core may not re-wrap as a typed
        # exception (e.g. warm_media_url on an unreachable clip URL).  Mapped by
        # class name so the framework does not import httpx at module scope.
        "ConnectError": Exit.BACKEND_UNREACHABLE,
        "NetworkError": Exit.BACKEND_UNREACHABLE,
        "RemoteProtocolError": Exit.BACKEND_UNREACHABLE,
        "ConnectTimeout": Exit.TIMEOUT,
        "ReadTimeout": Exit.TIMEOUT,
        "WriteTimeout": Exit.TIMEOUT,
        "PoolTimeout": Exit.TIMEOUT,
        "TimeoutException": Exit.TIMEOUT,
    }
    for klass in type(exc).__mro__:
        code = by_name.get(klass.__name__)
        if code is not None:
            return code
    return None


def _format_validation(exc: ValidationError) -> str:
    """Render pydantic errors as ``field: reason``, comma-separated."""
    parts = []
    for error in exc.errors():
        location = ".".join(str(piece) for piece in error["loc"]) or "(payload)"
        parts.append(f"{location}: {error['msg']}")
    return "; ".join(parts)


def require_services(name: str, requires: frozenset[str], ctx: Context) -> None:
    """Fail before dispatch when the deployment lacks a service the command calls.

    Checked here rather than inside each group so the diagnostic is uniform,
    and checked per command so a deployment missing one optional service still
    serves the paths that never touch it.

    Takes ``name``/``requires`` rather than an :class:`Action` so a surface
    without the job grammar -- ``vss vios``, which is a click.Group of plain
    commands -- reports a missing backend with the same wording as a group
    that does.
    """
    if not requires:
        return
    if ctx.deployment is None:
        if ctx.config_error:
            raise config_mod.ConfigError(f"`{name}` needs a deployment: {ctx.config_error}")
        raise config_mod.ConfigError(
            f"no deployment configured, and `{name}` needs "
            f"{', '.join(sorted(requires))}. Run `vss configure --base-url <origin>` first."
        )
    missing = sorted(service for service in requires if not ctx.deployment.has(service))
    if missing:
        known = ", ".join(sorted(ctx.deployment.services)) or "(none)"
        raise config_mod.ConfigError(
            f"`{name}` needs {', '.join(missing)}, which the deployment at "
            f"{ctx.deployment.base_url} does not expose; it has: {known}. "
            f"Re-run `vss configure --base-url {ctx.deployment.base_url}` if the deployment changed."
        )


def guarded(call: Callable[[], Result]) -> Result:
    """Run a verb, turning a typed library failure into its exit code.

    A typed failure is a diagnosis, not a crash. Without this a missing index
    -- the ordinary "nothing ingested yet" case -- exits 1 with an
    Elasticsearch traceback, which no harness can branch on.
    """
    try:
        return call()
    except Exception as exc:
        code = _exit_for(exc)
        if code is None:
            raise
        click.echo(f"vss: {exc}", err=True)
        raise SystemExit(int(code)) from exc


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

    #: Services ``run`` calls, for a group that declares :attr:`Input` rather
    #: than :attr:`actions`. Such a group has one path, so there is nothing to
    #: declare per action -- and without this the synthesized action carries no
    #: requirements, leaving the only single-path groups without the uniform
    #: missing-backend diagnostic every ``actions``-declaring group gets.
    #: Ignored when :attr:`actions` is non-empty, where each action states its
    #: own.
    requires: ClassVar[frozenset[str]] = frozenset()

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

    @final
    def memory(self, ctx: Context) -> Any:
        """The memory tier these verbs read, opened on first use.

        Resolved here rather than in :func:`context_from` so a command that
        never touches memory -- ``run --no-persist``, ``configure`` -- does not
        pay for the Elasticsearch import. An injected :attr:`Context.memory`
        wins, which is what lets tests run the read verbs against a store in
        the same process.
        """
        if ctx.memory is None:
            ctx.memory = memory_mod.build(ctx.deployment)
            click_context = click.get_current_context(silent=True)
            if click_context is not None:
                click_context.call_on_close(ctx.memory.close)
        return ctx.memory

    @final
    def persist_memory(self, ctx: Context, *, no_persist: bool) -> Any:
        """Return the memory store when the deployment policy says to persist, else None.

        Delegates the full persistence-policy decision to the memory module so
        individual commands do not inspect ``memory.enabled`` or
        ``memory.persist_by_default`` directly.  See
        :func:`vss_cli.memory.open_for_persist` for the policy semantics.

        An injected :attr:`Context.memory` (e.g. a test double) bypasses the
        policy check so tests can control the store directly.
        """
        if no_persist:
            return None
        if ctx.memory is not None:
            return ctx.memory
        return memory_mod.open_for_persist(ctx.deployment, no_persist=False)

    def status(self, job_id: str, ctx: Context) -> Result:
        return Result(body=self.memory(ctx).status(self.name, job_id), job_id=job_id)

    def get(self, job_id: str, ctx: Context) -> Result:
        return Result(body=self.memory(ctx).get(self.name, job_id), job_id=job_id)

    def list(self, filters: dict[str, Any], ctx: Context) -> Result:
        return Result(body=self.memory(ctx).query(self.name, filters))

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
        return self._action_command(
            Action(name="run", summary=f"Run a {self.name} job.", Input=self.Input, requires=self.requires)
        )

    def _action_command(self, action: Action) -> click.Command:
        owner = self
        model = action.Input

        extra_names = {p.name for p in owner.extra_params if p.name}

        def callback(**values: Any) -> None:
            ctx = context_from(values)
            ctx.extra = {k: v for k, v in values.items() if k in extra_names and v is not None and v != ()}
            payload = params_mod.collect(model, values)
            try:
                inputs = model(**payload)
            except ValidationError as exc:
                # Input the model rejects is the caller's error, not a crash:
                # report it as exit 2 with the offending fields named, rather
                # than letting a pydantic traceback out as a generic exit 1.
                raise InvalidInput(_format_validation(exc)) from exc
            require_services(action.name, action.requires, ctx)

            def dispatch() -> Result:
                try:
                    return owner.run(action.name if owner.actions else "", inputs, ctx)
                except ValidationError as exc:
                    # A group's input model is a CLI-shaped subset of whatever
                    # the library accepts, so the library can still reject a
                    # value that passed here (a timestamp typed as a string,
                    # say). That is equally the caller's error, same exit 2.
                    raise InvalidInput(_format_validation(exc)) from exc

            emit(guarded(dispatch), ctx, marker_group=owner.name)

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
            help=inspect.cleandoc(model.__doc__ or action.summary) + requires_note(action.requires),
        )

    def _handle_command(self, verb: str, fn: Any) -> click.Command:
        owner = self

        def callback(**values: Any) -> None:
            ctx = context_from(values)
            emit(guarded(lambda: fn(values["job_id"], ctx)), ctx)

        return click.Command(
            name=verb,
            params=[
                click.Option(["--job-id"], required=True),
                *params_mod.shared_options(),
            ],
            callback=callback,
            short_help=f"{verb.capitalize()} a {owner.name} job by id.",
        )

    def _list_command(self) -> click.Command:
        owner = self

        def _instant(_ctx: click.Context, _param: click.Parameter, value: str | None) -> str | None:
            """Reject a malformed ``--since`` while it is still the caller's error.

            Left alone it reaches the store's time helpers as a ``ValueError``,
            which ``_exit_for`` does not map, so an ordinary typo exits 1 with a
            traceback rather than 2 with a sentence. Validated with the same
            function that will parse it, so the two cannot disagree.
            """
            if value is None:
                return None
            from vss_core._foundation.time import iso8601_to_datetime

            try:
                iso8601_to_datetime(value)
            except ValueError as error:
                raise click.BadParameter(f"{value!r} is not an ISO-8601 instant, e.g. 2026-08-13T20:00:00Z") from error
            return value

        filters = (
            # Durations ("1h") read well but were never implemented; the help
            # promised them and the parser rejected them.
            click.Option(["--since"], callback=_instant, help="Only jobs at or after this ISO-8601 instant."),
            click.Option(["--sensor-id"], help="Restrict to one sensor."),
            click.Option(["--status"], help="Restrict to one job status."),
        )

        def callback(**values: Any) -> None:
            ctx = context_from(values)
            selected = {k: values[k] for k in ("since", "sensor_id", "status") if values.get(k)}
            emit(guarded(lambda: owner.list(selected, ctx)), ctx)

        return click.Command(
            name="list",
            params=[*filters, *params_mod.shared_options()],
            callback=callback,
            short_help=f"List recent {owner.name} jobs, including in-flight.",
        )


# -- helpers ------------------------------------------------------------


def context_from(values: dict[str, Any]) -> Context:
    """Assemble a Context from the shared flags, resolving the deployment.

    The recorded deployment is the only source of endpoints. When none is
    recorded ``deployment`` is None, and :func:`require_services` turns that
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
        pretty=values.get("pretty"),
        log_level=values.get("log_level") or "WARNING",
    )


def _completion_marker(result: Result, group: str) -> dict[str, Any]:
    """Build the compact, group-agnostic completion callback from one result."""
    marker_data = result.extra.get("marker")
    data = marker_data if isinstance(marker_data, dict) else {}
    status = str(data.get("status") or "completed")
    if status == "timeout":
        event = "vss_job_timeout"
    elif status == "failed":
        event = "vss_job_failed"
    else:
        event = "vss_job_completed"
    return {
        "event": event,
        "group": memory_mod.group_token(group),
        "job_id": result.job_id,
        "asset_id": data.get("asset_id"),
        "status": status,
        "persisted": bool(data.get("persisted", False)),
        "exit_hint": int(result.exit),
    }


def emit(result: Result, ctx: Context, *, marker_group: str | None = None) -> None:
    """Render a Result, append the §7.2 marker, and carry its exit code."""
    import json

    if result.body is not None:
        pretty = bool(ctx.pretty)
        text = json.dumps(result.body, indent=2 if pretty else None, default=str)
        click.echo(text)
    if marker_group is not None and result.job_id:
        marker = _completion_marker(result, marker_group)
        text = json.dumps(marker, separators=(",", ":"), default=str)
        if len(text.encode("utf-8")) > 1024:
            marker["asset_id"] = None
            text = json.dumps(marker, separators=(",", ":"), default=str)
        if len(text.encode("utf-8")) > 1024:  # pragma: no cover - fixed fields are bounded
            raise ValueError("completion marker exceeds 1 KB")
        click.echo(text)
    if result.exit != Exit.SUCCESS:
        raise SystemExit(int(result.exit))
