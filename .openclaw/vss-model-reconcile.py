#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Reconcile /sandbox/.openclaw/openclaw.json with the model the gateway serves.

openclaw.json is generated inside the digest-pinned base image for its default
model, and NemoClaw's own startup fixes (apply_model_override and the #3175
reconcile in nemoclaw-start.sh) silently no-op unless the container starts as
root. Managed images get root startup from their driver; this image's OCI user
is `sandbox` (OpenShell rejects a root image user for custom sandboxes), so
under the Docker driver nothing ever corrects the file: the agent then caps
output and compacts against the BASE model's contextWindow/maxTokens while
actually talking to whatever the gateway routes.

This image deliberately makes openclaw.json sandbox-owned (the plugin flow
already rewrites it and its hash at build), so the same reconcile is safe here
as the sandbox user. vss-start runs it before handing over to nemoclaw-start.

Two jobs, mirroring upstream where upstream has a shape:
- names: agents.defaults.model.primary and models.providers.inference.models[0]
  follow NEMOCLAW_MODEL_OVERRIDE when set (validated like upstream), else the
  model `openshell inference get --json` reports.
- limits: upstream's reconcile never corrects contextWindow/maxTokens, and the
  gateway probe does not expose them, so there is no runtime source of truth.
  VSS_OPENCLAW_CONTEXT_WINDOW / VSS_OPENCLAW_MAX_TOKENS are unambiguous
  operator overrides, honored whenever set (no image bakes them).
  NEMOCLAW_CONTEXT_WINDOW / NEMOCLAW_MAX_TOKENS are build-ARG ENVs the base
  image always bakes, so a runtime value equal to the baked one is
  indistinguishable from "not set" — they count as operator overrides only
  when they DIFFER from the baked values (an operator who wants exactly the
  baked value under another model uses the VSS_OPENCLAW_* form). Otherwise,
  when the served model differs from the baked one, the stale keys are
  DELETED so OpenClaw's model catalog and defaults apply instead of the wrong
  model's caps.

Runtime mode is fail-open: any error logs and exits 0 — never blocks startup.
--snapshot (build time) records the baked primary and env limits to
/etc/openclaw-harness/baked-model.json and fails loudly.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys

CONFIG = "/sandbox/.openclaw/openclaw.json"
HASH = "/sandbox/.openclaw/.config-hash"
BAKED = "/etc/openclaw-harness/baked-model.json"


def log(msg: str) -> None:
    print(f"[vss-reconcile] {msg}", file=sys.stderr)


def valid_model(model: str) -> bool:
    return bool(model) and len(model) <= 256 and not re.search(r"[\x00-\x1f\x7f]", model)


def gateway_model() -> str:
    """The model the OpenShell gateway serves; empty when unknowable."""
    try:
        r = subprocess.run(
            ["openshell", "inference", "get", "--json"],
            capture_output=True, text=True, timeout=3,
        )
        if r.returncode != 0:
            return ""
        model = json.loads(r.stdout).get("model")
        return model if isinstance(model, str) else ""
    except Exception:
        return ""


def refresh_hash(config: str, hash_path: str) -> None:
    with open(config, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    with open(hash_path, "w") as f:
        f.write(f"{digest}  {config}\n")


def snapshot(config: str | None = None, baked: str | None = None, env: dict | None = None) -> None:
    """Build time: record what the image was generated for (fail loudly)."""
    config = CONFIG if config is None else config
    baked = BAKED if baked is None else baked
    env = os.environ if env is None else env
    cfg = json.load(open(config))
    primary = cfg.get("agents", {}).get("defaults", {}).get("model", {}).get("primary")
    if not isinstance(primary, str) or not primary:
        raise SystemExit("baked config has no agents.defaults.model.primary")
    os.makedirs(os.path.dirname(baked), exist_ok=True)
    with open(baked, "w") as f:
        json.dump(
            {
                "primary": primary,
                "env_context_window": env.get("NEMOCLAW_CONTEXT_WINDOW", ""),
                "env_max_tokens": env.get("NEMOCLAW_MAX_TOKENS", ""),
            },
            f, indent=2,
        )
        f.write("\n")


def reconcile(config: str | None = None, hash_path: str | None = None, baked: str | None = None,
              env: dict | None = None, probe=None) -> bool:
    """Returns True when the config was rewritten."""
    config = CONFIG if config is None else config
    hash_path = HASH if hash_path is None else hash_path
    baked = BAKED if baked is None else baked
    env = os.environ if env is None else env
    probe = gateway_model if probe is None else probe
    if os.path.islink(config) or os.path.islink(hash_path):
        log("refusing: config or hash path is a symlink")
        return False
    if not os.path.isfile(config):
        return False

    target = env.get("NEMOCLAW_MODEL_OVERRIDE", "")
    if target and not valid_model(target):
        log("NEMOCLAW_MODEL_OVERRIDE invalid — ignoring")
        target = ""
    if not target:
        target = probe()
    if not valid_model(target):
        return False

    qualified = target if target.startswith("inference/") else f"inference/{target}"
    bare = qualified[len("inference/"):]

    cfg = json.load(open(config))
    baked_info = {}
    if os.path.isfile(baked):
        try:
            baked_info = json.load(open(baked))
        except Exception:
            baked_info = {}

    changed = False
    model_slot = cfg.setdefault("agents", {}).setdefault("defaults", {}).setdefault("model", {})
    if model_slot.get("primary") != qualified:
        model_slot["primary"] = qualified
        changed = True

    inference = cfg.setdefault("models", {}).setdefault("providers", {}).setdefault("inference", {})
    models_list = inference.get("models")
    if not isinstance(models_list, list) or not models_list:
        models_list = [{}]
        inference["models"] = models_list
    first = models_list[0]
    if not isinstance(first, dict):
        first = {}
        models_list[0] = first
    if first.get("id") != bare or first.get("name") != qualified:
        first["id"] = bare
        first["name"] = qualified
        changed = True

    # Limits: an explicit VSS_OPENCLAW_* override always wins (never baked, so
    # never ambiguous); a NEMOCLAW_* env counts only when it differs from the
    # baked value (it is a build-ARG ENV, always present, so equality is
    # indistinguishable from "not set"); else a model change drops the stale
    # keys so OpenClaw's own catalog/defaults rule.
    for vss_key, env_key, cfg_key, baked_key in (
        ("VSS_OPENCLAW_CONTEXT_WINDOW", "NEMOCLAW_CONTEXT_WINDOW", "contextWindow", "env_context_window"),
        ("VSS_OPENCLAW_MAX_TOKENS", "NEMOCLAW_MAX_TOKENS", "maxTokens", "env_max_tokens"),
    ):
        explicit = env.get(vss_key, "")
        value = env.get(env_key, "")
        if explicit and re.fullmatch(r"[1-9][0-9]*", explicit):
            if first.get(cfg_key) != int(explicit):
                first[cfg_key] = int(explicit)
                changed = True
        elif value and value != baked_info.get(baked_key, "") and re.fullmatch(r"[1-9][0-9]*", value):
            if first.get(cfg_key) != int(value):
                first[cfg_key] = int(value)
                changed = True
        elif baked_info.get("primary") and qualified != baked_info["primary"] and cfg_key in first:
            del first[cfg_key]
            log(f"dropped baked {cfg_key} (generated for {baked_info['primary']}, "
                f"gateway serves {qualified}); OpenClaw defaults apply — set "
                f"{vss_key} to pin a value")
            changed = True

    if not changed:
        return False

    with open(config, "w") as f:
        json.dump(cfg, f, indent=2)
    refresh_hash(config, hash_path)
    log(f"reconciled model config to {qualified}")
    return True


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--snapshot"]:
        snapshot()
        return 0
    try:
        reconcile()
    except Exception as exc:  # fail-open: never block sandbox startup
        log(f"skipped ({exc})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
