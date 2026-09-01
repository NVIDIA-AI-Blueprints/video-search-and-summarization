# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Static ``vss configure memory`` contract tests."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from typing import Any

from click.testing import CliRunner
import pytest

from vss_cli import config as config_mod
from vss_cli import configure as configure_mod
from vss_cli.exits import Exit

if TYPE_CHECKING:
    from pathlib import Path


def _deployment(*, elasticsearch: bool = True) -> config_mod.Deployment:
    services = {"agent": config_mod.Service(url="http://example/api")}
    if elasticsearch:
        services["elasticsearch"] = config_mod.Service(url="http://example/elasticsearch")
    return config_mod.Deployment(
        base_url="http://example",
        services=services,
        written_at="2026-08-24T00:00:00+00:00",
    )


@pytest.fixture
def config_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(config_mod.CONFIG_HOME_ENV, str(tmp_path))
    config_mod.save(_deployment())
    return tmp_path


def _invoke(*args: str) -> Any:
    return CliRunner().invoke(configure_mod.configure, ["memory", *args])


def test_configure_memory_preserves_deployment_and_permissions(config_home: Path) -> None:
    result = _invoke(
        "--enable",
        "--backend",
        "elasticsearch",
        "--index",
        "vss-memory",
        "--persist-by-default",
    )
    assert result.exit_code == 0, result.output

    deployment = config_mod.load()
    assert deployment.services["agent"].url == "http://example/api"
    assert deployment.services["elasticsearch"].url == "http://example/elasticsearch"
    assert deployment.memory == config_mod.MemoryConfig()
    assert config_home.joinpath("config.json").stat().st_mode & 0o777 == 0o600


def test_configure_memory_updates_only_supplied_values(config_home: Path) -> None:
    assert _invoke("--index", "tenant-memory", "--no-persist-by-default").exit_code == 0
    assert _invoke("--disable").exit_code == 0
    memory_config = config_mod.load().memory
    assert memory_config == config_mod.MemoryConfig(
        enabled=False,
        backend="elasticsearch",
        index="tenant-memory",
        persist_by_default=False,
    )


def test_memory_show_prints_only_effective_memory_configuration(config_home: Path) -> None:
    assert _invoke("--index", "tenant-memory").exit_code == 0
    result = _invoke("show")
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {
        "enabled": True,
        "backend": "elasticsearch",
        "index": "tenant-memory",
        "persist_by_default": True,
        "markdown": {
            "enabled": False,
            "harness": "openclaw",
            "workspace": None,
            "write_by_default": False,
        },
        "introspection": None,
        "embeddings": {
            "enabled": False,
            "provider": "openclaw_gateway",
            "endpoint": "http://127.0.0.1:18789/v1",
            "model": "openclaw/default",
            "dimensions": None,
            "index": "vss-memory-embeddings-v1",
            "timeout_seconds": 30.0,
            "batch_size": 16,
            "api_key_env": "OPENCLAW_GATEWAY_TOKEN",
            "query_input_type": None,
            "document_input_type": None,
        },
        "retrieval": {
            "mode": "hybrid",
            "candidate_count": 50,
            "rrf_rank_constant": 60,
        },
    }
    assert "services" not in result.output
    assert "base_url" not in result.output


def test_memory_check_accepts_reachable_backend(
    config_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _invoke().exit_code == 0
    checked: list[tuple[str, str]] = []

    def reachable(deployment: config_mod.Deployment, memory_config: config_mod.MemoryConfig) -> str:
        checked.append((deployment.services["elasticsearch"].url, memory_config.index))
        return "reachable"

    monkeypatch.setattr(configure_mod, "_check_memory_backend", reachable)
    result = _invoke("check")
    assert result.exit_code == 0, result.output
    assert result.output.strip() == "reachable"
    assert checked == [("http://example/elasticsearch", "vss-memory")]


def test_memory_backend_check_uses_ingress_safe_read_only_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested: list[str] = []

    class Response:
        def raise_for_status(self) -> None:
            return None

    def get(url: str, **_kwargs: object) -> Response:
        requested.append(url)
        return Response()

    monkeypatch.setattr("httpx.get", get)
    detail = configure_mod._check_memory_backend(_deployment(), config_mod.MemoryConfig())
    assert requested == ["http://example/elasticsearch/_cat/indices?h=index&format=json"]
    assert "authoritative index=vss-memory" in detail


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("--backend", "sqlite"), "unsupported memory backend"),
        (("--index", "VSS Memory"), "invalid memory index"),
        (("--disable",), "cannot be enabled by default"),
    ],
)
def test_invalid_memory_configuration_exits_four(
    config_home: Path,
    args: tuple[str, ...],
    message: str,
) -> None:
    result = _invoke(*args)
    assert result.exit_code == int(Exit.CONFIGURATION)
    assert message in result.output
    assert config_mod.load().memory is None


def test_memory_show_and_check_require_configuration(config_home: Path) -> None:
    for command in ("show", "check"):
        result = _invoke(command)
        assert result.exit_code == int(Exit.CONFIGURATION)
        assert "vss configure memory" in result.output


def test_memory_check_rejects_disabled_memory(config_home: Path) -> None:
    assert _invoke("--disable", "--no-persist-by-default").exit_code == 0
    result = _invoke("check")
    assert result.exit_code == int(Exit.CONFIGURATION)
    assert "memory is disabled" in result.output
    assert "vss configure memory --enable" in result.output


def test_memory_check_requires_elasticsearch_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(config_mod.CONFIG_HOME_ENV, str(tmp_path))
    config_mod.save(
        config_mod.Deployment(
            base_url="http://example",
            services=_deployment(elasticsearch=False).services,
            memory=config_mod.MemoryConfig(),
        )
    )
    result = _invoke("check")
    assert result.exit_code == int(Exit.CONFIGURATION)
    assert "no Elasticsearch route" in result.output
    assert "vss configure --base-url" in result.output


def test_memory_check_reports_backend_reachability_as_exit_three(
    config_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _invoke().exit_code == 0

    class Unreachable:
        def raise_for_status(self) -> None:
            import httpx

            raise httpx.ConnectError("refused")

    monkeypatch.setattr("httpx.get", lambda *_args, **_kwargs: Unreachable())
    result = _invoke("check")
    assert result.exit_code == int(Exit.BACKEND_UNREACHABLE)
    assert "backend unreachable" in result.output
    assert "vss configure memory check" in result.output


def test_version_one_config_has_actionable_migration_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(config_mod.CONFIG_HOME_ENV, str(tmp_path))
    tmp_path.joinpath("config.json").write_text(
        json.dumps(
            {
                "version": 1,
                "base_url": "http://example",
                "written_at": "2026-08-24T00:00:00+00:00",
                "services": {"elasticsearch": {"url": "http://example/elasticsearch"}},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(config_mod.ConfigError) as error:
        config_mod.load()
    assert "vss configure --base-url <origin>" in str(error.value)
    assert "then re-run `vss configure memory" in str(error.value)


def test_main_configure_preserves_memory_policy(
    config_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _invoke("--index", "tenant-memory", "--no-persist-by-default").exit_code == 0
    monkeypatch.setattr(configure_mod, "_probe", lambda *_args, **_kwargs: (True, "HTTP 200"))
    monkeypatch.setattr(configure_mod, "_describe", lambda *_args, **_kwargs: [])

    result = CliRunner().invoke(configure_mod.configure, ["--base-url", "http://new"])
    assert result.exit_code == 0, result.output
    assert config_mod.load().memory == config_mod.MemoryConfig(
        index="tenant-memory",
        persist_by_default=False,
    )


def test_configure_openclaw_markdown_settings(config_home: Path) -> None:
    workspace = config_home / "openclaw-workspace"
    workspace.mkdir()
    result = _invoke(
        "--markdown",
        "--harness",
        "openclaw",
        "--workspace",
        str(workspace),
        "--write-notes-by-default",
    )
    assert result.exit_code == 0, result.output
    memory_config = config_mod.load().memory
    assert memory_config is not None
    assert memory_config.markdown == config_mod.MarkdownMemoryConfig(
        enabled=True,
        harness="openclaw",
        workspace=str(workspace),
        write_by_default=True,
    )
    shown = json.loads(_invoke("show").output)
    assert shown["markdown"]["workspace"] == str(workspace)
    assert shown["markdown"]["write_by_default"] is True


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("--markdown",), "requires `--workspace"),
        (("--markdown", "--workspace", "relative/path"), "absolute path"),
        (("--harness", "other"), "unsupported Markdown memory harness"),
        (("--no-markdown", "--write-notes-by-default"), "while Markdown memory is disabled"),
        (
            (
                "--markdown",
                "--workspace",
                "/tmp/openclaw",
                "--write-notes-by-default",
                "--no-persist-by-default",
            ),
            "authoritative persistence is disabled",
        ),
    ],
)
def test_invalid_markdown_configuration_exits_four(
    config_home: Path,
    args: tuple[str, ...],
    message: str,
) -> None:
    result = _invoke(*args)
    assert result.exit_code == int(Exit.CONFIGURATION)
    assert message in result.output


def test_memory_check_validates_openclaw_workspace(
    config_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = config_home / "missing-workspace"
    assert _invoke("--markdown", "--workspace", str(missing)).exit_code == 0
    monkeypatch.setattr(configure_mod, "_check_memory_backend", lambda *_args, **_kwargs: "reachable")
    result = _invoke("check")
    assert result.exit_code == int(Exit.CONFIGURATION)
    assert "workspace is invalid" in result.output

    missing.mkdir()
    result = _invoke("check")
    assert result.exit_code == 0, result.output
    assert "OpenClaw Markdown cache enabled" in result.output


def test_memory_config_without_markdown_section_uses_disabled_defaults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(config_mod.CONFIG_HOME_ENV, str(tmp_path))
    tmp_path.joinpath("config.json").write_text(
        json.dumps(
            {
                "version": 2,
                "base_url": "http://example",
                "services": {"elasticsearch": {"url": "http://example/elasticsearch"}},
                "memory": {
                    "enabled": True,
                    "backend": "elasticsearch",
                    "index": "vss-memory",
                    "persist_by_default": True,
                },
            }
        ),
        encoding="utf-8",
    )
    memory_config = config_mod.load().memory
    assert memory_config is not None
    assert memory_config.markdown == config_mod.MarkdownMemoryConfig()


def test_configure_introspection_judge_round_trip_and_show(config_home: Path) -> None:
    criteria = "Require direct support from every cited record."
    result = _invoke(
        "introspection",
        "--judge-endpoint",
        "http://127.0.0.1:18789/v1",
        "--judge-model",
        "openclaw/default",
        "--judge-backend-model",
        "ollama/gemma3:12b",
        "--judge-api-key-env",
        "OPENCLAW_GATEWAY_TOKEN",
        "--judge-criteria",
        criteria,
    )

    assert result.exit_code == 0, result.output
    memory_config = config_mod.load().memory
    assert memory_config is not None
    assert memory_config.introspection == config_mod.IntrospectionMemoryConfig(
        judge=config_mod.IntrospectionJudgeConfig(
            endpoint="http://127.0.0.1:18789/v1",
            model="openclaw/default",
            backend_model="ollama/gemma3:12b",
            api_key_env="OPENCLAW_GATEWAY_TOKEN",
            criteria_prompt=criteria,
        )
    )
    shown = json.loads(_invoke("show").output)
    assert shown["introspection"]["judge"] == memory_config.introspection.judge.to_json()


def test_first_introspection_configuration_requires_only_endpoint(config_home: Path) -> None:
    missing = _invoke("introspection")
    assert missing.exit_code == int(Exit.INVALID_INPUT)
    assert "--judge-endpoint is required" in missing.output

    configured = _invoke("introspection", "--judge-endpoint", "http://127.0.0.1:18789/v1")
    assert configured.exit_code == 0, configured.output
    judge = config_mod.load().memory.introspection.judge  # type: ignore[union-attr]
    assert judge.model == "openclaw/default"
    assert judge.criteria_prompt == config_mod.DEFAULT_INTROSPECTION_CRITERIA_PROMPT
    assert judge.backend_model is None
    assert judge.api_key_env is None


def test_introspection_criteria_file_stores_utf8_contents(config_home: Path) -> None:
    criteria_file = config_home / "criteria.txt"
    criteria_file.write_text("Require direct evidence.\nPreserve uncertainty.\n", encoding="utf-8")

    result = _invoke(
        "introspection",
        "--judge-endpoint",
        "https://llm.example.com/v1",
        "--judge-criteria-file",
        str(criteria_file),
    )

    assert result.exit_code == 0, result.output
    judge = config_mod.load().memory.introspection.judge  # type: ignore[union-attr]
    assert judge.criteria_prompt == "Require direct evidence.\nPreserve uncertainty.\n"


def test_introspection_partial_updates_and_clear_flags(config_home: Path) -> None:
    assert (
        _invoke(
            "introspection",
            "--judge-endpoint",
            "http://127.0.0.1:18789/v1",
            "--judge-api-key-env",
            "OPENCLAW_GATEWAY_TOKEN",
            "--judge-backend-model",
            "ollama/gemma3:12b",
            "--judge-criteria",
            "Original criteria",
        ).exit_code
        == 0
    )
    assert _invoke("introspection", "--judge-model", "openclaw/research").exit_code == 0
    judge = config_mod.load().memory.introspection.judge  # type: ignore[union-attr]
    assert judge.endpoint == "http://127.0.0.1:18789/v1"
    assert judge.model == "openclaw/research"
    assert judge.api_key_env == "OPENCLAW_GATEWAY_TOKEN"
    assert judge.backend_model == "ollama/gemma3:12b"
    assert judge.criteria_prompt == "Original criteria"

    cleared = _invoke(
        "introspection",
        "--clear-judge-api-key-env",
        "--clear-judge-backend-model",
    )
    assert cleared.exit_code == 0, cleared.output
    judge = config_mod.load().memory.introspection.judge  # type: ignore[union-attr]
    assert judge.api_key_env is None
    assert judge.backend_model is None


@pytest.mark.parametrize(
    "args",
    (
        (
            "--judge-endpoint",
            "https://llm.example/v1",
            "--judge-criteria",
            "inline",
            "--judge-criteria-file",
            __file__,
        ),
        (
            "--judge-endpoint",
            "https://llm.example/v1",
            "--judge-backend-model",
            "model",
            "--clear-judge-backend-model",
        ),
        (
            "--judge-endpoint",
            "https://llm.example/v1",
            "--judge-api-key-env",
            "TOKEN",
            "--clear-judge-api-key-env",
        ),
    ),
)
def test_introspection_rejects_conflicting_options(config_home: Path, args: tuple[str, ...]) -> None:
    assert _invoke("introspection", *args).exit_code == int(Exit.INVALID_INPUT)


@pytest.mark.parametrize(
    ("updates", "message"),
    (
        ({"endpoint": ""}, "endpoint must be non-empty"),
        ({"model": ""}, "model must be non-empty"),
        ({"criteria_prompt": " "}, "criteria must be non-empty"),
        ({"api_key_env": "NOT-VALID"}, "environment variable"),
        ({"backend_model": ""}, "backend model must be non-empty"),
    ),
)
def test_introspection_judge_rejects_invalid_values(updates: dict[str, object], message: str) -> None:
    values: dict[str, object] = {
        "endpoint": "https://llm.example/v1",
        "model": "custom-model",
        "criteria_prompt": "Direct evidence only.",
    }
    values.update(updates)
    with pytest.raises(config_mod.ConfigError, match=message):
        config_mod.IntrospectionJudgeConfig(**values).validate()  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("raw", "message"),
    (
        ({"judge": {"endpoint": "https://llm.example/v1"}, "extra": True}, "memory.introspection"),
        ({"judge": {"endpoint": "https://llm.example/v1", "extra": True}}, "memory.introspection.judge"),
    ),
)
def test_introspection_config_rejects_unknown_fields(raw: dict[str, object], message: str) -> None:
    with pytest.raises(config_mod.ConfigError, match=message):
        config_mod.IntrospectionMemoryConfig.from_json(raw)


def test_old_memory_configuration_without_introspection_still_loads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(config_mod.CONFIG_HOME_ENV, str(tmp_path))
    tmp_path.joinpath("config.json").write_text(
        json.dumps(
            {
                "version": 1,
                "base_url": "http://example",
                "services": {"elasticsearch": {"url": "http://example/elasticsearch"}},
                "memory": {
                    "enabled": True,
                    "backend": "elasticsearch",
                    "index": "vss-memory",
                    "persist_by_default": True,
                    "markdown": {
                        "enabled": False,
                        "harness": "openclaw",
                        "workspace": None,
                        "write_by_default": False,
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    memory_config = config_mod.load().memory
    assert memory_config is not None
    assert memory_config.introspection is None


def test_show_never_resolves_or_displays_judge_secret(
    config_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CUSTOM_LLM_API_KEY", "super-secret-value")
    assert (
        _invoke(
            "introspection",
            "--judge-endpoint",
            "https://llm.example/v1",
            "--judge-api-key-env",
            "CUSTOM_LLM_API_KEY",
        ).exit_code
        == 0
    )

    shown = _invoke("show")
    assert shown.exit_code == 0
    assert "CUSTOM_LLM_API_KEY" in shown.output
    assert "super-secret-value" not in shown.output


def test_embeddings_select_openclaw_defaults_and_discover_dimensions(
    config_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probes: list[config_mod.EmbeddingConfig] = []

    def probe(config: config_mod.EmbeddingConfig) -> tuple[int, str | None]:
        probes.append(config)
        return 768, "resolved/backend-model"

    monkeypatch.setattr(configure_mod, "_probe_embedding", probe)
    result = _invoke("--embeddings")
    assert result.exit_code == 0, result.output
    embedding = config_mod.load().memory.embeddings  # type: ignore[union-attr]
    assert embedding == config_mod.EmbeddingConfig(enabled=True, dimensions=768)
    assert probes == [config_mod.EmbeddingConfig(enabled=True)]
    assert "discovered embedding dimensions: 768" in result.output


def test_custom_embedding_and_retrieval_configuration_round_trip(config_home: Path) -> None:
    result = _invoke(
        "--embeddings",
        "--embedding-provider",
        "openai_compatible",
        "--embedding-endpoint",
        "http://embedding.example/v1",
        "--embedding-model",
        "example-embedding-model",
        "--embedding-dimensions",
        "384",
        "--embedding-api-key-env",
        "VSS_EMBED_KEY",
        "--embedding-query-input-type",
        "query",
        "--embedding-document-input-type",
        "document",
        "--retrieval-mode",
        "semantic",
    )
    assert result.exit_code == 0, result.output
    memory_config = config_mod.load().memory
    assert memory_config is not None
    assert memory_config.embeddings == config_mod.EmbeddingConfig(
        enabled=True,
        provider="openai_compatible",
        endpoint="http://embedding.example/v1",
        model="example-embedding-model",
        dimensions=384,
        api_key_env="VSS_EMBED_KEY",
        query_input_type="query",
        document_input_type="document",
    )
    assert memory_config.retrieval == config_mod.RetrievalConfig(mode="semantic")
    assert config_mod.MemoryConfig.from_json(memory_config.to_json()) == memory_config
    assert config_mod.CONFIG_VERSION == 2


def test_disabled_embeddings_force_effective_keyword_retrieval() -> None:
    assert config_mod.MemoryConfig().retrieval.mode == "hybrid"
    assert config_mod.MemoryConfig().effective_retrieval_mode == "keyword"
    assert (
        config_mod.MemoryConfig(
            embeddings=config_mod.EmbeddingConfig(enabled=True, dimensions=768)
        ).effective_retrieval_mode
        == "hybrid"
    )


def test_custom_unauthenticated_endpoint_does_not_retain_openclaw_token(config_home: Path) -> None:
    result = _invoke(
        "--embeddings",
        "--embedding-provider",
        "openai_compatible",
        "--embedding-endpoint",
        "http://127.0.0.1:9000/v1",
        "--embedding-model",
        "local-custom-model",
        "--embedding-dimensions",
        "256",
        "--no-embedding-auth",
    )
    assert result.exit_code == 0, result.output
    embedding = config_mod.load().memory.embeddings  # type: ignore[union-attr]
    assert embedding.provider == "openai_compatible"
    assert embedding.api_key_env is None


def test_explicit_dimensions_allow_offline_configuration(
    config_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        configure_mod,
        "_probe_embedding",
        lambda *_args: pytest.fail("explicit dimensions must not probe the endpoint"),
    )
    result = _invoke(
        "--embeddings",
        "--embedding-provider",
        "openai_compatible",
        "--embedding-endpoint",
        "http://127.0.0.1:9000/v1",
        "--embedding-model",
        "offline-model",
        "--embedding-dimensions",
        "512",
        "--no-embedding-auth",
    )
    assert result.exit_code == 0, result.output
    assert config_mod.load().memory.embeddings.dimensions == 512  # type: ignore[union-attr]


def test_switching_provider_profiles_applies_new_defaults_before_overrides(config_home: Path) -> None:
    assert _invoke("--embeddings", "--embedding-dimensions", "768").exit_code == 0
    switched = _invoke(
        "--embedding-provider",
        "openai_compatible",
        "--embedding-endpoint",
        "https://embedding.example/v1",
        "--embedding-model",
        "custom-model",
        "--embedding-dimensions",
        "1024",
        "--no-embedding-auth",
    )
    assert switched.exit_code == 0, switched.output
    embedding = config_mod.load().memory.embeddings  # type: ignore[union-attr]
    assert embedding == config_mod.EmbeddingConfig(
        enabled=True,
        provider="openai_compatible",
        endpoint="https://embedding.example/v1",
        model="custom-model",
        dimensions=1024,
        api_key_env=None,
    )


@pytest.mark.parametrize(
    ("embeddings", "retrieval", "message"),
    [
        (
            config_mod.EmbeddingConfig(
                enabled=True,
                provider="openai_compatible",
                endpoint=None,
                model=None,
            ),
            config_mod.RetrievalConfig(),
            "require explicit",
        ),
        (
            config_mod.EmbeddingConfig(enabled=True, endpoint="ftp://example", dimensions=3),
            config_mod.RetrievalConfig(),
            "absolute HTTP",
        ),
        (
            config_mod.EmbeddingConfig(
                enabled=True,
                endpoint="http://user:" + "password@example",
                dimensions=3,
            ),
            config_mod.RetrievalConfig(),
            "embedded credentials",
        ),
        (config_mod.EmbeddingConfig(dimensions=0), config_mod.RetrievalConfig(), "positive integer"),
        (config_mod.EmbeddingConfig(timeout_seconds=0), config_mod.RetrievalConfig(), "timeout"),
        (config_mod.EmbeddingConfig(batch_size=129), config_mod.RetrievalConfig(), "batch size"),
        (config_mod.EmbeddingConfig(), config_mod.RetrievalConfig(mode="other"), "retrieval mode"),
        (config_mod.EmbeddingConfig(), config_mod.RetrievalConfig(candidate_count=0), "candidate count"),
        (config_mod.EmbeddingConfig(), config_mod.RetrievalConfig(rrf_rank_constant=0), "RRF"),
    ],
)
def test_invalid_embedding_and_retrieval_configuration(
    embeddings: config_mod.EmbeddingConfig,
    retrieval: config_mod.RetrievalConfig,
    message: str,
) -> None:
    with pytest.raises(config_mod.ConfigError, match=message):
        config_mod.MemoryConfig(embeddings=embeddings, retrieval=retrieval).validate()


def test_embedding_index_must_differ_from_authoritative_index() -> None:
    with pytest.raises(config_mod.ConfigError, match="must differ"):
        config_mod.MemoryConfig(
            embeddings=config_mod.EmbeddingConfig(
                enabled=True,
                dimensions=768,
                index="vss-memory",
            )
        ).validate()


def test_show_names_api_key_environment_without_resolving_secret(
    config_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VSS_EMBED_KEY", "do-not-print-this")
    assert _invoke("--embedding-api-key-env", "VSS_EMBED_KEY").exit_code == 0
    result = _invoke("show")
    assert "VSS_EMBED_KEY" in result.output
    assert "do-not-print-this" not in result.output


@pytest.mark.parametrize(
    ("args", "message"),
    (
        (("--embedding-provider", "unsupported"), "Invalid value"),
        (("--embedding-endpoint", "relative/path"), "absolute HTTP"),
        (("--embedding-endpoint", "https://user:" + "secret@example/v1"), "embedded credentials"),
        (("--embedding-dimensions", "0"), "positive integer"),
        (("--embedding-timeout-seconds", "0"), "timeout"),
        (("--embedding-batch-size", "0"), "batch size"),
        (("--embedding-api-key-env", "NOT-VALID"), "environment variable"),
    ),
)
def test_embedding_cli_rejects_invalid_profile_values(
    config_home: Path,
    args: tuple[str, ...],
    message: str,
) -> None:
    result = _invoke(*args)
    assert result.exit_code != 0
    assert message in result.output


def test_embedding_config_strictly_rejects_unknown_fields() -> None:
    raw = config_mod.EmbeddingConfig().to_json() | {"token": "must-not-be-accepted"}
    with pytest.raises(config_mod.ConfigError, match="unknown fields: token"):
        config_mod.EmbeddingConfig.from_json(raw)


def test_embedding_check_probes_once_and_reports_lazy_missing_index(
    config_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _invoke("--embeddings", "--embedding-dimensions", "768").exit_code == 0
    probes: list[str] = []

    class Provider:
        def __init__(self, **_kwargs: object) -> None:
            return None

        def embed_query(self, text: str) -> list[float]:
            probes.append(text)
            return [0.0] * 768

        @property
        def resolved_model(self) -> str:
            return "resolved/backend-model"

        def close(self) -> None:
            return None

    class Missing:
        status_code = 404

    monkeypatch.setattr("vss_core.memory.OpenAICompatibleEmbeddingProvider", Provider)
    monkeypatch.setattr(configure_mod, "_check_memory_backend", lambda *_args, **_kwargs: "elasticsearch reachable")
    monkeypatch.setattr("httpx.get", lambda *_args, **_kwargs: Missing())
    result = _invoke("check")
    assert result.exit_code == 0, result.output
    assert len(probes) == 1
    assert "target=openclaw/default" in result.output
    assert "resolved_model=resolved/backend-model" in result.output
    assert "created lazily" in result.output


def test_embedding_check_rejects_malformed_probe_as_backend_failure(
    config_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _invoke("--embeddings", "--embedding-dimensions", "768").exit_code == 0

    class Provider:
        def __init__(self, **_kwargs: object) -> None:
            return None

        def embed_query(self, _text: str) -> list[float]:
            from vss_core.memory import EmbeddingProviderError

            raise EmbeddingProviderError("malformed response")

        @property
        def resolved_model(self) -> None:
            return None

        def close(self) -> None:
            return None

    monkeypatch.setattr("vss_core.memory.OpenAICompatibleEmbeddingProvider", Provider)
    monkeypatch.setattr(configure_mod, "_check_memory_backend", lambda *_args, **_kwargs: "elasticsearch reachable")
    result = _invoke("check")
    assert result.exit_code == int(Exit.BACKEND_UNREACHABLE)
    assert "malformed response" in result.output


def test_embedding_check_reports_missing_credential_as_configuration_error(
    config_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _invoke("--embeddings", "--embedding-dimensions", "768").exit_code == 0

    class Provider:
        def __init__(self, **_kwargs: object) -> None:
            return None

        def embed_query(self, _text: str) -> list[float]:
            from vss_core.memory import EmbeddingProviderError

            raise EmbeddingProviderError("embedding API key environment variable 'OPENCLAW_GATEWAY_TOKEN' is not set")

        @property
        def resolved_model(self) -> None:
            return None

        def close(self) -> None:
            return None

    monkeypatch.setattr("vss_core.memory.OpenAICompatibleEmbeddingProvider", Provider)
    monkeypatch.setattr(configure_mod, "_check_memory_backend", lambda *_args, **_kwargs: "elasticsearch reachable")
    result = _invoke("check")
    assert result.exit_code == int(Exit.CONFIGURATION)
    assert "embedding credential error" in result.output
    assert "OPENCLAW_GATEWAY_TOKEN" in result.output
