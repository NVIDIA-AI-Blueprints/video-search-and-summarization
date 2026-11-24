# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regression tests for judge verdicts containing copied tool output."""

import importlib.util
from pathlib import Path
import unittest


JUDGE_PATH = Path(__file__).resolve().parents[1] / "verifiers/generic_judge.py"
SPEC = importlib.util.spec_from_file_location("verdict_generic_judge", JUDGE_PATH)
judge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(judge)


class JudgeVerdictJSON(unittest.TestCase):
    def test_literal_tab_in_matched_is_accepted(self):
        # The judge emitted a Docker name/status row with a literal tab in
        # several run-43826085614 Alerts verdicts.
        response = (
            '{"pass": true, "matched": "vss-rtvi-vlm\tUp 11 minutes (healthy)", '
            '"rationale": "Container is running."}'
        )
        self.assertEqual(judge._parse_verdict_json(response)["matched"],
                         "vss-rtvi-vlm\tUp 11 minutes (healthy)")

    def test_last_verdict_wins_even_after_literal_tab(self):
        response = (
            '{"pass": false, "matched": "vss-rtvi-vlm\tUp", "rationale": "First"}\n'
            '{"pass": true, "matched": "vss-rtvi-vlm\tUp", "rationale": "Retry"}'
        )
        self.assertIs(judge._parse_verdict_json(response)["pass"], True)

    def test_unrelated_json_and_broken_syntax_are_not_verdicts(self):
        self.assertIsNone(judge._parse_verdict_json('{"matched": "name\tUp"}'))
        self.assertIsNone(judge._parse_verdict_json(
            '{"pass": true "matched": "name\tUp", "rationale": "Missing comma"}'
        ))

    def test_prompt_requests_escaped_control_characters(self):
        self.assertIn("Escape tabs, newlines", judge._JUDGE_SYSTEM_PROMPT)
        self.assertIn("Escape tabs, newlines", judge._VERDICT_NUDGE)


if __name__ == "__main__":
    unittest.main()
