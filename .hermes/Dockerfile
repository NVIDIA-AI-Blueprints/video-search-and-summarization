# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# The Hermes harness image: Hermes built from its own public source on a
# standard Ubuntu base, plus the `vss` CLI, the operation skills, the vss_cli
# tool plugin and the workspace docs. policy.yaml beside this file is the
# matching policy; it is baked in, so one artifact carries harness and policy.
#
# Not carried: the NGC CLI. An operation skill that resolves a sample video by
# name shells out to `ngc`, and that call fails here; the `ngc` entry in
# policy.yaml simply never matches.
#
# Build context is the checkout root, so the image carries this tree:
#
#   docker build -f .hermes/Dockerfile \
#     --build-arg HERMES_REF="$HERMES_REF" \
#     -t "openshell-hermes-vss:$TAG" .

ARG BASE_IMAGE=nvcr.io/nvidia/base/ubuntu:24.04
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.11.6
# Hermes publishes weekly tags; v2026.9.14 is 0.21.3, the verified version.
# Keep HERMES_REF on a tag, not a branch.
ARG HERMES_REPO=https://github.com/NousResearch/hermes-agent
ARG HERMES_REF=v2026.9.14
# The CLI wheels' version: hatch-vcs reads git metadata, and a build context has
# no .git. Same arg and default as ../services/agent/docker/Dockerfile.
ARG VSS_PACKAGE_VERSION=0.0.0+local

FROM ${UV_IMAGE} AS uv

# ── Python 3.13 ────────────────────────────────────────────────────────────────
# The base ships no python3 at all, so uv supplies it — 3.13, the top of
# Hermes' >=3.11,<3.14 range and the floor the vss CLI needs. Staging the whole
# prefix under usr/ is what puts it at /usr/bin/python3.13 with sys.prefix=/usr
# instead of uv's version-and-arch directory: the OPA binary check matches
# /proc/<pid>/exe, which no symlink can redirect.
FROM ${BASE_IMAGE} AS python
COPY --from=uv /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && uv python install --install-dir /tmp/py 3.13 \
    && mkdir -p /opt/pystage/usr \
    && cp -a /tmp/py/cpython-3.13.*/. /opt/pystage/usr/ \
    && rm -rf /tmp/py /opt/pystage/usr/lib/python3.13/test \
         /opt/pystage/usr/lib/python3.13/idlelib \
    && find /opt/pystage -name 'libpython*.a' -delete \
    && /opt/pystage/usr/bin/python3.13 -c 'import ssl, sqlite3, venv, ctypes'

# ── Hermes runtime: upstream source + its venv ─────────────────────────────────
FROM ${BASE_IMAGE} AS hermes
ARG HERMES_REPO
ARG HERMES_REF
COPY --from=uv /uv /usr/local/bin/uv
COPY --from=python /opt/pystage/ /
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/*
RUN git init -q /opt/hermes \
    && git -C /opt/hermes remote add origin "$HERMES_REPO" \
    && git -C /opt/hermes fetch -q --depth 1 --filter=blob:none origin "$HERMES_REF" \
    && git -C /opt/hermes checkout -q FETCH_HEAD
ENV UV_PYTHON=/usr/bin/python3.13 \
    UV_PROJECT_ENVIRONMENT=/opt/hermes/.venv
# Core dependencies only: every provider, messaging platform and browser extra
# is opt-in in Hermes' own pyproject.toml. The one exception is --extra sms,
# Hermes' aiohttp-only extra; aiohttp is what the api_server platform's adapter
# needs, and without it the gateway logs "No adapter available for api_server"
# and binds nothing. Taking it as an extra holds the version to uv.lock's pin
# instead of resolving one outside it.
#
# Editable, because Hermes' setup.py refuses to build a wheel or sdist of
# itself: the source tree is the installed package and stays in the runtime.
RUN cd /opt/hermes && uv sync --no-dev --frozen --extra sms \
    && .venv/bin/hermes --version
# The build tree is not the runtime tree: the harness ships the package, its
# bundled skills and its bundled plugins, not the repo it was built from. The
# directories below are the TUI, web UI, tests and docs site — all node or
# browser surfaces, none reachable from the API server or the CLI agent.
RUN rm -rf /opt/hermes/.git /opt/hermes/tests /opt/hermes/tests-js /opt/hermes/evals \
      /opt/hermes/website /opt/hermes/web /opt/hermes/ui-tui /opt/hermes/apps \
      /opt/hermes/native /opt/hermes/nix /opt/hermes/contributors \
      /opt/hermes/package-lock.json

# ── VSS source: skills + workspace docs + CLI wheels ──────────────────────────
# Same selection rules as ../.openclaw/Dockerfile's vss-src stage. Built on
# the hermes stage so the wheels are built against the same /usr/bin/python3.13
# the runtime resolves.
FROM hermes AS vss-src
# libs/vss by its two members, not whole: a developer's .venv sits beside them
# and BuildKit would send all of it.
COPY libs/vss/pyproject.toml libs/vss/uv.lock /opt/vss-src/libs/vss/
COPY libs/vss/cli /opt/vss-src/libs/vss/cli
COPY libs/vss/core /opt/vss-src/libs/vss/core
COPY skills /opt/vss-src/skills
COPY .openclaw/workspace /opt/vss-src/.openclaw/workspace
# Operation skills: every skill whose SKILL.md frontmatter declares vss-requires.
RUN mkdir -p /opt/vss-assets/skills \
    && find /opt/vss-src/skills -name SKILL.md | sort | while read -r f; do \
         if sed -n '2,/^---$/p' "$f" | grep -qE '^[[:space:]]*vss-requires:'; then \
           d=$(dirname "$f"); cp -R "$d" "/opt/vss-assets/skills/$(basename "$d")"; \
         fi; \
       done \
    && test "$(ls /opt/vss-assets/skills | wc -l)" -gt 0 \
    && cp /opt/vss-src/skills/vss-build-vision-ai/scripts/sync_skills.py /opt/vss-assets/sync_skills.py
# Workspace docs: the shared set, less BOOTSTRAP.md. AGENTS.md follows that
# file on first run, and it probes for a repository, a GPU and the NGC CLI,
# none of which a sandbox that only operates a deployment has.
RUN mkdir -p /opt/vss-assets/workspace \
    && cp /opt/vss-src/.openclaw/workspace/*.md /opt/vss-assets/workspace/ \
    && rm -f /opt/vss-assets/workspace/BOOTSTRAP.md \
    && test -f /opt/vss-assets/workspace/AGENTS.md
# Only the CLI library workspace (libs/vss). services/agent is deliberately not
# built: a sandbox operates a deployment through the CLI, it does not run the
# agent. Building here keeps VSS source out of the runtime image.
ARG VSS_PACKAGE_VERSION
RUN mkdir -p /opt/vss-assets/wheels \
    && SETUPTOOLS_SCM_PRETEND_VERSION="$VSS_PACKAGE_VERSION" \
       uv build --wheel --all-packages --project /opt/vss-src/libs/vss -o /opt/vss-assets/wheels

# ── Runtime ────────────────────────────────────────────────────────────────────
# The toolchain purge is by construction rather than by removal: this stage
# installs no compiler, no node and no network client beyond git, so there is
# nothing to strip afterwards. Every build happens in a stage above and only its output
# is copied; uv is the one exception, removed once the vss venv exists.
FROM ${BASE_IMAGE} AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HERMES_HOME=/sandbox/.hermes \
    PATH=/usr/local/vss/bin:/opt/hermes/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/* \
    # 998:999 is the process identity policy.yaml requires.
    && groupadd -g 999 sandbox \
    && useradd -u 998 -g 999 -m -d /sandbox -s /bin/bash sandbox
COPY --from=python /opt/pystage/ /
COPY --from=hermes /opt/hermes /opt/hermes
# config.yaml, which upstream Hermes ships none of. Endpoint, model and key
# stay ${env:} refs: they are a per-deployment choice the create command
# renders. The three settings after the model block are the only non-default
# guardrails this harness relies on; the api_server platform needs no entry,
# since it starts by default and binds its documented 8642.
RUN su sandbox -s /bin/sh -c 'set -e; cd /sandbox; H=/opt/hermes/.venv/bin/hermes; \
      $H config set model.default "\${env:HARNESS_MODEL}"; \
      $H config set model.provider custom; \
      $H config set model.base_url "\${env:HARNESS_BASE_URL}"; \
      $H config set model.api_key "\${env:HARNESS_API_KEY}"; \
      $H config set agent.max_turns 60; \
      $H config set approvals.mode manual; \
      $H config set browser.restrict_evaluate true' \
    && test -f /sandbox/.hermes/config.yaml
COPY --from=uv /uv /usr/local/bin/uv
COPY --from=vss-src /opt/vss-assets/ /opt/vss-assets/
COPY .hermes/plugin /opt/vss-assets/plugin
RUN set -eux; \
    # The vss CLI, from the wheels built out of the same tree as the skills.
    # Installed by path, not by name: with --find-links a bare name still
    # admits index candidates, so a future published nvidia-vss-cli could
    # outrank the wheel and silently break the CLI==skills guarantee.
    uv venv --python /usr/bin/python3.13 /usr/local/vss \
    && VIRTUAL_ENV=/usr/local/vss uv pip install /opt/vss-assets/wheels/*.whl \
    && vss --version \
    # The vss_cli plugin resolves an absolute VSS_BIN, not a PATH lookup.
    && ln -s /usr/local/vss/bin/vss /usr/local/bin/vss \
    # The shipped set stays read-only under /opt/vss-skills/skills. Hermes
    # loads skills from $HERMES_HOME/skills only and seeds that directory from
    # its bundled library on every launch, so the VSS set ships inside the
    # bundle under vss/: any HERMES_HOME gets it with nothing run beforehand.
    # vss-hermes-sync re-selects after `vss configure` and owns only
    # $HERMES_HOME/skills/vss, leaving Hermes' own skills alone.
    && mkdir -p /opt/vss-skills \
    && cp -R /opt/vss-assets/skills /opt/vss-skills/skills \
    && cp /opt/vss-assets/sync_skills.py /opt/vss-skills/sync_skills.py \
    && test ! -e /opt/hermes/skills/vss \
    && /usr/bin/python3.13 /opt/vss-skills/sync_skills.py \
         --skills-dir /opt/vss-skills/skills --active-dir /opt/hermes/skills/vss --all \
    && printf '#!/bin/sh\nactive="${HERMES_HOME:-/sandbox/.hermes}/skills/vss"\nmkdir -p "$active"\nexec /usr/bin/python3.13 /opt/vss-skills/sync_skills.py --skills-dir /opt/vss-skills/skills --active-dir "$active" "$@"\n' > /usr/local/bin/vss-hermes-sync \
    && chmod 0755 /usr/local/bin/vss-hermes-sync \
    # The vss_cli tool plugin, bundled (not a $HERMES_HOME/plugins/ user
    # install) so a `kind: backend` plugin auto-loads for every HERMES_HOME
    # with no per-home `plugins enable` step.
    && cp -R /opt/vss-assets/plugin /opt/hermes/plugins/vss \
    && /usr/bin/python3.13 -m py_compile /opt/hermes/plugins/vss/__init__.py \
    # py_compile cannot see a double-wrapped schema, which reaches the model as a
    # vss_cli with no parameters. Assert the shape Hermes itself hands the model.
    && HERMES_HOME=/sandbox/.hermes /opt/hermes/.venv/bin/python -I -c \
        'from hermes_cli.plugins import discover_plugins; from tools.registry import registry; discover_plugins(); fn = registry.get_definitions({"vss_cli"})[0]["function"]; assert sorted(fn.get("parameters", {}).get("properties", {})) == ["args", "cwd", "timeoutSec"], fn' \
    && cp /opt/vss-assets/workspace/*.md /sandbox/ \
    && chown -R sandbox:sandbox /sandbox \
    && chmod -R a+rX /opt/vss-skills /opt/hermes/skills/vss /opt/hermes/plugins/vss \
    && test -f /opt/hermes/skills/vss/vss-ask-video/SKILL.md && test -f /sandbox/AGENTS.md \
    && rm -rf /opt/vss-assets /usr/local/bin/uv
# The access boundary, baked so one artifact carries harness and policy. The
# directory is made here rather than left to COPY, which would give it the
# --chmod mode and leave it unsearchable.
RUN mkdir -p /etc/openshell && chmod 0755 /etc/openshell
COPY --chmod=0644 .hermes/policy.yaml /etc/openshell/policy.yaml

# Harbor trial paths, which Harbor and its agent adapters address by name. Real
# directories, not symlinks into /sandbox: OpenShell chowns every read_write
# policy path at sandbox start and refuses a symlink. World-writable so the
# harness can populate them as the sandbox user.
RUN for d in task output logs tests solution; do mkdir -p "/$d" && chmod 777 "/$d"; done

# ── VLM request defaults for `vss vlm run` ────────────────────────────────────
# Runtime variables, declared empty: the CLI reads empty as unset, so the VLM
# server's own behavior applies until a sandbox sets one. Names and meanings:
# libs/vss/cli/README.md ("Configure VLM requests").
ENV VSS_VLM_BACKEND= \
    VSS_VLM_MODEL= \
    VSS_VLM_TIMEOUT= \
    VSS_VLM_TEMPERATURE= \
    VSS_VLM_MAX_TOKENS= \
    VSS_VLM_SEED= \
    VSS_VLM_ENABLE_REASONING= \
    VSS_VLM_CHUNK_DURATION= \
    VSS_VLM_FPS= \
    VSS_VLM_MAX_FRAMES= \
    VSS_VLM_TOTAL_PIXELS= \
    VSS_VLM_LOCKED=false

USER sandbox
WORKDIR /sandbox
CMD ["sleep", "infinity"]

LABEL harness.base="nvidia-ubuntu" \
      harness.contract="harbor-trial-v1" \
      harness.agent="hermes" \
      harness.vss-cli="true" \
      harness.vss-plugin="true" \
      harness.ngc-cli="false" \
      harness.workspace-root="/sandbox"
