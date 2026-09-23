# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# OpenClaw harness image — NemoClaw's managed OpenClaw runtime + the VSS OpenClaw
# plugin (vss CLI tool + VSS skills + workspace instructions) + the pinned vss CLI.
# tags: [nemoclaw, openclaw, vss-plugin, vss-cli]
#
# Layout follows NemoClaw's documented custom-image workflow
# (docs.nvidia.com/nemoclaw → Install OpenClaw Plugins): take the complete
# managed runtime, name it `nemoclaw-runtime`, build the plugin in a separate
# stage from its lockfile, then extend the runtime with `openclaw plugins
# install` as the sandbox user and refresh the managed config hash.
#
# The doc starts from NemoClaw's stock Dockerfile with a version-matched source
# checkout as the build context. That Dockerfile COPYs from a dozen places in
# the NemoClaw tree, so it cannot be vendored here. NemoClaw's managed-images
# workflow publishes the image that Dockerfile produces, so we `FROM` that
# image at an immutable digest instead: same layers, no foreign checkout.
#
# Build context is this directory (.openclaw): the plugin source is
# COPYd from ./plugin and the OpenClaw workspace instructions from ./workspace;
# the skills and the vss CLI come from a pinned commit of this repo so the two
# always match — or, dev loop, from a `.vss-src/` working-tree snapshot placed
# here by skills/vss-build-vision-ai/scripts/stage_vss_src.py, which wins over
# the pin when present.
#
#   docker build -t <registry>/vss-harness-openclaw:<tag> .openclaw

# NemoClaw v0.0.127 managed OpenClaw runtime: OpenClaw 2026.7.1, node 24.18.1,
# python 3.13, git, the nemoclaw plugin, nemoclaw-start entrypoint. v0.0.127 is
# the NEMOCLAW_INSTALL_REF deploy/docker/scripts/deploy_nemoclaw.ipynb installs,
# and NemoClaw requires the CLI, base image and plugin OpenClaw to be one release.
# Pinned by digest — a mutable tag with IfNotPresent silently serves whatever
# the node cached first. Move it deliberately, together with OPENCLAW_VERSION
# and the notebook's NEMOCLAW_INSTALL_REF.
ARG BASE_IMAGE=ghcr.io/nvidia/nemoclaw/openclaw-sandbox@sha256:959d1255717b54f724248eb282dbb049d3cd785d5aa4eddb0da2a03839ad6fca
# The OpenClaw the base carries. The plugin's devDependency must match it, so
# the compiled plugin links against the runtime it will actually load in.
ARG OPENCLAW_VERSION=2026.7.1
# Same builder image NemoClaw's Dockerfile uses for its plugin stages.
ARG BUILDER_IMAGE=node:24.18.1-trixie-slim@sha256:ac39e4b5fcb2b1b34b20364fd58b2e898f3bb80731ee6f62a7536f9df3d6aadc
# uv 0.12.10, by index digest (multi-arch).
ARG UV_IMAGE=ghcr.io/astral-sh/uv@sha256:2bb3ebca0a796a155094a27773d290c4b074572e6107f171d88d086682fd2500
# NGC CLI, staged unconditionally: the operation skills fetch sample data and
# release fixtures with `ngc registry resource download-version`, and the
# sandbox cannot install it for itself at runtime — its egress policy allows no
# NGC host. Same version prerequisites.md pins for a host install.
ARG NGC_CLI_VERSION=4.10.0
# Skills and the vss CLI: one pinned commit of this repo, so an eval can say
# exactly which it measured. Must be a commit whose operation skills declare
# `vss-requires` in their SKILL.md frontmatter (staging fails loudly otherwise)
# and whose CLI library lives at libs/vss (the wheel build below fails loudly
# on older layouts). Override with --build-arg VSS_REF=<sha>; bump the default
# when the skills move.
ARG VSS_REPO=https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization
ARG VSS_REF=01ecaf705c676123aadc77177143aa963338026f

# The session values `onboard --from` rewrites. Declared before the first FROM
# because the patcher only rewrites the first `ARG <name>=` line in the file
# (src/lib/onboard/dockerfile-patch.ts, /m without /g), and read in a stage that
# does not descend from BASE_IMAGE because OpenShell builds through the Docker
# Engine API, i.e. the legacy builder, where an ENV inherited from the base
# image wins over a same-named ARG in RUN and in ENV substitution alike. The
# base exports all five, so declaring them in the final stage reads back the
# base image's model however faithfully onboard patched this file.
ARG NEMOCLAW_PRIMARY_MODEL_REF=
ARG NEMOCLAW_MODEL=
ARG NEMOCLAW_CONTEXT_WINDOW=
ARG NEMOCLAW_MAX_TOKENS=
ARG CHAT_UI_URL=

# ── onboard's session values, out of the base image's ENV shadow ──────────────
FROM ${BUILDER_IMAGE} AS onboard-args
ARG NEMOCLAW_PRIMARY_MODEL_REF
ARG NEMOCLAW_MODEL
ARG NEMOCLAW_CONTEXT_WINDOW
ARG NEMOCLAW_MAX_TOKENS
ARG CHAT_UI_URL
RUN mkdir -p /onboard \
    && printf '%s' "$NEMOCLAW_PRIMARY_MODEL_REF" > /onboard/NEMOCLAW_PRIMARY_MODEL_REF \
    && printf '%s' "$NEMOCLAW_MODEL" > /onboard/NEMOCLAW_MODEL \
    && printf '%s' "$NEMOCLAW_CONTEXT_WINDOW" > /onboard/NEMOCLAW_CONTEXT_WINDOW \
    && printf '%s' "$NEMOCLAW_MAX_TOKENS" > /onboard/NEMOCLAW_MAX_TOKENS \
    && printf '%s' "$CHAT_UI_URL" > /onboard/CHAT_UI_URL

# ── uv, for wheel builds and the vss CLI venv ──────────────────────────────────
FROM ${UV_IMAGE} AS uv

# ── VSS source checkout + CLI wheels ───────────────────────────────────────────
FROM ${BUILDER_IMAGE} AS vss-src
ARG VSS_REPO
ARG VSS_REF
ARG NGC_CLI_VERSION
ARG TARGETARCH
# python3 (3.13 on trixie) for the wheel builds below; curl/unzip for the NGC CLI.
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates python3 curl unzip \
    && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /usr/local/bin/uv
# Dev-loop override: a .vss-src/ snapshot in the build context (staged by
# stage_vss_src.py) wins over the pinned fetch, so a local skill or CLI edit
# reaches the image without a push. The Dockerfil[e] glob keeps this COPY
# valid when nothing is staged — the Dockerfile itself always matches.
COPY Dockerfil[e] .vss-sr[c] /opt/vss-src/
RUN rm -f /opt/vss-src/Dockerfile \
    && if [ -f /opt/vss-src/STAGED ]; then \
         echo "vss-src: pre-staged working-tree snapshot" && cat /opt/vss-src/STAGED; \
       else \
         git init -q /opt/vss-src \
         && git -C /opt/vss-src fetch -q --depth 1 "$VSS_REPO" "$VSS_REF" \
         && git -C /opt/vss-src checkout -q FETCH_HEAD; \
       fi
# The vss CLI as wheels — only the CLI library workspace (libs/vss: the
# nvidia-vss-cli entry point and its nvidia-vss-core dependency). The agent
# service package under services/agent is deliberately NOT built or shipped:
# a sandbox operates a deployment through the CLI, it does not run the agent.
# Building here (where the checkout and its .git live) keeps VSS source out of
# the runtime image: the runtime installs the wheels by name and ships no
# checkout. hatch-vcs reads the version from .git on the pinned path; a
# pre-staged snapshot has no .git and carries a PEP 440 pretend version in its
# STAGED marker instead.
RUN set -eux; \
    if [ -f /opt/vss-src/STAGED ]; then \
      export SETUPTOOLS_SCM_PRETEND_VERSION="$(sed -n 's/^version=//p' /opt/vss-src/STAGED)"; \
      test -n "$SETUPTOOLS_SCM_PRETEND_VERSION"; \
    fi; \
    mkdir -p /opt/vss-wheels \
    && uv build --wheel --all-packages --project /opt/vss-src/libs/vss -o /opt/vss-wheels \
    && ls /opt/vss-wheels

# NGC CLI, unpacked here for the runtime stage to copy. Fetched in this builder
# rather than in the managed base, which carries no guarantee of `curl` or
# `unzip`; extracting here also keeps the runtime to one COPY.
RUN set -eux; \
    case "${TARGETARCH:-amd64}" in \
      amd64) ngc_zip=ngccli_linux.zip ;; \
      arm64) ngc_zip=ngccli_arm64.zip ;; \
      *) echo "no NGC CLI build for TARGETARCH=${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    curl -fsSLo /tmp/ngccli.zip \
      "https://api.ngc.nvidia.com/v2/resources/nvidia/ngc-apps/ngc_cli/versions/${NGC_CLI_VERSION}/files/${ngc_zip}"; \
    unzip -qo /tmp/ngccli.zip -d /opt; \
    rm -f /tmp/ngccli.zip; \
    chmod +x /opt/ngc-cli/ngc; \
    test -x /opt/ngc-cli/ngc

# ── Plugin build (from its lockfile) ───────────────────────────────────────────
FROM ${BUILDER_IMAGE} AS vss-plugin-builder
ARG OPENCLAW_VERSION
ENV NPM_CONFIG_AUDIT=false NPM_CONFIG_FUND=false NPM_CONFIG_UPDATE_NOTIFIER=false
WORKDIR /opt/vss-plugin
COPY plugin/package.json plugin/package-lock.json plugin/tsconfig.json ./
# The lockfile pins the openclaw devDependency; it has to be the base's version.
RUN node -e 'const l=require("./package-lock.json"); const v=l.packages["node_modules/openclaw"].version; if (v!==process.argv[1]) { console.error(`plugin lockfile pins openclaw ${v}, base image carries ${process.argv[1]}`); process.exit(1); }' "$OPENCLAW_VERSION"
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY plugin/openclaw.plugin.json plugin/stage-assets.sh ./
COPY plugin/src/ ./src/
RUN npm run build \
    && npm prune --omit=dev --omit=peer --ignore-scripts --no-audit --no-fund \
    && test ! -e node_modules/openclaw
# Content assets: the operation skills — every skill in the pinned checkout whose
# SKILL.md frontmatter declares `vss-requires` — and the OpenClaw
# workspace instructions from this directory, staged under the plugin root.
# skills/ is the full set; the manifest points at skills-active/, which the
# plugin fills from the deployment the vss CLI recorded (see sync_skills.py,
# the shared selector staged from the pinned VSS checkout).
# At build every listed skill is active, so a sandbox that has not run
# `vss configure` yet still has them all.
COPY --from=vss-src /opt/vss-src/skills /tmp/vss-skills
COPY --from=vss-src /opt/vss-src/skills/vss-build-vision-ai/scripts/sync_skills.py ./sync_skills.py
COPY workspace/ /tmp/vss-workspace/
# Build-time equivalent of `sync_skills.py --all` (everything active); this
# builder is a node image without python3, and --all is a plain copy. The
# runtime re-selects with the shared tool (register() and vss-openclaw-sync).
RUN ./stage-assets.sh /tmp/vss-skills /tmp/vss-workspace \
    && rm -rf /tmp/vss-skills /tmp/vss-workspace \
    && cp -R skills skills-active \
    && test "$(ls skills-active | wc -l)" = "$(ls skills | wc -l)"

# ── Managed runtime + vss CLI ──────────────────────────────────────────────────
FROM ${BASE_IMAGE} AS nemoclaw-runtime
USER root

# Harbor trial paths. Harbor and its agent adapters address /task, /output,
# /logs, /tests and /solution by name. They are REAL directories, not symlinks
# into /sandbox: the OpenShell policy engine chowns every read_write path at
# sandbox start and refuses a symlink ("read_write path '/task' is a symlink —
# refusing to chown, potential privilege escalation"), which failed every trial
# before the agent started. World-writable like the openshell-community image,
# so the harness can populate them as the sandbox user; the eval harness moves
# files in and out with exec+tar, so they do not need to live in the workspace.
RUN for d in task output logs tests solution; do mkdir -p "/$d" && chmod 777 "/$d"; done

# The vss CLI. nvidia-vss-cli is on no reachable index, so the vss-src stage
# builds it as wheels from the same pinned checkout the skills came from (or
# from the pre-staged snapshot) and the runtime installs those by name — no
# VSS source ships in this image. Own venv, so it never touches the runtime's
# python.
COPY --from=uv /uv /usr/local/bin/uv
COPY --from=vss-src /opt/vss-wheels/ /opt/vss-wheels/
# Installed by wheel PATH, not by name: with --find-links a bare name still
# admits index candidates, so a future published nvidia-vss-cli could outrank
# the wheel built from VSS_REF and silently break the CLI==skills guarantee.

# NGC CLI on PATH for root and the sandbox user. Unconditional: an operation
# skill that resolves a sample video by name shells out to `ngc` directly, and a
# sandbox that lacks it fails with `command not found` having no way to recover.
COPY --from=vss-src /opt/ngc-cli/ /usr/local/lib/ngc-cli/
RUN chmod -R a+rX /usr/local/lib/ngc-cli \
    && chmod +x /usr/local/lib/ngc-cli/ngc \
    && ln -sf /usr/local/lib/ngc-cli/ngc /usr/local/bin/ngc \
    && ngc --version \
    && su -s /bin/sh sandbox -c "ngc --version"
RUN set -eux; \
    uv venv --python /usr/bin/python3 /usr/local/vss \
    && VIRTUAL_ENV=/usr/local/vss uv pip install /opt/vss-wheels/*.whl \
    && ln -sf /usr/local/vss/bin/vss /usr/local/bin/vss \
    && rm -rf /opt/vss-wheels \
    && vss --version \
    && vss vlm run --help | grep -F -- '--fps' \
    && su -s /bin/sh sandbox -c "vss --version"

# ── Extend the managed runtime with the VSS plugin ─────────────────────────────
FROM nemoclaw-runtime AS vss-runtime
# Re-declared here because build args are stage-scoped and this is the final stage.
ARG NEMOCLAW_TOOL_DISCLOSURE=progressive
ENV NEMOCLAW_TOOL_DISCLOSURE=${NEMOCLAW_TOOL_DISCLOSURE}
COPY --from=vss-plugin-builder --chown=sandbox:sandbox \
    /opt/vss-plugin/package.json \
    /opt/vss-plugin/package-lock.json \
    /opt/vss-plugin/openclaw.plugin.json \
    /opt/vss-plugin/
COPY --from=vss-plugin-builder --chown=sandbox:sandbox /opt/vss-plugin/dist/ /opt/vss-plugin/dist/
COPY --from=vss-plugin-builder --chown=sandbox:sandbox /opt/vss-plugin/node_modules/ /opt/vss-plugin/node_modules/
COPY --from=vss-plugin-builder --chown=sandbox:sandbox /opt/vss-plugin/skills/ /opt/vss-plugin/skills/
COPY --from=vss-plugin-builder --chown=sandbox:sandbox /opt/vss-plugin/skills-active/ /opt/vss-plugin/skills-active/
COPY --from=vss-plugin-builder --chown=sandbox:sandbox /opt/vss-plugin/sync_skills.py /opt/vss-plugin/sync_skills.py
COPY --from=vss-plugin-builder --chown=sandbox:sandbox /opt/vss-plugin/workspace/ /opt/vss-plugin/workspace/

# Staged outside the managed extensions dir and installed (copied) into it, per
# the NemoClaw doc. The pre-check fails the build if npm kept a private
# node_modules/openclaw that would stop OpenClaw creating its runtime link; the
# post-checks prove the link exists and resolves to the image's own runtime.
USER sandbox
RUN test ! -e /opt/vss-plugin/node_modules/openclaw \
    && HOME=/sandbox openclaw plugins install /opt/vss-plugin \
    && test -L /sandbox/.openclaw/extensions/vss/node_modules/openclaw \
    && test "$(realpath /sandbox/.openclaw/extensions/vss/node_modules/openclaw)" = "$(realpath /usr/local/lib/node_modules/openclaw)" \
    && HOME=/sandbox openclaw plugins enable vss \
    && HOME=/sandbox openclaw plugins inspect vss --json > /dev/null \
    && test -d /sandbox/.openclaw/extensions/vss/skills-active \
    && test -f /sandbox/.openclaw/extensions/vss/workspace/AGENTS.md

# `vss-openclaw-sync`: re-select the active skills from what `vss configure`
# recorded, for operators and harnesses that configure the deployment after the
# gateway is up. The plugin runs the same selection itself at register time.
USER root
RUN printf '%s\n' '#!/bin/sh' 'exec python3 /sandbox/.openclaw/extensions/vss/sync_skills.py --plugin-dir /sandbox/.openclaw/extensions/vss "$@"' > /usr/local/bin/vss-openclaw-sync \
    && chmod 755 /usr/local/bin/vss-openclaw-sync

# Enabling the plugin changes openclaw.json after the managed runtime hashed it;
# nemoclaw-start verifies that hash, so refresh it.
#
# Harbor (the eval runner) OWNS ~/.openclaw/openclaw.json at trial time: its
# openclaw adapter writes a fresh config from its own baseline plus an overlay
# and copies it over this file. Anything this image needs in that config must
# therefore be published where the harness can find it, not only baked here.
# /etc/openclaw-harness/config-overlay.json carries exactly that: the plugins
# block (nemoclaw + vss enabled) lifted from the config built above. The eval
# harness deep-merges it into Harbor's config inside the sandbox, so the plugin,
# its skills and workspace files load under Harbor's config too. The hash below
# is left unlocked (mutable default) for the same reason: two writers, one file.
USER root
# `onboard --from` rewrites the session's values into this file
# (src/lib/onboard/dockerfile-patch.ts) and expects NemoClaw's config generator
# to run again at build. A custom image cannot re-run that generator, so it
# inherits the base image's openclaw.json -- and every ARG this file does not
# declare is dropped by a regex String.replace that matches nothing and warns
# nothing. Only NEMOCLAW_TOOL_DISCLOSURE has a contract NemoClaw verifies; the
# rest fail silently, which is how the model and the UI origins were being lost.
# The values arrive as files, not build args -- see the onboard-args stage --
# and are read back below. Copied here rather than with the plugin above so a
# new session's values do not invalidate the plugin install.
COPY --from=onboard-args /onboard/ /etc/vss-onboard-args/
COPY apply-onboard-config.py /usr/local/bin/vss-apply-onboard-config
RUN chmod 755 /usr/local/bin/vss-apply-onboard-config \
    && NEMOCLAW_PRIMARY_MODEL_REF="$(cat /etc/vss-onboard-args/NEMOCLAW_PRIMARY_MODEL_REF)" \
       NEMOCLAW_MODEL="$(cat /etc/vss-onboard-args/NEMOCLAW_MODEL)" \
       NEMOCLAW_CONTEXT_WINDOW="$(cat /etc/vss-onboard-args/NEMOCLAW_CONTEXT_WINDOW)" \
       NEMOCLAW_MAX_TOKENS="$(cat /etc/vss-onboard-args/NEMOCLAW_MAX_TOKENS)" \
       CHAT_UI_URL="$(cat /etc/vss-onboard-args/CHAT_UI_URL)" \
       python3 /usr/local/bin/vss-apply-onboard-config \
    && chown sandbox:sandbox /sandbox/.openclaw/openclaw.json \
    && chmod 660 /sandbox/.openclaw/openclaw.json \
    && sha256sum /sandbox/.openclaw/openclaw.json > /sandbox/.openclaw/.config-hash \
    && chown sandbox:sandbox /sandbox/.openclaw/.config-hash \
    && chmod 660 /sandbox/.openclaw/.config-hash \
    && mkdir -p /etc/openclaw-harness \
    && node -e 'const c=JSON.parse(require("fs").readFileSync("/sandbox/.openclaw/openclaw.json","utf8")); \
                const o={plugins:c.plugins||{}}; \
                require("fs").writeFileSync("/etc/openclaw-harness/config-overlay.json", JSON.stringify(o,null,2)+"\n");' \
    && chmod 644 /etc/openclaw-harness/config-overlay.json \
    && node -e 'const o=JSON.parse(require("fs").readFileSync("/etc/openclaw-harness/config-overlay.json","utf8")); if(!o.plugins.entries.vss.enabled) process.exit(1)'

# Workspace-variant selector for the plugin's register-time seeding: apply the
# _nemoclaw overlay (ENV.md, sandbox host alias, proxy notes) in this image.
ENV VSS_WORKSPACE_VARIANT=nemoclaw

# OpenShell mounts /proc read-only. OpenClaw's supported zero value disables
# its optional child OOM-score write; pod resource limits remain authoritative.
ENV OPENCLAW_CHILD_OOM_SCORE_ADJ=0

# The managed base image already contains generic workspace Markdown, while
# register-time seeding intentionally preserves existing files. Seed the VSS
# variant while building the immutable image so a fresh sandbox starts with
# the NemoClaw-specific instructions; later runtime seeding still preserves
# user edits.
USER root
RUN set -eu; \
    workspace=/sandbox/.openclaw/workspace; \
    mkdir -p "$workspace"; \
    cp /opt/vss-plugin/workspace/_nemoclaw/*.md "$workspace"/; \
    for source in /opt/vss-plugin/workspace/*.md; do \
      target="$workspace/$(basename "$source")"; \
      [ -e "$target" ] || cp "$source" "$target"; \
    done; \
    chown -R sandbox:sandbox "$workspace"

# NemoClaw's shell hooks apply a hard `ulimit -u 512` in every shell
# (/etc/profile.d/nemoclaw-rlimits.sh and /etc/bash.bashrc, sourcing
# /usr/local/lib/nemoclaw/sandbox-rlimits.sh). That is harmless when the shell
# runs as the `sandbox` user, which owns a handful of processes. The OpenShell
# k8s driver runs the sandbox container as uid 0, and RLIMIT_NPROC for root
# counts every root-owned process on the node, thousands on a k3s host, so the
# first fork in the upload's ssh session fails with EAGAIN and the sandbox dies
# (exit 254) before Harbor's first exec. Process limits for eval sandboxes are
# the cluster's job (pod pids limits), not the shell's.
USER root
RUN rm -f /etc/profile.d/nemoclaw-rlimits.sh \
    && sed -i '/sandbox-rlimits/d; /NemoClaw sandbox resource limits/d' /etc/bash.bashrc \
    && ! grep -rq "sandbox-rlimits" /etc/profile.d /etc/bash.bashrc

# nvm shim. Harbor's openclaw adapter bootstraps node with nvm (`. ~/.nvm/nvm.sh
# && nvm use 22 && node -v`); this image pins node, so the shim answers `use`
# and `install` for the pinned major and refuses any other, exactly as the
# openshell-community image did. Without it every trial fails at agent setup
# with "/sandbox/.nvm/nvm.sh: No such file or directory".
# (Kaniko does not support heredocs in RUN; the shim ships as a context file.)
COPY --chown=sandbox:sandbox nvm-shim.sh /sandbox/.nvm/nvm.sh

# Final USER is the non-root default OpenShell requires. WORKDIR /sandbox makes
# /sandbox the OpenShell workspace (OpenShell derives it from the OCI WorkingDir).
# Entrypoint and health check are inherited from the managed runtime — note
# OpenShell replaces the entrypoint with its own when it creates a sandbox, so
# an image-owned ENTRYPOINT never runs and cannot be used to fix config. The
# command is not inherited: the runtime's CMD is /bin/bash,
# which reads EOF and exits as soon as nemoclaw-start hands over when the
# container has no TTY, so an OpenShell sandbox built from this image went to
# phase Error before the first exec. The eval harness drives the agent with
# exec, so the container only has to stay alive.
USER sandbox
WORKDIR /sandbox
CMD ["sleep", "infinity"]

LABEL harness.base="nemoclaw-openclaw-sandbox" \
      harness.contract="harbor-trial-v1" \
      harness.agent="openclaw" \
      harness.vss-cli="true" \
      harness.vss-plugin="true" \
      harness.ngc-cli="true" \
      harness.workspace-root="/sandbox"
