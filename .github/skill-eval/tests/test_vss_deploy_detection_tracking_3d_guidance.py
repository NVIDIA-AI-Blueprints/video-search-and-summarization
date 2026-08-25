# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regression tests for the MV3DT routing summary contract."""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SKILL = REPO_ROOT / "skills/vss-deploy-detection-tracking-3d/SKILL.md"
EVAL_SPEC = REPO_ROOT / "skills/vss-deploy-detection-tracking-3d/evals/routing.json"
COMPOSE_PATH = "services/rtvi/rt-cv-3d/rt-cv-mv3dt/docker/compose.yml"


def test_routing_summary_is_available_in_skill_metadata() -> None:
    """The routing model must see the deployment summary before skill loading."""
    frontmatter = SKILL.read_text().split("---", 2)[1]

    assert COMPOSE_PATH in frontmatter
    for component in (
        "perception",
        "BEV Fusion",
        "bundled Mosquitto",
        "bundled Kafka",
        "kafka-topic-init",
    ):
        assert component in frontmatter


def test_routing_guidance_matches_the_behavioral_spec() -> None:
    """The skill and valid verifier should retain the same concrete contract."""
    skill = SKILL.read_text()
    first_case = json.loads(EVAL_SPEC.read_text())["expects"][0]
    contract = "\n".join(first_case["checks"])

    assert COMPOSE_PATH in skill
    assert COMPOSE_PATH in contract
    for component in (
        "perception",
        "BEV Fusion",
        "Mosquitto",
        "Kafka",
        "kafka-topic-init",
    ):
        assert component in skill
        assert component in contract
