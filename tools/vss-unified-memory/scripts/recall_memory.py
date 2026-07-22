#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Recall VSS memory using a strict JSON query received on standard input."""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import TextIO


def _reexec_with_local_environment() -> None:
    venv_root = Path(__file__).resolve().parents[1] / ".venv"
    venv_python = venv_root / "bin" / "python"
    if venv_python.is_file() and Path(sys.prefix).resolve() != venv_root.resolve():
        os.execv(str(venv_python), [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]])


_reexec_with_local_environment()

from pydantic import TypeAdapter, ValidationError  # noqa: E402

from vss_unified_memory.adapters.cli.input_models import RecallMemoryInput  # noqa: E402
from vss_unified_memory.adapters.cli.mapper import (  # noqa: E402
    map_application_error,
    map_recall_input_to_query,
    map_recall_result_to_output,
    map_validation_error,
)
from vss_unified_memory.adapters.cli.output_models import ErrorOutput, OutputModel  # noqa: E402
from vss_unified_memory.adapters.embeddings.nvidia import NvidiaEmbeddingProvider  # noqa: E402
from vss_unified_memory.adapters.persistence.elasticsearch.repository import (  # noqa: E402
    ElasticsearchMemoryRepository,
)
from vss_unified_memory.application.errors import ApplicationError  # noqa: E402
from vss_unified_memory.application.observability import OperationTelemetry, append_observability_log  # noqa: E402
from vss_unified_memory.application.use_cases.recall_memory import RecallMemoryUseCase  # noqa: E402
from vss_unified_memory.config import Settings  # noqa: E402

logger = logging.getLogger(__name__)
recall_input_adapter = TypeAdapter(RecallMemoryInput)


def build_use_case(settings: Settings, telemetry: OperationTelemetry) -> RecallMemoryUseCase:
    repository = ElasticsearchMemoryRepository(
        endpoint=str(settings.elasticsearch_endpoint),
        index=settings.elasticsearch_index,
        embedding_dimensions=settings.embedding_dimensions,
        request_timeout_seconds=settings.request_timeout_seconds,
        telemetry=telemetry,
    )
    embedding_provider = NvidiaEmbeddingProvider(
        endpoint=str(settings.embedding_endpoint),
        model=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
        max_characters=settings.embedding_max_characters,
        timeout_seconds=settings.request_timeout_seconds,
    )
    return RecallMemoryUseCase(
        repository=repository,
        embedding_provider=embedding_provider,
        telemetry=telemetry,
        include_query_preview=settings.observability_include_previews,
    )


def _write_output(stdout: TextIO, output: OutputModel) -> None:
    stdout.write(output.model_dump_json() + "\n")


def _finalize_total_latency(result_output: OutputModel, total_ms: float) -> OutputModel:
    if not hasattr(result_output, "observability") or result_output.observability is None:
        return result_output
    observability = result_output.observability
    latency = observability.latency_ms.model_copy(update={"total": total_ms})
    return result_output.model_copy(update={"observability": observability.model_copy(update={"latency_ms": latency})})


def run_cli(
    stdin: TextIO,
    stdout: TextIO,
    recall_memory: RecallMemoryUseCase,
    *,
    observability_log: Path | None = None,
) -> int:
    started = time.perf_counter()
    record_id: str | None = None
    try:
        raw_json = json.load(stdin)
        input_model = recall_input_adapter.validate_python(raw_json)
        query = map_recall_input_to_query(input_model)
        record_id = query.record_id
        result = recall_memory.execute(query)
        output = _finalize_total_latency(map_recall_result_to_output(result), (time.perf_counter() - started) * 1000.0)
        _write_output(stdout, output)
        append_observability_log(
            observability_log,
            tool_name="recall_memory",
            status="complete",
            record_id=record_id,
            summary_id=record_id if record_id and record_id.startswith("summary:") else None,
            observability=output.observability.model_dump(mode="json") if output.observability else None,
        )
        return 0
    except (json.JSONDecodeError, ValidationError, ValueError) as error:
        error_output = map_validation_error(error, error_code="invalid_memory_query")
        _write_output(stdout, error_output)
        append_observability_log(
            observability_log,
            tool_name="recall_memory",
            status="failed",
            record_id=record_id,
        )
        return 2
    except ApplicationError as error:
        error_output = map_application_error(error)
        _write_output(stdout, error_output)
        append_observability_log(
            observability_log,
            tool_name="recall_memory",
            status="failed",
            record_id=record_id,
        )
        return error.exit_code
    except Exception:
        logger.exception("unexpected recall failure")
        _write_output(
            stdout,
            ErrorOutput(
                error_code="unexpected_error",
                message="unexpected recall failure",
                retryable=False,
            ),
        )
        append_observability_log(
            observability_log,
            tool_name="recall_memory",
            status="failed",
            record_id=record_id,
        )
        return 5


def main() -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    if len(sys.argv) != 1:
        _write_output(
            sys.stdout,
            ErrorOutput(
                error_code="invalid_invocation",
                message="command-line arguments are not accepted; provide JSON through standard input",
                retryable=False,
            ),
        )
        return 2
    try:
        settings = Settings()
        telemetry = OperationTelemetry()
        use_case = build_use_case(settings, telemetry)
    except ValidationError as error:
        _write_output(sys.stdout, map_validation_error(error, error_code="invalid_configuration"))
        return 2
    except Exception:
        logger.exception("failed to construct recall dependencies")
        _write_output(
            sys.stdout,
            ErrorOutput(
                error_code="configuration_error",
                message="failed to construct recall dependencies",
                retryable=False,
            ),
        )
        return 5
    return run_cli(
        stdin=sys.stdin,
        stdout=sys.stdout,
        recall_memory=use_case,
        observability_log=settings.observability_log,
    )


if __name__ == "__main__":
    raise SystemExit(main())
