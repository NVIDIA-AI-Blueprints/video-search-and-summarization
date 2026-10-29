# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import unittest
from pathlib import Path

from http_latency import build_result, encode_multipart, percentile, summarize
from kpis import DEFAULT_KPIS_PATH, evaluate_kpis, load_kpis, resolved_required_endpoints
from openapi_matrix import (
    EXTRA_WAREHOUSE_GETS,
    SKIP_PATHS,
    dump_backed_endpoints,
    dump_backed_names,
    load_spec,
    p0_post_endpoints,
    query_notes,
    query_params_for,
    query_window_s,
    spec_get_endpoints,
    spec_get_names,
)
from scenario_config import scenario_settings

PERF = Path(__file__).resolve().parents[1]
REPO = Path(__file__).resolve().parents[5]
PRE_FLIGHT = PERF / "scripts" / "pre_flight.sh"
DEPLOY = PERF / "scripts" / "deploy.sh"
POST_DEPLOY = PERF / "scripts" / "post_deploy.sh"
COMMON = PERF / "scripts" / "common.sh"
TEARDOWN = PERF / "scripts" / "teardown.sh"
ES_OVERLAY = PERF / "setup" / "compose-es.yml"
INTEGRATION = REPO / "services" / "analytics" / "video-analytics-api" / "test" / "integration-test"
JS_LOADER = INTEGRATION / "scripts" / "load_elasticsearch_data_dump.js"


class HttpLatencyTests(unittest.TestCase):
    def test_percentile_interpolates(self) -> None:
        values = [0.01, 0.02, 0.03, 0.04, 0.10]
        self.assertAlmostEqual(percentile(values, 50) or 0, 0.03)
        self.assertGreater(percentile(values, 99) or 0, percentile(values, 50) or 0)

    def _measured(self, livez, query):
        kpis = load_kpis()
        spec = kpis["scenarios"]["quick_test"]["warehouse-es-dump"]
        measured = {
            "schema_version": "0.1",
            "service": "video-analytics-api",
            "scenario": "quick_test",
            "config_id": "warehouse-es-dump",
            "endpoints": {},
        }
        for name in resolved_required_endpoints(spec):
            samples = livez if name == "livez" else query
            measured["endpoints"][name] = summarize(samples, 20, 20)
            measured["endpoints"][name]["present"] = True
        return kpis, spec, measured

    def test_kpis_yaml_loads_and_gates_result(self) -> None:
        self.assertTrue(DEFAULT_KPIS_PATH.is_file())
        kpis, spec, measured = self._measured(
            [0.0006, 0.0007, 0.0018],
            [0.018, 0.022, 0.035],
        )
        self.assertEqual(spec["required_endpoints"], ["spec_gets"])
        self.assertEqual(spec["livez"]["max_p50_s"], 0.005)
        self.assertEqual(spec["livez"]["max_p99_s"], 0.02)
        self.assertEqual(spec["frames"]["max_p50_s"], 0.05)
        self.assertEqual(spec["frames"]["max_p99_s"], 0.10)
        self.assertEqual(spec["behavior"]["max_p99_s"], 0.08)
        self.assertEqual(spec["default"]["max_p99_s"], 0.20)
        self.assertEqual(spec["config_calibration_images"]["max_p99_s"], 1.00)
        verdict = evaluate_kpis(measured, kpis)
        self.assertTrue(verdict["passed"], verdict)

    def test_openapi_matrix_covers_dump_backed_gets(self) -> None:
        spec = load_spec()
        rows = spec_get_endpoints(spec)
        names = spec_get_names(spec)
        self.assertEqual(len(rows), 40)
        self.assertEqual(len(names), 40)
        self.assertIn("livez", names)
        self.assertIn("frames", names)
        self.assertIn("events", names)
        self.assertIn("metrics_occupancy_fov", names)
        self.assertIn("sensor_lookup", names)
        self.assertIn("config_calibration_image", names)
        paths = {row["path"] for row in rows}
        self.assertNotIn("/behavior/pts", paths)
        self.assertNotIn("/metrics/road-network/segment-speed", paths)
        self.assertIn("/config/calibration/image", paths)
        for skipped in SKIP_PATHS:
            self.assertNotIn(skipped, paths)
        get_count = sum(1 for ops in spec["paths"].values() if "get" in ops)
        self.assertEqual(get_count, 48)
        frames = next(row for row in rows if row["name"] == "frames")
        self.assertNotIn("timestamp", frames["query_keys"])
        occupancy = next(row for row in rows if row["name"] == "metrics_occupancy_tripwire")
        self.assertEqual(occupancy["query_keys"], ["place", "timestamp"])
        image = next(row for row in rows if row["name"] == "config_calibration_image")
        self.assertEqual(image["query_keys"], ["place", "view"])
        for name in ("behavior", "events", "incidents", "alerts"):
            row = next(item for item in rows if item["name"] == name)
            self.assertIn("sensorId", row["query_keys"], name)
            self.assertNotIn("place", row["query_keys"], name)
        events_amr = next(row for row in rows if row["name"] == "events_amr")
        self.assertIn("place", events_amr["query_keys"])
        self.assertNotIn("sensorId", events_amr["query_keys"])
        for row in rows:
            has_sensor = "sensorId" in row["query_keys"] or "sensorIds" in row["query_keys"]
            self.assertFalse(
                has_sensor and "place" in row["query_keys"],
                row,
            )
        self.assertTrue(EXTRA_WAREHOUSE_GETS.issubset(paths))

    def test_p0_posts_seed_calibration_image(self) -> None:
        posts = p0_post_endpoints()
        self.assertEqual(
            [row["name"] for row in posts],
            ["config_upload_file_calibration", "config_calibration_images"],
        )
        for row in posts:
            self.assertEqual(row["method"], "POST")
            self.assertEqual(row["ok_statuses"], (201,))
            self.assertTrue(row["multipart"])
            for _, path in row["multipart"]:
                self.assertTrue(Path(path).is_file(), path)
        names = dump_backed_names()
        self.assertEqual(names[:2], [row["name"] for row in posts])
        self.assertIn("config_calibration_image", names)
        self.assertEqual(len(dump_backed_endpoints()), 42)
        body, content_type = encode_multipart(posts[1]["multipart"])
        self.assertIn("multipart/form-data; boundary=", content_type)
        self.assertIn(b'filename="Top.png"', body)
        self.assertIn(b'filename="calibration_image_metadata.json"', body)

    def test_frames_query_keeps_range_without_timestamp(self) -> None:
        frames = next(row for row in spec_get_endpoints() if row["name"] == "frames")
        params = query_params_for(
            frames,
            {
                "sensorId": "Camera",
                "fromTimestamp": "2026-02-14T10:16:00.000Z",
                "toTimestamp": "2026-02-14T10:17:00.000Z",
                "timestamp": "2026-02-14T10:17:00.000Z",
            },
        )
        self.assertEqual(
            params,
            {
                "sensorId": "Camera",
                "fromTimestamp": "2026-02-14T10:16:00.000Z",
                "toTimestamp": "2026-02-14T10:17:00.000Z",
                "maxResultSize": "25",
            },
        )
        notes = query_notes(frames, params)
        self.assertEqual(notes["query_window_s"], 60.0)
        self.assertEqual(notes["maxResultSize"], 25)

    def test_query_window_and_size_limits(self) -> None:
        self.assertEqual(
            query_window_s(
                {
                    "fromTimestamp": "2026-02-14T10:16:00.000Z",
                    "toTimestamp": "2026-02-14T10:17:00.000Z",
                }
            ),
            60.0,
        )
        flowrate = next(row for row in spec_get_endpoints() if row["name"] == "metrics_flowrate")
        clustering = next(row for row in spec_get_endpoints() if row["name"] == "clustering_behavior")
        alerts = next(row for row in spec_get_endpoints() if row["name"] == "frames_alerts")
        self.assertEqual(flowrate["limits"]["timeWindowSizeInSec"], 5)
        self.assertEqual(clustering["limits"]["maxClusterSampleSize"], 100)
        self.assertEqual(alerts["limits"]["maxResultSize"], 25)
        flow_params = query_params_for(
            flowrate,
            {"sensorId": "Camera", "toTimestamp": "2026-02-14T10:17:00.000Z"},
        )
        self.assertEqual(flow_params["timeWindowSizeInSec"], "5")
        self.assertEqual(query_notes(flowrate, flow_params)["query_window_s"], 5.0)

    def test_slow_frames_p99_fails(self) -> None:
        kpis, _, measured = self._measured(
            [0.0006, 0.0007, 0.0018],
            [0.018, 0.022, 0.035],
        )
        measured["endpoints"]["frames"] = summarize([0.04, 0.045, 0.20], 20, 20)
        measured["endpoints"]["frames"]["present"] = True
        verdict = evaluate_kpis(measured, kpis)
        self.assertFalse(verdict["passed"])
        self.assertTrue(
            any("frames.p99_s" in item for item in verdict["violations"]),
            verdict,
        )

    def test_missing_endpoint_fails(self) -> None:
        kpis = load_kpis()
        payload = build_result(
            {"livez": summarize([0.01], 1, 1)},
            artifact_ref="nvcr.io/example/video-analytics-api:test",
            scenario="quick_test",
            config_id="warehouse-es-dump",
            request_count=1,
            warmup_requests=0,
        )
        self.assertFalse(payload["success"])
        self.assertTrue(payload["kpis"]["violations"])

    def test_integration_js_loader_rejects_lfs_and_bulk_errors(self) -> None:
        text = JS_LOADER.read_text()
        self.assertIn("git-lfs.github.com", text)
        self.assertIn("json.errors", text)
        lifecycle = (INTEGRATION / "lifecycle.sh").read_text()
        self.assertIn("load_elasticsearch_data_dump.js", lifecycle)
        self.assertIn("va_api_load_elasticsearch_dumps", POST_DEPLOY.read_text())
        self.assertNotIn("dump_loader.py", POST_DEPLOY.read_text())
        self.assertFalse((PERF / "benchmark" / "dump_loader.py").exists())

    def test_scenario_config_reads_request_count(self) -> None:
        settings = scenario_settings(PERF / "configs" / "config.yaml", "quick_test")
        self.assertEqual(settings["request_count"], 20)
        self.assertEqual(settings["sensor_id"], "Camera")
        self.assertEqual(settings["place"], "building=Warehouse/room=Room-1")
        self.assertEqual(settings["endpoints"], ["spec_gets"])

    def test_pre_flight_does_not_chmod_0777(self) -> None:
        text = PRE_FLIGHT.read_text()
        self.assertNotIn("0777", text)
        self.assertNotIn("chmod 777", text)

    def test_env_file_is_compose_key_value(self) -> None:
        text = COMMON.read_text()
        generate_env = (INTEGRATION / "generate_env.sh").read_text()
        self.assertIn("Docker Compose --env-file does not accept", text)
        self.assertIn("ELASTICSEARCH_HOST_PORT=$ELASTICSEARCH_HOST_PORT", text)
        self.assertNotIn("export ELASTICSEARCH_HOST_PORT=", text)
        self.assertNotIn("export COMPOSE_PROJECT_NAME=", generate_env)
        self.assertIn("PERF_CONFIG_PATH", text)
        self.assertIn("generate_env.sh", text)
        self.assertIn("lifecycle.sh", text)
        self.assertNotIn("compose/docker", text)

    def test_deploy_starts_elasticsearch_first_and_dumps_logs(self) -> None:
        text = DEPLOY.read_text()
        self.assertIn("--no-deps --force-recreate elasticsearch", text)
        self.assertIn("wait_for_elasticsearch", text)
        self.assertIn("dump_es_debug", text)
        self.assertIn("$ES_INIT_CONTAINER", text)

    def test_es_overlay_bind_mounts_and_disables_mmap(self) -> None:
        text = ES_OVERLAY.read_text()
        self.assertIn("elastic/data:/tmp/elastic/data", text)
        self.assertIn("node.store.allow_mmap", text)
        self.assertIn("tmpfs: !override []", text)

    def test_teardown_is_scoped_and_marker_protected(self) -> None:
        common = COMMON.read_text()
        teardown = TEARDOWN.read_text()
        self.assertIn("VA_API_PERF_OWNERSHIP_MARKER", common)
        self.assertIn("prepare_benchmark_dir", common)
        self.assertIn("canonical_benchmark_dir", common)
        self.assertIn("cleanup_docker_environment", teardown)
        self.assertIn('"$ES_INIT_CONTAINER"', teardown)
        self.assertNotIn("rm -rf", teardown)

    def test_service_entrypoint_uses_uv_and_canonical_runner(self) -> None:
        runner = (PERF / "scripts" / "run_benchmark.sh").read_text()
        self.assertIn("setup_environment.sh", runner)
        self.assertIn("benchmark/core/vss_perf_benchmark.py", runner)
        self.assertIn("PERF_NATIVE_ONLY", runner)
        self.assertFalse((PERF / "run_benchmark.sh").exists())
        self.assertFalse((PERF / "benchmark" / "minio_upload.py").exists())

    def test_perf_config_uses_deploy_tree_and_shared_reporting(self) -> None:
        text = (PERF / "configs" / "perf-configs.yaml").read_text()
        self.assertIn("deploy/docker/services/", text)
        self.assertIn("libs/vss-perf-reporting/", text)
        self.assertNotIn("compose/docker/services/", text)
        self.assertNotIn("vlmGpus: \"unused\"", text)


if __name__ == "__main__":
    unittest.main()
