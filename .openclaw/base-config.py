#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Configure the OpenClaw base image's tools (Dockerfile.base).

Tools are switched off by name, never allowlisted: `tools.allow` would also
filter every tool a plugin installed later registers, and the base exists so
users can add skills and plugins when they evaluate it. Kept built-ins: exec,
process, read, write, edit, apply_patch, memory_search, memory_get.

  - DENY: every other built-in tool (OpenClaw's tool groups), plus
    code_execution (remote Python on xAI) and node_inference (the ollama
    provider plugin's tool; the provider itself stays). group:memory stays
    enabled (see Memory below).
  - DISABLE: the bundled plugins that register tools, so their tools and
    skills are gone until a user re-enables them. memory-core stays enabled
    (see Memory below).

Memory: on by default, matching the vss-plugin's vss_cli tool also being
preloaded in this base (../.hermes/base-config.py keeps the two harnesses'
bases in lockstep). group:memory's built-ins (memory_search, memory_get) and
the memory-core bundled plugin both stay enabled — an agent extended with VSS
skills and plugins should retain memory across turns out of the box, not
silently lose it because it started from the base image.

Also writes /etc/openclaw-harness/config-overlay.json: Harbor replaces
openclaw.json at trial time and deep-merges only that overlay back in, so the
tool policy must be published there too. Run before the config hash refresh.
"""

from __future__ import annotations

import json
from pathlib import Path

CONFIG = Path("/sandbox/.openclaw/openclaw.json")
OVERLAY = Path("/etc/openclaw-harness/config-overlay.json")

DENY = [
    "code_execution",                                                    # group:runtime
    "sessions_list", "sessions_history", "sessions_send", "sessions_spawn",
    "sessions_yield", "subagents", "session_status",                     # group:sessions
    "web_search", "x_search", "web_fetch",                               # group:web
    "browser", "canvas",                                                 # group:ui
    "heartbeat_respond", "cron", "gateway",                              # group:automation
    "message",                                                           # group:messaging
    "nodes",                                                             # group:nodes
    "agents_list", "get_goal", "create_goal", "update_goal",
    "update_plan", "skill_workshop",                                     # group:agents
    "image", "image_generate", "music_generate", "video_generate", "tts",  # group:media
    "node_inference",                                                    # ollama plugin
]
DISABLE = ["browser", "canvas", "file-transfer"]


def main() -> None:
    cfg = json.loads(CONFIG.read_text())
    tools = cfg.setdefault("tools", {})
    if "allow" in tools:
        raise SystemExit("base image must not set tools.allow")
    tools["deny"] = DENY
    tools["toolSearch"] = False
    web = tools.setdefault("web", {})
    web.setdefault("fetch", {})["enabled"] = False
    web.setdefault("search", {})["enabled"] = False
    entries = cfg.setdefault("plugins", {}).setdefault("entries", {})
    for plugin in DISABLE:
        entries.setdefault(plugin, {})["enabled"] = False
    CONFIG.write_text(json.dumps(cfg, indent=2) + "\n")  # in place: keeps owner and mode

    OVERLAY.parent.mkdir(parents=True, exist_ok=True)
    overlay = {"plugins": cfg["plugins"], "tools": {k: tools[k] for k in ("deny", "toolSearch", "web")}}
    OVERLAY.write_text(json.dumps(overlay, indent=2) + "\n")


if __name__ == "__main__":
    main()
