# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The /llm ingress route, and its parity with the Docker edge.

The Docker edge publishes the in-deployment LLM at one address whatever GPU it
landed on (`bk_llm_strip` in services/infra/haproxy/haproxy.cfg.template), so a
caller reaches `<origin>/llm/v1/chat/completions` without knowing the placement.
These tests hold the Kubernetes side to the same shape:

  1. the mount and its strip rewrite come from the canonical route table, so all
     four developer profiles pick it up rather than one chart growing its own;
  2. the backend is the LLM NIM's own Service -- the same name the NIMService CR
     renders, release-name prefix included -- and not a node address or a port
     that has to be kept in step by hand;
  3. a profile with no in-deployment LLM NIM mounts nothing, rather than an
     Ingress pointed at a Service that was never created;
  4. the upstream timeout is at least the Docker edge's, and is scoped to this
     backend rather than imposed on every route on the origin.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import unittest
from functools import cache
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
HELM_ROOT = REPO_ROOT / "helm"
PROFILES_DIR = HELM_ROOT / "developer-profiles"
ROUTE_TABLE = HELM_ROOT / "services" / "common" / "templates" / "_ingress-routes.tpl"
NEMOTRON_CHART = (
    HELM_ROOT / "services" / "nims" / "charts" / "nemotron-3.5-lightning-30b-a3b"
)
HAPROXY_TEMPLATE = (
    REPO_ROOT / "docker" / "services" / "infra" / "haproxy" / "haproxy.cfg.template"
)

PROFILES = (
    "dev-profile-base",
    "dev-profile-alerts",
    "dev-profile-lvs",
    "dev-profile-search",
)
HOST = "10.0.0.1"
MOUNT = "/llm"

# The route is only useful if the prefix comes off: the NIM serves /v1/..., so
# <origin>/llm/v1/chat/completions has to arrive as /v1/chat/completions.
#
# Both sources are `^`-anchored and the bare-root form is `$`-terminated,
# because that is what the canonical table renders for every rewriting route
# (`vss.ingress.pathRewriteRows`). `replace-path` substitutes the WHOLE path
# wherever its regex matches, and the rules run in order against the previous
# rule's output, so an unanchored `/llm/(.*)` would fire on any path that merely
# contains the prefix and discard everything ahead of it.
EXPECTED_REWRITES = {f"^{MOUNT}/(.*)": r"/\1", f"^{MOUNT}$": "/"}

helm_required = unittest.skipUnless(
    shutil.which("helm"), "helm is not installed; chart rendering cannot be checked"
)


def _run(cmd: list[str], cwd: Path) -> str:
    out = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)
    if out.returncode != 0:
        raise AssertionError(f"{' '.join(cmd)} failed:\n{out.stderr}")
    return out.stdout


def setUpModule() -> None:
    """Profiles render only with their file:// dependencies unpacked."""
    if not shutil.which("helm"):
        return
    for profile in PROFILES:
        if not (PROFILES_DIR / profile / "charts").is_dir():
            _run(["helm", "dependency", "build", profile], cwd=PROFILES_DIR)


@cache
def _render(profile: str, overrides: tuple[str, ...] = ()) -> str:
    cmd = [
        "helm",
        "template",
        "vss",
        "./" + profile,
        "--set",
        "global.externalHost=" + HOST,
        # base and lvs ship it off by default; setting it on the others is a no-op.
        "--set",
        "vssIngress.enabled=true",
    ]
    for override in overrides:
        cmd.extend(["--set", override])
    return _run(cmd, cwd=PROFILES_DIR)


def _docs(profile: str, overrides: tuple[str, ...] = ()) -> list[dict]:
    return [d for d in yaml.safe_load_all(_render(profile, overrides)) if d]


def _ingresses(profile: str, overrides: tuple[str, ...] = ()) -> list[dict]:
    return [d for d in _docs(profile, overrides) if d.get("kind") == "Ingress"]


def _llm_backends(
    profile: str, overrides: tuple[str, ...] = ()
) -> list[tuple[str, int]]:
    """(service, port) for every /llm mount the profile renders."""
    found = []
    for ing in _ingresses(profile, overrides):
        for rule in ing["spec"]["rules"]:
            for entry in rule.get("http", {}).get("paths", []):
                if entry["path"] == MOUNT:
                    svc = entry["backend"]["service"]
                    found.append((svc["name"], svc["port"]["number"]))
    return found


def _rewrites(profile: str, overrides: tuple[str, ...] = ()) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for ing in _ingresses(profile, overrides):
        raw = (ing["metadata"].get("annotations") or {}).get(
            "haproxy.org/path-rewrite", ""
        )
        for line in raw.splitlines():
            if line.strip():
                src, dst = line.split()
                pairs[src] = dst
    return pairs


def _llm_nim_service(profile: str, overrides: tuple[str, ...] = ()) -> dict:
    """The LLM NIMService CR, whose name is the Service the operator creates."""
    for doc in _docs(profile, overrides):
        if doc.get("kind") == "NIMService" and "nemotron" in doc["metadata"]["name"]:
            return doc
    raise AssertionError(f"{profile}: no LLM NIMService rendered")


def _seconds(value: str) -> int:
    match = re.fullmatch(r"(\d+)(ms|s|m|h)?", value.strip())
    if not match:
        raise AssertionError(f"cannot read a duration from {value!r}")
    amount, unit = int(match.group(1)), match.group(2) or "s"
    return amount * {"ms": 0, "s": 1, "m": 60, "h": 3600}[unit]


class LlmRouteTableTests(unittest.TestCase):
    """The mount is defined once, in the table every profile reads."""

    def _rows(self) -> list[dict]:
        body = ROUTE_TABLE.read_text()
        start = body.index('{{- define "vss.ingress.routeTable" -}}')
        end = body.index("{{- end -}}", start)
        return yaml.safe_load(body[start:end].split("-}}", 1)[1])

    def test_table_defines_the_llm_mount_as_a_stripped_prefix(self):
        rows = [r for r in self._rows() if r["path"] == MOUNT]
        self.assertEqual(len(rows), 1, f"expected exactly one {MOUNT} row, got {rows}")
        self.assertEqual(rows[0]["key"], "llm")
        self.assertEqual(rows[0]["pathType"], "Prefix")
        self.assertEqual(rows[0]["rewrite"], "strip")

    def test_llm_precedes_the_ui_catch_all(self):
        paths = [r["path"] for r in self._rows()]
        self.assertLess(
            paths.index(MOUNT),
            paths.index("/"),
            "the UI catch-all has to render last or it swallows /llm",
        )


@helm_required
class LlmRouteRenderTests(unittest.TestCase):
    """Every profile mounts /llm at its own LLM NIM."""

    def test_all_profiles_mount_llm(self):
        for profile in PROFILES:
            with self.subTest(profile=profile):
                self.assertEqual(
                    len(_llm_backends(profile)),
                    1,
                    f"{profile} should mount {MOUNT} exactly once",
                )

    def test_backend_is_the_llm_nim_service_not_a_node_address(self):
        """The route follows the NIM, so it survives the model moving GPUs."""
        for profile in PROFILES:
            for overrides in ((), ("global.useReleaseNamePrefix=true",)):
                with self.subTest(profile=profile, overrides=overrides):
                    nim = _llm_nim_service(profile, overrides)
                    ((service, port),) = _llm_backends(profile, overrides)
                    self.assertEqual(service, nim["metadata"]["name"])
                    self.assertEqual(port, nim["spec"]["expose"]["service"]["port"])

    def test_prefix_is_stripped_before_the_nim_sees_it(self):
        for profile in PROFILES:
            with self.subTest(profile=profile):
                rewrites = _rewrites(profile)
                for src, want in EXPECTED_REWRITES.items():
                    self.assertEqual(rewrites.get(src), want)


@helm_required
class LlmRouteFailsClosedTests(unittest.TestCase):
    """No in-deployment LLM means no route, not a route to nothing."""

    # The umbrella and the leaf: /llm is the only route whose backend is a
    # subchart of a subchart, so both gates are worth pinning.
    CASES = ("nims.enabled=false", "nims.nemotron35.enabled=false")

    def test_disabling_the_nim_removes_the_mount(self):
        for profile in PROFILES:
            for case in self.CASES:
                with self.subTest(profile=profile, case=case):
                    self.assertEqual(_llm_backends(profile, (case,)), [])

    def test_disabling_the_nim_leaves_no_orphan_rewrite(self):
        """A rewrite for an unmounted prefix would reshape someone else's path."""
        for profile in PROFILES:
            for case in self.CASES:
                with self.subTest(profile=profile, case=case):
                    rewrites = _rewrites(profile, (case,))
                    for src in EXPECTED_REWRITES:
                        self.assertNotIn(src, rewrites)

    def test_a_remote_llm_alone_does_not_drop_the_route(self):
        """llmBaseUrl says where consumers point, not whether a NIM is deployed.

        The Docker edge draws the same line: bk_llm_strip exists whenever the LLM
        NIM container is up, whatever the agent was told to call.
        """
        backends = _llm_backends(
            "dev-profile-lvs", ("global.llmBaseUrl=https://llm.example/v1",)
        )
        self.assertEqual(len(backends), 1)


@helm_required
class LlmUpstreamTimeoutTests(unittest.TestCase):
    """A cold NIM's first token must not surface as a gateway error."""

    def test_timeout_is_scoped_to_the_llm_service(self):
        for profile in PROFILES:
            with self.subTest(profile=profile):
                annotations = _llm_nim_service(profile)["spec"]["expose"][
                    "service"
                ].get("annotations", {})
                self.assertIn("haproxy.org/timeout-server", annotations)

    def test_timeout_is_not_imposed_on_every_other_route(self):
        """An ingress-scoped timeout would raise the ceiling for all backends."""
        for profile in PROFILES:
            with self.subTest(profile=profile):
                for ing in _ingresses(profile):
                    annotations = ing["metadata"].get("annotations") or {}
                    self.assertNotIn("haproxy.org/timeout-server", annotations)

    def test_timeout_matches_the_docker_edge(self):
        docker = re.search(
            r"backend bk_llm_strip\b(.*?)(?=\n(?:backend|frontend|listen)\b)",
            HAPROXY_TEMPLATE.read_text(),
            re.DOTALL,
        )
        self.assertIsNotNone(docker, "the Docker edge no longer defines bk_llm_strip")
        edge = re.search(r"timeout server\s+(\S+)", docker.group(1))
        self.assertIsNotNone(edge, "bk_llm_strip carries no timeout server")

        chart = yaml.safe_load((NEMOTRON_CHART / "values.yaml").read_text())
        self.assertGreaterEqual(
            _seconds(chart["ingressTimeoutServer"]),
            _seconds(edge.group(1)),
            "the Kubernetes upstream timeout is shorter than the Docker edge's, so a "
            "cold start that Docker tolerates would 504 here",
        )


class LlmRouteDockerParityTests(unittest.TestCase):
    """The two edges publish the LLM at the same path."""

    def test_docker_edge_mounts_the_same_prefix(self):
        text = HAPROXY_TEMPLATE.read_text()
        self.assertIn(f"acl p_llm path {MOUNT}", text)
        self.assertIn(f"acl p_llm path_beg {MOUNT}/", text)
        self.assertIn("use_backend bk_llm_strip if h_main p_llm", text)

    def test_docker_edge_strips_the_same_prefix(self):
        text = HAPROXY_TEMPLATE.read_text()
        self.assertIn(rf"http-request replace-path ^{MOUNT}/(.*) /\1", text)
        self.assertIn(rf"http-request replace-path ^{MOUNT}$ /", text)


if __name__ == "__main__":
    unittest.main()
