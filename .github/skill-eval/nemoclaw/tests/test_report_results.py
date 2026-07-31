# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "report_results.py"
SPEC = importlib.util.spec_from_file_location("nemoclaw_report_results", MODULE_PATH)
assert SPEC and SPEC.loader
report_results = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(report_results)


def row(slug: str, status_name: str) -> dict[str, str]:
    return {
        "slug": slug,
        "name": f"skill/{status_name}/RTXPRO6000BW",
        "skill": "skill",
        "spec_stem": status_name,
        "spec_path": f"skills/skill/evals/{status_name}.json",
        "platform": "RTXPRO6000BW",
        "task_limit": "0",
        "kind": "eval",
    }


def benchmark(status: str, detail: str = "public detail") -> str:
    return (
        "# Skills Eval Benchmark - NemoClaw\n\n"
        "| Platform | Result | Detail |\n"
        "|---|---|---|\n"
        f"| RTXPRO6000BW | {status} | {detail} |\n\n"
        f"{report_results.EVAL_ROW_COMPLETION_MARKER}\n"
    )


class BlockedBenchmarkTest(unittest.TestCase):
    def test_blocked_benchmark_is_deterministic_markdown_safe_and_redacted(self):
        planned = row("skill__blocked__rtx", "blocked")
        planned["skill"] = "skill|<unsafe>"
        reason = (
            "missing [tool](https://unsafe.invalid) | <b>private</b> "
            "ANTHROPIC_API_KEY=do-not-publish\n"
            "Authorization: Bearer also-private"
        )
        planned["spec_path"] += "\N{RIGHT-TO-LEFT OVERRIDE}hidden"

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "benchmark.md"
            report_results.write_blocked_benchmark(planned, reason, output)
            first = output.read_text(encoding="utf-8")
            report_results.write_blocked_benchmark(planned, reason, output)
            second = output.read_text(encoding="utf-8")

        self.assertEqual(first, second)
        self.assertIn("- Status: `BLOCKED`", first)
        self.assertTrue(
            first.rstrip().endswith(
                report_results.PLANNED_BLOCKED_COMPLETION_MARKER
            )
        )
        self.assertIn("skill\\|&lt;unsafe&gt;", first)
        self.assertIn("missing \\[tool\\](https://unsafe.invalid) \\|", first)
        self.assertIn("ANTHROPIC\\_API\\_KEY=&lt;redacted&gt;", first)
        self.assertIn("Authorization=&lt;redacted&gt;", first)
        self.assertNotIn("do-not-publish", first)
        self.assertNotIn("also-private", first)
        self.assertNotIn("\N{RIGHT-TO-LEFT OVERRIDE}", first)


class VerdictTest(unittest.TestCase):
    def _create(self, benchmark_text: str | None, outcome: str) -> dict:
        planned = report_results._canonical_row(row("skill__spec__rtx", "spec"))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            benchmark_path = root / "benchmark.md"
            if benchmark_text is not None:
                benchmark_path.write_text(benchmark_text, encoding="utf-8")
            output = root / "verdict.json"
            verdict = report_results.create_verdict(
                row=planned,
                step_outcome=outcome,
                benchmark_path=benchmark_path,
                output=output,
            )
            self.assertEqual(verdict, json.loads(output.read_text(encoding="utf-8")))
            return verdict

    def test_classifies_pass(self):
        verdict = self._create(benchmark("PASS 1.0", "secret=not-exported"), "success")
        self.assertEqual(verdict["status"], "PASS")
        self.assertTrue(verdict["benchmark"]["present"])
        self.assertNotIn("not-exported", json.dumps(verdict))

    def test_classifies_fail_from_benchmark_or_step_outcome(self):
        self.assertEqual(self._create(benchmark("FAIL 0.5"), "success")["status"], "FAIL")
        self.assertEqual(self._create(benchmark("PASS 1.0"), "failure")["status"], "FAIL")

    def test_classifies_blocked_even_when_eval_step_failed(self):
        blocked = (
            "# Benchmark\n\n- Status: `BLOCKED`\n\n"
            f"{report_results.EVAL_ROW_COMPLETION_MARKER}\n"
        )
        self.assertEqual(self._create(blocked, "failure")["status"], "BLOCKED")

    def test_classifies_missing_benchmark_or_cancelled_step(self):
        self.assertEqual(self._create(None, "failure")["status"], "MISSING")
        self.assertEqual(self._create(benchmark("PASS 1.0"), "cancelled")["status"], "MISSING")

    def test_partial_benchmark_is_missing_even_when_it_contains_a_result(self):
        partial = benchmark("FAIL 0.5").replace(
            report_results.EVAL_ROW_COMPLETION_MARKER,
            "",
        )
        self.assertEqual(self._create(partial, "failure")["status"], "MISSING")


class AggregateTest(unittest.TestCase):
    def _write_artifact(
        self,
        root: Path,
        planned: dict[str, str],
        status: str,
        outcome: str,
    ) -> None:
        artifact = root / planned["slug"]
        artifact.mkdir(parents=True)
        benchmark_path = artifact / "benchmark.md"
        if status == "BLOCKED":
            report_results.write_blocked_benchmark(
                report_results._canonical_row(planned),
                "unsupported public coverage",
                benchmark_path,
            )
        else:
            benchmark_path.write_text(benchmark(status), encoding="utf-8")
        report_results.create_verdict(
            row=report_results._canonical_row(planned),
            step_outcome=outcome,
            benchmark_path=benchmark_path,
            output=artifact / "verdict.json",
        )

    def test_aggregate_marks_absent_row_missing_and_only_missing_is_nonzero(self):
        rows = [
            row("skill__pass__rtx", "pass"),
            row("skill__fail__rtx", "fail"),
            row("skill__blocked__rtx", "blocked"),
            row("skill__absent__rtx", "absent"),
        ]
        rows[2]["kind"] = "blocked"
        rows[2]["reason"] = "standalone deployment is not available"
        matrix = {"include": rows}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifacts = root / "artifacts"
            self._write_artifact(artifacts, rows[0], "PASS 1.0", "success")
            self._write_artifact(artifacts, rows[1], "FAIL 0.0", "failure")
            self._write_artifact(artifacts, rows[2], "BLOCKED", "success")
            markdown_output = root / "combined.md"
            json_output = root / "combined.json"

            combined, return_code = report_results.aggregate_results(
                matrix=matrix,
                artifacts_root=artifacts,
                markdown_output=markdown_output,
                json_output=json_output,
            )

            self.assertEqual(return_code, 1)
            self.assertEqual(
                combined["counts"],
                {"PASS": 1, "FAIL": 1, "BLOCKED": 1, "MISSING": 1},
            )
            self.assertEqual(combined["report_status"], "INCOMPLETE")
            self.assertEqual([item["status"] for item in combined["rows"]], [
                "PASS",
                "FAIL",
                "BLOCKED",
                "MISSING",
            ])
            self.assertEqual(
                combined["rows"][2]["reason"],
                "standalone deployment is not available",
            )
            markdown = markdown_output.read_text(encoding="utf-8")
            self.assertIn("| 1 | 1 | 1 | 1 |", markdown)
            self.assertIn("standalone deployment is not available", markdown)
            self.assertNotIn(str(root), markdown)

            self._write_artifact(artifacts, rows[3], "PASS 1.0", "success")
            complete, return_code = report_results.aggregate_results(
                matrix=matrix,
                artifacts_root=artifacts,
                markdown_output=markdown_output,
                json_output=json_output,
            )
            first_json = json_output.read_bytes()
            first_markdown = markdown_output.read_bytes()
            complete_again, second_return_code = report_results.aggregate_results(
                matrix=matrix,
                artifacts_root=artifacts,
                markdown_output=markdown_output,
                json_output=json_output,
            )

            self.assertEqual(return_code, 0)
            self.assertEqual(second_return_code, 0)
            self.assertEqual(complete["report_status"], "COMPLETE")
            self.assertEqual(complete, complete_again)
            self.assertEqual(first_json, json_output.read_bytes())
            self.assertEqual(first_markdown, markdown_output.read_bytes())

    def test_hash_mismatch_is_missing(self):
        planned = row("skill__pass__rtx", "pass")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifacts = root / "artifacts"
            self._write_artifact(artifacts, planned, "PASS 1.0", "success")
            (artifacts / planned["slug"] / "benchmark.md").write_text(
                benchmark("PASS 1.0", "changed after verdict"),
                encoding="utf-8",
            )
            combined, return_code = report_results.aggregate_results(
                matrix={"include": [planned]},
                artifacts_root=artifacts,
                markdown_output=root / "combined.md",
                json_output=root / "combined.json",
            )

        self.assertEqual(return_code, 1)
        self.assertEqual(combined["rows"][0]["status"], "MISSING")
        self.assertIn("hash", combined["rows"][0]["reason"])

    def test_planned_blocked_row_requires_its_reporting_step_to_succeed(self):
        planned = row("skill__blocked__rtx", "blocked")
        planned["kind"] = "blocked"
        planned["reason"] = "unsupported"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = root / "artifact"
            artifact.mkdir()
            benchmark_path = artifact / "benchmark.md"
            report_results.write_blocked_benchmark(
                report_results._canonical_row(planned),
                planned["reason"],
                benchmark_path,
            )
            verdict = report_results.create_verdict(
                row=report_results._canonical_row(planned),
                step_outcome="skipped",
                benchmark_path=benchmark_path,
                output=artifact / "verdict.json",
            )

        self.assertEqual(verdict["status"], "MISSING")

    def test_empty_plan_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(
                report_results.ReportInputError,
                "at least one row",
            ):
                report_results.aggregate_results(
                    matrix={"include": []},
                    artifacts_root=root / "artifacts",
                    markdown_output=root / "combined.md",
                    json_output=root / "combined.json",
                )


class CliTest(unittest.TestCase):
    def test_verdict_cli_accepts_row_file(self):
        planned = row("skill__pass__rtx", "pass")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            row_file = root / "row.json"
            row_file.write_text(json.dumps(planned), encoding="utf-8")
            benchmark_path = root / "benchmark.md"
            benchmark_path.write_text(benchmark("PASS 1.0"), encoding="utf-8")
            output = root / "verdict.json"

            return_code = report_results.main(
                [
                    "verdict",
                    "--row-file",
                    str(row_file),
                    "--step-outcome",
                    "success",
                    "--benchmark",
                    str(benchmark_path),
                    "--output",
                    str(output),
                ]
            )

        self.assertEqual(return_code, 0)


if __name__ == "__main__":
    unittest.main()
