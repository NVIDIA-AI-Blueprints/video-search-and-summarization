# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The command-group base class: the framework owns the verbs and the lifecycle.

Every group answers the same four verbs (SDD §3.1)::

    vss <group> run       synchronous; returns only when the result is final
    vss <group> status    reconcile a record memory still marks pending
    vss <group> get       fetch a completed record by job_id
    vss <group> list      recent jobs, including in-flight

A group contributes only its domain. ``run`` is the framework's: it resolves
memory policy, mints the job, writes ``submitted``, calls the group's
:meth:`~CommandGroup.execute`, writes the outcome, and reports through one
:class:`Result` shape. The group supplies four hooks -- :meth:`~CommandGroup.adapter`,
:meth:`~CommandGroup.prepare`, :meth:`~CommandGroup.execute`,
:meth:`~CommandGroup.build_bundle` -- and one renderer,
:meth:`~CommandGroup.render`, for the keys only it knows.

§6.2 makes ``status``/``get``/``list`` pure reads against the memory index --
"get on a completed job, list, and terminal status never touch a backend" --
so a group has nothing to contribute to them and inherits the framework's.

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

    from vss_core.memory import RecordBundle
    from vss_core.memory.adapters import LifecycleAdapter

    from .lifecycle import Job

#: Contract version. A group built against a different major is refused at
#: load time rather than half-mounted.
API_VERSION = 2


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
        "AnalyticsInvalidInputError": Exit.INVALID_INPUT,
        "InvalidInputError": Exit.INVALID_INPUT,
        "VIOSInvalidInputError": Exit.INVALID_INPUT,
        "VIOSNotFoundError": Exit.NOT_FOUND,
        "VIOSTimeoutError": Exit.TIMEOUT,
        "AnalyticsNotFoundError": Exit.NOT_FOUND,
        "AnalyticsTimeoutError": Exit.TIMEOUT,
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

    #: Group name as it appears in ``vss <name> ...``. Also the job-id domain:
    #: jobs are ``<name>-<ULID>``.
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
    #:
    #: Two names are read by the framework when a group declares them:
    #: ``no_persist`` opts one run out of memory, and ``write_memory_note``
    #: overrides the Markdown-note policy. A group that does not declare
    #: ``write_memory_note`` never writes notes.
    extra_params: ClassVar[Sequence[click.Parameter]] = ()

    #: Non-job subcommands. §2 keeps ``search embed|attribute`` as
    #: "low-level non-job primitives (developer surface)": no job_id, no
    #: persistence, not part of the verb grammar.
    primitives: ClassVar[Sequence[click.Command]] = ()

    #: Whether a configured-but-unreachable memory tier stops the run before
    #: the work (exit 4) or lets it proceed unpersisted (exit 6 on success).
    #: Both are deliberate: an hour of summarization that nothing can hold is
    #: worth refusing up front; a seconds-long visual answer is not worth
    #: losing to a store outage. Default is refuse; a point call flips it.
    require_memory: ClassVar[bool] = True

    # -- what a group contributes ----------------------------------------

    @classmethod
    @abstractmethod
    def adapter(cls) -> type[LifecycleAdapter]:
        """The adapter that maps this group's jobs onto memory records.

        A classmethod rather than a class attribute so the ``vss_core`` import
        stays inside the body and off the ``--help`` path.
        """

    @abstractmethod
    def prepare(self, action: str, inputs: BaseModel, ctx: Context, job: Job, *, persist: bool) -> None:
        """Resolve what to run and fill ``job.input_data`` (and ``job.asset_id``).

        Runs before anything is written, so raising here -- an
        :class:`InvalidInput`, a :class:`~vss_cli.config.ConfigError` -- leaves
        no record and exits without a marker, like any pre-work usage error.
        ``persist`` says whether the framework intends to write, for checks
        such as "a persisted record needs an asset id".
        """

    @abstractmethod
    def execute(self, action: str, inputs: BaseModel, ctx: Context, job: Job) -> Any:
        """Do the work and return the domain output.

        Everything raised here is post-mint and owes the caller a record.
        Raise :class:`~vss_cli.lifecycle.JobError` to classify the outcome
        (exit code, ``failed``/``timeout``, stderr text); an :class:`InvalidInput`
        becomes exit 2; anything else is mapped by :func:`_exit_for`, or exit 1.
        May refine ``job.input_data`` before returning -- a resolved window, say.
        """

    @abstractmethod
    def build_bundle(self, job: Job, output: Any) -> RecordBundle:
        """Map the output onto the terminal parent record plus its result rows."""

    @abstractmethod
    def render(self, job: Job, output: Any, persist: dict[str, Any] | None) -> dict[str, Any]:
        """The group's own body keys for a successful run.

        ``persist`` is the framework's persistence block (or None when nothing
        was attempted); a group may add to it. The framework overlays
        ``job_id``, ``status``, ``persisted``, ``record`` and ``persist`` after.
        """

    # -- the framework's run ---------------------------------------------

    @final
    def run(self, action: str, inputs: BaseModel, ctx: Context) -> Result:
        """Mint, write ``submitted``, execute, write the outcome, report.

        ``action`` is the sub-action name, or ``""`` for a group that declares
        no :attr:`actions`.
        """
        from vss_core.memory.adapters import utc_now_iso

        from .lifecycle import Job
        from .lifecycle import JobError
        from .lifecycle import Lifecycle
        from .lifecycle import mint_job_id
        from .memory_policy import MemoryPolicyInputError
        from .memory_policy import resolve_memory_policy

        declared = {param.name for param in self.extra_params}
        no_persist = bool(ctx.extra.get("no_persist", False))
        note_override = ctx.extra.get("write_memory_note") if "write_memory_note" in declared else False
        try:
            policy = resolve_memory_policy(ctx.deployment, no_persist=no_persist, note_override=note_override)
        except MemoryPolicyInputError as error:
            raise InvalidInput(str(error)) from error

        job = Job(job_id=mint_job_id(self.name), created_at=utc_now_iso())
        self.prepare(action, inputs, ctx, job, persist=policy.persist)

        # Opened before the work, not after: a deployment with no memory at all
        # is worth an immediate exit 4 rather than an hour of work followed by
        # the discovery that nothing can hold it -- unless the group says a
        # store outage must not cost the caller the answer.
        memory: memory_mod.Memory | None = None
        persist_error: str | None = None
        if policy.persist:
            try:
                memory = self.memory(ctx)
            except memory_mod.MemoryUnavailable as error:
                if self.require_memory:
                    raise
                persist_error = str(error)
                click.echo(f"vss: unified memory is unavailable, running {self.name} without it ({error})", err=True)

        lifecycle = Lifecycle(memory, self.adapter()(), job_id=job.job_id, created_at=job.created_at)
        if lifecycle.active and not lifecycle.open(job.input_data):
            persist_error = lifecycle.persist_error
            click.echo(
                f"vss: unified memory is not writable, running {self.name} without it ({persist_error})", err=True
            )

        def marker(status: str, persisted: bool) -> dict[str, Any]:
            return {"marker": {"asset_id": job.asset_id, "status": status, "persisted": persisted}}

        def close(status: str, detail: str) -> str:
            """Close the record out, and say so when the handle went stale.

            A stale record is worse than none: it still reads ``submitted``, so
            ``status`` reports a finished job as running. Silence would leave a
            caller reconciling against a handle that cannot answer.
            """
            record = lifecycle.close(status, detail, job.input_data)  # type: ignore[arg-type]
            if record == "stale":
                click.echo(
                    f"vss: could not record job {job.job_id} as {status} in unified memory, "
                    f"so `status` still reports it submitted",
                    err=True,
                )
            return record

        def failure(status: str, detail: str, code: Exit, diagnostic: str) -> Result:
            record = close(status, detail)
            click.echo(diagnostic, err=True)
            body = {
                "job_id": job.job_id,
                "status": status,
                "record": record,
                "persisted": record == "closed",
                "error": detail,
            }
            return Result(body=body, exit=code, job_id=job.job_id, extra=marker(status, record == "closed"))

        try:
            output = self.execute(action, inputs, ctx, job)
        except JobError as fail:
            return failure(fail.status, fail.detail, fail.exit, fail.diagnostic or f"vss: {fail.detail}")
        except InvalidInput as exc:
            return failure("failed", exc.message, Exit.INVALID_INPUT, f"vss: {exc.message}")
        except ValidationError as exc:
            detail = _format_validation(exc)
            return failure("failed", detail, Exit.INVALID_INPUT, InvalidInput(detail).format_message())
        except Exception as exc:
            code = _exit_for(exc) or Exit.ERROR
            return failure("failed", str(exc), code, f"vss: {self.name} failed: {exc}")

        token = memory_mod.group_token(self.name)
        persist: dict[str, Any] | None = None
        persisted = False
        record = "absent"
        status = "completed"
        code = Exit.SUCCESS
        if lifecycle.active:
            assert memory is not None
            # ValueError joins the store's own failures: an output this group
            # cannot shape into a record is as unpersistable as a refused write,
            # and costs the caller the same nothing. RuntimeError covers an
            # adapter that refuses a bundle it cannot build.
            unpersistable: tuple[type[BaseException], ...] = (ValueError, RuntimeError, *memory_mod.write_failures())
            try:
                outcome = lifecycle.complete(self.build_bundle(job, output))
            except unpersistable as error:
                # Never lose the result the caller already paid for: degrade to
                # partial so only the write is retried, not the whole job.
                persist = {"status": "failed", "index": memory.index, "group": token, "error": str(error)}
                record = close("partial", str(error))
                status, code = "partial", Exit.PARTIAL
            else:
                persist = {
                    "status": "complete" if outcome.ok else "failed",
                    "index": memory.index,
                    "group": token,
                    **outcome.to_dict(),
                }
                if outcome.ok:
                    # `closed` without asking: the terminal upsert above is what
                    # closing means, and it returned.
                    record, persisted = "closed", True
                else:
                    persist["error"] = "persistence incomplete"
                    # upsert_bundle re-marks a written parent `partial` itself;
                    # only a parent that never got past `submitted` needs closing.
                    stored = memory.service.get(job.job_id, reconcile=False)
                    record = (
                        close("partial", f"persistence incomplete: {outcome.to_dict()}")
                        if stored.job.status in {"submitted", "running"}
                        else "closed"
                    )
                    status, code = "partial", Exit.PARTIAL
        elif persist_error is not None:
            # Retrieval succeeded and only the write did not: exit 6 tells the
            # harness to keep this answer instead of re-running the job.
            persist = {"status": "failed", "error": persist_error}
            status, code = "partial", Exit.PARTIAL

        body = self.render(job, output, persist)
        body["job_id"] = job.job_id
        body["status"] = status
        body["persisted"] = persisted
        body["record"] = record
        if persist is not None:
            body["persist"] = persist

        if persisted and policy.write_note:
            assert memory is not None and ctx.deployment is not None
            try:
                from . import memory_notes

                parent = memory.service.get(job.job_id, reconcile=False)
                note = memory_notes.write(parent, ctx.deployment)
                body["memory_note"] = {"written": note.written, "path": note.path}
            except Exception as error:
                click.echo(f"vss: {self.name} succeeded but Markdown memory-note write failed ({error})", err=True)
                body["memory_note"] = {"written": False, "error": str(error)}
                return Result(body=body, exit=Exit.PARTIAL, job_id=job.job_id, extra=marker("completed", True))

        return Result(body=body, exit=code, job_id=job.job_id, extra=marker(status, persisted))

    # -- deprecated reads, forwarding to `vss memory` --------------------
    #
    # §6.2 made these pure reads against the memory index, so a group never had
    # anything to contribute to them: every one was `self.memory(ctx).<verb>`
    # with the group's own name. Reads belong on the cross-group surface, where
    # one implementation serves every group including the ones added at
    # runtime, so `vss memory get|status|query` is now where they live.
    #
    # They stay mounted for one release because they are published --
    # `cli/README.md` lists them for all three job groups, `cli/AGENTS.md`
    # shows `vss search get` and `vss vlm get`, and the shipped
    # vss-summarize-video skill reconciles an exit 7 with `vss summarize get`.
    # Hidden from help, warned on stderr, and removed in the release after the
    # one that ships this.

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
        for verb in ("status", "get", "list"):
            group.commands[verb].hidden = True
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
            _warn_deprecated_read(owner.name, verb)
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
            _warn_deprecated_read(owner.name, "list")
            selected = {k: values[k] for k in ("since", "sensor_id", "status") if values.get(k)}
            emit(guarded(lambda: owner.list(selected, ctx)), ctx)

        return click.Command(
            name="list",
            params=[*filters, *params_mod.shared_options()],
            callback=callback,
            short_help=f"List recent {owner.name} jobs, including in-flight.",
        )


# -- helpers ------------------------------------------------------------


#: `vss <group> <verb>` -> the `vss memory` command that replaces it.
_READ_REPLACEMENTS = {
    "status": "vss memory status --job-id <id>",
    "get": "vss memory get --job-id <id>",
    "list": "vss memory query --group <group> --parents-only",
}


def _warn_deprecated_read(group: str, verb: str) -> None:
    """Name the replacement on stderr, once per invocation.

    stdout stays exactly what it was, so a caller that only reads the payload
    is unaffected for this release.
    """
    replacement = _READ_REPLACEMENTS[verb].replace("<group>", memory_mod.group_token(group))
    click.echo(
        f"vss: `vss {group} {verb}` is deprecated and will be removed in the next release; use `{replacement}`",
        err=True,
    )


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
