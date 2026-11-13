# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# The Hermes harness image: Hermes built from its own public source on a
# standard Ubuntu base.
#
# policy.yaml beside this file is the matching policy: the agent's own model
# endpoint and package access, nothing else. The interpreter keeps its
# /usr/bin/python3.13 path and the process its 998:999 identity, both of which
# that file pins by exact path.
#
# Build context is this directory (.hermes):
#
#   docker build --build-arg HERMES_REF="$HERMES_REF" \
#     -t "openshell-hermes:$HERMES_REF" .hermes

ARG BASE_IMAGE=nvcr.io/nvidia/base/ubuntu:24.04
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.11.6
# Hermes publishes weekly tags; v2026.9.14 is 0.21.3, the verified version.
# Keep HERMES_REF on a tag, not a branch.
ARG HERMES_REPO=https://github.com/NousResearch/hermes-agent
ARG HERMES_REF=v2026.9.14

FROM ${UV_IMAGE} AS uv

# ── Python 3.13 ────────────────────────────────────────────────────────────────
# The base ships no python3 at all, so uv supplies it — 3.13, the top of
# Hermes' >=3.11,<3.14 range. Staging the whole prefix under usr/ is what puts
# it at /usr/bin/python3.13 with sys.prefix=/usr instead of uv's version-and-arch
# directory: the OPA binary check matches /proc/<pid>/exe, which no symlink can
# redirect.
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

# ── Runtime ────────────────────────────────────────────────────────────────────
# The toolchain purge is by construction rather than by removal: this stage
# installs no compiler, no network client and no node, so there is nothing to
# strip afterwards. Every build happens in a stage above and only its output
# is copied.
FROM ${BASE_IMAGE} AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HERMES_HOME=/sandbox/.hermes \
    PATH=/opt/hermes/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/* \
    # 998:999 is the process identity policy.yaml requires.
    && groupadd -g 999 sandbox \
    && useradd -u 998 -g 999 -m -d /sandbox -s /bin/bash sandbox
COPY --from=python /opt/pystage/ /
COPY --from=hermes /opt/hermes /opt/hermes
# OpenShell mounts the supervisor's CA at a fixed path and the sandbox's start
# command runs this before the agent. certifi's own bundle cannot take the
# append: it is root-owned under /opt, which the policy mounts read-only. So the
# public roots and the CA are written together to SSL_CERT_FILE, under /sandbox,
# which every TLS client in the image reads ahead of certifi.
ENV SSL_CERT_FILE=/sandbox/.hermes/ca-bundle.crt
RUN printf '%s\n' '#!/bin/sh' 'set -e' \
      'ca=/run/openshell-supervisor-ca/material/ca-bundle.crt' \
      '[ -r "$ca" ] || { echo "no supervisor CA at $ca" >&2; exit 1; }' \
      's=$(/opt/hermes/.venv/bin/python3 -c "import certifi;print(certifi.where())")' \
      'cat "$s" "$ca" > "$SSL_CERT_FILE"' \
      > /usr/local/bin/trust-supervisor-ca \
    && chmod 0755 /usr/local/bin/trust-supervisor-ca
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
    && test -f /sandbox/.hermes/config.yaml \
    # Seeded with the public roots alone, so SSL_CERT_FILE resolves in a later
    # build stage and outside a sandbox, where there is no supervisor CA.
    && install -o sandbox -g sandbox -m 0644 \
         "$(/opt/hermes/.venv/bin/python3 -c 'import certifi;print(certifi.where())')" \
         "$SSL_CERT_FILE"
# The access boundary, baked so one artifact carries harness and policy. The
# directory is made here rather than left to COPY, which would give it the
# --chmod mode and leave it unsearchable.
RUN mkdir -p /etc/openshell && chmod 0755 /etc/openshell
COPY --chmod=0644 policy.yaml /etc/openshell/policy.yaml

USER sandbox
WORKDIR /sandbox
CMD ["sleep", "infinity"]

LABEL harness.base="nvidia-ubuntu" \
      harness.contract="harbor-trial-v1" \
      harness.agent="hermes" \
      harness.vss-cli="false" \
      harness.vss-plugin="false" \
      harness.ngc-cli="false" \
      harness.workspace-root="/sandbox"
