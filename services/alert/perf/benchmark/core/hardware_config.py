# SPDX-License-Identifier: Apache-2.0
"""Resolve native deployment profiles without changing benchmark workloads."""

from pathlib import Path
from urllib.parse import urlsplit

import yaml

PERF_ROOT = Path(__file__).resolve().parents[2]


def profile_for_config(config_id, detected_profile):
    # Preserve the original local invocation's automatic hardware detection.
    if config_id == "alerts-verification-compose":
        return detected_profile
    rows = yaml.safe_load((PERF_ROOT / "configs/perf-configs.yaml").read_text())["perf_configs"]
    selected = next((row for row in rows if row["id"] == config_id), None)
    if selected is None:
        # Custom local report identifiers have always been accepted by the runner.
        return detected_profile
    requested = selected["hardwareProfile"]
    equivalent = {"IGX-THOR": "THOR", "AGX-THOR": "THOR"}
    if equivalent.get(requested, requested) != equivalent.get(detected_profile, detected_profile):
        raise ValueError(
            f"Alerts config {config_id} requires {requested}, detected {detected_profile}"
        )
    return requested


def remote_llm_arguments(profile, environment):
    """Use the source CI remote-LLM topology only on its designated platforms."""
    if profile not in {"RTXPRO4500BW", "DGX-SPARK"}:
        return []
    endpoint = environment.get("ALERTS_PERF_REMOTE_LLM_URL", "").strip()
    try:
        parsed = urlsplit(endpoint)
        valid = (
            parsed.scheme in {"http", "https"} and parsed.hostname
            and not parsed.username and not parsed.password
            and not parsed.query and not parsed.fragment
            and not any(char.isspace() for char in endpoint)
        )
        # Validate a supplied port, including nonnumeric and out-of-range values.
        parsed.port
    except ValueError:
        valid = False
    if not valid:
        raise ValueError(
            f"{profile} requires ALERTS_PERF_REMOTE_LLM_URL set to an HTTP(S) "
            "endpoint without embedded credentials, query or fragment"
        )
    return ["--use-remote-llm", "--llm-url", endpoint]


def supporting_images_for_profile(profile, values):
    """Select configured GB300 support images without changing the target artifact."""
    selected = dict(values)
    if profile == "GB300":
        for component in ("RT_VLM", "RT_CV"):
            key = f"PERF_VSS_{component}_IMAGE"
            selected[key] = values[f"PERF_VSS_{component}_SBSA_IMAGE"]
    return selected
