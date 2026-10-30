# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path
import unittest

import yaml


PERF_ROOT = Path(__file__).resolve().parents[1]


class RTVIEmbedPerfContractTest(unittest.TestCase):
    def test_required_service_contract_files_exist(self):
        for relative_path in (
            "configs/config.yaml",
            "configs/perf-configs.yaml",
            "scripts/run_benchmark.sh",
            "scripts/native_setup_perf_env.sh",
            "scripts/native_teardown_perf_env.sh",
            "setup/pyproject.toml",
            "setup/uv.lock",
            "setup/setup_environment.sh",
            "setup/compose/compose.perf.yaml",
        ):
            self.assertTrue((PERF_ROOT / relative_path).is_file(), relative_path)

    def test_poc_matrix_is_single_gpu(self):
        config = yaml.safe_load((PERF_ROOT / "configs/perf-configs.yaml").read_text())
        enabled = [row for row in config["perf_configs"] if row.get("enabled")]
        self.assertEqual(len(enabled), 1)
        self.assertEqual(enabled[0]["vlmGpus"], "0")

    def test_service_does_not_upload_reports(self):
        runner = (PERF_ROOT / "scripts/run_benchmark.sh").read_text()
        self.assertNotIn("--upload", runner)
        self.assertIn("--output-json", runner)

    def test_runner_uses_managed_vst_directory_outside_checkout(self):
        runner = (PERF_ROOT / "scripts/run_benchmark.sh").read_text()
        self.assertIn('if [[ -z "${VST_DIR:-}" ]]; then', runner)
        self.assertIn('runtime_base="/tmp/vss-perf-$(id -u)/rtvi-embed"', runner)
        self.assertIn('VST_DIR="$(mktemp -d "${runtime_base}/run.XXXXXXXX")"', runner)
        self.assertIn('export RTVI_MANAGED_VST_DIR="$VST_DIR"', runner)
        self.assertNotIn("$output/native/vst_package", runner)

    def test_teardown_only_removes_marked_managed_vst_directory(self):
        teardown = (PERF_ROOT / "scripts/native_teardown_perf_env.sh").read_text()
        self.assertIn('"${RTVI_MANAGED_VST_DIR}" != "${VST_DIR}"', teardown)
        self.assertIn('/tmp/vss-perf-$(id -u)/rtvi-embed/run.', teardown)
        self.assertIn('docker run --rm --user 0', teardown)
        self.assertIn('chown -R ${owner} /cleanup', teardown)

    def test_setup_validates_host_prerequisites(self):
        setup = (PERF_ROOT / "scripts/native_setup_perf_env.sh").read_text()
        self.assertIn("require_cmd docker", setup)
        self.assertIn("require_cmd ss", setup)
        self.assertIn("docker compose version", setup)


if __name__ == "__main__":
    unittest.main()
