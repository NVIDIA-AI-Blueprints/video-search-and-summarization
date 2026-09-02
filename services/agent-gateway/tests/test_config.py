# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from vss_agent_gateway.config import ConfigError, GatewayConfig


class ConfigTest(unittest.TestCase):
    def test_defaults_are_loopback_and_responses_protocol(self) -> None:
        with patch.dict(
            os.environ, {"AGENT_BACKEND_URL": "http://agent.local:8642"}, clear=True
        ):
            config = GatewayConfig.from_env()
        self.assertEqual(config.bind_host, "127.0.0.1")
        self.assertEqual(config.backend_protocol, "responses")
        self.assertEqual(config.backend_path, "/v1/responses")
        self.assertEqual(config.backend_session_field, "user")

    def test_non_loopback_bind_requires_authentication(self) -> None:
        with (
            patch.dict(
                os.environ,
                {
                    "AGENT_BACKEND_URL": "http://agent.local",
                    "AGENT_GATEWAY_BIND_HOST": "0.0.0.0",
                },
                clear=True,
            ),
            self.assertRaisesRegex(ConfigError, "TOKEN"),
        ):
            GatewayConfig.from_env()

    def test_rejects_credentials_in_backend_url_and_reserved_headers(self) -> None:
        with (
            patch.dict(
                os.environ,
                {"AGENT_BACKEND_URL": "http://user:pass@agent.local"},
                clear=True,
            ),
            self.assertRaisesRegex(ConfigError, "credentials"),
        ):
            GatewayConfig.from_env()
        with (
            patch.dict(
                os.environ,
                {
                    "AGENT_BACKEND_URL": "http://agent.local",
                    "AGENT_BACKEND_HEADERS_JSON": '{"Authorization":"secret"}',
                },
                clear=True,
            ),
            self.assertRaisesRegex(ConfigError, "cannot override"),
        ):
            GatewayConfig.from_env()


if __name__ == "__main__":
    unittest.main()
