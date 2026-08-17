# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# -----------------------------------------------------------------------------
# Stage 1: Builder — install Python deps (with build tools for any C extensions)
# -----------------------------------------------------------------------------
FROM python:3.13-trixie AS builder

WORKDIR /usr/src/app

# Build deps only when needed; clean in same layer to avoid extra image size
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps via uv (locked). Build context: monorepo root.
# External CI should build with:
# docker build -f services/configurators/vss-configurator/docker/Dockerfile .
COPY --from=ghcr.io/astral-sh/uv:0.11.24 /uv /usr/local/bin/uv
COPY libs/analytics/spatialai-data-utils /libs/analytics/spatialai-data-utils
COPY services/configurators/vss-configurator/pyproject.toml \
    services/configurators/vss-configurator/uv.lock \
    ./
RUN uv sync --frozen --no-dev

# -----------------------------------------------------------------------------
# Stage 2: Runtime — distroless Python 3.13 (no shell, no package manager)
# -----------------------------------------------------------------------------
# Chainguard Python 3.13 is distroless: ~23MB base, no shell, non-root by default.
# Google distroless does not ship Python 3.13; Chainguard does.
# Runtime is distroless, so startup must use a Python entrypoint instead of a shell script.
# FROM cgr.dev/chainguard/python:3.13 AS runtime
FROM nvcr.io/nvidia/distroless/python:3.13-v4.0.5 AS runtime

WORKDIR /usr/src/app

# Bring in deps from builder; distroless has its own Python, so we use PYTHONPATH
# instead of copying into system paths (avoids mixing builder and runtime layouts).
COPY --from=builder /usr/src/app/.venv/lib/python3.13/site-packages /usr/src/app/site-packages
ENV PYTHONPATH=/usr/src/app/site-packages

# Application code (entrypoint.py is used because distroless has no shell).
# Source retains Apache-2.0 SPDX headers; third-party licenses ship in-image.
COPY services/configurators/vss-configurator/app/ .
COPY services/configurators/vss-configurator/3rdParty_Licenses.md \
    /usr/src/app/3rdParty_Licenses.md

# The NVIDIA Software License Agreement is not vendored in this source tree.
# BuildKit performs this download itself, so no HTTP client is needed in any
# stage, and --checksum verifies the bytes before the layer is created: a
# CDN error page or a silently replaced document fails the build.
ADD --chown=root:root --chmod=0444 \
    --checksum=sha256:f5ad3da1faf5734a1f9ea1ad4ab6862236bf061a0ebbb02dea0fb726b81d7e0d \
    https://www.nvidia.com/content/dam/en-zz/Solutions/license-agreements/enterprise-software/NVIDIA-Software-License-Agreement-2026.5.7.pdf \
    /usr/src/app/NVIDIA-Software-License-Agreement.pdf

ENV PORT=5000
EXPOSE 5000

# No shell — must run Python entrypoint directly
CMD ["python", "entrypoint.py"]
