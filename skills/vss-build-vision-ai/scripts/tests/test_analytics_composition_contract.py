# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regression contract for NemoClaw read-only analytics ownership."""

import json
from pathlib import Path


SKILLS_ROOT = Path(__file__).resolve().parents[3]
BUILD_SKILL = SKILLS_ROOT / "vss-build-vision-ai"
QUERY_SKILL = SKILLS_ROOT / "operations" / "vss-query-analytics"


def test_nemoclaw_analytics_owns_only_the_api_service() -> None:
    owner = (BUILD_SKILL / "references/services/video-analytics-api.md").read_text()
    agent_owner = (BUILD_SKILL / "references/services/agent.md").read_text()
    skill = (QUERY_SKILL / "SKILL.md").read_text()

    assert 'vss-requires: "analytics"' in skill
    assert "`vss-video-analytics-api`" in owner
    assert "excludes `vss-va-mcp` and `vss-agent`" in owner
    assert "A request for read-only" in agent_owner
    assert "is not an MCP or Agent request" in agent_owner

    regression = json.loads(
        (BUILD_SKILL / "eval/vdr_3_nemoclaw_read_only_analytics.json").read_text()
    )
    expected = regression["expects"][0]["expected_services"]
    assert expected == {
        "included": ["vss-video-analytics-api"],
        "excluded": ["vss-va-mcp", "vss-agent"],
    }


def test_explicit_legacy_mcp_selection_remains_available() -> None:
    agent_owner = (BUILD_SKILL / "references/services/agent.md").read_text()

    assert "Explicit legacy video-analytics MCP surface" in agent_owner
    assert "Add `vss-va-mcp` only for agent configurations that use video-analytics MCP." in agent_owner


def test_query_skill_uses_cli_without_legacy_endpoint_commands() -> None:
    skill = (QUERY_SKILL / "SKILL.md").read_text()

    assert "vss analytics incidents" in skill
    assert "vss analytics sensors" in skill
    assert "vss vios list" in skill
    for forbidden in ("9901", "/va-mcp", "VA_MCP_URL", "method=initialize"):
        assert forbidden not in skill
