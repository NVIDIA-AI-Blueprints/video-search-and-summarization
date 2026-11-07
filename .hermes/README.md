<!--
SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Hermes harness

The Hermes agent harness and the access boundary it runs under. Each harness
owns a top-level directory (`.openclaw/`, `.hermes/`); the `SKILL.md` tree at
the repo root (`skills/`) stays harness-neutral, and each directory packages it
the way that harness loads skills.

| Path | What it is |
|---|---|
| `Dockerfile` | The sandbox image: Hermes built from its own public source on a standard Ubuntu base, with `policy.yaml` baked in. No VSS content — see [Adding VSS](#adding-vss) |
| `policy.yaml`, `boundary.yaml` | The OpenShell policy this image is built against, and the ceiling `openshell-prover check` proves it stays inside |
| `plugin/` | The VSS Hermes plugin: `plugin.yaml`, `__init__.py` (the `vss_cli` tool, registered via `ctx.register_tool`) |

Bring-up — the gateway, the provider profile, the sandbox and the verification
steps — is in [`../.openshell/README.md`](../.openshell/README.md). This file
covers the image and the plugin only.

## Building

```
docker build --build-arg HERMES_REF=v2026.9.14 \
  -t openshell-hermes:v2026.9.14 .hermes
```

`HERMES_REF` has to be passed as well as named in the tag: Docker does not read
the environment for build args, so naming it only in `-t` builds the
Dockerfile's default ref under a tag claiming a different one. Keep it on a
Hermes release tag rather than a branch.

| Build arg | Default | What it pins |
|---|---|---|
| `BASE_IMAGE` | `nvcr.io/nvidia/base/ubuntu:24.04` | the OS layer |
| `HERMES_REPO`, `HERMES_REF` | upstream Hermes, `v2026.9.14` (0.21.3) | the agent |
| `UV_IMAGE` | `ghcr.io/astral-sh/uv:0.11.6` | the Python 3.13 install and the venv build |

The toolchain purge is by construction rather than by removal: the runtime
stage installs no compiler, no node and no network client beyond `git`, so
there is nothing to strip afterwards. Every build happens in an earlier stage
and only its output is copied.

Two things the image leaves to the sandbox's start command, both because the
values are not known at build time:

- **CA bundle.** `trust-supervisor-ca` writes certifi's public roots and the
  supervisor's mounted CA together to `SSL_CERT_FILE` under `/sandbox`.
  certifi's own bundle cannot take the append: it is root-owned under `/opt`,
  which the policy mounts read-only.
- **Inference target.** `config.yaml` carries the endpoint, model and key as
  `${env:HARNESS_BASE_URL}`, `${env:HARNESS_MODEL}` and
  `${env:HARNESS_API_KEY}`; the create command renders the first two and the
  egress proxy injects the third.

## The policy pair

`policy.yaml` is baked to `/etc/openshell/policy.yaml`, so one artifact carries
the harness and the access boundary it was proven against. It grants the
agent's own model endpoint and the package hosts, and nothing else.

```
openshell-prover check .hermes/policy.yaml --boundary .hermes/boundary.yaml
# result: within_boundary
```

`binaries` names `/usr/bin/python3.13` and the process identity is `998:999`,
both pinned by the image. Why those values, and what makes the prover return
`unsupported` rather than a pass, is in
[`../.openshell/README.md`](../.openshell/README.md#4-quickstart-steps-explained).

## Adding VSS

The image here has no `vss` CLI, no operation skills and no grant to reach a
deployment. [`../.openshell/vss/`](../.openshell/vss/) is the optional layer
that adds all three: it takes this image as its base, installs the CLI, the
skills, the workspace docs and the plugin from `plugin/`, and replaces the
baked policy with one carrying the `vss-backend` and `ngc` grants.

The plugin lives here rather than there because it is Hermes code: it mirrors
the OpenClaw plugin's tool exactly — same name, same `{args, cwd, timeoutSec}`
schema, same no-shell subprocess exec, same `{command, exitCode, signal,
timedOut, stdout, stderr, truncated}` result shape. See `./plugin/__init__.py`.

The VSS layer reads it from the pinned `VSS_REF` checkout, not from a build
context, so a local edit reaches the image only once it is pushed.

## Skill discovery

Hermes loads skills from `$HERMES_HOME/skills` only, and seeds that directory
from its bundled library (`/opt/hermes/skills`) on every launch, tracking what
it copied in `.bundled_manifest`. The VSS layer fills `/opt/hermes/skills/vss/`
from the shipped set, so no caller has to run anything first, whichever
`HERMES_HOME` it picks.

`vss-hermes-sync` passes `$HERMES_HOME/skills/vss` as `--active-dir`
(`/sandbox/.hermes/skills/vss` when `HERMES_HOME` is unset), so it never
touches Hermes' own skills, and Hermes does not re-add a skill that
`vss-hermes-sync` deselected. At build, `--all` activates every shipped skill;
after `vss configure` records a deployment, run `vss-hermes-sync` in the
sandbox to re-select against `vss configure check`.

The machinery is `skills/vss-build-vision-ai/scripts/sync_skills.py`,
stdlib-only, with its behavior pinned by unit tests beside it. The same file
serves the OpenClaw plugin and host tooling.

## Sandbox contract

`CMD sleep infinity` so an exec-driven sandbox stays up, final `USER sandbox`
and `WORKDIR /sandbox`. `LABEL harness.agent=hermes`.

`vss vlm run` request defaults are runtime variables the VSS layer declares:
every `VSS_VLM_*` setting (`VSS_VLM_BACKEND`, `VSS_VLM_MODEL`, `VSS_VLM_FPS`,
`VSS_VLM_MAX_FRAMES`, `VSS_VLM_TOTAL_PIXELS`, …; full list in
[the CLI README](../libs/vss/cli/README.md#configure-vlm-requests)) is empty,
which the CLI reads as unset, and `VSS_VLM_LOCKED=false`. Set
`VSS_VLM_LOCKED=true` to make the values the sandbox sets win over saved ones
and lock them against the agent's own overrides. Set them on the sandbox
(`openshell sandbox create --env VSS_VLM_FPS=2`, `docker run -e …`); no build
arg is involved. `VSS_VLM_API_KEY` is set the same way and is not declared, so
the Bearer token never appears in the image.
