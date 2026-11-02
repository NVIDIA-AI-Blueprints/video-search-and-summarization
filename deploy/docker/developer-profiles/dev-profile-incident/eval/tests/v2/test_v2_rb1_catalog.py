# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""RB1 (read-only endpoint checks, 2026-11-01): the capability registry agrees with the recorded model listings."""

from __future__ import annotations

import json
from pathlib import Path

from evalv2.adapters.registry import load_capabilities
from evalv2.tasks.chat import scoring as chat_scoring

CATALOG = json.loads((Path(__file__).resolve().parents[2] / "evalv2" / "adapters" / "catalog_rb1.json").read_text())
ENDPOINTS = CATALOG["endpoints"]


def test_rb1_was_read_only_and_recorded_with_hashes():
    assert "No inference, embedding or other billable request" in CATALOG["rb1"]["method"]
    for endpoint in ENDPOINTS.values():
        assert endpoint["status"] == 200 and len(endpoint["response_sha256"]) == 64 and endpoint["models"]


def test_every_registered_model_is_listed_where_the_registry_says():
    for model, capabilities in load_capabilities().items():
        assert capabilities.listed_on, f"{model}: no endpoint listed it in RB1"
        for endpoint in capabilities.listed_on:
            assert model in ENDPOINTS[endpoint]["models"], (model, endpoint)
        assert not capabilities.verified, f"{model}: RB1 was read-only, so no capability is verified yet"


def test_the_allowlisted_p1_models_and_the_chat_default_are_on_switchyard():
    allowlist = ["nvidia/cosmos-3-nano-reasoner", "nvidia/cosmos-3-super-reasoner",
                 "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"]
    assert set(allowlist) <= set(ENDPOINTS["switchyard"]["models"])
    assert "nvidia/nemotron-3-ultra" in ENDPOINTS["switchyard"]["models"], "P5's DEFAULT_MODEL is a Switchyard id"
    assert "nvidia/nemotron-3-ultra" not in ENDPOINTS["nvidia-api-catalog"]["models"]


def test_a_third_family_judge_exists_only_on_the_catalog():
    """X19/X18: Switchyard has only NVIDIA families, so a judge outside the subjects' and generators' families needs
    the catalog (and its key)."""
    families = {chat_scoring.model_family(m) for m in ENDPOINTS["switchyard"]["models"]}
    assert families <= {"nemotron", "cosmos"}, families
    judges = [m for m in ENDPOINTS["nvidia-api-catalog"]["models"]
              if chat_scoring.model_family(m) in ("gpt", "mistral", "gemini") or m.startswith(("google/gemma",
                                                                                                "moonshotai/"))]
    assert "openai/gpt-oss-20b" in judges
    for judge in judges[:1]:
        chat_scoring.check_judge_family(judge, subjects=["nvidia/cosmos-3-super-reasoner",
                                                         "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"],
                                        generators=["claude-opus"])
