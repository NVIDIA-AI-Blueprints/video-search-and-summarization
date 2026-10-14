# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Exercise source-aware bootstrap and VIOS config cache invalidation."""

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


class SourceBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        repository = Path(__file__).resolve().parents[2]
        self.perf = self.root / "perf"
        shutil.copytree(repository / "perf/configs", self.perf / "configs")
        (self.perf / "scripts").mkdir()
        for name in ("common.sh", "prepare_runtime.py"):
            shutil.copyfile(
                repository / "perf/scripts" / name, self.perf / "scripts" / name
            )
        (self.perf / "benchmark").mkdir()
        shutil.copyfile(
            repository / "perf/benchmark/requirements.txt",
            self.perf / "benchmark/requirements.txt",
        )
        self.source_config = (
            self.root / "deploy/docker/services/vios/configs/vst_config.json"
        )
        self.source_config.parent.mkdir(parents=True)
        self.source_config.write_text('{"expiry":10080}\n')
        reference = Path("services/rtvi/rt-cv/reference-configs/warehouse-2d")
        shutil.copytree(repository / reference, self.root / reference)
        for package in ("core", "cli"):
            source = self.root / "libs/vss" / package
            (source / "src").mkdir(parents=True)
            (source / "pyproject.toml").write_text(
                '[project]\nname="nvidia-vss-' + package + '"\n'
            )
            (source / "src/module.py").write_text('VERSION="original"\n')
        self.venv = self.root / "venv"
        binaries = self.venv / "bin"
        binaries.mkdir(parents=True)
        for name in ("python", "python3"):
            target = binaries / name
            target.write_text(
                "#!/usr/bin/env bash\nexec " + shlex.quote(sys.executable) + ' "$@"\n'
            )
            target.chmod(0o755)
        (binaries / "vss").write_text("#!/usr/bin/env bash\nexit 0\n")
        (binaries / "vss").chmod(0o755)
        uv = binaries / "uv"
        uv.write_text(
            "#!/usr/bin/env python3\nimport json,os,sys\n"
            "with open(os.environ['BOOTSTRAP_COMMANDS'],'a') as stream: stream.write(json.dumps(sys.argv[1:])+'\\n')\n"
        )
        uv.chmod(0o755)
        matrix = yaml.safe_load((self.perf / "configs/perf-configs.yaml").read_text())
        self.environment = {
            **os.environ,
            "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
            "PYTHONPATH": os.pathsep.join(site.getsitepackages()),
            "VSS_PERF_VENV": str(self.venv),
            "CONFIG_ID": matrix["perf_configs"][0]["id"],
            "BOOTSTRAP_COMMANDS": str(self.root / "commands.jsonl"),
        }
        for name in (
            "VSS_PERF_STAGE",
            "VSS_PERF_WRAPPER",
            "VSS_PERF_BOUNDED_STAGE",
            "VSS_DATA_DIR",
            "PERF_CONFIG",
            "PERF_CONFIGS",
            "ARTIFACT_REF",
            "IMAGE_TAG",
        ):
            self.environment.pop(name, None)

    def common(self, *, prepare=False):
        command = 'source "$1"; '
        if prepare:
            command += '"$PYTHON" "$VSS_PERF_ROOT/scripts/prepare_runtime.py"; '
        command += 'printf "%s\\n" "$VST_CONFIG_PATH"'
        process = subprocess.run(
            ["bash", "-c", command, "bootstrap", str(self.perf / "scripts/common.sh")],
            env=self.environment,
            capture_output=True,
            text=True,
            check=True,
        )
        return Path(process.stdout.strip().splitlines()[-1])

    def installations(self):
        return [
            json.loads(line)
            for line in (self.root / "commands.jsonl").read_text().splitlines()
        ]

    def test_installed_cli_is_refreshed_for_python_source_and_dependency_changes(self):
        self.common()
        self.assertEqual(len(self.installations()), 1)
        self.common()
        self.assertEqual(len(self.installations()), 1)
        (self.root / "libs/vss/cli/src/module.py").write_text('VERSION="updated"\n')
        self.common()
        self.assertEqual(len(self.installations()), 2)
        (self.root / "libs/vss/core/pyproject.toml").write_text(
            '[project]\nname="nvidia-vss-core"\ndependencies=["new-dependency"]\n'
        )
        self.common()
        self.assertEqual(len(self.installations()), 3)
        requirements = self.perf / "benchmark/requirements.txt"
        requirements.write_text(requirements.read_text() + "\n# changed requirements\n")
        self.common()
        self.assertEqual(len(self.installations()), 4)
        for command in self.installations():
            self.assertIn("--reinstall-package", command)
            self.assertIn("nvidia-vss-cli", command)
            self.assertIn("nvidia-vss-core", command)

    def test_source_config_changes_create_fresh_copy_and_preserve_runtime_mutations(
        self,
    ):
        original = self.common(prepare=True)
        (original / "vst_config.json").write_text('{"runtime_mutation":true}\n')
        self.assertEqual(self.common(prepare=True), original)
        self.assertEqual(
            json.loads((original / "vst_config.json").read_text()),
            {"runtime_mutation": True},
        )
        cache = original.parent / "models/cached-engine.plan"
        cache.write_text("preserved cache")
        self.source_config.write_text('{"expiry":60}\n')
        updated = self.common(prepare=True)
        self.assertNotEqual(updated, original)
        self.assertEqual(
            json.loads((updated / "vst_config.json").read_text()), {"expiry": 60}
        )
        self.assertEqual(cache.read_text(), "preserved cache")
        self.assertEqual(len(self.installations()), 1)


if __name__ == "__main__":
    unittest.main()
