# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Sandbox image — OpenClaw harness. One image, one harness: the image names the
# harness under test and pins its version, so an eval run pairs (image, agent)
# unambiguously even though the base ships a couple of agents of its own.
# tags: [nemoclaw-lineage, openclaw]
ARG BASE_IMAGE=ghcr.io/nvidia-ai-blueprints/vss/vss-harness-base:latest
FROM ${BASE_IMAGE}

# The package is `openclaw` on the public npm registry (the scoped name
# @openclaw/openclaw does not exist — that was the reason earlier builds here
# thought it was private). Pinned to an exact version, not a
# dist-tag, so an eval run's harness does not move under it. 2026.9.1 and not
# extended-stable (2026.6.34): harbor 0.20.0's adapter opens with
#   openclaw setup --baseline --workspace .
# which extended-stable rejects — `OpenClaw does not recognize option
# "--baseline"` — killing the trial before the agent is asked anything.
# Override with
#   --build-arg OPENCLAW_NPM_SPEC='openclaw@<version>'
#   --build-arg OPENCLAW_NPM_REGISTRY=https://npm.internal.example.com/
ARG OPENCLAW_NPM_SPEC=openclaw@2026.9.1
ARG OPENCLAW_NPM_REGISTRY=
# Every openclaw release from 2026.7.1 on declares
#   engines.node >=22.22.3 <23 || >=24.15.0 <25 || >=25.9.0
# and refuses to install otherwise. The community base ships 22.22.1 — three
# patch versions short — so npm aborts with
#   [openclaw] error: this OpenClaw release requires Node >=22.22.3 ...
# Rather than downgrade openclaw (2026.6.34 predates the `openclaw setup
# --baseline` interface harbor 0.20.0 drives), install a satisfying node beside
# the distro one and put it first on PATH. `npm install -g node@x` fetches an
# official prebuilt binary, so this stays a pinned, reproducible layer.
# Into its own prefix: a plain `npm install -g node` tries to symlink
# /usr/bin/node and aborts with EEXIST against the distro one.
ARG NODE_NPM_SPEC=node@22.23.2
USER root
RUN npm install -g --prefix /opt/node "$NODE_NPM_SPEC" \
    && /opt/node/bin/node -v \
    && ln -sf /opt/node/bin/node /usr/local/bin/node
# Symlinked into /usr/local/bin rather than prepended to PATH: a login shell
# (`bash -lc`, which is how the harness adapters invoke everything) re-derives
# PATH from /etc/profile and drops anything set here, and /usr/local/bin already
# precedes /usr/bin. Without this, openclaw refuses to start at runtime even
# though it installed fine at build time.
# npm has to run ON the new node — the engines check reads process.version — but
# the `node` npm package ships only the binary, so npm itself still comes from
# the distro install and is invoked through its cli.js. --prefix is explicit
# because npm otherwise derives it from the running node and would bury the
# binary in /opt/node/lib/node_modules/node/bin, which is on nobody's PATH.
RUN if [ -n "$OPENCLAW_NPM_REGISTRY" ]; then npm config set registry "$OPENCLAW_NPM_REGISTRY"; fi \
    && node /usr/lib/node_modules/npm/bin/npm-cli.js install -g --prefix /usr/local "$OPENCLAW_NPM_SPEC" \
    && node /usr/lib/node_modules/npm/bin/npm-cli.js cache clean --force \
    && openclaw --version
USER sandbox

LABEL harness.agent="openclaw"

# The VSS skills come from the base at /opt/skills, symlinked into
# /sandbox/.agents/skills; the vss config.json lands at /sandbox/.vss/config.json.
