#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regression tests for sequential HAProxy Ingress path rewrites."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HELM_ROOT = REPO_ROOT / "deploy/helm"
ANNOTATION = "haproxy.org/path-rewrite:"
REWRITE_RULE = re.compile(
    r"^(?P<source>\^?(?:/\S+|\{\{\s+\$\w+\s*\}\}\S*))\s+\S+$"
)


def rewrite_blocks(path: Path) -> list[list[str]]:
    """Return source patterns from each path-rewrite annotation in a manifest."""
    blocks: list[list[str]] = []
    lines = path.read_text().splitlines()
    for index, line in enumerate(lines):
        if ANNOTATION not in line or line.lstrip().startswith("#"):
            continue
        annotation_indent = len(line) - len(line.lstrip())
        sources: list[str] = []
        for candidate in lines[index + 1 :]:
            stripped = candidate.lstrip()
            indent = len(candidate) - len(stripped)
            if stripped and not stripped.startswith("{{") and indent <= annotation_indent:
                break
            match = REWRITE_RULE.fullmatch(stripped)
            if match:
                sources.append(match.group("source"))
        blocks.append(sources)
    return blocks


def literal_rules(relative_path: str) -> list[tuple[str, str]]:
    rules: list[tuple[str, str]] = []
    for line in (REPO_ROOT / relative_path).read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("^/"):
            source, replacement = stripped.split()
            rules.append((source, replacement))
    return rules


def apply_sequentially(path: str, rules: list[tuple[str, str]]) -> str:
    for source, replacement in rules:
        path = re.sub(source, replacement, path)
    return path


class HelmIngressRewriteTest(unittest.TestCase):
    def test_multi_rule_annotations_anchor_every_source_pattern(self):
        annotations = 0
        for manifest in HELM_ROOT.rglob("*.yaml"):
            for sources in rewrite_blocks(manifest):
                if len(sources) < 2:
                    continue
                annotations += 1
                with self.subTest(manifest=manifest.relative_to(REPO_ROOT)):
                    self.assertTrue(sources)
                    self.assertEqual(
                        [],
                        [source for source in sources if not source.startswith("^")],
                    )
        self.assertGreater(annotations, 0)

    def test_lvs_backend_path_cannot_trigger_later_alias_rule(self):
        rules = literal_rules(
            "deploy/helm/developer-profiles/dev-profile-lvs/"
            "vss-ingress-example-rewrites.yaml"
        )
        self.assertEqual(
            "/v1/video-summarization/jobs",
            apply_sequentially("/lvs/v1/video-summarization/jobs", rules),
        )

    def test_alert_bridge_backend_path_cannot_trigger_va_mcp_rule(self):
        rules = literal_rules(
            "deploy/helm/developer-profiles/dev-profile-alerts/"
            "vss-ingress-example-rewrites.yaml"
        )
        self.assertEqual(
            "/api/v1/va-mcp/x",
            apply_sequentially("/alert-bridge/api/v1/va-mcp/x", rules),
        )


if __name__ == "__main__":
    unittest.main()
