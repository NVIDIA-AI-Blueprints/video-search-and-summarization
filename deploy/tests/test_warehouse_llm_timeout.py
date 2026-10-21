# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Warehouse report LLM requests must outlive ChatNVIDIA's 60s default.

GB300 warehouse nightly (2026-10-17 and 2026-10-18) failed report synthesis
with SocketTimeoutError exactly 60s after the incident video URL was ready,
three times. ChatNVIDIA's NVIDIA client defaults timeout to 60 seconds.
NAT NIMModelConfig extra=allow forwards a YAML timeout to that client.
HAProxy's LLM backend already waits 600s; the client must not be stingier.
"""

from __future__ import annotations

from pathlib import Path
import unittest

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WAREHOUSE_LLM_CONFIGS = (
    REPO_ROOT / "docker/industry-profiles/warehouse-operations/vss-agent/configs/config.yml",
    REPO_ROOT / "helm/industry-profiles/warehouse-operations/warehouse-2d-app/configs/vss-agent/config.yml",
)
MIN_REPORT_LLM_TIMEOUT_S = 600


class WarehouseLlmTimeoutTests(unittest.TestCase):
    def test_report_llm_timeout_outlives_chatnvidia_default(self) -> None:
        for path in WAREHOUSE_LLM_CONFIGS:
            with self.subTest(config=str(path.relative_to(REPO_ROOT))):
                llm = yaml.safe_load(path.read_text())["llms"]["llm"]
                timeout = llm.get("timeout")
                self.assertIsNotNone(
                    timeout,
                    f"{path} leaves ChatNVIDIA on its 60s default, which cut off "
                    "warehouse report synthesis on GB300",
                )
                self.assertGreaterEqual(float(timeout), MIN_REPORT_LLM_TIMEOUT_S)


if __name__ == "__main__":
    unittest.main()
