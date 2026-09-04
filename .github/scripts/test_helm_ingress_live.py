#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""What the rendered Ingress objects actually do on a real HAProxy controller.

``test_helm_ingress_rewrites.py`` and ``check_helm_alias_root_rewrites.py``
assert the *text* of ``haproxy.org/path-rewrite``. They cannot see whether the
controller agrees: which mount a request selects, and which path the backend is
handed, are decided by the controller's route map and its per-rule ACL guards,
neither of which exists until something is deployed. Three ingress defects have
been found in these charts and every one was argued from `helm template` output
and upstream Go source.

This test closes that gap. It stands up a ``kind`` cluster, installs the
HAProxy Kubernetes Ingress controller at the version the warehouse READMEs pin,
renders each chart's Ingress, points it at path-echoing stubs, and asserts the
contract that actually matters:

    a request for <path> reaches <backend> carrying <forwarded path>

The stub is ``traefik/whoami``, which echoes its own ``--name`` and the verbatim
request line, so one probe answers both halves. No product image, GPU or NGC
credential is involved: what is under test is routing, and routing is testable
with backends that only report what they were asked for.

Skips rather than fails when ``kind``/``kubectl``/``helm`` are absent, so it is
safe to keep in the tree and run locally.

Run directly for a live run; it is deliberately not part of the fast lint job.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HELM_ROOT = REPO_ROOT / "deploy/helm"

CLUSTER = os.environ.get("VSS_INGRESS_TEST_CLUSTER", "vss-ingress-test")
NAMESPACE = "vss-ingress-test"
# Host port the kind node's :80 is published on. Deliberately not 80, and not
# 7777 or 9200, so a live Compose deployment on the same host is undisturbed.
EDGE_PORT = int(os.environ.get("VSS_INGRESS_TEST_PORT", "18080"))
EDGE = f"http://127.0.0.1:{EDGE_PORT}"

# The chart version the warehouse READMEs install. Pinned because the
# pathType: Prefix trailing-slash behaviour this test relies on is a property of
# the controller binary (pkg/route/route.go), not of the Ingress API, and a
# version-agnostic assertion about it would be worthless.
CONTROLLER_CHART_VERSION = "1.49.0"

STUB_IMAGE = "traefik/whoami:latest"

# `name:port` per backend the charts under test name by default.
STUBS = [
    "vss-agent-ui:3000",
    "vss-agent:8000",
    "vss-vios-ingress:30888",
    "vss-vios-nvstreamer:31000",
    "vss-va-mcp:9901",
    "vss-video-analytics-api:8081",
    "vss-behavior-analytics:8080",
    "elasticsearch:9200",
    "vss-rtvi-vlm:8000",
    "vss-rtvi-cv:9000",
    "vss-rtvi-embed:8000",
    "phoenix:6006",
    "kibana:5601",
    "grafana:3000",
    "prometheus:9090",
]


def run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, **kwargs)


def require(*tools: str) -> str | None:
    missing = [t for t in tools if shutil.which(t) is None]
    return f"needs {', '.join(missing)} on PATH" if missing else None


class Cluster:
    """A kind cluster with the HAProxy ingress controller and the stubs."""

    def __init__(self) -> None:
        self.config = None

    def up(self) -> None:
        existing = run(["kind", "get", "clusters"]).stdout.split()
        if CLUSTER not in existing:
            config = textwrap.dedent(
                f"""
                kind: Cluster
                apiVersion: kind.x-k8s.io/v1alpha4
                name: {CLUSTER}
                nodes:
                  - role: control-plane
                    extraPortMappings:
                      - containerPort: 80
                        hostPort: {EDGE_PORT}
                        listenAddress: "127.0.0.1"
                        protocol: TCP
                """
            ).strip()
            created = subprocess.run(
                ["kind", "create", "cluster", "--config", "-", "--wait", "180s"],
                input=config,
                capture_output=True,
                text=True,
            )
            if created.returncode != 0:
                raise RuntimeError(f"kind create cluster failed:\n{created.stderr}")

        # Never inherit whatever context the ambient kubeconfig happens to be
        # on. `kind delete cluster` clears current-context, so a box that has
        # had any other kind cluster deleted leaves kubectl falling back to
        # localhost:8080 -- which surfaces as "ingress apply failed", reads as a
        # chart defect, and is not one. helm and kubectl are both pointed at the
        # cluster this test created.
        exported = run(["kind", "export", "kubeconfig", "--name", CLUSTER])
        if exported.returncode != 0:
            raise RuntimeError(f"kind export kubeconfig failed:\n{exported.stderr}")
        reachable = run(["kubectl", "cluster-info"])
        if reachable.returncode != 0:
            raise RuntimeError(
                f"the kind cluster is not reachable, so nothing below would be "
                f"testing the charts:\n{reachable.stderr}"
            )

        # The controller is installed exactly as the warehouse README says to,
        # so a divergence here is a divergence operators would hit.
        run(["helm", "repo", "add", "haproxytech", "https://haproxytech.github.io/helm-charts"])
        run(["helm", "repo", "update", "haproxytech"])
        installed = run(
            [
                "helm", "upgrade", "--install", "haproxy-ingress",
                "haproxytech/kubernetes-ingress",
                "--version", CONTROLLER_CHART_VERSION,
                "-n", "haproxy-controller", "--create-namespace",
                "--set", "controller.kind=DaemonSet",
                "--set", "controller.daemonset.useHostPort=true",
                "--set", "controller.daemonset.hostPorts.http=80",
                "--set", "controller.service.enabled=false",
                "--set", "controller.ingressClass=haproxy",
                "--wait", "--timeout", "5m",
            ]
        )
        if installed.returncode != 0:
            raise RuntimeError(f"controller install failed:\n{installed.stderr}")

        run(["kubectl", "create", "namespace", NAMESPACE])
        # Preloading saves fifteen pulls, but the pods stay on IfNotPresent so a
        # cache miss falls back to the registry instead of failing the whole run
        # with ErrImageNeverPull.
        run(["docker", "pull", STUB_IMAGE])
        run(["kind", "load", "docker-image", STUB_IMAGE, "--name", CLUSTER])
        applied = subprocess.run(
            ["kubectl", "apply", "-n", NAMESPACE, "-f", "-"],
            input=self.stub_manifests(),
            capture_output=True,
            text=True,
        )
        if applied.returncode != 0:
            raise RuntimeError(f"stub apply failed:\n{applied.stderr}")
        # `wait --all` is vacuous for a Deployment that was never created, so
        # the roster is checked by name first. A stub missing from the namespace
        # reads downstream as a routing failure, which is the one conclusion it
        # does not support.
        want = {spec.rpartition(":")[0] for spec in STUBS}
        have = set(run([
            "kubectl", "-n", NAMESPACE, "get", "deployments",
            "-o", "jsonpath={.items[*].metadata.name}",
        ]).stdout.split())
        if want - have:
            raise RuntimeError(
                f"these stub Deployments were not created, so the manifests are "
                f"wrong rather than the charts: {sorted(want - have)}"
            )
        ready = run([
            "kubectl", "-n", NAMESPACE, "wait", "--for=condition=available",
            "--timeout=300s", "deployment", "--all",
        ])
        if ready.returncode != 0:
            # Without this the run continues and every route assertion fails with
            # a 503 that looks like a routing bug rather than an absent backend.
            pods = run(["kubectl", "-n", NAMESPACE, "get", "pods"]).stdout
            raise RuntimeError(
                f"stub backends never became ready, so no routing conclusion is "
                f"possible:\n{ready.stderr}\n{pods}"
            )

    @staticmethod
    def stub_manifests() -> str:
        """One whoami Deployment/Service per backend the charts name.

        Every stub listens on :80 in the pod and each Service publishes the port
        the chart's default expects, so the rendered Ingress can be applied
        byte-for-byte rather than rewritten to suit the test.
        """
        parts = []
        for spec in STUBS:
            name, _, port = spec.rpartition(":")
            parts.append(
                textwrap.dedent(
                    f"""
                    ---
                    apiVersion: apps/v1
                    kind: Deployment
                    metadata:
                      name: {name}
                    spec:
                      replicas: 1
                      selector:
                        matchLabels: {{ app: {name} }}
                      template:
                        metadata:
                          labels: {{ app: {name} }}
                        spec:
                          containers:
                            - name: whoami
                              image: {STUB_IMAGE}
                              imagePullPolicy: IfNotPresent
                              args: ["--port", "80", "--name", "{name}"]
                              ports: [{{ containerPort: 80 }}]
                    ---
                    apiVersion: v1
                    kind: Service
                    metadata:
                      name: {name}
                    spec:
                      selector: {{ app: {name} }}
                      ports: [{{ port: {port}, targetPort: 80 }}]
                    """
                ).strip()
            )
        return "\n".join(parts) + "\n"

    def down(self) -> None:
        if os.environ.get("VSS_INGRESS_TEST_KEEP"):
            return
        run(["kind", "delete", "cluster", "--name", CLUSTER])


def render(chart: Path, sets: list[str]) -> str:
    argv = ["helm", "template", "vss", str(chart), "--namespace", NAMESPACE,
            "--show-only", "templates/vss-ingress.yaml"]
    for item in sets:
        argv += ["--set", item]
    result = run(argv)
    if result.returncode != 0:
        raise RuntimeError(f"helm template {chart.name} failed:\n{result.stderr}")
    return result.stdout


def apply_ingress(manifest: str) -> None:
    """Replace every Ingress in the namespace with the one under test.

    Deleting first is load-bearing, not tidiness. The controller merges the
    `haproxy.org/path-rewrite` rules of every Ingress it watches into one
    config, so a chart left behind from an earlier assertion can supply the very
    rewrite the chart under test is missing. That is not hypothetical: with the
    warehouse and developer-profile Ingresses both applied, the pre-fix
    warehouse chart -- which has no `^/storage$` rule and demonstrably hands the
    backend an unrewritten `/storage` on its own -- passed this suite.
    """
    run(["kubectl", "-n", NAMESPACE, "delete", "ingress", "--all", "--wait=true"])
    applied = subprocess.run(
        ["kubectl", "apply", "-n", NAMESPACE, "-f", "-"],
        input=manifest, capture_output=True, text=True,
    )
    if applied.returncode != 0:
        raise RuntimeError(f"ingress apply failed:\n{applied.stderr}")


def probe(path: str, host: str = "") -> dict[str, str | None]:
    """One request; reports which stub answered and what path it received."""
    argv = ["curl", "-s", "-o", "-", "-w", "\n__STATUS__%{http_code}", "--max-time", "15"]
    if host:
        argv += ["-H", f"Host: {host}"]
    argv.append(f"{EDGE}{path}")
    body = run(argv).stdout

    status = ""
    backend = None
    received = None
    for line in body.splitlines():
        if line.startswith("__STATUS__"):
            status = line[len("__STATUS__"):]
        elif line.startswith("Hostname:"):
            backend = line.split(":", 1)[1].strip()
        elif line.startswith("GET ") and " HTTP/" in line:
            received = line.split()[1]
    return {"status": status, "backend": backend, "received": received}


# `requested -> (backend deployment, path the backend must be handed)`.
#
# These are the assertions no existing test can make. Each was verified against
# a live controller; a change that breaks one is a routing regression even when
# the rendered annotation still lints clean.
WAREHOUSE_EXPECTATIONS = {
    "/vios": ("vss-vios-ingress", "/vst"),
    "/vios/api/v1/sensor/list": ("vss-vios-ingress", "/vst/api/v1/sensor/list"),
    "/storage": ("vss-vios-ingress", "/vst/storage"),
    "/storage/clip.mp4": ("vss-vios-ingress", "/vst/storage/clip.mp4"),
    "/vst": ("vss-vios-ingress", "/vst"),
    "/vst/api/v1/sensor/list": ("vss-vios-ingress", "/vst/api/v1/sensor/list"),
    "/video-analytics-api": ("vss-video-analytics-api", "/"),
    "/video-analytics-api/metrics": ("vss-video-analytics-api", "/metrics"),
    "/behavior-analytics": ("vss-behavior-analytics", "/"),
    "/streamer": ("vss-vios-nvstreamer", "/"),
    "/streamer/index.html": ("vss-vios-nvstreamer", "/index.html"),
}

CANONICAL_EXPECTATIONS = {
    "/vios": ("vss-vios-ingress", "/vst"),
    "/vios/api/v1/sensor/list": ("vss-vios-ingress", "/vst/api/v1/sensor/list"),
    "/storage": ("vss-vios-ingress", "/vst/storage"),
    "/storage/clip.mp4": ("vss-vios-ingress", "/vst/storage/clip.mp4"),
    "/vios/storage": ("vss-vios-ingress", "/vst/storage"),
    "/vios/storage/clip.mp4": ("vss-vios-ingress", "/vst/storage/clip.mp4"),
    # The anchored rewrite must leave the canonical prefix alone. An unanchored
    # `/storage/(.*)` collapses any path *containing* the prefix onto
    # /vst/storage/<tail>, so this row is what makes the `^` load-bearing.
    "/vst/storage/clip.mp4": ("vss-vios-ingress", "/vst/storage/clip.mp4"),
    "/elasticsearch": ("elasticsearch", "/"),
    "/elasticsearch/_cat/indices": ("elasticsearch", "/_cat/indices"),
    "/rtvi-embed/v1/embeddings": ("vss-rtvi-embed", "/v1/embeddings"),
    # behavior-analytics is forwarded WHOLE here, and stripped by the warehouse
    # charts. Asserted as it is, not as it "should" be: the two really differ,
    # and the difference is unobservable today because the image serves no HTTP.
    "/behavior-analytics": ("vss-behavior-analytics", "/behavior-analytics"),
    "/behavior-analytics/api/v1/x": ("vss-behavior-analytics", "/behavior-analytics/api/v1/x"),
    # Longest-prefix wins: /api/chat is the UI, /api is the agent.
    "/api/chat": ("vss-agent-ui", "/api/chat"),
    "/openapi.json": ("vss-agent", "/openapi.json"),
}


class LiveIngressTest(unittest.TestCase):
    cluster: Cluster | None = None

    @classmethod
    def setUpClass(cls) -> None:
        reason = require("kind", "kubectl", "helm", "docker", "curl")
        if reason:
            raise unittest.SkipTest(reason)
        cls.cluster = Cluster()
        cls.cluster.up()

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.cluster:
            cls.cluster.down()

    def assert_routes(self, manifest: str, expectations: dict, host: str = "") -> None:
        apply_ingress(manifest)

        # The controller rewrites its maps and reloads asynchronously, so a route
        # can be briefly absent after the Ingress lands. Waiting is only ever
        # right for that case: once a backend answers, the path it was handed is
        # final, and retrying a wrong path just pays the whole deadline before
        # reporting the same defect.
        deadline = time.monotonic() + 120
        while True:
            failures = []
            unrouted = False
            for path, (backend, forwarded) in sorted(expectations.items()):
                got = probe(path, host=host)
                actual = (got["backend"] or "").rsplit("-", 2)[0]
                if actual != backend or got["received"] != forwarded:
                    unrouted = unrouted or got["backend"] is None
                    failures.append(
                        f"  GET {path}\n"
                        f"    expected {backend} to receive {forwarded!r}\n"
                        f"    got      {actual or '(no backend)'} received "
                        f"{got['received']!r} (HTTP {got['status']})"
                    )
            if not failures or not unrouted or time.monotonic() > deadline:
                break
            time.sleep(3)

        if failures:
            self.fail(
                "the live controller routed these differently than the charts "
                "promise:\n" + "\n".join(failures)
            )

    def test_warehouse_charts_route_every_mount_and_bare_root(self) -> None:
        """Each warehouse chart strips or replaces the prefix on every mount.

        The bare roots are the point: they route to a healthy backend either
        way, so a missing `^/X$` rule is invisible to anything that only checks
        whether a request was admitted.
        """
        for name in ("warehouse-2d-app", "warehouse-3d-app", "warehouse-mv3dt-app"):
            chart = HELM_ROOT / "industry-profiles/warehouse-operations" / name
            with self.subTest(chart=name):
                manifest = render(
                    chart,
                    [
                        # Gated on global.vssIngress.enabled, NOT vssIngress.enabled.
                        # With the wrong key the template renders comments only and
                        # every assertion below would pass against no Ingress, so
                        # the guard test after this one asserts the flag itself.
                        "global.vssIngress.enabled=true",
                        "analytics.enabled=true",
                        "analytics.vss-behavior-analytics.enabled=true",
                        "vios.vss-vios-nvstreamer.enabled=true",
                    ],
                )
                self.assertIn("kind: Ingress", manifest, "no Ingress rendered")
                self.assert_routes(manifest, WAREHOUSE_EXPECTATIONS)

    def test_canonical_route_table_routes_every_mount(self) -> None:
        """The shared table in services/common, as a developer profile renders it."""
        chart = HELM_ROOT / "developer-profiles/dev-profile-search"
        manifest = render(
            chart,
            [
                "vssIngress.enabled=true",
                "vssIngress.ingressClassName=haproxy",
                "vssIngress.hosts.main=vss.local",
                "infra.elasticsearch.enabled=true",
                "analytics.vss-video-analytics-api.enabled=true",
                "analytics.vss-behavior-analytics.enabled=true",
                "rtvi.vss-rtvi-embed.enabled=true",
                "agent.vss-va-mcp.enabled=true",
            ],
        )
        self.assertIn("kind: Ingress", manifest, "no Ingress rendered")
        self.assert_routes(manifest, CANONICAL_EXPECTATIONS, host="vss.local")

    def test_every_rewriting_mount_is_covered_by_an_expectation(self) -> None:
        """A probe table that misses a mount passes forever.

        Mirrors the "a lint that matches nothing passes" guard the static checks
        already carry: a new rewriting route has to gain a row above, or this
        fails and says which one is untested.
        """
        chart = HELM_ROOT / "industry-profiles/warehouse-operations/warehouse-2d-app"
        manifest = render(
            chart,
            [
                "global.vssIngress.enabled=true",
                "analytics.enabled=true",
                "analytics.vss-behavior-analytics.enabled=true",
                "vios.vss-vios-nvstreamer.enabled=true",
            ],
        )
        mounts = {
            line.strip().split()[0][1:].removesuffix("/(.*)")
            for line in manifest.splitlines()
            if line.strip().startswith("^/") and "/(.*)" in line
        }
        self.assertTrue(mounts, "no rewriting mounts found; this test is inert")
        probed = set(WAREHOUSE_EXPECTATIONS)
        missing = sorted(m for m in mounts if m not in probed)
        self.assertEqual(
            missing, [],
            f"these warehouse mounts rewrite but are never probed live: {missing}. "
            "Add a row to WAREHOUSE_EXPECTATIONS naming the backend and the path "
            "it must receive.",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
