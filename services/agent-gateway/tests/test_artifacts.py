# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import unittest

from vss_agent_gateway.artifacts import (
    ARTIFACT_CLOSE,
    ARTIFACT_OPEN,
    ArtifactStreamParser,
    parse_artifact,
    strip_artifact_envelopes,
    strip_artifacts_from_value,
)


def envelope(kind: str = "vss.search.results") -> str:
    payload = {
        "version": "1.0",
        "kind": kind,
        "payload": {"data": [{"video_name": "clip.mp4"}]},
    }
    return f"{ARTIFACT_OPEN}{json.dumps(payload)}{ARTIFACT_CLOSE}"


class ParseArtifactTests(unittest.TestCase):
    def test_accepts_namespaced_vss_artifact(self) -> None:
        raw = json.dumps(
            {
                "version": "1.0",
                "kind": "vss.alert.incidents",
                "payload": {"incidents": []},
            }
        )
        artifact = parse_artifact(raw)
        self.assertIsNotNone(artifact)
        assert artifact is not None
        self.assertEqual(artifact.kind, "vss.alert.incidents")
        self.assertTrue(artifact.artifact_id.startswith("artifact_"))

    def test_rejects_unknown_version_kind_and_non_object_payload(self) -> None:
        invalid = (
            {"version": "2.0", "kind": "vss.search.results", "payload": {}},
            {"version": "1.0", "kind": "search.results", "payload": {}},
            {"version": "1.0", "kind": "vss.search.results", "payload": []},
        )
        for value in invalid:
            with self.subTest(value=value):
                self.assertIsNone(parse_artifact(json.dumps(value)))

    def test_rejects_non_finite_payload_numbers(self) -> None:
        self.assertIsNone(
            parse_artifact(
                '{"version":"1.0","kind":"vss.search.results","payload":{"score":NaN}}'
            )
        )


class ArtifactStreamParserTests(unittest.TestCase):
    def test_extracts_split_envelope_without_exposing_it_as_text(self) -> None:
        parser = ArtifactStreamParser()
        value = f"Before {envelope()} after"
        events = []
        for character in value:
            events.extend(parser.feed(character))
        events.extend(parser.finish())

        self.assertEqual(
            "".join(
                str(event.data["delta"])
                for event in events
                if event.type == "message.delta"
            ),
            "Before  after",
        )
        artifacts = [event for event in events if event.type == "artifact.created"]
        self.assertEqual(len(artifacts), 1)
        self.assertEqual(artifacts[0].data["kind"], "vss.search.results")

    def test_deduplicates_identical_artifacts(self) -> None:
        parser = ArtifactStreamParser()
        events = parser.feed(envelope() + envelope()) + parser.finish()
        self.assertEqual(
            sum(event.type == "artifact.created" for event in events),
            1,
        )

    def test_inspects_nested_tool_output_and_deduplicates_final_text(self) -> None:
        parser = ArtifactStreamParser()
        value = envelope()
        events = parser.inspect_complete({"content": [{"text": value}]})
        events += parser.feed(value) + parser.finish()
        self.assertEqual(
            sum(event.type == "artifact.created" for event in events),
            1,
        )
        self.assertFalse(any(event.type == "message.delta" for event in events[:1]))

    def test_preserves_malformed_and_unclosed_envelopes(self) -> None:
        malformed = f"{ARTIFACT_OPEN}not-json{ARTIFACT_CLOSE}"
        unclosed = f"tail {ARTIFACT_OPEN}still-open"
        for value in (malformed, unclosed):
            with self.subTest(value=value):
                parser = ArtifactStreamParser()
                events = parser.feed(value) + parser.finish()
                self.assertEqual(
                    "".join(
                        str(event.data["delta"])
                        for event in events
                        if event.type == "message.delta"
                    ),
                    value,
                )
                self.assertFalse(
                    any(event.type == "artifact.created" for event in events)
                )

    def test_strips_only_valid_envelopes_for_recovery_history(self) -> None:
        valid = envelope()
        invalid = (
            '<vss-ui-artifact>{"version":"2.0","kind":"vss.search.results",'
            '"payload":{}}</vss-ui-artifact>'
        )
        self.assertEqual(
            strip_artifact_envelopes(f"before{valid}middle{invalid}after"),
            f"beforemiddle{invalid}after",
        )

    def test_strips_artifacts_from_nested_tool_output(self) -> None:
        valid = envelope()
        self.assertEqual(
            strip_artifacts_from_value({"content": [{"text": f"before{valid}after"}]}),
            {"content": [{"text": "beforeafter"}]},
        )


if __name__ == "__main__":
    unittest.main()
