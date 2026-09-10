#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Prove the absent-backend lint fails on the combinations it exists to catch.

A guard on this branch has twice certified the exact broken configuration it
was written to reject, so every rule in ``check_gateway_optional_backends.py``
is exercised here from the broken side as well as the clean one -- including
the original defect: a route moved behind the gateway with no marker at all.
"""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("check_gateway_optional_backends.py")
SPEC = importlib.util.spec_from_file_location("check_gateway_optional_backends", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
LINT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LINT)

HEADER = "x-vss-gateway-unavailable"

ROUTE = """    acl p_rtvi_cv path /rtvi-cv
    acl p_rtvi_cv path_beg /rtvi-cv/
    use_backend bk_rtvi_cv_strip if h_main p_rtvi_cv
"""
MARKER = "    http-request set-var(txn.gw_absent) str(rtvi-cv) if h_main p_rtvi_cv { nbsrv(bk_rtvi_cv_strip) eq 0 }\n"
RETURN = (
    f'    http-request return status 503 hdr {HEADER} "%[var(txn.gw_absent)]" '
    'content-type text/plain lf-string "absent" if h_main gw_absent\n'
)
DELETE = f"    http-response del-header {HEADER}\n"


def _helper(directory: str, *, header: str = HEADER) -> Path:
    path = Path(directory) / "gateway.py"
    path.write_text(f'"""doc"""\n\nGATEWAY_UNAVAILABLE_HEADER = "{header}"\n')
    return path


def _template(
    directory: str,
    *,
    route: str = ROUTE,
    marker: str = MARKER,
    ret: str = RETURN,
    delete: str = DELETE,
    extra: str = "",
) -> Path:
    path = Path(directory) / "haproxy.cfg.template"
    path.write_text(
        "backend bk_rtvi_cv_strip\n"
        "    server s1 vss-rtvi-cv:9000 check\n"
        "\n"
        "frontend fe_http\n"
        '    bind "${HAPROXY_BIND_ADDR}:${HAPROXY_PORT}"\n'
        "    acl h_main hdr(host) -i localhost\n"
        + route
        + extra
        + marker
        + "    acl gw_absent var(txn.gw_absent) -m found\n"
        + ret
        + delete
    )
    return path


class CleanTemplateTest(unittest.TestCase):
    def test_a_marked_route_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            failures, marked = LINT.scan_template(_template(directory), _helper(directory))
            self.assertEqual([], failures)
            self.assertEqual({"rtvi-cv"}, marked)


class TheRealTreeTest(unittest.TestCase):
    def test_the_shipped_template_passes(self) -> None:
        failures, marked = LINT.scan_template(LINT.TEMPLATE)
        self.assertEqual([], failures)
        self.assertEqual([], LINT.scan_readme(LINT.README, marked))

    def test_the_shipped_template_is_actually_in_scope(self) -> None:
        # A lint that silently matches nothing passes forever. These are the
        # optional services PR #1983 moved behind the edge; rtvi-cv is the one
        # whose absence was observed to hard-fail an upload.
        _, marked = LINT.scan_template(LINT.TEMPLATE)
        self.assertLessEqual(
            {"rtvi-cv", "rtvi-embed", "rtvi-vlm", "lvs", "alert-bridge", "va-mcp", "phoenix", "elasticsearch"},
            marked,
        )

    def test_the_agent_and_the_gateway_agree_on_the_header(self) -> None:
        self.assertEqual(HEADER, LINT.helper_header(LINT.AGENT_HELPER))


class MissingMarkerTest(unittest.TestCase):
    """The original defect: a route behind the gateway with no marker."""

    def test_an_unmarked_route_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            failures, _ = LINT.scan_template(
                _template(directory, marker=""),
                _helper(directory),
            )
            self.assertTrue(failures)
            self.assertIn("this lint is not checking anything", " ".join(failures))

    def test_a_second_route_added_without_a_marker_fails(self) -> None:
        extra = (
            "    acl p_rtvi_vlm path /rtvi-vlm\n"
            "    acl p_rtvi_vlm path_beg /rtvi-vlm/\n"
            "    use_backend bk_rtvi_vlm_strip if h_main p_rtvi_vlm\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            failures, _ = LINT.scan_template(_template(directory, extra=extra), _helper(directory))
            self.assertEqual(1, len(failures), failures)
            self.assertIn("bk_rtvi_vlm_strip", failures[0])
            self.assertIn("no absent-backend marker", failures[0])

    def test_naming_the_backend_exempt_is_how_you_opt_out(self) -> None:
        extra = (
            "    acl p_rtvi_vlm path /rtvi-vlm\n"
            "    use_backend bk_rtvi_vlm_strip if h_main p_rtvi_vlm\n"
        )
        original = dict(LINT.UNMARKED_BACKENDS)
        try:
            LINT.UNMARKED_BACKENDS["bk_rtvi_vlm_strip"] = "test"
            with tempfile.TemporaryDirectory() as directory:
                failures, _ = LINT.scan_template(_template(directory, extra=extra), _helper(directory))
                self.assertEqual([], failures)
        finally:
            LINT.UNMARKED_BACKENDS.clear()
            LINT.UNMARKED_BACKENDS.update(original)


class WrongBackendTest(unittest.TestCase):
    """`nbsrv()` takes a literal name, so a copy-paste is silent otherwise."""

    def test_a_marker_naming_the_neighbours_backend_fails(self) -> None:
        extra = (
            "    acl p_rtvi_vlm path /rtvi-vlm\n"
            "    use_backend bk_rtvi_vlm_strip if h_main p_rtvi_vlm\n"
            "    http-request set-var(txn.gw_absent) str(rtvi-vlm) if h_main p_rtvi_vlm "
            "{ nbsrv(bk_rtvi_cv_strip) eq 0 }\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            failures, _ = LINT.scan_template(_template(directory, extra=extra), _helper(directory))
            joined = " ".join(failures)
            self.assertIn("checks nbsrv(bk_rtvi_cv_strip)", joined)
            self.assertIn("report the wrong service absent", joined)

    def test_a_marker_for_a_route_that_does_not_exist_fails(self) -> None:
        stale = "    http-request set-var(txn.gw_absent) str(gone) if h_main p_gone { nbsrv(bk_gone) eq 0 }\n"
        with tempfile.TemporaryDirectory() as directory:
            failures, _ = LINT.scan_template(
                _template(directory, marker=MARKER + stale),
                _helper(directory),
            )
            self.assertIn("can never fire", " ".join(failures))

    def test_a_marker_reporting_the_wrong_mount_name_fails(self) -> None:
        wrong = "    http-request set-var(txn.gw_absent) str(rtvi-vlm) if h_main p_rtvi_cv { nbsrv(bk_rtvi_cv_strip) eq 0 }\n"
        with tempfile.TemporaryDirectory() as directory:
            failures, _ = LINT.scan_template(_template(directory, marker=wrong), _helper(directory))
            self.assertIn("would name the wrong service", " ".join(failures))


class HeaderContractTest(unittest.TestCase):
    def test_a_renamed_header_on_the_gateway_side_fails(self) -> None:
        renamed = RETURN.replace(HEADER, "x-vss-gateway-down")
        with tempfile.TemporaryDirectory() as directory:
            failures, _ = LINT.scan_template(
                _template(directory, ret=renamed, delete="    http-response del-header x-vss-gateway-down\n"),
                _helper(directory),
            )
            joined = " ".join(failures)
            self.assertIn("the tolerance would never fire", joined)

    def test_a_renamed_header_on_the_agent_side_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            failures, _ = LINT.scan_template(
                _template(directory),
                _helper(directory, header="x-vss-absent"),
            )
            self.assertIn("the tolerance would never fire", " ".join(failures))

    def test_a_missing_agent_constant_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "gateway.py"
            missing.write_text('"""no constant here"""\n')
            failures, _ = LINT.scan_template(_template(directory), missing)
            self.assertIn("GATEWAY_UNAVAILABLE_HEADER is not defined", " ".join(failures))

    def test_a_missing_synthesised_reply_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            failures, _ = LINT.scan_template(_template(directory, ret=""), _helper(directory))
            self.assertIn("expected exactly one", " ".join(failures))

    def test_a_missing_forgery_strip_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            failures, _ = LINT.scan_template(_template(directory, delete=""), _helper(directory))
            self.assertIn("claiming to be absent", " ".join(failures))


class ReadmeTest(unittest.TestCase):
    def test_a_missing_section_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            readme = Path(directory) / "README.md"
            readme.write_text("# Deploy\n\nnothing relevant\n")
            failures = LINT.scan_readme(readme, {"rtvi-cv"})
            self.assertIn("is missing", " ".join(failures))

    def test_an_undocumented_marked_route_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            readme = Path(directory) / "README.md"
            readme.write_text(
                f"# Deploy\n\n{LINT.README_SECTION}\n\n"
                f"`{HEADER}` marks an absent backend. Marked: /rtvi-cv.\n"
            )
            self.assertEqual([], LINT.scan_readme(readme, {"rtvi-cv"}))
            failures = LINT.scan_readme(readme, {"rtvi-cv", "rtvi-vlm"})
            self.assertIn("does not list it", " ".join(failures))


if __name__ == "__main__":
    unittest.main(verbosity=2)
