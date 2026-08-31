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

"""The routing map rules, tested once instead of three times.

These were three implementations -- in the source, in the terminal sink, and in
configuration validation -- of two rules. The point of the tests at the bottom
is that they stay one implementation: they check that the same bad map produces
the same explanation whichever of the three an operator reaches first, which is
what stopped being true while each had its own copy.
"""

import pytest

from mdx.stream_routing import (
    SUPPORTED_KINDS,
    clean_stream_name,
    require_distinct_streams,
    require_known_keys,
    require_stream_map,
    require_stream_name,
)


class TestCleanStreamName:
    def test_a_name_is_itself(self):
        assert clean_stream_name("mdx-alerts") == "mdx-alerts"

    def test_surrounding_whitespace_is_removed_not_rejected(self):
        """Invisible in a config file, and never what anyone meant to type."""
        assert clean_stream_name("  mdx-alerts \n") == "mdx-alerts"

    @pytest.mark.parametrize("value", ["", "   ", None, 0, 7, [], {}, True])
    def test_anything_that_is_not_a_name_is_none(self, value):
        assert clean_stream_name(value) is None


class TestRequireStreamName:
    def test_a_name_passes_through_stripped(self):
        assert require_stream_name(" s ", "a.b") == "s"

    def test_a_blank_names_the_setting_to_edit(self):
        """The message has to name the config line, because the class that read
        it is not where the mistake is."""
        with pytest.raises(ValueError, match=r"a\.b\['alert'\] is empty"):
            require_stream_name("", "a.b['alert']")

    def test_a_blank_explains_the_usual_cause(self):
        with pytest.raises(ValueError, match="unresolved variable in a rendered"):
            require_stream_name("   ", "a.b")

    def test_the_caller_supplies_the_remedy(self):
        """Removing the key means different things to the source and the sink,
        so neither wording can be the shared one."""
        with pytest.raises(ValueError, match="Remove the key to use the default"):
            require_stream_name(None, "a.b",
                                remedy="Remove the key to use the default.")

    def test_a_non_string_is_reported_as_a_type_error_not_a_blank(self):
        """``stream: 8080`` is a different mistake from ``stream:`` and reading
        one message for the other sends the operator to the wrong line."""
        with pytest.raises(ValueError, match="must be a stream name, got int"):
            require_stream_name(8080, "a.b")


class TestRequireStreamMap:
    def test_a_populated_mapping_passes_through(self):
        streams = {"alert": "s"}
        assert require_stream_map(streams, "a.b", SUPPORTED_KINDS) is streams

    @pytest.mark.parametrize("value", [None, {}, [], "mdx-alerts", 0])
    def test_a_missing_or_unusable_map_is_rejected(self, value):
        with pytest.raises(ValueError, match="must be a mapping naming a stream"):
            require_stream_map(value, "a.b", SUPPORTED_KINDS)

    def test_the_message_lists_the_keys_the_caller_accepts(self):
        """Not a fixed list: the event-bridge sink's keys are output routes, and
        naming kinds at it sends the operator to write a config it rejects."""
        with pytest.raises(ValueError) as raised:
            require_stream_map({}, "a.b", ("enhanced_anomaly", "incidents"))
        message = str(raised.value)
        assert "enhanced_anomaly" in message and "incidents" in message
        assert "alert" not in message


class TestRequireDistinctStreams:
    def test_distinct_streams_are_accepted(self):
        require_distinct_streams({"alert": "a", "incident": "i"}, "a.b")

    def test_two_keys_on_one_stream_is_rejected(self):
        """Not cosmetic: the key selects the decode schema, so sharing means one
        of the two kinds is decoded with the other's schema and published."""
        with pytest.raises(ValueError, match="cannot carry two event kinds"):
            require_distinct_streams({"alert": "s", "incident": "s"}, "a.b")

    def test_the_message_names_both_claimants(self):
        with pytest.raises(ValueError) as raised:
            require_distinct_streams({"alert": "s", "incident": "s"}, "a.b")
        message = str(raised.value)
        assert "'alert'" in message and "'incident'" in message

    def test_whitespace_does_not_hide_a_collision(self):
        with pytest.raises(ValueError, match="cannot carry two event kinds"):
            require_distinct_streams({"alert": "s", "incident": " s "}, "a.b")

    def test_blanks_are_left_to_the_name_check(self):
        """Two blanks are not a collision worth reporting as one -- the blank is
        the error, and reporting it twice under a second name is noise."""
        require_distinct_streams({"alert": "", "incident": None}, "a.b")


class TestRequireKnownKeys:
    """A key the reader does not look for is ignored, and ignored looks exactly
    like absent -- which for a sink means "do not publish that kind". So one
    misspelt key disabled a whole route while the sink reported healthy.
    """

    def test_the_expected_keys_are_accepted(self):
        require_known_keys({"incidents": "i"}, "a.b", ("enhanced_anomaly", "incidents"))

    def test_the_legacy_suffix_is_accepted_for_every_key(self):
        require_known_keys(
            {"incidents_stream": "i"}, "a.b", ("enhanced_anomaly", "incidents"),
        )

    def test_an_unknown_key_is_rejected(self):
        with pytest.raises(ValueError, match="no place for incident"):
            require_known_keys({"incident": "i"}, "a.b", ("incidents",))

    def test_the_message_says_what_the_section_does_accept(self):
        with pytest.raises(ValueError) as raised:
            require_known_keys({"typo": "t"}, "a.b", ("enhanced_anomaly", "incidents"))
        message = str(raised.value)
        assert "enhanced_anomaly, incidents" in message
        assert "a.b" in message

    def test_every_unknown_key_is_named_at_once(self):
        """One round trip through the config, not one per attempt to start."""
        with pytest.raises(ValueError) as raised:
            require_known_keys({"a": 1, "b": 2}, "a.b", ("incidents",))
        assert "a, b" in str(raised.value)

    def test_extras_are_accepted_without_being_advertised(self):
        """The heartbeat stream is not an event kind and the legacy kind names
        are not what anyone should be told to write, but neither is an error."""
        require_known_keys(
            {"incident": "i", "heartbeat": "hb", "anomaly": "a"},
            "a.b", SUPPORTED_KINDS, extra=("heartbeat", "anomaly"),
        )

    def test_an_empty_map_has_nothing_unknown_in_it(self):
        """Emptiness is :func:`require_stream_map`'s to report."""
        require_known_keys({}, "a.b", ("incidents",))


class TestTheThreeReadersStillAgree:
    """The regression guard for M1: one rule, one wording, one failure mode.

    Each of these built its own version once, and an operator's error message
    depended on which ran first -- validation returning False with one sentence,
    a constructor raising with another.
    """

    @staticmethod
    def _duplicate_map():
        return {"alert": "shared", "incident": "shared"}

    def test_configuration_validation_reports_the_shared_sentence(self, caplog):
        from mdx.event_bridge_factory import _validate_redis_streams

        with caplog.at_level("ERROR"):
            ok = _validate_redis_streams(
                "redis_source", {"streams": self._duplicate_map()})
        assert ok is False
        assert "cannot carry two event kinds" in caplog.text

    def test_the_source_reports_the_shared_sentence(self):
        from mdx.source.source_redis_stream import SourceRedisStream

        source = SourceRedisStream.__new__(SourceRedisStream)
        with pytest.raises(ValueError, match="cannot carry two event kinds"):
            SourceRedisStream._parse_streams(source, self._duplicate_map())

    def test_validation_and_the_source_reject_the_same_maps(self, caplog):
        """The pair that mattered: a map validation accepted and the constructor
        then rejected is a deployment that passes its own preflight and dies."""
        from mdx.event_bridge_factory import _validate_redis_streams
        from mdx.source.source_redis_stream import SourceRedisStream

        maps = [
            {},
            {"alert": ""},
            {"alert": "   "},
            {"alert": None},
            {"alert": 8080},
            {"alert": "s", "incident": "s"},
            {"alert": "s", "incident": " s "},
        ]
        for streams in maps:
            with caplog.at_level("ERROR"):
                validated = _validate_redis_streams("redis_source",
                                                    {"streams": streams})
            source = SourceRedisStream.__new__(SourceRedisStream)
            try:
                SourceRedisStream._parse_streams(source, streams)
                constructed = True
            except ValueError:
                constructed = False
            assert validated is constructed, streams
