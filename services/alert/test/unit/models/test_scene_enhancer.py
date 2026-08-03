# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import json
from pathlib import Path

import pytest


_PARSER_PATH = (
    Path(__file__).resolve().parents[5]
    / "deploy/docker/industry-profiles/smartcities/vlm-as-verifier/parsers/scene_enhancer.py"
)
_SPEC = importlib.util.spec_from_file_location("smartcities_scene_enhancer", _PARSER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
SceneEnhancer = _MODULE.SceneEnhancer


def test_scene_enhancer_parses_fenced_json():
    parser = SceneEnhancer()

    assert parser.parse(
        '```json\n'
        '{"verdict":"yes",'
        '"description":"Confirmed collision between orange vehicle 118 and black vehicle 92",'
        '"risk_level":"high",'
        '"recommended_action":"Dispatch responders"}'
        '\n```'
    ) == {
        "verdict": "Yes",
        "description": "Confirmed collision between orange vehicle 118 and black vehicle 92",
        "risk_level": "high",
        "recommended_action": "Dispatch responders",
    }


def test_scene_enhancer_rejects_malformed_json():
    parser = SceneEnhancer()

    with pytest.raises(json.JSONDecodeError):
        parser.parse(
            '{"verdict":"No" '
            '"description":"Rejected collision between blue vehicle 1 and white vehicle 2" '
            '"risk_level":"low" "recommended_action":"Continue monitoring"}'
        )


def test_scene_enhancer_rejects_missing_required_field():
    parser = SceneEnhancer()

    with pytest.raises(ValueError, match="recommended_action"):
        parser.parse(
            '{"verdict":"Yes",'
            '"description":"Confirmed collision between two vehicles",'
            '"risk_level":"high"}'
        )


def test_scene_enhancer_rejects_non_binary_verdict():
    parser = SceneEnhancer()

    with pytest.raises(ValueError, match="verdict"):
        parser.parse(
            '{"verdict":"vehicle_collision",'
            '"description":"Confirmed collision between two vehicles",'
            '"risk_level":"high",'
            '"recommended_action":"Dispatch responders"}'
        )
