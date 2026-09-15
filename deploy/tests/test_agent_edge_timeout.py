# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The edge timeout on the backends that carry a whole agent turn.

`/api/vss-chat` proxies through the UI to the agent, and `/chat` reaches the
agent directly, so `bk_vss_ui` and `bk_vss_agent` hold the connection open for
an entire turn. A turn is at least as long as the tools it calls, and those
callees already carry their own generous timeouts (`bk_lvs_strip` 3600s,
`bk_llm_strip` 600s). Leaving the caller on the 120s default inverts that: the
edge cuts the answer off mid-turn and the truncated response reads to the
browser as a backend failure rather than as a slow-but-working request.

These tests hold that ordering in place:

  1. both agent-carrying backends set their own `timeout server` rather than
     inheriting the default;
  2. neither is shorter than any backend the agent waits on;
  3. neither is shorter than the Kubernetes edge already grants the same
     service, so a call Compose tolerates does not 504 under Helm (and the
     reverse).
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
HELM_ROOT = REPO_ROOT / "helm"
HAPROXY_TEMPLATE = (
    REPO_ROOT / "docker" / "services" / "infra" / "haproxy" / "haproxy.cfg.template"
)

# Backends that hold the connection for a whole agent turn.
AGENT_EDGE_BACKENDS = ("bk_vss_ui", "bk_vss_agent")

# Backends an agent turn can be waiting on when the edge decides to give up.
AGENT_CALLEES = ("bk_lvs_strip", "bk_llm_strip")

# Profiles whose Helm chart already raises the agent's own server timeout.
HELM_AGENT_TIMEOUT_PROFILES = ("dev-profile-lvs", "dev-profile-search")


def _seconds(value: str) -> int:
    match = re.fullmatch(r"(\d+)(ms|s|m|h)?", value.strip())
    if not match:
        raise AssertionError(f"cannot read a duration from {value!r}")
    amount, unit = int(match.group(1)), match.group(2) or "s"
    return amount * {"ms": 0, "s": 1, "m": 60, "h": 3600}[unit]


def _backend_body(name: str) -> str:
    found = re.search(
        rf"backend {name}\b(.*?)(?=\n(?:backend|frontend|listen)\b)",
        HAPROXY_TEMPLATE.read_text(),
        re.DOTALL,
    )
    if found is None:
        raise AssertionError(f"the Docker edge no longer defines {name}")
    return found.group(1)


def _timeout_server(name: str) -> str | None:
    found = re.search(r"timeout server\s+(\S+)", _backend_body(name))
    return found.group(1) if found else None


def _default_timeout_server() -> str:
    defaults = re.search(
        r"\ndefaults\b(.*?)(?=\n(?:backend|frontend|listen|resolvers)\b)",
        HAPROXY_TEMPLATE.read_text(),
        re.DOTALL,
    )
    assert defaults is not None, "the Docker edge has no defaults block"
    found = re.search(r"timeout server\s+(\S+)", defaults.group(1))
    assert found is not None, "the defaults block sets no timeout server"
    return found.group(1)


class AgentEdgeTimeoutTests(unittest.TestCase):
    """The backends carrying a turn outlive the tools that turn calls."""

    def test_agent_backends_set_their_own_timeout(self):
        for name in AGENT_EDGE_BACKENDS:
            with self.subTest(backend=name):
                self.assertIsNotNone(
                    _timeout_server(name),
                    f"{name} carries a whole agent turn but inherits the "
                    f"{_default_timeout_server()} default, so the edge gives up "
                    "mid-turn and the caller reads a truncated response as a "
                    "backend failure",
                )

    def test_agent_backends_outlive_the_tools_they_wait_on(self):
        for caller in AGENT_EDGE_BACKENDS:
            caller_timeout = _timeout_server(caller)
            self.assertIsNotNone(caller_timeout, f"{caller} carries no timeout server")
            for callee in AGENT_CALLEES:
                callee_timeout = _timeout_server(callee)
                self.assertIsNotNone(callee_timeout, f"{callee} carries no timeout server")
                with self.subTest(caller=caller, callee=callee):
                    self.assertGreaterEqual(
                        _seconds(caller_timeout),
                        _seconds(callee_timeout),
                        f"{caller} gives up before {callee} does, so a call the "
                        f"tool would have completed is reported as a failure",
                    )


class AgentEdgeHelmParityTests(unittest.TestCase):
    """Compose does not give the agent less headroom than Kubernetes does."""

    def _helm_agent_timeout(self, profile: str) -> str:
        values = yaml.safe_load(
            (HELM_ROOT / "developer-profiles" / profile / "values.yaml").read_text()
        )
        timeout = (values.get("agent") or {}).get("vss-agent", {}).get(
            "ingressTimeoutServer"
        )
        self.assertTrue(
            timeout,
            f"{profile} no longer raises the agent's ingressTimeoutServer; drop it "
            "from HELM_AGENT_TIMEOUT_PROFILES if that is deliberate",
        )
        return timeout

    def test_docker_edge_is_not_stingier_than_helm(self):
        for profile in HELM_AGENT_TIMEOUT_PROFILES:
            helm_timeout = self._helm_agent_timeout(profile)
            for name in AGENT_EDGE_BACKENDS:
                docker_timeout = _timeout_server(name)
                self.assertIsNotNone(docker_timeout, f"{name} carries no timeout server")
                with self.subTest(profile=profile, backend=name):
                    self.assertGreaterEqual(
                        _seconds(docker_timeout),
                        _seconds(helm_timeout),
                        f"{name} on the Docker edge is shorter than {profile} grants "
                        "vss-agent under Helm, so the same call behaves differently "
                        "on the two edges",
                    )


if __name__ == "__main__":
    unittest.main()
