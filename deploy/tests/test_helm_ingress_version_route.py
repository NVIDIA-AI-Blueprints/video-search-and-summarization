# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The HAProxy ingress answers GET /api/v1/version on every ingress-owning chart.

Every chart that owns a templates/vss-ingress.yaml -- the four developer
profiles and the three warehouse apps -- renders one `http-request return`
into haproxy.org/frontend-config-snippet carrying its stamped
.Values.vssVersion (.github/version-convention.md). The rule is scoped to the
ingress host, and emitted only when there is one: a hostless rule would answer
for every host on a shared controller, i.e. report this release's version for
other deployments. A caller-supplied frontend snippet on a developer profile is
kept after the rule, never replaced.
"""

from __future__ import annotations

import shutil
import subprocess
import unittest
from functools import cache
from pathlib import Path

import yaml

HELM_DIR = Path(__file__).resolve().parents[1] / "helm"
DEV = ("dev-profile-base", "dev-profile-alerts", "dev-profile-lvs", "dev-profile-search")
WAREHOUSE = ("warehouse-2d-app", "warehouse-3d-app", "warehouse-mv3dt-app")
SNIPPET = "haproxy.org/frontend-config-snippet"

helm_required = unittest.skipUnless(
    shutil.which("helm"), "helm is not installed; chart rendering cannot be checked"
)


def _chart_dir(chart: str) -> Path:
    group = "developer-profiles" if chart in DEV else "industry-profiles/warehouse-operations"
    return HELM_DIR / group / chart


@cache
def _deps(chart: str) -> None:
    out = subprocess.run(["helm", "dependency", "build", "."], cwd=_chart_dir(chart),
                         capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stderr


def _stamped_version(chart: str) -> str:
    return yaml.safe_load((_chart_dir(chart) / "values.yaml").read_text())["vssVersion"]


def _ingresses(chart: str, host: str, *overrides: str) -> list[dict]:
    """Every Ingress the chart's vss-ingress.yaml renders."""
    _deps(chart)
    cmd = ["helm", "template", "review", ".", "--show-only", "templates/vss-ingress.yaml",
           "--set", "vssIngress.enabled=true", "--set", "global.vssIngress.enabled=true",
           "--set", f"global.externalHost={host}"]
    for override in overrides:
        cmd += ["--set-string", override]
    out = subprocess.run(cmd, cwd=_chart_dir(chart), capture_output=True, text=True, check=False)
    if out.returncode != 0 and "could not find template" in out.stderr:
        return []  # hostless render produced no Ingress at all
    assert out.returncode == 0, out.stderr
    return [d for d in yaml.safe_load_all(out.stdout) if d and d.get("kind") == "Ingress"]


def _snippets(chart: str, host: str, *overrides: str) -> list[str]:
    return [(i["metadata"].get("annotations") or {}).get(SNIPPET, "") for i in _ingresses(chart, host, *overrides)]


def _version_rules(snippets: list[str]) -> list[str]:
    return [line.strip() for s in snippets for line in s.splitlines() if "path /api/v1/version" in line]


@helm_required
class IngressVersionRoute(unittest.TestCase):
    def test_every_chart_answers_with_its_stamped_version_for_its_host(self):
        for chart in DEV + WAREHOUSE:
            with self.subTest(chart=chart):
                ingresses = _ingresses(chart, "vss.example")
                rules = _version_rules(
                    [(i["metadata"].get("annotations") or {}).get(SNIPPET, "") for i in ingresses]
                )
                self.assertEqual(len(rules), 1, rules)
                rule = rules[0]
                # Guarded on the Ingress's own main host (dev-profile-search derives
                # vss-search.<externalHost>.nip.io; the others use externalHost).
                main_host = next(r["host"] for i in ingresses for r in i["spec"]["rules"] if r.get("host"))
                self.assertIn(
                    f'\'{{"service":"vss","version":"{_stamped_version(chart)}"}}\'', rule
                )
                self.assertIn("http-request return status 200 content-type application/json", rule)
                self.assertIn(f"{{ req.hdr(host),field(1,:) -i {main_host} }}", rule)

    def test_no_host_means_no_version_rule(self):
        # dev-profile-search always derives a nip.io host, so it is never hostless.
        for chart in ("dev-profile-base", "dev-profile-alerts", "dev-profile-lvs") + WAREHOUSE:
            with self.subTest(chart=chart):
                self.assertEqual(_version_rules(_snippets(chart, "")), [])

    def test_a_caller_frontend_snippet_is_kept_after_the_rule(self):
        for chart in DEV:
            with self.subTest(chart=chart):
                snippets = _snippets(chart, "vss.example",
                                     f"vssIngress.annotations.haproxy\\.org/frontend-config-snippet=http-request set-header X-Caller yes")
                snippet = next(s for s in snippets if "X-Caller" in s)
                self.assertLess(snippet.index("path /api/v1/version"), snippet.index("X-Caller"))


if __name__ == "__main__":
    unittest.main()
