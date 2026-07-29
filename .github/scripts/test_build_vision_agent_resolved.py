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
    / "vss-build-vision-agent"
    / "scripts"
)
sys.path.insert(0, str(SCRIPT_DIR))

from validate_resolved_yml import validate_document


class ValidateResolvedYmlTest(unittest.TestCase):
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
