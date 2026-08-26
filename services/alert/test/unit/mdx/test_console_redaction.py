# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Redaction for the console sinks.

Both console sinks write the whole payload to the log, which puts VLM reasoning
about people, VST URLs and GPS fixes into whatever collects those logs. The
``redact`` option is the control for that, so these tests pin the two
properties that make it trustworthy: the named fields really do leave the
rendered text, and the caller's document is not mutated on the way — the
durable sinks are expected to publish the original.
"""

import json

from mdx.sink.console_render import REDACTED, parse_redact_paths, redact
from mdx.sink.sink_console import ConsoleSink
from mdx.sink.vlm_enhanced_sink.sink_console import VLMEnhancedConsoleSink


def document():
    return {
        "sensorId": "cam-1",
        "category": "Vehicle Collision",
        "info": {
            "verdict": "confirmed",
            "reasoning": "a person in a red jacket crosses against the light",
            "videoSource": "http://vst:30888/media/cam-1.mp4?token=secret",
            "location": "37.7749,-122.4194,0.0",
        },
    }


class TestParseRedactPaths:
    """Deployment configs substitute environment variables, which yield strings."""

    def test_a_list_is_taken_as_is(self):
        assert parse_redact_paths(["info.reasoning", "info.location"]) == [
            "info.reasoning",
            "info.location",
        ]

    def test_a_comma_separated_string_is_split(self):
        assert parse_redact_paths("info.reasoning, info.videoSource") == [
            "info.reasoning",
            "info.videoSource",
        ]

    def test_an_unset_option_redacts_nothing(self):
        assert parse_redact_paths(None) == []
        assert parse_redact_paths("") == []


class TestRedact:
    def test_a_nested_path_is_masked(self):
        result = redact(document(), ["info.videoSource"])
        assert result["info"]["videoSource"] == REDACTED

    def test_the_other_fields_survive(self):
        """Redaction has to leave the verdict readable or the sink is useless."""
        result = redact(document(), ["info.reasoning"])
        assert result["info"]["verdict"] == "confirmed"
        assert result["sensorId"] == "cam-1"

    def test_a_top_level_path_is_masked(self):
        assert redact(document(), ["sensorId"])["sensorId"] == REDACTED

    def test_the_callers_document_is_not_mutated(self):
        """The Elastic and Redis sinks publish the same dict, unredacted."""
        original = document()
        redact(original, ["info.reasoning"])
        assert original["info"]["reasoning"].startswith("a person")

    def test_an_unresolvable_path_is_ignored(self):
        result = redact(document(), ["info.nope", "nope.nope", "sensorId.deeper"])
        assert result["sensorId"] == "cam-1"

    def test_a_non_dict_payload_passes_through(self):
        assert redact("plain text", ["info.reasoning"]) == "plain text"


class TestConsoleSinkRendersRedacted:
    def test_the_event_bridge_sink_masks_configured_fields(self):
        sink = ConsoleSink({
            "event_bridge": {"console_sink": {"redact": ["info.videoSource"]}},
        })
        rendered = sink._render(document())
        assert "secret" not in rendered
        assert REDACTED in rendered
        assert "confirmed" in rendered

    def test_the_event_bridge_sink_defaults_to_no_redaction(self):
        sink = ConsoleSink({})
        assert sink.redact_paths == []
        assert "secret" in sink._render(document())

    def test_a_json_string_payload_is_redacted_too(self):
        """Raw writes arrive as encoded JSON, not as a dict."""
        sink = ConsoleSink({
            "event_bridge": {"console_sink": {"redact": ["info.videoSource"]}},
        })
        rendered = sink._render(json.dumps(document()).encode("utf-8"))
        assert "secret" not in rendered

    def test_the_vlm_sink_masks_configured_fields(self):
        sink = VLMEnhancedConsoleSink(redact_paths=["info.reasoning"])
        rendered = sink._render(document())
        assert "red jacket" not in rendered
        assert "confirmed" in rendered

    def test_the_vlm_sink_reads_redact_from_config(self):
        sink = VLMEnhancedConsoleSink.from_config({
            "vlm_enhanced_sink": {
                "type": "console",
                "console": {"redact": "info.reasoning,info.location"},
            },
        })
        assert sink._redact_paths == ["info.reasoning", "info.location"]
        rendered = sink._render(document())
        assert "red jacket" not in rendered
        assert "37.7749" not in rendered

    def test_the_vlm_sink_defaults_to_no_redaction(self):
        sink = VLMEnhancedConsoleSink()
        assert sink._redact_paths == []
        assert "red jacket" in sink._render(document())
