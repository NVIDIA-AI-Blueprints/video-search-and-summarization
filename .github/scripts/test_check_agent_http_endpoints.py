#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("check_agent_http_endpoints.py")
SPEC = importlib.util.spec_from_file_location("check_agent_http_endpoints", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
LINT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LINT)


class AgentHttpEndpointsTest(unittest.TestCase):
    def test_tree_has_no_docker_only_agent_http_endpoints(self) -> None:
        self.assertEqual([], LINT.scan_paths(LINT.default_paths()))

    def test_docker_only_elasticsearch_url_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "fake-agent.yml"
            config.write_text("elasticsearch_url: http://elasticsearch:9200\n")

            failures = LINT.scan_paths([config])

        self.assertEqual(1, len(failures))
        self.assertIn("Docker-only HTTP host 'elasticsearch'", failures[0])


if __name__ == "__main__":
    unittest.main()
