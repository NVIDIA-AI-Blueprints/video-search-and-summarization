# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Exercise direct shared-runner hooks, including child failure and timeout."""

import json
import os
import shlex
import shutil
import site
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


class DirectStageTests(unittest.TestCase):
    def run_hook(
        self, *, timeout=False, stage="pre_flight", leak=False, reset_fixture=False
    ):
        repository = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            perf = root / "perf"
            shutil.copytree(
                repository / "perf",
                perf,
                ignore=shutil.ignore_patterns(
                    ".venv",
                    ".bootstrap",
                    ".runtime",
                    ".runtime-state.env",
                    "results",
                    "__pycache__",
                ),
            )
            shared = Path("services/vios/test/bdd_tests/tests/perf/vss_perf_common.py")
            (root / shared).parent.mkdir(parents=True)
            shutil.copyfile(repository / shared, root / shared)
            config_source = Path("deploy/docker/services/vios/configs")
            shutil.copytree(repository / config_source, root / config_source)
            for package in ("core", "cli"):
                source = root / "libs/vss" / package
                (source / "src").mkdir(parents=True)
                (source / "pyproject.toml").write_text(
                    '[project]\nname="nvidia-vss-' + package + '"\n'
                )
            fake_venv = root / "fake-venv"
            binaries = fake_venv / "bin"
            binaries.mkdir(parents=True)
            python = (
                "#!/usr/bin/env bash\nexec " + shlex.quote(sys.executable) + ' "$@"\n'
            )
            docker = "#!/usr/bin/env bash\n" + (
                "sleep 10\n" if timeout else "exit 17\n"
            )
            if stage == "teardown":
                (root / "leftover").write_text("logstash")
                volumes = (
                    {
                        "opaque-db-volume": {
                            "project": "hook-project",
                            "logical": "vios_pg_data",
                        },
                        "opaque-apt-cache": {
                            "project": "hook-project",
                            "logical": "vios_apt_cache",
                        },
                        "unrelated-db": {
                            "project": "other-project",
                            "logical": "vios_pg_data",
                        },
                    }
                    if reset_fixture
                    else {}
                )
                (root / "volumes.json").write_text(json.dumps(volumes))
                docker = "#!/usr/bin/env python3\n" + "\n".join(
                    [
                        "import json,os,pathlib,sys",
                        "args=sys.argv[1:]",
                        "volume_file=pathlib.Path(os.environ['HOOK_VOLUMES'])",
                        "volumes=json.loads(volume_file.read_text())",
                        "state=pathlib.Path(os.environ['HOOK_LEFTOVER'])",
                        "with open(os.environ['HOOK_COMMANDS'],'a') as log: log.write(json.dumps(args)+'\\n')",
                        "if 'down' in args:",
                        f"    if '*' in args and not {leak!r}: state.unlink(missing_ok=True)",
                        "elif 'ps' in args or 'network' in args:",
                        "    if state.exists(): print('project-leftover')",
                        "elif args[:2] == ['volume','ls']:",
                        "    filters=[args[i+1] for i,value in enumerate(args) if value == '--filter']",
                        "    expected={'label=com.docker.compose.project=hook-project','label=com.docker.compose.volume=vios_pg_data'}",
                        "    if set(filters) == expected:",
                        "        for name,item in volumes.items():",
                        "            if item == {'project':'hook-project','logical':'vios_pg_data'}: print(name)",
                        "elif args[:2] == ['volume','rm']:",
                        "    for name in args[2:]: volumes.pop(name)",
                        "    volume_file.write_text(json.dumps(volumes))",
                        "",
                    ]
                )
            for name, source in {
                "python": python,
                "python3": python,
                "vss": "#!/usr/bin/env bash\nexit 0\n",
                "uv": "#!/usr/bin/env bash\nexit 0\n",
                "nvidia-smi": "#!/usr/bin/env bash\necho 'NVIDIA RTX PRO 6000 Blackwell Server Edition, 97887, 595.80'\n",
                "docker": docker,
            }.items():
                binary = binaries / name
                binary.write_text(source)
                binary.chmod(0o755)
            config_file = perf / "configs/config.yaml"
            config = yaml.safe_load(config_file.read_text())
            config["global"]["stage_timeouts"]["pre_flight"] = 0.01 if timeout else 1
            config_file.write_text(yaml.safe_dump(config))
            config_id = yaml.safe_load(
                (perf / "configs/perf-configs.yaml").read_text()
            )["perf_configs"][0]["id"]
            results = root / "results"
            environment = {
                **os.environ,
                "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
                "PYTHONPATH": os.pathsep.join(site.getsitepackages()),
                "VSS_PERF_VENV": str(fake_venv),
                "CONFIG_ID": config_id,
                "PERF_OUTPUT_DIR": str(results),
                "PERF_SCENARIO": "quick_test",
                "UPLOAD_TO_MINIO": "",
                "DOCKER_USE_SUDO": "false",
                "HOOK_LEFTOVER": str(root / "leftover"),
                "HOOK_COMMANDS": str(root / "commands.jsonl"),
                "HOOK_VOLUMES": str(root / "volumes.json"),
                "COMPOSE_PROJECT_NAME": "hook-project",
            }
            for name in (
                "VSS_PERF_WRAPPER",
                "VSS_PERF_BOUNDED_STAGE",
                "PERF_CONFIG",
                "PERF_CONFIGS",
                "VSS_DATA_DIR",
                "ARTIFACT_REF",
                "IMAGE_TAG",
            ):
                environment.pop(name, None)
            process = subprocess.run(
                [str(perf / "scripts" / (stage + ".sh"))],
                env=environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=20,
            )
            if stage == "teardown":
                commands = [
                    json.loads(line)
                    for line in (root / "commands.jsonl").read_text().splitlines()
                ]
                down = next(command for command in commands if "down" in command)
                self.assertIn("*", down)
                self.assertIn("--profile", down)
                self.assertIn("--project-name", down)
                self.assertEqual(
                    process.returncode,
                    1 if leak else 0,
                    process.stdout + "\n" + process.stderr,
                )
                paths = list(results.glob("*.json"))
                if leak:
                    self.assertEqual(len(paths), 1)
                    result = json.loads(paths[0].read_text())
                    self.assertEqual(result["summary"]["overall_status"], "FAIL")
                    self.assertEqual(
                        result["test_cases"][0]["test_case_id"], "teardown"
                    )
                else:
                    self.assertFalse((root / "leftover").exists())
                    self.assertEqual(paths, [])
                    if reset_fixture:
                        remaining = json.loads((root / "volumes.json").read_text())
                        self.assertEqual(
                            set(remaining), {"opaque-apt-cache", "unrelated-db"}
                        )
                        remaining["second-opaque-database"] = {
                            "project": "hook-project",
                            "logical": "vios_pg_data",
                        }
                        (root / "volumes.json").write_text(json.dumps(remaining))
                        repeated = subprocess.run(
                            [str(perf / "scripts/teardown.sh")],
                            env=environment,
                            capture_output=True,
                            text=True,
                            check=False,
                            timeout=20,
                        )
                        self.assertEqual(
                            repeated.returncode,
                            0,
                            repeated.stdout + "\n" + repeated.stderr,
                        )
                        remaining = json.loads((root / "volumes.json").read_text())
                        self.assertEqual(
                            set(remaining), {"opaque-apt-cache", "unrelated-db"}
                        )
                        commands = [
                            json.loads(line)
                            for line in (root / "commands.jsonl")
                            .read_text()
                            .splitlines()
                        ]
                        removed = [
                            command[2:]
                            for command in commands
                            if command[:2] == ["volume", "rm"]
                        ]
                        self.assertEqual(
                            removed, [["opaque-db-volume"], ["second-opaque-database"]]
                        )
                return
            self.assertEqual(
                process.returncode,
                124 if timeout else 17,
                process.stdout + "\n" + process.stderr,
            )
            paths = list(results.glob("*.json"))
            self.assertEqual(len(paths), 1, process.stdout + "\n" + process.stderr)
            result = json.loads(paths[0].read_text())
            self.assertEqual(result["summary"]["overall_status"], "FAIL")
            self.assertEqual(result["summary"]["total_tests"], 1)
            self.assertEqual(result["test_cases"][0]["test_case_id"], "pre_flight")
            self.assertIn("124" if timeout else "17", result["test_cases"][0]["error"])

    def test_two_runs_reset_only_the_project_sensor_database(self):
        self.run_hook(stage="teardown", reset_fixture=True)

    def test_teardown_selects_all_profiles_to_remove_explicit_targets(self):
        self.run_hook(stage="teardown")

    def test_teardown_zero_exit_with_project_leftovers_is_failed(self):
        self.run_hook(stage="teardown", leak=True)

    def test_direct_preflight_failure_produces_one_standard_result(self):
        self.run_hook(timeout=False)

    def test_direct_preflight_timeout_produces_one_standard_result(self):
        self.run_hook(timeout=True)


if __name__ == "__main__":
    unittest.main()
