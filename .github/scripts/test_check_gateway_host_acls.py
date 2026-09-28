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


def _gw_compose(directory: str, entries: str) -> Path:
    path = Path(directory) / "compose.yml"
    path.write_text("services:\n  vss-haproxy-ingress:\n    environment:\n" + entries)
    return path


def _profile(directory: str, body: str) -> Path:
    path = Path(directory) / "overrides.env"
    path.write_text(body)
    return path


class NonEmptyTest(unittest.TestCase):
    """An empty ACL variable stops the gateway from parsing its config at all.

    Confirmed against haproxy 3.4.2 with the real template: blanking HOST_IP or
    EXTERNAL_IP, or leaving either unset, aborts the parse with "argument number
    4 ... is empty and marks the end of the argument list".
    """

    def test_the_tree_guarantees_every_acl_variable(self) -> None:
        _, used = LINT.scan_template(LINT.TEMPLATE)
        self.assertEqual([], LINT.scan_non_empty(used, LINT.GATEWAY_COMPOSE, LINT.BASE_PROFILE))

    def test_the_load_bearing_placeholder_is_covered(self) -> None:
        # HOST_IP has no Compose default, so the profile placeholder is the only
        # thing keeping a never-configured deployment parseable. Assert the real
        # files still work that way, or this rule is checking the wrong thing.
        self.assertNotIn("HOST_IP", LINT.compose_defaults(LINT.GATEWAY_COMPOSE))
        self.assertTrue(
            LINT.resolves_non_empty("HOST_IP", LINT.env_assignments(LINT.BASE_PROFILE), frozenset())
        )

    def test_an_emptied_placeholder_is_caught_through_the_chain(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            compose = _gw_compose(directory, "      HOST_IP: ${HOST_IP}\n")
            profile = _profile(directory, 'HOST_IP=\nEXTERNAL_IP="${HOST_IP}"\n')
            failures = LINT.scan_non_empty({"HOST_IP", "EXTERNAL_IP"}, compose, profile)
            self.assertTrue(any("HOST_IP" in failure for failure in failures), failures)
            # The indirection must not launder an empty value into a pass.
            self.assertTrue(any("EXTERNAL_IP" in failure for failure in failures), failures)

    def test_a_missing_assignment_is_caught(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            compose = _gw_compose(directory, "      HOST_IP: ${HOST_IP}\n")
            profile = _profile(directory, "SOMETHING_ELSE=1\n")
            self.assertNotEqual([], LINT.scan_non_empty({"HOST_IP"}, compose, profile))

    def test_a_compose_default_alone_is_enough(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            compose = _gw_compose(directory, "      VSS_GATEWAY_HOST: ${VSS_GATEWAY_HOST:-vss.local}\n")
            profile = _profile(directory, "SOMETHING_ELSE=1\n")
            self.assertEqual([], LINT.scan_non_empty({"VSS_GATEWAY_HOST"}, compose, profile))

    def test_an_empty_compose_default_is_not_enough(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            compose = _gw_compose(directory, "      VSS_GATEWAY_HOST: ${VSS_GATEWAY_HOST:-}\n")
            profile = _profile(directory, "SOMETHING_ELSE=1\n")
            self.assertNotEqual([], LINT.scan_non_empty({"VSS_GATEWAY_HOST"}, compose, profile))

    def test_a_reference_to_an_undefined_variable_is_caught(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            compose = _gw_compose(directory, "      HOST_IP: ${HOST_IP}\n")
            profile = _profile(directory, 'EXTERNAL_IP="${NOWHERE}"\n')
            self.assertNotEqual([], LINT.scan_non_empty({"EXTERNAL_IP"}, compose, profile))

    def test_a_reference_cycle_does_not_hang(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            compose = _gw_compose(directory, "      HOST_IP: ${HOST_IP}\n")
            profile = _profile(directory, 'HOST_IP="${EXTERNAL_IP}"\nEXTERNAL_IP="${HOST_IP}"\n')
            self.assertNotEqual([], LINT.scan_non_empty({"HOST_IP"}, compose, profile))


ACL_VARS = {"HOST_IP", "EXTERNAL_IP", "VSS_PUBLIC_HOST", "VSS_PUBLIC_PORT"}
GATEWAY_ON = f"COMPOSE_PROFILES=redis,{LINT.GATEWAY_SERVICE},vss-ui\n"
# What a profile has to say for the ACL variables to resolve, indirection and
# all, exactly as the real profiles write it.
CONFIGURED = GATEWAY_ON + (
    "HOST_IP='<HOST_IP>'\n"
    'EXTERNAL_IP="${HOST_IP}"\n'
    "VSS_PUBLIC_HOST=${EXTERNAL_IP}\n"
    "VSS_PUBLIC_PORT=7777\n"
)


def _names(paths: list[str]) -> list[str]:
    """Trim the temp-directory prefix so assertions read on the layout.

    ``display`` only relativises paths inside the real repository, so a tree
    built under /tmp comes back absolute.
    """
    return [path[path.index("deploy/docker") :] for path in paths]


def _tree(directory: str, *, profile_env: str, overlay_env: str, overlay_deployable: bool) -> Path:
    """Build a miniature deploy/docker: one deployable profile, one overlay.

    Mirrors the real layout closely enough for the include graph to mean the
    same thing -- a root compose that includes a per-tree compose, which in turn
    includes each profile's own.
    """
    root = Path(directory) / "deploy" / "docker"
    (root / "developer-profiles" / "dev-profile-base").mkdir(parents=True)
    (root / "industry-profiles" / "overlay-profile").mkdir(parents=True)

    (root / "compose.yml").write_text(
        "include:\n"
        "  - path: ./developer-profiles/compose.yml\n"
        "  - path: ./industry-profiles/compose.yml\n"
    )
    (root / "developer-profiles" / "compose.yml").write_text(
        "include:\n  - path: ./dev-profile-base/compose.yml\n"
    )
    industry = "include:\n"
    if overlay_deployable:
        industry += "  - path: ./overlay-profile/compose.yml\n"
    (root / "industry-profiles" / "compose.yml").write_text(industry)

    for path, body in (
        (root / "developer-profiles" / "dev-profile-base", profile_env),
        (root / "industry-profiles" / "overlay-profile", overlay_env),
    ):
        (path / "compose.yml").write_text("services: {}\n")
        (path / ".env").write_text("")
        (path / "overrides.env").write_text(body)
    return root


class ProfileTest(unittest.TestCase):
    """The ACL variables must be guaranteed by the profile being deployed.

    ``scan_non_empty`` checks one profile. This rule checks every profile that
    can be brought up on its own, which is the claim that actually matters.
    """

    def test_the_tree_passes(self) -> None:
        _, used = LINT.scan_template(LINT.TEMPLATE)
        failures, _, _ = LINT.scan_profiles(used, LINT.GATEWAY_COMPOSE)
        self.assertEqual([], failures)

    def test_the_real_profiles_are_actually_in_scope(self) -> None:
        # Non-vacuity, and specific: warehouse-operations is the deployable
        # industry profile, and it was outside this lint's reach entirely until
        # this rule existed.
        _, used = LINT.scan_template(LINT.TEMPLATE)
        _, checked, _ = LINT.scan_profiles(used, LINT.GATEWAY_COMPOSE)
        self.assertIn("deploy/docker/industry-profiles/warehouse-operations", checked)
        for name in ("base", "alerts", "lvs", "search"):
            self.assertIn(f"deploy/docker/developer-profiles/dev-profile-{name}", checked)

    def test_smartcities_is_classified_as_an_overlay(self) -> None:
        # The false positive this rule was written to settle. smartcities
        # enables the gateway and defines none of the ACL variables, so read on
        # its own it looks fatal; it is deployed by merging into
        # dev-profile-alerts, whose values it inherits.
        _, used = LINT.scan_template(LINT.TEMPLATE)
        failures, checked, overlays = LINT.scan_profiles(used, LINT.GATEWAY_COMPOSE)
        self.assertIn("deploy/docker/industry-profiles/smartcities", overlays)
        self.assertNotIn("deploy/docker/industry-profiles/smartcities", checked)
        self.assertEqual([], failures)

    def test_the_smartcities_overlay_really_does_lack_the_variables(self) -> None:
        # If the overlay ever gains them, the test above stops proving anything
        # about the skip, so pin the premise rather than the conclusion.
        env = LINT.profile_env(LINT.DOCKER_ROOT / "industry-profiles/smartcities")
        for name in sorted(ACL_VARS):
            self.assertFalse(LINT.resolves_non_empty(name, env, frozenset()), name)

    def test_an_unconfigured_deployable_profile_is_caught(self) -> None:
        # The canary: a profile that can be brought up on its own, enables the
        # gateway, and never defines the ACL variables. haproxy 3.4.2 aborts the
        # parse on exactly this input.
        with tempfile.TemporaryDirectory() as directory:
            root = _tree(
                directory,
                profile_env=GATEWAY_ON,
                overlay_env=CONFIGURED,
                overlay_deployable=False,
            )
            failures, checked, _ = LINT.scan_profiles(ACL_VARS, _gw_compose(directory, ""), root)
            self.assertEqual(["deploy/docker/developer-profiles/dev-profile-base"], _names(checked))
            for name in sorted(ACL_VARS):
                self.assertTrue(any(name in failure for failure in failures), (name, failures))
            self.assertTrue(any("would not start at all" in f for f in failures), failures)

    def test_a_configured_deployable_profile_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = _tree(
                directory,
                profile_env=CONFIGURED,
                overlay_env=CONFIGURED,
                overlay_deployable=False,
            )
            failures, _, _ = LINT.scan_profiles(ACL_VARS, _gw_compose(directory, ""), root)
            self.assertEqual([], failures)

    def test_an_unconfigured_overlay_is_skipped_and_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = _tree(
                directory,
                profile_env=CONFIGURED,
                overlay_env=GATEWAY_ON,
                overlay_deployable=False,
            )
            failures, _, overlays = LINT.scan_profiles(ACL_VARS, _gw_compose(directory, ""), root)
            self.assertEqual([], failures)
            # Skipped, but named on stdout rather than passed over in silence.
            self.assertEqual(["deploy/docker/industry-profiles/overlay-profile"], _names(overlays))

    def test_an_overlay_that_becomes_deployable_is_caught(self) -> None:
        # The property that keeps the smartcities skip honest: the distinction
        # comes from the include graph, so wiring the overlay into a deployable
        # stack without giving it the ACL variables fails on that same commit.
        with tempfile.TemporaryDirectory() as directory:
            root = _tree(
                directory,
                profile_env=CONFIGURED,
                overlay_env=GATEWAY_ON,
                overlay_deployable=True,
            )
            failures, checked, overlays = LINT.scan_profiles(
                ACL_VARS, _gw_compose(directory, ""), root
            )
            self.assertEqual([], overlays)
            self.assertIn("deploy/docker/industry-profiles/overlay-profile", _names(checked))
            self.assertNotEqual([], failures)

    def test_a_compose_default_covers_a_profile_that_omits_the_variable(self) -> None:
        # VSS_GATEWAY_HOST is the real instance: no profile has to set it,
        # because the gateway service defaults it to vss.local.
        with tempfile.TemporaryDirectory() as directory:
            root = _tree(
                directory,
                profile_env=GATEWAY_ON,
                overlay_env=CONFIGURED,
                overlay_deployable=False,
            )
            compose = _gw_compose(directory, "      HOST_IP: ${HOST_IP:-127.0.0.1}\n")
            failures, _, _ = LINT.scan_profiles({"HOST_IP"}, compose, root)
            self.assertEqual([], failures)

    def test_a_profile_that_does_not_enable_the_gateway_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = _tree(
                directory,
                profile_env=CONFIGURED,
                overlay_env=CONFIGURED,
                overlay_deployable=False,
            )
            base = root / "developer-profiles" / "dev-profile-base"
            (base / "overrides.env").write_text("COMPOSE_PROFILES=redis,vss-ui\nHOST_IP=\n")
            # Nothing left that enables the gateway, so the rule reports that it
            # is checking nothing rather than passing quietly.
            failures, checked, _ = LINT.scan_profiles(ACL_VARS, _gw_compose(directory, ""), root)
            self.assertEqual([], checked)
            self.assertTrue(any("not checking anything" in f for f in failures), failures)

    def test_a_commented_out_gateway_does_not_count_as_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = _tree(
                directory,
                profile_env=CONFIGURED,
                overlay_env=CONFIGURED,
                overlay_deployable=False,
            )
            base = root / "developer-profiles" / "dev-profile-base"
            (base / "overrides.env").write_text(
                f"# COMPOSE_PROFILES=redis,{LINT.GATEWAY_SERVICE}\nCOMPOSE_PROFILES=redis\n"
            )
            self.assertFalse(LINT.enables_gateway(base))

    def test_a_substring_service_name_does_not_count_as_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = _tree(
                directory,
                profile_env=CONFIGURED,
                overlay_env=CONFIGURED,
                overlay_deployable=False,
            )
            base = root / "developer-profiles" / "dev-profile-base"
            (base / "overrides.env").write_text(
                f"COMPOSE_PROFILES=redis,{LINT.GATEWAY_SERVICE}-metrics\n"
            )
            self.assertFalse(LINT.enables_gateway(base))

    def test_the_overrides_file_is_resolved_against_the_profile_env(self) -> None:
        # The runbook passes .env then the overrides file, so an override may
        # legitimately reference something only .env sets.
        with tempfile.TemporaryDirectory() as directory:
            root = _tree(
                directory,
                profile_env=CONFIGURED,
                overlay_env=CONFIGURED,
                overlay_deployable=False,
            )
            base = root / "developer-profiles" / "dev-profile-base"
            (base / ".env").write_text("HOST_IP=10.0.0.1\n")
            (base / "overrides.env").write_text(GATEWAY_ON + 'EXTERNAL_IP="${HOST_IP}"\n')
            env = LINT.profile_env(base)
            self.assertEqual("10.0.0.1", env["HOST_IP"])
            self.assertTrue(LINT.resolves_non_empty("EXTERNAL_IP", env, frozenset()))

    def test_the_include_graph_is_followed_transitively(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = _tree(
                directory,
                profile_env=CONFIGURED,
                overlay_env=CONFIGURED,
                overlay_deployable=True,
            )
            reachable = LINT.include_graph(root / "compose.yml")
            for relative in (
                "developer-profiles/dev-profile-base/compose.yml",
                "industry-profiles/overlay-profile/compose.yml",
            ):
                self.assertIn((root / relative).resolve(), reachable)

    def test_an_include_cycle_does_not_hang(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.yml").write_text("include:\n  - path: ./b.yml\n")
            (root / "b.yml").write_text("include:\n  - path: ./a.yml\n")
            self.assertEqual(2, len(LINT.include_graph(root / "a.yml")))


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
