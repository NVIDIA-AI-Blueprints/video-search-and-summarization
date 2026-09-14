#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Keep the /elasticsearch edge guard an allowlist, on both edges.

Elasticsearch runs with ``xpack.security.enabled: false``
(``services/infra/elk/elasticsearch/configs/elasticsearch.yml``), so anything
the ``/elasticsearch`` mount forwards executes **unauthenticated**. That makes
this guard the only authorisation control in front of the cluster, and it is
declared twice -- once in the Docker gateway's frontend
(``haproxy.cfg.template``) and once as a Service-scoped
``haproxy.org/backend-config-snippet`` on the Helm Elasticsearch Service. Both
have to enforce the same policy, and it has to stay an allowlist.

It was a denylist first, and the denylist could not be finished. Measured
against a live 9.4.4 deployment it named ``_bulk``, ``_update``, ``_close`` and
the cluster-admin prefixes, and let through ``_reindex``, ``_scripts/<id>``,
``_aliases`` (whose ``remove_index`` action deletes an index), ``_clone``,
``_split``, ``_shrink``, ``_rollover``, ``<index>/_mapping``, ``<index>/_doc``,
``<index>/_create/<id>`` and ``_tasks/<id>/_cancel`` -- and a percent-encoded
``%5Fcluster/settings`` walked straight past the admin pattern into the
backend. Three of those were reported as live bypasses of the shipped config:

    POST /elasticsearch/_bulk                    reached the cluster
    POST /elasticsearch/_scripts/s1              reached the cluster
    GET  /elasticsearch/%5Fcluster/settings      reached the cluster

Those three are the reason this lint exists, and they are asserted by name
below. A regression guard that does not catch the bugs that actually happened
is decorative.

**This asserts the effective policy, not the text of the rules.** It parses the
``acl``, ``http-request set-var``, ``http-request deny`` and ``use_backend``
directives out of each config, evaluates them the way HAProxy does -- same-name
ACLs OR together, rule conditions AND together, ``!`` negates, all
``http-request`` rules run before any ``use_backend`` -- and puts a corpus of
real request shapes through the result. A textual assertion is not good enough
here: the shapes are eleven regexes over five ACL names, and "the pattern is
still present" can hold while a one-character edit elsewhere makes it
unreachable. This project has already been bitten by exactly that with the
ingress harness, where a rule that read correctly was not the rule in force.

So the corpus is the specification, in three parts:

* the **denials**, headed by the three bypasses above, each with the status the
  policy owes it -- 403 where the verb is served but the operation is not
  allowed, 405 where the verb is wrong for the mount at all;
* the **permits**, because a guard that only checks denials is satisfied by
  ``deny all``: ``GET /`` is elasticsearch-py's product check and runs before
  every other call any Python caller makes, ``GET /_cat/indices`` is what
  ``vss configure`` probes with, ``GET /_cluster/health`` is the knowledge
  layer's health check, ``POST /<index>/_search`` is how everything searches,
  and ``PUT /vss-memory*/_doc/<id>`` is unified memory persisting a job;
* **parity**, since the same corpus runs through both edges and the verdicts
  have to match. A policy tightened on Docker and left open on Kubernetes is
  the failure the shared route table exists to prevent, and it would otherwise
  read as fixed.

The Helm snippet is evaluated twice per case, on the prefixed path and on the
path the mount's ``replace-path`` leaves behind, because that snippet is
backend-scoped and cannot know whether the strip has run yet. Its patterns make
the prefix optional for that reason, and both spellings have to reach the same
verdict.

Also asserted, cheaply: the copy of the guard block in
``skills/vss-build-vision-ai/references/services/ingress.md`` -- which tells an
agent composing a gateway to copy it verbatim -- still has the same directive
lines as the template. Directive lines only, so rewording the comments around
it is free; nothing else in CI compared the two, and a stale copy there
propagates a weakened guard into every stack built from that skill.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

TEMPLATE = ROOT / "deploy/docker/services/infra/haproxy/haproxy.cfg.template"
HELM_SERVICE = ROOT / "deploy/helm/services/infra/charts/elasticsearch/templates/service.yaml"
HELM_VALUES = ROOT / "deploy/helm/services/infra/charts/elasticsearch/values.yaml"
DOC = ROOT / "skills/vss-build-vision-ai/references/services/ingress.md"

MOUNT = "/elasticsearch"

# The ACL names that make up the guard on either edge. The Helm snippet prefixes
# everything with `vss_` because a backend-config-snippet shares a namespace
# with the controller's own rules.
GUARD_ACL = re.compile(r"^(?:vss_)?(?:p_elasticsearch|es_[a-z_0-9]+)$")
GUARD_VAR = re.compile(r"^txn\.(?:vss_)?es_ok$")

ACL = re.compile(r"^\s*acl\s+(?P<name>\S+)\s+(?P<rest>\S.*?)\s*$")
SET_VAR = re.compile(r"^\s*http-request\s+set-var\((?P<var>[^)]+)\)\s+\S+(?P<rest>.*?)\s*$")
DENY = re.compile(r"^\s*http-request\s+deny\s+status\s+(?P<status>\d+)(?P<rest>.*?)\s*$")
USE_BACKEND = re.compile(r"^\s*use_backend\s+(?P<backend>\S+)(?P<rest>.*?)\s*$")
IF_CLAUSE = re.compile(r"\bif\s+(?P<conditions>.*)$")
# `acl es_allowed var(txn.es_ok) -m found`
VAR_FOUND = re.compile(r"^var\((?P<var>[^)]+)\)\s+-m\s+found$")
# `memoryIndex: vss-memory` in the chart's values.
MEMORY_INDEX = re.compile(r"^\s*memoryIndex:\s*(?P<value>\S+)\s*$")

# What an ACL that is declared outside the guard block resolves to while the
# corpus runs. `h_main` is the gateway's Host allowlist -- every case below is a
# request from a declared origin, which is the only way to reach this mount at
# all, and check_gateway_host_acls.py owns that list.
GIVEN = {"h_main": True}


class Policy:
    """The guard's directives, evaluated the way HAProxy evaluates them."""

    def __init__(self, source: str) -> None:
        self.source = source
        # name -> [(match kind, argument)]. Same-name ACLs OR together.
        self.acls: dict[str, list[tuple[str, str]]] = {}
        # (kind, argument, conditions) in declaration order.
        self.rules: list[tuple[str, str, list[str]]] = []

    def add_acl(self, name: str, rest: str) -> None:
        found = VAR_FOUND.match(rest)
        if found:
            self.acls.setdefault(name, []).append(("var", found.group("var")))
            return
        kind, _, argument = rest.partition(" ")
        self.acls.setdefault(name, []).append((kind, argument.strip()))

    def add_rule(self, kind: str, argument: str, rest: str) -> None:
        clause = IF_CLAUSE.search(rest)
        conditions = clause.group("conditions").split() if clause else []
        self.rules.append((kind, argument, conditions))

    def acl_matches(self, name: str, method: str, path: str, variables: set[str]) -> bool:
        for kind, argument in self.acls[name]:
            if kind == "method" and method in argument.split():
                return True
            if kind == "path" and path == argument:
                return True
            if kind == "path_beg" and path.startswith(argument):
                return True
            if kind == "path_reg" and re.search(argument, path):
                return True
            if kind == "var" and argument in variables:
                return True
        return False

    def holds(self, condition: str, method: str, path: str, variables: set[str]) -> bool:
        negated = condition.startswith("!")
        name = condition.lstrip("!")
        if name in self.acls:
            value = self.acl_matches(name, method, path, variables)
        elif name in GIVEN:
            value = GIVEN[name]
        else:
            raise KeyError(name)
        return not value if negated else value

    def verdict(self, method: str, path: str) -> str:
        """``"403"``, ``"405"``, ``"forward"`` or ``"unrouted"`` for a request.

        HAProxy runs every ``http-request`` rule before it picks a backend, so
        the set-vars and denies are evaluated first regardless of where the
        ``use_backend`` sits in the file, and the answer does not depend on the
        textual order of the two groups.
        """
        variables: set[str] = set()
        routed = not any(kind == "use_backend" for kind, _, _ in self.rules)
        for kind, argument, conditions in self.rules:
            if kind == "use_backend":
                continue
            if all(self.holds(c, method, path, variables) for c in conditions):
                if kind == "set-var":
                    variables.add(argument)
                elif kind == "deny":
                    return argument
        for kind, argument, conditions in self.rules:
            if kind != "use_backend":
                continue
            if all(self.holds(c, method, path, variables) for c in conditions):
                routed = True
        return "forward" if routed else "unrouted"


def directive_lines(text: str) -> list[str]:
    """The guard's directive lines, whitespace-normalised, comments dropped."""
    kept: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        acl = ACL.match(line)
        if acl and GUARD_ACL.match(acl.group("name")):
            kept.append(stripped)
            continue
        set_var = SET_VAR.match(line)
        if set_var and GUARD_VAR.match(set_var.group("var")):
            kept.append(stripped)
            continue
        if (DENY.match(line) or USE_BACKEND.match(line)) and references_guard(stripped):
            kept.append(stripped)
    return kept


def references_guard(line: str) -> bool:
    """True when a deny or use_backend is conditioned on the guard's ACLs."""
    clause = IF_CLAUSE.search(line)
    conditions = clause.group("conditions").split() if clause else []
    return any(GUARD_ACL.match(c.lstrip("!")) for c in conditions)


def parse(text: str, source: str) -> Policy:
    """Build a Policy from the guard directives in ``text``."""
    policy = Policy(source)
    for line in directive_lines(text):
        acl = ACL.match(line)
        if acl and GUARD_ACL.match(acl.group("name")):
            policy.add_acl(acl.group("name"), acl.group("rest"))
            continue
        set_var = SET_VAR.match(line)
        if set_var:
            policy.add_rule("set-var", set_var.group("var"), set_var.group("rest"))
            continue
        deny = DENY.match(line)
        if deny:
            policy.add_rule("deny", deny.group("status"), deny.group("rest"))
            continue
        use_backend = USE_BACKEND.match(line)
        if use_backend:
            policy.add_rule("use_backend", use_backend.group("backend"), use_backend.group("rest"))
    return policy


def memory_index(values: Path) -> str:
    """The chart's ``ingressGuard.memoryIndex``, interpolated into the snippet."""
    for line in values.read_text().splitlines():
        match = MEMORY_INDEX.match(line)
        if match:
            return match.group("value")
    return ""


def strip_mount(path: str) -> str:
    """The path the mount's ``replace-path`` rules hand the backend.

    ``^/elasticsearch/(.*) /\\1`` and ``^/elasticsearch$ /``, so
    ``/elasticsearch/_bulk`` becomes ``/_bulk`` and ``/elasticsearch``
    becomes ``/``.
    """
    if path == MOUNT:
        return "/"
    if path.startswith(MOUNT + "/"):
        return path[len(MOUNT) :]
    return path


# (method, path, expected verdict, why this case is here). The `why` is printed
# on failure, because "POST /_bulk is allowed now" is only actionable next to
# the reason it must not be.
DENIALS = [
    (
        "POST",
        "/elasticsearch/_bulk",
        "403",
        "the bulk write API: one request rewrites or deletes documents across "
        "every index. It passed the denylist this allowlist replaced, and was "
        "reported as a live bypass of the shipped config",
    ),
    (
        "POST",
        "/elasticsearch/_scripts/s1",
        "403",
        "stores a Painless script under a name any later query can call. It "
        "passed the denylist this allowlist replaced, and was reported as a "
        "live bypass of the shipped config",
    ),
    (
        "GET",
        "/elasticsearch/%5Fcluster/settings",
        "403",
        "cluster settings reached through a percent-encoded underscore. The "
        "denylist's admin pattern matched the literal spelling only, so this "
        "walked past it into the backend; it was reported as a live bypass. "
        "An index-expression segment must stay spelled out as lowercase "
        "characters that exclude `%`, or every admin path comes back encoded",
    ),
    (
        "POST",
        "/elasticsearch/%5Fbulk",
        "403",
        "the same encoding trick on the bulk API",
    ),
    (
        "POST",
        "/elasticsearch/_reindex",
        "403",
        "copies one index over another; unnamed by the denylist",
    ),
    (
        "POST",
        "/elasticsearch/_aliases",
        "403",
        "its `remove_index` action deletes an index outright",
    ),
    (
        "POST",
        "/elasticsearch/mdx-raw-1/_doc",
        "403",
        "creates a document with a generated id -- a write on a read-only mount",
    ),
    (
        "POST",
        "/elasticsearch/mdx-raw-1/_close",
        "403",
        "closes an index, taking it out of service",
    ),
    (
        "POST",
        "/elasticsearch/mdx-raw-1/_update_by_query",
        "403",
        "rewrites every document a query matches",
    ),
    (
        "GET",
        "/elasticsearch/_cluster/settings",
        "403",
        "cluster administration: only `_cluster/health` is on the allowlist",
    ),
    (
        "GET",
        "/elasticsearch/_nodes",
        "403",
        "node-level topology, outside the read shapes any caller here needs",
    ),
    (
        "POST",
        "/elasticsearch/_search/scroll",
        "403",
        "allocates server-side state that only DELETE releases, and nothing "
        "in the tree calls it. Deliberately absent from the query allowlist",
    ),
    (
        "POST",
        "/elasticsearch/_sql",
        "403",
        "a second query language, deliberately absent from the query allowlist",
    ),
    (
        "POST",
        "/elasticsearch/_tasks/t1/_cancel",
        "403",
        "cancels another caller's running task; unnamed by the denylist",
    ),
    (
        "PUT",
        "/elasticsearch/mdx-raw-1/_doc/1",
        "405",
        "a document write outside the unified-memory index. The one PUT this "
        "mount serves is scoped to vss-memory* so the exception cannot widen "
        "onto another index",
    ),
    (
        "PUT",
        "/elasticsearch/vss-memory/_settings",
        "405",
        "index settings on the memory index: the PUT exception covers "
        "documents, not configuration",
    ),
    (
        "DELETE",
        "/elasticsearch/mdx-raw-1",
        "405",
        "deletes an index. DELETE is not a verb this mount serves at all",
    ),
    (
        "PATCH",
        "/elasticsearch/mdx-raw-1/_doc/1",
        "405",
        "a verb outside the mount's set, which must fail on the verb rather "
        "than fall through to a shape check",
    ),
]

PERMITS = [
    (
        "GET",
        "/elasticsearch",
        "elasticsearch-py issues GET / as its product check before any other "
        "call, so every Python caller in the tree stops working without it",
    ),
    ("GET", "/elasticsearch/", "the same product check with the trailing slash"),
    (
        "GET",
        "/elasticsearch/_cat/indices",
        "what `vss configure` probes the mount with",
    ),
    (
        "GET",
        "/elasticsearch//_cat/indices",
        "a client that joined its base URL and path with a spare slash",
    ),
    (
        "GET",
        "/elasticsearch/_cluster/health",
        "the knowledge layer's es_caption health check",
    ),
    ("GET", "/elasticsearch/mdx-raw-1", "an index existence check"),
    ("HEAD", "/elasticsearch/mdx-raw-1", "the same check without a body"),
    ("GET", "/elasticsearch/mdx-raw-1/_mapping", "per-index metadata"),
    ("GET", "/elasticsearch/vss-memory/_doc/j1", "a single-document read"),
    (
        "POST",
        "/elasticsearch/mdx-raw-1/_search",
        "how es_caption and everything else searches -- the query rides in the "
        "body, which is the only reason POST is served on this mount",
    ),
    (
        "POST",
        "/elasticsearch/mdx-a,mdx-b/_search",
        "a multi-index search: an index expression can carry commas and globs",
    ),
    ("POST", "/elasticsearch/_msearch", "a cluster-wide multi-search"),
    ("GET", "/elasticsearch/_search", "the same query endpoint over GET"),
    (
        "PUT",
        "/elasticsearch/vss-memory/_doc/j1%23caption%23r1",
        "unified memory persisting a job. The id carries `#` encoded as %23, "
        "so a document-id segment has to stay unrestricted",
    ),
    (
        "PUT",
        "/elasticsearch/vss-memory-dev/_doc/j1",
        "the same write against a --memory-index override's suffixed variant",
    ),
    (
        "OPTIONS",
        "/elasticsearch/mdx-raw-1/_search",
        "a browser preflight, which carries no body and mutates nothing",
    ),
]


def check_policy(policy: Policy, paths: list[str]) -> list[str]:
    """Run the corpus through one edge's policy.

    ``paths`` says how to spell each case for this edge: the Docker frontend
    sees the request as sent, the Helm snippet may see it before or after the
    mount's strip, and both spellings have to agree.
    """
    failures: list[str] = []
    for method, path, expected, why in DENIALS:
        for spelling in paths:
            got = policy.verdict(method, spelling(path))
            if got == expected:
                continue
            reached = "reaches Elasticsearch" if got == "forward" else f"is denied {got}"
            failures.append(
                f"{policy.source}: {method} {spelling(path)} {reached}, expected "
                f"{expected}. {why}."
            )
    for method, path, why in PERMITS:
        for spelling in paths:
            got = policy.verdict(method, spelling(path))
            if got != "forward":
                failures.append(
                    f"{policy.source}: {method} {spelling(path)} is denied {got} but has "
                    f"to be served -- {why}. The allowlist is now too narrow for a "
                    f"caller in this repository, which is a broken deployment rather "
                    f"than a tightened guard."
                )
    return failures


def check_parity(docker: Policy, helm: Policy) -> list[str]:
    """The two edges must answer the corpus identically."""
    failures: list[str] = []
    cases = [(m, p) for m, p, _, _ in DENIALS] + [(m, p) for m, p, _ in PERMITS]
    for method, path in cases:
        left = docker.verdict(method, path)
        right = helm.verdict(method, path)
        if left != right:
            failures.append(
                f"{docker.source} answers {method} {path} with {left} and "
                f"{helm.source} answers {right}. The two edges enforce one policy: "
                f"a mount tightened on one and left open on the other reads as fixed "
                f"and is not."
            )
    return failures


def check_shape(policy: Policy, *, needs_route: bool) -> list[str]:
    """A lint that parses nothing passes forever, so assert it found a guard."""
    failures: list[str] = []
    if not policy.acls:
        failures.append(
            f"{policy.source}: no /elasticsearch guard ACLs were recognised, so this "
            f"lint is not checking anything. Either the guard was removed -- which "
            f"leaves an unauthenticated cluster on a public mount -- or the ACLs were "
            f"renamed out of the `es_*` / `vss_es_*` namespace this reads."
        )
        return failures
    denies = [r for r in policy.rules if r[0] == "deny"]
    if not denies:
        failures.append(
            f"{policy.source}: the guard declares ACLs but no `http-request deny`, so "
            f"nothing is refused. An allowlist that does not deny is a comment."
        )
    if needs_route and not any(r[0] == "use_backend" for r in policy.rules):
        failures.append(
            f"{policy.source}: no `use_backend` gated on the guard's ACLs, so the "
            f"corpus cannot distinguish a served request from an unrouted one."
        )
    if any("allow" in r[0] for r in policy.rules):
        failures.append(
            f"{policy.source}: uses `http-request allow`, which also skips the "
            f"backend's replace-path rules -- the request would reach Elasticsearch "
            f"with the /elasticsearch prefix still on it. Set a variable and deny on "
            f"its absence instead."
        )
    return failures


def check_doc_copy(template: Path, doc: Path) -> list[str]:
    """The skill's copy-verbatim block must carry the template's directives.

    Directive lines only: the prose and comments around it are free to differ,
    and do.
    """
    if not doc.is_file():
        return [f"{display(doc)}: missing, so the documented guard cannot be compared"]
    wanted = directive_lines(template.read_text())
    documented = directive_lines(doc.read_text())
    if not documented:
        return [
            f"{display(doc)}: carries no /elasticsearch guard directives. It tells an "
            f"agent to copy the guard verbatim, so a stack built from that skill would "
            f"be composed without one."
        ]
    if documented == wanted:
        return []
    missing = [line for line in wanted if line not in documented]
    extra = [line for line in documented if line not in wanted]
    detail = []
    if missing:
        detail.append("missing " + "; ".join(missing))
    if extra:
        detail.append("stale " + "; ".join(extra))
    if not detail:
        detail.append("the directives are in a different order")
    return [
        f"{display(doc)}: the documented guard no longer matches "
        f"{display(template)} -- {', '.join(detail)}. That block is copied verbatim "
        f"into composed gateways, so it propagates whichever version it holds."
    ]


def display(path: Path) -> str:
    """Path relative to the repository root where possible, for diagnostics."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path, default=TEMPLATE)
    parser.add_argument("--helm-service", type=Path, default=HELM_SERVICE)
    parser.add_argument("--helm-values", type=Path, default=HELM_VALUES)
    parser.add_argument("--doc", type=Path, default=DOC)
    args = parser.parse_args(argv)

    failures: list[str] = []

    docker = parse(args.template.read_text(), display(args.template))
    failures += check_shape(docker, needs_route=True)

    index = memory_index(args.helm_values)
    if not index:
        failures.append(
            f"{display(args.helm_values)}: no `memoryIndex:` found, so the Helm "
            f"snippet's memory-write exception cannot be resolved and its policy "
            f"cannot be evaluated."
        )
        helm = None
    else:
        rendered = args.helm_service.read_text().replace("{{ $mem }}", index)
        helm = parse(rendered, display(args.helm_service))
        failures += check_shape(helm, needs_route=False)

    if not failures:
        failures += check_policy(docker, [lambda path: path])
        if helm is not None:
            failures += check_policy(helm, [lambda path: path, strip_mount])
            failures += check_parity(docker, helm)

    failures += check_doc_copy(args.template, args.doc)

    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1

    print(
        f"Elasticsearch allowlist lint passed "
        f"({len(DENIALS)} denials, {len(PERMITS)} permits, on both edges; "
        f"memory index {index!r}; documented copy current)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
