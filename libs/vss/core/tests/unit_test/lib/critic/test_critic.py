# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Contract tests for lib.critic.CriticAgent.

Locks in the behaviors `/api/v1/critic` depends on:
  - All criteria True → CONFIRMED
  - Any criterion False → REJECTED
  - Parse failure → UNVERIFIED (recoverable; never propagates as exception)
  - ```json fenced output is unwrapped
  - evaluation_count caps the number of VLM calls
  - Empty sensor_id entries are skipped
  - time_format='offset' converts ISO timestamps to seconds-since-stream-start
    via VST resolve_stream_id + get_timeline; clamps to clip end

EXPERIMENTAL in v1 (DESIGN.md §6.4) — these tests pin the current behavior;
the contract may evolve before v1 stable.
"""

from __future__ import annotations

from datetime import UTC
from datetime import datetime

from pydantic import ValidationError
import pytest

from vss_core._foundation.errors import BackendUnreachableError
from vss_core._foundation.errors import ConfigurationError
from vss_core.critic import CriticAgent
from vss_core.critic import VideoInfo
from vss_core.critic.models import CriticAgentInput
from vss_core.critic.models import CriticAgentResult
from vss_core.vios import VSTError

# ---------------------------------------------------------------------- mocks


class _FakeVST:
    def __init__(
        self,
        *,
        timeline: tuple[str, str] = ("2025-01-01T00:00:00Z", "2025-01-01T00:01:00Z"),
    ) -> None:
        self._timeline = timeline

    def build_screenshot_url(self, *, sensor_id, timestamp, internal=False) -> str:
        return ""

    async def resolve_stream_id(self, sensor_id: str) -> str:
        return f"stream-{sensor_id}"

    async def get_timeline(self, stream_id: str) -> tuple[str, str]:
        return self._timeline

    async def aclose(self) -> None:
        return None


class _FakeVLM:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[dict] = []

    async def analyze(
        self,
        *,
        sensor_id: str,
        start_timestamp: str,
        end_timestamp: str,
        prompt: str,
        time_format: str = "iso",
    ) -> str:
        self.calls.append(
            {
                "sensor_id": sensor_id,
                "start_timestamp": start_timestamp,
                "end_timestamp": end_timestamp,
                "time_format": time_format,
            }
        )
        return self.response


class _FailingVLM:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def analyze(self, **kwargs) -> str:
        raise self.error


class _PerSensorVLM:
    async def analyze(self, *, sensor_id: str, **kwargs) -> str:
        if sensor_id == "bad":
            raise ConfigurationError("clip-specific request rejected")
        return '{"subject:forklift": true}'


def _video(sensor_id: str = "cam01", *, start=10, end=20, source_type: str | None = None) -> VideoInfo:
    return VideoInfo(
        sensor_id=sensor_id,
        start_timestamp=datetime(2025, 1, 1, 0, 0, start, tzinfo=UTC),
        end_timestamp=datetime(2025, 1, 1, 0, 0, end, tzinfo=UTC),
        source_type=source_type,
    )


# ---------------------------------------------------------------------- tests


class TestCriticVerdict:
    @pytest.mark.asyncio
    async def test_all_criteria_true_yields_confirmed(self):
        vlm = _FakeVLM('{"person": true, "walking": true}')
        c = CriticAgent(vlm_analyzer=vlm, vst=_FakeVST())
        out = await c.run(CriticAgentInput(query="q", videos=[_video()]))
        assert out.video_results[0].result == CriticAgentResult.CONFIRMED
        assert out.video_results[0].criteria_met == {"person": True, "walking": True}

    @pytest.mark.asyncio
    async def test_any_criterion_false_yields_rejected(self):
        vlm = _FakeVLM('{"person": true, "walking": false}')
        c = CriticAgent(vlm_analyzer=vlm, vst=_FakeVST())
        out = await c.run(CriticAgentInput(query="q", videos=[_video()]))
        assert out.video_results[0].result == CriticAgentResult.REJECTED

    @pytest.mark.asyncio
    async def test_nested_criteria_met_shape_yields_rejected(self):
        vlm = _FakeVLM('{"result": "rejected", "criteria_met": {"running": false}}')
        c = CriticAgent(vlm_analyzer=vlm, vst=_FakeVST())
        out = await c.run(CriticAgentInput(query="q", videos=[_video()]))
        assert out.video_results[0].result == CriticAgentResult.REJECTED
        assert out.video_results[0].criteria_met == {"running": False}


class TestCriticErrors:
    @pytest.mark.asyncio
    async def test_empty_json_is_unverified(self):
        c = CriticAgent(vlm_analyzer=_FakeVLM("{}"), vst=_FakeVST())
        out = await c.run(CriticAgentInput(query="q", videos=[_video()]))
        assert out.video_results[0].result == CriticAgentResult.UNVERIFIED

    @pytest.mark.asyncio
    async def test_string_boolean_is_unverified(self):
        c = CriticAgent(vlm_analyzer=_FakeVLM('{"running": "false"}'), vst=_FakeVST())
        out = await c.run(CriticAgentInput(query="q", videos=[_video()]))
        assert out.video_results[0].result == CriticAgentResult.UNVERIFIED

    @pytest.mark.asyncio
    async def test_backend_failure_yields_unverified(self):
        error = BackendUnreachableError("vlm", "temporarily unavailable")
        critic = CriticAgent(vlm_analyzer=_FailingVLM(error), vst=_FakeVST())

        out = await critic.run(CriticAgentInput(query="q", videos=[_video()]))

        assert out.video_results[0].result == CriticAgentResult.UNVERIFIED
        assert out.video_results[0].criteria_met == {}

    @pytest.mark.asyncio
    async def test_vst_failure_yields_per_video_unverified(self):
        class DeadVST(_FakeVST):
            async def resolve_stream_id(self, sensor_id: str) -> str:
                raise VSTError(f"cannot resolve {sensor_id}")

        critic = CriticAgent(vlm_analyzer=_FakeVLM('{"object": true}'), vst=DeadVST(), time_format="offset")

        out = await critic.run(CriticAgentInput(query="q", videos=[_video()]))

        assert out.video_results[0].result == CriticAgentResult.UNVERIFIED
        assert out.video_results[0].criteria_met == {}

    @pytest.mark.asyncio
    async def test_configuration_error_yields_unverified(self):
        error = ConfigurationError("missing VLM model")
        critic = CriticAgent(vlm_analyzer=_FailingVLM(error), vst=_FakeVST())

        out = await critic.run(CriticAgentInput(query="q", videos=[_video()]))

        assert out.video_results[0].result == CriticAgentResult.UNVERIFIED
        assert out.video_results[0].criteria_met == {}

    @pytest.mark.asyncio
    async def test_untyped_analyzer_error_yields_unverified(self):
        error = RuntimeError("analyzer contract violation")
        critic = CriticAgent(vlm_analyzer=_FailingVLM(error), vst=_FakeVST())

        out = await critic.run(CriticAgentInput(query="q", videos=[_video()]))

        assert out.video_results[0].result == CriticAgentResult.UNVERIFIED
        assert out.video_results[0].criteria_met == {}

    @pytest.mark.asyncio
    async def test_one_unexpected_failure_preserves_other_verdicts(self):
        critic = CriticAgent(vlm_analyzer=_PerSensorVLM(), vst=_FakeVST())

        out = await critic.run(
            CriticAgentInput(
                query="forklift",
                videos=[_video("good"), _video("bad"), _video("also-good")],
            )
        )

        assert [result.result for result in out.video_results] == [
            CriticAgentResult.CONFIRMED,
            CriticAgentResult.UNVERIFIED,
            CriticAgentResult.CONFIRMED,
        ]

    @pytest.mark.asyncio
    async def test_parse_failure_yields_unverified(self):
        vlm = _FakeVLM("not even json")
        c = CriticAgent(vlm_analyzer=vlm, vst=_FakeVST())
        out = await c.run(CriticAgentInput(query="q", videos=[_video()]))
        assert out.video_results[0].result == CriticAgentResult.UNVERIFIED
        assert out.video_results[0].criteria_met == {}

    @pytest.mark.asyncio
    async def test_json_fence_is_unwrapped(self):
        vlm = _FakeVLM('```json\n{"x": true}\n```')
        c = CriticAgent(vlm_analyzer=vlm, vst=_FakeVST())
        out = await c.run(CriticAgentInput(query="q", videos=[_video()]))
        assert out.video_results[0].result == CriticAgentResult.CONFIRMED

    @pytest.mark.asyncio
    async def test_answer_wrapper_is_unwrapped(self):
        vlm = _FakeVLM('<think>reasoning</think><answer>{"person": true, "running": true}</answer>')
        c = CriticAgent(vlm_analyzer=vlm, vst=_FakeVST())
        out = await c.run(CriticAgentInput(query="q", videos=[_video()]))
        assert out.video_results[0].result == CriticAgentResult.CONFIRMED
        assert out.video_results[0].criteria_met == {"person": True, "running": True}

    @pytest.mark.asyncio
    async def test_json_object_is_extracted_from_text(self):
        vlm = _FakeVLM('Final answer: {"person": true, "running": false}')
        c = CriticAgent(vlm_analyzer=vlm, vst=_FakeVST())
        out = await c.run(CriticAgentInput(query="q", videos=[_video()]))
        assert out.video_results[0].result == CriticAgentResult.REJECTED

    @pytest.mark.asyncio
    async def test_explicit_confirmed_verdict_cannot_override_failed_criterion(self):
        vlm = _FakeVLM('{"result": "confirmed", "criteria_met": {"running": false}}')
        c = CriticAgent(vlm_analyzer=vlm, vst=_FakeVST())
        out = await c.run(CriticAgentInput(query="q", videos=[_video()]))
        assert out.video_results[0].result == CriticAgentResult.UNVERIFIED
        assert out.video_results[0].criteria_met == {"running": False}


class TestCriticBatching:
    def test_non_positive_concurrency_is_rejected(self):
        with pytest.raises(ConfigurationError, match="max_concurrent"):
            CriticAgent(vlm_analyzer=_FakeVLM("{}"), vst=_FakeVST(), max_concurrent_verifications=0)

    def test_invalid_default_evaluation_count_is_rejected(self):
        with pytest.raises(ConfigurationError, match="num_videos"):
            CriticAgent(vlm_analyzer=_FakeVLM("{}"), vst=_FakeVST(), num_videos_to_evaluate=0)

    def test_blank_query_is_rejected(self):
        with pytest.raises(ValidationError, match="query"):
            CriticAgentInput(query=" ", videos=[])

    def test_invalid_video_time_range_is_rejected(self):
        timestamp = datetime(2025, 1, 1, tzinfo=UTC)
        with pytest.raises(ValidationError, match="end_timestamp"):
            VideoInfo(sensor_id="cam", start_timestamp=timestamp, end_timestamp=timestamp)

    @pytest.mark.asyncio
    async def test_empty_sensor_id_skipped(self):
        vlm = _FakeVLM("{}")
        c = CriticAgent(vlm_analyzer=vlm, vst=_FakeVST())
        await c.run(
            CriticAgentInput(
                query="q",
                videos=[_video(""), _video("real")],
            )
        )
        assert len(vlm.calls) == 1
        assert vlm.calls[0]["sensor_id"] == "real"

    @pytest.mark.asyncio
    async def test_evaluation_count_caps(self):
        vlm = _FakeVLM("{}")
        c = CriticAgent(vlm_analyzer=vlm, vst=_FakeVST())
        await c.run(
            CriticAgentInput(
                query="q",
                evaluation_count=2,
                videos=[_video(f"s{i}") for i in range(5)],
            )
        )
        assert len(vlm.calls) == 2

    @pytest.mark.asyncio
    async def test_default_evaluates_every_verifiable_video(self):
        vlm = _FakeVLM("{}")
        c = CriticAgent(vlm_analyzer=vlm, vst=_FakeVST())

        await c.run(
            CriticAgentInput(
                query="q",
                videos=[_video(f"s{i}") for i in range(7)],
            )
        )

        assert len(vlm.calls) == 7

    @pytest.mark.asyncio
    async def test_eval_cap_counts_verifiable_only(self):
        # Empty-sensor entries are filtered BEFORE the cap, so they cannot consume
        # cap slots and starve the genuinely verifiable videos.
        vlm = _FakeVLM("{}")
        c = CriticAgent(vlm_analyzer=vlm, vst=_FakeVST())
        await c.run(
            CriticAgentInput(
                query="q",
                evaluation_count=2,
                videos=[_video(""), _video(""), _video("s1"), _video("s2"), _video("s3")],
            )
        )
        assert len(vlm.calls) == 2
        assert {call["sensor_id"] for call in vlm.calls} == {"s1", "s2"}


class TestCriticTimeFormat:
    @pytest.mark.asyncio
    async def test_iso_passes_through(self):
        vlm = _FakeVLM("{}")
        c = CriticAgent(vlm_analyzer=vlm, vst=_FakeVST(), time_format="iso")
        await c.run(CriticAgentInput(query="q", videos=[_video()]))
        assert vlm.calls[0]["time_format"] == "iso"
        # ISO strings are pure passthrough — no conversion to seconds.
        assert "T" in vlm.calls[0]["start_timestamp"]

    @pytest.mark.asyncio
    async def test_offset_converts_to_seconds(self):
        vlm = _FakeVLM("{}")
        c = CriticAgent(vlm_analyzer=vlm, vst=_FakeVST(), time_format="offset")
        # clip starts at 00:00:00, video clip is at 00:00:10 → 00:00:20.
        await c.run(CriticAgentInput(query="q", videos=[_video(start=10, end=20)]))
        assert vlm.calls[0]["time_format"] == "offset"
        assert vlm.calls[0]["start_timestamp"] == "10.0"
        assert vlm.calls[0]["end_timestamp"] == "20.0"

    @pytest.mark.asyncio
    async def test_offset_rebases_synthetic_search_timestamps_to_live_timeline(self):
        vlm = _FakeVLM("{}")
        vst = _FakeVST(
            timeline=("2026-07-31T12:00:00Z", "2026-07-31T12:01:00Z"),
        )
        c = CriticAgent(vlm_analyzer=vlm, vst=vst, time_format="offset")

        await c.run(CriticAgentInput(query="q", videos=[_video(start=10, end=20, source_type="video_file")]))

        assert vlm.calls[0]["start_timestamp"] == "10.0"
        assert vlm.calls[0]["end_timestamp"] == "20.0"

    @pytest.mark.asyncio
    async def test_offset_preserves_cross_midnight_file_interval(self):
        vlm = _FakeVLM("{}")
        vst = _FakeVST(
            timeline=("2026-07-31T12:00:00Z", "2026-08-01T12:01:00Z"),
        )
        c = CriticAgent(vlm_analyzer=vlm, vst=vst, time_format="offset")
        video = VideoInfo(
            sensor_id="cam",
            start_timestamp=datetime(2025, 1, 1, 23, 59, 50, tzinfo=UTC),
            end_timestamp=datetime(2025, 1, 2, 0, 0, 10, tzinfo=UTC),
            source_type="video_file",
        )

        await c.run(CriticAgentInput(query="q", videos=[video]))

        assert vlm.calls[0]["start_timestamp"] == "86390.0"
        assert vlm.calls[0]["end_timestamp"] == "86410.0"

    @pytest.mark.asyncio
    async def test_offset_preserves_multi_day_file_position(self):
        vlm = _FakeVLM("{}")
        vst = _FakeVST(
            timeline=("2026-07-31T12:00:00Z", "2026-08-02T12:00:00Z"),
        )
        c = CriticAgent(vlm_analyzer=vlm, vst=vst, time_format="offset")
        video = VideoInfo(
            sensor_id="cam",
            start_timestamp=datetime(2025, 1, 2, 1, 0, 0, tzinfo=UTC),
            end_timestamp=datetime(2025, 1, 2, 1, 0, 20, tzinfo=UTC),
            source_type="video_file",
        )

        await c.run(CriticAgentInput(query="q", videos=[video]))

        assert vlm.calls[0]["start_timestamp"] == "90000.0"
        assert vlm.calls[0]["end_timestamp"] == "90020.0"

    @pytest.mark.asyncio
    async def test_offset_does_not_rebase_non_file_bounds_onto_another_clip(self):
        """Only the synthetic file epoch may be re-anchored.

        Rebasing a wall-clock bound that merely falls outside the current
        timeline would verify unrelated footage and return a confident verdict
        about a clip the caller never retrieved. Left literal, VST rejects the
        request and the candidate fails open to ``unverified``.
        """
        vlm = _FakeVLM("{}")
        vst = _FakeVST(timeline=("2026-07-31T12:00:00Z", "2026-07-31T12:01:00Z"))
        c = CriticAgent(vlm_analyzer=vlm, vst=vst, time_format="offset")

        out = await c.run(CriticAgentInput(query="q", videos=[_video(start=10, end=20)]))

        assert vlm.calls == []
        assert out.video_results[0].result == CriticAgentResult.UNVERIFIED

    @pytest.mark.asyncio
    async def test_offset_clamps_to_clip_end(self):
        # clip is 60s long (00:00 → 01:00); request end at 5min → must clamp to 60.
        vlm = _FakeVLM("{}")
        c = CriticAgent(vlm_analyzer=vlm, vst=_FakeVST(), time_format="offset")
        v = VideoInfo(
            sensor_id="cam",
            start_timestamp=datetime(2025, 1, 1, 0, 0, 10, tzinfo=UTC),
            end_timestamp=datetime(2025, 1, 1, 0, 5, 0, tzinfo=UTC),
        )
        await c.run(CriticAgentInput(query="q", videos=[v]))
        assert vlm.calls[0]["end_timestamp"] == "60.0"


class TestCriticOutputShape:
    @pytest.mark.asyncio
    async def test_video_results_field(self):
        vlm = _FakeVLM("{}")
        c = CriticAgent(vlm_analyzer=vlm, vst=_FakeVST())
        out = await c.run(CriticAgentInput(query="q", videos=[_video()]))
        assert hasattr(out, "video_results")
        assert isinstance(out.video_results, list)

    @pytest.mark.asyncio
    async def test_video_result_field_names(self):
        vlm = _FakeVLM('{"x": true}')
        c = CriticAgent(vlm_analyzer=vlm, vst=_FakeVST())
        out = await c.run(CriticAgentInput(query="q", videos=[_video()]))
        r = out.video_results[0]
        assert set(r.model_dump().keys()) == {"video_info", "result", "criteria_met"}
        # Enum value contract — must match agents/critic_agent.py:168-173
        assert r.result.value in {"confirmed", "rejected", "unverified"}
