<!--
SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# OpenClaw harness

Everything VSS needs to run on OpenClaw, in one place:

| Path | What it is |
|---|---|
| `Dockerfile` | The sandbox image: NemoClaw's published managed OpenClaw runtime (digest-pinned) + the `vss` CLI + this plugin, installed with `openclaw plugins install` |
| `plugin/` | The VSS OpenClaw plugin: `openclaw.plugin.json`, `package.json` + lockfile, `src/index.ts`, `stage-assets.sh` |
| `workspace/` | The OpenClaw workspace instruction files (`AGENTS.md`, `SOUL.md`, `IDENTITY.md`, `TOOLS.md`, `BOOTSTRAP.md`) and the `_nemoclaw/` overlay for the sandbox (`ENV.md`, host alias, proxy notes) |

## The plugin

Declared in `plugin/openclaw.plugin.json`, built with `defineToolPlugin` from
the OpenClaw SDK:

- **`vss_cli` tool** — runs the pinned `vss` CLI with an argument array and
  returns exit code, stdout and stderr. The agent drives the VSS backends
  through a typed tool call instead of a free-form shell.
- **Skills** — `skills: ["./skills"]`. `stage-assets.sh` copies every directory
  holding a `SKILL.md` from the repo's `skills/` tree under the plugin root, so
  OpenClaw loads them as plugin skills (`openclaw skills list` shows them with
  source `openclaw-extra`). The skills say which `vss` subcommands to reach for;
  the tool is how they are invoked.
- **Workspace seeding** — at register time the plugin copies `workspace/*.md`
  into the agent's configured workspace (`agents.defaults.workspace`) when the
  files are not there yet, applying the `_<variant>` overlay first.
  `VSS_WORKSPACE_VARIANT` selects the overlay; unset, it is `nemoclaw` when
  running under NemoClaw's runtime. Existing files are never overwritten: the
  workspace is the agent's memory.

### Working on it

```
cd agent-harness/openclaw/plugin
npm ci && npm run build          # type-check and compile against the pinned OpenClaw SDK
npm run stage                    # stage skills/ and workspace/ from this checkout
```

`dist/`, `node_modules/`, `skills/` and `workspace/` under `plugin/` are build
products and are not committed. The manifest must list every tool in
`contracts.tools`. Keep `openclaw` a devDependency (release-matched) and
peerDependency, never a dependency: the image build prunes it so the installed
plugin links to the image's own runtime, and fails if that link is missing.

To use the plugin with a desktop OpenClaw instead of the sandbox image:

```
cd agent-harness/openclaw/plugin && npm ci && npm run prepare-local
openclaw plugins install "$PWD" && openclaw plugins enable vss
openclaw skills list | grep vss-
```

## The image

The layout is NemoClaw's documented custom-image workflow (NemoClaw docs →
*Install OpenClaw Plugins*): name the completed managed runtime
`nemoclaw-runtime`, build the plugin from its lockfile in a separate stage,
install it as the sandbox user, refresh the managed config hash, end with
`USER sandbox`. The doc builds the runtime from NemoClaw's stock Dockerfile with
a version-matched NemoClaw checkout as context; that Dockerfile copies from a
dozen places in the NemoClaw tree and cannot live here, so this image starts
from the image that Dockerfile produces, which NemoClaw publishes to
`ghcr.io/nvidia/nemoclaw/openclaw-sandbox`.

Skills and the `vss` CLI come from one pinned commit of this repo (`VSS_REF`),
so they always match. `vss` is installed from source because `nvidia-vss` is on
no reachable index. The workspace files come from this directory.

```
docker build -t <registry>/vss-harness-openclaw:<tag> agent-harness/openclaw
```

The eval harness's Provision panel does the same: it shows this Dockerfile,
lets an operator edit it for a variant experiment, and builds a
content-addressed image with this directory as context. Real changes belong in
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
(`npm install --package-lock-only` in `plugin/` after editing its
`devDependencies.openclaw`). NemoClaw's doc is explicit that a plugin image must
not mix one release's runtime with another's OpenClaw.

### Trial paths

`WORKDIR /sandbox` makes `/sandbox` the OpenShell workspace, which the sandbox
user owns and `openshell sandbox download` serves. Harbor and its agent adapters
address `/task /output /logs /tests /solution` by name, so the image creates
those as root-level symlinks into `/sandbox/`; the directories themselves are
created at trial start by the eval harness, as the sandbox user. Nothing under
the trial contract is pre-created or root-owned. The oracle and verifier inputs
(`/tests`, `/solution`) are therefore protected by ordering, not ownership:
Harbor uploads them only after the agent phase ends, and a sandbox is never
reused across trials.

## One image, one harness

The image installs exactly one agent runtime and declares it with
`LABEL harness.agent=openclaw`, so an eval run pairs `(image, agent)`
unambiguously. Harbor (the eval orchestrator) runs **outside** the sandbox and
supplies the command; the image inherits NemoClaw's `nemoclaw-start` entrypoint
and gateway health check.
