#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for sanitized multi-format agent/hardware timelines."""

from __future__ import annotations

import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_timeline import timeline as mod  # noqa: E402


class TimelineFixture(unittest.TestCase):
    def _generate(self, root: Path) -> Path:
        trial = root / "2026-08-01__21-00-00" / "step-1__trial" / "agent"
        trial.mkdir(parents=True)
        trajectory = {
            "steps": [
                {
                    "timestamp": "2026-08-01T21:00:00Z",
                    "source": "agent",
                    "message": "SECRET_MESSAGE",
                    "tool_calls": [{
                        "tool_call_id": "toolu_secret_id",
                        "function_name": "Bash",
                        "arguments": {
                            "command": (
                                "API_KEY=CANARYSECRET curl -H "
                                "'Authorization: Bearer CANARYSECRET' "
                                "https://private.invalid/run"
                            )
                        },
                        "observation": {"body": "CANARY_OBSERVATION"},
                    }],
                },
                {
                    "timestamp": "2026-08-01T21:00:10Z",
                    "source": "user",
                    "message": "CANARY_USER_PROMPT",
                },
            ]
        }
        (trial / "trajectory.json").write_text(json.dumps(trajectory))

        token = "run-search-rtx-chain-step1"
        gpu = root / "gputrace"
        gpu.mkdir()
        (gpu / f"{token}.csv").write_text(
            "timestamp,gpu_index,util_gpu_pct,util_mem_pct,mem_used_mib,"
            "mem_total_mib,power_w\n"
            "2026/08/01 21:00:00.000,0,0,2,4096,97887,70\n"
            "2026/08/01 21:00:10.000,0,95,40,50000,97887,320\n"
        )
        epoch = dt.datetime(
            2026, 8, 1, 21, 0, tzinfo=dt.timezone.utc
        ).timestamp()
        (gpu / f"{token}.json").write_text(json.dumps({
            "started_at": epoch,
            "finished_at": epoch + 20,
        }))

        system = root / "systemtrace"
        system.mkdir()
        header = (
            "timestamp_ns,cpu_count,cpu_user_pct,cpu_system_pct,cpu_iowait_pct,"
            "cpu_idle_pct,cpu_steal_pct,load_1m,load_5m,load_15m,mem_used_mib,"
            "mem_available_mib,mem_total_mib,swap_used_mib,swap_total_mib\n"
        )
        timestamp_ns = int(epoch * 1_000_000_000)
        (system / f"{token}.csv").write_text(
            header + f"{timestamp_ns},32,12,4,1,83,0,1,2,3,8000,56000,64000,0,0\n"
        )

        result = mod.generate(
            root,
            include_task_name="step-1",
            trace_token=token,
            started_at=epoch - 1,
            finished_at=epoch + 20,
            metadata={
                "skill": "search",
                "spec_stem": "spec",
                "unsafe": "<script>CANARY_METADATA</script>",
            },
        )
        self.assertIsNotNone(result)
        return result

    def test_generates_all_three_comparison_formats(self):
        with tempfile.TemporaryDirectory() as td:
            dest = self._generate(Path(td))
            names = {path.name for path in dest.iterdir()}
        self.assertEqual(names, {
            "timeline.json",
            "timeline.perfetto.json",
            "timeline.html",
            "timeline.otlp.jsonl",
        })

    def test_raw_trajectory_content_cannot_reach_any_output(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            dest = self._generate(root)
            output = "\n".join(path.read_text() for path in dest.iterdir())
            retained_agent_dirs = list(root.glob("*/*/agent"))
        for secret in (
            "CANARYSECRET", "SECRET_MESSAGE", "CANARY_OBSERVATION",
            "CANARY_USER_PROMPT", "private.invalid", "toolu_secret_id",
        ):
            self.assertNotIn(secret, output)
        self.assertIn("Bash: curl", output)
        self.assertEqual(retained_agent_dirs, [])

    def test_canonical_model_correlates_samples_to_agent_steps(self):
        with tempfile.TemporaryDirectory() as td:
            dest = self._generate(Path(td))
            model = json.loads((dest / "timeline.json").read_text())
        self.assertEqual(model["spans"][0]["id"], "step-1")
        first_gpu = next(
            metric for metric in model["metrics"]
            if metric["name"] == "gpu.utilization"
        )
        self.assertEqual(first_gpu["active_step"], "step-1")
        first_cpu = next(
            metric for metric in model["metrics"]
            if metric["name"] == "cpu.user"
        )
        self.assertEqual(first_cpu["active_step"], "step-1")
        self.assertGreater(
            model["summary"]["waste_signals"][
                "gpu_idle_with_resident_memory_pct"
            ],
            0,
        )
        self.assertFalse(model["privacy"]["raw_commands_included"])

    def test_perfetto_and_otlp_outputs_have_expected_envelopes(self):
        with tempfile.TemporaryDirectory() as td:
            dest = self._generate(Path(td))
            perfetto = json.loads((dest / "timeline.perfetto.json").read_text())
            otlp = [
                json.loads(line)
                for line in (dest / "timeline.otlp.jsonl").read_text().splitlines()
            ]
        self.assertTrue(any(event["ph"] == "X" for event in perfetto["traceEvents"]))
        self.assertTrue(any(event["ph"] == "C" for event in perfetto["traceEvents"]))
        self.assertIn("resourceSpans", otlp[0])
        self.assertIn("resourceMetrics", otlp[1])
        self.assertTrue(
            otlp[0]["resourceSpans"][0]["scopeSpans"][0]["spans"][0][
                "startTimeUnixNano"
            ].isdigit()
        )

    def test_html_is_self_contained(self):
        with tempfile.TemporaryDirectory() as td:
            dest = self._generate(Path(td))
            page = (dest / "timeline.html").read_text()
        self.assertIn("<svg", page)
        self.assertNotIn('src="http', page)
        self.assertNotIn('href="http', page)


class Sanitization(unittest.TestCase):
    def test_command_summary_is_fail_closed(self):
        self.assertEqual(
            mod._command_summary(
                "TOKEN=secret sudo docker compose up && curl https://private"
            ),
            "docker + curl",
        )
        self.assertEqual(mod._command_summary("/secret/custom-tool --token x"), "shell")

    def test_legacy_encoded_tool_calls_are_supported_without_messages(self):
        step = {
            "message": json.dumps({
                "message": {
                    "content": [{
                        "type": "tool_use",
                        "name": "Bash",
                        "input": {"command": "python3 secret.py"},
                    }]
                }
            })
        }
        calls = mod._legacy_calls(step)
        self.assertEqual(calls[0]["function_name"], "Bash")
        self.assertEqual(mod._command_summary(calls[0]["arguments"]["command"]), "python3")

    def test_timestamp_units_are_normalized_without_epoch_shift(self):
        expected = 1_785_618_000_000_000
        self.assertEqual(mod._timestamp_us(expected * 1_000), expected)
        self.assertEqual(mod._timestamp_us(expected), expected)
        self.assertEqual(mod._timestamp_us(expected // 1_000), expected)
        self.assertEqual(mod._timestamp_us(expected / 1_000_000), expected)

    def test_system_clock_is_aligned_to_coordinator_start(self):
        with tempfile.TemporaryDirectory() as td:
            csv_path = Path(td) / "system.csv"
            csv_path.write_text(
                "timestamp_ns,cpu_user_pct\n"
                "1785621600000000000,12.5\n"
            )
            metrics = mod._system_metrics(
                csv_path, {"started_at": 1_785_618_000.0}
            )
        self.assertEqual(metrics[0]["timestamp_us"], 1_785_618_000_000_000)

    def test_metadata_is_field_allowlisted_not_merely_redacted(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            epoch = dt.datetime(
                2026, 8, 1, 21, 0, tzinfo=dt.timezone.utc
            ).timestamp()
            dest = mod.generate(
                root,
                include_task_name="step-1",
                trace_token="trace",
                started_at=epoch,
                finished_at=epoch + 1,
                metadata={"skill": "search", "arbitrary": "CANARYSECRET"},
            )
            model = json.loads((dest / "timeline.json").read_text())
        self.assertEqual(model["metadata"], {"skill": "search"})
        self.assertNotIn("CANARYSECRET", json.dumps(model))

    def test_render_failure_still_deletes_raw_agent_directory(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            agent = root / "2026-08-01" / "step-1__trial" / "agent"
            agent.mkdir(parents=True)
            (agent / "trajectory.json").write_text(json.dumps({
                "steps": [{
                    "timestamp": "2026-08-01T21:00:00Z",
                    "source": "agent",
                    "message": "CANARYSECRET",
                }]
            }))
            epoch = dt.datetime(
                2026, 8, 1, 21, 0, tzinfo=dt.timezone.utc
            ).timestamp()
            with mock.patch.object(mod, "_html", side_effect=OSError("full")):
                with self.assertRaisesRegex(OSError, "full"):
                    mod.generate(
                        root,
                        include_task_name="step-1",
                        trace_token="trace",
                        started_at=epoch - 1,
                        finished_at=epoch + 1,
                    )
            self.assertFalse(agent.exists())

    def test_no_result_or_trajectory_still_generates_archivable_outputs(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            dest = mod.generate(
                root,
                include_task_name="step-1",
                trace_token="timeout-step1",
                started_at=1_785_618_000.0,
                finished_at=1_785_618_010.0,
            )
            names = {path.name for path in dest.iterdir()}
        self.assertEqual(names, {
            "timeline.json",
            "timeline.perfetto.json",
            "timeline.html",
            "timeline.otlp.jsonl",
        })


if __name__ == "__main__":
    unittest.main(verbosity=2)
