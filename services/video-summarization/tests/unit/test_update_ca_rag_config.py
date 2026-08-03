# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""
Unit tests for ViaStreamHandler.update_ca_rag_config — scenario/events forwarding.

Verifies that scenario and events are forwarded to the CA-RAG summarization
params unconditionally, regardless of enable_vlm_structured_output, so that
the aggregation LLM has context for generating accurate summaries via both
/summarize and /v1/summarize.
"""

from threading import RLock


def _make_handler(config=None):
    from via_stream_handler import ViaStreamHandler

    handler = ViaStreamHandler.__new__(ViaStreamHandler)
    handler._lock = RLock()
    handler._ca_rag_config = config if config is not None else _base_config()
    handler._live_stream_info_map = {}
    return handler


def _base_config():
    return {
        "context_manager": {"functions": ["summarization"]},
        "functions": {
            "summarization": {"tools": {"db": "vector_db", "llm": "llm_tool"}},
        },
        "tools": {
            "vector_db": {"params": {}},
            "llm_tool": {"params": {}},
        },
    }


def _make_ri(**overrides):
    from via_stream_handler import RequestInfo

    ri = RequestInfo()
    defaults = dict(
        source_id="test-id",
        is_live=False,
        chunk_size=10,
        summarize_batch_size=None,
        enable_vlm_structured_output=True,
        summarize=False,
        enable_audio=False,
        user_specified_collection_name=None,
        custom_metadata=None,
        delete_external_collection=False,
        schema=None,
        batch_response_method=None,
        scenario=None,
        events=None,
        auto_generate_prompt=None,
        time_metadata_keys=None,
        summarize_top_p=None,
        summarize_temperature=None,
        summarize_max_tokens=None,
    )
    defaults.update(overrides)
    for k, v in defaults.items():
        setattr(ri, k, v)
    return ri


class TestScenarioEventsForwarding:
    """scenario and events must reach CA-RAG params on every code path."""

    def test_forwarded_when_structured_output_enabled(self):
        handler = _make_handler()
        ri = _make_ri(
            enable_vlm_structured_output=True,
            scenario="warehouse",
            events=["forklift", "crash"],
        )
        config = handler.update_ca_rag_config(ri)
        params = config["functions"]["summarization"]["params"]
        assert params["scenario"] == "warehouse"
        assert params["events"] == ["forklift", "crash"]

    def test_forwarded_when_structured_output_disabled(self):
        handler = _make_handler()
        ri = _make_ri(
            enable_vlm_structured_output=False,
            scenario="retail",
            events=["theft", "slip-and-fall"],
        )
        config = handler.update_ca_rag_config(ri)
        params = config["functions"]["summarization"]["params"]
        assert params["scenario"] == "retail"
        assert params["events"] == ["theft", "slip-and-fall"]

    def test_none_scenario_not_forwarded(self):
        handler = _make_handler()
        ri = _make_ri(enable_vlm_structured_output=True, scenario=None, events=["crash"])
        config = handler.update_ca_rag_config(ri)
        params = config["functions"]["summarization"]["params"]
        assert "scenario" not in params
        assert params["events"] == ["crash"]

    def test_none_events_not_forwarded(self):
        handler = _make_handler()
        ri = _make_ri(enable_vlm_structured_output=True, scenario="security", events=None)
        config = handler.update_ca_rag_config(ri)
        params = config["functions"]["summarization"]["params"]
        assert params["scenario"] == "security"
        assert "events" not in params
