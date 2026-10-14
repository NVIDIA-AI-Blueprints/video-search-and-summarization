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
"""Unit tests for critic_agent module."""

import json
from unittest.mock import AsyncMock

import pytest

from vss_agents.agents.critic_agent import CriticAgentConfig
from vss_agents.agents.critic_agent import CriticAgentInput
from vss_agents.agents.critic_agent import CriticAgentOutput
from vss_agents.agents.critic_agent import CriticAgentResult
from vss_agents.agents.critic_agent import VideoInfo
from vss_agents.agents.critic_agent import VideoResult
from vss_agents.agents.critic_agent import critic_agent
from vss_agents.agents.critic_agent import get_json_from_string
from vss_agents.tools.search import CriticResult

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_VIDEO_A = VideoInfo(
    sensor_id="sensor-001",
    start_timestamp="2025-08-25T03:05:55.000Z",
    end_timestamp="2025-08-25T03:06:15.000Z",
)
_VIDEO_B = VideoInfo(
    sensor_id="sensor-002",
    start_timestamp="2025-08-25T04:00:00.000Z",
    end_timestamp="2025-08-25T04:01:00.000Z",
)
_VIDEO_C = VideoInfo(
    sensor_id="sensor-003",
    start_timestamp="2025-08-25T05:00:00.000Z",
    end_timestamp="2025-08-25T05:01:00.000Z",
)


def _vlm_json(criteria: dict[str, bool], *, fence: bool = True) -> str:
    """Build a VLM-style JSON response, optionally wrapped in a markdown fence."""
    raw = json.dumps(criteria, indent=4)
    return f"```json\n{raw}\n```" if fence else raw


@pytest.fixture
def default_config() -> CriticAgentConfig:
    return CriticAgentConfig(
        _type="critic_agent",
        video_analysis_tool={"_type": "video_analysis"},
    )


@pytest.fixture
def config_no_tool() -> CriticAgentConfig:
    return CriticAgentConfig(_type="critic_agent", video_analysis_tool=None)


async def _build_execute_fn(config: CriticAgentConfig, mock_tool: AsyncMock | None) -> callable:
    """Instantiate the critic agent generator and return the inner _execute_critic function."""
    mock_builder = AsyncMock()
    mock_builder.get_function.return_value = mock_tool
    gen = critic_agent.__wrapped__(config, mock_builder)
    func_info = await gen.__anext__()
    return func_info.single_fn


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class TestGetJsonFromString:
    """Tests for the get_json_from_string helper used to parse VLM output."""

    def test_extracts_from_markdown_fence(self) -> None:
        raw = '```json\n{"subject:man": true, "blue shirt": true}\n```'
        parsed = json.loads(get_json_from_string(raw))
        assert parsed == {"subject:man": True, "blue shirt": True}

    def test_passthrough_without_fence(self) -> None:
        raw = '{"subject:woman": true, "picking up a box": false}'
        assert get_json_from_string(raw) == raw

    def test_extracts_with_surrounding_prose(self) -> None:
        raw = 'Here is the evaluation:\n```json\n{"running": false}\n```\nDone.'
        assert json.loads(get_json_from_string(raw)) == {"running": False}


# ---------------------------------------------------------------------------
# Critic agent endpoint tests (full _execute_critic flow)
# ---------------------------------------------------------------------------


class TestCriticAgentEndpoint:
    """Tests exercising the full _execute_critic flow with a mocked VLM tool."""

    @pytest.mark.asyncio
    async def test_single_video_confirmed(self, default_config: CriticAgentConfig) -> None:
        mock_tool = AsyncMock()
        mock_tool.ainvoke.return_value = _vlm_json({"subject:man": True, "blue shirt": True})
        execute = await _build_execute_fn(default_config, mock_tool)

        result: CriticAgentOutput = await execute(
            CriticAgentInput(query="Find a man in a blue shirt", videos=[_VIDEO_A])
        )

        assert len(result.video_results) == 1
        assert result.video_results[0].result == CriticAgentResult.CONFIRMED

    @pytest.mark.asyncio
    async def test_single_video_rejected(self, default_config: CriticAgentConfig) -> None:
        mock_tool = AsyncMock()
        mock_tool.ainvoke.return_value = _vlm_json({"subject:woman": True, "picking up a box": False})
        execute = await _build_execute_fn(default_config, mock_tool)

        result: CriticAgentOutput = await execute(
            CriticAgentInput(query="Find the woman picking up a box", videos=[_VIDEO_A])
        )

        assert len(result.video_results) == 1
        assert result.video_results[0].result == CriticAgentResult.REJECTED

    @pytest.mark.asyncio
    async def test_multiple_videos_mixed_results(self, default_config: CriticAgentConfig) -> None:
        mock_tool = AsyncMock()
        mock_tool.ainvoke.side_effect = [
            _vlm_json({"subject:man": True, "running": True}),
            _vlm_json({"subject:man": True, "running": False}),
            _vlm_json({"subject:man": True, "running": True}),
        ]
        execute = await _build_execute_fn(default_config, mock_tool)

        result: CriticAgentOutput = await execute(
            CriticAgentInput(query="Find a running man", videos=[_VIDEO_A, _VIDEO_B, _VIDEO_C])
        )

        assert len(result.video_results) == 3
        verdicts = [r.result for r in result.video_results]
        assert verdicts.count(CriticAgentResult.CONFIRMED) == 2
        assert verdicts.count(CriticAgentResult.REJECTED) == 1

    @pytest.mark.asyncio
    async def test_evaluation_count_limits_processing(self, default_config: CriticAgentConfig) -> None:
        mock_tool = AsyncMock()
        mock_tool.ainvoke.return_value = _vlm_json({"subject:person": True})
        execute = await _build_execute_fn(default_config, mock_tool)

        result: CriticAgentOutput = await execute(
            CriticAgentInput(query="Find a person", videos=[_VIDEO_A, _VIDEO_B, _VIDEO_C], evaluation_count=1)
        )

        assert len(result.video_results) == 1
        assert mock_tool.ainvoke.call_count == 1

    @pytest.mark.asyncio
    async def test_empty_videos_list(self, default_config: CriticAgentConfig) -> None:
        mock_tool = AsyncMock()
        execute = await _build_execute_fn(default_config, mock_tool)

        result: CriticAgentOutput = await execute(CriticAgentInput(query="Find anything", videos=[]))

        assert result.video_results == []
        mock_tool.ainvoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_partial_vlm_failures(self, default_config: CriticAgentConfig) -> None:
        """One VLM call succeeds, one fails — results contain both CONFIRMED and UNVERIFIED."""
        mock_tool = AsyncMock()
        mock_tool.ainvoke.side_effect = [
            _vlm_json({"subject:person": True}),
            RuntimeError("VLM timeout"),
        ]
        execute = await _build_execute_fn(default_config, mock_tool)

        result: CriticAgentOutput = await execute(CriticAgentInput(query="Find a person", videos=[_VIDEO_A, _VIDEO_B]))

        result_types = {r.result for r in result.video_results}
        assert CriticAgentResult.CONFIRMED in result_types
        assert CriticAgentResult.UNVERIFIED in result_types

    @pytest.mark.asyncio
    async def test_no_tool_configured_returns_unverified(self, config_no_tool: CriticAgentConfig) -> None:
        execute = await _build_execute_fn(config_no_tool, None)

        result: CriticAgentOutput = await execute(CriticAgentInput(query="Find anything", videos=[_VIDEO_A]))

        assert len(result.video_results) == 1
        assert result.video_results[0].result == CriticAgentResult.UNVERIFIED

    @pytest.mark.asyncio
    async def test_invalid_json_returns_unverified(self, default_config: CriticAgentConfig) -> None:
        mock_tool = AsyncMock()
        mock_tool.ainvoke.return_value = "this is definitely not valid json"
        execute = await _build_execute_fn(default_config, mock_tool)

        result: CriticAgentOutput = await execute(CriticAgentInput(query="Find a car", videos=[_VIDEO_A]))

        assert len(result.video_results) == 1
        assert result.video_results[0].result == CriticAgentResult.UNVERIFIED


# ---------------------------------------------------------------------------
# Criteria-met response tests
# ---------------------------------------------------------------------------


class TestCriteriaMet:
    """Tests focused on how criteria_met is populated in the critic agent response."""

    @pytest.mark.asyncio
    async def test_all_criteria_true_returns_full_dict(self, default_config: CriticAgentConfig) -> None:
        criteria = {"subject:man": True, "blue shirt": True, "dark pants": True}
        mock_tool = AsyncMock()
        mock_tool.ainvoke.return_value = _vlm_json(criteria)
        execute = await _build_execute_fn(default_config, mock_tool)

        result: CriticAgentOutput = await execute(
            CriticAgentInput(query="Find a man in blue shirt and dark pants", videos=[_VIDEO_A])
        )

        vr = result.video_results[0]
        assert vr.result == CriticAgentResult.CONFIRMED
        assert vr.criteria_met == criteria

    @pytest.mark.asyncio
    async def test_failed_criterion_shows_which_failed(self, default_config: CriticAgentConfig) -> None:
        criteria = {"subject:person": True, "running": False, "green jacket": True}
        mock_tool = AsyncMock()
        mock_tool.ainvoke.return_value = _vlm_json(criteria)
        execute = await _build_execute_fn(default_config, mock_tool)

        result: CriticAgentOutput = await execute(
            CriticAgentInput(query="Find the running person in a green jacket", videos=[_VIDEO_A])
        )

        vr = result.video_results[0]
        assert vr.result == CriticAgentResult.REJECTED
        assert vr.criteria_met["running"] is False
        assert vr.criteria_met["green jacket"] is True
        assert vr.criteria_met["subject:person"] is True

    @pytest.mark.asyncio
    async def test_relational_failure_pattern(self, default_config: CriticAgentConfig) -> None:
        """Subject present and attribute matches, but action bound to wrong entity."""
        criteria = {"subject:player": True, "red": True, "makes a basket": False}
        mock_tool = AsyncMock()
        mock_tool.ainvoke.return_value = _vlm_json(criteria)
        execute = await _build_execute_fn(default_config, mock_tool)

        result: CriticAgentOutput = await execute(
            CriticAgentInput(query="Find the red team player making a basket", videos=[_VIDEO_A])
        )

        vr = result.video_results[0]
        assert vr.result == CriticAgentResult.REJECTED
        assert vr.criteria_met["subject:player"] is True
        assert vr.criteria_met["red"] is True
        assert vr.criteria_met["makes a basket"] is False

    @pytest.mark.asyncio
    async def test_empty_criteria_dict_is_confirmed(self, default_config: CriticAgentConfig) -> None:
        """Empty criteria dict means nothing to fail — vacuous truth → CONFIRMED."""
        mock_tool = AsyncMock()
        mock_tool.ainvoke.return_value = _vlm_json({})
        execute = await _build_execute_fn(default_config, mock_tool)

        result: CriticAgentOutput = await execute(CriticAgentInput(query="Anything", videos=[_VIDEO_A]))

        vr = result.video_results[0]
        assert vr.result == CriticAgentResult.CONFIRMED
        assert vr.criteria_met == {}

    @pytest.mark.asyncio
    async def test_vlm_error_criteria_met_is_empty_dict(self, default_config: CriticAgentConfig) -> None:
        mock_tool = AsyncMock()
        mock_tool.ainvoke.side_effect = RuntimeError("service down")
        execute = await _build_execute_fn(default_config, mock_tool)

        result: CriticAgentOutput = await execute(CriticAgentInput(query="Find a car", videos=[_VIDEO_A]))

        vr = result.video_results[0]
        assert vr.result == CriticAgentResult.UNVERIFIED
        assert vr.criteria_met == {}

    @pytest.mark.asyncio
    async def test_no_tool_criteria_met_is_empty_dict(self, config_no_tool: CriticAgentConfig) -> None:
        execute = await _build_execute_fn(config_no_tool, None)

        result: CriticAgentOutput = await execute(CriticAgentInput(query="Find a car", videos=[_VIDEO_A]))

        vr = result.video_results[0]
        assert vr.result == CriticAgentResult.UNVERIFIED
        assert vr.criteria_met == {}

    @pytest.mark.asyncio
    async def test_raw_json_without_fence_parses(self, default_config: CriticAgentConfig) -> None:
        """VLM returns plain JSON (no markdown fence) — criteria_met should still populate."""
        criteria = {"subject:person": True, "green jacket": True}
        mock_tool = AsyncMock()
        mock_tool.ainvoke.return_value = _vlm_json(criteria, fence=False)
        execute = await _build_execute_fn(default_config, mock_tool)

        result: CriticAgentOutput = await execute(
            CriticAgentInput(query="Find a person in a green jacket", videos=[_VIDEO_A])
        )

        vr = result.video_results[0]
        assert vr.result == CriticAgentResult.CONFIRMED
        assert vr.criteria_met == criteria


# ---------------------------------------------------------------------------
# Verdicts for the response shapes CRITIC_AGENT_PROMPT asks the VLM to produce
# ---------------------------------------------------------------------------


class TestPromptDerivedVerdicts:
    """Verdicts for the response shapes CRITIC_AGENT_PROMPT asks the VLM to produce.

    The prompt derives criteria from the query before the clip is seen, reports an absent
    subject as a single false subject criterion, and treats an absence the *user* asked
    for as a criterion in its own right. These pin what the critic does with each shape.
    Whether a given model obeys the prompt is an eval question against a live VLM, not
    something a unit test can answer.
    """

    @pytest.mark.asyncio
    async def test_absent_subject_yields_rejected(self, default_config: CriticAgentConfig) -> None:
        mock_tool = AsyncMock()
        mock_tool.ainvoke.return_value = _vlm_json({"subject:dog": False})
        execute = await _build_execute_fn(default_config, mock_tool)

        result: CriticAgentOutput = await execute(
            CriticAgentInput(query="Find the dog chasing a ball", videos=[_VIDEO_A])
        )

        vr = result.video_results[0]
        assert vr.result == CriticAgentResult.REJECTED
        assert vr.criteria_met == {"subject:dog": False}

    @pytest.mark.asyncio
    async def test_user_requested_absence_unsatisfied_yields_rejected(self, default_config: CriticAgentConfig) -> None:
        """The subject wears a helmet, so the user-requested absence is unsatisfied."""
        mock_tool = AsyncMock()
        mock_tool.ainvoke.return_value = _vlm_json({"subject:person": True, "without a helmet": False})
        execute = await _build_execute_fn(default_config, mock_tool)

        result: CriticAgentOutput = await execute(
            CriticAgentInput(query="Find a person without a helmet", videos=[_VIDEO_A])
        )

        vr = result.video_results[0]
        assert vr.result == CriticAgentResult.REJECTED
        assert vr.criteria_met["without a helmet"] is False

    @pytest.mark.asyncio
    async def test_user_requested_absence_satisfied_yields_confirmed(self, default_config: CriticAgentConfig) -> None:
        mock_tool = AsyncMock()
        mock_tool.ainvoke.return_value = _vlm_json({"subject:person": True, "without a helmet": True})
        execute = await _build_execute_fn(default_config, mock_tool)

        result: CriticAgentOutput = await execute(
            CriticAgentInput(query="Find a person without a helmet", videos=[_VIDEO_A])
        )

        assert result.video_results[0].result == CriticAgentResult.CONFIRMED

    @pytest.mark.asyncio
    async def test_query_and_criteria_rules_reach_the_vlm(self, default_config: CriticAgentConfig) -> None:
        mock_tool = AsyncMock()
        mock_tool.ainvoke.return_value = _vlm_json({"subject:dog": False})
        execute = await _build_execute_fn(default_config, mock_tool)

        await execute(CriticAgentInput(query="Find the dog chasing a ball", videos=[_VIDEO_A]))

        prompt = mock_tool.ainvoke.call_args.args[0]["user_prompt"]
        assert "Find the dog chasing a ball" in prompt
        assert "Derive the criteria from the user prompt ALONE" in prompt
        assert "An absence the USER asked for is a legitimate criterion" in prompt

    @pytest.mark.asyncio
    @pytest.mark.xfail(
        strict=True,
        reason="The criteria keys are still chosen by the VLM, so a clip it merely "
        "describes accurately aggregates as a match. Binding the key set in code is the "
        "fix; delete this marker when that lands.",
    )
    async def test_accurately_described_clip_is_not_confirmed(self, default_config: CriticAgentConfig) -> None:
        """The response captured in NVBug 6402815: warehouse footage for a fruit query.

        Every criterion is true -- including the one reporting that no fruit is present --
        so strict-AND aggregation confirms it. Asserts only "not confirmed" because either
        candidate fix (reject, or unverified on a key-set mismatch) satisfies the contract.
        """
        mock_tool = AsyncMock()
        mock_tool.ainvoke.return_value = _vlm_json(
            {
                "subject:worker": True,
                "wearing yellow hard hat": True,
                "standing on ladder": True,
                "no fruit or trees visible": True,
            }
        )
        execute = await _build_execute_fn(default_config, mock_tool)

        result: CriticAgentOutput = await execute(
            CriticAgentInput(query="show fruits falling from tree", videos=[_VIDEO_A])
        )

        assert result.video_results[0].result != CriticAgentResult.CONFIRMED


# ---------------------------------------------------------------------------
# CriticResult mapping tests (VideoResult → CriticResult used by search)
# ---------------------------------------------------------------------------


class TestCriticResultMapping:
    """Tests for CriticResult construction from VideoResult, mirroring search.py mapping logic."""

    def test_confirmed_video_result_maps_correctly(self) -> None:
        vr = VideoResult(
            video_info=_VIDEO_A,
            result=CriticAgentResult.CONFIRMED,
            criteria_met={"subject:man": True, "blue shirt": True},
        )
        cr = CriticResult(result=vr.result.value, criteria_met=vr.criteria_met or {})

        assert cr.result == "confirmed"
        assert cr.criteria_met == {"subject:man": True, "blue shirt": True}

    def test_rejected_video_result_maps_correctly(self) -> None:
        vr = VideoResult(
            video_info=_VIDEO_A,
            result=CriticAgentResult.REJECTED,
            criteria_met={"subject:woman": True, "picking up a box": False},
        )
        cr = CriticResult(result=vr.result.value, criteria_met=vr.criteria_met or {})

        assert cr.result == "rejected"
        assert cr.criteria_met["picking up a box"] is False

    def test_unverified_with_none_criteria_maps_to_empty_dict(self) -> None:
        vr = VideoResult(
            video_info=_VIDEO_A,
            result=CriticAgentResult.UNVERIFIED,
            criteria_met=None,
        )
        cr = CriticResult(result=vr.result.value, criteria_met=vr.criteria_met or {})

        assert cr.result == "unverified"
        assert cr.criteria_met == {}
