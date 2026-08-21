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
