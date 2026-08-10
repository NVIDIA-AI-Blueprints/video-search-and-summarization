# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from vss_unified_memory.adapters.cli.input_models import PersistSummaryInput

FIXTURE = Path(__file__).parents[1] / "fixtures" / "vss_summary_input.json"


def test_valid_summary_input() -> None:
    model = PersistSummaryInput.model_validate_json(FIXTURE.read_text())
    assert len(model.content.events) == 2


def test_unknown_input_field_is_rejected() -> None:
    payload = json.loads(FIXTURE.read_text())
    payload["elasticsearch_index"] = "attacker-controlled"
    with pytest.raises(ValidationError, match="Extra inputs"):
        PersistSummaryInput.model_validate(payload)


def test_reversed_event_range_is_rejected() -> None:
    payload = json.loads(FIXTURE.read_text())
    payload["content"]["events"][0]["end_time"] = 1
    with pytest.raises(ValidationError, match="end_time"):
        PersistSummaryInput.model_validate(payload)
