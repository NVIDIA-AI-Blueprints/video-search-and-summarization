#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("check_gateway_host_acls.py")
SPEC = importlib.util.spec_from_file_location("check_gateway_host_acls", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
LINT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LINT)

ORIGINS = (
    ("${VSS_PUBLIC_HOST}", "${VSS_PUBLIC_HOST}:${VSS_PUBLIC_PORT}"),
    ("${HOST_IP}", "${HOST_IP}:${HAPROXY_PORT}"),
)


def _acls(name: str, pairs: tuple[tuple[str, str], ...]) -> str:
    lines = []
    for bare, ported in pairs:
        lines.append(f'    acl {name} hdr(host) -i "{bare}"')
        lines.append(f'    acl {name} hdr(host) -i "{ported}"')
    return "\n".join(lines) + "\n"


def _template(
    directory: str,
    *,
    known: tuple[tuple[str, str], ...] = ORIGINS,
    main: tuple[tuple[str, str], ...] = ORIGINS,
    route: str = "    use_backend bk_vst_ingress if h_main p_vst\n",
) -> Path:
    path = Path(directory) / "haproxy.cfg.template"
    path.write_text(
        "backend bk_vst_ingress\n"
        "    server s1 vst-ingress:30888 check\n"
        "\n"
        "frontend fe_http\n"
        '    bind "${HAPROXY_BIND_ADDR}:${HAPROXY_PORT}"\n'
        + _acls("known_host", known)
        + "    http-request deny deny_status 404 hdr x-vss-gateway-deny unknown-host if !known_host\n"
        + _acls("h_main", main)
        + "    acl p_vst path_beg /vst/\n"
        + route
    )
    return path


def _readme(directory: str, *, names: tuple[str, ...] = ()) -> Path:
    path = Path(directory) / "README.md"
    body = "".join(f"- `{name}` is declared.\n" for name in names)
    path.write_text(
        "# Deploy\n\n"
        f"{LINT.README_SECTION}\n\n"
        "The Host ACLs are an allowlist.\n\n"
        f"{body}"
        "\n## Something else\n\nUnrelated prose mentioning EXTERNAL_IP.\n"
    )
    return path


ALL_NAMES = ("VSS_PUBLIC_HOST", "VSS_PUBLIC_PORT", "HOST_IP")


class TreeTest(unittest.TestCase):
    def test_the_tree_passes(self) -> None:
        failures, used = LINT.scan_template(LINT.TEMPLATE)
        self.assertEqual([], failures)
        self.assertEqual([], LINT.scan_readme(LINT.README, used))

    def test_the_real_template_is_actually_in_scope(self) -> None:
        # A lint that silently matches nothing passes forever. Assert the real
        # allowlists were found and that they name the variables this PR's
        # reviewers asked about.
        _, used = LINT.scan_template(LINT.TEMPLATE)
        self.assertLessEqual({"VSS_PUBLIC_HOST", "VSS_GATEWAY_HOST", "HOST_IP", "EXTERNAL_IP"}, used)


class DivergenceTest(unittest.TestCase):
    def test_a_clean_pair_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            failures, used = LINT.scan_template(_template(directory))
            self.assertEqual([], failures)
            self.assertEqual({"VSS_PUBLIC_HOST", "VSS_PUBLIC_PORT", "HOST_IP", "HAPROXY_PORT"}, used)

    def test_an_origin_missing_from_h_main_is_caught(self) -> None:
        # The live failure this guards: admitted by known_host, matched by no
        # use_backend, answered 503 with no x-vss-gateway-deny header.
        with tempfile.TemporaryDirectory() as directory:
            path = _template(directory, main=ORIGINS[1:])
            failures, _ = LINT.scan_template(path)
            self.assertTrue(any("not for h_main" in failure for failure in failures), failures)
            self.assertTrue(any("503" in failure for failure in failures), failures)

    def test_an_origin_missing_from_known_host_is_caught(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = _template(directory, known=ORIGINS[1:])
            failures, _ = LINT.scan_template(path)
            self.assertTrue(any("not for known_host" in failure for failure in failures), failures)

    def test_an_origin_with_no_ported_form_is_caught(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = _template(directory, known=(("${VSS_PUBLIC_HOST}", "${HOST_IP}:${HAPROXY_PORT}"),))
            failures, _ = LINT.scan_template(path)
            self.assertTrue(any("without a ported form" in failure for failure in failures), failures)

    def test_an_ungated_route_is_caught(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = _template(directory, route="    use_backend bk_vst_ingress if p_vst\n")
            failures, _ = LINT.scan_template(path)
            self.assertTrue(any("not gated on h_main" in failure for failure in failures), failures)

    def test_a_backend_section_route_is_not_mistaken_for_a_frontend_one(self) -> None:
        # use_backend only appears in a frontend, but the scanner must not start
        # reporting every line of an unrelated section if that ever changes.
        with tempfile.TemporaryDirectory() as directory:
            path = _template(directory)
            path.write_text(path.read_text() + "\nbackend bk_other\n    use_backend nope\n")
            failures, _ = LINT.scan_template(path)
            self.assertEqual([], failures)

    def test_unrecognised_allowlists_fail_loudly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "haproxy.cfg.template"
            path.write_text("frontend fe_http\n    bind *:7777\n")
            failures, used = LINT.scan_template(path)
            self.assertEqual(set(), used)
            self.assertTrue(any("not checking anything" in failure for failure in failures), failures)


class ReadmeTest(unittest.TestCase):
    def test_a_documented_allowlist_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = _readme(directory, names=ALL_NAMES)
            self.assertEqual([], LINT.scan_readme(path, set(ALL_NAMES) | {"HAPROXY_PORT"}))

    def test_an_undocumented_origin_variable_is_caught(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = _readme(directory, names=ALL_NAMES)
            failures = LINT.scan_readme(path, {"EXTERNAL_IP"})
            self.assertTrue(any("EXTERNAL_IP" in failure for failure in failures), failures)

    def test_prose_outside_the_section_does_not_count(self) -> None:
        # EXTERNAL_IP appears in the README body but not in the allowlist
        # section, which is the only place an operator would look for it.
        with tempfile.TemporaryDirectory() as directory:
            path = _readme(directory, names=ALL_NAMES)
            self.assertIn("EXTERNAL_IP", path.read_text())
            self.assertNotEqual([], LINT.scan_readme(path, {"EXTERNAL_IP"}))

    def test_a_missing_section_is_caught(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "README.md"
            path.write_text("# Deploy\n\nNo allowlist section here.\n")
            failures = LINT.scan_readme(path, {"HOST_IP"})
            self.assertTrue(any("is missing" in failure for failure in failures), failures)


if __name__ == "__main__":
    unittest.main(verbosity=2)
