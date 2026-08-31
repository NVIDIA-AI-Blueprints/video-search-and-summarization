######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
######################################################################################################

import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import canary_executor


def valid_manifest():
    return {
        "schema_version": 1,
        "run_id": "rtvi-canary-20260829T010203Z",
        "host": "runner@example",
        "expected_hostname": "gpu-host",
        "repo": "/work/rtvi-microservices",
        "repo_commit": "a" * 40,
        "benchmark_python": "/venv/bin/python",
        "config": "perf/benchmark/rtvi_vlm_config_h100.yaml",
        "scenario": "concurrency_test_1_token",
        "service_image": "rtvi:canary",
        "service_image_id": "sha256:" + "1" * 64,
        "mediamtx_image": "mediamtx:1",
        "mediamtx_image_id": "sha256:" + "2" * 64,
        "ffmpeg_image": "ffmpeg:1",
        "ffmpeg_image_id": "sha256:" + "3" * 64,
        "compose_env": "/fixtures/compose.env",
        "model_cache": "/cache/models",
        "video": "/fixtures/video.mp4",
        "video_sha256": "4" * 64,
        "output_root": "/runs",
        "public_host": "10.0.0.4",
        "gpu_index": 0,
        "gpu_uuid": "GPU-1234",
        "ports": {"backend": 8010, "rtsp": 8554, "dcgm": 9400, "node": 19100},
        "timeouts": {"ready": 900, "benchmark": 720},
        "plan": {
            "schema_version": 1,
            "name": "one-stream-canary",
            "benchmark_command": ["python3", "perf/benchmark/rtvi_perf_benchmark.py"],
            "environment": {
                "code_commit": "a" * 40,
                "container_digest": "sha256:" + "1" * 64,
                "runtime_policy": "fresh_per_run",
                "clean_source_identity": "a" * 40,
                "cache_policy": "empty",
                "model_revision": "model",
                "hardware": "H100",
                "precision": "bf16",
            },
            "paths": {
                "output": "/runs/rtvi-canary-20260829T010203Z/output",
                "scratch": "/runs/rtvi-canary-20260829T010203Z/scratch",
                "mutable_cache": "/runs/rtvi-canary-20260829T010203Z/cache",
            },
            "workload": {
                "media": "4" * 64,
                "prompt": "prompt-hash",
                "input_tokens": 2048,
                "output_tokens": 1,
                "load_unit": "independent_live_stream",
                "source_identity_policy": "unique_per_stream",
                "source_identity_count": 1,
                "session_reuse": False,
            },
            "measurement": {
                "claim": "canary",
                "warmup_runs": 0,
                "repetitions": 3,
                "metrics": ["p95_latency_ms"],
            },
            "scenarios": [
                {"name": "concurrency_test_1_token", "concurrency_levels": [1]}
            ],
        },
    }


class CanaryExecutorTests(unittest.TestCase):
    def test_validates_one_stream_identity_and_builds_one_remote_job(self):
        manifest = canary_executor.resolve_manifest(valid_manifest())
        launch = canary_executor.build_launch(manifest, "/tmp/manifest.json", "/bundle")

        self.assertRegex(
            manifest["project"], r"^rtvicanary20260829t010203z-[0-9a-f]{32}$"
        )
        self.assertEqual(len(launch["stage_argv"]), 2)
        self.assertEqual(
            launch["start_argv"][:6],
            ["ssh", "runner@example", "bash", "--noprofile", "--norc", "-lc"],
        )
        self.assertEqual(
            launch["wait_argv"][:6],
            ["ssh", "runner@example", "bash", "--noprofile", "--norc", "-lc"],
        )
        self.assertNotIn("wait-for", launch["start_argv"][6])
        self.assertIn(
            f"tmux -L {manifest['project']}-executor", launch["start_argv"][6]
        )
        self.assertIn("default-shell /bin/bash", launch["start_argv"][6])
        self.assertIn("runner.log", launch["start_argv"][6])
        self.assertIn("status.json", launch["wait_argv"][6])
        self.assertIn('"result": "(PASS|FAIL)"', launch["wait_argv"][6])

    def test_rejects_non_unique_paths_and_unpinned_identity(self):
        manifest = valid_manifest()
        manifest["plan"]["paths"]["scratch"] = manifest["plan"]["paths"]["output"]
        with self.assertRaisesRegex(ValueError, "distinct"):
            canary_executor.resolve_manifest(manifest)

        manifest = valid_manifest()
        paths = manifest["plan"]["paths"]
        paths["output"], paths["mutable_cache"] = (
            paths["mutable_cache"],
            paths["output"],
        )
        with self.assertRaisesRegex(ValueError, "run-owned"):
            canary_executor.resolve_manifest(manifest)

        manifest = valid_manifest()
        manifest["service_image_id"] = "latest"
        with self.assertRaisesRegex(ValueError, "service_image_id"):
            canary_executor.resolve_manifest(manifest)

    def test_rejects_unsafe_ssh_host(self):
        for host in ("-oProxyCommand=bad", "runner@example:22", "runner@host name"):
            manifest = valid_manifest()
            manifest["host"] = host
            with self.assertRaisesRegex(ValueError, "safe SSH"):
                canary_executor.resolve_manifest(manifest)

    def test_accepts_two_independent_streams_and_rejects_capacity_load(self):
        manifest = valid_manifest()
        manifest["plan"]["scenarios"][0]["concurrency_levels"] = [2]
        manifest["plan"]["workload"]["source_identity_count"] = 2
        resolved = canary_executor.resolve_manifest(manifest)
        self.assertEqual(resolved["stream_count"], 2)

        manifest["plan"]["scenarios"][0]["concurrency_levels"] = [3]
        manifest["plan"]["workload"]["source_identity_count"] = 3
        with self.assertRaisesRegex(
            ValueError, "one, two, four, eight, sixteen, or thirty-two"
        ):
            canary_executor.resolve_manifest(manifest)

        manifest = valid_manifest()
        manifest["plan"]["environment"]["code_commit"] = "b" * 40
        with self.assertRaisesRegex(ValueError, "repo_commit"):
            canary_executor.resolve_manifest(manifest)

    def test_accepts_four_independent_semantic_sources(self):
        manifest = valid_manifest()
        manifest["semantic_isolation"] = True
        manifest["plan"]["scenarios"][0]["concurrency_levels"] = [4]
        manifest["plan"]["workload"]["source_identity_count"] = 4

        resolved = canary_executor.resolve_manifest(manifest)

        self.assertEqual(resolved["stream_count"], 4)
        self.assertEqual(
            canary_executor.semantic_colors(4), ("red", "blue", "green", "yellow")
        )

    def test_accepts_eight_independent_semantic_sources(self):
        manifest = valid_manifest()
        manifest["semantic_isolation"] = True
        manifest["plan"]["scenarios"][0]["concurrency_levels"] = [8]
        manifest["plan"]["workload"]["source_identity_count"] = 8

        resolved = canary_executor.resolve_manifest(manifest)

        self.assertEqual(resolved["stream_count"], 8)
        self.assertEqual(
            canary_executor.semantic_colors(8),
            ("red", "blue", "green", "yellow", "orange", "pink", "white", "black"),
        )

    def test_accepts_sixteen_distinguishable_semantic_sources(self):
        manifest = valid_manifest()
        manifest["semantic_isolation"] = True
        manifest["plan"]["scenarios"][0]["concurrency_levels"] = [16]
        manifest["plan"]["workload"]["source_identity_count"] = 16

        resolved = canary_executor.resolve_manifest(manifest)

        self.assertEqual(resolved["stream_count"], 16)
        self.assertEqual(len(resolved["semantic_sources"]), 16)
        self.assertEqual(resolved["semantic_sources"][:2], ["red-solid", "red-border"])
        self.assertEqual(
            resolved["semantic_sources"][-2:], ["black-solid", "black-border"]
        )
        self.assertEqual(canary_executor.semantic_video_filter("red-solid"), "")
        self.assertIn(
            "drawbox=x=0:y=0:w=iw:h=ih",
            canary_executor.semantic_video_filter("red-border"),
        )
        self.assertIn(
            "color=white", canary_executor.semantic_video_filter("black-border")
        )

    def test_accepts_checksum_pinned_object_media_for_qualification(self):
        manifest = valid_manifest()
        manifest["semantic_isolation"] = True
        manifest["qualification_only"] = True
        manifest["plan"]["scenarios"][0]["concurrency_levels"] = [16]
        manifest["plan"]["workload"]["source_identity_count"] = 16
        manifest["semantic_media"] = {
            f"object-{index:02d}": {
                "path": f"/fixtures/object-{index:02d}.jpg",
                "sha256": f"{index:064x}",
            }
            for index in range(1, 17)
        }

        resolved = canary_executor.resolve_manifest(manifest)

        self.assertEqual(
            resolved["semantic_sources"], sorted(manifest["semantic_media"])
        )
        self.assertTrue(resolved["qualification_only"])
        self.assertEqual(resolved["semantic_task"], "object")
        volume, source = canary_executor.semantic_publisher_input(
            "object-01", resolved["semantic_media"]
        )
        self.assertEqual(volume, ["-v", "/fixtures/object-01.jpg:/fixture.jpg:ro"])
        self.assertEqual(
            source[:6], ["-loop", "1", "-framerate", "10", "-i", "/fixture.jpg"]
        )
        self.assertIn("scale=trunc(iw/2)*2:trunc(ih/2)*2", source)

        manifest["semantic_media"]["object-01"]["sha256"] = "bad"
        with self.assertRaisesRegex(ValueError, "semantic_media"):
            canary_executor.resolve_manifest(manifest)

    def test_accepts_thirty_two_only_with_checksum_pinned_object_media(self):
        manifest = valid_manifest()
        manifest["semantic_isolation"] = True
        manifest["plan"]["scenarios"][0]["concurrency_levels"] = [32]
        manifest["plan"]["workload"]["source_identity_count"] = 32
        manifest["semantic_media"] = {
            f"object-{index:02d}": {
                "path": f"/fixtures/object-{index:02d}.jpg",
                "sha256": f"{index:064x}",
            }
            for index in range(1, 33)
        }

        resolved = canary_executor.resolve_manifest(manifest)

        self.assertEqual(resolved["stream_count"], 32)
        self.assertEqual(len(resolved["semantic_sources"]), 32)

        manifest.pop("semantic_media")
        with self.assertRaisesRegex(ValueError, "semantic_media"):
            canary_executor.resolve_manifest(manifest)

    def test_semantic_isolation_requires_two_streams(self):
        manifest = valid_manifest()
        manifest["semantic_isolation"] = True
        with self.assertRaisesRegex(
            ValueError, "two, four, eight, sixteen, or thirty-two"
        ):
            canary_executor.resolve_manifest(manifest)

        manifest["plan"]["scenarios"][0]["concurrency_levels"] = [2]
        manifest["plan"]["workload"]["source_identity_count"] = 2
        resolved = canary_executor.resolve_manifest(manifest)
        self.assertTrue(resolved["semantic_isolation"])

    def test_semantic_score_rejects_swapped_mixed_and_missing_outputs(self):
        passed = canary_executor.score_semantic_isolation(
            {"red": ["RED", "dominant color: red"], "blue": ["BLUE", "blue."]},
            samples=2,
        )
        self.assertEqual(passed["status"], "PASS")

        passed = canary_executor.score_semantic_isolation(
            {"object-01": ["OBJECT 01"], "object-02": ["OBJECT 02"]},
            samples=1,
            expected=("object-01", "object-02"),
        )
        self.assertEqual(passed["status"], "PASS")
        with self.assertRaisesRegex(ValueError, "semantic isolation"):
            canary_executor.score_semantic_isolation(
                {"object-01": ["OBJECT 02"], "object-02": ["OBJECT 02"]},
                samples=1,
                expected=("object-01", "object-02"),
            )

        passed = canary_executor.score_semantic_isolation(
            {"red-solid": ["RED SOLID"], "red-border": ["RED BORDER"]},
            samples=1,
            expected=("red-solid", "red-border"),
        )
        self.assertEqual(passed["status"], "PASS")
        with self.assertRaisesRegex(ValueError, "semantic isolation"):
            canary_executor.score_semantic_isolation(
                {"red-solid": ["RED BORDER"], "red-border": ["RED BORDER"]},
                samples=1,
                expected=("red-solid", "red-border"),
            )

        for outputs in (
            {"red": ["BLUE"], "blue": ["RED"]},
            {"red": ["RED and BLUE"], "blue": ["BLUE"]},
            {"red": ["RED"], "blue": []},
        ):
            with self.assertRaisesRegex(ValueError, "semantic isolation"):
                canary_executor.score_semantic_isolation(outputs, samples=1)

        passed = canary_executor.score_semantic_isolation(
            {
                "red": ["RED"],
                "blue": ["BLUE"],
                "green": ["GREEN"],
                "yellow": ["YELLOW"],
            },
            samples=1,
            expected=("red", "blue", "green", "yellow"),
        )
        self.assertEqual(passed["status"], "PASS")

    def test_extracts_live_caption_before_stream_finish(self):
        self.assertEqual(
            canary_executor.caption_from_event(
                {"chunk_responses": [{"chunk_id": 0, "content": "RED"}]}
            ),
            "RED",
        )
        self.assertEqual(
            canary_executor.caption_from_event(
                {"choices": [{"delta": {"content": "RED"}, "finish_reason": None}]}
            ),
            "RED",
        )
        self.assertIsNone(
            canary_executor.caption_from_event(
                {"choices": [{"delta": {}, "finish_reason": "stop"}]}
            )
        )

    def test_parses_terminal_status_after_remote_shell_banner(self):
        status = canary_executor.status_from_output(
            'limit: No such limit.\n{"result": "PASS", "state": "completed"}\n'
        )
        self.assertEqual(status["result"], "PASS")

    def test_wait_returns_on_terminal_status_without_waiting_for_eof(self):
        started = time.monotonic()
        status = canary_executor.wait_for_status(
            [
                sys.executable,
                "-c",
                (
                    "import json,time; "
                    "print(json.dumps({'result':'PASS','state':'completed'}), flush=True); "
                    "time.sleep(60)"
                ),
            ],
            timeout=5,
        )

        self.assertEqual(status["result"], "PASS")
        self.assertLess(time.monotonic() - started, 2)

    def test_wait_ignores_pass_until_cleanup_is_terminal(self):
        status = canary_executor.wait_for_status(
            [
                sys.executable,
                "-c",
                (
                    "import json,time; "
                    "print(json.dumps({'result':'PASS','state':'running'}), flush=True); "
                    "time.sleep(.1); "
                    "print(json.dumps({'result':'PASS','state':'completed'}), flush=True); "
                    "time.sleep(60)"
                ),
            ],
            timeout=5,
        )

        self.assertEqual(status["state"], "completed")

    def test_status_wait_timeout_accounts_for_semantic_phases(self):
        plain = canary_executor.resolve_manifest(valid_manifest())
        semantic_manifest = valid_manifest()
        semantic_manifest["plan"]["workload"]["source_identity_count"] = 2
        semantic_manifest["plan"]["scenarios"][0]["concurrency_levels"] = [2]
        semantic_manifest["semantic_isolation"] = True
        semantic = canary_executor.resolve_manifest(semantic_manifest)

        self.assertEqual(
            canary_executor.status_wait_timeout(plain),
            canary_executor.startup_timeout_budget(plain["stream_count"])
            + plain["timeouts"]["ready"]
            + plain["timeouts"]["benchmark"]
            + canary_executor.WATCHER_BASE_GRACE
            + canary_executor.cleanup_timeout_budget(plain["stream_count"]),
        )
        self.assertGreater(
            canary_executor.status_wait_timeout(semantic),
            canary_executor.status_wait_timeout(plain),
        )
        self.assertEqual(
            canary_executor.status_wait_timeout(semantic)
            - canary_executor.status_wait_timeout(plain),
            canary_executor.HTTP_TIMEOUT * (semantic["stream_count"] + 3)
            + 10
            + min(120, semantic["timeouts"]["benchmark"])
            + 2 * canary_executor.SEMANTIC_DELETE_TIMEOUT
            + canary_executor.SEMANTIC_DRAIN_TIMEOUT
            + canary_executor.startup_timeout_budget(semantic["stream_count"])
            - canary_executor.startup_timeout_budget(plain["stream_count"])
            + canary_executor.cleanup_timeout_budget(semantic["stream_count"])
            - canary_executor.cleanup_timeout_budget(plain["stream_count"]),
        )

    def test_cleanup_timeout_is_terminal_and_evidenced(self):
        with tempfile.TemporaryDirectory() as directory:
            run = object.__new__(canary_executor.RemoteRun)
            run.logs = Path(directory)
            run.result = "RUNNING"

            def stall():
                raise subprocess.TimeoutExpired(["docker", "rm"], 3)

            result = run.bounded_cleanup("docker rm", stall)

            self.assertEqual(result.returncode, 124)
            self.assertEqual(run.result, "FAIL")
            self.assertIn(
                "docker rm: exceeded 3s",
                (run.logs / "cleanup-timeouts.log").read_text(),
            )

    def test_created_auxiliary_id_is_retained_without_discovery(self):
        run = object.__new__(canary_executor.RemoteRun)
        run.aux_by_name = {}
        created = subprocess.CompletedProcess(["docker", "run"], 0, "a" * 64 + "\n")

        run.remember_auxiliary("publisher-1", created)

        self.assertEqual(run.aux_by_name, {"publisher-1": "a" * 64})

    def test_remote_commands_have_a_default_subprocess_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            run = object.__new__(canary_executor.RemoteRun)
            run.logs = Path(directory)
            completed = subprocess.CompletedProcess(["docker", "logs"], 0, "")

            with mock.patch.object(subprocess, "run", return_value=completed) as called:
                run.command("docker", "logs", "container", check=False)

            self.assertEqual(
                called.call_args.kwargs["timeout"],
                canary_executor.RUNTIME_COMMAND_TIMEOUT,
            )

    def test_checksum_deadline_leaves_no_partial_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "artifact.log"
            output = root / "checksums.sha256"
            artifact.write_text("evidence")
            ticks = iter([0.0, 31.0])

            with self.assertRaisesRegex(TimeoutError, "checksum deadline"):
                canary_executor.write_checksums(
                    root, [artifact], output, timeout=30, now=lambda: next(ticks)
                )

            self.assertFalse(output.exists())
            self.assertFalse((root / "checksums.sha256.tmp").exists())

    def test_requires_fresh_measurements_from_each_independent_source(self):
        record = {
            "iteration": 1,
            "success": True,
            "stream_count": 2,
            "actual_streams_started": 2,
            "streams_with_errors": 0,
            "rtsp_urls": ["rtsp://host/bcd-1", "rtsp://host/bcd-2"],
            "unique_rtsp_url_per_stream": True,
            "rtsp_url_source_count": 2,
            "rtsp_url_pool_exhausted": False,
            "rtsp_url_reuse_count": 0,
            "skipped_rtsp_source_count": 0,
            "per_stream_stats": {
                "stream-a": {"total_measurements": 12},
                "stream-b": {"total_measurements": 11},
            },
            "latency_history": {"stream-a": [0.5], "stream-b": [0.6]},
        }
        summary = canary_executor.validate_source_coverage(
            [record, {**record, "iteration": 2}], 2, 2
        )
        self.assertEqual(summary["iterations"], 2)
        self.assertEqual(summary["measurements_per_iteration"], [23, 23])

        record["per_stream_stats"]["stream-b"]["total_measurements"] = 0
        with self.assertRaisesRegex(ValueError, "fresh measurements"):
            canary_executor.validate_source_coverage([record], 2, 1)


if __name__ == "__main__":
    unittest.main()
