# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from vss_unified_memory.application.observability import (
    OperationTelemetry,
    append_observability_log,
    estimate_tokens_from_chars,
    query_text_observability_fields,
    safe_preview,
    sha256_hex,
)


def test_estimate_tokens_from_chars() -> None:
    assert estimate_tokens_from_chars(0) == 0
    assert estimate_tokens_from_chars(1) == 1
    assert estimate_tokens_from_chars(5) == 2


def test_query_text_observability_fields_hash_only_by_default() -> None:
    fields = query_text_observability_fields("secret forklift query", include_preview=False)
    assert fields["query_text_chars"] == 21
    assert fields["query_text_hash"] == sha256_hex("secret forklift query")
    assert "query_text_preview" not in fields


def test_query_text_observability_fields_can_include_preview() -> None:
    fields = query_text_observability_fields("secret forklift query", include_preview=True)
    assert fields["query_text_preview"] == "secret forklift query"


def test_safe_preview_truncates() -> None:
    text = "a" * 100
    preview = safe_preview(text, max_length=20)
    assert preview.endswith("...")
    assert len(preview) <= 20


def test_operation_telemetry_measure() -> None:
    telemetry = OperationTelemetry()
    with telemetry.measure("chunking"):
        pass
    assert telemetry.get_latency("chunking") is not None
    assert telemetry.get_latency("chunking") >= 0.0


def test_append_observability_log(tmp_path) -> None:
    log_path = tmp_path / "observability.jsonl"
    append_observability_log(
        log_path,
        tool_name="persist_summary",
        status="complete",
        summary_id="summary:1",
        observability={"event_count": 2},
    )
    line = log_path.read_text(encoding="utf-8").strip()
    assert '"tool_name":"persist_summary"' in line
    assert '"summary_id":"summary:1"' in line
