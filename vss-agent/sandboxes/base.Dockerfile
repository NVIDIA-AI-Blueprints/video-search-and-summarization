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
# The agent writes to /task, /output and /logs, and those are world-writable
# because a trial may run as a different uid than this image is built with.
RUN mkdir -p /task /output /logs/agent /logs/verifier /logs/artifacts \
    && chmod -R 777 /task /output /logs

# /solution and /tests belong to the ORACLE and VERIFIER planes and are
# deliberately NOT agent-writable or agent-readable. At 0777 an evaluated agent
# could read the expected answer and the verifier's own inputs, which does not
# fail loudly — it silently produces scores that mean nothing. Harbor uploads the
# real tests during the verifier phase, after the agent has finished, so nothing
# needs them to be open while the agent is running.
RUN mkdir -p /solution /tests && chmod 0755 /solution /tests

# The VSS skills (skills/ at the repo root) are what let an agent drive a live
# VSS deployment — vss-summarize-video, vss-ask-video, vss-deploy-profile and the
# rest. Baking them in is what makes an image self-contained: an eval run must not
# depend on the harness having network access to a skills repo at trial time.
# Build context is the repo root (see .github/workflows/sandbox-images.yml).
# They live under /usr/share, NOT /opt: the sandbox policy's filesystem_policy
# grants read on /usr, /lib, /app and /etc but says nothing about /opt, so
# Landlock denies a read there and the skills would be invisible to the agent
# ("Permission denied" on /opt/skills, even though the mode is 0777).
COPY skills/ /usr/share/vss-skills/
RUN chmod -R a+rX /usr/share/vss-skills
# One set of files, three names: the two paths the base's agents discover skills
# under, plus /opt/skills for anything that has it hard-coded. /sandbox is HOME
# for every user the policy may run as, so these resolve either way.
RUN ln -sfn /usr/share/vss-skills /opt/skills \
    && for d in /sandbox/.agents/skills /sandbox/.claude/skills; do \
         mkdir -p "$d"; \
         for s in /usr/share/vss-skills/*/; do ln -sfn "$s" "$d/$(basename "$s")"; done; \
       done \
    && chown -R sandbox:sandbox /sandbox/.agents /sandbox/.claude

# nvm compatibility shim. Every harness adapter that drives a node agent opens
# with some form of
#     . ~/.nvm/nvm.sh && nvm use 22 && node -v && npm -v
# because the images they were written against installed node through nvm. This
# base gets node 22 from the distro instead, so that line fails on a file that
# does not exist and takes the whole `&&` chain — and the trial — with it. The
# shim makes `nvm use`/`nvm install` succeed when the version asked for is the
# node already installed, and fail loudly otherwise; it never downloads
# anything, which matters because the sandbox egress policy would refuse.
RUN mkdir -p /sandbox/.nvm && cat > /sandbox/.nvm/nvm.sh <<'NVM' \
    && chown -R sandbox:sandbox /sandbox/.nvm
# Shim, not nvm. See vss-agent/sandboxes/base.Dockerfile.
nvm() {
  case "$1" in
    use|install)
      want="${2#v}"; want="${want%%.*}"
      have="$(node -v 2>/dev/null)"; have="${have#v}"; have="${have%%.*}"
      if [ -z "$want" ] || [ "$want" = "$have" ] || [ "$want" = "default" ] \
         || [ "$want" = "node" ] || [ "$want" = "lts" ]; then
        echo "Now using node $(node -v) (nvm shim)"
        return 0
      fi
      echo "nvm shim: this image ships node $(node -v); it cannot install v$want" >&2
      return 1 ;;
    current) node -v ;;
    which)   command -v node ;;
    ls|list) node -v ;;
    *)       return 0 ;;
  esac
}
NVM

# The `vss` CLI, so a trial drives the VSS backends directly rather than through
# vss-agent. Installed from git on purpose: nvidia-vss is published nowhere
# reachable — public PyPI, pypi.nvidia.com and the NVIDIA artifactory index that
# serves nvdataset all 404 it, and this repository has no publish workflow — so a
# `pip install nvidia-vss[cli]` layer can only work on a machine with an index
# nobody else has. Cloning the source is the honest way to get it.
#
# Pinned to a commit, not a branch: an eval must be able to say which CLI it
# measured. `vss` lives at services/agent/packages/vss_cli and is pulled in by
# the nvidia-vss meta package's `cli` extra.
ARG VSS_CLI_REF=a7cd4bc9d5ad513acfe38bc8724e9c37e64cd2cf
ARG VSS_CLI_REPO=https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization
USER root
RUN uv venv /opt/vss \
    && VIRTUAL_ENV=/opt/vss uv pip install \
         "nvidia-vss[cli] @ git+${VSS_CLI_REPO}@${VSS_CLI_REF}#subdirectory=services/agent" \
    && ln -sf /opt/vss/bin/vss /usr/local/bin/vss \
    && vss --version

USER sandbox
WORKDIR /task

LABEL harness.base="openshell-community" \
      harness.contract="harbor-trial-v1"

ENTRYPOINT []
CMD ["sleep", "infinity"]
