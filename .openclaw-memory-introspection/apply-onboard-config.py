#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Apply onboard's build args to the openclaw.json this image inherits.

NemoClaw generates /sandbox/.openclaw/openclaw.json inside its managed base
image, from build ARGs its own Dockerfile declares. `onboard --from <Dockerfile>`
rewrites those ARGs in the custom Dockerfile with the session's values
(src/lib/onboard/dockerfile-patch.ts) and expects the generator to run again at
build. A custom image cannot re-run the generator (the post-generator
attestation allowlist forbids it), so it inherits the base image's config -- and
every ARG the custom Dockerfile does not declare is dropped by a regex
`String.replace` that matches nothing and reports nothing.

The user-visible bug that comes from that silence is the model: the sandbox keeps
the base image's model, context window and max tokens, so the agent caps output
and compacts against the wrong model's limits. This script closes it -- the
Dockerfile declares the ARGs, and this applies them to the inherited config at
build, before the config hash is recomputed.

Derivations mirror NemoClaw's generator (scripts/generate-openclaw-config.mts):
origins are unique([loopback, chat, portless]); allowInsecureAuth is
scheme == http; device auth is disabled for a non-loopback UI host.

Do not register remote UI origins here. Onboard rewrites CHAT_UI_URL to its
loopback dashboard forward. Register the remote origin after onboard in
deploy_nemoclaw.ipynb; keep the derivation below for callers whose URL survives.
"""

from __future__ import annotations

import json
import os
import re
import sys
from urllib.parse import urlparse

CONFIG = "/sandbox/.openclaw/openclaw.json"
_LOOPBACK = {"localhost", "127.0.0.1", "::1", "[::1]"}


def _is_loopback(host: str) -> bool:
    host = (host or "").lower().strip("[]")
    return host in {h.strip("[]") for h in _LOOPBACK} or host.startswith("127.")


def _qualify(model: str) -> str:
    return model if model.startswith("inference/") else f"inference/{model}"


def control_ui(chat_ui_url: str, gateway_port: int) -> dict | None:
    """The controlUi block NemoClaw's generator would have produced."""
    if not chat_ui_url:
        return None
    parsed = urlparse(chat_ui_url)
    if not parsed.scheme or not parsed.hostname:
        return None
    loopback = f"http://127.0.0.1:{gateway_port}"
    chat = f"{parsed.scheme}://{parsed.netloc}"
    portless = (
        f"{parsed.scheme}://{parsed.hostname}"
        if parsed.port is not None and not _is_loopback(parsed.hostname)
        else None
    )
    origins: list[str] = []
    for origin in (loopback, chat, portless):
        if origin and origin not in origins:
            origins.append(origin)
    return {
        "allowedOrigins": origins,
        "allowInsecureAuth": parsed.scheme == "http",
        "dangerouslyDisableDeviceAuth": not _is_loopback(parsed.hostname),
    }


def apply(config: str | None = None, env: dict | None = None) -> list[str]:
    """Patch the config in place. Returns one line per change, for the build log."""
    config = CONFIG if config is None else config
    env = os.environ if env is None else env
    with open(config) as handle:
        cfg = json.load(handle)
    changes: list[str] = []

    # --- model identity: onboard supplies the session's model -----------------
    model = (env.get("NEMOCLAW_PRIMARY_MODEL_REF") or env.get("NEMOCLAW_MODEL") or "").strip()
    if model and len(model) <= 256 and not re.search(r"[\x00-\x1f\x7f]", model):
        qualified = _qualify(model)
        slot = cfg.setdefault("agents", {}).setdefault("defaults", {}).setdefault("model", {})
        if slot.get("primary") != qualified:
            changes.append(f"model.primary {slot.get('primary')} -> {qualified}")
            slot["primary"] = qualified
        inference = cfg.setdefault("models", {}).setdefault("providers", {}).setdefault("inference", {})
        models = inference.get("models")
        if not isinstance(models, list) or not models or not isinstance(models[0], dict):
            models = [{}]
            inference["models"] = models
        bare = qualified[len("inference/"):]
        model_changed = models[0].get("id") != bare or models[0].get("name") != qualified
        if model_changed:
            models[0]["id"] = bare
            models[0]["name"] = qualified
            changes.append(f"models[0] -> {qualified}")
        # Limits: onboard passes the session values. A value the base image baked
        # for *another* model is worse than none, so drop it when unset here - but
        # only when the model actually changed: the baked limits are right for the
        # baked model, and onboard does not forward them for it.
        for env_key, cfg_key in (("NEMOCLAW_CONTEXT_WINDOW", "contextWindow"),
                                 ("NEMOCLAW_MAX_TOKENS", "maxTokens")):
            raw = (env.get(env_key) or "").strip()
            if raw and re.fullmatch(r"[1-9][0-9]*", raw):
                if models[0].get(cfg_key) != int(raw):
                    models[0][cfg_key] = int(raw)
                    changes.append(f"{cfg_key} -> {raw}")
            elif model_changed and cfg_key in models[0]:
                del models[0][cfg_key]
                changes.append(f"{cfg_key} dropped (baked for another model, none supplied)")

    # --- control UI origins: onboard supplies CHAT_UI_URL --------------------
    gateway = cfg.setdefault("gateway", {})
    port = gateway.get("port") if isinstance(gateway.get("port"), int) else 18789
    ui = control_ui((env.get("CHAT_UI_URL") or "").strip(), port)
    if ui:
        current = gateway.setdefault("controlUi", {})
        if current.get("allowedOrigins") != ui["allowedOrigins"]:
            changes.append(f"allowedOrigins {current.get('allowedOrigins')} -> {ui['allowedOrigins']}")
        current.update(ui)

    if changes:
        with open(config, "w") as handle:
            json.dump(cfg, handle, indent=2)
            handle.write("\n")
    return changes


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    strict = "--require-change" in argv
    changes = apply()
    for line in changes:
        print(f"[vss-onboard-config] {line}", file=sys.stderr)
    if not changes:
        print("[vss-onboard-config] no onboard build args supplied; config left as the base image generated it", file=sys.stderr)
        if strict:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
