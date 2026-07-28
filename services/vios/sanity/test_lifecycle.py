# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import provision
from run_sanity import _cleanup_transient_artifacts, _run_plans, _start_metadata_service

_OVERLAY = Path(__file__).resolve().parents[1] / "test/bdd_tests/scripts/overlay"
sys.path.insert(0, str(_OVERLAY))
from fake_es_server import FakeESStore


class LifecycleTest(unittest.TestCase):
    def test_consumer_overrides_use_notification_config(self):
        with tempfile.TemporaryDirectory() as tempdir:
            notification_config = Path(tempdir) / "notification_config.json"
            notification_config.write_text(json.dumps({
                "message_broker": {
                    "enable_notification_consumer": False,
                    "use_message_broker_consumer": "redis",
                    "message_broker_topic_consumer": "",
                    "kafka_server_address": "",
                }
            }))
            original = provision._VST_NOTIFICATION_CONFIG
            try:
                provision._VST_NOTIFICATION_CONFIG = notification_config
                changed = provision.apply_vst_notification_config({
                    "enable_notification_consumer": True,
                    "use_message_broker_consumer": "kafka",
                    "message_broker_topic_consumer": "vst-overlay-test",
                    "kafka_server_address": "172.17.0.1:9092",
                }, recreate=False)
            finally:
                provision._VST_NOTIFICATION_CONFIG = original

            config = json.loads(notification_config.read_text())["message_broker"]
            self.assertTrue(changed)
            self.assertTrue(config["enable_notification_consumer"])
            self.assertEqual(config["use_message_broker_consumer"], "kafka")
            self.assertEqual(config["message_broker_topic_consumer"], "vst-overlay-test")
            self.assertEqual(config["kafka_server_address"], "172.17.0.1:9092")

    def test_overlay_webhooks_enable_only_streaming_and_remove(self):
        with tempfile.TemporaryDirectory() as tempdir:
            notification_config = Path(tempdir) / "notification_config.json"
            notification_config.write_text(json.dumps({
                "message_broker": {},
                "webhooks": {"enabled": False, "items": [
                    {"enabled": False, "id": "add", "camera_status_change": "camera_add",
                     "request": [{"url": "http://old/add", "method": "POST"}]},
                    {"enabled": False, "id": "stream", "camera_status_change": "camera_streaming",
                     "request": [{"url": "http://old/stream", "method": "POST"}],
                     "auth": {"type": "hmac"}},
                    {"enabled": False, "id": "remove", "camera_status_change": "camera_remove",
                     "request": [{"url": "http://old/remove", "method": "POST"}]},
                ]},
            }))
            original = provision._VST_NOTIFICATION_CONFIG
            try:
                provision._VST_NOTIFICATION_CONFIG = notification_config
                provision.configure_overlay_webhooks(True)
            finally:
                provision._VST_NOTIFICATION_CONFIG = original

            config = json.loads(notification_config.read_text())
            items = {item["camera_status_change"]: item for item in config["webhooks"]["items"]}
            self.assertTrue(config["webhooks"]["enabled"])
            self.assertFalse(items["camera_add"]["enabled"])
            self.assertTrue(items["camera_streaming"]["enabled"])
            self.assertTrue(items["camera_remove"]["enabled"])
            self.assertEqual(items["camera_streaming"]["request"][0]["method"], "PUT")
            self.assertEqual(items["camera_remove"]["request"][0]["method"], "DELETE")
            self.assertNotIn("auth", items["camera_streaming"])

    def test_deployment_mode_toggle_is_reversible(self):
        with tempfile.TemporaryDirectory() as tempdir:
            compose_env = Path(tempdir) / "compose.env"
            compose_env.write_text("""HOST_IP=10.0.0.1
# ---------- DIRECT MODE (uncomment below for direct mode) ----------
#VST_USE_SDRC=false
#NGINX_MODE=vst
#STREAM_PROCESSOR_MODULE_ENDPOINT=http://${HOST_IP}:30001

# ---------- SDRC MODE (comment below to use direct mode) ----------
VST_USE_SDRC=true
COMPOSE_PROFILES=sdrc
NGINX_MODE=vst-sdrc
STREAM_PROCESSOR_MODULE_ENDPOINT=http://${HOST_IP}:10000

################ next section
VALUE=kept
""")
            original = provision._COMPOSE_ENV
            try:
                provision._COMPOSE_ENV = compose_env
                provision.apply_deployment_mode("direct")
                direct = compose_env.read_text()
                provision.apply_deployment_mode("sdrc")
                sdrc = compose_env.read_text()
            finally:
                provision._COMPOSE_ENV = original

            self.assertIn("\nVST_USE_SDRC=false\n", direct)
            self.assertIn("\nNGINX_MODE=vst\n", direct)
            self.assertNotIn("\nCOMPOSE_PROFILES=sdrc\n", direct)
            self.assertIn("\nVST_USE_SDRC=true\n", sdrc)
            self.assertIn("\nCOMPOSE_PROFILES=sdrc\n", sdrc)
            self.assertIn("VALUE=kept", sdrc)

    def test_plan3_is_one_rtsp_one_file_without_sync(self):
        from plans import load_plans

        _defaults, loaded = load_plans(str(Path(__file__).with_name("sanity_plans.yaml")))
        self.assertEqual([plan["name"].split(" | ")[0] for plan in loaded],
                         ["Plan-1", "Plan-2", "Plan-3", "Plan-4", "Plan-5"])
        plan3 = loaded[2]
        setup = plan3["setup"]
        self.assertEqual(setup["deployment_mode"], "direct")
        self.assertEqual(setup["event_transport"], "webhook")
        self.assertEqual(setup["consumer"], "kafka")
        self.assertEqual(setup["rtsp_copies"], 1)
        self.assertFalse(setup.get("sync_wall", False))
        self.assertEqual(len(plan3["usecases"]), 6)
        self.assertNotIn("webrtc", {case["test"] for case in plan3["usecases"]})

    def test_cleanup_removes_only_reproducible_transients(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            provision = root / "provision"
            provision.mkdir()
            (provision / "generated.mp4").write_bytes(b"generated")
            (root / "case_control.mp4").write_bytes(b"control")
            (root / "evidence.mp4").write_bytes(b"evidence")
            (root / "report.pdf").write_bytes(b"report")
            (root / "failed_cases.json").write_text("{}")

            stats = _cleanup_transient_artifacts(root)

            self.assertEqual(stats["files"], 2)
            self.assertFalse(provision.exists())
            self.assertFalse((root / "case_control.mp4").exists())
            self.assertTrue((root / "evidence.mp4").exists())
            self.assertTrue((root / "report.pdf").exists())
            self.assertTrue((root / "failed_cases.json").exists())

    def test_video_cleanup_preserves_unrelated_uploads(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            owned = root / "Warehouse_test_h264_1080p.mp4"
            unrelated = root / "my_ui_upload.mp4"
            owned.write_bytes(b"owned")
            unrelated.write_bytes(b"user")
            old_root = provision._NVS_VIDEOS
            try:
                provision._NVS_VIDEOS = root
                removed = provision.wipe_nvstreamer_videos("/inputs/Warehouse_test.mp4")
            finally:
                provision._NVS_VIDEOS = old_root
            self.assertEqual(removed, 1)
            self.assertFalse(owned.exists())
            self.assertTrue(unrelated.exists())

    def test_keep_deployment_rejects_multiple_enabled_plans(self):
        plans = Path(__file__).with_name("sanity_plans.yaml")
        with self.assertRaisesRegex(ValueError, "exactly one enabled plan/system"):
            _run_plans(str(plans), keep_deployment=True)

    def test_fake_es_retention_is_time_based_per_sensor(self):
        hour_ms = 60 * 60 * 1000
        store = FakeESStore(retention_hours=3)
        store.append([
            {"id": "a-old", "sensorId": "a", "_epoch_ms": 0},
            {"id": "b-only", "sensorId": "b", "_epoch_ms": 0},
            {"id": "a-new", "sensorId": "a", "_epoch_ms": 4 * hour_ms},
        ])

        hits = store.search(None, None, None, None, None)["hits"]["hits"]
        retained = {(hit["_source"]["sensorId"], hit["_id"]) for hit in hits}
        self.assertEqual(retained, {("a", "a-new"), ("b", "b-only")})

    def test_metadata_service_receives_requested_retention(self):
        ctx = SimpleNamespace(
            broker="redis",
            base_url="http://localhost:30888",
            nvstreamer_url="http://localhost:31000",
        )
        with mock.patch.dict("os.environ", {"VIOS_SANITY_ES_RETENTION_HOURS": "10"}):
            with mock.patch("subprocess.Popen") as popen:
                popen.return_value.poll.return_value = None
                _start_metadata_service(ctx, wait_s=0)

        command = popen.call_args.args[0]
        flag_index = command.index("--es-retention-hours")
        self.assertEqual(command[flag_index + 1], "10")


if __name__ == "__main__":
    unittest.main()
