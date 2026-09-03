# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = SERVICE_ROOT / "vss_agent_gateway"


class RuntimeDependencyTest(unittest.TestCase):
    def test_gateway_runtime_uses_only_the_python_standard_library(self) -> None:
        third_party: dict[str, set[str]] = {}
        for source_path in PACKAGE_ROOT.rglob("*.py"):
            tree = ast.parse(source_path.read_text(encoding="utf-8"), source_path)
            for node in ast.walk(tree):
                roots: set[str] = set()
                if isinstance(node, ast.Import):
                    roots = {alias.name.partition(".")[0] for alias in node.names}
                elif (
                    isinstance(node, ast.ImportFrom) and node.level == 0 and node.module
                ):
                    roots = {node.module.partition(".")[0]}
                for root in roots - sys.stdlib_module_names - {"vss_agent_gateway"}:
                    third_party.setdefault(root, set()).add(
                        str(source_path.relative_to(SERVICE_ROOT))
                    )

        self.assertEqual(third_party, {})
        self.assertFalse((SERVICE_ROOT / "requirements.txt").exists())
        self.assertNotIn(
            "pip install",
            (SERVICE_ROOT / "Dockerfile").read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
