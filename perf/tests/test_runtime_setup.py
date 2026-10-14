# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Check staged runtime semantics against the actual production config source."""

import configparser
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


class RuntimeSetupTests(unittest.TestCase):
    def test_repeated_staging_preserves_service_owned_vios_config(self):
        repository = Path(__file__).resolve().parents[2]
        script = repository / "perf/scripts/prepare_runtime.py"
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary)
            environment = {
                **os.environ,
                "VSS_REPO_ROOT": str(repository),
                "VSS_DATA_DIR": str(data),
            }
            subprocess.run([sys.executable, str(script)], env=environment, check=True)
            config = data / "vios-configs/vst_config.json"
            runtime = config.read_text() + "\n"
            config.write_text(runtime)
            private_logs = data / "data_log/vst/vst_data/logs"
            private_logs.mkdir()
            private_logs.chmod(0o700)
            subprocess.run([sys.executable, str(script)], env=environment, check=True)
            self.assertEqual(config.read_text(), runtime)
            self.assertEqual(private_logs.stat().st_mode & 0o777, 0o700)

    def test_strongly_typed_model_and_assigned_sensor_are_staged_without_source_changes(
        self,
    ):
        repository = Path(__file__).resolve().parents[2]
        original = (
            repository
            / "services/rtvi/rt-cv/reference-configs/warehouse-2d/ds-ppl-analytics-pgie-config.yml"
        )
        before = original.read_bytes()
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary)
            environment = {
                **os.environ,
                "VSS_REPO_ROOT": str(repository),
                "VSS_DATA_DIR": str(data),
                "VSS_PERF_STREAM_URL": "rtsp://fixture:8554/live",
            }
            script = repository / "perf/scripts/prepare_runtime.py"
            subprocess.run([sys.executable, str(script)], env=environment, check=True)
            pgie = yaml.safe_load(
                (data / "rt-cv-configs/ds-ppl-analytics-pgie-config.yml").read_text()
            )["property"]
            self.assertEqual(pgie["strongly-typed"], 1)
            self.assertEqual(pgie["network-mode"], 0)
            self.assertTrue(pgie["onnx-file"].endswith(".fp16.onnx"))
            sensor = data / "sensor.json"
            sensor.write_text(
                json.dumps(
                    {"sensor_id": "vios-issued-uuid", "name": "compose-base-perf"}
                )
            )
            subprocess.run(
                [sys.executable, str(script), "--sensor-json", str(sensor)],
                env=environment,
                check=True,
            )
            config = configparser.ConfigParser()
            config.read(data / "rt-cv-configs/ds-main-config.txt")
            self.assertEqual(
                config["source-list"]["sensor-id-list"], "vios-issued-uuid"
            )
            self.assertEqual(config["application"]["enable-perf-measurement"], "1")
            self.assertEqual(config["sink1"]["msg-conv-payload-type"], "2")
        self.assertEqual(original.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
