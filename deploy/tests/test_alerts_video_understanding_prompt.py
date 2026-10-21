# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Alerts incident analysis must call video_understanding_iso with user_prompt.

Nightly IGX-T7000 alerts-verification (2026-10-18) failed after the planner
called video_understanding_iso with user_query. The tool schema requires
user_prompt and forbids extra fields, so the first call raised:

  2 validation errors for VideoUnderstandingInput
  user_prompt: Field required
  user_query: Extra inputs are not permitted

The agent recovered once, then the plan update kept the validation failure
and execution hit the recursion limit. These prompts are the trigger.
"""

from __future__ import annotations

from pathlib import Path
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
ALERTS_CONFIGS = (
    REPO_ROOT / "docker/developer-profiles/dev-profile-alerts/vss-agent/configs/config.yml",
    REPO_ROOT / "helm/developer-profiles/dev-profile-alerts/configs/vss-agent/config.yml",
)
INCIDENT_PROMPT = (
    '"Describe this incident in detail. What happened? What objects/vehicles '
    'are involved? What are the environmental conditions?"'
)
ISO_GUIDELINE = "Required: sensor_id (sensor NAME, not UUID), user_prompt"


class AlertsVideoUnderstandingPromptTests(unittest.TestCase):
    def test_incident_example_uses_user_prompt_not_user_query(self) -> None:
        for path in ALERTS_CONFIGS:
            with self.subTest(config=str(path.relative_to(REPO_ROOT))):
                text = path.read_text()
                self.assertIn(
                    f'"user_prompt": {INCIDENT_PROMPT}',
                    text,
                    f"{path.name} WORKFLOW 5 example is missing user_prompt",
                )
                self.assertNotIn(
                    f'"user_query": {INCIDENT_PROMPT}',
                    text,
                    f"{path.name} WORKFLOW 5 example still teaches user_query",
                )

    def test_tool_guidelines_require_user_prompt(self) -> None:
        for path in ALERTS_CONFIGS:
            with self.subTest(config=str(path.relative_to(REPO_ROOT))):
                text = path.read_text()
                guideline_block = text.split("4. video_understanding / video_understanding_iso:", 1)
                self.assertEqual(len(guideline_block), 2, f"{path.name} is missing tool guideline 4")
                iso_guidelines = guideline_block[1].split("5. rtvi_vlm_alert:", 1)[0]
                self.assertIn(ISO_GUIDELINE, iso_guidelines)
                self.assertNotIn("user_query", iso_guidelines)


if __name__ == "__main__":
    unittest.main()
