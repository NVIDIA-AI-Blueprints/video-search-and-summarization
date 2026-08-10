# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
from io import StringIO
from pathlib import Path

from scripts.persist_summary import run_cli
from vss_unified_memory.application.models import PersistSummaryResult, WriteStatus
from vss_unified_memory.domain.models import Summary

FIXTURE = Path(__file__).parents[1] / "fixtures" / "vss_summary_input.json"


class FakePersistSummaryUseCase:
    def execute(self, summary: Summary) -> PersistSummaryResult:
        return PersistSummaryResult(
            status=WriteStatus.COMPLETE,
            summary_id=summary.id,
            event_ids=tuple(event.id for event in summary.events),
            attempted_records=3,
            successful_records=3,
        )


def test_persist_summary_cli_success() -> None:
    stdin = StringIO(FIXTURE.read_text())
    stdout = StringIO()
    exit_code = run_cli(stdin, stdout, FakePersistSummaryUseCase())  # type: ignore[arg-type]
    assert exit_code == 0
    output = json.loads(stdout.getvalue())
    assert output["status"] == "complete"
    assert output["summary_id"].startswith("summary:")


def test_persist_summary_cli_validation_failure() -> None:
    stdout = StringIO()
    exit_code = run_cli(StringIO("{}"), stdout, FakePersistSummaryUseCase())  # type: ignore[arg-type]
    assert exit_code == 2
    output = json.loads(stdout.getvalue())
    assert output["error_code"] == "invalid_summary_input"
    assert output["retryable"] is False
