#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regression tests for relative redirects in the Helm VIOS nginx ingress."""
from __future__ import annotations

import re
import shutil
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HELM_ROOT = REPO_ROOT / "deploy/helm"
VIOS_CHART = HELM_ROOT / "services/vios"
NGINX_TEMPLATE = VIOS_CHART / "charts/vios-ingress/configs/nginx-vst.conf.template"
# The chart directory is vios-ingress, the chart *name* is vss-vios-ingress, and
# --show-only addresses the subchart by name.
CONFIGMAP = "charts/vss-vios-ingress/templates/configmap.yaml"

# Every profile that ships the VIOS umbrella, and therefore this ConfigMap.
# test_profiles_shipping_the_chart_are_all_enumerated keeps the list honest.
PROFILES = [
    "deploy/helm/developer-profiles/dev-profile-base",
    "deploy/helm/developer-profiles/dev-profile-lvs",
    "deploy/helm/developer-profiles/dev-profile-alerts",
    "deploy/helm/developer-profiles/dev-profile-search",
    "deploy/helm/industry-profiles/warehouse-operations/warehouse-2d-app",
    "deploy/helm/industry-profiles/warehouse-operations/warehouse-3d-app",
    "deploy/helm/industry-profiles/warehouse-operations/warehouse-mv3dt-app",
]

ACTION = re.compile(r"\{\{-?.*?-?\}\}", re.DOTALL)
DEPENDENCY = re.compile(
    r"-\s+name:\s*(?P<name>\S+)\s*\n"
    r"\s+version:\s*\S+\s*\n"
    r'\s+repository:\s*"?(?P<repository>[^"\s]+)"?'
)
ABSOLUTE_REDIRECT = re.compile(r"^absolute_redirect\s+(?P<value>\S+?)\s*;$")
VST_REDIRECT = "return 301 /vst/;"


def render_locally(template: Path) -> str:
    """Render the nginx template the way the ConfigMap does, minus Helm.

    The directives this module asserts on are literals in the template, not
    values, so dropping every Go action reproduces the nginx structure helm
    emits. That matters because no CI job installs helm -- the compose-golden
    job runs these scripts on a bare runner -- and a test that could only run
    where helm happens to exist would guard nothing on a pull request.
    ``test_helm_renders_a_config_that_matches_the_local_render`` ties this back
    to real helm output wherever the binary is available.
    """
    return ACTION.sub("", template.read_text())


def configmap_nginx_conf(manifest: str) -> str:
    """Return the nginx.conf block literal out of a rendered ConfigMap."""
    lines = manifest.splitlines()
    for index, line in enumerate(lines):
        if line.strip() != "nginx.conf: |":
            continue
        indent = len(line) - len(line.lstrip()) + 2
        body = []
        for candidate in lines[index + 1 :]:
            if candidate.strip() and len(candidate) - len(candidate.lstrip()) < indent:
                break
            body.append(candidate[indent:])
        return "\n".join(body)
    raise AssertionError("rendered ConfigMap has no nginx.conf key")


def directives(config: str) -> list[tuple[tuple[str, ...], str]]:
    """Pair every nginx directive with the blocks that enclose it.

    A substring check cannot answer the question this module asks. nginx
    resolves ``absolute_redirect`` per scope, inheriting it from the nearest
    enclosing block, so the directive dropped into some unrelated ``location``
    would read as present and still leave the ``/vst`` redirect absolute. Only
    the nesting distinguishes the two, so the nesting is what gets recorded.
    """
    found: list[tuple[tuple[str, ...], str]] = []
    block: list[str] = []
    for raw in config.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith("{"):
            block.append(line[:-1].strip())
        elif line == "}":
            block.pop()
        else:
            found.append((tuple(block), line))
    return found


def governing_absolute_redirect(config: str, scope: tuple[str, ...]) -> str | None:
    """Return the ``absolute_redirect`` value nginx applies inside ``scope``.

    Nearest enclosing block wins, so of the directives whose block path is a
    prefix of ``scope`` the deepest one is the effective setting. Returning the
    *value* rather than a boolean is what lets the tests below fail on ``on``
    instead of merely on absence.
    """
    governing = [
        (block, match.group("value"))
        for block, line in directives(config)
        if (match := ABSOLUTE_REDIRECT.fullmatch(line))
        and scope[: len(block)] == block
    ]
    if not governing:
        return None
    return max(governing, key=lambda item: len(item[0]))[1]


def vst_redirect_scope(config: str) -> tuple[str, ...]:
    """Return the block path of the `location = /vst` 301, proving it parsed."""
    for block, line in directives(config):
        if line == VST_REDIRECT:
            return block
    raise AssertionError(f"nginx config emits no {VST_REDIRECT!r}")


class HelmViosNginxRelativeRedirectTest(unittest.TestCase):
    def test_vst_redirect_is_nested_under_http_and_a_listening_server(self):
        """Sanity-check the parse before asserting anything about scopes."""
        scope = vst_redirect_scope(render_locally(NGINX_TEMPLATE))
        self.assertEqual("http", scope[0])
        self.assertEqual("location = /vst", scope[-1])
        self.assertIn("server", scope)

    def test_rendered_config_forbids_nginx_absolutising_the_vst_redirect(self):
        config = render_locally(NGINX_TEMPLATE)
        scope = vst_redirect_scope(config)
        self.assertEqual(
            "off",
            governing_absolute_redirect(config, scope),
            "the /vst redirect is not covered by `absolute_redirect off;` in its "
            "enclosing http {} or server {}, so nginx will rewrite Location into "
            f"scheme://host:30888/vst/ and leave the gateway origin ({NGINX_TEMPLATE})",
        )

    def test_profiles_shipping_the_chart_are_all_enumerated(self):
        """Every profile depending on the VIOS umbrella is in PROFILES.

        The directive has to hold for all of them, and a new profile that
        picked up the chart without being listed here would otherwise inherit
        the fix silently or lose it silently.
        """
        shipping = []
        for root in ("developer-profiles", "industry-profiles"):
            for chart in (HELM_ROOT / root).rglob("Chart.yaml"):
                if "/charts/" in str(chart):
                    continue
                for match in DEPENDENCY.finditer(chart.read_text()):
                    if match.group("name") == "vios":
                        shipping.append(chart.parent)
        self.assertEqual(
            sorted(REPO_ROOT / profile for profile in PROFILES),
            sorted(shipping),
        )

    def test_every_profile_resolves_to_this_one_nginx_template(self):
        """All profiles share the template, so one directive covers them all.

        This is what makes it sound to assert on the template once rather than
        rendering seven charts: the dependencies are file:// references to the
        same tree, not vendored copies that could drift apart.
        """
        for profile in PROFILES:
            with self.subTest(profile=profile):
                chart = REPO_ROOT / profile
                repository = next(
                    match.group("repository")
                    for match in DEPENDENCY.finditer((chart / "Chart.yaml").read_text())
                    if match.group("name") == "vios"
                )
                self.assertTrue(repository.startswith("file://"))
                resolved = (chart / repository[len("file://") :]).resolve()
                self.assertEqual(VIOS_CHART.resolve(), resolved)

    def test_the_scope_model_rejects_a_directive_that_governs_nothing(self):
        """The nesting is load-bearing, and this test can tell.

        The first config is the shape a substring check would accept: the
        directive is in the file, but in a sibling location, so nginx still
        absolutises the /vst Location. If governing_absolute_redirect ever
        drifts back to a flat search this case stops failing and the assertion
        above loses its teeth. The remaining two pin the values apart, because
        a check that only looked for absence would pass an explicit `on`.
        """
        scope = ("http", "server", "location = /vst")
        sibling = (
            "http {\n"
            "    server {\n"
            "        location /vst/assets/ {\n"
            "            absolute_redirect off;\n"
            "        }\n"
            "        location = /vst {\n"
            f"            {VST_REDIRECT}\n"
            "        }\n"
            "    }\n"
            "}\n"
        )
        self.assertIsNone(governing_absolute_redirect(sibling, scope))
        enclosing = (
            "http {\n"
            "    absolute_redirect off;\n"
            "    server {\n"
            "        location = /vst {\n"
            f"            {VST_REDIRECT}\n"
            "        }\n"
            "    }\n"
            "}\n"
        )
        self.assertEqual("off", governing_absolute_redirect(enclosing, scope))
        # A nearer `on` overrides the http-level default, so the deepest match
        # -- not the first -- has to be the one reported.
        overridden = enclosing.replace(
            "    server {", "    server {\n        absolute_redirect on;"
        )
        self.assertEqual("on", governing_absolute_redirect(overridden, scope))

    def test_helm_renders_a_config_that_matches_the_local_render(self):
        """Confirm the local render against real helm where helm exists.

        Rendering the VIOS umbrella directly needs no `helm dependency build`:
        its subcharts are in-tree directories. The profiles vendor .tgz
        artifacts that are gitignored and absent on a fresh checkout, which is
        the other reason the assertions above read the shared template instead.
        """
        helm = shutil.which("helm")
        if helm is None:
            self.skipTest("helm is not installed; the local render still applies")
        manifest = subprocess.run(
            [helm, "template", "vss", str(VIOS_CHART), "--show-only", CONFIGMAP],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        config = configmap_nginx_conf(manifest)
        scope = vst_redirect_scope(config)
        self.assertEqual(
            ("http", "server", "location = /vst"),
            tuple(part for part in scope if not part.startswith("if ")),
        )
        self.assertEqual("off", governing_absolute_redirect(config, scope))

    def test_helm_ingress_keeps_the_guarantee_the_other_configs_already_make(self):
        """Parity: no VIOS nginx config may emit an absolute /vst Location.

        The HAProxy gateway rewrites this redirect for its /vios alias and says
        so in deploy/docker/services/infra/haproxy/haproxy.cfg.template -- it
        matches on `^/vst(/.*)?$`, which an absolutised Location does not. The
        compose configs hold that up already; the Helm one is on the same
        contract and this is the test that says so for all of them at once.
        """
        configs = sorted(
            path
            for pattern in ("*.conf", "*.conf.template")
            for path in REPO_ROOT.rglob(pattern)
            if VST_REDIRECT in path.read_text()
        )
        self.assertIn(NGINX_TEMPLATE, configs)
        for path in configs:
            with self.subTest(config=path.relative_to(REPO_ROOT)):
                config = render_locally(path)
                scope = vst_redirect_scope(config)
                self.assertEqual("off", governing_absolute_redirect(config, scope))


if __name__ == "__main__":
    unittest.main(verbosity=2)
