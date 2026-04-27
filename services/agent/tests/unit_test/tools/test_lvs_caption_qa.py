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
"""Unit tests for LVS caption Q&A."""

import json
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

from pydantic import ValidationError
import pytest

from vss_agents.tools.lvs_caption_qa import LVSCaptionQAConfig
from vss_agents.tools.lvs_caption_qa import LVSCaptionQAInput
from vss_agents.tools.lvs_caption_qa import _build_lvs_caption_query
from vss_agents.tools.lvs_caption_qa import _format_evidence
from vss_agents.tools.lvs_caption_qa import _infer_time_scope_from_question
from vss_agents.tools.lvs_caption_qa import _seconds_to_pts_ns
from vss_agents.tools.lvs_caption_qa import lvs_caption_qa


class TestLVSCaptionQAConfig:
    """Test LVS caption Q&A config."""

    def test_required_fields(self):
        config = LVSCaptionQAConfig(llm_name="mock_llm", es_endpoint="http://localhost:9200")
        assert config.llm_name == "mock_llm"
        assert config.es_endpoint == "http://localhost:9200"
        assert config.es_index == "lvs-events"
        assert config.video_understanding_tool == "video_understanding"
        assert config.max_results == 8

    def test_missing_llm_name_raises(self):
        with pytest.raises(ValidationError):
            LVSCaptionQAConfig(es_endpoint="http://localhost:9200")

    def test_missing_es_endpoint_raises(self):
        with pytest.raises(ValidationError):
            LVSCaptionQAConfig(llm_name="mock_llm")


class TestLVSCaptionQAInput:
    """Test LVS caption Q&A input validation."""

    def test_basic_input(self):
        inp = LVSCaptionQAInput(sensor_id="camera-1", question="What happened?")
        assert inp.sensor_id == "camera-1"
        assert inp.question == "What happened?"

    def test_invalid_time_range_raises(self):
        with pytest.raises(ValidationError):
            LVSCaptionQAInput(sensor_id="camera-1", question="What happened?", start_timestamp=20, end_timestamp=10)

    def test_negative_time_raises(self):
        with pytest.raises(ValidationError):
            LVSCaptionQAInput(sensor_id="camera-1", question="What happened?", start_timestamp=-1)


class TestTimeInference:
    """Test simple timestamp inference from question text."""

    def test_between_seconds(self):
        assert _infer_time_scope_from_question("What happened between 45s and 60s?") == (45.0, 60.0)

    def test_from_mmss_to_mmss(self):
        assert _infer_time_scope_from_question("Describe from 1:05 to 1:20") == (65.0, 80.0)

    def test_single_timestamp(self):
        assert _infer_time_scope_from_question("What is visible at 12 seconds?") == (12.0, None)

    def test_before_timestamp(self):
        assert _infer_time_scope_from_question("What happened before 30s?") == (None, 30.0)


class TestQueryBuilding:
    """Test ES query construction."""

    def test_time_filter_uses_pts_nanoseconds(self):
        query = _build_lvs_caption_query(
            question="white sedan",
            sensor_id="camera-1",
            doc_types=["raw_events"],
            start_seconds=45.0,
            end_seconds=60.0,
            size=5,
        )

        assert query["size"] == 5
        filters = query["query"]["bool"]["filter"]
        assert {"range": {"metadata.content_metadata.start_pts": {"lte": _seconds_to_pts_ns(60.0)}}} in filters
        assert {"range": {"metadata.content_metadata.end_pts": {"gte": _seconds_to_pts_ns(45.0)}}} in filters

    def test_doc_type_and_source_filters_present(self):
        query = _build_lvs_caption_query(
            question="delivery truck",
            sensor_id="abc123-stream-id",
            doc_types=["structured_events", "aggregated_summary"],
        )
        filters = query["query"]["bool"]["filter"]
        assert filters[0]["bool"]["minimum_should_match"] == 1
        assert filters[1]["bool"]["minimum_should_match"] == 1
        assert query["query"]["bool"]["must"][0]["multi_match"]["query"] == "delivery truck"

    def test_can_build_source_only_fallback_query(self):
        query = _build_lvs_caption_query(
            question="What happened in the video?",
            sensor_id="camera-1",
            doc_types=["structured_events", "aggregated_summary"],
            use_keyword_query=False,
        )

        assert query["query"]["bool"]["must"] == [{"match_all": {}}]


class TestEvidenceFormatting:
    """Test extraction and formatting from LVS ES documents."""

    def test_raw_event_relative_times_shift_to_chunk_pts(self):
        hit = {
            "_score": 7.0,
            "_source": {
                "text": json.dumps(
                    {
                        "events": [
                            {
                                "start_time": 0.0,
                                "end_time": 15.0,
                                "type": "vehicle_moving",
                                "description": "A white sedan enters and parks.",
                            }
                        ]
                    }
                ),
                "metadata": {
                    "content_metadata": {
                        "doc_type": "raw_events",
                        "start_pts": 45_000_000_000,
                        "end_pts": 60_000_000_000,
                    }
                },
            },
        }

        evidence = _format_evidence([hit], start_seconds=45.0, end_seconds=60.0, max_chars=2000)

        assert "[45s-60s] vehicle_moving: A white sedan enters and parks." in evidence

    def test_structured_events_are_chronological(self):
        hit = {
            "_score": 3.0,
            "_source": {
                "text": json.dumps(
                    {
                        "events": [
                            {"start_time": 20.0, "end_time": 25.0, "description": "Second event"},
                            {"start_time": 0.0, "end_time": 5.0, "description": "First event"},
                        ]
                    }
                ),
                "metadata": {"content_metadata": {"doc_type": "structured_events"}},
            },
        }

        evidence = _format_evidence([hit], start_seconds=None, end_seconds=None, max_chars=2000)

        assert evidence.splitlines()[0] == "[0s-5s] First event"
        assert evidence.splitlines()[1] == "[20s-25s] Second event"

    def test_aggregated_summary_excluded_for_time_scoped_question(self):
        hit = {
            "_score": 1.0,
            "_source": {
                "text": "Overall video summary.",
                "metadata": {"content_metadata": {"doc_type": "aggregated_summary"}},
            },
        }

        evidence = _format_evidence([hit], start_seconds=10.0, end_seconds=20.0, max_chars=2000)

        assert evidence == ""


class TestLVSCaptionQAInner:
    """Test the registered tool inner function."""

    @pytest.fixture
    def config(self):
        return LVSCaptionQAConfig(llm_name="mock_llm", es_endpoint="http://localhost:9200")

    @pytest.fixture
    def mock_builder(self):
        builder = AsyncMock()
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(content="The answer came from stored captions.")
        builder.get_llm.return_value = mock_llm
        return builder

    @pytest.mark.asyncio
    async def test_answers_from_stored_captions(self, config, mock_builder):
        mock_es = MagicMock()
        mock_es.indices.exists = AsyncMock(return_value=True)
        mock_es.search = AsyncMock(
            return_value={
                "hits": {
                    "hits": [
                        {
                            "_score": 4.0,
                            "_source": {
                                "text": json.dumps(
                                    {"events": [{"start_time": 0, "end_time": 5, "description": "A person walks."}]}
                                ),
                                "metadata": {"content_metadata": {"doc_type": "structured_events"}},
                            },
                        }
                    ]
                }
            }
        )
        mock_es.close = AsyncMock()

        with patch("vss_agents.tools.lvs_caption_qa.AsyncElasticsearch", return_value=mock_es):
            gen = lvs_caption_qa.__wrapped__(config, mock_builder)
            fi = await gen.__anext__()
            result = await fi.single_fn(LVSCaptionQAInput(sensor_id="camera-1", question="What happened?"))

        assert result == "The answer came from stored captions."
        mock_builder.get_tool.assert_not_called()

    @pytest.mark.asyncio
    async def test_uses_source_only_es_fallback_before_vlm(self, config, mock_builder):
        mock_es = MagicMock()
        mock_es.indices.exists = AsyncMock(return_value=True)
        mock_es.search = AsyncMock(
            side_effect=[
                {"hits": {"hits": []}},
                {
                    "hits": {
                        "hits": [
                            {
                                "_score": 1.0,
                                "_source": {
                                    "text": json.dumps(
                                        {
                                            "events": [
                                                {
                                                    "start_time": 0,
                                                    "end_time": 5,
                                                    "description": "A person walks.",
                                                }
                                            ]
                                        }
                                    ),
                                    "metadata": {"content_metadata": {"doc_type": "structured_events"}},
                                },
                            }
                        ]
                    }
                },
            ]
        )
        mock_es.close = AsyncMock()

        with patch("vss_agents.tools.lvs_caption_qa.AsyncElasticsearch", return_value=mock_es):
            gen = lvs_caption_qa.__wrapped__(config, mock_builder)
            fi = await gen.__anext__()
            result = await fi.single_fn(LVSCaptionQAInput(sensor_id="camera-1", question="What happened?"))

        assert result == "The answer came from stored captions."
        assert mock_es.search.await_count == 2
        second_query = mock_es.search.await_args_list[1].kwargs["body"]
        assert second_query["query"]["bool"]["must"] == [{"match_all": {}}]
        mock_builder.get_tool.assert_not_called()

    @pytest.mark.asyncio
    async def test_falls_back_when_index_missing(self, config, mock_builder):
        fallback_tool = AsyncMock()
        fallback_tool.ainvoke.return_value = "VLM fallback answer"
        mock_builder.get_tool.return_value = fallback_tool

        mock_es = MagicMock()
        mock_es.indices.exists = AsyncMock(return_value=False)
        mock_es.close = AsyncMock()

        with patch("vss_agents.tools.lvs_caption_qa.AsyncElasticsearch", return_value=mock_es):
            gen = lvs_caption_qa.__wrapped__(config, mock_builder)
            fi = await gen.__anext__()
            result = await fi.single_fn(LVSCaptionQAInput(sensor_id="camera-1", question="What happened at 12s?"))

        assert result == "VLM fallback answer"
        fallback_tool.ainvoke.assert_awaited_once()
        payload = fallback_tool.ainvoke.await_args.kwargs["input"]
        assert payload["start_timestamp"] == 12.0
        assert payload["end_timestamp"] == 42.0

    @pytest.mark.asyncio
    async def test_falls_back_when_hits_have_no_usable_evidence(self, config, mock_builder):
        fallback_tool = AsyncMock()
        fallback_tool.ainvoke.return_value = "VLM fallback answer"
        mock_builder.get_tool.return_value = fallback_tool

        mock_es = MagicMock()
        mock_es.indices.exists = AsyncMock(return_value=True)
        mock_es.search = AsyncMock(return_value={"hits": {"hits": [{"_score": 1.0, "_source": {"text": ""}}]}})
        mock_es.close = AsyncMock()

        with patch("vss_agents.tools.lvs_caption_qa.AsyncElasticsearch", return_value=mock_es):
            gen = lvs_caption_qa.__wrapped__(config, mock_builder)
            fi = await gen.__anext__()
            result = await fi.single_fn(LVSCaptionQAInput(sensor_id="camera-1", question="What happened?"))

        assert result == "VLM fallback answer"
        fallback_tool.ainvoke.assert_awaited_once()
