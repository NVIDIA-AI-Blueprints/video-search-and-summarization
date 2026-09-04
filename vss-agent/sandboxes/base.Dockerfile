# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# vss-harness-base — the substrate every sandbox image in this catalog builds on.
#
# It derives from the OpenShell/NemoClaw community sandbox base rather than
# rebuilding one: that image is what `openshell sandbox create --from base`
# resolves to, and it already carries the default sandbox policy
# (/etc/openshell/policy.yaml), the agent-skills layout (/sandbox/.agents/skills),
# uv-managed python and the node toolchain. Evaluating agents in the environment
# they actually run in is the point — a bespoke base would drift from it.
#
# Note it is not agent-free: it already carries codex and claude. The variants
# still exist because an eval run must pin the harness it is measuring — a
# variant either installs an agent the base lacks (hermes, pi, openclaw) or
# pins the version of one it has (codex).
#
# What we add is only what the eval harness contracts require: the Harbor trial
# directories, and a stable place for skills the harness bakes in.
# Pinned by digest, not :latest — a mutable tag with IfNotPresent silently
# serves whatever the node happened to cache first. Refresh deliberately.
ARG OPENSHELL_BASE=ghcr.io/nvidia/openshell-community/sandboxes/base@sha256:aeef1c63f00e2913ea002ccb3aaf925f338b5c5d70e63576f0d95c16a138044e
FROM ${OPENSHELL_BASE}

USER root

# Harbor trial contract: the agent works in /task, writes its answer to /output,
# logs to /logs; /solution and /tests belong to the oracle and verifier planes.
# World-writable because the trial may run as a different uid than we build with.
RUN mkdir -p /task /output /logs/agent /logs/verifier /logs/artifacts /solution /tests \
    && chmod -R 777 /task /output /logs /solution /tests

# The VSS skills (skills/ at the repo root) are what let an agent drive a live
# VSS deployment — vss-summarize-video, vss-ask-video, vss-deploy-profile and the
# rest. Baking them in is what makes an image self-contained: an eval run must not
# depend on the harness having network access to a skills repo at trial time.
# Build context is the repo root (see .github/workflows/sandbox-images.yml).
RUN mkdir -p /opt/skills && chmod 777 /opt/skills
COPY --chown=sandbox:sandbox skills/ /opt/skills/
# The community base discovers agent skills under these two paths; symlink rather
# than copy so all three views stay one set of files.
RUN for d in /sandbox/.agents/skills /sandbox/.claude/skills; do \
      mkdir -p "$d"; \
      for s in /opt/skills/*/; do ln -sfn "$s" "$d/$(basename "$s")"; done; \
    done \
    && chown -R sandbox:sandbox /sandbox/.agents /sandbox/.claude

USER sandbox
WORKDIR /task

LABEL harness.base="openshell-community" \
      harness.contract="harbor-trial-v1"

ENTRYPOINT []
CMD ["sleep", "infinity"]
