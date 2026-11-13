# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# The optional VSS layer on top of the pure harness ../../../.hermes/Dockerfile
# builds. Adds the `vss` CLI, the operation skills, the vss_cli tool plugin and
# the workspace docs, and replaces the baked policy with the pair beside this
# file, which is the pure one plus the vss-backend and ngc grants.
#
# Nothing below re-does what the base already did: the interpreter, Hermes, the
# sandbox identity, config.yaml and trust-supervisor-ca all come from it.
#
# Not carried over: the NGC CLI. An operation skill that resolves a sample
# video by name shells out to `ngc`, and that call fails here; the `ngc` entry
# in policy.yaml simply never matches.
#
# Build context is the checkout root, so the image carries this tree:
#
#   docker build -f services/openshell/vss/Dockerfile \
#     --build-arg HARNESS_IMAGE="openshell-hermes:$HERMES_REF" \
#     -t "openshell-hermes-vss:$TAG" .

ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.11.6
# The image ../../../.hermes/Dockerfile produced. No default: a stale base silently
# yields a harness the policy pair here was not proven against.
ARG HARNESS_IMAGE
# The CLI wheels' version: hatch-vcs reads git metadata, and a build context has
# no .git. Same arg and default as ../../../services/agent/docker/Dockerfile.
ARG VSS_PACKAGE_VERSION=0.0.0+local

FROM ${UV_IMAGE} AS uv

# ── VSS source: skills + workspace docs + CLI wheels ──────────────────────────
# Same selection rules as ../../../.openclaw/Dockerfile's vss-src stage. Built on the
# harness image rather than the bare base so the wheels are built against the
# same /usr/bin/python3.13 the runtime resolves.
FROM ${HARNESS_IMAGE} AS vss-src
USER root
COPY --from=uv /uv /usr/local/bin/uv
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
# Workspace docs: the shared set only.
RUN mkdir -p /opt/vss-assets/workspace \
    && cp /opt/vss-src/.openclaw/workspace/*.md /opt/vss-assets/workspace/ \
    && test -f /opt/vss-assets/workspace/AGENTS.md
# Only the CLI library workspace (libs/vss). services/agent is deliberately not
# built: a sandbox operates a deployment through the CLI, it does not run the
# agent. Building here keeps VSS source out of the runtime image.
ARG VSS_PACKAGE_VERSION
RUN mkdir -p /opt/vss-assets/wheels \
    && SETUPTOOLS_SCM_PRETEND_VERSION="$VSS_PACKAGE_VERSION" \
       uv build --wheel --all-packages --project /opt/vss-src/libs/vss -o /opt/vss-assets/wheels

# ── Runtime ────────────────────────────────────────────────────────────────────
FROM ${HARNESS_IMAGE} AS runtime
USER root
ENV PATH=/usr/local/vss/bin:/opt/hermes/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
COPY --from=uv /uv /usr/local/bin/uv
COPY --from=vss-src /opt/vss-assets/ /opt/vss-assets/
COPY .hermes/plugin /opt/vss-assets/plugin
RUN set -eux; \
    # The vss CLI, from the wheels built out of the same tree as the skills.
    # Installed by path, not by name: with --find-links a bare name still
    # admits index candidates, so a future published nvidia-vss-cli could
    # outrank the wheel and silently break the CLI==skills guarantee. The CLI
    # needs >=3.13, which is what the base interpreter has to stay at.
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
# Replaces the base's pure policy with the pair beside this file.
COPY --chmod=0644 services/openshell/vss/policy.yaml /etc/openshell/policy.yaml

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
