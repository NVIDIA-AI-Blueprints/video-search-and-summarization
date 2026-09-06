<!--
SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Sandbox image

The Dockerfile for the agent sandbox the VSS eval harness runs. **This directory
is the single source of truth** — the harness builds its sandbox image from this
file and from nothing kept outside this repo.

| File | What it builds |
|---|---|
| `openclaw-vss-cli.Dockerfile` | OpenShell community sandbox base (pinned by digest) + the Harbor trial dirs + the VSS skills + the `vss` CLI + OpenClaw, all pinned |

The image is self-contained: it needs no build context beyond the Dockerfile
itself. Everything it adds is either already in the base or fetched by a pinned
`RUN` layer (the VSS skills and `vss` CLI from a commit of this repo, OpenClaw
and a satisfying node from the public npm registry).

## Building

```
docker build -f vss-agent/sandboxes/openclaw-vss-cli.Dockerfile \
  -t <registry>/vss-harness-openclaw-vss-cli:<tag> vss-agent/sandboxes
```

The harness's Provision panel does the same thing: it shows this file, lets an
operator edit it for a variant experiment, and builds a content-addressed image
from the edited text. Real changes belong in a PR here, not in the running
environment.

Pins are build args, so a downstream build can move one without editing the file:

| Build arg | Default | What it pins |
|---|---|---|
| `OPENSHELL_BASE` | `ghcr.io/nvidia/openshell-community/sandboxes/base@sha256:…` | the sandbox base |
| `VSS_REPO`, `VSS_REF` | this repo, a commit sha | the skills and the `vss` CLI |
| `OPENCLAW_NPM_SPEC`, `OPENCLAW_NPM_REGISTRY` | `openclaw@2026.9.1`, public npm | the harness under test |
| `NODE_NPM_SPEC` | `node@22.23.2` | the node OpenClaw runs on (the base's 22.22.1 is below OpenClaw's `engines.node`) |

This repository does not publish the image and does not name a registry. It owns
the *definition*; where a build lands is the operator's decision.

## One image, one harness

The image installs exactly one agent runtime and declares it with
`LABEL harness.agent=openclaw`, so an image identifies the harness under test
and an eval run pairs `(image, agent)` unambiguously. Comparing harnesses means
running the same task tree against several images — not one image with several
CLIs on `PATH`, which would leave the agent selectable at runtime.

Harbor (the eval orchestrator) runs **outside** the sandbox: it creates the
sandbox from the image and drives the agent adapter inside it. Nothing about the
eval loop is baked into the image.

## Contract

The image keeps: the Harbor trial dirs (`/task /output /logs` agent-writable,
`/solution /tests` not), a declared `USER` (OpenShell requirement), agent state
relocatable via env (no `$HOME` assumptions baked into paths), and no reliance
on an entrypoint — the harness supplies the command.

Because the harness supplies the command and runs it **non-interactively**, every
agent binary has to be on `PATH` without a shell hook: nvm's `~/.bashrc` snippet
is never sourced by `docker exec`/OpenShell, and Ubuntu's `.bashrc` returns early
for non-interactive shells. `openclaw` and `vss` are therefore linked into
`/usr/local/bin`, and a small `~/.nvm/nvm.sh` shim keeps adapters that open with
`. ~/.nvm/nvm.sh && nvm use 22` working against the node already installed.
