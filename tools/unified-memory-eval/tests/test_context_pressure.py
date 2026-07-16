#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main

from scripts.context_pressure import (
    apply_context_pressure,
    build_context_pressure_message,
    context_pressure_settings,
    should_apply_context_pressure,
)


class BuildContextPressureMessageTest(TestCase):
    def test_body_length_matches_chars(self) -> None:
        message = build_context_pressure_message(1, 500)
        body_start = message.index("\n\n") + 2
        body_end = message.rindex("\n\n")
        body = message[body_start:body_end]
        self.assertEqual(len(body), 500)

    def test_message_instructs_ignore_and_json_ack(self) -> None:
        message = build_context_pressure_message(3, 100)
        self.assertIn("Context pressure turn 3", message)
        self.assertIn("Ignore this filler", message)
        self.assertIn('"pressure_ack": true', message)
        self.assertIn("CONTEXT PRESSURE FILLER", message)
        self.assertIn("unrelated to the BWC eval", message)


class ContextPressureSettingsTest(TestCase):
    def test_zero_pressure(self) -> None:
        self.assertEqual(
            context_pressure_settings(0, 0),
            {
                "context_pressure_turns": 0,
                "context_pressure_chars": 0,
                "context_pressure_total_chars": 0,
                "context_pressure_placement": "none",
            },
        )

    def test_nonzero_pressure(self) -> None:
        settings = context_pressure_settings(5, 4000, "after_locator")
        self.assertEqual(settings["context_pressure_total_chars"], 20000)
        self.assertEqual(settings["context_pressure_placement"], "after_locator")


class ShouldApplyContextPressureTest(TestCase):
    def test_none(self) -> None:
        self.assertFalse(should_apply_context_pressure("none", 1))
        self.assertFalse(should_apply_context_pressure("none", 2))

    def test_before_locator(self) -> None:
        self.assertTrue(should_apply_context_pressure("before_locator", 1))
        self.assertFalse(should_apply_context_pressure("before_locator", 2))

    def test_after_locator(self) -> None:
        self.assertFalse(should_apply_context_pressure("after_locator", 1))
        self.assertTrue(should_apply_context_pressure("after_locator", 2))

    def test_before_each_turn(self) -> None:
        self.assertTrue(should_apply_context_pressure("before_each_turn", 1))
        self.assertTrue(should_apply_context_pressure("before_each_turn", 16))


class ApplyContextPressureTest(TestCase):
    def test_apply_context_pressure_calls_openclaw(self) -> None:
        calls: list[str] = []

        def fake_run_openclaw_json(message, session_key, model, timeout, log_path):
            calls.append(message)
            self.assertEqual(session_key, "session")
            self.assertIn("pressure_ack", message)
            return ({"pressure_ack": True}, 10, 1)

        with TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "openclaw.log"
            apply_context_pressure(2, 50, "session", "model", 30, log_path, fake_run_openclaw_json)

        self.assertEqual(len(calls), 2)
        self.assertIn("Context pressure turn 1", calls[0])
        self.assertIn("Context pressure turn 2", calls[1])

    def test_apply_context_pressure_rejects_missing_ack(self) -> None:
        def bad_run_openclaw_json(message, session_key, model, timeout, log_path):
            return ({"ready": True}, 10, 1)

        with TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "openclaw.log"
            with self.assertRaisesRegex(RuntimeError, "did not acknowledge context pressure"):
                apply_context_pressure(1, 50, "session", "model", 30, log_path, bad_run_openclaw_json)

    def test_apply_context_pressure_noop_when_disabled(self) -> None:
        calls: list[str] = []

        def track(message, session_key, model, timeout, log_path):
            calls.append(message)
            return ({"pressure_ack": True}, 0, 0)

        with TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "openclaw.log"
            apply_context_pressure(0, 100, "session", "model", 30, log_path, track)
            apply_context_pressure(5, 0, "session", "model", 30, log_path, track)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    main()
