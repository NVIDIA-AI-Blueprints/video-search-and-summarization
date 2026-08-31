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

"""The two factories read transport names the same way, and now provably so.

Each used to carry its own alias table and its own normalizer, with a docstring
saying the two were kept on one contract. These are that contract, written as
assertions instead: an operator's ``redis_stream`` has to select Redis on both
sides, because the alternative is a deployment where the terminal sink took the
spelling and the event bridge quietly fell back to Kafka.
"""

import pytest

from mdx.transport.names import (
    CONSOLE,
    ELASTIC,
    KAFKA,
    REDIS_STREAM,
    TERMINAL_SINK_ALIASES,
    TRANSPORT_ALIASES,
    fold,
    normalize,
    require_terminal_sink_type,
)


class TestFolding:
    @pytest.mark.parametrize("spelling", [
        "redisStream", "redis_stream", "redis-stream", "REDISSTREAM",
        "  RedisStream  ", "Redis_Stream",
    ])
    def test_every_reasonable_spelling_folds_together(self, spelling):
        """One YAML author writes ``redis_stream`` and another ``redisStream``;
        neither meant a different transport."""
        assert fold(spelling) == "redisstream"

    def test_folding_does_not_merge_different_transports(self):
        assert fold("kafka") != fold("console")


class TestNormalize:
    @pytest.mark.parametrize("spelling", ["redisStream", "redis_stream", "redis"])
    def test_the_redis_spellings_resolve_to_the_canonical_name(self, spelling):
        assert normalize(spelling) == REDIS_STREAM

    def test_an_unknown_name_is_none_not_a_guess(self):
        assert normalize("rabbitmq") is None

    @pytest.mark.parametrize("value", [None, 0, [], {}, True])
    def test_a_non_string_is_none(self, value):
        """"Not configured" and "configured wrongly" are the caller's to tell
        apart, from its own config rather than from here."""
        assert normalize(value) is None

    def test_elastic_is_not_an_event_bridge_transport(self):
        """Which is what makes the split-transport warning stay quiet for the
        default deployment: an Elasticsearch terminal sink beside a Kafka error
        sink is the normal shape, not a mismatch."""
        assert normalize("elastic") is None
        assert normalize("elastic", TERMINAL_SINK_ALIASES) == ELASTIC


class TestTheTwoFactoriesAgree:
    """The guard for M4. Previously an invariant held by remembering."""

    def test_the_terminal_sink_accepts_every_event_bridge_spelling(self):
        for spelling, canonical in TRANSPORT_ALIASES.items():
            assert TERMINAL_SINK_ALIASES[spelling] == canonical

    def test_the_terminal_sink_adds_only_elasticsearch(self):
        extra = set(TERMINAL_SINK_ALIASES) - set(TRANSPORT_ALIASES)
        assert extra == {"elastic", "elasticsearch"}

    def test_both_factories_resolve_a_spelling_identically(self):
        """Reached through the factories themselves, not the table, so a factory
        that grew its own normalizer again would fail here."""
        from mdx.event_bridge_factory import _normalize_transport
        from mdx.sink.vlm_enhanced_sink.factory import _normalize_sink_type

        for spelling in ("redis_stream", "redisStream", "REDIS", "kafka",
                         "console", "  Kafka "):
            assert _normalize_transport(spelling) == _normalize_sink_type(spelling), spelling

    def test_the_factories_differ_only_where_they_must(self):
        from mdx.event_bridge_factory import _normalize_transport
        from mdx.sink.vlm_enhanced_sink.factory import _normalize_sink_type

        assert _normalize_sink_type("elasticsearch") == ELASTIC
        assert _normalize_transport("elasticsearch") is None

    def test_the_canonical_names_are_the_ones_deployments_write(self):
        """These reach config files, Helm values and metric labels, so they are
        not free to be tidied."""
        assert (KAFKA, REDIS_STREAM, ELASTIC) == ("kafka", "redisStream", "elastic")


class TestTheTerminalSinkNameIsRequiredToResolve:
    """Read through the event bridge's table, every unrecognized value came back
    as ``None`` -- and ``None`` was read as "not Redis", which validation had
    nothing more to say about. So ``type: mongo`` was declared valid and failed
    in the forked pipeline child, on a traceback about a sink class.
    """

    @pytest.mark.parametrize("spelling,expected", [
        ("elastic", ELASTIC),
        ("elasticsearch", ELASTIC),
        ("redisStream", REDIS_STREAM),
        ("redis_stream", REDIS_STREAM),
        ("redis", REDIS_STREAM),
        ("kafka", KAFKA),
        ("console", CONSOLE),
        ("  Console  ", CONSOLE),
    ])
    def test_every_supported_spelling_resolves(self, spelling, expected):
        assert require_terminal_sink_type(spelling) == expected

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_absent_is_the_default_rather_than_an_error(self, value):
        """Elasticsearch is what this service has always defaulted to, and a
        rendered config produces the empty string for an unset variable."""
        assert require_terminal_sink_type(value) == ELASTIC

    @pytest.mark.parametrize("value", [
        "mongo", "elasticsearc", "rabbitmq", "redisstreams", 7, [], True,
    ])
    def test_anything_else_is_refused_by_name(self, value):
        with pytest.raises(ValueError, match="Unsupported vlm_enhanced_sink.type"):
            require_terminal_sink_type(value)

    def test_the_message_lists_what_is_supported(self):
        with pytest.raises(ValueError) as raised:
            require_terminal_sink_type("mongo")
        for supported in (ELASTIC, KAFKA, REDIS_STREAM, CONSOLE):
            assert supported in str(raised.value)

    def test_the_factory_resolves_through_the_same_function(self):
        """Or validation could accept a name the factory then refuses."""
        from mdx.sink.vlm_enhanced_sink import factory

        with pytest.raises(ValueError, match="Unsupported vlm_enhanced_sink.type"):
            factory.build_vlm_enhanced_sink({"vlm_enhanced_sink": {"type": "mongo"}})
