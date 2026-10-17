#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for compose_image_golden.py and the SSOT-aware name matching in
check_container_tag_source.py. Run directly:

    python3 .github/scripts/test_compose_image_golden.py
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_container_tag_source import (  # noqa: E402
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


class ReleaseRegistryDefaultsTest(unittest.TestCase):
    CONTAINERS_ENV = Path(__file__).resolve().parents[2] / "deploy/docker/containers.env"
    IMAGE_VARS = re.findall(r"^(\w+_IMAGE)=", CONTAINERS_ENV.read_text(), re.MULTILINE)

    def resolve_images(self, overrides, names=None):
        result = subprocess.run(
            [
                "/bin/bash",
                "-c",
                'source "$1"; shift; for name; do printf "%s=%s\\n" "$name" "${!name}"; done',
                "containers-env-test",
                str(self.CONTAINERS_ENV),
                *(self.IMAGE_VARS if names is None else names),
            ],
            env=overrides,
            check=True,
            capture_output=True,
            text=True,
        )
        return dict(line.split("=", 1) for line in result.stdout.splitlines())

    def test_all_images_default_to_release_registry(self):
        images = self.resolve_images({})
        self.assertTrue(images)
        for name, image in images.items():
            with self.subTest(name=name):
                self.assertTrue(image.startswith("nvcr.io/nvidia/vss-core/"), image)

    def test_custom_registry_applies_to_all_images(self):
        images = self.resolve_images(
            {
                "VSS_CONTAINER_REGISTRY": "registry.example/release",
            }
        )
        for name, image in images.items():
            with self.subTest(name=name):
                self.assertTrue(image.startswith("registry.example/release/"), image)

    def test_individual_image_overrides_are_preserved(self):
        for name in self.IMAGE_VARS:
            with self.subTest(name=name):
                override = "registry.example/custom/image:custom-tag"
                images = self.resolve_images(
                    {
                        "VSS_CONTAINER_REGISTRY": "registry.example/shared",
                        name: override,
                    }
                )
                self.assertEqual(images.pop(name), override)
                self.assertTrue(
                    all(
                        image.startswith("registry.example/shared/")
                        for image in images.values()
                    )
                )

    def test_empty_overrides_fall_back_to_release_registry(self):
        overrides = dict.fromkeys(self.IMAGE_VARS, "")
        overrides["VSS_CONTAINER_REGISTRY"] = ""
        self.assertEqual(self.resolve_images(overrides), self.resolve_images({}))

    TAG_VARS = re.findall(r"^(\w+_TAG)=", CONTAINERS_ENV.read_text(), re.MULTILINE)
    SUFFIX_TAGS = {
        "VSS_RT_EMBED_TAG", "VSS_RT_CV_TAG", "VSS_RT_VLM_TAG",
        "VSS_VIDEO_SUMMARIZATION_TAG",
    }

    def test_all_tags_default_to_release(self):
        tags = self.resolve_images({}, self.TAG_VARS)
        self.assertTrue(tags)
        self.assertEqual(set(tags.values()), {"3.3.0"})
        self.assertTrue(self.resolve_images({})["SDR_MW_L_IMAGE"].endswith(":3.3.0"))

    def test_shared_tag_and_suffix(self):
        for shared_tag in ("3.3.0", "custom-tag"):
            with self.subTest(shared_tag=shared_tag):
                overrides = {"VSS_CONTAINER_TAG": shared_tag, "VSS_CONTAINER_TAG_SUFFIX": "-sbsa"}
                tags = self.resolve_images(overrides, self.TAG_VARS)
                for name, tag in tags.items():
                    self.assertEqual(tag, shared_tag + ("-sbsa" if name in self.SUFFIX_TAGS else ""), name)
                self.assertTrue(self.resolve_images(overrides)["SDR_MW_L_IMAGE"].endswith(":" + shared_tag))

    def test_individual_tag_overrides_take_precedence(self):
        for name in self.TAG_VARS:
            if name == "VSS_CONTAINER_TAG":
                continue
            with self.subTest(name=name):
                overrides = {
                    "VSS_CONTAINER_TAG": "shared-tag",
                    "VSS_CONTAINER_TAG_SUFFIX": "-sbsa",
                    name: "individual-tag",
                }
                self.assertEqual(self.resolve_images(overrides, self.TAG_VARS)[name], "individual-tag")

    def test_empty_tag_overrides_use_release_defaults(self):
        overrides = dict.fromkeys(self.TAG_VARS, "")
        self.assertEqual(set(self.resolve_images(overrides, self.TAG_VARS).values()), {"3.3.0"})


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
