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

ARG PYTHON_VERSION=3.13
ARG DISTROLESS_IMG=nvcr.io/nvidia/distroless/python
ARG DISTROLESS_TAG=${PYTHON_VERSION}-v4.0.5

# -----------------------------------------------------------------------------
# Stage 1: Builder — install Python deps (with build tools for any C extensions)
# -----------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-trixie AS builder

WORKDIR /usr/src/app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /var/lib/dpkg/status.d

# Install Python deps via uv (locked). Build context: repo root.
COPY --from=ghcr.io/astral-sh/uv:0.11.24 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# -----------------------------------------------------------------------------
# Stage 2: Runtime — distroless Python (no shell, no package manager)
# -----------------------------------------------------------------------------
FROM ${DISTROLESS_IMG}:${DISTROLESS_TAG} AS runtime

ARG PYTHON_VERSION
ENV LD_LIBRARY_PATH=/lib/distroless/

WORKDIR /usr/src/app

COPY --from=builder /usr/src/app/.venv/lib/python${PYTHON_VERSION}/site-packages /usr/src/app/site-packages
ENV PYTHONPATH=/usr/src/app/site-packages

# Distroless runtime libs (matches the working Dockerfile pattern)
# COPY --from=builder /lib/distroless/ /lib/distroless/
# dpkg status for scanners (image-level package metadata)
COPY --from=builder /var/lib/dpkg/status.d /var/lib/dpkg/status.d

# Application code (entrypoint.py replaces start.sh because distroless has no shell).
COPY app/ .

# Third-party license text for container distribution.
COPY 3rdParty_Licenses.md /usr/src/app/ThirdPartyLicences.txt

ENV PORT=5000
EXPOSE 5000

CMD ["python", "entrypoint.py"]
