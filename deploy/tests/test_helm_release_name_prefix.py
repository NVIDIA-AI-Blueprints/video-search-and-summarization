# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Two releases of a developer profile can share a namespace.

With global.useReleaseNamePrefix on, every object a profile renders carries
the release name, so installing releases a and b side by side creates no
object both of them claim. Renamed objects stay reachable: every ConfigMap,
PVC and ServiceAccount a pod names, and every workload and Service SDRC
addresses, is one the same release renders. With the prefix off, the
Elasticsearch and Kibana init objects keep their legacy names.

NGC credential Secrets are named by values and are meant to be shared across
the namespace, so they are left out by rendering without creating them.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from functools import cache
from pathlib import Path

import yaml

PROFILES_DIR = Path(__file__).resolve().parents[1] / "helm" / "developer-profiles"
PROFILES = ("dev-profile-lvs", "dev-profile-search", "dev-profile-base", "dev-profile-alerts")
# (profile, *helm args): each SDRC config, plus chart-level overrides that must
# move SDRC's references together with the objects they name.
SDRC_CASES = (
    ("dev-profile-lvs",),
    ("dev-profile-search",),
    ("dev-profile-alerts",),
    ("dev-profile-alerts", "-f", "./dev-profile-alerts/values-realtime.yaml"),
    ("dev-profile-alerts", "--set", "rtvi.vss-rtvi-cv.useReleaseNamePrefix=true"),
    ("dev-profile-alerts", "--set", "rtvi.vss-rtvi-cv.statefulSetName=cv-custom"),
    ("dev-profile-lvs", "--set", "infra.sdrc.useReleaseNamePrefix=true",
     "--set", "vios.vss-vios-sensor.peerUseReleaseNamePrefix.sdrc=true"),
    ("dev-profile-lvs", "--set", "infra.sdrc.useReleaseNamePrefix=false",
     "--set", "vios.vss-vios-sensor.peerUseReleaseNamePrefix.sdrc=false"),
    ("dev-profile-lvs", "--set", "infra.redis.fullnameOverride=shared-redis",
     "--set", "infra.sdrc.runtimeEnv.WDM_WL_REDIS_SERVER=shared-redis"),
)
LEGACY_INIT_NAMES = {
    ("ConfigMap", "vss-elasticsearch-init-scripts"),
    ("Job", "vss-elasticsearch-init"),
    ("ConfigMap", "vss-kibana-init-import"),
    ("Job", "vss-kibana-init"),
}

helm_required = unittest.skipUnless(
    shutil.which("helm"), "helm is not installed; chart rendering cannot be checked"
)


def _run(cmd: list[str]) -> str:
    out = subprocess.run(cmd, cwd=PROFILES_DIR, capture_output=True, text=True, check=False)
    if out.returncode != 0:
        raise AssertionError(f"{' '.join(cmd)} failed:\n{out.stderr}")
    return out.stdout


def setUpModule() -> None:
    """Repackage the file:// dependencies so the render sees the current subcharts."""
    if not shutil.which("helm"):
        return
    for profile in PROFILES:
        _run(["helm", "dependency", "build", profile])


@cache
def _render(profile: str, release: str, prefix: bool, *args: str) -> tuple[dict, ...]:
    cmd = ["helm", "template", release, f"./{profile}", *args,
           "--set", f"global.useReleaseNamePrefix={str(prefix).lower()}"]
    documents = tuple(d for d in yaml.safe_load_all(_run(cmd)) if d)
    assert documents, "the chart rendered nothing; the assertions would be vacuous"
    return documents


def _names(documents) -> set[tuple[str, str]]:
    return {(d["kind"], d["metadata"]["name"]) for d in documents}


def _pod_specs(documents):
    for doc in documents:
        spec = doc.get("spec") or {}
        pod = (spec.get("jobTemplate") or {}).get("spec", spec).get("template", {}).get("spec")
        if pod:
            yield doc, pod


def _references(documents) -> set[tuple[str, str]]:
    refs = set()
    for _, pod in _pod_specs(documents):
        for volume in pod.get("volumes") or []:
            if "configMap" in volume:
                refs.add(("ConfigMap", volume["configMap"]["name"]))
            if "persistentVolumeClaim" in volume:
                refs.add(("PersistentVolumeClaim", volume["persistentVolumeClaim"]["claimName"]))
        account = pod.get("serviceAccountName")
        if account and account != "default":
            refs.add(("ServiceAccount", account))
    return refs


def _host(address: str) -> str:
    return address.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0]


def _sdrc_references(documents) -> set[tuple[str, str]]:
    """The StatefulSets SDRC places streams on and the Services it calls."""
    refs = set()
    for doc in documents:
        if doc["kind"] != "ConfigMap" or doc["metadata"].get("labels", {}).get("app.kubernetes.io/name") != "sdrc":
            continue
        for text in (doc.get("data") or {}).values():
            if "k8s-workload" not in text:
                continue
            for section, workload in yaml.safe_load(text).items():
                if not section.startswith("k8s-workload"):
                    continue
                refs |= {("StatefulSet", workload[k]) for k in ("wl_obj_name", "WDM_WL_OBJECT_NAME") if k in workload}
                refs |= {("Service", _host(workload[k]))
                         for k in ("VST_STREAMS_ENDPOINT", "VST_STATUS_ENDPOINT") if k in workload}
                refs |= {("Service", _host(e)) for e in json.loads(workload.get("HEADERLESS_SERVICE_ENDPOINTS", "[]"))}
    for _, pod in _pod_specs(documents):
        for container in pod.get("containers") or []:
            for env in container.get("env") or []:
                if env["name"] == "WDM_WL_REDIS_SERVER":
                    refs.add(("Service", env["value"]))
    return refs


@helm_required
class ReleaseNamePrefixTests(unittest.TestCase):
    def test_two_prefixed_releases_have_no_object_names_in_common(self):
        for profile in PROFILES:
            with self.subTest(profile=profile):
                a = _names(_render(profile, "a", True))
                b = _names(_render(profile, "b", True))
                self.assertEqual(sorted(a & b), [])

    def test_every_object_a_pod_names_is_rendered_by_the_same_release(self):
        for profile in PROFILES:
            for prefix in (False, True):
                with self.subTest(profile=profile, prefix=prefix):
                    documents = _render(profile, "a", prefix)
                    self.assertEqual(sorted(_references(documents) - _names(documents)), [])

    def test_every_object_sdrc_addresses_is_rendered_by_the_same_release(self):
        for profile, *args in SDRC_CASES:
            for prefix in (False, True):
                with self.subTest(profile=profile, args=args, prefix=prefix):
                    documents = _render(profile, "a", prefix, *args)
                    refs = _sdrc_references(documents)
                    self.assertTrue(refs, "no SDRC references found; the check would be vacuous")
                    self.assertEqual(sorted(refs - _names(documents)), [])

    def test_unprefixed_init_objects_keep_their_legacy_names(self):
        for profile in ("dev-profile-lvs", "dev-profile-search", "dev-profile-alerts"):
            with self.subTest(profile=profile):
                self.assertLessEqual(LEGACY_INIT_NAMES, _names(_render(profile, "a", False)))


if __name__ == "__main__":
    unittest.main()
