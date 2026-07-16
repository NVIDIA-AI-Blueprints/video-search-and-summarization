# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path

from vss_unified_memory.adapters.cli.input_models import PersistSummaryInput
from vss_unified_memory.adapters.cli.mapper import map_input_to_summary

FIXTURE = Path(__file__).parents[1] / "fixtures" / "vss_summary_input.json"


def test_mapper_assigns_deterministic_ids_and_ordinals() -> None:
    input_model = PersistSummaryInput.model_validate_json(FIXTURE.read_text())
    first = map_input_to_summary(input_model)
    second = map_input_to_summary(input_model)
    assert first == second
    assert first.id == "summary:11111111-1111-4111-8111-111111111111"
    assert [event.ordinal for event in first.events] == [1, 2]
    assert first.events[0].id == "event:11111111-1111-4111-8111-111111111111:0001"
