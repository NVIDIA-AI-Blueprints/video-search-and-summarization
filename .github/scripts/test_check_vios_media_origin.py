#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Self-test for check_vios_media_origin.py.

Runs before the lint in CI. A lint of this shape fails open in two ways -- by
skipping the assignment that actually decides the value, and by accepting an
origin whose scheme it cannot express -- so each is pinned here with the exact
text that shipped the defect.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_vios_media_origin import HELM_HELPERS  # noqa: E402
from check_vios_media_origin import MINTER  # noqa: E402
from check_vios_media_origin import definitions_in  # noqa: E402
from check_vios_media_origin import main  # noqa: E402
from check_vios_media_origin import scan_helm  # noqa: E402
from check_vios_media_origin import scan_minter  # noqa: E402
from check_vios_media_origin import scan_paths  # noqa: E402

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {detail}")
        FAILURES.append(name)


def scan_text(text: str, suffix: str = ".env") -> tuple[list[str], int]:
    """Scan *text* as a file, returning the lint's own verdict."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"sample{suffix}"
        path.write_text(text, encoding="utf-8")
        return scan_paths([path])


# --------------------------------------------------------------------------
# definitions_in: which assignments resolve to a value that runs
# --------------------------------------------------------------------------
print("definitions_in")

check(
    "a bare value is its own definition",
    definitions_in("${VST_EXTERNAL_URL:-${VST_INTERNAL_URL}}/vst")
    == ["${VST_EXTERNAL_URL:-${VST_INTERNAL_URL}}/vst"],
)

# The regression that made an earlier version of this lint vacuous: Compose
# gives `environment:` precedence over `env_file:`, so this inline default -- not
# services/vios/vst.env -- is what the container runs with whenever the
# --env-file chain leaves the variable unset, which every profile in this tree
# does. Treating it as mere plumbing checks the file that loses.
check(
    "a passthrough's default is a definition",
    definitions_in("${VST_INGRESS_ENDPOINT:-${VST_INTERNAL_IP:-vst-ingress:30888}/vst}")
    == ["${VST_INTERNAL_IP:-vst-ingress:30888}/vst"],
)

check(
    "a passthrough with no default defines nothing",
    definitions_in("${VST_INGRESS_ENDPOINT}") == [],
)


# --------------------------------------------------------------------------
# The defect, in each form it has actually taken
# --------------------------------------------------------------------------
print("rejects the defect")

failures, checked = scan_text("VST_INGRESS_ENDPOINT=${VST_INTERNAL_IP}/vst")
check("internal-only env definition is rejected", bool(failures), f"({failures})")
check("...and was actually examined", checked == 1, f"(checked={checked})")
check(
    "...naming the reason",
    any("no public origin" in f for f in failures),
    f"({failures})",
)

# Verbatim from deploy/docker/services/vios/streamprocessing/docker-compose.yaml
# before the fix -- the value the deployment really ran with.
failures, checked = scan_text(
    "    environment:\n"
    "      - VST_INGRESS_ENDPOINT=${VST_INGRESS_ENDPOINT:-"
    "${VST_INTERNAL_IP:-vst-ingress:${VST_PORT:-30888}}/vst}\n",
    suffix=".yaml",
)
check("the shipped Compose default is rejected", bool(failures), f"({failures})")
check("...and was actually examined", checked == 1, f"(checked={checked})")

# A public origin with no scheme is the trap that looks fixed: VIOS falls back
# to security.use_https, which is false, so an https origin mints http://host:443
# and the TLS listener answers 400.
failures, _ = scan_text("VST_INGRESS_ENDPOINT=${VSS_PUBLIC_HOST}:${VSS_PUBLIC_PORT}/vst")
check("a schemeless public origin is rejected", bool(failures), f"({failures})")
check(
    "...naming the scheme as the reason",
    any("carries no scheme" in f for f in failures),
    f"({failures})",
)

failures, _ = scan_text("VST_INGRESS_ENDPOINT=${VST_INTERNAL_URL:-${VST_EXTERNAL_URL}}/vst")
check("the internal origin first is rejected", bool(failures), f"({failures})")
check(
    "...naming the ordering as the reason",
    any("before the public one" in f for f in failures),
    f"({failures})",
)


# --------------------------------------------------------------------------
# The fix, and the fallback it must keep
# --------------------------------------------------------------------------
print("accepts the fix")

failures, checked = scan_text("VST_INGRESS_ENDPOINT=${VST_EXTERNAL_URL:-${VST_INTERNAL_URL}}/vst")
check("public origin with internal fallback is accepted", not failures, f"({failures})")
check("...and was actually examined", checked == 1, f"(checked={checked})")

failures, _ = scan_text(
    "      - VST_INGRESS_ENDPOINT=${VST_INGRESS_ENDPOINT:-${VST_EXTERNAL_URL:-"
    "${VST_INTERNAL_URL:-http://vst-ingress:${VST_PORT:-30888}}}/vst}\n",
    suffix=".yaml",
)
check("the fixed Compose default is accepted", not failures, f"({failures})")

# A deployment may inline the derivation rather than going through
# VST_EXTERNAL_URL, as long as the scheme comes with it.
failures, _ = scan_text(
    "VST_INGRESS_ENDPOINT=${VSS_PUBLIC_HTTP_PROTOCOL}://${VSS_PUBLIC_HOST}:${VSS_PUBLIC_PORT}/vst"
)
check("an inlined public derivation is accepted", not failures, f"({failures})")

failures, _ = scan_text("VST_INGRESS_ENDPOINT=${VST_INGRESS_ENDPOINT}", suffix=".yaml")
check("a bare passthrough is not a finding", not failures, f"({failures})")


# --------------------------------------------------------------------------
# scan_minter: the C++ half that makes a scheme-carrying value safe
# --------------------------------------------------------------------------
print("scan_minter rejects losing the scheme passthrough")


def minter_tree(body: str) -> Path:
    """A throwaway tree holding utils.cpp with *body* as the function."""
    tmp = Path(tempfile.mkdtemp())
    path = tmp / MINTER
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return tmp


FIXED = """
string getIngressBaseUrl()
{
    const DeviceConfig& config = GET_CONFIG();
    if (config.ingress_endpoint.rfind("http://", 0) == 0 ||
        config.ingress_endpoint.rfind("https://", 0) == 0)
    {
        return config.ingress_endpoint;
    }
    const string protocol = config.use_https ? "https" : "http";
    return protocol + "://" + config.ingress_endpoint;
}
"""

# Verbatim pre-fix source. Paired with the configuration this lint *requires*,
# it mints http://http://host:7777/vst/... -- observed live.
DEFECT = """
string getIngressBaseUrl()
{
    const DeviceConfig& config = GET_CONFIG();
    const string protocol = config.use_https ? "https" : "http";
    const string baseUrl = protocol + "://" + config.ingress_endpoint;
    return baseUrl;
}
"""

check("the fixed minter is accepted", not scan_minter(minter_tree(FIXED)))
check("the pre-fix minter is a finding", bool(scan_minter(minter_tree(DEFECT))))
check(
    "a half-revert that drops one scheme test is a finding",
    bool(scan_minter(minter_tree(FIXED.replace('"https://"', '"http://"')))),
)
check(
    "a minter that is gone is a finding, not a skip",
    bool(scan_minter(Path(tempfile.mkdtemp()))),
)
check(
    "a renamed minter is a finding, not a skip",
    bool(scan_minter(minter_tree(FIXED.replace("getIngressBaseUrl", "getBaseUrl")))),
)


# --------------------------------------------------------------------------
# scan_helm: the charts set the same variable from a template helper
# --------------------------------------------------------------------------
print("scan_helm requires every emitted Helm endpoint to carry a scheme")

HELM_FIXED = {
    "vss-vios-streamprocessing.vstIngressEndpoint": """
{{- define "vss-vios-streamprocessing.vstIngressEndpoint" -}}
{{- $internal := printf "http://%s" (ternary (printf "%s-vss-vios-ingress:30888/vst" .Release.Name) "vss-vios-ingress:30888/vst" $pfx) }}
{{- if ne $explicit "" }}
{{- $explicit }}
{{- else }}
{{- printf "%s://%s:%s/vst" $es $eh $ep }}
{{- end }}
""",
    "vss-vios-sensor.vstIngressEndpointUrl": """
{{- define "vss-vios-sensor.vstIngressEndpointUrl" -}}
{{- $internal := printf "http://%s" (ternary (printf "%s-vss-vios-ingress:30888/vst" .Release.Name) "vss-vios-ingress:30888/vst" $pfx) }}
{{- printf "%s://%s/vst" $es $eh }}
{{- end }}
""",
}

# The shape that shipped in vios-streamprocessing: authority only, on the
# premise that the app would prepend the scheme.
HELM_DEFECT = '{{- printf "%s:%s/vst" $eh $ep }}'


def helm_tree(bodies: dict[str, str]) -> Path:
    """A throwaway tree holding both chart helper files."""
    tmp = Path(tempfile.mkdtemp())
    for relative, helper in HELM_HELPERS:
        path = tmp / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(bodies[helper], encoding="utf-8")
    return tmp


check("both fixed helpers are accepted", not scan_helm(helm_tree(HELM_FIXED)))

for _, target in HELM_HELPERS:
    regressed = dict(HELM_FIXED)
    regressed[target] = HELM_FIXED[target].replace(
        '{{- printf "%s://%s:%s/vst" $es $eh $ep }}', HELM_DEFECT
    ).replace('{{- printf "%s://%s/vst" $es $eh }}', HELM_DEFECT)
    check(f"a schemeless emit in {target} is a finding", bool(scan_helm(helm_tree(regressed))))

internal_regressed = dict(HELM_FIXED)
internal_regressed["vss-vios-streamprocessing.vstIngressEndpoint"] = HELM_FIXED[
    "vss-vios-streamprocessing.vstIngressEndpoint"
].replace(
    '{{- $internal := printf "http://%s" (ternary (printf "%s-vss-vios-ingress:30888/vst" .Release.Name) "vss-vios-ingress:30888/vst" $pfx) }}',
    '{{- $internal := ternary (printf "%s-vss-vios-ingress:30888/vst" .Release.Name) "vss-vios-ingress:30888/vst" $pfx }}',
)
check(
    "a schemeless in-cluster default is a finding",
    bool(scan_helm(helm_tree(internal_regressed))),
)

check("a missing chart is a finding, not a skip", bool(scan_helm(Path(tempfile.mkdtemp()))))

renamed = dict(HELM_FIXED)
renamed["vss-vios-sensor.vstIngressEndpointUrl"] = HELM_FIXED[
    "vss-vios-sensor.vstIngressEndpointUrl"
].replace("vstIngressEndpointUrl", "vstIngressBase")
check("a renamed helper is a finding, not a skip", bool(scan_helm(helm_tree(renamed))))


# --------------------------------------------------------------------------
# Non-vacuity: nothing to check is a failure, not a pass
# --------------------------------------------------------------------------
print("refuses to pass vacuously")

with tempfile.TemporaryDirectory() as tmp:
    empty = Path(tmp) / "nothing.env"
    empty.write_text("SOMETHING_ELSE=1\n", encoding="utf-8")
    failures, checked = scan_paths([empty])
    check("a file with no assignment yields no findings", not failures)
    check("...and nothing counted", checked == 0, f"(checked={checked})")

# The repository's own files must satisfy the lint, and it must find them.
check("the repository passes its own lint", main([]) == 0)


print()
if FAILURES:
    print(f"{len(FAILURES)} self-test failure(s): {', '.join(FAILURES)}")
    raise SystemExit(1)
print("check_vios_media_origin self-tests passed.")
