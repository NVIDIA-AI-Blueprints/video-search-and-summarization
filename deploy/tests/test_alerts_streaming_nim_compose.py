# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Resolve the real alerts Compose graph without starting GPU containers."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / "deploy/docker/developer-profiles/dev-profile-alerts"


@unittest.skipUnless(shutil.which("docker"), "Docker Compose CLI is required")
class AlertsStreamingNimComposeTests(unittest.TestCase):
    def resolve(self, remote):
        settings = (PROFILE / "overrides.env").read_text()
        replacements = {
            "VSS_APPS_DIR": str(ROOT / "deploy/docker"),
            "VSS_DATA_DIR": "/tmp/vss-streaming-nim-config-test",
            "HOST_IP": "127.0.0.1",
            "MODE": "2d_vlm",
            "ALERT_AGENT_ALWAYS_ON": "true",
            "COMPOSE_PROFILES": "${COMPOSE_PROFILES_VLM}",
        }
        settings = "\n".join(
            f"{line.split('=', 1)[0]}={replacements[line.split('=', 1)[0]]}"
            if line.split("=", 1)[0] in replacements
            else line
            for line in settings.splitlines()
        )
        settings += (
            "\nSTREAMING_NIM_IMAGE=example/streaming-nim:config-test\nSTREAMING_NIM_DEVICE_ID=1\n"
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix="-overrides.env") as env_file:
            env_file.write(settings)
            env_file.flush()
            command = [
                "docker",
                "compose",
                "--env-file",
                str(ROOT / "deploy/docker/containers.env"),
                "--env-file",
                str(PROFILE / ".env"),
                "--env-file",
                env_file.name,
                "-f",
                str(ROOT / "deploy/docker/compose.yml"),
            ]
            if remote:
                command += ["-f", str(PROFILE / "compose.streaming-nim.yml")]
            command += ["config", "--no-env-resolution", "--format", "json"]
            # Do not let exported deployment values override the test fixture.
            environment = {key: os.environ[key] for key in ("PATH", "HOME") if key in os.environ}
            result = subprocess.run(
                command, env=environment, capture_output=True, text=True, check=False
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)["services"]

    def test_default_real_time_profile_keeps_chunking_without_nim(self):
        services = self.resolve(remote=False)
        env = services["rtvi-vlm"]["environment"]
        self.assertEqual(env["RTVI_STREAMING_VLM_ENABLED"], "false")
        self.assertEqual(env["VLM_MODEL_TO_USE"], "cosmos-reason3")
        self.assertNotIn("VIA_VLM_STREAMING_NIM_ENABLED", env)
        self.assertNotIn("streaming-nim", services)

    def test_overlay_forwards_to_healthy_nim_and_preserves_alert_bus(self):
        services = self.resolve(remote=True)
        rtvi = services["rtvi-vlm"]
        env = rtvi["environment"]
        self.assertEqual(env["RTVI_STREAMING_VLM_ENABLED"], "false")
        self.assertEqual(env["VIA_VLM_STREAMING_NIM_ENABLED"], "true")
        self.assertEqual(env["VLM_MODEL_TO_USE"], "openai-compat")
        self.assertEqual(env["VIA_VLM_ENDPOINT"], "http://streaming-nim:8000/v1")
        self.assertEqual(env["MESSAGE_BUS"], "kafka")
        self.assertEqual(env["RTVI_LIVE_STREAM_EMIT_EOS_ENDPOINT"], "true")
        self.assertEqual(rtvi["depends_on"]["streaming-nim"]["condition"], "service_healthy")
        nim = services["streaming-nim"]
        self.assertEqual(nim["environment"]["NIM_ENABLE_STREAMING"], "1")
        self.assertFalse(nim.get("ports"))
        self.assertEqual(services["streaming-nim-cache-init"]["environment"]["MODE"], "2d_vlm")

    def test_skill_build_merges_overlay_and_selects_build_local_rules(self):
        """The skill's ordered include must survive resolution as a standalone model."""
        source_rules = (PROFILE / "vlm-as-verifier/realtime-config.yml").read_bytes()
        with tempfile.TemporaryDirectory() as temporary:
            build = Path(temporary)
            patches = build / "patches"
            patches.mkdir()
            rules = patches / "realtime-config.yml"
            # JSON is valid YAML; this is a generated always-on rule payload.
            rules.write_text(
                json.dumps(
                    {
                        "always_on_rules": [
                            {
                                "rule_id": "ppe",
                                "alert_type": "missing_ppe",
                                "always_on_params": {
                                    "model": "${VLM_NAME}",
                                    "system_prompt": "Watch for missing PPE. Answer yes or no.",
                                    "prompt": "Is PPE missing?",
                                    "inference_mode": "streaming_vlm",
                                    "streaming_frame_policy": "ordered",
                                    "streaming_window_frames": 8,
                                    "chunk_duration": 1,
                                    "chunk_overlap_duration": 0,
                                    "num_frames_per_second_or_fixed_frames_chunk": 1,
                                },
                            }
                        ]
                    }
                )
            )
            (build / "override.env").write_text(
                f"FOUNDATION=alerts\nBUILD_DIR={build}\n"
                f"VSS_APPS_DIR={ROOT / 'deploy/docker'}\n"
                "VSS_DATA_DIR=/tmp/vss-streaming-nim-config-test\nHOST_IP=127.0.0.1\n"
                "MODE=2d_vlm\nALERT_AGENT_ALWAYS_ON=true\n"
                "COMPOSE_PROFILES=${COMPOSE_PROFILES_VLM}\n"
                "RTVI_STREAMING_VLM_ENABLED=false\nRTVI_VLM_MODEL_TO_USE=openai-compat\n"
                "VLM_NAME=nvidia/cosmos3-nano-reasoner\n"
                "STREAMING_NIM_IMAGE=example/streaming-nim:config-test\nSTREAMING_NIM_DEVICE_ID=1\n"
                "NEXT_PUBLIC_ALERTS_TAB_MANAGE_ALERTS_SUB_TAB_ENABLE_REALTIME_ALERTS=true\n"
                "NEXT_PUBLIC_ALERTS_TAB_MANAGE_ALERTS_SUB_TAB_ENABLE_CV_ALERTS_VERIFICATION=false\n"
                "VLM_AS_VERIFIER_CONFIG_FILE_REALTIME=${BUILD_DIR}/patches/realtime-config.yml\n"
            )
            compose = build / "compose.yml"
            compose.write_text(
                json.dumps(
                    {
                        "include": [
                            {
                                "path": [
                                    str(ROOT / "deploy/docker/compose.yml"),
                                    str(PROFILE / "compose.streaming-nim.yml"),
                                ]
                            }
                        ]
                    }
                )
            )
            command = ["docker", "compose"]
            for layer in (
                ROOT / "deploy/docker/containers.env",
                PROFILE / ".env",
                PROFILE / "overrides.env",
                build / "override.env",
            ):
                command += ["--env-file", str(layer)]
            command += ["-f", str(compose), "config", "--no-env-resolution", "--format", "json"]
            environment = {key: os.environ[key] for key in ("PATH", "HOME") if key in os.environ}
            result = subprocess.run(
                command, env=environment, capture_output=True, text=True, check=False
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            services = json.loads(result.stdout)["services"]
            rtvi = services["rtvi-vlm"]
            bridge = services["alert-bridge"]
            self.assertEqual(rtvi["environment"]["VIA_VLM_STREAMING_NIM_ENABLED"], "true")
            self.assertEqual(bridge["environment"]["RTVI_VLM_MODEL_TO_USE"], "openai-compat")
            self.assertEqual(bridge["environment"]["VLM_NAME"], "nvidia/cosmos3-nano-reasoner")
            rule_mount = next(
                v for v in bridge["volumes"] if v["target"] == "/app/configs/realtime-config.yml"
            )
            self.assertEqual(Path(rule_mount["source"]), rules)
            self.assertIn("streaming-nim", services)
            self.assertIn("streaming-nim-cache-init", services)
            self.assertEqual(
                (PROFILE / "vlm-as-verifier/realtime-config.yml").read_bytes(), source_rules
            )


if __name__ == "__main__":
    unittest.main()
