# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# OpenShell Hermes harness image (build context: repo root).
# Carries upstream Hermes, the `vss` CLI, operational skills, the `vss_cli`
# plugin, workspace docs, and `/etc/openshell/policy.yaml`.

ARG BASE_IMAGE=nvcr.io/nvidia/base/ubuntu:24.04
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.11.24
ARG HERMES_REPO=https://github.com/NousResearch/hermes-agent
ARG HERMES_REF=v2026.9.14
ARG VSS_PACKAGE_VERSION=0.0.0+local

FROM ${UV_IMAGE} AS uv

# Stage CPython 3.13 under /usr/bin/python3.13 so OPA matches /proc/<pid>/exe.
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
# `--extra sms` supplies aiohttp for the api_server adapter; strip GPL/patented codecs.
RUN cd /opt/hermes && uv sync --no-dev --frozen --extra sms \
    && uv pip uninstall --python .venv/bin/python pillow-heif \
    && rm -f .venv/lib/python3.13/site-packages/pillow.libs/libavif* \
    && .venv/bin/hermes --version
RUN rm -rf /opt/hermes/.git /opt/hermes/tests /opt/hermes/tests-js /opt/hermes/evals \
      /opt/hermes/website /opt/hermes/web /opt/hermes/ui-tui /opt/hermes/apps \
      /opt/hermes/native /opt/hermes/nix /opt/hermes/contributors \
      /opt/hermes/package-lock.json

FROM hermes AS vss-src
COPY libs/vss/pyproject.toml libs/vss/uv.lock /opt/vss-src/libs/vss/
COPY libs/vss/cli /opt/vss-src/libs/vss/cli
COPY libs/vss/core /opt/vss-src/libs/vss/core
COPY skills /opt/vss-src/skills
COPY .hermes/workspace /opt/vss-assets/workspace
RUN mkdir -p /opt/vss-assets/skills \
    && find /opt/vss-src/skills -name SKILL.md | sort | while read -r f; do \
         if sed -n '2,/^---$/p' "$f" | grep -qE '^[[:space:]]*vss-requires:'; then \
           d=$(dirname "$f"); cp -R "$d" "/opt/vss-assets/skills/$(basename "$d")"; \
         fi; \
       done \
    && test "$(ls /opt/vss-assets/skills | wc -l)" -gt 0 \
    && cp /opt/vss-src/skills/vss-build-vision-ai/scripts/sync_skills.py /opt/vss-assets/sync_skills.py
ARG VSS_PACKAGE_VERSION
RUN mkdir -p /opt/vss-assets/wheels \
    && uv export --frozen --no-dev --no-emit-workspace --no-hashes \
         --all-packages --project /opt/vss-src/libs/vss -o /opt/vss-assets/constraints.txt \
    && SETUPTOOLS_SCM_PRETEND_VERSION="$VSS_PACKAGE_VERSION" \
       uv build --wheel --all-packages --project /opt/vss-src/libs/vss -o /opt/vss-assets/wheels

FROM ${BASE_IMAGE} AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HERMES_HOME=/sandbox/.hermes \
    PATH=/usr/local/vss/bin:/opt/hermes/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
# curl and jq are required by bundled operational skills for direct REST routes and JSON parsing.
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl git jq \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 999 sandbox \
    && useradd -u 998 -g 999 -m -d /sandbox -s /bin/bash sandbox
COPY --from=python /opt/pystage/ /
COPY --from=hermes /opt/hermes /opt/hermes
RUN su sandbox -s /bin/sh -c 'set -e; cd /sandbox; H=/opt/hermes/.venv/bin/hermes; \
      $H config set model.default "\${env:HARNESS_MODEL}"; \
      $H config set model.provider custom; \
      $H config set model.base_url "\${env:HARNESS_BASE_URL}"; \
      $H config set model.api_key "\${env:HARNESS_API_KEY}"; \
      $H config set agent.max_turns 60; \
      $H config set approvals.mode manual; \
      $H config set browser.restrict_evaluate true' \
    && test -f /sandbox/.hermes/config.yaml
COPY --from=vss-src /opt/vss-assets/ /opt/vss-assets/
COPY .hermes/plugin /opt/vss-assets/plugin
RUN --mount=type=bind,from=uv,source=/uv,target=/usr/local/bin/uv \
    set -eux; export UV_NO_CACHE=1; \
    uv venv --python /usr/bin/python3.13 /usr/local/vss \
    && VIRTUAL_ENV=/usr/local/vss uv pip install -c /opt/vss-assets/constraints.txt /opt/vss-assets/wheels/*.whl \
    && VIRTUAL_ENV=/usr/local/vss uv pip uninstall opencv-python-headless numpy \
    && vss --version \
    && ln -s /usr/local/vss/bin/vss /usr/local/bin/vss \
    && mkdir -p /opt/vss-skills \
    && cp -R /opt/vss-assets/skills /opt/vss-skills/skills \
    && cp /opt/vss-assets/sync_skills.py /opt/vss-skills/sync_skills.py \
    && test ! -e /opt/hermes/skills/vss \
    && /usr/bin/python3.13 /opt/vss-skills/sync_skills.py \
         --skills-dir /opt/vss-skills/skills --active-dir /opt/hermes/skills/vss --all \
    && printf '#!/bin/sh\nactive="${HERMES_HOME:-/sandbox/.hermes}/skills/vss"\nmkdir -p "$active"\nexec /usr/bin/python3.13 /opt/vss-skills/sync_skills.py --skills-dir /opt/vss-skills/skills --active-dir "$active" "$@"\n' > /usr/local/bin/vss-hermes-sync \
    && chmod 0755 /usr/local/bin/vss-hermes-sync \
    && cp -R /opt/vss-assets/plugin /opt/hermes/plugins/vss \
    && /usr/bin/python3.13 -m py_compile /opt/hermes/plugins/vss/__init__.py \
    && HERMES_HOME=/sandbox/.hermes /opt/hermes/.venv/bin/python -I -c \
        'from hermes_cli.plugins import discover_plugins; from tools.registry import registry; discover_plugins(); fn = registry.get_definitions({"vss_cli"})[0]["function"]; assert sorted(fn.get("parameters", {}).get("properties", {})) == ["args", "cwd", "timeoutSec"], fn' \
    && cp /opt/vss-assets/workspace/*.md /sandbox/ \
    && chown -R sandbox:sandbox /sandbox \
    && chmod -R a+rX /opt/vss-skills /opt/hermes/skills/vss /opt/hermes/plugins/vss \
    && test -f /opt/hermes/skills/vss/vss-ask-video/SKILL.md && test -f /sandbox/AGENTS.md \
    && rm -rf /opt/vss-assets

RUN mkdir -p /etc/openshell && chmod 0755 /etc/openshell
COPY --chmod=0644 .hermes/policy.yaml /etc/openshell/policy.yaml

# Real directories (not symlinks): OpenShell chowns read_write paths at sandbox start.
RUN for d in task output logs tests solution; do mkdir -p "/$d" && chmod 777 "/$d"; done

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

# Fail build if any patent-encumbered codec library leaked into the runtime image.
RUN ["/usr/bin/python3.13", "-c", "import os,re,sys; pat=re.compile(r'^(libavcodec|libavformat|libavutil|libavfilter|libavdevice|libavresample|libavif|libswscale|libswresample|libpostproc|libx264|libx265|libde265|libopenh264|libkvazaar).*\\.so',re.I); hits=[os.path.join(r,f) for b in ('/opt','/usr') if os.path.exists(b) for r,_,fs in os.walk(b) for f in fs if pat.match(f)]; sys.exit('FAIL: patent-encumbered codec libraries present in image:\\n'+'\\n'.join(sorted(hits))) if hits else print('OK: no patent-encumbered codec libraries in image')"]

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
