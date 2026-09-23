# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""dev-profile-base settings an on-demand (leased, single-GPU) deployment needs.

Found deploying the base profile under a GPU lease for evaluation runs:

  1. The VIOS sensor's stream-processor endpoint. The profile set it to a
     template string, but vss-vios-sensor reads the value verbatim (no `tpl`),
     so the sensor was handed literal "{{- $g := ..." text. Empty lets the
     subchart derive the release-aware endpoint itself.
  2. VST's download_files_timeout_secs was hard-coded at 120 s; hour-long
     videos ingested by URL need more, so it is a value (schema-checked).
  3. The profile Ingress took no controller annotations, so a non-HAProxy
     class (Traefik with a routing middleware) could not be used. The HAProxy
     path rewrites are still added unless the caller supplies their own.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import unittest
from functools import cache
from pathlib import Path

import yaml

HELM_ROOT = Path(__file__).resolve().parents[1] / "helm"
PROFILES_DIR = HELM_ROOT / "developer-profiles"
PROFILE = "dev-profile-base"
HOST = "10.0.0.1"

helm_required = unittest.skipUnless(
    shutil.which("helm"), "helm is not installed; chart rendering cannot be checked"
)


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=PROFILES_DIR, capture_output=True, text=True, check=False)


@cache
def _deps() -> None:
    out = _run(["helm", "dependency", "build", PROFILE])
    assert out.returncode == 0, out.stderr


def _render(*overrides: str, release: str = "vss") -> subprocess.CompletedProcess:
    _deps()
    cmd = ["helm", "template", release, "./" + PROFILE,
           "--set", "global.externalHost=" + HOST, "--set", "vssIngress.enabled=true"]
    for override in overrides:
        cmd += ["--set", override]
    return _run(cmd)


def _docs(*overrides: str, release: str = "vss") -> list[dict]:
    out = _render(*overrides, release=release)
    assert out.returncode == 0, out.stderr
    return [d for d in yaml.safe_load_all(out.stdout) if d]


def _env(docs: list[dict], name: str) -> list[str]:
    values = []
    for d in docs:
        spec = ((d.get("spec") or {}).get("template") or {}).get("spec") or {}
        for c in spec.get("containers") or []:
            values += [e.get("value") for e in c.get("env") or [] if e.get("name") == name]
    return values


def _vst_timeouts(docs: list[dict]) -> list[int]:
    found = []
    for d in docs:
        if d.get("kind") != "ConfigMap":
            continue
        for text in (d.get("data") or {}).values():
            found += [int(v) for v in re.findall(r'"download_files_timeout_secs":\s*(\d+)', text or "")]
    return found


def _ingress(docs: list[dict]) -> dict:
    ing = [d for d in docs if d.get("kind") == "Ingress"]
    assert len(ing) == 1, [d["metadata"]["name"] for d in ing]
    return ing[0]


@helm_required
class StreamProcessorEndpoint(unittest.TestCase):
    def test_the_sensor_gets_a_url_not_template_text(self):
        for release, prefix in (("vss", "false"), ("gpu-lease-x", "true")):
            with self.subTest(release=release, useReleaseNamePrefix=prefix):
                values = _env(_docs("global.useReleaseNamePrefix=" + prefix, release=release),
                              "STREAM_PROCESSOR_MODULE_ENDPOINT")
                self.assertTrue(values)
                host = f"{release}-vss-vios-streamprocessing" if prefix == "true" else "vss-vios-streamprocessing"
                for value in values:
                    self.assertNotIn("{{", value)
                    self.assertEqual(value, f"http://{host}:30001")


@helm_required
class VstDownloadTimeout(unittest.TestCase):
    def test_default_is_unchanged_and_the_value_overrides_it(self):
        self.assertIn(120, _vst_timeouts(_docs()))
        self.assertIn(1800, _vst_timeouts(_docs("vios.vss-vios-streamprocessing.downloadFilesTimeoutSecs=1800")))

    def test_a_non_integer_is_refused_by_the_schema(self):
        out = _render("vios.vss-vios-streamprocessing.downloadFilesTimeoutSecs=abc")
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("schema", out.stderr)


@helm_required
class IngressAnnotations(unittest.TestCase):
    def test_default_keeps_the_haproxy_path_rewrites(self):
        ann = _ingress(_docs())["metadata"]["annotations"]
        self.assertEqual(list(ann), ["haproxy.org/path-rewrite"])
        self.assertIn("/rtvi-vlm/(.*) /\\1", ann["haproxy.org/path-rewrite"])

    def test_caller_annotations_are_added_for_another_ingress_class(self):
        mw = "vss-eval-runtime-runtime-vss-routes@kubernetescrd"
        ann = _ingress(_docs(
            "vssIngress.ingressClassName=traefik",
            "vssIngress.annotations.traefik\\.ingress\\.kubernetes\\.io/router\\.middlewares=" + mw,
        ))["metadata"]["annotations"]
        self.assertEqual(ann["traefik.ingress.kubernetes.io/router.middlewares"], mw)
        self.assertIn("haproxy.org/path-rewrite", ann)

    def test_a_caller_rewrite_is_not_overwritten(self):
        ann = _ingress(_docs(
            "vssIngress.annotations.haproxy\\.org/path-rewrite=/custom /"))["metadata"]["annotations"]
        self.assertEqual(ann["haproxy.org/path-rewrite"], "/custom /")


if __name__ == "__main__":
    unittest.main()
