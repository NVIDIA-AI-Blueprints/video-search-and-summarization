#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Keep the gateway's long server timeouts from silently reverting to the default.

``haproxy.cfg.template`` sets ``timeout server 120s`` in ``defaults``, and a
handful of backends override it because the work behind them outlasts that: a
first token off a cold NIM, a caption on a loaded RT-VLM, a summarization run.
Every one of those overrides is load-bearing and none of them is asserted
anywhere, which is the same shape as the two defects this branch already
shipped -- the ingress verifier that passed behind vendored Helm deps, and the
endpoint lint that passed because the hosts it scanned were missing. A guard
that asserts nothing looks exactly like a guard.

Why this is not testable any other way: a 504 at 120.1s is indistinguishable
from a model failure to every caller, and no CI job runs an inference long
enough to produce one. The value was proven by hand once -- a 150s request
through ``/rtvi-vlm`` returned 200 while an unchanged ``/rtvi-cv`` control
returned 504 at 120.1s -- and a hand proof does not survive a rebase.

Deliberately NOT a check that ``bk_rtvi_vlm_strip`` says ``600s``. Freezing an
integer would fail a legitimate 900s and would still miss the failure that
matters, which is a slow route inheriting a short timeout. So:

1. **The threshold is read, not written.** Every registered backend has to
   carry a ``timeout server`` strictly greater than whatever ``defaults`` says.
   Raising the default, or raising one backend from 600s to 900s, passes; a
   revert to the default, a deletion, or a lowering fails.
2. **Growth is deliberate.** A backend that carries a raised timeout without an
   entry in :data:`SLOW_BACKENDS` fails, so the registry cannot fall behind the
   file it describes.
3. **Aliases cannot drift from what they alias.** Backends that forward to the
   same upstream must carry the same effective timeout. This rule is derived
   entirely from the config -- it names no service -- and it is what catches the
   copy-paste case: ``/video-summarization`` is a second front door onto
   ``lvs-server``, and an alias that 504s where the original succeeds is worse
   than no alias. Any future alias of ``/llm`` or ``/rtvi-vlm`` is covered the
   moment it is added.
4. **A route left on the default is a decision, not an omission.**
   :data:`DEFAULT_TIMEOUT_BACKENDS` records the ones examined and left alone,
   with the evidence, and fails if one silently acquires an override -- which
   forces whoever raises it to say why here.
5. **Both edges or neither.** Docker and Kubernetes publish the same routes on
   one origin, and this repo treats that parity as an invariant. The Helm side
   expresses the same thing as ``haproxy.org/timeout-server`` on the backend's
   Service, fed by ``ingressTimeoutServer``; a raised Docker timeout whose Helm
   counterpart is unset means the same call succeeds on Compose and 504s on
   Kubernetes.

On ``/rtvi-cv``, which looks like an omission and is not: it is reached through
the gateway by a client that bounds itself at ``read=120.0``
(``search_core/clients/rtvi_cv_embed.py``) for text-embedding calls, plus
RT-CV's ``/api/v1/stream/add`` control plane. The edge default is 120s, so it
cannot truncate a request the caller would still be waiting for, and raising it
would change no observable outcome. Its Helm chart does set 3600s, and that is
not a contradiction: the HAProxy ingress controller's own default is 50s, well
under the client's 120s bound, so Kubernetes has to raise it to reach the
ceiling Compose already has.

``/rtvi-embed`` was recorded here for the same reason and it was wrong: text
embedding is not its only gateway-routed caller. On the search profile the
agent's ``COSMOS_EMBED_ENDPOINT`` is ``${VSS_GATEWAY_ORIGIN}/rtvi-embed``, and
``video_ingest`` posts ``/v1/generate_video_embeddings`` there with a 600s
client timeout. It is in :data:`SLOW_BACKENDS` now.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

TEMPLATE = ROOT / "deploy/docker/services/infra/haproxy/haproxy.cfg.template"
HELM = ROOT / "deploy/helm"

#: Backends that must carry a ``timeout server`` above the ``defaults`` value,
#: and the Helm Service that has to say the same thing. ``helm`` is the chart
#: directory whose ``ingressTimeoutServer`` feeds
#: ``haproxy.org/timeout-server``, and ``key`` is the name a developer profile
#: uses to override it -- both, because a chart may ship the raise as its own
#: default (video-summarization) or leave it to the profiles that enable the
#: service (rtvi-vlm).
SLOW_BACKENDS = {
    "bk_llm_strip": {
        "why": "first token off a cold NIM outlasts the default; a 504 there reads as a model failure",
        "helm": "services/nims/charts/nemotron-3.5-lightning-30b-a3b",
        "key": "nemotron-3.5-lightning-30b-a3b",
    },
    "bk_rtvi_vlm_strip": {
        "why": (
            "a caption on a cold or loaded RT-VLM outlasts the default, and this route now carries "
            "the agent, alert-bridge and lvs-server, which previously called rtvi-vlm:8000 with no "
            "proxy timeout at all"
        ),
        "helm": "services/rtvi/charts/rtvi-vlm",
        "key": "vss-rtvi-vlm",
    },
    "bk_lvs_strip": {
        "why": "a summarization run outlasts the default; the CLI bounds the wait itself",
        "helm": "services/video-summarization",
        "key": "vss-summarization",
    },
    "bk_video_summarization_strip": {
        "why": "the /lvs alias; a summarization must not 504 here while succeeding on /lvs",
        "helm": "services/video-summarization",
        "key": "vss-summarization",
    },
    "bk_vss_agent": {
        "why": (
            "POST /api/v1/videos/<sensor>/complete blocks on RT-Embed generation for the agent's "
            "own 600s client timeout plus the VST calls around it, and the ingest contract in "
            "skills/operations/vss-search-archive bounds that request at 900s -- above the "
            "default, so the edge is what truncates it"
        ),
        "helm": "services/agent/charts/agent",
        "key": "vss-agent",
    },
    "bk_rtvi_embed_strip": {
        "why": (
            "the agent's COSMOS_EMBED_ENDPOINT is the gateway on the search profile, and "
            "video_ingest posts /v1/generate_video_embeddings there with a 600s client timeout, "
            "blocking until generation completes"
        ),
        "helm": "services/rtvi/charts/rtvi-embed",
        "key": "vss-rtvi-embed",
    },
    "bk_vst_storage_api_direct": {
        "why": "uploads of whole videos outlast the default",
        # VST is published through the VIOS nginx, not a Service annotation, and
        # that config already sets proxy_read_timeout 3600s on the upload and
        # download locations (vios-ingress/configs/nginx-vst.conf.template).
        "helm": None,
        "key": None,
    },
}

#: Routes fronting a model that are deliberately left on the ``defaults``
#: timeout, with the evidence. Checked, so raising one is an edit here too.
DEFAULT_TIMEOUT_BACKENDS = {
    "bk_rtvi_cv_strip": (
        "gateway-routed traffic is /api/v1/stream/add plus text embedding through "
        "RTVICVEmbedClient, which sets read=120.0 -- the same ceiling the edge default "
        "already gives, so raising it truncates nothing"
    ),
}

BACKEND = re.compile(r"^backend\s+(?P<name>\S+)\s*$")
TIMEOUT_SERVER = re.compile(r"^\s*timeout\s+server\s+(?P<value>\S+)\s*$")
SERVER = re.compile(r"^\s*server\s+\S+\s+(?P<address>\"[^\"]+\"|\S+)")
DEFAULTS = re.compile(r"^defaults\s*$")
INGRESS_TIMEOUT = re.compile(r"^(?P<indent>\s*)ingressTimeoutServer:\s*(?P<value>\S.*?)\s*$")
YAML_KEY = re.compile(r"^(?P<indent>\s*)(?P<key>[\w.-]+):\s*(?P<rest>.*?)\s*$")

#: HAProxy's own unit table (docs.haproxy.org section 2.4). A bare number is
#: MILLISECONDS, which is why this cannot be an int() call: `timeout server 600`
#: is 0.6s, not ten minutes, and would otherwise read as a raise.
UNITS = {"us": 1e-6, "ms": 1e-3, "s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}


def seconds(value: str) -> float | None:
    """An HAProxy time value in seconds, or None if it is not one."""
    match = re.fullmatch(r"(?P<number>\d+)(?P<unit>us|ms|s|m|h|d)?", value.strip().strip('"'))
    if not match:
        return None
    return int(match.group("number")) * UNITS[match.group("unit") or "ms"]


def display(path: Path) -> str:
    """Path relative to the repository root where possible, for diagnostics."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def parse_template(path: Path) -> tuple[float | None, int, dict[str, dict[str, object]]]:
    """The ``defaults`` server timeout, its line, and one record per backend."""
    default: float | None = None
    default_line = 0
    backends: dict[str, dict[str, object]] = {}

    section: str | None = None
    current: str | None = None
    for number, line in enumerate(path.read_text().splitlines(), start=1):
        if DEFAULTS.match(line):
            section, current = "defaults", None
            continue
        backend_match = BACKEND.match(line)
        if backend_match:
            section = current = backend_match.group("name")
            backends[current] = {"line": number, "timeout": None, "timeout_line": 0, "upstream": None}
            continue
        if line and not line[0].isspace():
            section = current = None
            continue

        timeout_match = TIMEOUT_SERVER.match(line)
        if timeout_match:
            if section == "defaults":
                default, default_line = seconds(timeout_match.group("value")), number
            elif current is not None:
                backends[current]["timeout"] = seconds(timeout_match.group("value"))
                backends[current]["timeout_line"] = number
            continue

        server_match = SERVER.match(line)
        if server_match and current is not None:
            backends[current]["upstream"] = server_match.group("address").strip('"')

    return default, default_line, backends


def helm_declarations(root: Path) -> tuple[dict[str, float | None], dict[str, list[tuple[Path, float | None]]]]:
    """What the Helm side declares: chart defaults, and per-profile enablements.

    Line-oriented rather than a YAML parse, so this guard needs no PyYAML in the
    job that runs it. Two shapes matter and no others: the key at the top level
    of a chart's own ``values.yaml``, which is that chart's default, and the key
    nested under a service name in a profile, which overrides it.

    Returns ``(chart_defaults, enabled)``. ``chart_defaults`` maps a chart
    directory name to its own value, ``None`` where the chart declares the knob
    and leaves it empty. ``enabled`` maps a service key to one entry per profile
    that switches it on, carrying that profile's override or ``None`` when it
    sets none -- which is the case that inherits the chart default, and the case
    a max-of-all-declarations check would miss entirely.

    **Declarations are keyed by their full block path, not by the block's own
    name, and an enablement is read through its ancestors.** Both halves of that
    were wrong before and the second one silently:

    * Profiles nest a service under its umbrella chart (``agent.vss-agent``,
      ``rtvi.vss-rtvi-vlm``), and they also nest sub-feature blocks that carry
      an ``enabled`` of their own -- ``waitForDependencies``, ``waitForKafka``,
      ``engineCache``, ``init``, ``topicJob``. Keying by bare name puts a
      service's ``ingressTimeoutServer`` and some unrelated block's ``enabled``
      into the same record whenever the names collide within one file. Keying by
      path cannot.
    * A subchart's ``enabled: true`` means nothing when its umbrella is switched
      off: ``warehouse-2d-app`` sets ``agent.enabled: false`` and still carries
      ``agent.vss-va-mcp.enabled: true`` underneath it. Read without the
      ancestor, that profile looks like it installs a service it does not, and
      this guard would demand an ``ingressTimeoutServer`` for a Service that is
      never created -- a failure a developer cannot act on. So an enablement
      counts only when no enclosing block declares ``enabled: false``.

    Sub-feature blocks are still recorded under their own name. That is
    harmless -- nothing looks them up, because :data:`SLOW_BACKENDS` keys are
    Helm service names -- and it is cheaper than teaching this parser which keys
    name a subchart, which it has no way to know without reading every
    ``Chart.yaml``.
    """
    chart_defaults: dict[str, float | None] = {}
    enabled: dict[str, list[tuple[Path, float | None]]] = {}

    for path in sorted(root.rglob("values.yaml")):
        # Declarations made *directly* on a block, keyed by that block's path
        # from the root of the file, e.g. ("agent", "vss-agent").
        declared_enabled: dict[tuple[str, ...], bool] = {}
        declared_timeout: dict[tuple[str, ...], float | None] = {}
        stack: list[tuple[int, str]] = []

        for line in path.read_text().splitlines():
            timeout_match = INGRESS_TIMEOUT.match(line)
            if timeout_match:
                indent = len(timeout_match.group("indent"))
                value = seconds(timeout_match.group("value"))  # None for `""`
                block = tuple(name for level, name in stack if level < indent)
                if block:
                    declared_timeout[block] = value
                else:
                    chart_defaults[path.parent.name] = value
                continue

            key_match = YAML_KEY.match(line)
            if not key_match:
                continue
            indent = len(key_match.group("indent"))
            while stack and stack[-1][0] >= indent:
                stack.pop()
            stack.append((indent, key_match.group("key")))
            if key_match.group("key") == "enabled" and key_match.group("rest") in ("true", "false"):
                block = tuple(name for level, name in stack[:-1] if level < indent)
                if block:
                    declared_enabled[block] = key_match.group("rest") == "true"

        for block, is_on in declared_enabled.items():
            if not is_on:
                continue
            if any(declared_enabled.get(block[:depth]) is False for depth in range(1, len(block))):
                continue  # an enclosing block switches the whole subtree off
            enabled.setdefault(block[-1], []).append((path, declared_timeout.get(block)))

    return chart_defaults, enabled


def scan(template: Path, helm: Path = HELM) -> list[str]:
    """Diagnostics for the raised-timeout contract across both edges."""
    failures: list[str] = []
    where = display(template)
    default, default_line, backends = parse_template(template)

    # 0. A lint that recognised nothing passes forever.
    if default is None:
        failures.append(
            f"{where}: no `timeout server` in the `defaults` section, so this lint has no "
            "threshold to compare against and is not checking anything"
        )
        return failures
    if not backends:
        failures.append(f"{where}: no backends recognised, so this lint is not checking anything")
        return failures

    if not SLOW_BACKENDS:
        failures.append(
            f"{where}:{default_line}: SLOW_BACKENDS is empty, so nothing is held above the "
            f"{default:g}s default and this lint asserts nothing"
        )
        return failures

    raised = {
        name: record
        for name, record in backends.items()
        if isinstance(record["timeout"], float) and record["timeout"] > default
    }

    # 1. Every registered backend beats the default.
    for name, entry in sorted(SLOW_BACKENDS.items()):
        record = backends.get(name)
        if record is None:
            failures.append(
                f"{where}: backend {name} is registered as fronting long-running work but no "
                "longer exists in the template. If the route was retired, drop its "
                "SLOW_BACKENDS entry in the same commit"
            )
            continue
        value = record["timeout"]
        if value is None:
            failures.append(
                f"{where}:{record['line']}: backend {name} has no `timeout server`, so it "
                f"inherits the {default:g}s default -- but {entry['why']}. A request that "
                "outlasts it is cut off with a 504 the caller cannot tell from a model "
                f"failure. Add `timeout server` above {default:g}s"
            )
        elif value <= default:
            failures.append(
                f"{where}:{record['timeout_line']}: backend {name} sets `timeout server` to "
                f"{value:g}s, at or below the {default:g}s default on line {default_line} -- "
                f"but {entry['why']}"
            )

    # 2. The registry cannot fall behind the file.
    for name, record in sorted(raised.items()):
        if name in SLOW_BACKENDS:
            continue
        failures.append(
            f"{where}:{record['timeout_line']}: backend {name} raises `timeout server` to "
            f"{record['timeout']:g}s but is not in SLOW_BACKENDS. Add it with the reason the "
            "work behind it outlasts the default, so the value is asserted rather than just "
            "present, and so the Helm side is checked to match"
        )

    # 3. Aliases cannot drift from what they alias. Derived from the config: two
    #    backends forwarding to the same upstream are two front doors onto one
    #    service, and they must behave the same.
    upstreams: dict[str, list[tuple[str, float]]] = {}
    for name, record in backends.items():
        upstream = record["upstream"]
        if not isinstance(upstream, str):
            continue
        value = record["timeout"]
        upstreams.setdefault(upstream, []).append((name, value if isinstance(value, float) else default))
    for upstream, sharers in sorted(upstreams.items()):
        if len(sharers) < 2 or len({value for _, value in sharers}) == 1:
            continue
        detail = ", ".join(f"{name} {value:g}s" for name, value in sorted(sharers))
        highest = max(value for _, value in sharers)
        lagging = sorted(name for name, value in sharers if value < highest)
        failures.append(
            f"{where}: {upstream} is fronted by backends with different server timeouts "
            f"({detail}). These are aliases of one service, so a call that succeeds on one "
            f"prefix 504s on another; {', '.join(lagging)} would be the one that fails"
        )

    # 4. A route left on the default stays a recorded decision.
    for name, why in sorted(DEFAULT_TIMEOUT_BACKENDS.items()):
        record = backends.get(name)
        if record is None:
            failures.append(
                f"{where}: backend {name} is recorded as deliberately left on the default but "
                "no longer exists in the template; drop its DEFAULT_TIMEOUT_BACKENDS entry"
            )
        elif record["timeout"] is not None:
            failures.append(
                f"{where}:{record['timeout_line']}: backend {name} now sets its own `timeout "
                f"server`, but DEFAULT_TIMEOUT_BACKENDS records it as left on the default "
                f"because {why}. Move it to SLOW_BACKENDS with the evidence that changed"
            )

    failures += scan_helm(helm, backends)
    return failures


def scan_helm(helm: Path, backends: dict[str, dict[str, object]]) -> list[str]:
    """The Kubernetes edge has to raise the same backends the Docker edge does."""
    if not helm.is_dir():
        return [f"{display(helm)}: not a directory, so Docker/Helm timeout parity is unchecked"]

    chart_defaults, enabled = helm_declarations(helm)
    if not chart_defaults and not enabled:
        return [
            f"{display(helm)}: no ingressTimeoutServer declarations found at all, so the "
            "Docker/Helm timeout parity half of this lint is not checking anything"
        ]

    failures: list[str] = []
    for name, entry in sorted(SLOW_BACKENDS.items()):
        record = backends.get(name)
        if record is None or not isinstance(record["timeout"], float):
            continue  # already reported by rule 1
        chart = entry["helm"]
        if chart is None:
            continue  # not published through a Service annotation; reason is in the entry
        docker = float(record["timeout"])
        chart_dir = helm / str(chart)
        if not chart_dir.is_dir():
            failures.append(
                f"{display(chart_dir)}: SLOW_BACKENDS maps {name} to this chart, which does not "
                "exist. The Docker/Helm parity for this timeout is unchecked until the mapping "
                "is corrected"
            )
            continue

        if chart_dir.name not in chart_defaults:
            failures.append(
                f"{display(chart_dir)}/values.yaml: the Docker edge raises {name} to "
                f"{docker:g}s, but this chart declares no ingressTimeoutServer, so nothing can "
                "annotate haproxy.org/timeout-server onto its Service. The same call would "
                "succeed on Compose and 504 on Kubernetes under the ingress controller's own "
                "default"
            )
            continue

        # A profile that switches the service on decides the effective value:
        # its own override, or the chart default when it sets none. Checked per
        # profile rather than as a maximum, because one profile raising the
        # timeout does nothing for a sibling that leaves it empty.
        default = chart_defaults[chart_dir.name]
        installers = enabled.get(str(entry["key"]), [])
        # Sorted by path alone: one file can now contribute two entries for one
        # service key, and a tuple sort would compare a float override against
        # a None one and raise.
        for path, override in sorted(installers, key=lambda item: str(item[0])):
            effective = override if override is not None else default
            if effective is None:
                failures.append(
                    f"{display(path)}: this profile enables {entry['key']} but sets no "
                    f"ingressTimeoutServer, and {display(chart_dir)}/values.yaml leaves the "
                    f"chart default empty -- so its /{str(entry['key']).removeprefix('vss-')} "
                    f"route inherits the ingress controller's own default while the Docker edge "
                    f"gives {name} {docker:g}s. Set ingressTimeoutServer on this profile's "
                    f"{entry['key']} block"
                )
            elif effective < docker:
                failures.append(
                    f"{display(path)}: ingressTimeoutServer for {entry['key']} is "
                    f"{effective:g}s, below the {docker:g}s the Docker edge gives {name}. The "
                    "Kubernetes edge would cut off a request Compose serves"
                )

        # Charts installed on their own, with no profile switching them on, are
        # held to their own default.
        if not installers and (default is None or default < docker):
            shown = "empty" if default is None else f"{default:g}s"
            failures.append(
                f"{display(chart_dir)}/values.yaml: ingressTimeoutServer is {shown}, below the "
                f"{docker:g}s the Docker edge gives {name}, and no profile overrides it"
            )
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path, default=TEMPLATE)
    parser.add_argument("--helm", type=Path, default=HELM)
    args = parser.parse_args(argv)

    failures = scan(args.template, args.helm)
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1

    default, _, backends = parse_template(args.template)
    print(
        f"Gateway slow-backend timeout lint passed ({len(SLOW_BACKENDS)} backends above the "
        f"{default:g}s default)."
    )
    for name in sorted(SLOW_BACKENDS):
        value = backends[name]["timeout"]
        print(f"  {name}: {value:g}s")
    print(f"  on the default by decision: {', '.join(sorted(DEFAULT_TIMEOUT_BACKENDS))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
