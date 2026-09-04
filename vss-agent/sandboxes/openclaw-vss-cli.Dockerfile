# Example sandbox Dockerfile — OpenClaw harness + pinned vss CLI.
# tags: [nemoclaw-lineage, openclaw, vss-cli-0.6.0]
ARG BASE_IMAGE=ghcr.io/nvidia-ai-blueprints/vss/vss-harness-base:latest
FROM ${BASE_IMAGE}

# Pin the vss CLI. `nvidia-vss` is not on public PyPI (pypi.org returns 404), so
# the index has to be supplied for this layer to resolve:
#   --build-arg VSS_PIP_INDEX_URL=https://pypi.internal.example.com/simple
#   --build-arg VSS_CLI_SPEC='nvidia-vss[cli]==0.6.0'
USER root
ARG VSS_CLI_SPEC=nvidia-vss[cli]==0.6.0
ARG VSS_PIP_INDEX_URL=
RUN python3 -m venv /opt/vss \
    && /opt/vss/bin/pip install ${VSS_PIP_INDEX_URL:+--index-url "$VSS_PIP_INDEX_URL"} "$VSS_CLI_SPEC" \
    && ln -sf /opt/vss/bin/vss /usr/local/bin/vss
USER ubuntu

# install any additional CLIs/tools the agent should have, e.g.:
# USER root
# RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
# USER ubuntu

# See openclaw.Dockerfile: the package is not on the public npm registry.
ARG OPENCLAW_NPM_SPEC=@openclaw/openclaw@latest
ARG OPENCLAW_NPM_REGISTRY=
RUN . $NVM_DIR/nvm.sh && nvm use 22 \
    && if [ -n "$OPENCLAW_NPM_REGISTRY" ]; then npm config set registry "$OPENCLAW_NPM_REGISTRY"; fi \
    && npm install -g "$OPENCLAW_NPM_SPEC" && openclaw --version

LABEL harness.agent="openclaw"
