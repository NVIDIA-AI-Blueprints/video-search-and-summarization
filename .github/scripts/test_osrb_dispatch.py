#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Focused tests for the public-to-private OSRB dispatch boundary."""

from __future__ import annotations

import importlib.util
import os
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest import mock

DIRECTORY = Path(__file__).parent


def load_python(name: str, path: Path):
    loader = SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    assert spec
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


trigger = load_python("trigger_downstream", DIRECTORY / "trigger-downstream-pipeline.sh")
check = load_python("osrb_check", DIRECTORY / "osrb_check.py")


class DispatchTests(unittest.TestCase):
    def test_extra_variables_are_string_map(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"DOWNSTREAM_EXTRA_VARIABLES_JSON": '{"OSRB_REVIEW":"true","PR":"42"}'},
            clear=False,
        ):
            self.assertEqual(
                trigger.configured_extra_variables(),
                {"OSRB_REVIEW": "true", "PR": "42"},
            )

    def test_extra_variables_reject_non_string_values(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"DOWNSTREAM_EXTRA_VARIABLES_JSON": '{"PR":42}'},
            clear=False,
        ), self.assertRaises(SystemExit):
            trigger.configured_extra_variables()

    def test_check_external_id_is_private_pipeline_scoped(self) -> None:
        self.assertEqual(check.EXTERNAL_PREFIX, "gitlab-osrb:")


if __name__ == "__main__":
    unittest.main()
