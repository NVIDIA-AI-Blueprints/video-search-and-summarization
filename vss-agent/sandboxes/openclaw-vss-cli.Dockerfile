# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Sandbox image — OpenClaw harness + pinned vss CLI + the VSS skills.
# tags: [nemoclaw-lineage, openclaw, vss-cli]
#
# Self-contained on purpose: this is the one sandbox image the VSS eval harness
# builds, and it is built from this file alone (the harness's Provision panel
# hands docker only the Dockerfile text, no repo checkout), so everything the
# image needs is either in the base or fetched by a pinned RUN layer below.
#
# It derives from the OpenShell/NemoClaw community sandbox base rather than
# rebuilding one: that image is what `openshell sandbox create --from base`
# resolves to, and it already carries the default sandbox policy
# (/etc/openshell/policy.yaml), the agent-skills layout (/sandbox/.agents/skills),
# uv-managed python and the node toolchain. Evaluating an agent in the
# environment it actually runs in is the point — a bespoke base would drift.
# Pinned by digest, not :latest — a mutable tag with IfNotPresent silently
# serves whatever the node happened to cache first. Refresh deliberately.
ARG OPENSHELL_BASE=ghcr.io/nvidia/openshell-community/sandboxes/base@sha256:aeef1c63f00e2913ea002ccb3aaf925f338b5c5d70e63576f0d95c16a138044e
FROM ${OPENSHELL_BASE}

USER root

# ── Harbor trial contract ──────────────────────────────────────────────────────
# The agent works in /task, writes its answer to /output, logs to /logs. These
# are world-writable because a trial may run as a different uid than this image
# is built with.
RUN mkdir -p /task /output /logs/agent /logs/verifier /logs/artifacts \
    && chmod -R 777 /task /output /logs
# /solution and /tests belong to the ORACLE and VERIFIER planes and are
# deliberately NOT agent-writable. At 0777 an evaluated agent could read the
# expected answer and the verifier's own inputs, which does not fail loudly — it
# silently produces scores that mean nothing. Harbor uploads the real tests
# during the verifier phase, after the agent has finished.
RUN mkdir -p /solution /tests && chmod 0755 /solution /tests

# ── VSS source: skills + vss CLI, one pinned checkout ──────────────────────────
# Pinned to a commit, not a branch: an eval must be able to say which skills and
# which CLI it measured. Override with --build-arg VSS_REF=<sha>.
#
# The `vss` CLI is installed from source because nvidia-vss is published nowhere
# reachable — public PyPI, pypi.nvidia.com and the NVIDIA artifactory index all
# 404 it — so a `pip install nvidia-vss[cli]` layer can only work on a machine
# with an index nobody else has. `vss` lives at services/agent/packages/vss_cli
# and is pulled in by the nvidia-vss meta package's `cli` extra. Its own venv, so
# it can never perturb /sandbox/.venv, the interpreter the agent's tooling runs from.
# The CLI packages are uv workspace members, which uv installs as *editable* links
# into the checkout, so the checkout stays in the image (git metadata and the rest
# of the tree stripped) rather than being deleted after the install.
#
# The skills (skills/ at the repo root) are what let the agent drive a live VSS
# deployment — vss-summarize-video, vss-ask-video, vss-deploy-profile and the
# rest. Baking them in is what makes the image self-contained: a trial must not
# depend on network access to a skills repo at run time.
#
# Everything lands under /usr/local or /usr/share, NOT /opt: the sandbox policy's
# filesystem_policy grants read on /usr, /lib, /app and /etc but says nothing
# about /opt, so Landlock denies a read there and the skills (or the venv) would
# be invisible to the agent. /opt/skills is kept only as a symlink for anything
# that has it hard-coded.
# One set of skill files, three names: every directory holding a SKILL.md (the
# tree may be flat or grouped by category) is linked by name into the two paths
# the base's agents discover skills under, and the whole tree is /opt/skills.
# /sandbox is HOME for every user the policy may run as, so these resolve either way.
ARG VSS_REPO=https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization
ARG VSS_REF=a7cd4bc9d5ad513acfe38bc8724e9c37e64cd2cf
# The install runs on the intact checkout because the packages take their version
# from git metadata (setuptools-scm); only afterwards is the tree stripped down to
# what the editable links point at.
RUN set -eux; \
    git init -q /usr/local/src/vss \
    && git -C /usr/local/src/vss fetch -q --depth 1 "$VSS_REPO" "$VSS_REF" \
    && git -C /usr/local/src/vss checkout -q FETCH_HEAD \
    && uv venv /usr/local/vss \
    && VIRTUAL_ENV=/usr/local/vss uv pip install "nvidia-vss[cli] @ /usr/local/src/vss/services/agent" \
    && ln -sf /usr/local/vss/bin/vss /usr/local/bin/vss \
    && mv /usr/local/src/vss/skills /usr/share/vss-skills \
    && mv /usr/local/src/vss/services/agent /usr/local/src/vss-agent \
    && rm -rf /usr/local/src/vss \
    && mkdir -p /usr/local/src/vss/services \
    && mv /usr/local/src/vss-agent /usr/local/src/vss/services/agent \
    && chmod -R a+rX /usr/share/vss-skills /usr/local/src/vss \
    && ln -sfn /usr/share/vss-skills /opt/skills \
    && for d in /sandbox/.agents/skills /sandbox/.claude/skills; do \
         mkdir -p "$d"; \
         find /usr/share/vss-skills -name SKILL.md -printf '%h\n' \
           | while read -r s; do ln -sfn "$s" "$d/$(basename "$s")"; done; \
       done \
    && chown -R sandbox:sandbox /sandbox/.agents /sandbox/.claude \
    && vss --version \
    && su sandbox -c 'vss --version'

# ── nvm compatibility shim ─────────────────────────────────────────────────────
# Every harness adapter that drives a node agent opens with some form of
#     . ~/.nvm/nvm.sh && nvm use 22 && node -v && npm -v
# because the images they were written against installed node through nvm. This
# image gets node from the distro (and /usr/local/node below) instead, so that line
# fails on a file that does not exist and takes the whole `&&` chain — and the
# trial — with it. The shim makes `nvm use`/`nvm install` succeed when the
# version asked for is the node already installed, and fail loudly otherwise; it
# never downloads anything, which matters because the sandbox egress policy
# would refuse.
RUN mkdir -p /sandbox/.nvm && cat > /sandbox/.nvm/nvm.sh <<'NVM' \
    && chown -R sandbox:sandbox /sandbox/.nvm
# Shim, not nvm. See vss-agent/sandboxes/openclaw-vss-cli.Dockerfile.
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

# ── OpenClaw ───────────────────────────────────────────────────────────────────
# The package is `openclaw` on the public npm registry (the scoped name
# @openclaw/openclaw does not exist). Pinned to an exact version, not a
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
# Rather than downgrade openclaw, install a satisfying node beside the distro
# one. `npm install -g node@x` fetches an official prebuilt binary, so this
# stays a pinned, reproducible layer. Into its own prefix: a plain
# `npm install -g node` tries to symlink /usr/bin/node and aborts with EEXIST.
# Under /usr/local, not /opt, for the same Landlock reason as above.
ARG NODE_NPM_SPEC=node@22.23.2
RUN npm install -g --prefix /usr/local/node "$NODE_NPM_SPEC" \
    && /usr/local/node/bin/node -v \
    && ln -sf /usr/local/node/bin/node /usr/local/bin/node
# Symlinked into /usr/local/bin rather than prepended to PATH: a login shell
# (`bash -lc`, which is how the harness adapters invoke everything) re-derives
# PATH from /etc/profile and drops anything set here, and /usr/local/bin already
# precedes /usr/bin. Without this, openclaw refuses to start at runtime even
# though it installed fine at build time.
# npm has to run ON the new node — the engines check reads process.version — but
# the `node` npm package ships only the binary, so npm itself still comes from
# the distro install and is invoked through its cli.js. --prefix is explicit
# because npm otherwise derives it from the running node and would bury the
# binary in /usr/local/node/lib/node_modules/node/bin, which is on nobody's PATH.
RUN if [ -n "$OPENCLAW_NPM_REGISTRY" ]; then npm config set registry "$OPENCLAW_NPM_REGISTRY"; fi \
    && node /usr/lib/node_modules/npm/bin/npm-cli.js install -g --prefix /usr/local "$OPENCLAW_NPM_SPEC" \
    && node /usr/lib/node_modules/npm/bin/npm-cli.js cache clean --force \
    && openclaw --version

USER sandbox
WORKDIR /task

LABEL harness.base="openshell-community" \
      harness.contract="harbor-trial-v1" \
      harness.agent="openclaw" \
      harness.vss-cli="true"

# No entrypoint: the harness supplies the command and runs it non-interactively.
ENTRYPOINT []
CMD ["sleep", "infinity"]
