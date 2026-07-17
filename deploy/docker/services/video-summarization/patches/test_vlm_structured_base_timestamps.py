import json
import unittest

from vss_ctx_rag.functions.summarization.vlm_structured_base import (
    Event,
    VlmStructuredBase,
)


class NormalizeEventTimestampsTest(unittest.TestCase):
    def test_late_chunk_relative_timestamps_are_rebased(self):
        event = Event(
            start_time=0.3,
            end_time=9.3,
            type="search",
            description="Search activity",
        )

        changed = VlmStructuredBase._normalize_event_timestamps(event, 450, 460)

        self.assertTrue(changed)
        self.assertAlmostEqual(event.start_time, 450.3)
        self.assertAlmostEqual(event.end_time, 459.3)

    def test_already_global_timestamps_are_unchanged(self):
        event = Event(
            start_time=450.3,
            end_time=459.3,
            type="search",
            description="Search activity",
        )

        changed = VlmStructuredBase._normalize_event_timestamps(event, 450, 460)

        self.assertFalse(changed)
        self.assertEqual((event.start_time, event.end_time), (450.3, 459.3))

    def test_first_chunk_timestamps_are_unchanged(self):
        event = Event(
            start_time=0.1,
            end_time=9.5,
            type="vehicle pull over",
            description="Vehicle stop",
        )

        changed = VlmStructuredBase._normalize_event_timestamps(event, 0, 10)

        self.assertFalse(changed)
        self.assertEqual((event.start_time, event.end_time), (0.1, 9.5))

    def test_live_absolute_timestamps_are_unchanged(self):
        chunk_start = 1_752_700_000.0
        event = Event(
            start_time=chunk_start + 0.5,
            end_time=chunk_start + 9.5,
            type="activity",
            description="Live activity",
        )

        changed = VlmStructuredBase._normalize_event_timestamps(
            event,
            chunk_start,
            chunk_start + 10,
        )

        self.assertFalse(changed)
        self.assertEqual(
            (event.start_time, event.end_time),
            (chunk_start + 0.5, chunk_start + 9.5),
        )

    def test_valid_nonlocal_timestamp_is_not_guessed(self):
        event = Event(
            start_time=9.3,
            end_time=19.52,
            type="arrest",
            description="Cross-boundary event",
        )

        changed = VlmStructuredBase._normalize_event_timestamps(event, 10, 20)

        self.assertFalse(changed)
        self.assertEqual((event.start_time, event.end_time), (9.3, 19.52))

    def test_zero_duration_event_uses_existing_chunk_fallback(self):
        doc = json.dumps(
            {
                "events": [
                    {
                        "start_time": 0,
                        "end_time": 0,
                        "type": "activity",
                        "description": "Untimed activity",
                    }
                ]
            }
        )

        events, needs_inference = VlmStructuredBase._parse_json_document(
            doc,
            {"start_ntp_float": 40, "end_ntp_float": 50},
        )

        self.assertEqual(needs_inference, [])
        self.assertEqual(len(events), 1)
        self.assertEqual((events[0].start_time, events[0].end_time), (40, 50))


if __name__ == "__main__":
    unittest.main()
