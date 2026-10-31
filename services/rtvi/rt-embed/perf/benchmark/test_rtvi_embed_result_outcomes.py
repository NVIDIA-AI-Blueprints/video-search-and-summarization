# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import unittest

from rtvi_perf_benchmark import (
    BENCHMARK_TEST_FAILURE_EXIT_CODE,
    _canonical_test_cases_with_outcomes,
    benchmark_suite_exit_code,
)
from vss_perf_common import build_result


class RTVIEmbedBenchmarkResultOutcomeTest(unittest.TestCase):
    def test_completed_test_failure_has_dedicated_exit_code_and_details(self):
        native = {
            "benchmark_mode": "max_live_streams",
            "test_cases": [
                {
                    "test_case_id": "max_live_streams_case_10sec",
                    "success": True,
                    "capacity_success": True,
                    "max_sustainable_streams": 102,
                    "fixed_load_latency_complete": False,
                    "unreached_latency_plateau_counts": [128],
                }
            ],
        }
        adapted = [
            {
                "test_case_id": "max_live_streams_case_10sec",
                "metrics": {"throughput": {"max_sustainable_streams": 102}},
            }
        ]

        cases = _canonical_test_cases_with_outcomes(adapted, native, "max_live_streams")

        self.assertEqual(cases[0]["status"], "FAIL")
        self.assertEqual(cases[0]["error"]["stage"], "kpi_validation")
        self.assertEqual(
            cases[0]["error"]["details"]["unreached_latency_plateau_counts"], [128]
        )
        report = build_result(
            "RTVI-Embed",
            "test-run",
            cases,
            passed=0,
            failed=1,
        )
        self.assertEqual(report["summary"]["overall_status"], "FAIL")
        self.assertEqual(report["test_cases"][0]["status"], "FAIL")
        self.assertEqual(
            benchmark_suite_exit_code(
                [{"total_test_cases": 1, "successful_test_cases": 0, "failed_test_cases": 1}]
            ),
            BENCHMARK_TEST_FAILURE_EXIT_CODE,
        )

    def test_adapter_omitted_failure_is_retained_in_report(self):
        cases = _canonical_test_cases_with_outcomes(
            [],
            {
                "test_cases": [
                    {"test_case_id": "case", "success": False, "error": "request timed out"}
                ]
            },
            "file_burst",
        )

        self.assertEqual(cases[0]["test_case_id"], "case")
        self.assertEqual(cases[0]["status"], "FAIL")
        self.assertEqual(cases[0]["error"]["message"], "request timed out")

    def test_operational_failures_remain_fatal(self):
        for result in (
            {"total_test_cases": 0, "failed_test_cases": 0},
            {"total_test_cases": 1, "failed_test_cases": 1, "cleanup_failed": True},
            {"total_test_cases": 1, "failed_test_cases": 1, "report_generation_failed": True},
        ):
            with self.subTest(result=result):
                self.assertEqual(benchmark_suite_exit_code([result]), 1)

    def test_all_passing_cases_return_success(self):
        self.assertEqual(
            benchmark_suite_exit_code(
                [{"total_test_cases": 1, "successful_test_cases": 1, "failed_test_cases": 0}]
            ),
            0,
        )


if __name__ == "__main__":
    unittest.main()
