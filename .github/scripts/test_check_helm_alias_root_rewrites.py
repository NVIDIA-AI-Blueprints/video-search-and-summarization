#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

SCRIPT = Path(__file__).with_name("check_helm_alias_root_rewrites.py")
SPEC = importlib.util.spec_from_file_location("check_helm_alias_root_rewrites", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
LINT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LINT)

# A route table with just enough rows to exercise both spellings: /vios is
# rewritten onto /vst, /va-mcp is stripped, /kibana is absent so the spelling
# rule has something it must stay quiet about.
ROUTE_TABLE = """\
{{- define "vss.ingress.routeTable" -}}
- key: vst
  path: /vst
  pathType: Prefix
  rewrite: none
- key: vst
  path: /vios
  pathType: Prefix
  rewrite: /vst
- key: vst
  path: /storage
  pathType: Prefix
  rewrite: /vst/storage
- key: va-mcp
  path: /va-mcp
  pathType: Prefix
  rewrite: strip
{{- end -}}
"""

GOOD_REWRITES = (
    "      ^/vios/(.*) /vst/\\1\n"
    "      ^/vios$ /vst\n"
    "      ^/storage/(.*) /vst/storage/\\1\n"
    "      ^/storage$ /vst/storage\n"
    "      ^/va-mcp/(.*) /\\1\n"
    "      ^/va-mcp$ /\n"
)

GOOD_PATHS = (
    "          - path: /vios\n"
    "            pathType: Prefix\n"
    "          - path: /storage\n"
    "            pathType: Prefix\n"
    "          - path: /va-mcp\n"
    "            pathType: Prefix\n"
)


def _manifest(directory: str, *, rewrites: str = GOOD_REWRITES, paths: str = GOOD_PATHS) -> Path:
    """Write a minimal ingress manifest shaped like the warehouse charts."""
    root = Path(directory)
    (root / "services/common/templates").mkdir(parents=True, exist_ok=True)
    (root / "services/common/templates/_ingress-routes.tpl").write_text(ROUTE_TABLE)
    path = root / "vss-ingress.yaml"
    path.write_text(
        "apiVersion: networking.k8s.io/v1\n"
        "kind: Ingress\n"
        "metadata:\n"
        "  annotations:\n"
        "    haproxy.org/path-rewrite: |\n"
        + rewrites
        + "    haproxy.org/frontend-config-snippet: |\n"
        "      http-request set-var(txn.path) path\n"
        "spec:\n"
        "  rules:\n"
        "    -\n"
        "      http:\n"
        "        paths:\n"
        + paths
    )
    return path


def _run(directory: str) -> tuple[int, str]:
    """Run the lint over a scratch helm root, returning (exit code, stderr)."""
    stderr = io.StringIO()
    with redirect_stderr(stderr), redirect_stdout(io.StringIO()):
        code = LINT.main(
            [
                "--helm-root",
                directory,
                "--route-table",
                str(Path(directory) / "services/common/templates/_ingress-routes.tpl"),
            ]
        )
    return code, stderr.getvalue()


class RootReplacementTest(unittest.TestCase):
    """The root target is derived, not guessed, and matches the template's rule."""

    def test_rewrite_onto_a_prefix_drops_the_capture_tail(self):
        self.assertEqual("/vst", LINT.root_replacement("/vst/\\1"))
        self.assertEqual("/vst/storage", LINT.root_replacement("/vst/storage/\\1"))

    def test_a_strip_becomes_the_bare_root(self):
        """`^/X/(.*) /\\1` leaves nothing, and HAProxy needs a path to set."""
        self.assertEqual("/", LINT.root_replacement("/\\1"))


class RootPairingTest(unittest.TestCase):
    def test_a_complete_manifest_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            _manifest(directory)
            code, stderr = _run(directory)
            self.assertEqual(0, code, stderr)

    def test_a_missing_root_rule_is_reported(self):
        """The warehouse defect: /X/(.*) present, /X$ absent."""
        with tempfile.TemporaryDirectory() as directory:
            _manifest(
                directory,
                rewrites=(
                    "      ^/vios/(.*) /vst/\\1\n"
                    "      ^/vios$ /vst\n"
                    "      ^/storage/(.*) /vst/storage/\\1\n"
                    "      ^/va-mcp/(.*) /\\1\n"
                    "      ^/va-mcp$ /\n"
                ),
            )
            code, stderr = _run(directory)
            self.assertEqual(1, code)
            self.assertIn("the /storage mount rewrites", stderr)
            self.assertIn("^/storage$ /vst/storage", stderr)
            # The mounts that *are* paired must not be blamed.
            self.assertNotIn("the /vios mount rewrites", stderr)
            self.assertNotIn("the /va-mcp mount rewrites", stderr)

    def test_a_root_rule_pointing_elsewhere_is_reported(self):
        """Presence is not enough: both halves must land in the same place."""
        with tempfile.TemporaryDirectory() as directory:
            _manifest(
                directory,
                rewrites=(
                    "      ^/vios/(.*) /vst/\\1\n"
                    "      ^/vios$ /\n"
                    "      ^/storage/(.*) /vst/storage/\\1\n"
                    "      ^/storage$ /vst/storage\n"
                    "      ^/va-mcp/(.*) /\\1\n"
                    "      ^/va-mcp$ /\n"
                ),
            )
            code, stderr = _run(directory)
            self.assertEqual(1, code)
            self.assertIn("would send the bare root and its subpaths", stderr)

    def test_rules_wrapped_in_a_conditional_are_still_paired(self):
        """The warehouse charts gate individual rules on `{{- if $x }}`.

        Those lines must not end the annotation block, or every conditional
        mount would go unchecked and the lint would pass the very file it was
        written for.
        """
        with tempfile.TemporaryDirectory() as directory:
            _manifest(
                directory,
                rewrites=(
                    "{{- if $analyticsEnabled }}\n"
                    "      ^/va-mcp/(.*) /\\1\n"
                    "{{- end }}\n"
                    "      ^/vios/(.*) /vst/\\1\n"
                    "      ^/vios$ /vst\n"
                    "      ^/storage/(.*) /vst/storage/\\1\n"
                    "      ^/storage$ /vst/storage\n"
                ),
            )
            code, stderr = _run(directory)
            self.assertEqual(1, code)
            self.assertIn("the /va-mcp mount rewrites", stderr)


class CanonicalSpellingTest(unittest.TestCase):
    def test_a_trailing_slash_on_a_canonical_mount_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            _manifest(
                directory,
                paths=(
                    "          - path: /vios/\n"
                    "            pathType: Prefix\n"
                    "          - path: /storage\n"
                    "            pathType: Prefix\n"
                    "          - path: /va-mcp\n"
                    "            pathType: Prefix\n"
                ),
            )
            code, stderr = _run(directory)
            self.assertEqual(1, code)
            self.assertIn("path '/vios/' diverges from the canonical route table", stderr)

    def test_a_mount_absent_from_the_table_is_not_policed(self):
        """/kibana is warehouse-local; the table has no opinion on its spelling."""
        with tempfile.TemporaryDirectory() as directory:
            _manifest(
                directory,
                paths=GOOD_PATHS + "          - path: /kibana/\n            pathType: Prefix\n",
            )
            code, stderr = _run(directory)
            self.assertEqual(0, code, stderr)


class VacuityTest(unittest.TestCase):
    """A lint that matches nothing passes forever, so silence has to fail."""

    def test_a_helm_root_with_no_ingress_manifests_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            _manifest(directory).unlink()
            code, stderr = _run(directory)
            self.assertEqual(1, code)
            self.assertIn("not checking anything", stderr)

    def test_an_unreadable_route_table_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            _manifest(directory)
            table = Path(directory) / "services/common/templates/_ingress-routes.tpl"
            table.write_text("{{- define \"vss.ingress.routeTable\" -}}\n{{- end -}}\n")
            code, stderr = _run(directory)
            self.assertEqual(1, code)
            self.assertIn("no canonical route rows found", stderr)


class RepositoryTest(unittest.TestCase):
    def test_the_charts_in_this_repository_pass(self):
        """The guard has to hold against the real manifests, not just fixtures."""
        stderr = io.StringIO()
        with redirect_stderr(stderr), redirect_stdout(io.StringIO()):
            code = LINT.main([])
        self.assertEqual(0, code, stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
