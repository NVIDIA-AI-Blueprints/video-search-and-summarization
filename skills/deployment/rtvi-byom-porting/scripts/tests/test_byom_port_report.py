# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path
import importlib.util
import unittest


SCRIPT = Path(__file__).parents[1] / "byom_port_report.py"
SPEC = importlib.util.spec_from_file_location("byom_port_report", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class ReportTest(unittest.TestCase):
    def test_handles_unexpected_shapes_and_escapes_markdown_cells(self):
        report = MODULE.build_report(
            {
                "model": "unexpected",
                "validation": "unexpected",
                "smoke": [
                    "unexpected",
                    {"name": "image|prompt", "status": True, "sample": "car|truck"},
                ],
                "caveats": "single caveat",
            }
        )

        self.assertIn("unknown model", report)
        self.assertIn("| image\\|prompt | PASS | car\\|truck |", report)
        self.assertIn("- single caveat", report)


if __name__ == "__main__":
    unittest.main()
