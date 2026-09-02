#!/usr/bin/env -S uv run --quiet --script
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
# /// script
# requires-python = ">=3.9"
# dependencies = ["pyyaml"]
# ///

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = (
    Path(__file__).resolve().parents[2]
    / "skills"
    / "vss-build-vision-ai"
    / "scripts"
)
sys.path.insert(0, str(SCRIPT_DIR))

from validate_resolved_yml import validate_document


class ValidateResolvedYmlTest(unittest.TestCase):
    @staticmethod
    def external_agent_document(
        *,
        protocol: str = "openclaw-ws",
        backend_url: str = "ws://127.0.0.1:18789",
    ) -> dict[str, object]:
        return {
            "services": {
                "agent-gateway": {
                    "environment": {
                        "AGENT_BACKEND_PROTOCOL": protocol,
                        "AGENT_BACKEND_URL": backend_url,
                    }
                },
                "vss-ui": {
                    "environment": {
                        "AGENT_GATEWAY_URL": (
                            "http://host.docker.internal:18090"
                        ),
                        "NEXT_PUBLIC_FORCE_HTTP_CHAT_TRANSPORT": "true",
                        "NEXT_PUBLIC_ENABLE_CHAT_TAB": "true",
                    }
                },
            }
        }

    def test_rejects_empty_model(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            errors = validate_document({}, Path(directory))

            self.assertEqual(errors, ["resolved Compose model has no services"])

    def test_valid_checked_in_file_bind(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo_root = Path(directory)
            config = repo_root / "config" / "postgresql.conf"
            config.parent.mkdir()
            config.write_text("listen_addresses = '*'\n")
            document = {
                "services": {
                    "postgres": {
                        "environment": {"HOST": "192.0.2.10"},
                        "volumes": [
                            {
                                "type": "bind",
                                "source": str(config),
                                "target": "/etc/postgresql/postgresql.conf",
                                "read_only": True,
                            }
                        ],
                    }
                }
            }

            self.assertEqual(validate_document(document, repo_root), [])

    def test_rejects_resolved_placeholders(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            errors = validate_document(
                {
                    "services": {
                        "api": {
                            "environment": {
                                "HOST": "<HOST_IP>",
                                "CONFIG": "/path/to/deploy/docker/config.yml",
                            }
                        }
                    }
                },
                Path(directory),
            )

            self.assertEqual(len(errors), 2)

    def test_rejects_empty_external_agent_backend_url(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            document = self.external_agent_document(backend_url="")

            errors = validate_document(document, Path(directory))

            self.assertEqual(len(errors), 1)
            self.assertIn("AGENT_BACKEND_URL", errors[0])
            self.assertIn("non-empty absolute URL", errors[0])

    def test_rejects_external_agent_protocol_url_scheme_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            document = self.external_agent_document(
                protocol="openclaw-ws",
                backend_url="http://127.0.0.1:18789",
            )

            errors = validate_document(document, Path(directory))

            self.assertEqual(len(errors), 1)
            self.assertIn("scheme ws, wss", errors[0])

    def test_rejects_credential_bearing_external_agent_url(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            document = self.external_agent_document(
                backend_url=(
                    "ws://operator:secret@127.0.0.1:18789/?debug=true"
                )
            )

            errors = validate_document(document, Path(directory))

            self.assertEqual(len(errors), 1)
            self.assertIn("must not contain credentials", errors[0])

    def test_rejects_external_agent_without_ui_gateway_route(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            document = self.external_agent_document()
            ui_environment = document["services"]["vss-ui"]["environment"]
            ui_environment["AGENT_GATEWAY_URL"] = ""
            ui_environment["NEXT_PUBLIC_FORCE_HTTP_CHAT_TRANSPORT"] = "false"

            errors = validate_document(document, Path(directory))

            self.assertEqual(len(errors), 2)
            self.assertTrue(any("AGENT_GATEWAY_URL" in error for error in errors))
            self.assertTrue(
                any("FORCE_HTTP_CHAT_TRANSPORT" in error for error in errors)
            )

    def test_rejects_external_agent_without_vss_ui(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            document = self.external_agent_document()
            del document["services"]["vss-ui"]

            errors = validate_document(document, Path(directory))

            self.assertEqual(len(errors), 1)
            self.assertIn("requires service 'vss-ui'", errors[0])

    def test_rejects_external_agent_with_disabled_chat_tab(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            document = self.external_agent_document()
            ui_environment = document["services"]["vss-ui"]["environment"]
            ui_environment["NEXT_PUBLIC_ENABLE_CHAT_TAB"] = "false"

            errors = validate_document(document, Path(directory))

            self.assertEqual(len(errors), 1)
            self.assertIn("NEXT_PUBLIC_ENABLE_CHAT_TAB", errors[0])

    def test_accepts_complete_external_agent_with_internal_vss_agent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            document = self.external_agent_document()
            document["services"]["vss-agent"] = {
                "environment": {"ROLE": "internal-lvs-peer"}
            }

            errors = validate_document(document, Path(directory))

            self.assertEqual(errors, [])

    def test_accepts_responses_backend_with_http_origin(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            document = self.external_agent_document(
                protocol="responses",
                backend_url="http://127.0.0.1:8642",
            )

            errors = validate_document(document, Path(directory))

            self.assertEqual(errors, [])

    def test_allows_container_shell_interpolation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            errors = validate_document(
                {
                    "services": {
                        "postgres": {
                            "healthcheck": {
                                "test": [
                                    "CMD-SHELL",
                                    'pg_isready -U "$${POSTGRES_USER}"',
                                ]
                            }
                        }
                    }
                },
                Path(directory),
            )

            self.assertEqual(errors, [])

    def test_allows_generated_mutable_bind_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo_root = Path(directory)
            errors = validate_document(
                {
                    "services": {
                        "init": {
                            "volumes": [
                                {
                                    "type": "bind",
                                    "source": str(repo_root / "runtime" / ".env"),
                                    "target": "/mnt/runtime.env",
                                }
                            ]
                        }
                    }
                },
                repo_root,
            )

            self.assertEqual(errors, [])

    def test_allows_bind_source_generated_from_checked_in_template(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo_root = Path(directory)
            generated = repo_root / "developer-profiles" / "config.yml"
            generated.parent.mkdir()
            generated.with_name("config.yml.tmpl").write_text("key: ${VALUE}\n")
            errors = validate_document(
                {
                    "services": {
                        "render": {
                            "volumes": [
                                {
                                    "type": "bind",
                                    "source": str(generated),
                                    "target": "/config.yml",
                                    "read_only": True,
                                }
                            ]
                        }
                    }
                },
                repo_root,
            )

            self.assertEqual(errors, [])

    def test_rejects_missing_checked_in_bind_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo_root = Path(directory)
            errors = validate_document(
                {
                    "services": {
                        "postgres": {
                            "volumes": [
                                {
                                    "type": "bind",
                                    "source": str(repo_root / "missing.conf"),
                                    "target": "/etc/postgresql/postgresql.conf",
                                    "read_only": True,
                                }
                            ]
                        }
                    }
                },
                repo_root,
            )

            self.assertEqual(len(errors), 1)
            self.assertIn("does not exist", errors[0])

    def test_rejects_directory_mounted_to_file_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo_root = Path(directory)
            config_directory = repo_root / "postgresql.conf"
            config_directory.mkdir()
            errors = validate_document(
                {
                    "services": {
                        "postgres": {
                            "volumes": [
                                {
                                    "type": "bind",
                                    "source": str(config_directory),
                                    "target": "/etc/postgresql/postgresql.conf",
                                    "read_only": True,
                                }
                            ]
                        }
                    }
                },
                repo_root,
            )

            self.assertEqual(len(errors), 1)
            self.assertIn("mounts directory", errors[0])


if __name__ == "__main__":
    unittest.main()
