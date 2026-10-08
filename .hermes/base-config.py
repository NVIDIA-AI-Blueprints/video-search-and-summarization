#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Configure and check the Hermes base image's tools (Dockerfile.base).

`apply` (root, at build) switches off every toolset the base does not ship and
re-hashes the managed config. `check` (sandbox user, at build) fails unless the
agent resolves to exactly KEEP on both platforms the image serves, and a fresh
HERMES_HOME seeds no skills.

Toolsets are switched off by name, never allowlisted: a toolset a user adds
later (a plugin, an MCP server) is on by default, so the base stays extensible.
That is also why OFF names leaf toolsets only: composites such as `coding` or
`debugging` re-list kept tools and would strip them too.

Run with /opt/hermes/.venv/bin/python3 (PyYAML and Hermes import from it).
"""

from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

HERMES = "/opt/hermes"
CONFIG = Path("/sandbox/.hermes/config.yaml")
ENV_FILE = Path("/sandbox/.hermes/.env")
HASH_FILES = (Path("/etc/nemoclaw/hermes.config-hash"), Path("/sandbox/.hermes/.config-hash"))

# Every toolset except terminal, file, skills and code_execution, plus the NemoClaw plugin's
# own (nemoclaw, audio). Toolsets that are off by default are listed too, so a
# runtime bump that flips a default does not bring them back.
OFF = [
    "web", "search", "x_search", "browser", "computer_use", "desktop_ui",
    "vision", "video", "image_gen", "video_gen", "tts",
    "todo", "session_search", "clarify", "delegation", "cronjob",
    "kanban", "project", "context_engine", "homeassistant", "spotify",
    "yuanbao", "discord", "discord_admin", "feishu_doc", "feishu_drive",
    "nemoclaw", "audio",
]
KEEP = {
    "terminal", "process",                                   # terminal
    "read_file", "write_file", "patch", "search_files",      # file
    "skills_list", "skill_view", "skill_manage",             # skills
    "execute_code",                                          # code_execution
    "memory",                                                # memory, on by default (see apply())
    "vss_cli",                                               # vss plugin (see ../.openclaw/plugin parity)
}
PLATFORMS = ("cli", "api_server")


def apply() -> None:
    cfg = yaml.safe_load(CONFIG.read_text())
    cfg.setdefault("agent", {})["disabled_toolsets"] = OFF
    # Tool search defers plugin tools behind tool_search/tool_describe/tool_call;
    # off, a tool a user's plugin adds (the vss plugin included) is sent to the
    # model directly.
    cfg.setdefault("tools", {}).setdefault("tool_search", {})["enabled"] = False
    # Memory on by default: MEMORY.md and USER.md enter the prompt, matching
    # the OpenClaw base keeping group:memory + memory-core enabled
    # (../.openclaw/base-config.py) so an agent extended from either base
    # retains memory across turns without extra setup.
    memory = cfg.setdefault("memory", {})
    memory["memory_enabled"] = True
    memory["user_profile_enabled"] = True
    with CONFIG.open("w") as f:  # in place: keeps sandbox ownership and mode
        yaml.safe_dump(cfg, f, sort_keys=False)
    # nemoclaw-start verifies these hashes at sandbox start.
    digest = subprocess.run(
        ["sha256sum", str(CONFIG), str(ENV_FILE)], check=True, capture_output=True, text=True
    ).stdout
    for path in HASH_FILES:
        path.write_text(digest)


def check() -> None:
    sys.path.insert(0, HERMES)
    from hermes_cli import tools_config
    from hermes_cli.plugins import discover_plugins
    import model_tools
    from tools.skills_sync import sync_skills

    # The vss plugin (bundled at /opt/hermes/plugins/vss, kind: backend, by
    # ./Dockerfile.base) auto-loads and registers vss_cli at discovery time;
    # get_tool_definitions() below only sees it if discovery has already run
    # in this process.
    discover_plugins()

    cfg = yaml.safe_load(CONFIG.read_text())
    disabled = cfg["agent"]["disabled_toolsets"]
    for platform in PLATFORMS:
        with contextlib.redirect_stdout(io.StringIO()):
            enabled = tools_config._get_platform_tools(cfg, platform)
            tools = model_tools.get_tool_definitions(
                enabled_toolsets=sorted(enabled), disabled_toolsets=disabled, quiet_mode=True
            )
        names = {t["function"]["name"] for t in tools}
        if names != KEEP:
            sys.exit(f"{platform}: tools {sorted(names)} != expected {sorted(KEEP)}")
        print(f"{platform}: {sorted(names)}")

    # Hermes seeds $HERMES_HOME/skills from its bundled library on every launch.
    with tempfile.TemporaryDirectory() as home:
        os.environ["HERMES_HOME"] = home
        with contextlib.redirect_stdout(io.StringIO()):
            sync_skills(quiet=True)
        seeded = sorted(str(p) for p in Path(home, "skills").rglob("SKILL.md"))
        if seeded:
            sys.exit(f"fresh HERMES_HOME seeded skills: {seeded}")
    print("skills: none seeded")


if __name__ == "__main__":
    {"apply": apply, "check": check}[sys.argv[1]]()
