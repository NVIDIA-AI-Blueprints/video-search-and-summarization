# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Exercise the runner's actual warmup block without deploying GPU services."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


RUNNER = Path(__file__).resolve().parents[2] / "perf/scripts/run_benchmark.sh"


class WarmupOutputTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.measured = self.root / "measured"
        self.measured.mkdir()
        (self.measured / "existing-report.json").write_text("preserve measured output")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        python = self.bin / "python3"
        python.write_text("""#!/usr/bin/env bash
set -euo pipefail
if [[ "${FAIL_WARMUP:-0}" == 1 ]]; then exit 7; fi
[[ "$*" == *"--scenario quick_test"* ]]
[[ "$*" != *"--output-json"* ]]
while (($#)); do
    if [[ "$1" == --output-dir ]]; then output="$2"; break; fi
    shift
done
[[ -d "$output" && -z "$(ls -A "$output")" ]]
printf 'warmup only\n' > "$output/warmup.log"
""")
        python.chmod(0o755)
        runner = RUNNER.read_text()
        self.block = runner.split("# Thor and Spark: warm up the stack", 1)[1].split(
            'if "${BENCHMARK_CMD[@]}"; then', 1
        )[0]
        # The heading's first line is part of the comment removed by split.
        self.block = "# Thor and Spark: warm up the stack" + self.block

    def run_warmup(self, scenario="single_file_test", compose="spark-test.yaml", ci=True, fail=False):
        env = dict(os.environ)
        for name in ("PERF_OUTPUT_DIR", "PERF_DIAGNOSTICS_DIR"):
            env.pop(name, None)
        env.update(
            PATH=f"{self.bin}:{env['PATH']}",
            VIA_OUTPUT_DIR=str(self.measured),
            COMPOSE_FILE_PATH=compose,
            BENCHMARK_CONFIG_PATH="unchanged-config.yaml",
            TEST_SCENARIO=scenario,
            FAIL_WARMUP="1" if fail else "0",
        )
        if ci:
            env["PERF_OUTPUT_DIR"] = str(self.root / "pipeline-output")
        script = (
            'set -euo pipefail\n'
            'info() { :; }; step() { :; }; error() { :; }\n'
            'SCENARIO_ARGS=("$TEST_SCENARIO")\n' + self.block
        )
        return subprocess.run(["bash", "-c", script], env=env, text=True, capture_output=True)

    def test_sequential_ci_scenarios_preserve_legacy_and_measured_reports(self):
        legacy = Path(str(self.measured) + "-warmup")
        legacy.mkdir()
        (legacy / "old.log").write_text("preserve legacy warmup")
        for scenario in ("single_file_test", "file_burst_test"):
            result = self.run_warmup(scenario)
            self.assertEqual(result.returncode, 0, result.stderr)
        logs = list((self.root / "pipeline-output/diagnostics/warmup").glob("run.*/warmup.log"))
        self.assertEqual(len(logs), 2)
        self.assertEqual((legacy / "old.log").read_text(), "preserve legacy warmup")
        self.assertEqual((self.measured / "existing-report.json").read_text(), "preserve measured output")
        self.assertFalse(list((self.root / "pipeline-output").rglob("*.json")))

    def test_standalone_warmups_use_distinct_directories(self):
        for _ in range(2):
            result = self.run_warmup(ci=False)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(list(self.root.glob("measured-warmup.*/warmup.log"))), 2)

    def test_thor_also_uses_isolated_warmup(self):
        result = self.run_warmup(compose="thor-test.yaml")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(list(self.root.rglob("warmup.log"))), 1)

    def test_quick_test_and_other_platforms_do_not_add_warmup(self):
        for scenario, compose in (("quick_test", "spark-test.yaml"), ("single_file_test", "x86-test.yaml")):
            result = self.run_warmup(scenario, compose)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(list(self.root.rglob("warmup.log")))

    def test_failed_warmup_remains_an_execution_failure(self):
        self.assertEqual(self.run_warmup(fail=True).returncode, 1)


if __name__ == "__main__":
    unittest.main()
