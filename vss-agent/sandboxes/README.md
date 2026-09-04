<!--
SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Sandbox images

Dockerfiles for the agent sandboxes the VSS eval harness runs. **This directory is
the single source of truth** — nothing in the harness builds an image from a file
kept outside this repo.

| File | Image | What it adds |
|---|---|---|
| `base.Dockerfile` | `ghcr.io/<owner>/vss/vss-harness-base` | Ubuntu 24.04 substrate: python, node/nvm, git, the Harbor contract dirs (`/task /output /logs /solution /tests`), declared OCI `USER` for OpenShell |
| `openclaw.Dockerfile` | `…/vss-harness-openclaw` | OpenClaw agent runtime |
| `openclaw-vss-cli.Dockerfile` | `…/vss-harness-openclaw-vss-cli` | OpenClaw + pinned `vss` CLI |
| `hermes.Dockerfile` | `…/vss-harness-hermes` | Hermes runtime |
| `codex.Dockerfile` | `…/vss-harness-codex` | Codex CLI (dev-team PIC runtime) |
| `pi.Dockerfile` | `…/vss-harness-pi` | PI coding agent (dev-team PICs, NVIDIA inference) |

## Publishing

`.github/workflows/sandbox-images.yml` builds the base, pushes it to GHCR, then
builds each variant `FROM` the freshly published base. Pull requests build
everything but push nothing (fork PRs have no package write token). Tags:
`latest` on the default branch, plus `sha-<commit>` and branch/PR tags.

## Consuming

Every variant takes a `BASE_IMAGE` build-arg (default: the published `latest`
base), so a downstream build can pin an exact base by digest:

```bash
docker build -f pi.Dockerfile \
  --build-arg BASE_IMAGE=ghcr.io/<owner>/vss/vss-harness-base:sha-<commit> .
```

Two variants also need to be told where their runtime comes from, because the
packages are not on the public registries:

| Dockerfile | Build args | Why |
|---|---|---|
| `openclaw.Dockerfile` | `OPENCLAW_NPM_SPEC`, `OPENCLAW_NPM_REGISTRY` | `@openclaw/openclaw` is not on `registry.npmjs.org` (404) |
| `openclaw-vss-cli.Dockerfile` | the two above plus `VSS_CLI_SPEC`, `VSS_PIP_INDEX_URL` | `nvidia-vss` is not on public PyPI (404) |

`.github/workflows/sandbox-images.yml` does not pass them, so those two matrix
legs only succeed on a runner whose default npm/pip registries carry the
packages.

The harness's Provision panel lists these files, lets an operator edit one for a
variant experiment, builds a content-addressed image from the edited text, and
pulls the published base from GHCR onto the cluster. Real changes belong in a PR
here, not in the running environment.

## One image, one harness

The base carries **no agent**. Each variant installs exactly one runtime and
declares it with `LABEL harness.agent=<name>`, so an image identifies the harness
under test and an eval run pairs `(image, agent)` unambiguously. Comparing
harnesses means running the same task tree against several images — not one image
with several CLIs on `PATH`, which would leave the agent selectable at runtime and
let a misbehaving agent invoke a different one.

Harbor (the eval orchestrator) runs **outside** the sandbox: it creates the
sandbox from the image and drives the agent adapter inside it. Nothing about the
eval loop is baked into these images.

## Contract

Sandboxes must keep: the contract dirs above, a declared `USER` (OpenShell
requirement), agent state relocatable via env (no `$HOME` assumptions baked into
paths), and no reliance on an entrypoint — the harness supplies the command.

Because the harness supplies the command and runs it **non-interactively**, every
agent binary has to be on `PATH` without a shell hook: nvm's `~/.bashrc` snippet
is never sourced by `docker exec`/OpenShell, and Ubuntu's `.bashrc` returns early
for non-interactive shells. `base.Dockerfile` therefore pins
`$NVM_DIR/default/bin` (a stable symlink to the aliased node version) and
`~/.local/bin` into `ENV PATH`. A variant that installs its runtime somewhere
else must extend `PATH` itself.
