<!--
SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Sandbox image

The agent sandbox the VSS eval harness runs: NemoClaw's managed OpenClaw
runtime, extended with the VSS OpenClaw plugin. **This directory is the single
source of truth** — the harness builds from here and from nothing outside this repo.

| Path | What it is |
|---|---|
| `openclaw-vss-cli.Dockerfile` | The image. `FROM` NemoClaw's published `openclaw-sandbox` (digest-pinned), plus the Harbor trial dirs, the `vss` CLI, and the VSS plugin installed with `openclaw plugins install` |
| `vss-plugin/` | The VSS OpenClaw plugin: `openclaw.plugin.json`, `package.json` + lockfile, `src/index.ts`. Ships the `vss_cli` tool and, at build time, the VSS skills |

## How it is put together

The layout is NemoClaw's documented custom-image workflow (NemoClaw docs →
*Install OpenClaw Plugins*): name the completed managed runtime
`nemoclaw-runtime`, build the plugin from its lockfile in a separate stage,
install it as the sandbox user, refresh the managed config hash, end with
`USER sandbox`. The doc builds the runtime from NemoClaw's stock Dockerfile with
a version-matched NemoClaw checkout as context; that Dockerfile copies from a
dozen places in the NemoClaw tree and cannot live here, so this image starts
from the image that Dockerfile produces, which NemoClaw publishes to
`ghcr.io/nvidia/nemoclaw/openclaw-sandbox`.

The VSS plugin does two things, both declared in its manifest:

- **`vss_cli` tool** — runs the pinned `vss` CLI with an argument array and
  returns exit code, stdout and stderr. The agent drives the VSS backends
  through a typed tool call instead of a free-form shell.
- **Skills** — `skills: ["./skills"]`. At build time every directory in this
  repo's `skills/` tree that holds a `SKILL.md` is copied under the plugin
  root, so OpenClaw loads them as plugin skills (`openclaw skills list` shows
  them with source `openclaw-extra`). The skills say which `vss` subcommands to
  reach for; the tool is how they are invoked.

Skills and the `vss` CLI come from one pinned commit of this repo (`VSS_REF`),
so they always match. `vss` is installed from source because `nvidia-vss` is on
no reachable index.

## Building

Build context is this directory:

```
cd vss-agent/sandboxes
docker build -f openclaw-vss-cli.Dockerfile -t <registry>/vss-harness-openclaw:<tag> .
```

The harness's Provision panel does the same: it shows the Dockerfile, lets an
operator edit it for a variant experiment, and builds a content-addressed image
with this directory (including `vss-plugin/`) as context. Real changes belong in
a PR here.

Pins are build args:

| Build arg | Default | What it pins |
|---|---|---|
| `BASE_IMAGE` | `ghcr.io/nvidia/nemoclaw/openclaw-sandbox@sha256:5a13…` (v0.0.99) | the managed runtime |
| `OPENCLAW_VERSION` | `2026.7.1` | the OpenClaw the base carries; the build fails if the plugin lockfile pins a different one |
| `VSS_REPO`, `VSS_REF` | this repo, a commit sha | the skills and the `vss` CLI |
| `BUILDER_IMAGE` | `node:22-trixie-slim@sha256:db8a…` | the plugin build stage (same as NemoClaw's) |
| `UV_IMAGE` | `ghcr.io/astral-sh/uv:0.12.10` | uv, for the `vss` venv |
| `NEMOCLAW_TOOL_DISCLOSURE` | `progressive` | NemoClaw tool disclosure mode |

Moving `BASE_IMAGE` to another NemoClaw release means moving `OPENCLAW_VERSION`
to the OpenClaw that release pins and regenerating the plugin lockfile
(`npm install --package-lock-only` in `vss-plugin/` after editing its
`devDependencies.openclaw`). NemoClaw's doc is explicit that a plugin image must
not mix one release's runtime with another's OpenClaw.

## Working on the plugin

```
cd vss-agent/sandboxes/vss-plugin
npm ci && npm run build          # type-check against the pinned OpenClaw SDK
```

`dist/`, `node_modules/` and `skills/` are build products and are not committed.
The manifest must list every tool in `contracts.tools`. Keep `openclaw` a
devDependency (release-matched) and peerDependency, never a dependency: the
build stage prunes it so the installed plugin links to the image's own runtime,
and the Dockerfile fails the build if that link is missing.

## One image, one harness

The image installs exactly one agent runtime and declares it with
`LABEL harness.agent=openclaw`, so an eval run pairs `(image, agent)`
unambiguously. Harbor (the eval orchestrator) runs **outside** the sandbox and
supplies the command; the image inherits NemoClaw's `nemoclaw-start` entrypoint
and gateway health check, and adds `/task /output /logs` (agent-writable) and
`/solution /tests` (not).

## Other harnesses

The plugin mechanism is OpenClaw's. NemoClaw ships Hermes, PI, LangChain Deep
Agents and NemoCUA as separate images with their own runtimes; Hermes has its
own plugin directory and skills path. For those, the `SKILL.md` tree in this
repo's `skills/` remains the portable format; only the `vss_cli` tool wrapper
is OpenClaw-specific.
