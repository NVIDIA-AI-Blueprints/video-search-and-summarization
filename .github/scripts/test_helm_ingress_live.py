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

# whoami never redirects, so it cannot show what an alias does to a Location
# header. VST's ingress does redirect -- `location = /vst { return 301 /vst/; }`
# with `absolute_redirect off` -- and that is the one backend behaviour the
# alias has to survive. This stub reproduces exactly that and nothing else.
REDIRECT_STUB_IMAGE = "nginx:1.27-alpine"
REDIRECT_STUB = "vss-vios-ingress-vst-redirect"

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
        for image in (STUB_IMAGE, REDIRECT_STUB_IMAGE):
            run(["docker", "pull", image])
            run(["kind", "load", "docker-image", image, "--name", CLUSTER])
        applied = subprocess.run(
            ["kubectl", "apply", "-n", NAMESPACE, "-f", "-"],
            input=self.stub_manifests() + self.redirect_stub_manifests(),
            capture_output=True,
            text=True,
        )
        if applied.returncode != 0:
            raise RuntimeError(f"stub apply failed:\n{applied.stderr}")
        # `wait --all` is vacuous for a Deployment that was never created, so
        # the roster is checked by name first. A stub missing from the namespace
        # reads downstream as a routing failure, which is the one conclusion it
        # does not support.
        want = {spec.rpartition(":")[0] for spec in STUBS} | {REDIRECT_STUB}
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

    @staticmethod
    def redirect_stub_manifests() -> str:
        """A stand-in for VST's ingress that redirects the way VST really does.

        No Service of its own: the alias assertion repoints the existing
        `vss-vios-ingress` Service at these pods, so the rendered Ingress -- and
        therefore the controller's backend, route map and rule IDs -- is the
        same object under test as everywhere else in this file. Swapping the
        image on the whoami Deployment instead would restart it and lose the
        path echo the other tests read.

        `absolute_redirect off` and `location = /vst` are copied verbatim from
        deploy/helm/services/vios/charts/vios-ingress/configs/nginx-vst.conf.template:
        the trailing-slash redirect on the bare prefix is what makes the UI's
        relative asset paths resolve, and it is the only redirect that config
        emits. Nothing else is reproduced -- notably there is no
        `location = /vst/storage`, because the real config has none either, so
        /storage and /vios/storage have no Location to rename.
        """
        conf = textwrap.dedent(
            """
            server {
                listen 80;
                absolute_redirect off;
                default_type text/plain;
                location = /vst { return 301 /vst/; }
                location / { return 200 "Hostname: $hostname\\n$request\\n"; }
            }
            """
        ).strip()
        # The config goes in as a JSON-quoted scalar, which YAML accepts as-is.
        # A block scalar would have to be re-indented to sit under `data:`, and
        # getting that wrong does not fail the apply -- it swallows the document
        # separator below, so the Deployment is silently never created and the
        # assertion reports "the stub never answered" instead of a broken
        # manifest. That happened; hence no block scalar here.
        return textwrap.dedent(
            f"""
            ---
            apiVersion: v1
            kind: ConfigMap
            metadata:
              name: {REDIRECT_STUB}
            data:
              default.conf: {json.dumps(conf)}
            ---
            apiVersion: apps/v1
            kind: Deployment
            metadata:
              name: {REDIRECT_STUB}
            spec:
              replicas: 1
              selector:
                matchLabels: {{ app: {REDIRECT_STUB} }}
              template:
                metadata:
                  labels: {{ app: {REDIRECT_STUB} }}
                spec:
                  containers:
                    - name: nginx
                      image: {REDIRECT_STUB_IMAGE}
                      imagePullPolicy: IfNotPresent
                      ports: [{{ containerPort: 80 }}]
                      volumeMounts:
                        - name: conf
                          mountPath: /etc/nginx/conf.d
                  volumes:
                    - name: conf
                      configMap: {{ name: {REDIRECT_STUB} }}
            """
        ).strip() + "\n"

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


# Chart values every assertion renders with. Shared so the routing and the
# alias-redirect assertions cannot drift into testing two different renders.
#
# Gated on global.vssIngress.enabled for warehouse and vssIngress.enabled for
# the developer profiles. The two are NOT interchangeable, and neither a missing
# flag nor the wrong one is an error: the template renders comments only, helm
# exits 0, and every assertion below would pass against no Ingress at all --
# which is why each render is checked for `kind: Ingress` before it is probed.
WAREHOUSE_SETS = [
    "global.vssIngress.enabled=true",
    "analytics.enabled=true",
    "analytics.vss-behavior-analytics.enabled=true",
    "vios.vss-vios-nvstreamer.enabled=true",
]

CANONICAL_SETS = [
    "vssIngress.enabled=true",
    "vssIngress.ingressClassName=haproxy",
    "vssIngress.hosts.main=vss.local",
    "infra.elasticsearch.enabled=true",
    "analytics.vss-video-analytics-api.enabled=true",
    "analytics.vss-behavior-analytics.enabled=true",
    "rtvi.vss-rtvi-embed.enabled=true",
    "agent.vss-va-mcp.enabled=true",
]

CANONICAL_HOST = "vss.local"

# Where every profile renders the VST Service. Same relative path under the
# warehouse umbrella and the developer profiles, because both take it from
# services/vios -- which is what lets one annotation there fix both families.
VST_SERVICE_TEMPLATE = "charts/vios/charts/vss-vios-ingress/templates/service.yaml"


def vst_service_annotations(chart: Path, sets: list[str]) -> dict[str, str]:
    """The annotations a chart puts on its vss-vios-ingress Service.

    This controller wants per-backend behaviour on the Service, not the Ingress
    (deploy/helm/services/common/README.md), and the alias's Location rewrite
    lives there. The stub Services in this namespace are hand-built, so nothing
    would carry that annotation to the controller and the redirect assertion
    would quietly test an unannotated backend. Read out of the chart rather
    than restated here, so changing the annotation changes what is asserted.
    """
    argv = ["helm", "template", "vss", str(chart), "--namespace", NAMESPACE,
            "--show-only", VST_SERVICE_TEMPLATE]
    for item in sets:
        argv += ["--set", item]
    rendered = run(argv)
    if rendered.returncode != 0:
        raise RuntimeError(
            f"helm template {chart.name} ({VST_SERVICE_TEMPLATE}) failed:\n{rendered.stderr}"
        )
    # kubectl rather than a YAML module: the runner is not guaranteed to have
    # one, and this file already depends on kubectl for everything else.
    parsed = run(
        ["kubectl", "create", "-f", "-", "--dry-run=client", "-o", "json"],
        input=rendered.stdout,
    )
    if parsed.returncode != 0:
        raise RuntimeError(f"could not parse {chart.name}'s VST Service:\n{parsed.stderr}")
    annotations = json.loads(parsed.stdout).get("metadata", {}).get("annotations") or {}
    if not annotations:
        raise RuntimeError(
            f"{chart.name} renders no annotations on the vss-vios-ingress Service. "
            "The alias's Location rewrite lives there, so there would be nothing "
            "to assert and this test would pass on a chart that lost the fix."
        )
    return annotations


def annotate_vst_service(annotations: dict[str, str | None]) -> None:
    """Put a chart's Service annotations on the stub Service, or drop them.

    A null value removes the key, which is how this is undone: an annotation
    left behind is the same failure mode as an Ingress left behind, and it
    would make the next chart look like it still carried the rewrite.
    """
    patched = run([
        "kubectl", "-n", NAMESPACE, "patch", "service", "vss-vios-ingress",
        "--type", "merge",
        "-p", json.dumps({"metadata": {"annotations": annotations}}),
    ])
    if patched.returncode != 0:
        raise RuntimeError(f"could not annotate vss-vios-ingress:\n{patched.stderr}")


def select_vst_backend(app_label: str) -> None:
    """Repoint the `vss-vios-ingress` stub Service at one of the two stubs.

    The Service name and port are what the rendered Ingress names, so they must
    not change; only which pods sit behind them does.
    """
    patched = run([
        "kubectl", "-n", NAMESPACE, "patch", "service", "vss-vios-ingress",
        "--type", "merge",
        "-p", json.dumps({"spec": {"selector": {"app": app_label}}}),
    ])
    if patched.returncode != 0:
        raise RuntimeError(f"could not repoint vss-vios-ingress:\n{patched.stderr}")


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
    """One request; reports which stub answered, the path it received, and the
    Location it sent back.

    `-i` rather than `--head`: the response headers carry the Location an alias
    has to rename, and the body carries the path the backend was handed, and a
    redirect assertion needs to see both halves of the same exchange. Neither
    stub emits a body line that could be mistaken for a header, and the status
    line starts `HTTP/` so it never reads as the echoed request line.

    `Location` is reported verbatim rather than through curl's
    `%{redirect_url}`, which resolves a relative header to an absolute URL --
    the distinction the rewrite is anchored on (see the Service annotation on
    vss-vios-ingress) would be exactly what got lost.
    """
    argv = ["curl", "-s", "-i", "-o", "-", "-w", "\n__STATUS__%{http_code}", "--max-time", "15"]
    if host:
        argv += ["-H", f"Host: {host}"]
    argv.append(f"{EDGE}{path}")
    body = run(argv).stdout

    status = ""
    backend = None
    received = None
    location = None
    for line in body.splitlines():
        if line.startswith("__STATUS__"):
            status = line[len("__STATUS__"):]
        elif line.startswith("Hostname:"):
            backend = line.split(":", 1)[1].strip()
        elif line.lower().startswith("location:"):
            location = line.split(":", 1)[1].strip()
        elif line.startswith("GET ") and " HTTP/" in line:
            received = line.split()[1]
    return {"status": status, "backend": backend, "received": received, "location": location}


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
    "/vst/storage/clip.mp4": ("vss-vios-ingress", "/vst/storage/clip.mp4"),
    # What makes the `^` on the storage rewrite load-bearing here.
    #
    # `replace-path` substitutes the WHOLE path when its regex matches
    # anywhere, so an unanchored `/storage/(.*)` turns any path containing the
    # prefix into /vst/storage/<tail> and discards everything ahead of it. The
    # row above cannot show that -- /vst/storage/clip.mp4 rewrites onto itself,
    # so anchored and unanchored agree. This one can: it selects the UI
    # catch-all, and the controller's ACL guard does not stop it, because the
    # rule IDs it matches on are built once per *Ingress* and stamped onto
    # every route of that Ingress (pkg/ingress/ingress.go). Drop the `^` and
    # the UI is handed /vst/storage/clip.mp4 instead.
    "/a/storage/clip.mp4": ("vss-agent-ui", "/a/storage/clip.mp4"),
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

# `requested -> (status, Location the caller must be handed)`, against a VST
# stub that redirects the way VST does. An alias whose first response bounces
# the caller onto the prefix it replaces is not an alias, so the /vios row is
# what makes /vios something a caller can migrate *to*.
#
# The other three rows pin the rewrite's blast radius rather than any change:
# every one of these mounts resolves to the same backend and the same Ingress,
# so the annotation is only correct if it leaves them exactly as they were.
VST_REDIRECT_EXPECTATIONS = {
    "/vios": ("301", "/vios/"),
    # The canonical prefix keeps its own redirect. A caller who asked for /vst
    # must not be moved onto the alias.
    "/vst": ("301", "/vst/"),
    # No redirect to rename, on either edge, which is why /storage needs no
    # rule of its own: nginx-vst.conf.template redirects the bare /vst prefix
    # and nothing else, so /vst/storage is served rather than redirected.
    "/storage": ("200", None),
    "/vios/storage": ("200", None),
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

    def assert_alias_redirects(self, chart: Path, sets: list[str], host: str = "") -> None:
        """Point the VST Service at the redirecting stub and check each Location.

        The chart supplies both halves: the Ingress that mounts /vios and the
        Service annotation that renames the Location on the way back. Only the
        Service's selector moves, so the controller's backend and rule IDs are
        the same ones the path assertions ran against.
        """
        manifest = render(chart, sets)
        self.assertIn("kind: Ingress", manifest, "no Ingress rendered")
        annotations = vst_service_annotations(chart, sets)

        apply_ingress(manifest)
        annotate_vst_service(annotations)
        select_vst_backend(REDIRECT_STUB)
        try:
            # Waiting here is for the endpoint swap landing in the controller,
            # which is the only transient state: until it does, the whoami pod
            # is still answering and every row reads as "no redirect at all".
            # Once a 301 appears the Location is final, so the assertions below
            # run once rather than being retried into the deadline.
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                if probe("/vst", host=host)["status"] == "301":
                    break
                time.sleep(3)
            else:
                self.fail(
                    "the redirecting VST stub never answered, so no conclusion "
                    "about the alias's Location header is possible"
                )

            failures = []
            for path, (status, expected) in sorted(VST_REDIRECT_EXPECTATIONS.items()):
                got = probe(path, host=host)
                if got["status"] != status or got["location"] != expected:
                    failures.append(
                        f"  GET {path}\n"
                        f"    expected HTTP {status}, Location: {expected!r}\n"
                        f"    got      HTTP {got['status']}, Location: {got['location']!r}"
                    )
            if failures:
                self.fail(
                    "the alias did not keep callers on the path they asked "
                    "for:\n" + "\n".join(failures)
                )
        finally:
            select_vst_backend("vss-vios-ingress")
            annotate_vst_service(dict.fromkeys(annotations))

    def test_warehouse_charts_route_every_mount_and_bare_root(self) -> None:
        """Each warehouse chart strips or replaces the prefix on every mount.

        The bare roots are the point: they route to a healthy backend either
        way, so a missing `^/X$` rule is invisible to anything that only checks
        whether a request was admitted.
        """
        for name in ("warehouse-2d-app", "warehouse-3d-app", "warehouse-mv3dt-app"):
            chart = HELM_ROOT / "industry-profiles/warehouse-operations" / name
            with self.subTest(chart=name):
                manifest = render(chart, WAREHOUSE_SETS)
                self.assertIn("kind: Ingress", manifest, "no Ingress rendered")
                self.assert_routes(manifest, WAREHOUSE_EXPECTATIONS)

    def test_canonical_route_table_routes_every_mount(self) -> None:
        """The shared table in services/common, as a developer profile renders it."""
        chart = HELM_ROOT / "developer-profiles/dev-profile-search"
        manifest = render(chart, CANONICAL_SETS)
        self.assertIn("kind: Ingress", manifest, "no Ingress rendered")
        self.assert_routes(manifest, CANONICAL_EXPECTATIONS, host=CANONICAL_HOST)

    def test_warehouse_alias_keeps_callers_on_vios(self) -> None:
        """A /vios redirect must land back on /vios, not on /vst."""
        self.assert_alias_redirects(
            HELM_ROOT / "industry-profiles/warehouse-operations/warehouse-2d-app",
            WAREHOUSE_SETS,
        )

    def test_canonical_alias_keeps_callers_on_vios(self) -> None:
        """The same, on the shared route table a developer profile renders."""
        self.assert_alias_redirects(
            HELM_ROOT / "developer-profiles/dev-profile-search",
            CANONICAL_SETS,
            host=CANONICAL_HOST,
        )

    def test_every_rewriting_mount_is_covered_by_an_expectation(self) -> None:
        """A probe table that misses a mount passes forever.

        Mirrors the "a lint that matches nothing passes" guard the static checks
        already carry: a new rewriting route has to gain a row above, or this
        fails and says which one is untested.
        """
        chart = HELM_ROOT / "industry-profiles/warehouse-operations/warehouse-2d-app"
        manifest = render(chart, WAREHOUSE_SETS)
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
