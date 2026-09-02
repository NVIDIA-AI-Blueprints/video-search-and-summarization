# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from vss_agent_gateway.capabilities import encode_receipt
from vss_agent_gateway.config import ConfigError, GatewayConfig

from tests.test_capabilities import valid_receipt


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

        for header in (
            "Authorization",
            "Content-Type",
            "Connection",
            "Proxy-Authorization",
        ):
            with (
                self.subTest(header=header),
                patch.dict(
                    os.environ,
                    {
                        "AGENT_BACKEND_URL": "http://agent.local",
                        "AGENT_BACKEND_HEADERS_JSON": json.dumps({header: "unsafe"}),
                    },
                    clear=True,
                ),
                self.assertRaisesRegex(ConfigError, "cannot override"),
            ):
                GatewayConfig.from_env()

    def test_session_routing_cannot_override_protocol_or_auth_fields(self) -> None:
        for variable, value in (
            ("AGENT_BACKEND_SESSION_FIELD", "input"),
            ("AGENT_BACKEND_SESSION_HEADER", "Authorization"),
            ("AGENT_BACKEND_SESSION_HEADER", "bad header"),
        ):
            with (
                self.subTest(variable=variable, value=value),
                patch.dict(
                    os.environ,
                    {"AGENT_BACKEND_URL": "http://agent.local", variable: value},
                    clear=True,
                ),
                self.assertRaises(ConfigError),
            ):
                GatewayConfig.from_env()

    def test_rejects_malformed_extra_header_names_and_values(self) -> None:
        for headers in (
            {"Bad Header": "value"},
            {"X-Test": "value\nsmuggled"},
            {"X-Test": "é"},
            {"X-Test": "x" * 8_193},
        ):
            with (
                self.subTest(headers=headers),
                patch.dict(
                    os.environ,
                    {
                        "AGENT_BACKEND_URL": "http://agent.local",
                        "AGENT_BACKEND_HEADERS_JSON": json.dumps(headers),
                    },
                    clear=True,
                ),
                self.assertRaises(ConfigError),
            ):
                GatewayConfig.from_env()

    def test_requires_and_loads_a_digest_bound_vss_receipt(self) -> None:
        with (
            patch.dict(
                os.environ,
                {
                    "AGENT_BACKEND_URL": "http://agent.local",
                    "AGENT_REQUIRE_VSS_CAPABILITIES": "true",
                },
                clear=True,
            ),
            self.assertRaisesRegex(ConfigError, "CAPABILITIES_B64 is required"),
        ):
            GatewayConfig.from_env()

        encoded, digest = encode_receipt(valid_receipt())
        with patch.dict(
            os.environ,
            {
                "AGENT_BACKEND_URL": "http://agent.local",
                "AGENT_REQUIRE_VSS_CAPABILITIES": "true",
                "AGENT_VSS_CAPABILITIES_B64": encoded,
                "AGENT_VSS_CAPABILITIES_SHA256": digest,
                "AGENT_EXPECTED_VSS_RUNTIME_REF": "a" * 40,
            },
            clear=True,
        ):
            config = GatewayConfig.from_env()

        self.assertIsNotNone(config.vss_capabilities)
        assert config.vss_capabilities is not None
        self.assertEqual(config.vss_capabilities.harness, "openclaw")

    def test_rejects_a_receipt_from_a_different_runtime_commit(self) -> None:
        encoded, digest = encode_receipt(valid_receipt())
        with (
            patch.dict(
                os.environ,
                {
                    "AGENT_BACKEND_URL": "http://agent.local",
                    "AGENT_REQUIRE_VSS_CAPABILITIES": "true",
                    "AGENT_VSS_CAPABILITIES_B64": encoded,
                    "AGENT_VSS_CAPABILITIES_SHA256": digest,
                    "AGENT_EXPECTED_VSS_RUNTIME_REF": "b" * 40,
                },
                clear=True,
            ),
            self.assertRaisesRegex(ConfigError, "does not match"),
        ):
            GatewayConfig.from_env()

    def test_rejects_receipt_without_digest(self) -> None:
        encoded, _ = encode_receipt(valid_receipt())
        with (
            patch.dict(
                os.environ,
                {
                    "AGENT_BACKEND_URL": "http://agent.local",
                    "AGENT_VSS_CAPABILITIES_B64": encoded,
                },
                clear=True,
            ),
            self.assertRaisesRegex(ConfigError, "CAPABILITIES_SHA256 is required"),
        ):
            GatewayConfig.from_env()


if __name__ == "__main__":
    unittest.main()
