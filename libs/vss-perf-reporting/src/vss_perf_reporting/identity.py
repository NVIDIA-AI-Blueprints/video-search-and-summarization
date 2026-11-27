# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Add requested-image provenance without changing measurements (stdlib only).

artifact_ref identifies the orchestrator's resolved input, not a Docker-observed
registry digest. Existing observed identities are preserved. No remote writes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile


def full_image_ref(value):
    """Require an explicit registry/repository and tag or sha256 digest."""
    if not isinstance(value, str) or len(value) > 512:
        return False
    return bool(re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9.:-]*/[a-z0-9][a-z0-9._/-]*(?::[A-Za-z0-9_][A-Za-z0-9_.-]*|@sha256:[a-f0-9]{64})", value))


def enriched_config(config, environment=None):
    env = os.environ if environment is None else environment
    result = dict(config or {})
    ref = env.get("PERF_ARTIFACT_REF", "")
    if ref:
        if not full_image_ref(ref):
            raise ValueError("PERF_ARTIFACT_REF must be a full tagged or digest image reference")
        if result.get("artifact_ref") and result["artifact_ref"] != ref:
            raise ValueError("Report artifact_ref contradicts the orchestrator image reference")
        result["artifact_ref"] = ref
        result["artifact_identity_source"] = "orchestrator_resolved_input"
    for variable, field in (("PERF_HARDWARE_PLATFORM", "hardware_platform"),
                            ("PERF_IMAGE_ARCH", "image_arch"),
                            ("PERF_REQUESTED_ARTIFACT_REF", "requested_artifact_ref")):
        value = env.get(variable, "")
        if value:
            result[field] = "IGX-THOR" if field == "hardware_platform" and value == "IGX-T7000" else value
    selection = env.get("PERF_IDENTITY_SELECTION")
    if selection:
        selected = json.loads(selection)
        if not isinstance(selected, dict):
            raise ValueError("Benchmark selection must be an object")
        fingerprint = hashlib.sha256(json.dumps(selected, sort_keys=True).encode())
        config_dir = Path(env.get("PERF_IDENTITY_CONFIG_DIR", ".__no_perf_config__"))
        for file in sorted(config_dir.glob("*")):
            if file.is_file() and file.suffix in (".yaml", ".yml", ".json"):
                if file.is_symlink() or file.stat().st_size > 1024 * 1024:
                    raise ValueError("Unsupported benchmark configuration file")
                fingerprint.update(file.name.encode())
                fingerprint.update(file.read_bytes())
        perf_root = config_dir.resolve().parent
        profile = selected.get("benchmarkConfig")
        profiles = [perf_root / profile] if profile else sorted((perf_root / "benchmark").rglob("*.yaml")) + sorted((perf_root / "benchmark").rglob("*.yml"))
        if len(profiles) > 256:
            raise ValueError("Too many benchmark profiles")
        for candidate in profiles:
            profile_path = candidate.resolve()
            if not profile_path.is_relative_to(perf_root):
                raise ValueError("Benchmark profile must be inside the performance package")
            fingerprint.update(str(candidate.relative_to(perf_root)).encode())
            if profile_path.is_file():
                if profile_path.stat().st_size > 1024 * 1024:
                    raise ValueError("Benchmark profile exceeds size limit")
                fingerprint.update(profile_path.read_bytes())
            else:
                fingerprint.update(b"missing-profile")
        result["benchmark_selection"] = selected
        result["configuration_fingerprint"] = fingerprint.hexdigest()
    return result


def finalize_directory(directory, environment=None):
    """Validate every candidate first; caller must withhold publication on error."""
    root = Path(directory).resolve(strict=True)
    candidates = []
    for path in sorted(root.rglob("*.json")):
        if (root.name != "vss-perf-results" and "vss-perf-results" not in path.relative_to(root).parts) or path.name.endswith(".upload.json"):
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError("Report symlinks are not supported")
        result = json.loads(path.read_text())
        if not isinstance(result, dict) or not all(k in result for k in ("schema_version", "metadata", "summary", "test_cases")):
            continue
        metadata = result["metadata"]
        metadata["config"] = enriched_config(metadata.get("config"), environment)
        candidates.append((path, result))
    for path, result in candidates:
        fd, temporary = tempfile.mkstemp(prefix=".identity-", dir=path.parent)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(result, stream, indent=2, allow_nan=False)
                stream.write("\n")
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return len(candidates)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report_dir")
    args = parser.parse_args()
    print(f"Finalized identity for {finalize_directory(args.report_dir)} canonical report(s)")
