# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import os
from pathlib import Path
import re
import subprocess
import unittest


SKILL = Path(__file__).resolve().parents[2]


class DeployExamplesTest(unittest.TestCase):
    def run_health_wait(self, payload):
        text = (SKILL / "references/deploy-rt-vlm-service.md").read_text(encoding="utf-8")
        loop = re.search(r"^HEALTH_DEADLINE=.*?^done$", text, re.MULTILINE | re.DOTALL)[0]
        return subprocess.run(
            [
                "bash",
                "-c",
                """set -euo pipefail
docker_cmd() {
  if [ "$1" = compose ]; then printf '%s' "$HEALTH_FIXTURE"; fi
}
sleep() { SECONDS=$((SECONDS + 1201)); }
COMPOSE_FILE=unused
"""
                + loop
                + "\nprintf 'ready\\n'",
            ],
            env={**os.environ, "HEALTH_FIXTURE": payload},
            capture_output=True,
            text=True,
            timeout=5,
        )

    def test_healthy_compose_json_formats(self):
        for payload in (
            '{"Health":"healthy"}',
            '[{"Health":"healthy"}]',
            '{"Health":"healthy"}\n{"Health":"healthy"}',
            '[{"Health":"healthy"},{"Health":"healthy"}]',
        ):
            with self.subTest(payload=payload):
                result = self.run_health_wait(payload)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "ready\n")

    def test_empty_or_unhealthy_output_times_out(self):
        for payload in (
            "",
            "[]",
            "{}",
            '{"Health":"starting"}',
            '{"Health":"healthy"}\n{"Health":"unhealthy"}',
            '[{"Health":"healthy"},{}]',
        ):
            with self.subTest(payload=payload):
                result = self.run_health_wait(payload)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("did not become healthy", result.stderr)
                self.assertNotIn("ready\n", result.stdout)

    def test_invalid_json_fails_immediately(self):
        result = self.run_health_wait("not json")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("failed to parse rtvi-vlm health", result.stderr)
        self.assertNotIn("waiting", result.stdout)

    def test_sse_example_captions_are_nested(self):
        text = (SKILL / "references/api-surface-26.05.md").read_text(encoding="utf-8")
        block = re.search(r"```text\n(data: .*?)\n```", text, re.DOTALL)[1]
        events = [
            line.removeprefix("data: ")
            for line in block.splitlines()
            if line.startswith("data: ")
        ]
        self.assertEqual(events[-1], "[DONE]")
        event = json.loads(events[0])
        self.assertIn("model", event)
        self.assertIn("media_info", event)
        self.assertNotIn("content", event)
        self.assertEqual(
            event["chunk_responses"][0]["content"],
            "A person walks through the warehouse.",
        )


if __name__ == "__main__":
    unittest.main()
