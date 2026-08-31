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
``redact`` option is the control for that, so these tests pin the properties
that make it trustworthy: the named fields really do leave the rendered text,
the caller's document is not mutated on the way — the durable sinks are expected
to publish the original — and, since masking is now on by default, that an
*unset* option masks while only an explicit opt-out does not.

The unset-versus-opt-out distinction gets its own class because the two arrive
as very similar values: a rendered deployment config substitutes an unset
variable as ``""``, which must not read as "the operator turned redaction off".
"""

import json

import pytest

from mdx.sink.console_render import (
    DEFAULT_REDACT_PATHS,
    REDACTED,
    REDACTION_CONFIGURED,
    REDACTION_DEFAULT,
    REDACTION_DISABLED,
    parse_redact_paths,
    resolve_max_chars,
    resolve_redact_paths,
    redact,
)
from mdx.sink.sink_console import ConsoleSink
from mdx.sink.vlm_enhanced_sink.sink_base import document_id
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

    def test_the_raw_normalizer_still_returns_nothing_for_an_empty_value(self):
        """``parse_redact_paths`` is the normalizer, not the policy."""
        assert parse_redact_paths(None) == []
        assert parse_redact_paths("") == []


class TestResolveRedactPaths:
    """Which of the three states a configured value lands in.

    Selecting a console sink is a quick debugging decision; the log collector it
    writes to is someone else's long-lived system. So the default has to be the
    safe one, and only a deliberate opt-out may turn it off.
    """

    def test_an_unset_option_masks_the_default_paths(self):
        paths, mode = resolve_redact_paths(None)
        assert mode == REDACTION_DEFAULT
        assert paths == list(DEFAULT_REDACT_PATHS)

    def test_the_empty_string_a_rendered_config_produces_is_not_an_opt_out(self):
        """Compose and Helm substitute an unset variable as "" — the case that
        would otherwise silently disable masking on every rendered deployment."""
        paths, mode = resolve_redact_paths("")
        assert mode == REDACTION_DEFAULT
        assert paths == list(DEFAULT_REDACT_PATHS)

    def test_named_paths_replace_the_defaults_rather_than_adding_to_them(self):
        paths, mode = resolve_redact_paths(["info.videoSource"])
        assert mode == REDACTION_CONFIGURED
        assert paths == ["info.videoSource"]

    def test_the_word_none_turns_masking_off(self):
        for spelling in ("none", "NONE", " off ", "false", "disabled"):
            paths, mode = resolve_redact_paths(spelling)
            assert (paths, mode) == ([], REDACTION_DISABLED), spelling

    def test_an_explicitly_empty_list_turns_masking_off(self):
        """Writing `redact: []` in YAML is an author's decision, unlike ""."""
        assert resolve_redact_paths([]) == ([], REDACTION_DISABLED)

    def test_a_boolean_false_turns_masking_off_and_true_uses_the_defaults(self):
        assert resolve_redact_paths(False) == ([], REDACTION_DISABLED)
        assert resolve_redact_paths(True) == (list(DEFAULT_REDACT_PATHS), REDACTION_DEFAULT)

    def test_an_unparseable_value_falls_back_to_the_defaults(self):
        """A lone comma is a typo, not consent to log everything."""
        paths, mode = resolve_redact_paths(",")
        assert mode == REDACTION_DEFAULT
        assert paths == list(DEFAULT_REDACT_PATHS)

    def test_the_verdict_fields_are_never_in_the_default_list(self):
        """Masking these would leave the sink unable to answer its own question."""
        for readable in ("id", "sensorId", "category", "info.verdict"):
            assert readable not in DEFAULT_REDACT_PATHS


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

    @pytest.mark.parametrize("field", ["videoPath", "video_path"])
    def test_the_media_path_is_masked_by_default(self, field):
        """Where the footage is, under either spelling. The Behavior schema
        writes ``videoPath``; the HTTP and direct-media response entities write
        ``video_path``. The default list reached ``info.videoSource`` and walked
        past both."""
        payload = dict(document(), **{field: "/media/cam-1/2026-08-31.mp4"})
        assert redact(payload, list(DEFAULT_REDACT_PATHS))[field] == REDACTED

    @pytest.mark.parametrize("field", ["videoPath", "video_path"])
    def test_the_nested_spelling_is_masked_too(self, field):
        """Which level it sits at depends on what produced the document, and a
        path that resolves nowhere costs nothing."""
        payload = document()
        payload["info"][field] = "/media/cam-1/2026-08-31.mp4"
        masked = redact(payload, list(DEFAULT_REDACT_PATHS))
        assert masked["info"][field] == REDACTED

    def test_the_console_sink_does_not_print_a_media_path(self, ):
        """The end of the argument: this is the sink an operator turns on to
        watch verdicts, and its log goes somewhere with a retention policy."""
        sink = ConsoleSink({})
        payload = dict(document(), video_path="/srv/media/cam-1/2026-08-31.mp4")
        assert "/srv/media" not in sink._render(payload)


class TestAListOfDocumentsIsMaskedToo:
    """A JSON array has the same paths as the object, one set per element.

    Returning it untouched with every other non-dictionary read as "there are no
    field paths here", which is true of a number and false of a batch. The raw
    write path renders whatever a producer published, so a batch published as an
    array was logged in full.
    """

    def test_every_element_is_masked(self):
        masked = redact([document(), document()], ["info.reasoning"])
        assert [item["info"]["reasoning"] for item in masked] == [REDACTED, REDACTED]

    def test_the_verdict_still_survives_in_each(self):
        masked = redact([document()], ["info.reasoning"])
        assert masked[0]["info"]["verdict"] == "confirmed"

    def test_the_callers_list_is_not_mutated(self):
        original = [document()]
        redact(original, ["info.reasoning"])
        assert original[0]["info"]["reasoning"].startswith("a person")

    def test_elements_that_are_not_documents_are_left_alone(self):
        assert redact([1, "two", None], ["info.reasoning"]) == [1, "two", None]

    def test_a_nested_list_of_documents_is_reached(self):
        masked = redact({"items": [document()]}, ["items"])
        # The path names the list itself, so the list is what is masked -- the
        # rule is unchanged, this only says it still applies inside a document.
        assert masked["items"] == REDACTED

    def test_the_event_bridge_sink_masks_a_published_array(self):
        sink = ConsoleSink({})
        rendered = sink._render(json.dumps([document()]).encode("utf-8"))
        assert "red jacket" not in rendered
        assert "secret" not in rendered


class TestConsoleSinkRendersRedacted:
    def test_the_event_bridge_sink_masks_configured_fields(self):
        sink = ConsoleSink({
            "event_bridge": {"console_sink": {"redact": ["info.videoSource"]}},
        })
        rendered = sink._render(document())
        assert "secret" not in rendered
        assert REDACTED in rendered
        assert "confirmed" in rendered

    def test_the_event_bridge_sink_masks_by_default(self):
        sink = ConsoleSink({})
        rendered = sink._render(document())
        assert "secret" not in rendered, "the VST URL leaked with no config at all"
        assert "red jacket" not in rendered
        assert "37.7749" not in rendered
        assert "confirmed" in rendered, "the verdict must stay readable"

    def test_the_event_bridge_sink_logs_in_full_when_told_to(self):
        sink = ConsoleSink({
            "event_bridge": {"console_sink": {"redact": "none"}},
        })
        assert sink.redact_paths == []
        assert "secret" in sink._render(document())

    def test_an_opaque_payload_is_summarized_rather_than_dumped(self):
        """A protobuf on the raw write path cannot be field-masked, and its
        printable runs carry the same material the dotted paths exist to hide."""
        sink = ConsoleSink({})
        rendered = sink._render(b"\x08\x01\x12\x1ba person in a red jacket")
        assert "red jacket" not in rendered
        assert "sha256:" in rendered

    def test_an_opaque_payload_is_still_dumped_when_redaction_is_off(self):
        sink = ConsoleSink({
            "event_bridge": {"console_sink": {"redact": "none"}},
        })
        assert "red jacket" in sink._render(b"a person in a red jacket")

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

    def test_the_vlm_sink_masks_by_default(self):
        sink = VLMEnhancedConsoleSink()
        rendered = sink._render(document())
        assert "red jacket" not in rendered
        assert "secret" not in rendered
        assert "confirmed" in rendered

    def test_the_vlm_sink_logs_in_full_when_told_to(self):
        sink = VLMEnhancedConsoleSink.from_config({
            "vlm_enhanced_sink": {"type": "console", "console": {"redact": "none"}},
        })
        assert sink._redact_paths == []
        assert "red jacket" in sink._render(document())


class TestTheTruncationLengthIsCoerced:
    """``int("")`` raises, and both sinks read this straight through it.

    A rendered config substitutes an unset variable as the empty string, so a
    deployment that mentioned ``max_chars`` without setting it crash-looped the
    pipeline child from inside a sink constructor -- over a display setting.
    """

    @pytest.mark.parametrize("value", ["", "   ", None, "lots", [], -50])
    def test_an_unusable_value_means_no_truncation(self, value):
        assert resolve_max_chars(value, "a.b") == 0

    @pytest.mark.parametrize("value", [2000, "2000"])
    def test_a_number_is_honoured_in_either_spelling(self, value):
        assert resolve_max_chars(value, "a.b") == 2000

    def test_an_unusable_value_says_so(self, caplog):
        """Falling back is not the same as ignoring: the operator set something."""
        with caplog.at_level("WARNING"):
            resolve_max_chars("lots", "event_bridge.console_sink.max_chars")
        assert "event_bridge.console_sink.max_chars" in caplog.text

    @pytest.mark.parametrize("value", ["", "lots"])
    def test_the_event_bridge_sink_starts_anyway(self, value):
        sink = ConsoleSink({
            "event_bridge": {"console_sink": {"max_chars": value}},
        })
        assert sink.max_chars == 0

    @pytest.mark.parametrize("value", ["", "lots"])
    def test_the_vlm_sink_starts_anyway(self, value):
        sink = VLMEnhancedConsoleSink.from_config({
            "vlm_enhanced_sink": {"type": "console", "console": {"max_chars": value}},
        })
        assert sink._max_chars == 0

    def test_a_configured_length_still_truncates(self):
        sink = ConsoleSink({"event_bridge": {"console_sink": {"max_chars": 40}}})
        rendered = sink._render(document())
        assert rendered.endswith("chars]")
        assert len(rendered) < len(json.dumps(document(), indent=2))


class TestTheRenderedIdIsTheOneTheDocumentCarries:
    """The pipeline writes the dedup fingerprint to ``Id``; the sinks read
    ``id``. So every verdict the console sink rendered was labelled ``id=None``
    -- on the one sink whose entire purpose is showing you the verdict.
    """

    def test_the_fingerprint_is_used(self, caplog):
        sink = VLMEnhancedConsoleSink()
        with caplog.at_level("INFO"):
            sink._emit("incident", "verdict", dict(document(), Id="fp-123"))
        assert "id=fp-123" in caplog.text

    def test_a_producers_own_id_is_still_used_when_there_is_no_fingerprint(self, caplog):
        sink = VLMEnhancedConsoleSink()
        with caplog.at_level("INFO"):
            sink._emit("incident", "verdict", dict(document(), id="evt-1"))
        assert "id=evt-1" in caplog.text

    def test_the_fingerprint_wins(self):
        """Same order as the dispatch log, so one event reads the same in both."""
        assert document_id({"Id": "fp", "id": "evt"}) == "fp"
