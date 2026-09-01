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

from dataclasses import replace
from datetime import UTC
from datetime import datetime
import json
from pathlib import Path
from typing import Any
from typing import NoReturn

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

    # Without a scheme httpx refuses to build the request, so every route comes
    # back "absent" with an UnsupportedProtocol detail and the summary blames
    # the ingress -- pointing at the deployment when the fault is the argument.
    # Assume http and say so, rather than guessing silently or failing on
    # something whose intent is unambiguous. Checked on "://" and not urlparse:
    # urlparse reads "localhost:7777" as scheme "localhost", path "7777".
    if "://" not in base_url:
        base_url = f"http://{base_url}"
        click.echo(f"no scheme given, assuming {base_url}", err=True)

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
        memory=_configured_memory_or_none(),
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


def _configured_memory_or_none() -> config_mod.MemoryConfig | None:
    """Preserve valid static memory policy when deployment routes are refreshed."""
    try:
        return config_mod.load().memory
    except config_mod.ConfigError:
        return None


def _memory_config_error(message: str) -> NoReturn:
    click.echo(f"vss configure memory: configuration error: {message}", err=True)
    raise SystemExit(int(Exit.CONFIGURATION))


def _memory_backend_error(message: str) -> NoReturn:
    click.echo(f"vss configure memory: backend unreachable: {message}", err=True)
    raise SystemExit(int(Exit.BACKEND_UNREACHABLE))


def _embedding_probe_error(error: BaseException) -> NoReturn:
    message = str(error)
    if "environment variable" in message or "authentication failed" in message or "authorization failed" in message:
        click.echo(f"vss configure memory: embedding credential error: {message}", err=True)
        raise SystemExit(int(Exit.CONFIGURATION))
    if "dimension" in message:
        click.echo(f"vss configure memory: embedding dimension error: {message}", err=True)
        raise SystemExit(int(Exit.CONFIGURATION))
    if "response" in message or "JSON" in message:
        click.echo(f"vss configure memory: malformed embedding response: {message}", err=True)
        raise SystemExit(int(Exit.BACKEND_UNREACHABLE))
    _memory_backend_error(message)


def _load_memory_deployment() -> config_mod.Deployment:
    try:
        return config_mod.load()
    except config_mod.ConfigError as error:
        _memory_config_error(str(error))
        raise AssertionError("unreachable") from error


def _require_memory_config(deployment: config_mod.Deployment) -> config_mod.MemoryConfig:
    memory_config = deployment.memory
    if memory_config is None:
        _memory_config_error(
            "memory is not configured; run `vss configure memory --enable --backend elasticsearch --index vss-memory`"
        )
    return memory_config


def _check_memory_backend(
    deployment: config_mod.Deployment,
    memory_config: config_mod.MemoryConfig,
    *,
    timeout: float = _PROBE_TIMEOUT_SECONDS,
) -> str:
    """Read Elasticsearch indices without creating or changing records."""
    import httpx

    endpoint = deployment.endpoint_or_none("elasticsearch")
    if not endpoint:
        _memory_config_error(
            "the configured deployment exposes no Elasticsearch route; "
            f"run `vss configure --base-url {deployment.base_url}` after exposing Elasticsearch"
        )
    try:
        response = httpx.get(
            f"{endpoint.rstrip('/')}/_cat/indices?h=index&format=json",
            timeout=timeout,
        )
        response.raise_for_status()
    except httpx.HTTPError as error:
        _memory_backend_error(
            f"Elasticsearch at {endpoint} did not answer; check the service, then run `vss configure memory check` ({error})"
        )
    return f"Elasticsearch reachable at {endpoint}; authoritative index={memory_config.index}"


def _probe_embedding(embedding: config_mod.EmbeddingConfig) -> tuple[int, str | None]:
    from vss_core.memory import EmbeddingProviderError
    from vss_core.memory import OpenAICompatibleEmbeddingProvider

    assert embedding.endpoint is not None
    assert embedding.model is not None
    provider = OpenAICompatibleEmbeddingProvider(
        endpoint=embedding.endpoint,
        model=embedding.model,
        dimensions=embedding.dimensions,
        timeout_seconds=embedding.timeout_seconds,
        batch_size=embedding.batch_size,
        api_key_env=embedding.api_key_env,
        query_input_type=embedding.query_input_type,
        document_input_type=embedding.document_input_type,
    )
    try:
        vector = provider.embed_query("VSS memory embedding health check")
    except EmbeddingProviderError as error:
        _embedding_probe_error(error)
    finally:
        provider.close()
    return len(vector), provider.resolved_model


def _check_embedding_backend(
    deployment: config_mod.Deployment,
    memory_config: config_mod.MemoryConfig,
) -> tuple[str, str]:
    """Probe one embedding and inspect the companion mapping without mutation."""
    import httpx

    embedding = memory_config.embeddings
    dimensions, resolved_model = _probe_embedding(embedding)
    resolved_detail = f"; resolved_model={resolved_model}" if resolved_model is not None else ""
    probe_detail = (
        f"Embedding endpoint reachable; provider={embedding.provider}; target={embedding.model}; "
        f"dimensions={dimensions}{resolved_detail}"
    )

    endpoint = deployment.endpoint_or_none("elasticsearch")
    if not endpoint:
        _memory_config_error("cannot inspect the embedding index because the deployment exposes no Elasticsearch route")
    mapping_url = f"{endpoint.rstrip('/')}/{embedding.index}/_mapping"
    try:
        response = httpx.get(mapping_url, timeout=embedding.timeout_seconds)
    except httpx.HTTPError as error:
        _memory_backend_error(f"could not inspect companion embedding index mapping ({type(error).__name__})")
    if response.status_code == 404:
        return probe_detail, (
            f"Companion embedding index {embedding.index} is missing; it will be created lazily "
            "on the first embedding write or backfill"
        )
    try:
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as error:
        _memory_backend_error(f"could not inspect companion embedding index mapping ({type(error).__name__})")
    index_mapping = payload.get(embedding.index) if isinstance(payload, dict) else None
    mappings = index_mapping.get("mappings") if isinstance(index_mapping, dict) else None
    properties = mappings.get("properties") if isinstance(mappings, dict) else None
    vector_mapping = properties.get("vector") if isinstance(properties, dict) else None
    metadata = mappings.get("_meta") if isinstance(mappings, dict) else None
    if (
        not isinstance(vector_mapping, dict)
        or vector_mapping.get("type") != "dense_vector"
        or vector_mapping.get("dims") != dimensions
        or vector_mapping.get("similarity") != "cosine"
        or not isinstance(metadata, dict)
        or metadata.get("model") != embedding.model
        or metadata.get("dimensions") != dimensions
    ):
        _memory_config_error(
            f"companion embedding index {embedding.index!r} has an incompatible vector mapping; "
            "configure a new versioned embedding index and backfill it"
        )
    return probe_detail, f"Companion embedding index mapping is compatible: {embedding.index}"


@configure.group(name="memory", invoke_without_command=True)
@click.option("--enable/--disable", "enabled", default=None, help="Enable or disable the memory subsystem.")
@click.option("--backend", default=None, help="Authoritative structured-memory backend (elasticsearch only).")
@click.option("--index", default=None, help="Authoritative Elasticsearch memory index.")
@click.option(
    "--persist-by-default/--no-persist-by-default",
    default=None,
    help="Whether job-producing commands persist automatically.",
)
@click.option("--markdown/--no-markdown", "markdown_enabled", default=None, help="Enable the Markdown cache sink.")
@click.option("--harness", default=None, help="Markdown memory harness (openclaw only).")
@click.option("--workspace", default=None, help="Absolute OpenClaw workspace path.")
@click.option(
    "--write-notes-by-default/--no-write-notes-by-default",
    default=None,
    help="Whether persisted jobs write compact Markdown notes by default.",
)
@click.option("--embeddings/--no-embeddings", "embeddings_enabled", default=None, help="Enable derived embeddings.")
@click.option(
    "--embedding-provider",
    type=click.Choice(["openclaw_gateway", "openai_compatible"]),
    default=None,
    help="Embedding endpoint profile.",
)
@click.option("--embedding-endpoint", default=None, help="OpenAI-compatible embedding base URL.")
@click.option("--embedding-model", default=None, help="OpenClaw agent target or custom embedding model.")
@click.option("--embedding-dimensions", type=int, default=None, help="Expected embedding vector dimensions.")
@click.option("--embedding-index", default=None, help="Companion Elasticsearch vector index.")
@click.option(
    "--embedding-timeout-seconds",
    "--embedding-timeout",
    "embedding_timeout_seconds",
    type=float,
    default=None,
    help="Embedding request timeout in seconds.",
)
@click.option("--embedding-batch-size", type=int, default=None, help="Maximum passage embeddings per request.")
@click.option("--embedding-api-key-env", default=None, help="Environment variable containing a Bearer token.")
@click.option("--no-embedding-auth", is_flag=True, help="Do not send a Bearer token to the embedding endpoint.")
@click.option("--embedding-query-input-type", default=None, help="Optional input_type for query embeddings.")
@click.option("--embedding-document-input-type", default=None, help="Optional input_type for document embeddings.")
@click.option(
    "--retrieval-mode",
    type=click.Choice(["keyword", "semantic", "hybrid"]),
    default=None,
    help="Preferred static memory retrieval mode.",
)
@click.option("--semantic-candidate-count", type=int, default=None, help="Semantic candidates before ranking.")
@click.option("--rrf-rank-constant", type=int, default=None, help="Reciprocal rank fusion constant.")
@click.pass_context
def configure_memory(
    ctx: click.Context,
    enabled: bool | None,
    backend: str | None,
    index: str | None,
    persist_by_default: bool | None,
    markdown_enabled: bool | None,
    harness: str | None,
    workspace: str | None,
    write_notes_by_default: bool | None,
    embeddings_enabled: bool | None,
    embedding_provider: str | None,
    embedding_endpoint: str | None,
    embedding_model: str | None,
    embedding_dimensions: int | None,
    embedding_index: str | None,
    embedding_timeout_seconds: float | None,
    embedding_batch_size: int | None,
    embedding_api_key_env: str | None,
    no_embedding_auth: bool,
    embedding_query_input_type: str | None,
    embedding_document_input_type: str | None,
    retrieval_mode: str | None,
    semantic_candidate_count: int | None,
    rrf_rank_constant: int | None,
) -> None:
    """Configure static VSS memory infrastructure and persistence policy."""
    if ctx.invoked_subcommand is not None:
        return
    deployment = _load_memory_deployment()
    current = deployment.memory or config_mod.MemoryConfig()
    current_markdown = current.markdown
    current_embeddings = current.embeddings
    current_retrieval = current.retrieval
    if embedding_api_key_env is not None and no_embedding_auth:
        _memory_config_error("cannot combine `--embedding-api-key-env` with `--no-embedding-auth`")
    requested_provider = embedding_provider
    if embeddings_enabled is True and requested_provider is None:
        requested_provider = "openclaw_gateway"
    if requested_provider is not None and (
        requested_provider != current_embeddings.provider or embeddings_enabled is True
    ):
        embedding_base = config_mod.EmbeddingConfig.for_provider(
            requested_provider,
            enabled=current_embeddings.enabled if embeddings_enabled is None else embeddings_enabled,
        )
    else:
        embedding_base = current_embeddings
    candidate = config_mod.MemoryConfig(
        enabled=current.enabled if enabled is None else enabled,
        backend=current.backend if backend is None else backend,
        index=current.index if index is None else index,
        persist_by_default=current.persist_by_default if persist_by_default is None else persist_by_default,
        markdown=config_mod.MarkdownMemoryConfig(
            enabled=current_markdown.enabled if markdown_enabled is None else markdown_enabled,
            harness=current_markdown.harness if harness is None else harness,
            workspace=current_markdown.workspace if workspace is None else workspace,
            write_by_default=current_markdown.write_by_default
            if write_notes_by_default is None
            else write_notes_by_default,
        ),
        introspection=current.introspection,
        embeddings=config_mod.EmbeddingConfig(
            enabled=embedding_base.enabled if embeddings_enabled is None else embeddings_enabled,
            provider=embedding_base.provider if embedding_provider is None else embedding_provider,
            endpoint=embedding_base.endpoint if embedding_endpoint is None else embedding_endpoint,
            model=embedding_base.model if embedding_model is None else embedding_model,
            dimensions=embedding_base.dimensions if embedding_dimensions is None else embedding_dimensions,
            index=embedding_base.index if embedding_index is None else embedding_index,
            timeout_seconds=embedding_base.timeout_seconds
            if embedding_timeout_seconds is None
            else embedding_timeout_seconds,
            batch_size=embedding_base.batch_size if embedding_batch_size is None else embedding_batch_size,
            api_key_env=None
            if no_embedding_auth
            else embedding_base.api_key_env
            if embedding_api_key_env is None
            else embedding_api_key_env,
            query_input_type=embedding_base.query_input_type
            if embedding_query_input_type is None
            else embedding_query_input_type,
            document_input_type=embedding_base.document_input_type
            if embedding_document_input_type is None
            else embedding_document_input_type,
        ),
        retrieval=config_mod.RetrievalConfig(
            mode=current_retrieval.mode if retrieval_mode is None else retrieval_mode,
            candidate_count=current_retrieval.candidate_count
            if semantic_candidate_count is None
            else semantic_candidate_count,
            rrf_rank_constant=current_retrieval.rrf_rank_constant if rrf_rank_constant is None else rrf_rank_constant,
        ),
    )
    try:
        candidate.validate()
        if candidate.embeddings.enabled and candidate.embeddings.dimensions is None:
            dimensions, resolved_model = _probe_embedding(candidate.embeddings)
            candidate = replace(
                candidate,
                embeddings=replace(candidate.embeddings, dimensions=dimensions),
            )
            resolved_detail = f" (endpoint reported {resolved_model})" if resolved_model is not None else ""
            click.echo(f"discovered embedding dimensions: {dimensions}{resolved_detail}", err=True)
        path = config_mod.save(
            config_mod.Deployment(
                base_url=deployment.base_url,
                services=deployment.services,
                memory=candidate,
                written_at=deployment.written_at,
            )
        )
    except config_mod.ConfigError as error:
        _memory_config_error(str(error))
    click.echo(f"wrote memory configuration to {path}", err=True)


@configure_memory.command(name="introspection")
@click.option("--judge-endpoint", help="OpenAI-compatible text-judge base URL.")
@click.option("--judge-model", help="API-facing text-judge model (default: openclaw/default on first setup).")
@click.option("--judge-backend-model", help="Optional OpenClaw backend model sent as x-openclaw-model.")
@click.option("--clear-judge-backend-model", is_flag=True, help="Remove the OpenClaw backend-model override.")
@click.option("--judge-api-key-env", help="Environment variable containing the text-judge Bearer token.")
@click.option("--clear-judge-api-key-env", is_flag=True, help="Remove the text-judge credential environment name.")
@click.option("--judge-criteria", help="Inline memory-sufficiency criteria.")
@click.option(
    "--judge-criteria-file",
    type=click.Path(path_type=Path, exists=True, dir_okay=False, readable=True),
    help="UTF-8 file whose contents become the memory-sufficiency criteria.",
)
def configure_memory_introspection(
    judge_endpoint: str | None,
    judge_model: str | None,
    judge_backend_model: str | None,
    clear_judge_backend_model: bool,
    judge_api_key_env: str | None,
    clear_judge_api_key_env: bool,
    judge_criteria: str | None,
    judge_criteria_file: Path | None,
) -> None:
    """Configure the text LLM used to judge and synthesize introspection."""
    if judge_backend_model is not None and clear_judge_backend_model:
        raise click.UsageError("cannot combine --judge-backend-model with --clear-judge-backend-model")
    if judge_api_key_env is not None and clear_judge_api_key_env:
        raise click.UsageError("cannot combine --judge-api-key-env with --clear-judge-api-key-env")
    if judge_criteria is not None and judge_criteria_file is not None:
        raise click.UsageError("cannot combine --judge-criteria with --judge-criteria-file")

    deployment = _load_memory_deployment()
    current_memory = deployment.memory or config_mod.MemoryConfig()
    current_introspection = current_memory.introspection
    current_judge = current_introspection.judge if current_introspection is not None else None
    if current_judge is None and judge_endpoint is None:
        raise click.UsageError("--judge-endpoint is required on first introspection configuration")

    criteria = judge_criteria
    if judge_criteria_file is not None:
        try:
            criteria = judge_criteria_file.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            _memory_config_error(f"cannot read introspection judge criteria file {judge_criteria_file}: {error}")

    candidate_judge = config_mod.IntrospectionJudgeConfig(
        endpoint=judge_endpoint if judge_endpoint is not None else current_judge.endpoint,  # type: ignore[union-attr]
        model=(
            judge_model
            if judge_model is not None
            else current_judge.model
            if current_judge is not None
            else "openclaw/default"
        ),
        backend_model=(
            None
            if clear_judge_backend_model
            else judge_backend_model
            if judge_backend_model is not None
            else current_judge.backend_model
            if current_judge is not None
            else None
        ),
        api_key_env=(
            None
            if clear_judge_api_key_env
            else judge_api_key_env
            if judge_api_key_env is not None
            else current_judge.api_key_env
            if current_judge is not None
            else None
        ),
        criteria_prompt=(
            criteria
            if criteria is not None
            else current_judge.criteria_prompt
            if current_judge is not None
            else config_mod.DEFAULT_INTROSPECTION_CRITERIA_PROMPT
        ),
    )
    candidate = config_mod.MemoryConfig(
        enabled=current_memory.enabled,
        backend=current_memory.backend,
        index=current_memory.index,
        persist_by_default=current_memory.persist_by_default,
        markdown=current_memory.markdown,
        introspection=config_mod.IntrospectionMemoryConfig(judge=candidate_judge),
    )
    try:
        candidate.validate()
        path = config_mod.save(
            config_mod.Deployment(
                base_url=deployment.base_url,
                services=deployment.services,
                memory=candidate,
                written_at=deployment.written_at,
            )
        )
    except config_mod.ConfigError as error:
        _memory_config_error(str(error))
    click.echo(f"wrote introspection judge configuration to {path}", err=True)


@configure_memory.command(name="show")
def show_memory() -> None:
    """Print only the effective static memory configuration."""
    deployment = _load_memory_deployment()
    memory_config = _require_memory_config(deployment)
    click.echo(json.dumps(memory_config.to_json(), indent=2))


@configure_memory.command(name="check")
def check_memory() -> None:
    """Validate static memory policy and read-only backend reachability."""
    deployment = _load_memory_deployment()
    memory_config = _require_memory_config(deployment)
    try:
        memory_config.validate()
    except config_mod.ConfigError as error:
        _memory_config_error(str(error))
    if not memory_config.enabled:
        _memory_config_error("memory is disabled; run `vss configure memory --enable`")
    click.echo(_check_memory_backend(deployment, memory_config))
    if memory_config.embeddings.enabled:
        embedding_probe, mapping_probe = _check_embedding_backend(deployment, memory_config)
        click.echo(embedding_probe)
        click.echo(mapping_probe)
    if memory_config.markdown.enabled:
        try:
            from vss_core.memory import OpenClawDailyNoteStore

            OpenClawDailyNoteStore(memory_config.markdown.workspace or "")
        except (ImportError, ValueError) as error:
            _memory_config_error(
                f"Markdown memory workspace is invalid; re-run `vss configure memory --workspace /absolute/path` ({error})"
            )
        click.echo(f"OpenClaw Markdown cache enabled at {memory_config.markdown.workspace}/memory/YYYY-MM-DD-vss.md")


@configure.command("show")
def show() -> None:
    """Print the recorded deployment."""
    try:
        deployment = config_mod.load()
    except config_mod.ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(deployment.to_json(), indent=2))


def _command_availability(deployment: config_mod.Deployment) -> list[tuple[str, bool, str]]:
    """Which command groups this deployment can actually serve.

    Requirements are static per action, and what the deployment exposes is
    already recorded, so this needs no probe. It exists because neither half
    is useful alone: `configure show` says what you have, `--help` says what a
    command needs, and nobody wants to do the join by hand.
    """
    from . import plugins

    # A group's requirements are unioned across its actions on purpose: a
    # partially-available group is a trap. `search` reporting "available"
    # because one retrieval path happens to work invites a caller to use the
    # group and fail on the path they actually wanted. Available means the
    # whole surface is.
    rows: list[tuple[str, bool, str]] = []
    for ref in plugins.discover():
        try:
            spec = plugins.load(ref.name)
        except Exception:
            continue
        requires: set[str] = set()
        for action in getattr(spec, "actions", ()) or ():
            requires |= set(getattr(action, "requires", frozenset()))
        requires |= set(getattr(spec, "requires", frozenset()) or frozenset())
        # A group declaring nothing -- `configure` itself -- always works.
        missing = sorted(r for r in requires if not deployment.has(r))
        rows.append((ref.name, not missing, ", ".join(sorted(requires)) if requires else "-"))
        if missing:
            rows[-1] = (ref.name, False, f"needs {', '.join(missing)}")
    return sorted(rows)


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
    rows = _command_availability(deployment)
    if rows:
        click.echo("", err=True)
        click.echo("commands:")
        for name, ok, detail in rows:
            click.echo(f"  {name:<14} {'available' if ok else 'unavailable':<12} {detail}")

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
