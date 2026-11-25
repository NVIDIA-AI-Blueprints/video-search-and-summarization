# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Idempotence tests for the VideoMME memory-seeding helper."""

import json
import os
import shutil
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(os.environ.get("VSS_TEST_SOURCE_ROOT", Path(__file__).resolve().parents[2]))
HELPER = ROOT / ".openclaw/helpers/seed_videomme_memory.sh"


@unittest.skipUnless(shutil.which("flock"), "flock is required by the runtime helper")
class SeedVideoMmeMemory(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.home = self.root / "home"
        self.logs = self.root / "logs"
        self.state = self.root / "state"
        self.bin = self.root / "bin"
        self.counter = self.root / "summary-count"
        self.home.joinpath(".vss").mkdir(parents=True)
        self.bin.mkdir()

        identity = {
            "trial_uuid": "11111111-2222-3333-4444-555555555555",
            "memory_index": (
                "vss-memory-video-mme-11111111-2222-3333-4444-555555555555"
            ),
        }
        self.home.joinpath(".vss/videomme-trial.json").write_text(
            json.dumps(identity)
        )

        fake_vss = self.bin / "vss"
        fake_vss.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                set -euo pipefail

                if [[ "$1" == "configure" ]]; then
                  exit 0
                fi

                if [[ "$1 $2" == "vios timeline" ]]; then
                  printf '%s\n' '{"segments":[{"start_time":"2026-01-01T00:00:00Z"}]}'
                  exit 0
                fi

                if [[ "$1 $2" == "vios clip" ]]; then
                  printf '%s\n' '{"media_url":"http://source/video.mp4","name":"video.mp4"}'
                  exit 0
                fi

                if [[ "$1 $2" == "summarize run" ]]; then
                  printf 'summary\n' >> "${FAKE_SUMMARY_COUNTER}"
                  sleep 0.25
                  if [[ "${FAKE_SUMMARY_FAIL:-0}" == "1" ]]; then
                    exit 42
                  fi
                  mkdir -p "${HOME}/.openclaw/workspace/memory"
                  printf '%s\n' '<!-- vss-job:job-1 -->' \
                    > "${HOME}/.openclaw/workspace/memory/job-1.md"
                  printf '%s' \
                    '{"job_id":"job-1","persist":{"status":"complete"},'
                  printf '%s\n' \
                    '"record":"closed","memory_note":{"written":true}}'
                  exit 0
                fi

                if [[ "$1 $2" == "memory get" ]]; then
                  printf '%s\n' '{"job":{"job_id":"job-1","status":"completed"}}'
                  exit 0
                fi

                printf 'unexpected fake vss call: %s\n' "$*" >&2
                exit 99
                """
            )
        )
        fake_vss.chmod(fake_vss.stat().st_mode | stat.S_IXUSR)

        self.environment = {
            **os.environ,
            "HOME": str(self.home),
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "VSS_GATEWAY_ORIGIN": "http://vss.example",
            "VSS_EVAL_RUN_ID": "test-run",
            "VSS_EVAL_TASK_ID": "test-task",
            "VSS_VIDEO_ID": "test-video",
            "VSS_SETUP_LOG_DIR": str(self.logs),
            "VSS_SETUP_STATE_DIR": str(self.state),
            "FAKE_SUMMARY_COUNTER": str(self.counter),
        }

    def tearDown(self):
        self.temporary_directory.cleanup()

    def invoke(self, **environment):
        return subprocess.Popen(
            ["bash", str(HELPER), "sensor-1"],
            env={**self.environment, **environment},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def test_concurrent_duplicate_waits_and_reuses_verified_success(self):
        first = self.invoke()
        second = self.invoke()

        first_output = first.communicate(timeout=10)
        second_output = second.communicate(timeout=10)

        self.assertEqual(first.returncode, 0, first_output)
        self.assertEqual(second.returncode, 0, second_output)
        self.assertEqual(self.counter.read_text().splitlines(), ["summary"])
        result = json.loads(self.logs.joinpath("setup-result.json").read_text())
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(result["job_id"], "job-1")
        self.assertFalse(self.logs.joinpath("setup-failure.json").exists())

    def test_terminal_failure_is_sticky_and_not_retried(self):
        first = self.invoke(FAKE_SUMMARY_FAIL="1")
        first_output = first.communicate(timeout=10)
        self.assertEqual(first.returncode, 6, first_output)

        second = self.invoke()
        second_output = second.communicate(timeout=10)
        self.assertEqual(second.returncode, 6, second_output)
        self.assertEqual(self.counter.read_text().splitlines(), ["summary"])
        failure = json.loads(self.logs.joinpath("setup-failure.json").read_text())
        self.assertEqual(failure["state"], "failed")
        self.assertEqual(failure["exit_code"], 6)
        self.assertEqual(failure["stage"], "full-video-summary")


if __name__ == "__main__":
    unittest.main()
