# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""vssIngress.annotations on every developer profile's Ingress.

A non-HAProxy ingress class (e.g. Traefik with a routing middleware) needs its
own controller annotations on the profile Ingress. Every profile builds one
annotation map: its own annotations, then the caller's vssIngress.annotations
over them, then the generated HAProxy path rewrites -- unless the caller
supplied haproxy.org/path-rewrite, which replaces the table. The default render
is unchanged, including the literal-block trailing newline on the rewrites.
"""

from __future__ import annotations

import shutil
import subprocess
import unittest
from functools import cache
from pathlib import Path

import yaml

PROFILES_DIR = Path(__file__).resolve().parents[1] / "helm" / "developer-profiles"
PROFILES = ("dev-profile-base", "dev-profile-alerts", "dev-profile-lvs", "dev-profile-search")
# Profile-owned annotations that must survive (inert timeouts kept for continuity).
OWN = {
    "dev-profile-lvs": {"haproxy.org/timeout-client": "3600s"},
    "dev-profile-search": {"haproxy.org/timeout-client": "3600s", "haproxy.org/timeout-tunnel": "3600s"},
}
MIDDLEWARE = "traefik.ingress.kubernetes.io/router.middlewares"

helm_required = unittest.skipUnless(
    shutil.which("helm"), "helm is not installed; chart rendering cannot be checked"
)


@cache
def _deps(profile: str) -> None:
    out = subprocess.run(["helm", "dependency", "build", profile], cwd=PROFILES_DIR,
                         capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stderr


def _annotations(profile: str, *overrides: str) -> dict:
    _deps(profile)
    cmd = ["helm", "template", "review", "./" + profile, "--set", "global.externalHost=x.example",
           "--set", "vssIngress.enabled=true"]
    for override in overrides:
        cmd += ["--set-string", override]
    out = subprocess.run(cmd, cwd=PROFILES_DIR, capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stderr
    ingresses = [d for d in yaml.safe_load_all(out.stdout) if d and d.get("kind") == "Ingress"]
    assert ingresses, profile
    # The profile's main Ingress carries the rewrite table.
    main = next(i for i in ingresses if "haproxy.org/path-rewrite" in (i["metadata"].get("annotations") or {})
                or MIDDLEWARE in (i["metadata"].get("annotations") or {}))
    return main["metadata"]["annotations"]


@helm_required
class IngressAnnotations(unittest.TestCase):
    def test_default_keeps_the_generated_rewrites_and_the_profile_annotations(self):
        for profile in PROFILES:
            with self.subTest(profile=profile):
                ann = _annotations(profile)
                rewrites = ann["haproxy.org/path-rewrite"]
                self.assertTrue(rewrites.endswith("\n"), repr(rewrites[-20:]))
                self.assertIn("^/storage /vst/storage", rewrites)
                for key, value in OWN.get(profile, {}).items():
                    self.assertEqual(ann[key], value)

    def test_caller_annotations_are_added(self):
        mw = "vss-eval-runtime-runtime-vss-routes@kubernetescrd"
        for profile in PROFILES:
            with self.subTest(profile=profile):
                ann = _annotations(profile, "vssIngress.ingressClassName=traefik",
                                   "vssIngress.annotations.traefik\\.ingress\\.kubernetes\\.io/router\\.middlewares=" + mw)
                self.assertEqual(ann[MIDDLEWARE], mw)
                self.assertIn("haproxy.org/path-rewrite", ann)
                for key, value in OWN.get(profile, {}).items():
                    self.assertEqual(ann[key], value)

    def test_a_caller_rewrite_replaces_the_table_and_a_caller_value_wins(self):
        for profile in PROFILES:
            with self.subTest(profile=profile):
                ann = _annotations(profile, "vssIngress.annotations.haproxy\\.org/path-rewrite=/custom /",
                                   "vssIngress.annotations.haproxy\\.org/timeout-client=10s")
                self.assertEqual(ann["haproxy.org/path-rewrite"], "/custom /")
                self.assertEqual(ann["haproxy.org/timeout-client"], "10s")


if __name__ == "__main__":
    unittest.main()
