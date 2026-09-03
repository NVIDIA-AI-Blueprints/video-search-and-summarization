#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Keep the media origin VIOS stamps into its URLs usable by whoever receives it.

VIOS answers ``/picture/url`` and the clip/full-file ``/url`` APIs with an
absolute ``imageUrl`` / ``videoUrl``, built in ``getIngressBaseUrl()``
(``services/vios/src/framework/utilities/utils.cpp``) out of
``VST_INGRESS_ENDPOINT``. That makes the variable the origin every consumer we
do not control receives: a browser calling VIOS, a webhook body, an alert's
``videoUrl``. Wired to the internal container name it produced
``http://vst-ingress:30888/vst/storage/...`` -- the recipient got ``Could not
resolve host`` while the identical path served 200 on the public origin.
Nothing in the response signals it, which is why this is a lint: it only breaks
in whatever consumes the URL, later and somewhere else.

Two invariants, and a simpler lint passes vacuously against both.

**The default inside a passthrough is a definition.** The Compose files carry
``VST_INGRESS_ENDPOINT=${VST_INGRESS_ENDPOINT:-<default>}`` under
``environment:``, and Compose gives ``environment:`` precedence over
``env_file:``. So when nothing in the ``--env-file`` chain sets the variable --
which is the case for every profile in this tree -- that inline default *is* the
value the container runs with, and the definition in ``services/vios/vst.env``
never applies. A lint that skips passthroughs checks the file that loses.

**The scheme has to travel with the authority.** ``getIngressBaseUrl()`` takes
the scheme from the endpoint when it carries one and only then falls back to
``security.use_https``. That fallback cannot express "TLS terminates upstream":
``use_https`` also decides whether VIOS serves TLS itself (``webServer.cpp``)
and is echoed to WebRTC and RTSP clients as ``useHttps``, so raising it to
correct a URL scheme breaks the listener and misinforms other clients. An
endpoint that names an https public origin without saying ``https://`` is
therefore minted as ``http://<host>:443/...`` and answered 400 by the TLS
listener -- no more usable than the internal name it replaced.

The two invariants only hold together, so both are checked here. Requiring the
configured value to carry a scheme is safe *because* ``getIngressBaseUrl()``
passes such a value through untouched. Revert that passthrough and the
configuration this lint demands becomes the broken input: the function prepends
its own scheme and mints ``http://http://host:7777/vst/...``, which was observed
live before the passthrough was added. Guarding the configuration alone would
call that combination clean.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

TARGET = "VST_INGRESS_ENDPOINT"

# An origin a caller outside the deployment can reach. VST_EXTERNAL_URL is the
# deployment's handle on it and already carries its scheme; VSS_PUBLIC_* is what
# that is built from, and is accepted so a profile may inline the derivation.
PUBLIC_TOKENS = ("VST_EXTERNAL_URL", "VSS_PUBLIC_")

# Names that resolve only on the deployment's own network.
INTERNAL_TOKENS = ("VST_INTERNAL_IP", "VST_INTERNAL_URL", "vst-ingress", "vss-vios-")

# `NAME=value` in an env file, `- NAME=value` in a Compose `environment:` list,
# and `value:` under a Helm `- name: NAME` pair are all matched from the
# assignment side, so one rule covers every place the variable is set.
ENV_ASSIGNMENT = re.compile(rf"^\s*-?\s*{TARGET}\s*=\s*(?P<value>.*?)\s*$")
HELM_NAME = re.compile(rf"^\s*-\s*name:\s*{TARGET}\s*$")
HELM_VALUE = re.compile(r"^\s*value:\s*(?P<value>.*?)\s*$")

# `${VST_INGRESS_ENDPOINT:-<default>}` -- the default is what runs when nothing
# in the --env-file chain sets the variable, so it is checked as a definition.
PASSTHROUGH_DEFAULT = re.compile(rf"^\$\{{{TARGET}:-(?P<default>.*)\}}$")

SCHEME_TOKENS = ("http://", "https://", "VST_EXTERNAL_URL", "VSS_PUBLIC_HTTP_PROTOCOL")

# The Helm charts set the same variable through a template helper rather than a
# ':-' chain, so they are checked by shape instead: whatever a branch emits has
# to carry its scheme, for the same reason the Compose value does.
HELM_HELPERS = (
    (
        Path("deploy/helm/services/vios/charts/vios-streamprocessing/templates/_helpers.tpl"),
        "vss-vios-streamprocessing.vstIngressEndpoint",
    ),
    (
        Path("deploy/helm/services/vios/charts/vios-sensor/templates/_helpers.tpl"),
        "vss-vios-sensor.vstIngressEndpointUrl",
    ),
)

# A `{{- printf ... }}` on its own is the helper's output; a `$internal :=` is
# the in-cluster default it may fall back to. Both become the variable's value.
HELM_EMIT = re.compile(r"^\{\{-?\s*printf\s")
HELM_INTERNAL = re.compile(r"\$internal\s*:=")

# The only reader of the variable, and the other half of the contract.
MINTER = Path("services") / "vios" / "src" / "framework" / "utilities" / "utils.cpp"
MINTER_FUNCTION = "getIngressBaseUrl"

# Body of `<type> getIngressBaseUrl(...) { ... }` up to the first line-initial
# `}`, which is this tree's brace style for a free function.
MINTER_BODY = re.compile(
    rf"{MINTER_FUNCTION}\s*\([^)]*\)\s*\{{(?P<body>.*?)^\}}",
    re.DOTALL | re.MULTILINE,
)


def first_index(value: str, tokens: Iterable[str]) -> int | None:
    """Position of the earliest of *tokens* in *value*, or None if absent."""
    found = [value.index(token) for token in tokens if token in value]
    return min(found) if found else None


def definitions_in(value: str) -> list[str]:
    """The values this assignment can actually resolve to.

    A bare value is its own definition. A passthrough contributes the default it
    falls back to, which is the value that runs whenever the variable is unset
    in the ``--env-file`` chain. A passthrough with no default contributes
    nothing: it can only forward a definition made elsewhere.
    """
    stripped = value.strip()
    passthrough = PASSTHROUGH_DEFAULT.match(stripped)
    if passthrough:
        return [passthrough.group("default")]
    if stripped.startswith((f"${{{TARGET}", f"${TARGET}")):
        return []
    return [stripped]


def assignments(path: Path) -> list[tuple[int, str]]:
    """Return ``(line number, value)`` for each assignment of the variable."""
    found: list[tuple[int, str]] = []
    pending: int | None = None
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if line.lstrip().startswith("#"):
            continue

        if pending is not None:
            helm_value = HELM_VALUE.match(line)
            if helm_value:
                found.append((pending, helm_value.group("value").strip("\"'")))
            pending = None

        if HELM_NAME.match(line):
            pending = number
            continue

        env = ENV_ASSIGNMENT.match(line)
        if env:
            found.append((number, env.group("value")))
    return found


def default_paths() -> list[Path]:
    """Every file under ``deploy/docker`` that mentions the variable.

    Scoped to the Compose deployment because Helm sets the same variable from a
    template helper rather than a ``:-`` chain, and a ``value: {{ include ... }}``
    line says nothing about what it resolves to. ``scan_helm`` checks those
    helpers by shape instead. ``test-scripts`` is excluded because it asserts
    *about* these values rather than setting them.
    """
    base = ROOT / "deploy" / "docker"
    paths = [
        path
        for path in base.rglob("*")
        if path.is_file()
        and "test-scripts" not in path.parts
        and TARGET in path.read_text(encoding="utf-8", errors="ignore")
    ]
    return sorted(set(paths))


def scan_paths(paths: Iterable[Path]) -> tuple[list[str], int]:
    """Return actionable diagnostics plus the number of definitions checked."""
    failures: list[str] = []
    checked = 0

    for path in paths:
        try:
            display = path.relative_to(ROOT)
        except ValueError:
            display = path

        for number, raw in assignments(path):
            for value in definitions_in(raw):
                checked += 1
                where = f"{display}:{number}"

                public_at = first_index(value, PUBLIC_TOKENS)
                internal_at = first_index(value, INTERNAL_TOKENS)

                if public_at is None:
                    failures.append(
                        f"{where}: {TARGET} resolves to {value!r}, which names no "
                        f"public origin. VIOS stamps this into every "
                        f"imageUrl/videoUrl it hands out, so an internal-only "
                        f"value is unresolvable for the browser, webhook or alert "
                        f"that receives it. Derive it from VST_EXTERNAL_URL and "
                        f"keep the internal origin as the ':-' fallback."
                    )
                elif internal_at is not None and internal_at < public_at:
                    failures.append(
                        f"{where}: {TARGET} resolves to {value!r}, which reaches "
                        f"the internal origin before the public one. The internal "
                        f"origin is only correct as the last resort, when no "
                        f"public origin is configured -- put it after the public "
                        f"reference in the ':-' chain."
                    )

                if first_index(value, SCHEME_TOKENS) is None:
                    failures.append(
                        f"{where}: {TARGET} resolves to {value!r}, which carries "
                        f"no scheme. getIngressBaseUrl() then falls back to "
                        f"security.use_https, which cannot say 'TLS terminates "
                        f"upstream' -- it also switches VIOS's own listener and is "
                        f"echoed to WebRTC/RTSP clients -- so an https origin "
                        f"would be minted http:// and answered 400. Include the "
                        f"scheme in the value."
                    )

    return failures, checked


def scan_helm(root: Path | None = None) -> list[str]:
    """Check the Helm helpers emit an endpoint that carries its scheme.

    The charts disagreed: ``vios-sensor`` already emitted ``https://host/vst``
    while ``vios-streamprocessing`` emitted ``host:443/vst`` under a comment
    saying the app would prepend the scheme. Against an https ``externalHost``
    that second form was minted ``http://`` and answered by a TLS listener.
    Both are checked so neither drifts back.

    A ``{{- $explicit }}`` branch is deliberately not checked -- that is the
    operator's own ``vstIngressEndpoint``, and the minter accepts it with or
    without a scheme.
    """
    failures: list[str] = []

    for relative, helper in HELM_HELPERS:
        path = (root or ROOT) / relative
        if not path.is_file():
            failures.append(
                f"{relative}: not found, so the Helm media origin is "
                f"unchecked. If the chart moved, point this check at it."
            )
            continue

        lines = path.read_text(encoding="utf-8").splitlines()
        try:
            start = next(i for i, line in enumerate(lines) if f'define "{helper}"' in line)
        except StopIteration:
            failures.append(
                f"{relative}: helper {helper!r} not found. It sets "
                f"{TARGET} for this chart; if it was renamed, update this check."
            )
            continue

        end = next(
            (i for i, line in enumerate(lines[start + 1 :], start + 1) if line.startswith("{{- end }}") and i > start),
            len(lines),
        )

        for offset, line in enumerate(lines[start : end + 1], start + 1):
            stripped = line.strip()
            emits = HELM_EMIT.match(stripped) or HELM_INTERNAL.search(stripped)
            if emits and "://" not in stripped:
                failures.append(
                    f"{relative}:{offset}: {helper} emits an endpoint with no "
                    f"scheme ({stripped!r}). VIOS stamps it into every "
                    f"imageUrl/videoUrl, and without a scheme getIngressBaseUrl() "
                    f"falls back to security.use_https -- which cannot say 'TLS "
                    f"terminates upstream', so an https externalHost is minted "
                    f"http://. Build it from global.externalScheme."
                )

    return failures


def scan_minter(root: Path | None = None) -> list[str]:
    """Check that the endpoint's own scheme survives being turned into a URL.

    Returns diagnostics, or an empty list when the passthrough is present. A
    missing file or a renamed function is reported rather than skipped: this
    half of the contract silently disappearing is what the check exists to
    prevent.
    """
    path = (root or ROOT) / MINTER
    if not path.is_file():
        return [
            f"{MINTER}: not found, so the scheme passthrough that makes a "
            f"scheme-carrying {TARGET} safe cannot be verified. If the minter "
            f"moved, point this check at it."
        ]

    match = MINTER_BODY.search(path.read_text(encoding="utf-8", errors="ignore"))
    if match is None:
        return [
            f"{MINTER}: {MINTER_FUNCTION}() not found. It is the only reader of "
            f"{TARGET}; if it was renamed, update this check with it."
        ]

    body = match.group("body")
    returns_verbatim = "return config.ingress_endpoint;" in body
    tests_both_schemes = '"http://"' in body and '"https://"' in body

    if returns_verbatim and tests_both_schemes:
        return []

    return [
        f"{MINTER}: {MINTER_FUNCTION}() no longer returns a scheme-carrying "
        f"{TARGET} verbatim. It has to, because this lint requires that value to "
        f"carry its scheme: without the passthrough the function prepends "
        f"another one and mints 'http://http://host/vst/...'. Restore the "
        f"early return of config.ingress_endpoint when it already begins "
        f"http:// or https://."
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path)
    args = parser.parse_args(argv)

    paths = args.paths or default_paths()
    failures, checked = scan_paths(paths)

    # Explicit paths mean a caller is linting specific fixtures, so only the
    # repository-wide run checks the minter that pairs with them.
    if not args.paths:
        failures = failures + scan_helm() + scan_minter()

    # A lint with nothing to check passes forever. The variable is set in the
    # VIOS env file and defaulted in the streamprocessing Compose file; if both
    # moved, this needs updating rather than quietly reporting success.
    if not args.paths and checked == 0:
        print(
            f"{TARGET} is no longer defined anywhere under deploy/docker -- this "
            f"lint is checking nothing. Point it at wherever the media origin "
            f"moved.",
            file=sys.stderr,
        )
        return 1

    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1

    extra = (
        ""
        if args.paths
        else (
            f", {len(HELM_HELPERS)} Helm helper(s) emitting a scheme"
            f", scheme passthrough intact in {MINTER.name}"
        )
    )
    print(
        f"VIOS media origin lint passed "
        f"({checked} resolvable definition(s) of {TARGET} in "
        f"{len(paths)} file(s){extra})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
