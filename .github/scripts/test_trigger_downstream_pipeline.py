#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import importlib.util
import os
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).with_name("trigger-downstream-pipeline.sh")
LOADER = SourceFileLoader("trigger_downstream_pipeline", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC
module = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(module)


class ExtraPipelineVariablesTest(unittest.TestCase):
    def test_accepts_string_map(self):
        with mock.patch.dict(
            os.environ,
            {
                "DOWNSTREAM_EXTRA_VARIABLES_JSON": (
                    '{"BUILD_TYPE":"ghcr-promotion","VSS_PROMOTION_TAG":"develop-abc"}'
                )
            },
            clear=True,
        ):
            self.assertEqual(
                module.extra_pipeline_variables(),
                {
                    "BUILD_TYPE": "ghcr-promotion",
                    "VSS_PROMOTION_TAG": "develop-abc",
                },
            )

    def test_rejects_reserved_variable_override(self):
        with mock.patch.dict(
            os.environ,
            {"DOWNSTREAM_EXTRA_VARIABLES_JSON": '{"VSS_SUBMODULE_HASH":"wrong"}'},
            clear=True,
        ):
            with self.assertRaises(SystemExit):
                module.extra_pipeline_variables()

    def test_empty_value_is_noop(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(module.extra_pipeline_variables(), {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
