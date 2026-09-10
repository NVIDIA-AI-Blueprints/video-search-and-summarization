<!--
SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Hermes harness

Everything VSS needs to run on Hermes, in one place: Each agent harness has its own self-contained
top-level directory (`.openclaw/`, `.hermes/`); the `SKILL.md` tree at the repo
root (`skills/`) stays harness-neutral, and each directory packages it the way
that harness loads skills.

| Path | What it is |
|---|---|
| `Dockerfile` | The sandbox image: NemoClaw's managed Hermes runtime (digest-pinned) + the `vss` CLI + the VSS operation skills + the workspace docs |

Hermes has no plugin or tool layer to add: it drives the deployment through the
`vss` CLI on `PATH`, loads skills from its canonical writable root
(`/sandbox/.hermes/skills`, per NemoClaw's `agents/hermes/manifest.yaml`), and
reads its instruction docs (`SOUL.md`, `AGENTS.md`, `TOOLS.md`, `ENV.md`, …)
from `/sandbox`. The image puts each in place:

- **Skills** — every skill in the pinned checkout whose `SKILL.md` frontmatter
  declares `metadata.vss-requires` (the same rule the OpenClaw plugin uses),
  copied into `/sandbox/.hermes/skills/`, sandbox-owned like the ones
  `nemohermes <sb> skill install` places there. Unlike the OpenClaw image, all
  of them are active; each skill begins with `vss configure check` and reports
  what the deployment cannot serve.
- **Workspace docs** — `.openclaw/workspace/*.md` with the
  `_nemoclaw/` overlay applied, flattened into `/sandbox/`, exactly where
  `deploy_nemoclaw.ipynb` used to upload them for Hermes. The docs are shared
  with the OpenClaw harness and come from the pinned checkout, not from this
  directory, so there is one copy to maintain.
- **`vss` CLI** — from the same pinned commit, in its own venv
  (`/usr/local/vss`), never touching `/opt/hermes/.venv`.

## The base image

NemoClaw publishes the complete Hermes image, `ghcr.io/nvidia/nemoclaw/hermes-sandbox`,
by publication cohort only — no release tag
([NVIDIA/NemoClaw#11228](https://github.com/NVIDIA/NemoClaw/issues/11228)). The
cohort a release selects is the one its `openclaw-sandbox` release tag is
labeled with:

```
docker buildx imagetools inspect ghcr.io/nvidia/nemoclaw/openclaw-sandbox:v0.0.114 --format '{{json .}}' \
  | jq -r '.image | to_entries[0].value.config.Labels["io.nvidia.nemoclaw.managed-image.cohort"]'
# ghrun-32694475593-1
docker buildx imagetools inspect ghcr.io/nvidia/nemoclaw/hermes-sandbox:cohort-ghrun-32694475593-1
# Digest: sha256:32d7ed6a…  ← BASE_IMAGE
```

Move `BASE_IMAGE` together with `../openclaw/Dockerfile`'s and the notebook's
`NEMOCLAW_INSTALL_REF`: NemoClaw requires the CLI and the images to be one
release.

## Building

```
docker build -t <registry>/vss-harness-hermes:<tag> .hermes
```

`deploy_nemoclaw.ipynb` does this through `nemohermes onboard --from
.hermes/Dockerfile` when `AGENT_RUNTIME=hermes`. The eval harness's
Provision panel lists this Dockerfile next to the OpenClaw one.

| Build arg | Default | What it pins |
|---|---|---|
| `BASE_IMAGE` | `ghcr.io/nvidia/nemoclaw/hermes-sandbox@sha256:32d7…` (v0.0.114 cohort) | the managed runtime |
| `VSS_REPO`, `VSS_REF` | this repo, a commit sha | skills, workspace docs and the `vss` CLI |
| `BUILDER_IMAGE` | `node:22-trixie-slim@sha256:db8a…` | the checkout stage |

## Trial paths and sandbox contract

Identical to the OpenClaw image: `/task /output /logs /tests /solution` as real
world-writable directories, NemoClaw's per-shell `ulimit -u` hooks removed,
`CMD sleep infinity` so an exec-driven sandbox stays up, final `USER sandbox`
and `WORKDIR /sandbox`. `LABEL harness.agent=hermes`.
