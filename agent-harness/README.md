<!--
SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Agent harnesses

One directory per agent harness VSS runs on. Each holds everything that harness
needs to drive a live VSS deployment, and nothing else: the sandbox image
definition, the harness-native packaging of the VSS skills, and the harness's
instruction files. The VSS eval harness builds its sandbox images from here;
**this tree is the single source of truth** for them.

| Directory | Harness | Status |
|---|---|---|
| [`openclaw/`](openclaw/) | OpenClaw, on NemoClaw's managed runtime | image + plugin + workspace |
| `hermes/` | Hermes | planned |
| `pi/` | PI coding agent | planned |

The `SKILL.md` tree at the repo root (`skills/`) is the portable form of the
skills and stays harness-neutral. A harness directory packages it the way that
harness loads skills (OpenClaw: a plugin with `skills: ["./skills"]`) and adds
the harness-specific pieces (OpenClaw: a `vss_cli` tool and the workspace
instruction files).

Each image keeps the same contract with the eval harness: one agent runtime,
declared with `LABEL harness.agent=<name>`; the OpenShell workspace at
`/sandbox`; the Harbor trial paths `/task /output /logs /tests /solution` as
root-level names resolving into that workspace, created at trial start by the
sandbox user.
