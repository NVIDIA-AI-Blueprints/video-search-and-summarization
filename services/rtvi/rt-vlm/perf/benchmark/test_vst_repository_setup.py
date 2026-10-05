# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Repository staging must work offline and preserve deployment isolation."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


PERF = Path(__file__).resolve().parents[1]
STAGER = PERF / "stage_vst.py"


class RepositoryVstSetupTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "linux", "setup requires Linux Bash and GNU sed")
    def test_setup_prerequisites_and_staging_without_artifactory(self):
        setup = (PERF / "setup_perf_env.sh").read_text()
        # Run actual prerequisite + staging/patching code, stopping before media,
        # system changes, Docker, or GPU execution. No credential file is read.
        code = setup.split("\nrequire_cmd curl\n", 1)[0]
        code += '\nPLATFORM=unknown\n'  # x86; skip Step 3's privileged cache-cleaner startup
        code += setup[setup.index('log "Step 4/12:'):setup.index('log "Step 6/12:')]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            perf = directory / "services/rtvi/rt-vlm/perf"
            perf.mkdir(parents=True)
            # Keep the real repository deployment source available at its relative path.
            (directory / "services/vios").symlink_to(PERF.parents[2] / "vios", target_is_directory=True)
            for name in ("stage_vst.py", "deploy_vst.sh"):
                (perf / name).write_text((PERF / name).read_text())
            script = perf / "check.sh"
            script.write_text(code)
            config = directory / "benchmark.yaml"
            config.write_text("global: {}\n")
            env = {
                "PATH": os.environ["PATH"], "HOME": str(directory),
                "BENCHMARK_CONFIG": str(config), "NVIDIA_VISIBLE_DEVICES": "0",
                "NGC_API_KEY": "unit-test-not-a-credential",
                "VST_COMPOSE_PROJECT": "isolated-test",
                "VST_DIR": str(directory / "staged"),
                "VST_SENSOR_PORT": "32000", "VST_STREAM_PROC_PORT": "32001",
                "CENTRALIZE_DB_PORT": "35432",
                "DOCKER_HOST": "unix:///nonexistent/rtvi-test.sock",
            }
            result = subprocess.run(["bash", str(script)], env=env, capture_output=True,
                                    text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("ARTIFACTORY", result.stdout + result.stderr)
            staged = directory / "staged"
            self.assertTrue((staged / "deploy.sh").is_file())
            compose = (staged / "stream-processing/docker-compose.yaml").read_text()
            self.assertEqual(compose.count("pg_isready -h 127.0.0.1 -p"), 1)
            self.assertNotIn("-p ${CENTRALIZE_DB_PORT:-5432} -p", compose)
            ingress = (staged / "stream-processing/configs/nginx-vst.conf").read_text()
            self.assertIn("http://127.0.0.1:32000", ingress)
            self.assertIn("http://127.0.0.1:32001", ingress)

    def test_fresh_stage_without_credentials_wires_ports_paths_and_projects(self):
        with tempfile.TemporaryDirectory() as temporary:
            temporary = str(Path(temporary).resolve())
            root = Path(temporary) / "vst"
            env = {
                "PATH": os.environ["PATH"],
                "VST_DIR": str(root),
                "PERF_VIDEOS_DIR": str(Path(temporary) / "videos"),
                "VST_COMPOSE_PROJECT": "offline-test",
                "VST_SENSOR_PORT": "32000",
                "VST_STREAM_PROC_PORT": "32001",
                "VST_INGRESS_PORT": "32888",
                "VST_RTSP_PORT": "32554",
                "NVSTREAMER_HTTP_PORT": "33000",
                "NVSTREAMER_RTSP_PORT": "33554",
            }
            result = subprocess.run(
                [sys.executable, str(STAGER)], env=env,
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((root / "deploy.sh").is_file())
            self.assertFalse((root / "vst_package.tar.gz").exists())
            stream_env = (root / "stream-processing/compose.env").read_text()
            for setting in (
                "VST_USE_SDRC=false", "COMPOSE_PROFILES=", "NGINX_MODE=vst",
                "SENSOR_HTTP_PORT=32000", "STREAM_PROCESSOR_HTTP_PORT_1=32001",
                "RTSP_SERVER_PORT_1=32554",
                f"VST_CONFIG_PATH={root}/stream-processing/configs",
            ):
                self.assertIn(setting + "\n", stream_env)
            ingress = (root / "stream-processing/configs/nginx-vst.conf").read_text()
            self.assertIn("http://127.0.0.1:32000", ingress)
            self.assertIn("http://127.0.0.1:32001", ingress)
            sources = json.loads((root / "stream-processing/configs/rtsp_streams.json").read_text())
            self.assertEqual(
                [item["endpoint"] for item in sources["Nvstreamer"] if item["enabled"]],
                ["localhost:33000"],
            )
            nv_env = (root / "nvstreamer/compose.env").read_text()
            self.assertIn("NVSTREAMER_RTSP_PORT_1=33554\n", nv_env)
            self.assertIn(f"NVSTREAMER_VIDEO_1={temporary}/videos\n", nv_env)
            self.assertTrue((root / "scripts/user_additional_install.sh").is_file())
            for folder in ("stream-processing", "nvstreamer"):
                compose = (root / folder / "docker-compose.yaml").read_text()
                self.assertIn("name: offline-test-apt-cache", compose)
                self.assertNotIn("container_name: sensor-ms\n", compose)

            # Verify the adapter's external command boundary without contacting Docker.
            # A bare teardown must still address this staged project's containers.
            binary = Path(temporary) / "bin"
            binary.mkdir()
            docker = binary / "docker"
            docker.write_text(
                f"#!{sys.executable}\nimport json, os, sys\n"
                "with open(os.environ['DOCKER_CALL_LOG'], 'w') as out:\n"
                "    json.dump(sys.argv[1:], out)\n"
            )
            docker.chmod(0o700)
            calls = Path(temporary) / "docker-call.json"
            command_env = {"PATH": f"{binary}:{os.environ['PATH']}", "DOCKER_CALL_LOG": str(calls)}
            for action in ("up", "down", "config"):
                for target in ("vst", "nvstreamer"):
                    deployed = subprocess.run(
                        ["bash", str(root / "deploy.sh"), action, target],
                        env=command_env, capture_output=True, text=True, timeout=5,
                    )
                    self.assertEqual(deployed.returncode, 0, deployed.stderr)
                    args = json.loads(calls.read_text())
                    self.assertEqual(args[args.index("--project-name") + 1], f"offline-test-{target}")

            calls.unlink()
            mismatch = subprocess.run(
                ["bash", str(root / "deploy.sh"), "down", "vst"],
                env={**command_env, "COMPOSE_PROJECT_NAME": "another-run"},
                capture_output=True, text=True, timeout=5,
            )
            self.assertNotEqual(mismatch.returncode, 0)
            self.assertFalse(calls.exists())

            # A second staging call must not overwrite a prior deployment's state.
            sentinel = root / "stream-processing/compose.env"
            sentinel.write_text("preserve prior deployment\n")
            again = subprocess.run([sys.executable, str(STAGER)], env=env,
                                   capture_output=True, text=True, timeout=10)
            self.assertNotEqual(again.returncode, 0)
            self.assertEqual(sentinel.read_text(), "preserve prior deployment\n")


if __name__ == "__main__":
    unittest.main()
