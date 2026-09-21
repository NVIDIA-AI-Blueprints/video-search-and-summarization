# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Container env values must render as digits, not float exponent notation.

Helm decodes values.yaml numbers as float64, and `quote` formats those with
%v, so a plain `{{ .Values.x | quote }}` renders 20000000 as "2e+07".
Kubernetes accepts that quoted form, but it is not a YAML 1.1 number, so a
round-trip through a YAML library re-emits it unquoted and the API server
then rejects `env.value` as a number where a string is required:

    Deployment in version "v1" cannot be handled as a Deployment: json:
    cannot unmarshal number into Go struct field EnvVar...env.value of type
    string

That is a whole-release failure -- nothing installs -- so it is worth pinning
on every chart rather than only on the one that regressed.
"""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
HELM_ROOT = REPO_ROOT / "deploy" / "helm"

# Charts that render standalone (no `helm dependency build` network access).
STANDALONE_CHARTS = (
    HELM_ROOT / "services" / "ui",
    HELM_ROOT / "services" / "alert",
    HELM_ROOT / "services" / "video-summarization",
)

# Go's %v on a float64 switches to exponent form for values like 2e+07.
EXPONENT = re.compile(r"^[-+]?[0-9]+(\.[0-9]+)?[eE][-+]?[0-9]+$")


def _helm_template(chart: Path) -> str:
    result = subprocess.run(
        ["helm", "template", "vss", str(chart), "--set", "enabled=true"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _env_values(rendered: str):
    """Yield (kind, name, container, env_name, env_value) for every env value."""
    for document in yaml.safe_load_all(rendered):
        if not isinstance(document, dict):
            continue
        spec = document.get("spec", {}) or {}
        pod_spec = (spec.get("template", {}) or {}).get("spec", {}) or {}
        containers = list(pod_spec.get("containers") or [])
        containers += list(pod_spec.get("initContainers") or [])
        for container in containers:
            for entry in container.get("env") or []:
                if not isinstance(entry, dict) or "value" not in entry:
                    continue
                yield (
                    document.get("kind"),
                    (document.get("metadata") or {}).get("name"),
                    container.get("name"),
                    entry.get("name"),
                    entry["value"],
                )


class HelmEnvValueTypeTests(unittest.TestCase):
    def test_env_values_are_strings(self):
        """A bare number under `value:` is rejected by the API server outright."""
        for chart in STANDALONE_CHARTS:
            rendered = _helm_template(chart)
            for kind, name, container, env_name, value in _env_values(rendered):
                with self.subTest(chart=chart.name, env=env_name):
                    self.assertIsInstance(
                        value,
                        str,
                        f"{kind}/{name} container {container}: {env_name} "
                        f"renders as {type(value).__name__} {value!r}; "
                        "Kubernetes requires a string",
                    )

    def test_env_values_avoid_exponent_notation(self):
        """`value: "2e+07"` installs, but loses its quotes on any YAML round-trip."""
        for chart in STANDALONE_CHARTS:
            rendered = _helm_template(chart)
            for kind, name, container, env_name, value in _env_values(rendered):
                if not isinstance(value, str) or not EXPONENT.match(value):
                    continue
                self.fail(
                    f"{kind}/{name} container {container}: {env_name}={value!r} "
                    "is float exponent notation. Helm renders values.yaml "
                    "numbers through float64, so pipe the value through "
                    "`int64` before `quote` to keep the digits."
                )

    def test_ui_adapter_char_limits_render_as_digits(self):
        """Pins the three limits that regressed, including their exact values."""
        rendered = _helm_template(HELM_ROOT / "services" / "ui")
        found = {
            env_name: value
            for _, _, _, env_name, value in _env_values(rendered)
            if env_name
            in {
                "AGENT_MAX_EVENT_CHARS_PER_RUN",
                "AGENT_MAX_THREAD_STATE_CHARS",
                "AGENT_MAX_RETAINED_CHARS",
            }
        }
        self.assertEqual(
            found,
            {
                "AGENT_MAX_EVENT_CHARS_PER_RUN": "20000000",
                "AGENT_MAX_THREAD_STATE_CHARS": "20000000",
                "AGENT_MAX_RETAINED_CHARS": "64000000",
            },
        )

    def test_env_values_survive_a_yaml_round_trip_as_strings(self):
        """Re-serialise, reload, values stay strings.

        Round-tripping is what the CI post-renderer does, and what kustomize,
        Argo CD and `kubectl get -o yaml | kubectl apply -f -` do. Note this
        catches only values PyYAML itself resolves as numbers: `2e+07` is a
        string under YAML 1.1 and a number under the YAML 1.2 core schema Go
        implements, so PyYAML re-reads its own unquoted output as a string
        while the API server rejects it. Exponent notation is caught by
        test_env_values_avoid_exponent_notation instead.
        """
        for chart in STANDALONE_CHARTS:
            rendered = _helm_template(chart)
            documents = [
                document
                for document in yaml.safe_load_all(rendered)
                if isinstance(document, dict)
            ]
            round_tripped = yaml.safe_dump_all(documents, sort_keys=False)
            for kind, name, container, env_name, value in _env_values(round_tripped):
                with self.subTest(chart=chart.name, env=env_name):
                    self.assertIsInstance(
                        value,
                        str,
                        f"{kind}/{name} container {container}: {env_name} "
                        f"became {type(value).__name__} {value!r} after a YAML "
                        "round-trip; the API server rejects the manifest",
                    )


if __name__ == "__main__":
    unittest.main()
