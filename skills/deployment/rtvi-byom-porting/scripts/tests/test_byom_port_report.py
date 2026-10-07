# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).parents[1] / "byom_port_report.py"
SPEC = importlib.util.spec_from_file_location("byom_port_report", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class ReportTest(unittest.TestCase):
    def test_cli_reads_and_writes_utf8_under_ascii_locale(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "facts.json"
            output_path = Path(tmpdir) / "report.md"
            input_path.write_text(
                json.dumps({"model": {"name": "Cosmos α"}}, ensure_ascii=False),
                encoding="utf-8",
            )
            env = os.environ.copy()
            env.update(
                {
                    "LC_ALL": "C",
                    "PYTHONCOERCECLOCALE": "0",
                    "PYTHONUTF8": "0",
                }
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--input-json",
                    str(input_path),
                    "--write-markdown",
                    str(output_path),
                ],
                capture_output=True,
                env=env,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Cosmos α", output_path.read_text(encoding="utf-8"))

    def test_handles_unexpected_shapes_and_escapes_markdown_cells(self):
        report = MODULE.build_report(
            {
                "model": "unexpected",
                "validation": "unexpected",
                "smoke": [
                    "unexpected",
                    {"name": "image|prompt", "status": True, "sample": "car|truck"},
                    {"name": "empty", "status": True, "sample": ""},
                ],
                "caveats": "single caveat",
                "integration": {"path": "plugin", "eager_mode": False},
                "performance": {"latency": "42 ms"},
            }
        )

        self.assertIn("unknown model", report)
        self.assertIn("| image\\|prompt | PASS | car\\|truck |", report)
        self.assertIn("| empty | UNKNOWN | - |", report)
        self.assertIn("- Integration path: plugin", report)
        self.assertIn("- Latency: 42 ms", report)
        self.assertIn("- single caveat", report)

    def test_escapes_untrusted_markdown_in_every_output_context(self):
        value = "`code` **bold** [link](url) <tag> a|b"
        report = MODULE.build_report(
            {
                "model": {
                    "name": value,
                    "source": value,
                    "revision": value,
                    "backend": value,
                    "container": value,
                },
                "validation": {
                    "legible_output": {"status": True, "evidence": value},
                },
                "smoke": [{"name": value, "status": True, "sample": value}],
                "integration": {
                    "path": value,
                    "image_digest": value,
                    "eager_mode": value,
                    "eager_reason": value,
                    "platforms": value,
                    "custom_kernels": value,
                },
                "performance": {
                    "accuracy": value,
                    "latency": value,
                    "throughput": value,
                    "gpu_utilization": value,
                    "gpu_memory": value,
                },
                "caveats": [value],
                "next_step": value,
            }
        )

        escaped = r"\`code\` \*\*bold\*\* \[link\]\(url\) \<tag\> a\|b"
        code = "`` `code` **bold** [link](url) <tag> a|b ``"
        self.assertIn(f"# RTVI BYOM Port Report: {escaped}", report)
        self.assertIn(f"- Source: {code}", report)
        self.assertIn(f"- Revision/tag: {code}", report)
        self.assertIn(f"- Backend: {code}", report)
        self.assertIn(f"- Container: {code}", report)
        self.assertIn(f"| Output is legible | PASS | {escaped} |", report)
        self.assertIn(f"| {escaped} | PASS | {escaped} |", report)
        self.assertIn(f"- Image digest: {code}", report)
        for label in (
            "Integration path",
            "Eager mode",
            "Eager reason",
            "Platform evidence",
            "Custom kernels",
            "Accuracy",
            "Latency",
            "Throughput",
            "GPU utilization",
            "GPU memory",
        ):
            self.assertIn(f"- {label}: {escaped}", report)
        self.assertEqual(report.count(f"- {escaped}"), 2)
        self.assertEqual(MODULE._md_code("a `` b"), "```a `` b```")
        self.assertEqual(MODULE._md_code(""), "<code></code>")
        self.assertEqual(MODULE._md_code(" "), "` `")
        self.assertEqual(MODULE._md_text("AT&amp;T"), r"AT\&amp;T")


if __name__ == "__main__":
    unittest.main()
