#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for compose_image_golden.py and the SSOT-aware name matching in
check_container_tag_source.py. Run directly:

    python3 .github/scripts/test_compose_image_golden.py
"""

from __future__ import annotations

import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_container_tag_source import (  # noqa: E402
    IMAGE_CONFIGS,
    image_refs_in_text,
    resolve_compose_vars,
)
from compose_image_golden import (  # noqa: E402
    load_containers_env,
    resolve_nested,
    uses_shared_coordinate,
)


class ResolveNestedTest(unittest.TestCase):
    def test_flat_default_applies_when_unset(self):
        self.assertEqual(
            resolve_nested("${A:-nvcr.io/nvidia/vss-core/img}:${T:-1.0}", {}),
            "nvcr.io/nvidia/vss-core/img:1.0",
        )

    def test_env_value_wins_over_default(self):
        self.assertEqual(resolve_nested("${A:-default}", {"A": "override"}), "override")

    def test_colon_dash_treats_empty_as_unset(self):
        self.assertEqual(resolve_nested("${A:-fallback}", {"A": ""}), "fallback")
        self.assertEqual(resolve_nested("${A-fallback}", {"A": ""}), "")

    def test_nested_default_resolves(self):
        env = {"REG": "nvcr.io/nvstaging/vss-core"}
        self.assertEqual(
            resolve_nested("${IMG:-${REG}/vss-agent}", env),
            "nvcr.io/nvstaging/vss-core/vss-agent",
        )

    def test_unset_without_default_is_kept_literally(self):
        self.assertEqual(
            resolve_nested("img:${VSS_AGENT_VERSION}", {}),
            "img:${VSS_AGENT_VERSION}",
        )

    def test_required_var_kept_when_unset(self):
        self.assertEqual(resolve_nested("${A:?msg}", {}), "${A:?msg}")
        self.assertEqual(resolve_nested("${A:?msg}", {"A": "v"}), "v")

    def test_deeply_nested(self):
        self.assertEqual(
            resolve_nested("${A:-${B:-${C:-leaf}}}", {}),
            "leaf",
        )


class LoadContainersEnvTest(unittest.TestCase):
    def test_top_down_self_referential_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "containers.env"
            env_file.write_text(
                "# comment\n"
                'REG="${REG:-nvcr.io/nvidia/vss-core}"\n'
                'IMG="${IMG:-${REG}/vss-agent}"\n'
                'TAG="${TAG:-3.2.1}"\n'
            )
            values = load_containers_env(env_file)
        self.assertEqual(values["REG"], "nvcr.io/nvidia/vss-core")
        self.assertEqual(values["IMG"], "nvcr.io/nvidia/vss-core/vss-agent")
        self.assertEqual(values["TAG"], "3.2.1")

    def test_shared_coordinate_refs_allow_single_ssot_tag_updates(self):
        self.assertTrue(
            uses_shared_coordinate(
                "${VSS_CONTAINER_REGISTRY}/vss-agent:${VSS_CONTAINER_TAG}"
            )
        )
        self.assertTrue(
            uses_shared_coordinate(
                "${VSS_CONTAINER_REGISTRY}/vss-configurator:${VSS_CONTAINER_TAG}"
            )
        )


class ParameterizedNameMatchingTest(unittest.TestCase):
    """The container-source gate must recognize SSOT-parameterized refs."""

    COMPOSE = """
services:
  vss-agent:
    image: ${VSS_AGENT_IMAGE:-${VSS_CONTAINER_REGISTRY:-nvcr.io/nvstaging/vss-core}/vss-agent}:${VSS_CONTAINER_TAG:-${VSS_AGENT_VERSION}}
  vss-ui:
    image: ${VSS_AGENT_UI_IMAGE:-${VSS_CONTAINER_REGISTRY:-nvcr.io/nvstaging/vss-core}/vss-agent-ui}:${VSS_CONTAINER_TAG:-${VSS_AGENT_UI_TAG:-3.2.1}}
  literal:
    image: nvcr.io/nvidia/vss-core/vss-agent:1.0
  other:
    image: postgres:16
"""

    def test_parameterized_registry_matches_and_raw_ref_returned(self):
        refs = image_refs_in_text(self.COMPOSE, "vss-agent")
        self.assertEqual(
            refs,
            [
                "${VSS_AGENT_IMAGE:-${VSS_CONTAINER_REGISTRY:-"
                "nvcr.io/nvstaging/vss-core}/vss-agent}"
                ":${VSS_CONTAINER_TAG:-${VSS_AGENT_VERSION}}",
                "nvcr.io/nvidia/vss-core/vss-agent:1.0",
            ],
        )

    def test_accepts_iterable_of_names(self):
        refs = image_refs_in_text(self.COMPOSE, ("vss-agent-ui", "vss-agent"))
        self.assertEqual(len(refs), 3)

    def test_third_party_not_matched(self):
        self.assertEqual(image_refs_in_text(self.COMPOSE, "postgres"), ["postgres:16"])

    def test_nested_global_registry_default_matches(self):
        ref = (
            "${VSS_AGENT_IMAGE:-${VSS_CONTAINER_REGISTRY:-"
            "nvcr.io/nvstaging/vss-core}/vss-agent}:"
            "${VSS_CONTAINER_TAG:-${VSS_AGENT_VERSION}}"
        )
        resolved, missing = resolve_compose_vars(
            ref,
            {
                "VSS_CONTAINER_REGISTRY": "ghcr.io/nvidia-ai-blueprints/vss",
                "VSS_CONTAINER_TAG": "develop-deadbeef",
                "VSS_AGENT_VERSION": "ignored",
            },
        )
        self.assertEqual(
            resolved,
            "ghcr.io/nvidia-ai-blueprints/vss/vss-agent:develop-deadbeef",
        )
        self.assertEqual(missing, ())

    def test_rtvi_embed_source_mapping_and_fixed_deployment_coordinate(self):
        config = IMAGE_CONFIGS["vss-rt-embed"]
        self.assertEqual(config.source_path, Path("services/rtvi/rt-embed"))

        compose = Path(
            "deploy/docker/services/rtvi/rtvi-embed/rtvi-embed-docker-compose.yml"
        ).read_text()
        refs = image_refs_in_text(compose, config.compose_names())
        self.assertEqual(len(refs), 1)

        resolved, missing = resolve_compose_vars(refs[0], {})
        self.assertEqual(missing, ())
        self.assertEqual(
            resolved,
            "nvcr.io/nvstaging/vss-core/vss-rt-embed:3.3.0-26.07.4",
        )

        overridden, missing = resolve_compose_vars(
            refs[0],
            {
                "RTVI_EMBED_IMAGE": "registry.example/rtvi-embed",
                "RTVI_EMBED_TAG": "immutable-tag",
            },
        )
        self.assertEqual(missing, ())
        self.assertEqual(overridden, "registry.example/rtvi-embed:immutable-tag")


class RtviEmbedDependencySourcesTest(unittest.TestCase):
    """RT Embed's CI image build must use only approved public package indexes."""

    ROOT = Path("services/rtvi/rt-embed/docker")
    APPROVED_INDEXES = {
        "https://pypi.org/simple/",
        "https://pypi.nvidia.com",
    }

    def test_python_dependency_configuration_uses_only_public_sources(self):
        pyproject_path = self.ROOT / "py_deps/pyproject.toml"
        pyproject = pyproject_path.read_text()
        requirements = (self.ROOT / "py_deps/requirements.txt").read_text()
        lockfile = (self.ROOT / "py_deps/pdm.lock").read_text()

        source_urls = {
            source["url"] for source in tomllib.loads(pyproject)["tool"]["pdm"]["source"]
        }
        self.assertEqual(source_urls, self.APPROVED_INDEXES)

        index_directives = {
            line.split(" ", maxsplit=1)[1]
            for line in requirements.splitlines()
            if line.startswith(("--index-url ", "--extra-index-url "))
        }
        self.assertEqual(index_directives, self.APPROVED_INDEXES)
        for package in ("vllm", "flashinfer", "xformers"):
            self.assertNotIn(package, requirements)
            self.assertNotIn(f'name = "{package}"', lockfile)

    def test_dockerfile_does_not_include_vllm_build_artifacts(self):
        dockerfile = (self.ROOT / "Dockerfile").read_text()
        self.assertNotIn("AS vllm_src", dockerfile)
        self.assertNotIn("flashinfer_cubin", dockerfile)
        self.assertIn("pip install --index-url https://pypi.org/simple", dockerfile)

    def test_remote_artifacts_are_verified_before_use(self):
        dockerfile = (self.ROOT / "Dockerfile").read_text()
        readme = (self.ROOT / "py_deps/README.md").read_text()

        deepstream_checksum = "fcafb7b5e4fbdf38b752eb35807011b69b14af826d6807180288d4d3d9b1ecbc"
        pdm_checksum = "e1c7f6455fa7ffc50cbc13e4d49c06dfaaf8e9b74d0c9b46287bf767f6a4e4fc"
        pyds_wheels = {
            (
                "https://github.com/NVIDIA-AI-IOT/deepstream_python_apps/releases/"
                "download/v1.2.2/pyds-1.2.2-cp312-cp312-linux_x86_64.whl"
            ): "74e13a6431cbef66b7a27da08658e0e30e7ca84bccb00a25a1a9c969608d3088",
            (
                "https://github.com/NVIDIA-AI-IOT/deepstream_python_apps/releases/"
                "download/v1.2.2/pyds-1.2.2-cp312-cp312-linux_aarch64.whl"
            ): "924bdbefb4dd931e62d7aca0b5967187e91eec618fc064f8239c52e9e4ed3a9b",
        }
        self.assertIn(deepstream_checksum, dockerfile)
        self.assertLess(
            dockerfile.index(deepstream_checksum),
            dockerfile.index("tar xf /tmp/deepstream_sdk_v9.1.0_x86_64.tbz2 -C /"),
        )

        self.assertNotIn("| python3 -", readme)
        self.assertIn(pdm_checksum, readme)
        self.assertLess(
            readme.index(pdm_checksum), readme.index('python3 "$PDM_INSTALLER_PATH"')
        )
        self.assertIn("sha256sum -c - &&", readme)

        self.assertNotIn("pip install https://github.com/NVIDIA-AI-IOT", dockerfile)
        self.assertIn('pip install "$pyds_wheel_path" --no-deps', dockerfile)
        for wheel_url, checksum in pyds_wheels.items():
            self.assertIn(wheel_url, dockerfile)
            self.assertIn(checksum, dockerfile)
            self.assertLess(
                dockerfile.index(checksum),
                dockerfile.index('pip install "$pyds_wheel_path" --no-deps'),
            )
        self.assertIn(
            'echo "$pyds_sha256  $pyds_wheel_path" | sha256sum -c -', dockerfile
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
