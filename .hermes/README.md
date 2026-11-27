<!--
SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Hermes Harness

Image and OpenShell policy for running the Hermes agent harness against a VSS deployment. For end-to-end gateway and sandbox bring-up, see [`../services/openshell/README.md`](../services/openshell/README.md).

| Path | Purpose |
|---|---|
| `Dockerfile` | Builds `openshell-hermes-vss` from upstream Hermes (`HERMES_REF`), `libs/vss`, `skills/`, `plugin/`, and `policy.yaml` |
| `policy.yaml`, `boundary.yaml` | Sandbox policy and ceiling verified by `openshell-prover check` |
| `plugin/` | `vss_cli` tool plugin (`plugin.yaml`, `__init__.py`), matching the OpenClaw tool contract |
| `workspace/` | `/sandbox/AGENTS.md` and `/sandbox/TOOLS.md` loaded into Hermes sessions |

## Build

Run from the repository root:

```bash
VERSION="$(cd libs/vss/cli && uvx --with hatch-vcs hatch version)"
docker build -f .hermes/Dockerfile \
  --build-arg HERMES_REF=v2026.9.14 \
  --build-arg VSS_PACKAGE_VERSION="$VERSION" \
  -t "openshell-hermes-vss:${VERSION//+/-}-from-source" .
```

| Build arg | Default | Purpose |
|---|---|---|
| `BASE_IMAGE` | `nvcr.io/nvidia/base/ubuntu:24.04` | OS base image |
| `HERMES_REPO`, `HERMES_REF` | `https://github.com/NousResearch/hermes-agent`, `v2026.9.14` | Upstream Hermes release tag |
| `UV_IMAGE` | `ghcr.io/astral-sh/uv:0.11.24` | Build-time Python 3.13 + wheel/venv installer |
| `VSS_PACKAGE_VERSION` | `0.0.0+local` | SemVer stamped into `nvidia-vss-core` / `nvidia-vss-cli` wheels |

## Policy Verification

```bash
openshell-prover check .hermes/policy.yaml --boundary .hermes/boundary.yaml
# result: within_boundary
```

`policy.yaml` grants `host.openshell.internal:7777` to `/usr/bin/python3.13` and `/usr/bin/curl`, plus read-only `GET` access to PyPI and GitHub for `/usr/bin/python3.13`. Inference model egress is authorized separately by the provider profile (`services/openshell/providers/*.yaml`).

## Skill Sync & Sandbox Contract

- Bundled operational skills live read-only under `/opt/vss-skills/skills` and are pre-activated in `/opt/hermes/skills/vss`. After pointing the CLI at a live deployment (`vss configure <url>`), run `vss-hermes-sync` inside the sandbox to filter `$HERMES_HOME/skills/vss` against `vss configure check`.
- Runs as `sandbox` (`998:999`) with `WORKDIR /sandbox` and `CMD ["sleep", "infinity"]`.
- Pre-creates writable Harbor trial directories (`/task`, `/output`, `/logs`, `/tests`, `/solution`) listed in `policy.yaml` (`LABEL harness.contract=harbor-trial-v1`).
- Declares empty `VSS_VLM_*` environment defaults (`VSS_VLM_LOCKED=false`); see [`libs/vss/cli/README.md`](../libs/vss/cli/README.md#configure-vlm-requests).
