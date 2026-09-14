#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Prove the exposure lint fails on the shapes it exists to catch.

A guard on this branch has more than once certified the exact configuration it
was written to reject -- an ingress verifier reading stale vendored charts, an
endpoint lint that skipped a missing host -- so every rule in
``check_gateway_route_exposure.py`` is exercised here from the broken side as
well as the clean one.

The cases that matter most are the two that make an internal-only tier a claim
rather than a label:

  * a route classified internal-only but not gated on both ACLs, which is the
    original FR-29 gap: no tier and no internal tier at all;
  * ``h_internal`` widened to a published origin, which leaves every rule in
    this lint passing while making every internal-only route reachable from
    outside. That one is silent by construction, which is why it is tested.

Also exercised: marker inheritance. An unmarked route must not pick up its
neighbour's tier -- that defect was live on the first run of this rule, where
the UI catch-all came out ``remote-agent-accessible`` because it sat below
``/va-mcp``.
"""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("check_gateway_route_exposure.py")
SPEC = importlib.util.spec_from_file_location("check_gateway_route_exposure", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
LINT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LINT)

DENY_HOST = (
    "    http-request deny deny_status 403 hdr x-vss-gateway-deny internal-only "
    'string "no" if h_main p_sdr !h_internal\n'
)
DENY_SRC = (
    "    http-request deny deny_status 403 hdr x-vss-gateway-deny internal-only "
    'string "no" if h_main p_sdr !gw_internal_src\n'
)

INTERNAL_ACLS = (
    "    acl gw_internal_src src 127.0.0.0/8\n"
    "    acl gw_internal_src src 172.16.0.0/12\n"
    "    acl h_internal hdr(host) -i vss-haproxy-ingress\n"
    '    acl h_internal hdr(host) -i "${HAPROXY_SERVICE_HOST}"\n'
)


def _table(directory: str, rows: str) -> Path:
    """A minimal canonical route table with the given rows."""
    path = Path(directory) / "_ingress-routes.tpl"
    path.write_text(
        '{{- define "vss.ingress.routeTable" -}}\n' + rows + "{{- end -}}\n"
    )
    return path


CLEAN_ROWS = (
    "- key: lvs\n  path: /lvs\n  pathType: Prefix\n  rewrite: strip\n"
    "  exposure: remote-agent-accessible\n"
    "- key: perception-sdr\n  path: /perception-sdr\n  pathType: Prefix\n  rewrite: none\n"
    "  exposure: internal-only\n"
)


def _template(
    directory: str,
    *,
    sdr_conds: str = "h_main p_sdr h_internal gw_internal_src",
    sdr_marker: str = "    # exposure: internal-only\n",
    lvs_marker: str = "    # exposure: remote-agent-accessible\n",
    denies: str = DENY_HOST + DENY_SRC,
    internal_acls: str = INTERNAL_ACLS,
) -> Path:
    path = Path(directory) / "haproxy.cfg.template"
    path.write_text(
        "backend bk_lvs_strip\n"
        "    server s1 lvs-server:38111 check\n"
        "\n"
        "backend bk_perception_sdr\n"
        "    server s1 vss-rtvi-cv-sdr:4001 check\n"
        "\n"
        "frontend fe_http\n"
        '    bind "${HAPROXY_BIND_ADDR}:${HAPROXY_PORT}"\n'
        '    acl h_main hdr(host) -i "${VSS_PUBLIC_HOST}"\n'
        + internal_acls
        + lvs_marker
        + "    acl p_lvs path /lvs\n"
        "    acl p_lvs path_beg /lvs/\n"
        "    use_backend bk_lvs_strip if h_main p_lvs\n"
        + sdr_marker
        + "    acl p_sdr path /perception-sdr\n"
        "    acl p_sdr path_beg /perception-sdr/\n"
        + denies
        + f"    use_backend bk_perception_sdr if {sdr_conds}\n"
    )
    return path


def _readme(directory: str, *, section: str = LINT.README_SECTION) -> Path:
    path = Path(directory) / "README.md"
    path.write_text(
        f"# doc\n\n{section}\n\n"
        "Tiers: internal-only, remote-agent-accessible, public-user-accessible.\n"
        "Internal: /perception-sdr and /behavior-analytics.\n"
    )
    return path


def _run(directory: str, **kwargs) -> tuple[list[str], dict]:
    rows = kwargs.pop("rows", CLEAN_ROWS)
    failures, classified = LINT.scan(_table(directory, rows), _template(directory, **kwargs))
    if classified:
        failures += LINT.scan_readme(_readme(directory), classified)
    return failures, classified


class ExposureLint(unittest.TestCase):
    def test_clean_configuration_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            failures, classified = _run(directory)
            self.assertEqual(failures, [])
            self.assertEqual(classified["internal"], ["/perception-sdr"])

    def test_row_without_exposure_fails(self) -> None:
        """The original FR-29 gap: a route carrying no classification at all."""
        rows = CLEAN_ROWS.replace("  exposure: remote-agent-accessible\n", "", 1)
        with tempfile.TemporaryDirectory() as directory:
            failures, _ = _run(directory, rows=rows)
            self.assertTrue(
                any("/lvs has no `exposure:` field" in f for f in failures), failures
            )

    def test_unknown_tier_fails(self) -> None:
        rows = CLEAN_ROWS.replace("remote-agent-accessible", "sort-of-public", 1)
        with tempfile.TemporaryDirectory() as directory:
            failures, _ = _run(directory, rows=rows)
            self.assertTrue(any("not one of" in f for f in failures), failures)

    def test_docker_route_without_marker_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            failures, _ = _run(directory, lvs_marker="")
            self.assertTrue(
                any("has no `# exposure:` marker" in f for f in failures), failures
            )

    def test_marker_is_not_inherited_by_the_next_route(self) -> None:
        """An unmarked route must not read as classified because a neighbour is."""
        with tempfile.TemporaryDirectory() as directory:
            failures, _ = _run(directory, sdr_marker="")
            self.assertTrue(
                any(
                    "bk_perception_sdr has no `# exposure:` marker" in f
                    for f in failures
                ),
                failures,
            )

    def test_edges_disagreeing_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            failures, _ = _run(
                directory, lvs_marker="    # exposure: public-user-accessible\n"
            )
            self.assertTrue(
                any("on the Docker edge but" in f for f in failures), failures
            )

    def test_internal_only_route_not_gated_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            failures, _ = _run(directory, sdr_conds="h_main p_sdr")
            for acl in ("h_internal", "gw_internal_src"):
                self.assertTrue(
                    any(f"is not gated on {acl}" in f for f in failures), failures
                )

    def test_internal_only_route_gated_on_only_the_host_fails(self) -> None:
        """Host alone is client-controlled, so it is not a boundary by itself."""
        with tempfile.TemporaryDirectory() as directory:
            failures, _ = _run(directory, sdr_conds="h_main p_sdr h_internal")
            self.assertTrue(
                any("is not gated on gw_internal_src" in f for f in failures), failures
            )

    def test_missing_deny_fails(self) -> None:
        """Without the deny the request falls through to the UI catch-all as 200."""
        with tempfile.TemporaryDirectory() as directory:
            failures, _ = _run(directory, denies=DENY_HOST)
            self.assertTrue(
                any("!gw_internal_src` covers it" in f for f in failures), failures
            )

    def test_internal_host_acl_naming_a_published_origin_fails(self) -> None:
        """The silent one: every internal-only route becomes publicly reachable."""
        for variable in LINT.PUBLIC_ORIGIN_VARS:
            widened = INTERNAL_ACLS + f'    acl h_internal hdr(host) -i "${{{variable}}}"\n'
            with tempfile.TemporaryDirectory() as directory:
                failures, _ = _run(directory, internal_acls=widened)
                self.assertTrue(
                    any(
                        f"reads {variable} -- an origin the deployment PUBLISHES" in f
                        for f in failures
                    ),
                    (variable, failures),
                )

    def test_missing_internal_acls_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            failures, _ = _run(directory, internal_acls="")
            self.assertTrue(
                any("no `acl h_internal hdr(host)` is declared" in f for f in failures),
                failures,
            )
            self.assertTrue(
                any("`acl gw_internal_src src ...` is not declared" in f for f in failures),
                failures,
            )

    def test_empty_public_mount_exception_fails(self) -> None:
        rows = CLEAN_ROWS.replace(
            "  exposure: internal-only\n",
            "  exposure: internal-only\n  publicMountException:\n",
            1,
        )
        with tempfile.TemporaryDirectory() as directory:
            failures, _ = _run(directory, rows=rows)
            self.assertTrue(
                any("empty `publicMountException`" in f for f in failures), failures
            )

    def test_unparseable_table_is_reported_rather_than_passing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            table = Path(directory) / "empty.tpl"
            table.write_text("nothing here\n")
            failures, _ = LINT.scan(table, _template(directory))
            self.assertTrue(
                any("not checking anything" in f for f in failures), failures
            )

    def test_readme_must_document_the_tiers_and_the_restricted_routes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, classified = _run(directory)
            missing = Path(directory) / "bare.md"
            missing.write_text("# doc\n\nnothing about exposure\n")
            self.assertTrue(LINT.scan_readme(missing, classified))

            partial = Path(directory) / "partial.md"
            partial.write_text(
                f"# doc\n\n{LINT.README_SECTION}\n\n"
                "Tiers: internal-only, remote-agent-accessible, public-user-accessible.\n"
            )
            failures = LINT.scan_readme(partial, classified)
            self.assertTrue(
                any("/perception-sdr" in f for f in failures), failures
            )


if __name__ == "__main__":
    unittest.main()
