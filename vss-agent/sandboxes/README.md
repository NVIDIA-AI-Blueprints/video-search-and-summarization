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

The harness's Provision panel lists these files, lets an operator edit one for a
variant experiment, builds a content-addressed image from the edited text, and
pulls the published base from GHCR onto the cluster. Real changes belong in a PR
here, not in the running environment.

## Contract

Sandboxes must keep: the contract dirs above, a declared `USER` (OpenShell
requirement), agent state relocatable via env (no `$HOME` assumptions baked into
paths), and no reliance on an entrypoint — the harness supplies the command.
