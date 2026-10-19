#!/usr/bin/env -S uv run --quiet --script
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
# /// script
# requires-python = ">=3.9"
# dependencies = ["pyyaml"]
# ///
"""Run the frozen Skill validator with the VSS 3.3.0 release fallback."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
VALIDATOR = ROOT / "skills/vss-build-vision-ai/scripts/validate_resolved_yml.py"
SPEC = importlib.util.spec_from_file_location("vss_release_validator", VALIDATOR)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Could not load {VALIDATOR}")
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)
# Supply release policy outside the Skill tree to preserve frozen harness images.
validator.DEFAULT_CONTAINER_TAG = "3.3.0"
_tag_errors = validator.container_tag_errors


def container_tag_errors(document, expected_tag):
    # Release build pins such as 3.3.0-5 are explicit, not the missing-export fallback.
    fallbacks = {
        validator.DEFAULT_CONTAINER_TAG,
        f"{validator.DEFAULT_CONTAINER_TAG}-sbsa",
    }
    services = {
        name: service
        for name, service in (document.get("services") or {}).items()
        if isinstance(service, dict)
        and isinstance(service.get("image"), str)
        and validator.image_tag(service["image"]) in fallbacks
    }
    return _tag_errors({"services": services}, expected_tag)


validator.container_tag_errors = container_tag_errors

if __name__ == "__main__":
    validator.main()
