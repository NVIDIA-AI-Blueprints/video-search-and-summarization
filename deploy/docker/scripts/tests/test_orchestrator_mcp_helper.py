# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import unittest
from pathlib import Path
from unittest import mock

HELPER_PATH = Path(__file__).parents[1] / "orchestrator_mcp_helper.py"
MODULE_SPEC = importlib.util.spec_from_file_location("orchestrator_mcp_helper_under_test", HELPER_PATH)
if MODULE_SPEC is None or MODULE_SPEC.loader is None:
    raise RuntimeError(f"Could not load {HELPER_PATH}")
helper = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(helper)


class ResolveOpenshellGatewayContainerTests(unittest.TestCase):
    def test_returns_first_matching_container_name(self) -> None:
        result = mock.Mock()
        result.stdout = "openshell-demo-abc\n"
        result.returncode = 0
        with mock.patch.object(helper.subprocess, "run", return_value=result) as run:
            name = helper.resolve_openshell_gateway_container("demo")
        self.assertEqual(name, "openshell-demo-abc")
        run.assert_called_once()
        args = run.call_args.args[0]
        self.assertIn("label=openshell.ai/sandbox-name=demo", args)

    def test_returns_none_when_no_containers(self) -> None:
        result = mock.Mock()
        result.stdout = "\n"
        result.returncode = 0
        with mock.patch.object(helper.subprocess, "run", return_value=result):
            self.assertIsNone(helper.resolve_openshell_gateway_container("demo"))


if __name__ == "__main__":
    unittest.main()
