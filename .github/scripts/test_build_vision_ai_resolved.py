#!/usr/bin/env -S uv run --quiet --script
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
# /// script
# requires-python = ">=3.9"
# dependencies = ["pyyaml"]
# ///

from __future__ import annotations

import re
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
REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SCRIPT_DIR))

from validate_nim_hardware_env import required_tuning_files
from validate_resolved_yml import validate_document


class ValidateResolvedYmlTest(unittest.TestCase):
    def test_root_compose_requires_deployment_roots(self) -> None:
        compose = (REPOSITORY / "deploy/docker/compose.yml").read_text()

        for key in ("VSS_APPS_DIR", "VSS_DATA_DIR", "HOST_IP", "VSS_PUBLIC_HOST"):
            self.assertIn(f"${{{key}:?", compose)

    def test_base_profile_keeps_documented_override_anchor(self) -> None:
        profiles = (
            REPOSITORY / "deploy/docker/developer-profiles/compose.yml"
        ).read_text()
        base_compose = (
            REPOSITORY
            / "deploy/docker/developer-profiles/dev-profile-base/compose.yml"
        )

        self.assertIn("dev-profile-base/compose.yml", profiles)
        self.assertIn("Intentionally empty, and kept", base_compose.read_text())

    def test_override_templates_do_not_assign_stock_sentinels(self) -> None:
        templates = [
            *(
                REPOSITORY / "deploy/docker/developer-profiles"
            ).glob("dev-profile-*/overrides.env"),
            REPOSITORY
            / "deploy/docker/industry-profiles/warehouse-operations/overrides.env",
        ]

        for template in templates:
            content = template.read_text()
            self.assertNotIn("<HOST_IP>", content, str(template))
            self.assertNotIn("/path/to/deploy/docker", content, str(template))
            self.assertNotIn("/path/to/vss-apps-data", content, str(template))
            self.assertNotIn("/path/to/vss-warehouse-app-data", content, str(template))

    def test_optional_minio_environment_wiring_has_a_source(self) -> None:
        compose = (
            REPOSITORY / "deploy/docker/services/video-summarization/compose.yml"
        ).read_text()
        profile = (
            REPOSITORY / "deploy/docker/developer-profiles/dev-profile-lvs/.env"
        ).read_text()
        helm = (
            REPOSITORY / "deploy/helm/services/video-summarization/values.yaml"
        ).read_text()

        self.assertIn("MINIO_PASSWORD=${MINIO_PASSWORD:-}", compose)
        self.assertIn("MINIO_PASSWORD=''", profile)
        for variable in (
            "MINIO_PASSWORD",
            "MINIO_USERNAME",
        ):
            self.assertIn(f"name: {variable}", helm)

    def test_dynamic_nim_hardware_env_files_are_optional_at_parse_time(self) -> None:
        nims = REPOSITORY / "deploy/docker/services/nim"
        compose_files = (
            nims / "nemotron-3.5-lightning-30b-a3b/compose.yml",
            nims / "cosmos3-reasoner/compose.yml",
            nims / "nvidia-nemotron-nano-9b-v2-fp8/compose.yml",
        )

        for compose_file in compose_files:
            content = compose_file.read_text()
            optional_hardware_envs = re.findall(
                r"- path: [^\n]+/hw-\$\{HARDWARE_PROFILE\}(?:-shared)?\.env\n"
                r"\s+required: false",
                content,
            )
            self.assertEqual(len(optional_hardware_envs), 2, str(compose_file))

    def test_dev_profile_preflights_the_selected_llm_hardware_file(self) -> None:
        # Making the NIM hw-*.env files optional at parse time removes Compose's
        # own failure for a missing tuning file. Only the LLM is deployed as a
        # standalone NIM (the VLM always runs as rtvi-vlm), so this preflight is
        # what still catches an unsupported board/mode pair.
        helper = (
            REPOSITORY / "deploy/docker/scripts/dev-profile.sh"
        ).read_text()

        self.assertIn("LLM '${_llm_slug_final}' has no tuning file", helper)

    def test_all_build_vision_resolve_paths_preflight_selected_nims(self) -> None:
        references = (
            REPOSITORY / "skills/vss-build-vision-ai/references/composition.md",
            REPOSITORY / "skills/vss-build-vision-ai/references/deployment.md",
            REPOSITORY
            / "skills/vss-build-vision-ai/references/profiles/warehouse.md",
        )

        for reference in references:
            content = reference.read_text()
            resolution = content.index(
                'config --no-consistency > "$BUILD_DIR/resolved.yml"'
            )
            validator = content.rfind(
                "validate_nim_hardware_env.py", 0, resolution
            )
            self.assertNotEqual(validator, -1, str(reference))
            self.assertLess(validator, resolution, str(reference))
            self.assertIn(
                "config --environment --no-consistency", content, str(reference)
            )
            validator_guard = content.rfind("if !", 0, validator)
            self.assertNotEqual(validator_guard, -1, str(reference))
            self.assertLess(validator - validator_guard, 200, str(reference))
            self.assertIn(
                '--profiles "$effective_profiles"', content, str(reference)
            )
            self.assertIn(
                '--hardware-profile "$effective_hardware"', content, str(reference)
            )

    def test_build_vision_docs_describe_blank_roots_and_explicit_nim_gate(
        self,
    ) -> None:
        skill = REPOSITORY / "skills/vss-build-vision-ai"
        warehouse = (skill / "references/profiles/warehouse.md").read_text()
        composition = (skill / "references/composition.md").read_text()
        teardown = (skill / "references/teardown.md").read_text()

        self.assertIn(
            "`overrides.env` ships `VSS_APPS_DIR`, `VSS_DATA_DIR`, and "
            "`HOST_IP` blank",
            warehouse,
        )
        self.assertIn("Root Compose `${VAR:?}` checks", composition)
        self.assertIn("validate_nim_hardware_env.py", warehouse)
        self.assertIn("checked-in override template", teardown)

        stale_claims = (
            "Ship as `/path/to/…` sentinels",
            "compose dies with a bare \"no such file\"",
            "placeholders the checked-in files ship",
        )
        for claim in stale_claims:
            self.assertNotIn(claim, warehouse + teardown)

    def test_root_compose_skill_guidance_requires_deployment_roots(self) -> None:
        auto_calibration = (
            REPOSITORY
            / "skills/tools/vss-generate-video-calibration/references"
            / "deploy-auto-calibration-service.md"
        ).read_text()
        rtsp = (
            REPOSITORY
            / "skills/tools/vss-generate-video-calibration/references/rtsp.md"
        ).read_text()
        vios = (
            REPOSITORY
            / "skills/operations/vss-manage-video-io-storage/references"
            / "integrate-vios-service.md"
        ).read_text()

        for variable in ("VSS_APPS_DIR", "VSS_DATA_DIR", "HOST_IP", "EXTERNAL_IP"):
            self.assertIn(variable, auto_calibration)
            self.assertIn(variable, rtsp)
            self.assertIn(variable, vios)

        self.assertNotIn("/path/to/deploy/docker", auto_calibration)
        self.assertNotIn("docker compose --env-file ...", rtsp)
        self.assertIn(
            "--env-file industry-profiles/warehouse-operations/generated.env",
            rtsp,
        )
        self.assertIn(
            "for _key in VSS_APPS_DIR VSS_DATA_DIR HOST_IP EXTERNAL_IP VSS_PUBLIC_HOST",
            auto_calibration,
        )
        self.assertIn(
            'if ! _value="$(printenv "$_key")" || [ -z "$_value" ]; then',
            auto_calibration,
        )
        self.assertIn("if ! ALL_IMAGES=", auto_calibration)
        self.assertIn('if [ -z "$AMC_IMAGES" ]; then', auto_calibration)
        self.assertIn("cd deploy/docker || {", auto_calibration)
        self.assertGreaterEqual(
            auto_calibration.count("--env-file containers.env"), 5
        )
        self.assertIn("cd deploy/docker || {", vios)
        self.assertEqual(vios.count("--env-file containers.env"), 2)

    def test_selected_nim_hardware_file_resolution(self) -> None:
        files = required_tuning_files(
            REPOSITORY,
            "redis,llm_local_shared_model-a,vlm_local_model-b,rtvi-vlm",
            "H100",
        )

        self.assertEqual(
            [path.name for path in files],
            ["hw-H100-shared.env", "hw-H100.env"],
        )
        self.assertEqual(
            [path.parent.name for path in files],
            ["model-a", "model-b"],
        )

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
