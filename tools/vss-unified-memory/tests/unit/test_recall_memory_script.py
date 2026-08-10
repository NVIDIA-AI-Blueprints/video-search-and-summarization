# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
from datetime import datetime, timezone
from io import StringIO

from scripts.recall_memory import run_cli
from vss_unified_memory.application.models import MemorySearchResult, RecallMemoryResult
from vss_unified_memory.domain.models import MediaRef, Summary


class FakeRecallMemoryUseCase:
    def execute(self, query: object) -> RecallMemoryResult:
        summary = Summary("summary:1", "Stored summary", MediaRef("vst", "video-1"), datetime.now(timezone.utc), ())
        return RecallMemoryResult((MemorySearchResult(summary),))


def test_recall_memory_cli_success() -> None:
    stdin = StringIO('{"operation":"get","record_id":"summary:1","include_related":true}')
    stdout = StringIO()
    exit_code = run_cli(stdin, stdout, FakeRecallMemoryUseCase())  # type: ignore[arg-type]
    assert exit_code == 0
    output = json.loads(stdout.getvalue())
    assert output["results"][0]["memory"]["id"] == "summary:1"


def test_recall_rejects_raw_elasticsearch_dsl() -> None:
    stdin = StringIO('{"operation":"search","query":{"match_all":{}}}')
    stdout = StringIO()
    exit_code = run_cli(stdin, stdout, FakeRecallMemoryUseCase())  # type: ignore[arg-type]
    assert exit_code == 2
    assert json.loads(stdout.getvalue())["status"] == "failed"
